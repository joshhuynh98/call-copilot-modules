"""Negative cases: every check must catch what it exists to catch."""
import pytest

from callcopilot.shared import policy
from callcopilot.modules.email.checks import EmailPolicy, check_email, enforce_email_text
from callcopilot.modules.recommendations.checks import check_recommendations
from callcopilot.shared.checks import failed
from callcopilot.shared.contracts import EmailDraft, MeetingSummary, Source, SourceType

DOC = "https://docs.example-opswat.invalid/icap/configuration/scan-timeouts"


def pol(icap):
    summary = MeetingSummary(**icap.golden_summary.model_dump())
    return EmailPolicy.build(icap.recommendations, summary)


def failed_names(icap, body, subject="Follow-up"):
    return {r.name for r in failed(check_email(EmailDraft(subject=subject, body=body), pol(icap)))}


GOOD = "Hi Dana,\n\nThanks for your time today.\n\nNext steps:\n1. Me: send the steps.\n2. You: send the log."


def test_good_email_passes(icap):
    assert failed_names(icap, GOOD + f"\n\nMore: {DOC}") == set()


@pytest.mark.parametrize("body,expected", [
    ("Hi Dana,\n\n**Next steps**\nsend the log", "no_markdown"),
    ("Hi Dana,\n\n* send the log\n* wait", "no_markdown"),
    ("Hi Dana,\n\n• send the log", "no_markdown"),
    ("Hi Dana,\n\n# Summary\nsend the log", "no_markdown"),
    ("Hi Dana,\n\nSee [the doc](" + DOC + ")", "no_markdown"),
    ("Hi Dana,\n\nRun `restart` now", "no_markdown"),
    ("Hi Dana,\n\nSee SUP-48211 for details.", "no_jira_keys"),
    ("Hi Dana,\n\nSee https://opswat-internal.atlassian.example.invalid/wiki/spaces/SUP/pages/20114", "no_internal_links"),
    ("Hi Dana,\n\nSee https://kb.internal.example.invalid/articles/icap-500-large-archives", "urls_public_and_provided"),
    ("Hi Dana,\n\nSee https://docs.example-opswat.invalid/made-up-page", "urls_public_and_provided"),
    ("Hi Dana,\n\n" + "word " * 70, "short_paragraphs"),
])
def test_email_violations_are_caught(icap, body, expected):
    assert expected in failed_names(icap, body)


@pytest.mark.parametrize("sentence", [
    "Let's schedule a call to go over this.", "I can set up a quick call tomorrow.", "Happy to jump on a Zoom call.",
    "Are you free for a meeting this week?", "When are you available?", "We will call you tomorrow.", "Let's connect next week.",
])
def test_meeting_offers_are_caught(icap, sentence):
    assert "no_meeting_offer" in failed_names(icap, f"Hi Dana,\n\n{sentence}")


@pytest.mark.parametrize("sentence", [
    "Thanks for your time on our call today.", "As discussed during the call, the timeout is 60 seconds.",
    "I have summarized our call below.", "Please set up the log collection and send it.",
])
def test_plain_references_to_the_call_are_not_offers(icap, sentence):
    assert "no_meeting_offer" not in failed_names(icap, f"Hi Dana,\n\n{sentence}")


def test_meeting_offers_are_never_allowed_there_is_no_setting(icap):
    offer = "Hi Dana," + chr(10) + chr(10) + "Happy to jump on a quick call."
    assert "no_meeting_offer" in failed_names(icap, offer)


def test_jira_key_detection_ignores_common_tokens():
    assert policy.find_jira_keys("SHA-256, TLS-12, CVE-2024-1234, UTF-8") == []
    assert policy.find_jira_keys("see SUP-48211 and MDCORE-12") == ["MDCORE-12", "SUP-48211"]


def test_enforce_makes_a_bad_email_pass(icap):
    p = pol(icap)
    bad = ("Hi Dana,\n\n**Next steps**\n- See SUP-48211 and https://opswat-internal.atlassian.example.invalid/browse/SUP-48211\n"
           "Let's schedule a call to review.\nDocs: " + DOC)
    clean, fixes = enforce_email_text(bad, p)
    assert fixes and DOC in clean and "SUP-48211" not in clean and "**" not in clean and "schedule" not in clean
    assert not failed(check_email(EmailDraft(subject="s", body=clean), p))


def test_third_party_scope_and_troubleshooting(vdi):
    summary = MeetingSummary(**vdi.golden_summary.model_dump())
    p = EmailPolicy.build(vdi.recommendations, summary)
    no_scope = {r.name for r in failed(check_email(EmailDraft(subject="s", body="Hi Marcus,\n\nWe looked at the logs."), p))}
    assert "third_party_scope_statement" in no_scope
    bad = {r.name for r in failed(check_email(EmailDraft(subject="s", body="Hi Marcus,\n\nThe proxy is third-party and outside OPSWAT's scope.\nPlease restart the proxy and update the firewall."), p))}
    assert "no_third_party_troubleshooting" in bad
    ok = "Hi Marcus,\n\nThe SSL inspection proxy is a third-party product and outside OPSWAT's scope; your network team should handle it."
    assert not failed(check_email(EmailDraft(subject="s", body=ok), p))


def test_hyphenated_and_spaced_terms_match():
    assert policy.mentions_term("the SSL-inspection device", ["ssl inspection"]) == ["ssl inspection"]


# -- recommendations --------------------------------------------------------------------------------
def recs_failed(icap, mutate):
    recs = icap.recommendations.model_copy(deep=True)
    mutate(recs)
    return {f"{r.name}" for r in failed(check_recommendations(recs, icap.transcript))}


def test_good_recommendations_pass(icap):
    assert not failed(check_recommendations(icap.recommendations, icap.transcript))


def test_recommendation_without_source_fails(icap):
    assert "source_count" in recs_failed(icap, lambda r: r.recommendations[0].sources.clear())


def test_too_many_sources_fails(icap):
    def m(r):
        s = r.recommendations[0].sources
        s.extend([s[0].model_copy(update={"url": f"https://docs.example-opswat.invalid/{i}"}) for i in range(4)])
    assert "source_count" in recs_failed(icap, m)


def test_bad_urls_and_types_fail(icap):
    def m(r):
        r.recommendations[0].sources[0].url = "not a url"
        r.recommendations[2].sources[0].jira_key = "SUP-1"  # does not match the URL's key
        r.recommendations[1].sources.append(Source(type=SourceType.public_doc, label="x", url="https://opswat-internal.atlassian.example.invalid/wiki/x"))
    names = recs_failed(icap, m)
    assert "source_types_and_urls" in names


def test_long_action_and_rationale_fail(icap):
    def m(r):
        r.recommendations[0].action = "x" * 200
        r.recommendations[1].rationale = ""
    assert "glanceable_text" in recs_failed(icap, m)


def test_unknown_trigger_segment_fails(icap):
    assert "trigger_segments_valid" in recs_failed(icap, lambda r: r.recommendations[0].trigger_segment_ids.append("seg_999"))


def test_duplicates_fail(icap):
    def m(r):
        dup = r.recommendations[0].model_copy(update={"id": "rec_dup", "rank": 4})
        r.recommendations.append(dup)
    assert "no_duplicates" in recs_failed(icap, m)


def test_bad_rank_order_fails(icap):
    def m(r):
        r.recommendations[0].rank, r.recommendations[1].rank = 2, 1
    assert "ranks_ordered" in recs_failed(icap, m)


def test_third_party_troubleshooting_in_recommendation_fails(icap):
    def m(r):
        r.recommendations[0].action = "Restart the customer's proxy and update the firewall rules."
    assert "no_third_party_troubleshooting" in recs_failed(icap, m)


def test_irrelevant_recommendation_is_flagged(icap):
    def m(r):
        rec = r.recommendations[0]
        rec.title, rec.action, rec.rationale = "Bake sourdough", "Knead dough overnight.", "Bread rises slowly."
        rec.sources[:] = [Source(type=SourceType.public_doc, label="Baking guide", url="https://docs.example-opswat.invalid/bread", snippet="flour water yeast")]
    assert "relevance_to_trigger_segments" in recs_failed(icap, m)
