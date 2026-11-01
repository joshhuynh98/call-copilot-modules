"""Summary and email modules against scripted (misbehaving) models, plus the fake-provider happy path."""
import json

from callcopilot.modules.email.checks import EmailPolicy, check_email
from callcopilot.shared.checks import failed
from callcopilot.shared.contracts import MeetingSummary
from callcopilot.shared.llm import LLMConfig
from callcopilot.modules.email import draft_email
from callcopilot.modules.summary import summarize
from callcopilot.app.pipeline import make_llm, run_email, run_summary
from conftest import ScriptedLLM

FAKE = LLMConfig(provider="fake")


def test_summary_on_fake_provider(icap):
    s = run_summary(icap.transcript, FAKE)
    assert s.case_id == "CASE-24101" and s.product.version == "5.8.0" and s.meta["chunks"] == 1
    assert {a.owner for a in s.next_actions} >= {"agent", "customer"}


def test_summary_drops_unknown_evidence_and_marks_ungrounded_claims_uncertain(icap):
    core = icap.golden_summary.model_copy(deep=True)
    core.issues[0].segment_ids = ["seg_002", "seg_999"]
    core.findings[0].segment_ids = ["seg_404"]
    s = summarize(icap.transcript, ScriptedLLM([core.model_dump_json()]), FAKE)
    assert s.issues[0].segment_ids == ["seg_002"] and not s.issues[0].uncertain
    assert s.findings[0].segment_ids == [] and s.findings[0].uncertain


def test_long_transcripts_are_map_reduced(icap):
    llm = ScriptedLLM([icap.golden_summary.model_dump_json()])
    s = summarize(icap.transcript, llm, LLMConfig(provider="fake", max_context_tokens=120))
    assert s.meta["chunks"] > 1
    assert len(llm.calls) == s.meta["chunks"] + 2  # one organise per chunk, a merge, then the writer
    assert [c[1].task for c in llm.calls[-2:]] == ["organize_reduce", "summary"]


def test_summary_marks_low_confidence_segments(icap):
    noisy = next(__import__("callcopilot.shared.sources", fromlist=["x"]).load_scenario("messy_noisy_call").transcript for _ in [0])
    s = run_summary(noisy, FAKE)
    assert s.low_confidence_segment_ids == noisy.low_confidence_ids() and s.low_confidence_segment_ids
    assert s.product.uncertain and s.product.version is None


def test_email_on_fake_provider_passes_all_checks(icap):
    s = run_summary(icap.transcript, FAKE)
    d = run_email(icap.transcript, s, icap.style, icap.recommendations, FAKE)
    pol = EmailPolicy.build(icap.recommendations, s)
    assert not failed(check_email(d, pol, signature=icap.style.signature))
    assert d.body.endswith(icap.style.signature) and d.links_used and not d.warnings and d.meta["repairs"] == 0


BAD = json.dumps({
    "subject": "Recap",
    "body": "Hi Dana,\n\n**Next steps**\n- See SUP-48211 and https://opswat-internal.atlassian.example.invalid/browse/SUP-48211\n"
            "Let's schedule a call to review.\nDocs: https://docs.example-opswat.invalid/invented",
})
GOOD = json.dumps({"subject": "Recap", "body": "Hi Dana,\n\nThank you for your time during the meeting.\n\nNext steps:\n1. Me: send the steps.\n2. You: send the log."})


def build(icap, replies, summary=None):
    summary = summary or MeetingSummary(**icap.golden_summary.model_dump())
    llm = ScriptedLLM(replies)
    return draft_email(icap.transcript, summary, icap.style, icap.recommendations, llm), llm


def test_email_repair_loop_fixes_a_bad_first_draft(icap):
    d, llm = build(icap, [BAD, GOOD])
    assert d.meta["repairs"] == 1 and not d.warnings and "SUP-" not in d.body and "**" not in d.body
    assert "has these problems" in llm.calls[1][0][-1].content


def test_email_enforcement_is_last_resort_and_tells_the_agent(icap):
    d, _ = build(icap, [BAD])
    pol = EmailPolicy.build(icap.recommendations, MeetingSummary(**icap.golden_summary.model_dump()))
    assert not failed(check_email(d, pol, signature=icap.style.signature))
    assert any("ticket id" in w for w in d.warnings) and any("meeting" in w for w in d.warnings)


def test_email_gets_scope_statement_when_model_forgets(vdi):
    summary = MeetingSummary(**vdi.golden_summary.model_dump())
    forgot = json.dumps({"subject": "Recap", "body": "Hi Marcus,\n\nThank you for your time during the meeting.\n\nNext steps:\n1. Me: review the log."})
    d = draft_email(vdi.transcript, summary, vdi.style, vdi.recommendations, ScriptedLLM([forgot]))
    assert "outside OPSWAT's scope" in d.body and any("out-of-scope statement" in w for w in d.warnings)


def test_uncertain_summary_points_become_claims_to_verify():
    from callcopilot.shared.sources import load_scenario
    sc = load_scenario("messy_noisy_call")
    s = run_summary(sc.transcript, FAKE)
    d = run_email(sc.transcript, s, sc.style, sc.recommendations, FAKE)
    assert d.claims_needing_verification
    assert any("version" in c.claim.lower() for c in d.claims_needing_verification)


def test_prompt_never_receives_internal_ids_or_links(icap):
    _, llm = build(icap, [GOOD])
    prompt = "\n".join(m.content for m in llm.calls[0][0])
    assert "SUP-48211" not in prompt and "atlassian" not in prompt and "kb.internal" not in prompt
    assert "https://docs.example-opswat.invalid/icap/configuration/scan-timeouts" in prompt


def test_email_prompt_always_forbids_meeting_offers(icap):
    _, llm = build(icap, [GOOD])
    assert "Do NOT offer, suggest or request a call, meeting, demo or screen share" in llm.calls[0][0][0].content


def test_redaction_in_the_loop_restores_names(icap):
    seen = []

    def echo(messages):
        seen.append("\n".join(m.content for m in messages))
        return json.dumps({"subject": "Recap", "body": "Hi Dana,\n\nThank you for your time during the meeting.\n\nNext steps:\n1. Me: send the steps."})

    llm = make_llm(LLMConfig(provider="openai", url="x", model="m"), icap.transcript)
    llm.inner = ScriptedLLM([echo])
    s = MeetingSummary(**icap.golden_summary.model_dump())
    d = draft_email(icap.transcript, s, icap.style, icap.recommendations, llm)
    assert "Dana Ortiz" not in seen[0] and "Kestrel" not in seen[0] and d.body.startswith("Hi Dana")
