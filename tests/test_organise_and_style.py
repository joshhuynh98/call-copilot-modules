"""Organise-first summary, recommendation triage, and the concise / plain-English style rules."""
import json

import pytest
from fastapi.testclient import TestClient

import callcopilot.app.api as api
from callcopilot.shared import policy
from callcopilot.modules.email.checks import EmailPolicy, check_email, enforce_email_text
from callcopilot.shared.checks import failed
from callcopilot.shared.checks import check_style
from callcopilot.shared.contracts import CallBrief, EmailDraft, MeetingSummary, RecNote
from callcopilot.shared.llm import LLMConfig
from callcopilot.modules.chatbot import ChatSession
from callcopilot.modules.email import draft_email
from callcopilot.modules.recommendations import complete_notes, triage_recommendations
from callcopilot.modules.summary import summarize
from callcopilot.shared.prompt_loader import load_prompt
from callcopilot.app.render import render_summary
from callcopilot.shared.sources import list_scenarios, load_scenario
from callcopilot.modules.chatbot.tools import FixtureToolBackend
from callcopilot.shared.contracts import MeetingContext
from conftest import ScriptedLLM

FAKE = LLMConfig(provider="fake")
nl = chr(10)


def brief_of(sc, **over):
    raw = json.loads(sc.golden_summary.model_dump_json())
    raw.pop("plain_language")
    raw.update(over)
    return CallBrief.model_validate(raw)


# -- organise first, then write -------------------------------------------------------------------------
def test_summary_runs_organise_then_write(icap):
    llm = ScriptedLLM([brief_of(icap).model_dump_json(), icap.golden_summary.model_dump_json()])
    s = summarize(icap.transcript, llm, FAKE, icap.recommendations)
    assert [c[1].task for c in llm.calls] == ["organize", "summary"]
    organise = "\n".join(m.content for m in llm.calls[0][0])
    assert "rec_1" in organise and "<<<BEGIN UNTRUSTED RECOMMENDATIONS>>>" in organise and "[seg_002]" in organise
    write = "\n".join(m.content for m in llm.calls[1][0])
    assert "Be brief." in write and "timed out waiting for Core scan result" in write  # style rules + the brief
    assert s.meta["prompts"] == "organize@v2+summary@v2" and s.meta["chunks"] == 1


def test_groups_are_present_and_separate(icap):
    s = summarize(icap.transcript, ScriptedLLM([brief_of(icap).model_dump_json(), icap.golden_summary.model_dump_json()]), FAKE)
    assert s.issues and s.before_call and s.in_call and s.findings
    assert {a.owner for a in s.next_actions} == {"agent", "customer"}


def test_writer_cannot_drop_or_alter_facts(icap):
    brief = brief_of(icap)
    brief.findings[0].uncertain = True
    thin = icap.golden_summary.model_copy(deep=True)
    thin.in_call = []
    thin.next_actions = thin.next_actions[:1]
    thin.product.version = "9.9.9"
    thin.findings[0].uncertain = False
    s = summarize(icap.transcript, ScriptedLLM([brief.model_dump_json(), thin.model_dump_json()]), FAKE)
    assert len(s.in_call) == 2 and len(s.next_actions) == 3  # restored from the brief
    assert s.product.version == "5.8.0"  # facts come from the brief
    assert s.findings[0].uncertain is True  # doubt is never lost in rewriting


def test_messy_recommendations_are_triaged(icap):
    notes = [RecNote(rec_id="rec_1"), RecNote(rec_id="rec_2", relevant=False, note="unrelated"), RecNote(rec_id="ghost", relevant=False)]
    llm = ScriptedLLM([brief_of(icap, recommendation_notes=[n.model_dump() for n in notes]).model_dump_json(),
                       icap.golden_summary.model_dump_json()])
    s = summarize(icap.transcript, llm, FAKE, icap.recommendations)
    by = {n.rec_id: n for n in s.recommendation_notes}
    assert set(by) == {"rec_1", "rec_2", "rec_3"}  # unknown id dropped, missing one added as relevant
    assert by["rec_2"].relevant is False and by["rec_3"].relevant is True
    assert [r.id for r in triage_recommendations(icap.recommendations, s.recommendation_notes).recommendations] == ["rec_1", "rec_3"]

    dropped_url = "https://docs.example-opswat.invalid/core/configuration/archive-handling"  # rec_2's public doc
    reply = json.dumps({"subject": "Recap", "body": f"Hi Dana,\n\nNext steps:\n1. Me: send the steps.\n\nMore: {dropped_url}"})
    email_llm = ScriptedLLM([reply])
    d = draft_email(icap.transcript, s, icap.style, icap.recommendations, email_llm)
    assert dropped_url not in "\n".join(m.content for m in email_llm.calls[0][0])  # never offered to the model
    assert dropped_url not in d.body and any("not allowed" in w for w in d.warnings)


def test_dropped_recommendations_still_forbid_their_ticket_ids(icap):
    kept = triage_recommendations(icap.recommendations, [RecNote(rec_id="rec_3", relevant=False)])
    assert [r.id for r in kept.recommendations] == ["rec_1", "rec_2"]
    assert "SUP-48211" in EmailPolicy.build(icap.recommendations, None, kept).jira_keys


def test_triage_edge_cases(icap):
    everything_dropped = [RecNote(rec_id=r.id, relevant=False) for r in icap.recommendations.recommendations]
    assert triage_recommendations(icap.recommendations, everything_dropped) is icap.recommendations  # never leave nothing
    recs = icap.recommendations.model_copy(deep=True)
    recs.recommendations.append(recs.recommendations[0].model_copy(update={"id": "rec_dup", "rank": 4}))
    notes = {n.rec_id: n for n in complete_notes(recs, [], 1)}
    assert notes["rec_dup"].relevant is False and "duplicate of rec_1" in notes["rec_dup"].note and notes["rec_1"].relevant
    assert complete_notes(recs, [RecNote(rec_id="rec_1", issue=7)], 1)[0].issue is None  # out-of-range issue index cleared
    assert complete_notes(None, [], 0) == []


# -- style: concise, no filler, plain sentences -----------------------------------------------------------
def test_style_checks_are_soft_and_catch_filler_and_long_bullets():
    res = {r.name: r for r in check_style("Thanks for your time today. Here is a quick recap.\n- " + "word " * 40)}
    assert not res["no_filler"].passed and not res["concise_bullets"].passed
    assert all(r.soft for r in res.values()) and not failed(list(res.values()))
    assert all(r.passed for r in check_style("Issue: uploads fail.\n- The timeout is 60 seconds.\n1. You: send the log."))


def test_dash_and_numbered_lists_are_plain_text(icap):
    pol = EmailPolicy.build(icap.recommendations, None)
    body = "Hi Dana,\n\nWhat we found:\n- one\n- two\n\nNext steps:\n1. Me: a\n2. You: b"
    assert "no_markdown" not in {r.name for r in failed(check_email(EmailDraft(subject="s", body=body), pol))}
    clean, fixes = enforce_email_text("Hi,\n\n* one\n+ two\n• three\n- four", pol)
    assert clean.count("- ") == 4 and "*" not in clean and "•" not in clean and fixes


def test_email_repair_asks_to_remove_filler(icap):
    s = MeetingSummary(**icap.golden_summary.model_dump())
    filler = json.dumps({"subject": "Recap", "body": "Hi Dana,\n\nThank you for your time during the meeting. Here is a quick recap.\n\nNext steps:\n1. Me: send the steps."})
    clean = json.dumps({"subject": "Recap", "body": "Hi Dana,\n\nThank you for your time during the meeting.\n\nNext steps:\n1. Me: send the steps."})
    llm = ScriptedLLM([filler, clean])
    d = draft_email(icap.transcript, s, icap.style, icap.recommendations, llm)
    assert "quick recap" not in d.body and "Thank you for your time" in d.body  # filler goes, the one courtesy stays
    assert d.meta["repairs"] == 1 and not d.warnings
    assert "no_filler" in llm.calls[1][0][-1].content


def test_one_courtesy_is_expected_wherever_the_model_puts_it(icap):
    pol = EmailPolicy.build(icap.recommendations, None)

    def courtesy(body):
        return next(r for r in check_email(EmailDraft(subject="s", body=body), pol) if r.name == "courtesy_line")

    opening = "Hi Dana," + nl + nl + "Thank you for your time during the meeting." + nl + nl + "Next steps:" + nl + "1. Me: a"
    closing = "Hi Dana," + nl + nl + "Next steps:" + nl + "1. Me: a" + nl + nl + "Thanks again for your time today."
    none = "Hi Dana," + nl + nl + "Next steps:" + nl + "1. Me: a"
    twice = "Hi Dana," + nl + nl + "Thanks for your time. Thank you for speaking with me." + nl + nl + "Next steps:" + nl + "1. Me: a"
    assert courtesy(opening).passed and courtesy(closing).passed  # placement is the model's call
    assert not courtesy(none).passed and courtesy(none).soft and not courtesy(twice).passed
    for phrase in ("Thank you for your time during the meeting.", "Thanks for your time today.", "Thank you for taking the time to join.",
                   "Thank you for speaking with me today.", "Thanks again for your time.", "I appreciate your time."):
        assert policy.COURTESY_RE.search(phrase), phrase
    assert not policy.FILLER_RE.search("Thank you for your time during the meeting.")  # a courtesy, not filler
    assert policy.FILLER_RE.search("Thank you for your patience.") and policy.FILLER_RE.search("Thanks for reaching out.")


def test_the_model_decides_format_and_length_there_are_no_email_settings(icap):
    s = MeetingSummary(**icap.golden_summary.model_dump())
    llm = ScriptedLLM([json.dumps({"subject": "Recap", "body": "Hi Dana," + nl + nl + "Thank you for your time during the meeting." + nl + nl + "Next steps:" + nl + "1. Me: send the steps."})])
    draft_email(icap.transcript, s, icap.style, icap.recommendations, llm)
    system = llm.calls[0][0][0].content
    for principle in ("YOU DECIDE THE FORMAT AND THE LENGTH", "Informative", "Courteous", "Concise", "ONE brief line", "never exceed 220"):
        assert principle in system, principle
    assert "{{" not in system  # every placeholder was filled
    import inspect
    from callcopilot.shared import contracts
    from callcopilot.app.pipeline import run_email
    assert not hasattr(contracts, "EmailOptions") and "options" not in inspect.signature(draft_email).parameters
    assert "options" not in inspect.signature(run_email).parameters
    assert "email_options" not in contracts.MeetingContext.model_fields


def test_length_ceiling_is_hard_and_target_is_soft(icap):
    pol = EmailPolicy.build(icap.recommendations, None)
    def results(n):
        body = "Hi Dana," + nl + nl + "Thank you for your time." + nl + nl + (("word " * 30).strip() + "." + nl + nl) * (n // 30)
        return {r.name: r for r in check_email(EmailDraft(subject="s", body=body), pol)}
    r = results(120)
    assert r["length_limit"].passed and r["target_length"].passed
    r = results(180)  # longer than most emails need: a warning, not a failure
    assert r["length_limit"].passed and not r["target_length"].passed and r["target_length"].soft
    assert not results(240)["length_limit"].passed  # past the ceiling: hard failure

def test_summary_and_chat_stay_free_of_courtesies(icap):
    result = next(r for r in check_style("Thank you for your time during the meeting.") if r.name == "courtesy_line")
    assert not result.passed and result.soft  # default (summary, chat): a thank-you is not wanted


@pytest.mark.parametrize("sid", list_scenarios())
def test_goldens_follow_the_style_rules(sid):
    sc = load_scenario(sid)
    summary = MeetingSummary(**sc.golden_summary.model_dump())
    text = summary.plain_language + "\n" + "\n".join(f"- {c.text}" for c in summary.all_claims())
    for body, courtesy in ((text, False), (sc.golden_email.body, True)):  # the email has its one thank-you line
        bad = [str(r) for r in check_style(body, courtesy_line=courtesy) if not r.passed]
        assert not bad, bad
    assert len(summary.plain_language.split()) <= 60


def test_prompts_carry_the_style_rules(icap):
    rules = load_prompt("style_rules").system
    assert "No filler" in rules and "Plain English" in rules
    for name in ("summary", "email", "chat"):
        assert "{{style_rules}}" in load_prompt(name).system
    ctx = MeetingContext(transcript=icap.transcript, recommendations=icap.recommendations,
                         summary=MeetingSummary(**icap.golden_summary.model_dump(), recommendation_notes=[RecNote(rec_id="rec_1")]))
    system = ChatSession(ctx, ScriptedLLM(["x"]), FixtureToolBackend(), FAKE)._system_prompt()
    assert "Be brief." in system and "recommendation_notes" not in system


# -- rendering and API -------------------------------------------------------------------------------------
def test_render_groups_sections_and_next_steps_by_owner(icap):
    text = render_summary(MeetingSummary(**icap.golden_summary.model_dump()))
    order = [text.index(h) for h in ("Issues", "Before the call", "During the call", "Findings", "Next steps")]
    assert order == sorted(order)
    assert text.index("Support (us):") < text.index("Customer:")
    assert "1. Confirm the ICAP scan timeout" in text and "2. Follow up" in text  # numbered per owner


def test_stateless_summary_endpoint_returns_triage_notes(icap):
    c = TestClient(api.create_app(FAKE, cors_origins=[], api_key=""))
    ctx = c.get("/v1/scenarios/icap_upload_timeouts").json()
    s = c.post("/v1/summary", json={"transcript": ctx["transcript"], "recommendations": ctx["recommendations"]}).json()
    assert [n["rec_id"] for n in s["recommendation_notes"]] == ["rec_1", "rec_2", "rec_3"]
    assert s["schema_version"] == "2.0" and s["issues"] and "issue" not in s


def test_writer_cannot_swap_owners_or_lose_doubt_by_merging(icap):
    brief = brief_of(icap)
    swapped = icap.golden_summary.model_copy(deep=True)
    swapped.next_actions[0].owner, swapped.next_actions[1].owner = "customer", "agent"  # same owners overall, order changed: fine
    s = summarize(icap.transcript, ScriptedLLM([brief.model_dump_json(), swapped.model_dump_json()]), FAKE)
    assert [a.owner for a in s.next_actions] == ["customer", "agent", "agent"]  # writer's list kept as is

    wrong = icap.golden_summary.model_copy(deep=True)
    wrong.next_actions[1].owner = "agent"  # customer action reassigned to support: owners no longer match the brief
    s = summarize(icap.transcript, ScriptedLLM([brief.model_dump_json(), wrong.model_dump_json()]), FAKE)
    assert [a.owner for a in s.next_actions] == [a.owner for a in brief.next_actions]  # brief restored

    merged = icap.golden_summary.model_copy(deep=True)
    brief.open_questions.append(brief.open_questions[0].model_copy(update={"text": "Another doubt."}))
    merged.open_questions[0].uncertain = False  # the writer merged two doubtful items and dropped the flag
    s = summarize(icap.transcript, ScriptedLLM([brief.model_dump_json(), merged.model_dump_json()]), FAKE)
    assert len(s.open_questions) == 2 and all(q.uncertain for q in s.open_questions)
