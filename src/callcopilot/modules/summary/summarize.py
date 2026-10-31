"""Meeting summary: transcript -> validated, evidence-linked MeetingSummary.

Short calls are summarised in one pass. If the rendered transcript exceeds the token budget
(AI_MAX_CONTEXT_TOKENS, default 12000 estimated tokens) it is split into chunks, each chunk is
summarised with the same schema, and a reduce step merges them.
"""
from __future__ import annotations

from callcopilot.contracts import MeetingSummary, Segment, SummaryCore, Transcript
from callcopilot.llm import LLMClient, LLMConfig, Tag, generate_structured
from callcopilot.llm.structured import schema_hint
from callcopilot.prompt_loader import load_prompt
from callcopilot.safety import UNTRUSTED_NOTICE, wrap_untrusted

CHUNK_OVERLAP_SEGMENTS = 2


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


def _finalize(core: SummaryCore, transcript: Transcript, *, repairs: int, chunks: int, prompt: str) -> MeetingSummary:
    """Deterministic clean-up: drop evidence ids that don't exist, mark claims without evidence as uncertain."""
    valid = transcript.segment_ids()
    summary = MeetingSummary(**core.model_dump())

    def fix(obj):
        obj.segment_ids = [i for i in obj.segment_ids if i in valid]

    for claim in summary.all_claims():
        fix(claim)
        if not claim.segment_ids:
            claim.uncertain = True
    for item in [summary.product, *summary.next_actions, *summary.third_party_components]:
        fix(item)
    summary.case_id = transcript.metadata.case_id
    summary.low_confidence_segment_ids = transcript.low_confidence_ids()
    summary.meta = {"prompt": prompt, "repairs": repairs, "chunks": chunks}
    return summary


def summarize(transcript: Transcript, llm: LLMClient, config: LLMConfig | None = None) -> MeetingSummary:
    config = config or LLMConfig()
    case_id = transcript.metadata.case_id
    prompt = load_prompt("summary")
    segments = transcript.segments
    chunks = chunk_segments(segments, transcript, config.transcript_token_budget)
    schema = schema_hint(SummaryCore)

    def run(chunk: list[Segment], note: str) -> tuple[SummaryCore, int]:
        msgs = prompt.render(
            untrusted_notice=UNTRUSTED_NOTICE, metadata=_metadata_block(transcript), chunk_note=note, schema=schema,
            transcript=wrap_untrusted("TRANSCRIPT", transcript.render(chunk)),
        )
        return generate_structured(llm, msgs, SummaryCore, tag=Tag("summary", case_id))

    if len(chunks) == 1:
        core, repairs = run(chunks[0], "")
        return _finalize(core, transcript, repairs=repairs, chunks=1, prompt=prompt.label)

    partials, repairs = [], 0
    for i, chunk in enumerate(chunks, 1):
        note = (f"This is part {i} of {len(chunks)} of the call; summarise only this part "
                f"(the first {CHUNK_OVERLAP_SEGMENTS if i > 1 else 0} segments overlap the previous part).\n\n")
        core, r = run(chunk, note)
        partials.append(core.model_dump_json())
        repairs += r
    reduce_prompt = load_prompt("summary_reduce")
    msgs = reduce_prompt.render(
        untrusted_notice=UNTRUSTED_NOTICE, metadata=_metadata_block(transcript), schema=schema,
        partials="\n".join(f"PART {i}: {p}" for i, p in enumerate(partials, 1)),
    )
    core, r = generate_structured(llm, msgs, SummaryCore, tag=Tag("summary_reduce", case_id))
    return _finalize(core, transcript, repairs=repairs + r, chunks=len(chunks), prompt=f"{prompt.label}+{reduce_prompt.label}")
