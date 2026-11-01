"""Meeting summary in two stages.

1. ORGANISE: read the (possibly messy) transcript and recommendations and group them into a clean brief:
   issues, before the call, in the call, findings, next actions by owner, open questions, third-party parts,
   plus triage of the recommendations. Long calls are organised chunk by chunk and merged (map-reduce).
2. WRITE: rewrite the brief concisely in plain English (style rules) without changing facts.

Code then guards the result: unknown evidence ids are dropped, claims without evidence become `uncertain`,
and anything the writer lost is restored from the brief.
"""
from __future__ import annotations

from callcopilot.shared.contracts import (
    CallBrief, Claim, MeetingSummary, RecommendationSet, Segment, SourceReview, SummaryContent, SummaryCore, Transcript,
)
from callcopilot.shared.llm import LLMClient, LLMConfig, Tag, generate_structured
from callcopilot.shared.llm.structured import schema_hint
from callcopilot.modules.recommendations import complete_notes
from callcopilot.shared.prompt_loader import load_prompt
from callcopilot.shared.safety import UNTRUSTED_NOTICE, wrap_untrusted

CHUNK_OVERLAP_SEGMENTS = 2
CLAIM_SECTIONS = ("issues", "before_call", "in_call", "findings", "open_questions")


def estimate_tokens(text: str) -> int:
    return len(text) // 4 + 1


def chunk_segments(segments: list[Segment], transcript: Transcript, budget: int) -> list[list[Segment]]:
    """Greedy split by estimated tokens, with a small overlap so context isn't cut mid-exchange."""
    chunks: list[list[Segment]] = []
    current: list[Segment] = []
    used = 0
    for seg in segments:
        cost = estimate_tokens(transcript.render([seg]))
        if current and used + cost > budget:
            chunks.append(current)
            current = current[-CHUNK_OVERLAP_SEGMENTS:]
            used = sum(estimate_tokens(transcript.render([s])) for s in current)
        current.append(seg)
        used += cost
    if current:
        chunks.append(current)
    return chunks


def _metadata_block(transcript: Transcript) -> str:
    m = transcript.metadata
    return (f"case_id: {m.case_id}\ncustomer: {m.customer_name}\nproduct on case: {m.product or 'unknown'}\n"
            f"version on case: {m.product_version or 'unknown'}")


MAX_REVIEW_CHARS = 600  # per source, in the prompt


def _review_line(source, review: SourceReview | None) -> str:
    label = f"[{source.type.value}] {source.label}"
    if review is None:
        return f"  - {label}: not reviewed"
    if review.status in ("ok", "redirected") and review.excerpt:
        return f"  - {label}: OPENED. It says: {' '.join(review.excerpt.split())[:MAX_REVIEW_CHARS]}"
    if review.status == "not_found":
        return f"  - {label}: NOT FOUND ({review.note or 'the page does not exist'})"
    return f"  - {label}: could not be read ({review.status}: {review.note or 'no reason given'})"


def render_recommendations(recs: RecommendationSet | None, reviews: list[SourceReview] | None = None) -> str:
    if recs is None or not recs.recommendations:
        return "(none provided)"
    by_url = {rv.url: rv for rv in reviews or []}
    lines = []
    for r in recs.recommendations:
        types = ", ".join(sorted({s.type.value for s in r.sources})) or "no sources"
        lines.append(f"{r.id} (rank {r.rank}): {r.title}. Action: {r.action} Why: {r.rationale} "
                     f"[sources: {types}; triggered by: {', '.join(r.trigger_segment_ids) or 'unknown'}]")
        if reviews is not None:
            lines += [_review_line(s, by_url.get(s.url)) for s in r.sources]
    return wrap_untrusted("RECOMMENDATIONS", "\n".join(lines))


def _keep_facts(core: SummaryCore, brief: CallBrief) -> None:
    """The writer may rephrase but must not lose or alter facts: restore from the brief where it did."""
    for name in CLAIM_SECTIONS:
        written: list[Claim] = getattr(core, name)
        source: list[Claim] = getattr(brief, name)
        doubt_lost = len(written) != len(source) and any(c.uncertain for c in source) and not any(c.uncertain for c in written)
        if (not written and source) or doubt_lost:
            setattr(core, name, [c.model_copy() for c in source])  # keep the brief's wording rather than lose content or doubt
        elif len(written) == len(source):  # the prompt says "same order", so items line up
            for w, b in zip(written, source):
                w.uncertain = w.uncertain or b.uncertain
                w.segment_ids = w.segment_ids or b.segment_ids
    # Next actions: the writer must keep the same actions per owner. If not, use the brief's list untouched.
    owners = lambda actions: sorted(a.owner for a in actions)  # noqa: E731
    if owners(core.next_actions) != owners(brief.next_actions):
        core.next_actions = [a.model_copy() for a in brief.next_actions]
    core.product = brief.product
    core.third_party_components = list(brief.third_party_components)


def _finalize(core: SummaryCore, brief: CallBrief, transcript: Transcript, recs: RecommendationSet | None,
              *, repairs: int, chunks: int, prompts: str, reviews: list[SourceReview] | None = None) -> MeetingSummary:
    """Deterministic clean-up: drop evidence ids that don't exist, mark claims without evidence as uncertain."""
    _keep_facts(core, brief)
    valid = transcript.segment_ids()
    summary = MeetingSummary(**core.model_dump())

    for claim in summary.all_claims():
        claim.segment_ids = [i for i in claim.segment_ids if i in valid]
        if not claim.segment_ids:
            claim.uncertain = True
    for item in [summary.product, *summary.next_actions, *summary.third_party_components]:
        item.segment_ids = [i for i in item.segment_ids if i in valid]
    summary.case_id = transcript.metadata.case_id
    summary.low_confidence_segment_ids = transcript.low_confidence_ids()
    summary.recommendation_notes = complete_notes(recs, brief.recommendation_notes, len(summary.issues), reviews)
    summary.source_reviews = list(reviews or [])
    summary.meta = {"prompts": prompts, "repairs": repairs, "chunks": chunks}
    return summary


def summarize(transcript: Transcript, llm: LLMClient, config: LLMConfig | None = None,
              recommendations: RecommendationSet | None = None, reviews: list[SourceReview] | None = None) -> MeetingSummary:
    config = config or LLMConfig()
    case_id = transcript.metadata.case_id
    organize = load_prompt("organize")
    chunks = chunk_segments(transcript.segments, transcript, config.transcript_token_budget)
    brief_schema = schema_hint(CallBrief)
    recs_text = render_recommendations(recommendations, reviews)
    repairs = 0
    used = [organize.label]

    def organise_chunk(chunk: list[Segment], note: str, recs_block: str) -> CallBrief:
        nonlocal repairs
        msgs = organize.render(
            untrusted_notice=UNTRUSTED_NOTICE, metadata=_metadata_block(transcript), chunk_note=note,
            transcript=wrap_untrusted("TRANSCRIPT", transcript.render(chunk)), recommendations=recs_block, schema=brief_schema,
        )
        brief, r = generate_structured(llm, msgs, CallBrief, tag=Tag("organize", case_id))
        repairs += r
        return brief

    if len(chunks) == 1:
        brief = organise_chunk(chunks[0], "", recs_text)
    else:
        partials = []
        for i, chunk in enumerate(chunks, 1):
            note = (f"This is part {i} of {len(chunks)} of the call; organise only this part "
                    f"(the first {CHUNK_OVERLAP_SEGMENTS if i > 1 else 0} segments overlap the previous part).\n\n")
            partials.append(organise_chunk(chunk, note, "(recommendations are triaged when the parts are merged)")
                            .model_dump_json(exclude={"recommendation_notes"}))
        reduce_prompt = load_prompt("organize_reduce")
        msgs = reduce_prompt.render(
            untrusted_notice=UNTRUSTED_NOTICE, metadata=_metadata_block(transcript), schema=brief_schema,
            partials="\n".join(f"PART {i}: {p}" for i, p in enumerate(partials, 1)), recommendations=recs_text,
        )
        brief, r = generate_structured(llm, msgs, CallBrief, tag=Tag("organize_reduce", case_id))
        repairs += r
        used.append(reduce_prompt.label)

    write = load_prompt("summary")
    used.append(write.label)
    msgs = write.render(
        untrusted_notice=UNTRUSTED_NOTICE, style_rules=load_prompt("style_rules").system,
        brief=brief.model_dump_json(exclude={"recommendation_notes"}, indent=1), schema=schema_hint(SummaryCore),
    )
    core, r = generate_structured(llm, msgs, SummaryCore, tag=Tag("summary", case_id))
    return _finalize(core, brief, transcript, recommendations, repairs=repairs + r, chunks=len(chunks), prompts="+".join(used), reviews=reviews)


__all__ = ["summarize", "SummaryContent"]
