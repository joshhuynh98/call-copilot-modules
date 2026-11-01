"""Every fixture scenario must be valid and its golden outputs must pass our own quality checks."""
import pytest

from callcopilot.modules.email.checks import EmailPolicy, check_email
from callcopilot.modules.recommendations.checks import check_recommendations
from callcopilot.shared.checks import failed
from callcopilot.modules.summary.checks import check_summary
from callcopilot.shared.contracts import EmailDraft, MeetingSummary
from callcopilot.shared.sources import list_scenarios, load_scenario

SCENARIOS = list_scenarios()


def test_at_least_one_scenario():
    assert SCENARIOS


@pytest.mark.parametrize("sid", SCENARIOS)
def test_scenario_files_validate(sid):
    sc = load_scenario(sid)
    assert sc.transcript.metadata.case_id == sc.recommendations.case_id
    ids = [s.id for s in sc.transcript.segments]
    assert len(ids) == len(set(ids)), "duplicate segment ids"
    for key in ("docs", "jira", "confluence", "cases", "customers"):
        assert key in sc.kb
    for rule in sc.chat_script["rules"]:
        assert "answer" in rule and rule["when_any"]
    assert sc.expectations["summary"] and sc.expectations["email"] and sc.expectations["recommendations"]


@pytest.mark.parametrize("sid", SCENARIOS)
def test_golden_recommendations_pass_quality_bar(sid):
    sc = load_scenario(sid)
    bad = failed(check_recommendations(sc.recommendations, sc.transcript))
    assert not bad, [str(b) for b in bad]


@pytest.mark.parametrize("sid", SCENARIOS)
def test_golden_summary_passes_checks(sid):
    sc = load_scenario(sid)
    summary = MeetingSummary(**sc.golden_summary.model_dump(), case_id=sc.transcript.metadata.case_id,
                             low_confidence_segment_ids=sc.transcript.low_confidence_ids())
    bad = failed(check_summary(summary, sc.transcript))
    assert not bad, [str(b) for b in bad]


@pytest.mark.parametrize("sid", SCENARIOS)
def test_golden_email_passes_checks_and_expectations(sid):
    sc = load_scenario(sid)
    summary = MeetingSummary(**sc.golden_summary.model_dump())
    pol = EmailPolicy.build(sc.recommendations, summary)
    draft = EmailDraft(subject=sc.golden_email.subject, body=sc.golden_email.body + "\n\n" + sc.style.signature)
    bad = failed(check_email(draft, pol, signature=sc.style.signature))
    assert not bad, [str(b) for b in bad]

    exp = sc.expectations["email"]
    low = draft.body.lower()
    for needle in exp["must_mention"]:
        assert needle.lower() in low, f"golden email should mention '{needle}'"
    for needle in exp["must_not_mention"]:
        assert needle.lower() not in low, f"golden email must not mention '{needle}'"
    for url in exp.get("required_public_links", []):
        assert url in draft.body
    assert bool(summary.third_party_components) == exp["third_party_scope_statement"]


@pytest.mark.parametrize("sid", SCENARIOS)
def test_golden_recommendation_expectations(sid):
    sc = load_scenario(sid)
    exp = sc.expectations["recommendations"]
    top = sc.recommendations.recommendations[0]
    for needle in exp["top_action_contains"]:
        assert needle.lower() in top.action.lower()
    types = {s.type.value for s in sc.recommendations.sources()}
    for t in exp["required_source_types"]:
        assert t in types
