"""Triage of messy recommendations: drop duplicates and items unrelated to the call before we use them.

The organise stage (LLM) flags relevance per recommendation. Code then (a) drops flags for ids that do not
exist, (b) marks code-detected duplicates as not relevant, (c) makes sure every recommendation has a note, so
nothing is silently lost. The chatbot still shows the agent the full list; only the email uses the triaged one.
"""
from __future__ import annotations

from callcopilot.checks.recommendations import find_duplicates
from callcopilot.contracts import RecNote, RecommendationSet


def complete_notes(recs: RecommendationSet | None, notes: list[RecNote], n_issues: int) -> list[RecNote]:
    if recs is None:
        return []
    by_id = {n.rec_id: n for n in notes if n.rec_id in {r.id for r in recs.recommendations}}
    dups = find_duplicates(recs.recommendations)
    out = []
    for r in recs.recommendations:
        note = by_id.get(r.id) or RecNote(rec_id=r.id)
        if note.issue is not None and not (0 <= note.issue < n_issues):
            note.issue = None
        if r.id in dups and note.relevant:
            note.relevant, note.note = False, f"duplicate of {dups[r.id]}"
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
