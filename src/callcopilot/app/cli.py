"""CLI: callcopilot <command>. Add --fake to run offline against the fixture scenarios."""
from __future__ import annotations

import argparse
import os
import sys

from callcopilot.shared.contracts import MeetingContext, RecommendationSet, StyleProfile, Transcript
from callcopilot.shared.llm import ConfigError, LLMError, load_config, load_dotenv, load_judge_config
from callcopilot.app.render import render_cheatsheet, render_email, render_summary

DEFAULT_QUESTIONS = ["What did the customer say about the version?", "What should I try next?"]


def _load_inputs(args):
    """Returns (transcript, recommendations|None, style|None, scenario|None)."""
    from callcopilot.shared.sources import FileRecommendationSource, FileTranscriptSource, load_scenario
    if args.scenario:
        sc = load_scenario(args.scenario)
        return sc.transcript, sc.recommendations, sc.style, sc
    if not args.transcript:
        raise SystemExit("Provide --scenario <id> (see `callcopilot scenarios`) or --transcript <file.json>.")
    recs = FileRecommendationSource(args.recs).get_recommendations() if getattr(args, "recs", None) else None
    style = StyleProfile.model_validate_json(open(args.style, encoding="utf-8").read()) if getattr(args, "style", None) else None
    return FileTranscriptSource(args.transcript).get_transcript(), recs, style, None


def _config(args):
    if getattr(args, "fake", False):
        os.environ["AI_PROVIDER"] = "fake"
    return load_config()


def _need_style(style):
    if style is None:
        raise SystemExit("An email needs a style profile: use --scenario or pass --style <file.json>.")
    return style


def _stream_chat(session, question: str) -> None:
    print(f"\nYOU: {question}\nCOPILOT: ", end="", flush=True)
    for ev in session.ask(question):
        if ev.type == "token":
            print(ev.text, end="", flush=True)
        elif ev.type == "tool_call":
            q = ev.tool.arguments.get("query") or ev.tool.arguments.get("customer") or ""
            print(f"\n  [{'guard ran ' if ev.tool.forced else ''}{ev.tool.tool}({q!r})]", end="", flush=True)
        elif ev.type == "tool_result":
            print(f" -> {ev.tool.result_count if ev.tool.error is None else 'error'} result(s)", end="", flush=True)
        elif ev.type == "error":
            print(f"\n[error] {ev.error}")
        elif ev.type == "done":
            r = ev.result
            print("\n" + ("" if r.text else ""))
            if r.text and not r.text.endswith("\n"):
                pass
            for c in r.citations:
                print(f"  source: {c.label} {c.jira_key or ''} {c.url or ''}".rstrip())
            for w in r.warnings:
                print(f"  warning: {w}")


def cmd_scenarios(args):
    from callcopilot.shared.sources import list_scenarios, load_scenario
    for sid in list_scenarios():
        m = load_scenario(sid).transcript.metadata
        print(f"{sid:28} {m.case_id}  {m.customer_name}  ({m.product})")


def cmd_summary(args):
    from callcopilot.app.pipeline import make_reference_reviewer, run_summary
    cfg = _config(args)
    t, recs, *_ = _load_inputs(args)
    s = run_summary(t, cfg, recommendations=recs, reviewer=make_reference_reviewer(cfg))
    print(s.model_dump_json(indent=2) if args.json else render_summary(s))


def cmd_email(args):
    from callcopilot.app.pipeline import make_reference_reviewer, run_email, run_summary
    cfg = _config(args)
    t, recs, style, _ = _load_inputs(args)
    summary = run_summary(t, cfg, recommendations=recs, reviewer=make_reference_reviewer(cfg))
    d = run_email(t, summary, _need_style(style), recs, cfg)
    print(d.model_dump_json(indent=2) if args.json else render_email(d))


def cmd_chat(args):
    from callcopilot.modules.chatbot import ChatSession
    from callcopilot.app.pipeline import make_llm, make_reference_reviewer, run_email, run_summary
    from callcopilot.modules.chatbot.tools import FixtureToolBackend
    cfg = _config(args)
    t, recs, style, sc = _load_inputs(args)
    llm = make_llm(cfg, t)
    summary = run_summary(t, cfg, llm, recs, make_reference_reviewer(cfg))
    draft = run_email(t, summary, style, recs, cfg, llm=llm) if style else None
    session = ChatSession(MeetingContext(transcript=t, recommendations=recs, style_profile=style, summary=summary, email_draft=draft),
                          llm, FixtureToolBackend(), cfg)
    questions = args.question or []
    if questions:
        for q in questions:
            _stream_chat(session, q)
        return
    print("Chat about this call. Empty line or Ctrl-D to quit.")
    while True:
        try:
            q = input("\n> ").strip()
        except EOFError:
            break
        if not q:
            break
        _stream_chat(session, q)


def cmd_demo(args):
    """Mock transcript in -> recommendations cheatsheet -> chatbot Q&A -> summary -> email draft out."""
    from callcopilot.modules.chatbot import ChatSession
    from callcopilot.app.pipeline import make_llm, make_reference_reviewer, run_email, run_summary
    from callcopilot.modules.chatbot.tools import FixtureToolBackend
    cfg = _config(args)
    t, recs, style, sc = _load_inputs(args)
    print(f"=== Call {t.metadata.case_id}: {t.metadata.customer_name} ({t.metadata.product}) | AI: {cfg.describe()} ===\n")
    print(t.render() + "\n")
    print(render_cheatsheet(recs) + "\n")
    llm = make_llm(cfg, t)
    summary = run_summary(t, cfg, llm, recs, make_reference_reviewer(cfg))
    print(render_summary(summary) + "\n")
    draft = run_email(t, summary, style, recs, cfg, llm=llm)
    print(render_email(draft))
    session = ChatSession(MeetingContext(transcript=t, recommendations=recs, style_profile=style, summary=summary, email_draft=draft),
                          llm, FixtureToolBackend(), cfg)
    print("\n=== Chatbot ===")
    for q in (sc.chat_script.get("demo_questions") if sc else None) or DEFAULT_QUESTIONS:
        _stream_chat(session, q)


def cmd_review(args):
    """Open sources the way the pipeline does, to check that docs access, the browser fallback and Atlassian MCP work."""
    from callcopilot.shared.contracts import Source, SourceType
    from callcopilot.shared.references import ReferenceReviewer, load_reference_config
    from callcopilot.shared.sources import FileRecommendationSource, load_scenario
    env = dict(os.environ)
    if not (env.get("REFERENCE_REVIEW") or "").strip():
        env["REFERENCE_REVIEW"] = "on"  # this command always reads the real sources unless told otherwise
    cfg = load_reference_config(env)
    if not cfg.enabled:
        raise SystemExit("REFERENCE_REVIEW is off. Set it to on (or leave it blank) to review sources.")
    reviewer = ReferenceReviewer(cfg)
    if args.url:
        kind = args.type or ("jira" if args.jira or "/browse/" in args.url else "confluence" if "/wiki/" in args.url else "public_doc")
        reviews = [reviewer.review_source(Source(type=SourceType(kind), label=args.url, url=args.url, jira_key=args.jira))]
    elif args.recs or args.scenario:
        recs = FileRecommendationSource(args.recs).get_recommendations() if args.recs else load_scenario(args.scenario).recommendations
        reviews = reviewer.review(recs)
    else:
        raise SystemExit("Give --url <link> (optionally --type/--jira), or --scenario <id>, or --recs <file.json>.")
    for rv in reviews:
        print(f"\n[{rv.type.value}] {rv.label or rv.url}\n  status: {rv.status}" + (f" ({rv.note})" if rv.note else ""))
        if rv.final_url and rv.final_url != rv.url:
            print(f"  leads to: {rv.final_url}")
        if rv.title:
            print(f"  title: {rv.title}")
        if rv.excerpt:
            print("  excerpt: " + " ".join(rv.excerpt.split())[:500])
    ok = sum(1 for rv in reviews if rv.status in ("ok", "redirected"))
    print(f"\n{ok}/{len(reviews)} sources opened. Docs mode: {cfg.docs_mode}; Atlassian MCP: {'configured' if cfg.atlassian_mcp_url else 'not configured'}.")


def cmd_recs_eval(args):
    from callcopilot.modules.recommendations.checks import check_recommendations
    from callcopilot.shared.checks import failed, summarize
    recs = RecommendationSet.model_validate_json(open(args.recommendations, encoding="utf-8").read())
    t = Transcript.model_validate_json(open(args.transcript, encoding="utf-8").read()) if args.transcript else None
    results = check_recommendations(recs, t)
    for r in results:
        if not r.passed or args.verbose:
            print(r)
    print(summarize(results))
    sys.exit(1 if failed(results) else 0)


def cmd_eval(args):
    from callcopilot.app.evals.judge import SKIP_MESSAGE
    from callcopilot.app.evals.runner import render_report, run_all, write_report
    cfg = _config(args)
    judge_cfg = None if args.no_judge else load_judge_config()
    reports, judge = run_all(cfg, judge_cfg, args.scenario_ids or None, use_judge=not args.no_judge)
    text = render_report(reports, cfg, judge, use_judge=not args.no_judge)
    print(text)
    if not judge and not args.no_judge:
        print(SKIP_MESSAGE)
    print(f"Report written to {write_report(reports, text)}")
    sys.exit(1 if any(r.errors or r.hard_failures for r in reports) else 0)


def cmd_serve(args):
    import uvicorn
    from callcopilot.app.api import create_app
    create_app(_config(args))  # fail fast on bad config before the server starts
    uvicorn.run("callcopilot.app.api:app", host=args.host, port=args.port)


def cmd_schemas(args):
    from callcopilot.app.export_schemas import export
    print("exported:", ", ".join(export()))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="callcopilot", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    def add(name, fn, help_, inputs=False, fake=True):
        sp = sub.add_parser(name, help=help_)
        sp.set_defaults(fn=fn)
        if fake:
            sp.add_argument("--fake", action="store_true", help="offline deterministic AI (AI_PROVIDER=fake)")
        if inputs:
            sp.add_argument("--scenario", help="fixture scenario id")
            sp.add_argument("--transcript", help="transcript JSON file")
            sp.add_argument("--recs", help="recommendations JSON file")
            sp.add_argument("--style", help="style profile JSON file")
        return sp

    add("scenarios", cmd_scenarios, "list fixture scenarios", fake=False)
    sp = add("summary", cmd_summary, "summarise a call", inputs=True)
    sp.add_argument("--json", action="store_true")
    sp = add("email", cmd_email, "draft the follow-up email", inputs=True)
    sp.add_argument("--json", action="store_true")
    sp = add("chat", cmd_chat, "chat about a call (interactive, or -q to ask and exit)", inputs=True)
    sp.add_argument("-q", "--question", action="append")
    add("demo", cmd_demo, "full vertical slice on one scenario", inputs=True)
    sp = add("review", cmd_review, "open recommendation sources (docs pages, Jira, Confluence) and show what was found", fake=False)
    sp.add_argument("--url", help="a single link to open")
    sp.add_argument("--type", choices=["public_doc", "jira", "confluence"], help="source type (guessed from the URL if omitted)")
    sp.add_argument("--jira", help="Jira ticket key for a Jira source")
    sp.add_argument("--scenario", help="review all sources of a fixture scenario's recommendations")
    sp.add_argument("--recs", help="review all sources of a recommendations JSON file")
    sp = add("recs-eval", cmd_recs_eval, "score any recommendations payload against the quality bar", fake=False)
    sp.add_argument("recommendations", help="recommendations JSON file")
    sp.add_argument("--transcript", help="transcript JSON file (enables trigger-segment checks)")
    sp.add_argument("-v", "--verbose", action="store_true")
    sp = add("eval", cmd_eval, "run all scenarios: deterministic checks + optional LLM judge")
    sp.add_argument("scenario_ids", nargs="*")
    sp.add_argument("--no-judge", action="store_true")
    sp = add("serve", cmd_serve, "run the HTTP API")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8000)
    add("schemas", cmd_schemas, "export JSON Schema for all contracts", fake=False)
    return p


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    args = build_parser().parse_args(argv)
    if args.command == "demo" and not args.scenario and not args.transcript:
        args.scenario = "icap_upload_timeouts"
    try:
        args.fn(args)
    except (ConfigError, LLMError, KeyError) as exc:
        print(f"error: {exc.args[0] if isinstance(exc, KeyError) else exc}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
