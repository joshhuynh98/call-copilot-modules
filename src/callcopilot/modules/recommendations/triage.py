"""Triage of messy recommendations, and which of their sources may be linked from a customer email.

The organise stage (LLM) flags relevance per recommendation and, when the sources were opened first, whether their
content supports the action. Code then (a) drops flags for ids that do not exist, (b) marks code-detected duplicates as
not relevant, (c) makes sure every recommendation has a note, and (d) refuses claims about sources that were never opened
(no review content means no "supported" verdict and no gist). The chatbot still shows the agent the full list; the email
uses only the triaged one, with only the links that checked out.
"""
from __future__ import annotations

from callcopilot.modules.recommendations.checks import find_duplicates
from callcopilot.shared import policy
from callcopilot.shared.contracts import RecNote, RecommendationSet, SourceReview

OPENED = ("ok", "redirected")


def _clean_gist(text: str) -> str:
    """A gist goes into the email prompt: no links and no ticket ids, one line."""
    for url in policy.extract_urls(text):
        text = text.replace(url, "")
    for key in policy.find_jira_keys(text):
        text = text.replace(key, "")
    return " ".join(text.split())[:300]


def complete_notes(recs: RecommendationSet | None, notes: list[RecNote], n_issues: int,
                   reviews: list[SourceReview] | None = None) -> list[RecNote]:
    if recs is None:
        return []
    by_id = {n.rec_id: n for n in notes if n.rec_id in {r.id for r in recs.recommendations}}
    dups = find_duplicates(recs.recommendations)
    opened = {rv.url for rv in reviews or [] if rv.status in OPENED and rv.excerpt}
    out = []
    for r in recs.recommendations:
        note = by_id.get(r.id) or RecNote(rec_id=r.id)
        if note.issue is not None and not (0 <= note.issue < n_issues):
            note.issue = None
        if r.id in dups and note.relevant:
            note.relevant, note.note = False, f"duplicate of {dups[r.id]}"
        if not any(s.url in opened for s in r.sources):  # nothing was read, so nothing can be claimed about it
            note.supported, note.gist = None, ""
        else:
            note.gist = _clean_gist(note.gist)
        out.append(note)
    return out


def triage_recommendations(recs: RecommendationSet | None, notes: list[RecNote]) -> RecommendationSet | None:
    """The relevant subset, in original order. If triage would remove everything, keep everything."""
    if recs is None or not notes:
        return recs
    keep = {n.rec_id for n in notes if n.relevant}
    kept = [r for r in recs.recommendations if r.id in keep or r.id not in {n.rec_id for n in notes}]
    if not kept:
        return recs
    return recs.model_copy(update={"recommendations": kept})


def email_link_recs(recs: RecommendationSet | None, notes: list[RecNote], reviews: list[SourceReview] | None) -> RecommendationSet | None:
    """Triaged recommendations whose sources are safe to offer to the customer.

    A source is dropped when its page does not exist, or when the reviewed content does not support the recommendation.
    A link that redirected is replaced by the page it really leads to. Links that could not be checked (blocked, error)
    are kept; the email draft warns the agent about them.
    """
    triaged = triage_recommendations(recs, notes)
    if triaged is None:
        return None
    by_url = {rv.url: rv for rv in reviews or []}
    unsupported = {n.rec_id for n in notes if n.supported is False}
    out = []
    for r in triaged.recommendations:
        sources = []
        for s in r.sources:
            rv = by_url.get(s.url)
            if r.id in unsupported or (rv is not None and rv.status == "not_found"):
                continue
            if rv is not None and rv.status == "redirected" and rv.final_url:
                s = s.model_copy(update={"url": rv.final_url})
            sources.append(s)
        out.append(r.model_copy(update={"sources": sources}))
    return triaged.model_copy(update={"recommendations": out})
