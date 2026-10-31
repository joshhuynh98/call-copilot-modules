"""Organise-first summary, recommendation triage, and the concise / plain-English style rules."""
import json

import pytest
from fastapi.testclient import TestClient

import callcopilot.api as api
from callcopilot.checks import EmailPolicy, check_email, enforce_email_text, failed
from callcopilot.checks.style import check_style
from callcopilot.contracts import CallBrief, EmailDraft, EmailOptions, MeetingSummary, RecNote
from callcopilot.llm import LLMConfig
from callcopilot.modules.chatbot import ChatSession
from callcopilot.modules.email import draft_email
from callcopilot.modules.recommendations import complete_notes, triage_recommendations
from callcopilot.modules.summary import summarize
from callcopilot.prompt_loader import load_prompt
from callcopilot.render import render_summary
from callcopilot.sources import list_scenarios, load_scenario
from callcopilot.tools import FixtureToolBackend
from callcopilot.contracts import MeetingContext
from conftest import ScriptedLLM

FAKE = LLMConfig(provider="fake")


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
    assert s.meta["prompts"] == "organize@v1+summary@v2" and s.meta["chunks"] == 1


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
    assert "SUP-48211" in EmailPolicy.build(EmailOptions(), icap.recommendations, None, kept).jira_keys


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
    pol = EmailPolicy.build(EmailOptions(), icap.recommendations, None)
    body = "Hi Dana,\n\nWhat we found:\n- one\n- two\n\nNext steps:\n1. Me: a\n2. You: b"
    assert "no_markdown" not in {r.name for r in failed(check_email(EmailDraft(subject="s", body=body), pol))}
    clean, fixes = enforce_email_text("Hi,\n\n* one\n+ two\n• three\n- four", pol)
    assert clean.count("- ") == 4 and "*" not in clean and "•" not in clean and fixes


def test_email_repair_asks_to_remove_filler(icap):
    s = MeetingSummary(**icap.golden_summary.model_dump())
    filler = json.dumps({"subject": "Recap", "body": "Hi Dana,\n\nThanks for your time today. Here is a quick recap.\n\nNext steps:\n1. Me: send the steps."})
    clean = json.dumps({"subject": "Recap", "body": "Hi Dana,\n\nNext steps:\n1. Me: send the steps."})
    llm = ScriptedLLM([filler, clean])
    d = draft_email(icap.transcript, s, icap.style, icap.recommendations, llm)
    assert "Thanks for your time" not in d.body and d.meta["repairs"] == 1 and not d.warnings
    assert "no_filler" in llm.calls[1][0][-1].content


@pytest.mark.parametrize("sid", list_scenarios())
def test_goldens_follow_the_style_rules(sid):
    sc = load_scenario(sid)
    summary = MeetingSummary(**sc.golden_summary.model_dump())
    text = summary.plain_language + "\n" + "\n".join(f"- {c.text}" for c in summary.all_claims())
    for body in (text, sc.golden_email.body):
        bad = [str(r) for r in check_style(body) if not r.passed]
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
