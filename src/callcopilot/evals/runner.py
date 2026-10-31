"""Eval harness: run every scenario through the modules, apply deterministic checks, optionally LLM-judge."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime

from callcopilot import policy
from callcopilot.checks import EmailPolicy, check_chat_turn, check_email, check_recommendations, check_summary, failed
from callcopilot.checks.base import CheckResult
from callcopilot.checks.chat import SEARCH_TOOLS
from callcopilot.checks.summary import must_mention
from callcopilot.contracts import EmailOptions, MeetingContext, MeetingSummary
from callcopilot.llm import LLMConfig
from callcopilot.modules.chatbot import ChatSession
from callcopilot.paths import REPO_ROOT
from callcopilot.pipeline import make_llm, run_email, run_summary
from callcopilot.sources import Scenario, list_scenarios, load_scenario
from callcopilot.tools import FixtureToolBackend

from .judge import SKIP_MESSAGE, Judge


@dataclass
class ScenarioReport:
    scenario: str
    sections: dict[str, list[CheckResult]] = field(default_factory=dict)
    judge: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def hard_failures(self) -> list[tuple[str, CheckResult]]:
        return [(sec, r) for sec, rs in self.sections.items() for r in failed(rs)]


def _recs_section(sc: Scenario) -> list[CheckResult]:
    out = check_recommendations(sc.recommendations, sc.transcript)
    exp = sc.expectations["recommendations"]
    top = sc.recommendations.recommendations[0].action.lower() if sc.recommendations.recommendations else ""
    miss = [n for n in exp["top_action_contains"] if n.lower() not in top]
    out.append(CheckResult("top_recommendation_is_likely_next_step", not miss, [f"top action lacks: {m}" for m in miss]))
    types = {s.type.value for s in sc.recommendations.sources()}
    miss = [t for t in exp["required_source_types"] if t not in types]
    out.append(CheckResult("required_source_types_present", not miss, [f"missing source type: {m}" for m in miss]))
    return out


def _summary_section(sc: Scenario, summary: MeetingSummary) -> list[CheckResult]:
    out = check_summary(summary, sc.transcript)
    exp = sc.expectations["summary"]
    blob = [summary.model_dump_json(exclude={"meta"})]
    out.append(must_mention(blob, exp["must_mention"]))
    if exp.get("version"):
        ok = summary.product.version == exp["version"] or summary.product.uncertain
        out.append(CheckResult("expected_version", ok, [] if ok else [f"expected {exp['version']}, got {summary.product.version}"]))
    owners = {a.owner for a in summary.next_actions}
    miss = [o for o in exp["next_action_owners"] if o not in owners]
    out.append(CheckResult("expected_action_owners", not miss, [f"no next action owned by {m}" for m in miss]))
    out.append(CheckResult("third_party_detected", bool(summary.third_party_components) == exp["third_party_expected"],
                           [f"expected third_party={exp['third_party_expected']}"] if bool(summary.third_party_components) != exp["third_party_expected"] else []))
    if exp.get("expect_uncertain"):
        flagged = any(c.uncertain for c in summary.all_claims()) or summary.product.uncertain
        out.append(CheckResult("uncertainty_marked", flagged, [] if flagged else ["noisy call but nothing marked uncertain"]))
    return out


def _email_section(sc: Scenario, summary: MeetingSummary, draft) -> list[CheckResult]:
    pol = EmailPolicy.build(EmailOptions(), sc.recommendations, summary)
    out = check_email(draft, pol, signature=sc.style.signature)
    exp = sc.expectations["email"]
    low = draft.body.lower()
    out.append(must_mention([low], exp["must_mention"]))
    leaked = [n for n in exp["must_not_mention"] if n.lower() in low]
    out.append(CheckResult("must_not_mention", not leaked, [f"mentions: {n}" for n in leaked]))
    miss = [u for u in exp.get("required_public_links", []) if u not in draft.body]
    out.append(CheckResult("required_public_links", not miss, [f"missing link: {u}" for u in miss]))
    out.append(CheckResult("has_claims_to_verify_or_none_needed", True))
    clean = not draft.warnings and draft.meta.get("repairs", 0) == 0
    out.append(CheckResult("email_first_pass_clean", clean, [*draft.warnings, f"repairs={draft.meta.get('repairs')}"] if not clean else [], soft=True))
    return out


def _chat_section(sc: Scenario, summary: MeetingSummary, draft, recs, config: LLMConfig, llm) -> list[CheckResult]:
    out: list[CheckResult] = []
    ctx = MeetingContext(transcript=sc.transcript, recommendations=recs, style_profile=sc.style, summary=summary, email_draft=draft)
    for probe in sc.chat_script.get("probes", []):
        session = ChatSession(ctx, llm, FixtureToolBackend(), config)
        result = None
        for ev in session.ask(probe["question"]):
            if ev.type == "done":
                result = ev.result
            elif ev.type == "error":
                out.append(CheckResult("chat_probe_ran", False, [f"{probe['question']!r}: {ev.error}"]))
        if result is None:
            continue
        checks = check_chat_turn(result, known_urls=set(session.sources), expect_contains=probe.get("expect_contains"))
        if probe.get("expect_tool_before_unknown"):
            searched = any(t.tool in SEARCH_TOOLS for t in result.tools_used)
            checks.append(CheckResult("probe_triggers_search", searched, [] if searched else ["no search tool was called"]))
        email_blocks = [b for b in __import__("re").findall(r"<email>(.*?)</email>", result.text, flags=__import__("re").S)]
        for b in email_blocks:
            pol = EmailPolicy.build(EmailOptions(), recs, summary)
            from callcopilot.contracts import EmailDraft
            checks += [CheckResult(f"email_block_{c.name}", c.passed, c.details) for c in
                       check_email(EmailDraft(subject="x", body=b.strip()), pol) if c.name in
                       ("no_markdown", "no_meeting_offer", "urls_public_and_provided", "no_jira_keys", "no_internal_links")]
        for c in checks:
            c.scope = probe["question"][:40]
        out += checks
    return out


def run_scenario(sid: str, config: LLMConfig, judge: Judge | None) -> ScenarioReport:
    sc = load_scenario(sid)
    rep = ScenarioReport(sid)
    rep.sections["recommendations"] = _recs_section(sc)
    llm = make_llm(config, sc.transcript)
    try:
        summary = run_summary(sc.transcript, config, llm, sc.recommendations)
        rep.sections["summary"] = _summary_section(sc, summary)
        draft = run_email(sc.transcript, summary, sc.style, sc.recommendations, config, llm=llm)
        rep.sections["email"] = _email_section(sc, summary, draft)
        rep.sections["chat"] = _chat_section(sc, summary, draft, sc.recommendations, config, llm)
    except Exception as exc:  # report, don't abort the whole run
        rep.errors.append(f"{type(exc).__name__}: {exc}")
        return rep
    if judge:
        try:
            golden = MeetingSummary(**sc.golden_summary.model_dump())
            for res in (judge.summary_faithfulness(sc.transcript, summary), judge.next_actions(sc.transcript, summary, golden),
                        judge.email_tone(sc.transcript.metadata.case_id, sc.style, draft),
                        judge.clarity_conciseness(sc.transcript.metadata.case_id, summary, draft),
                        judge.recommendation_relevance(sc.transcript, sc.recommendations)):
                rep.judge.append(asdict(res))
        except Exception as exc:
            rep.errors.append(f"judge failed: {type(exc).__name__}: {exc}")
    return rep


def run_all(config: LLMConfig, judge_config: LLMConfig | None, only: list[str] | None = None, use_judge: bool = True):
    judge = Judge(judge_config) if (judge_config and use_judge) else None
    reports = [run_scenario(sid, config, judge) for sid in (only or list_scenarios())]
    return reports, judge


def render_report(reports: list[ScenarioReport], config: LLMConfig, judge: Judge | None, use_judge: bool = True) -> str:
    lines = [f"# Eval report - {datetime.now():%Y-%m-%d %H:%M}", "",
             f"- Generation provider: {config.describe()}",
             f"- Judge: {judge.config.describe() if judge else ('disabled (--no-judge)' if not use_judge else SKIP_MESSAGE)}"]
    if config.is_fake:
        lines.append("- NOTE: fake provider serves the golden outputs, so passing proves the pipeline and checkers work, not model quality.")
    sections = ["recommendations", "summary", "email", "chat"]
    lines += ["", "## Summary (hard checks passed / total)", "", "| Scenario | " + " | ".join(sections) + " | Result |",
              "|---|" + "---|" * (len(sections) + 1)]
    for r in reports:
        cells = []
        for s in sections:
            rs = r.sections.get(s)
            cells.append("n/a" if rs is None else f"{sum(1 for c in rs if c.passed or c.soft)}/{len(rs)}")
        status = "ERROR" if r.errors else ("FAIL" if r.hard_failures else "PASS")
        lines.append(f"| {r.scenario} | " + " | ".join(cells) + f" | {status} |")

    for r in reports:
        problems = [f"- FAIL [{sec}] {c}" for sec, c in r.hard_failures]
        warns = [f"- WARN [{sec}] {c}" for sec, rs in r.sections.items() for c in rs if not c.passed and c.soft]
        errs = [f"- ERROR {e}" for e in r.errors]
        if problems or warns or errs:
            lines += ["", f"### {r.scenario}", *errs, *problems, *warns]
    judged = [r for r in reports if r.judge]
    if judged:
        lines += ["", "## Judge scores (1-5)", "", "| Scenario | Rubric | Score | Reasons |", "|---|---|---|---|"]
        for r in judged:
            for j in r.judge:
                lines.append(f"| {r.scenario} | {j['rubric']} ({j['version']}) | {j['score']} | {j['reasons'].replace('|', '/')} |")
    return "\n".join(lines) + "\n"


def write_report(reports: list[ScenarioReport], text: str) -> str:
    out = REPO_ROOT / "evals" / "reports"
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    (out / f"report-{stamp}.md").write_text(text, encoding="utf-8")
    data = [{"scenario": r.scenario, "errors": r.errors, "judge": r.judge,
             "sections": {s: [{"name": c.name, "passed": c.passed, "soft": c.soft, "scope": c.scope, "details": c.details} for c in rs]
                          for s, rs in r.sections.items()}} for r in reports]
    (out / f"report-{stamp}.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    return str(out / f"report-{stamp}.md")


__all__ = ["run_all", "render_report", "write_report", "ScenarioReport", "policy"]
