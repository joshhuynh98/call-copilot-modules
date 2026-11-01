"""INPUT contract: transcript module -> us.

Lenient on purpose: only `id`, `speaker` and `text_original` are required per segment, so a
partially-filled payload from the transcript module still validates.
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1.0"
LOW_CONFIDENCE = 0.6


class Speaker(StrEnum):
    agent = "agent"
    customer = "customer"


class Segment(BaseModel):
    id: str = Field(description="Stable segment id, e.g. seg_007. Referenced by recommendations and our claims.")
    speaker: Speaker
    start_ms: int = Field(default=0, ge=0, description="Milliseconds from call start.")
    end_ms: int = Field(default=0, ge=0)
    text_original: str
    text_translated: str | None = Field(default=None, description="English text; null if the original is already English.")
    language: str = "en"
    confidence: float | None = Field(default=None, ge=0, le=1, description="ASR/translation confidence if known.")
    is_final: bool = True

    @property
    def text(self) -> str:
        """Text we reason over: translated if present, else original."""
        return self.text_translated or self.text_original

    @property
    def low_confidence(self) -> bool:
        return self.confidence is not None and self.confidence < LOW_CONFIDENCE


class CallMetadata(BaseModel):
    case_id: str
    customer_name: str
    customer_contact: str | None = Field(default=None, description="Person on the call; used for the email greeting.")
    agent_name: str | None = None
    product: str | None = Field(default=None, description="Product as recorded on the case, if any. May be wrong or missing.")
    product_version: str | None = None
    call_start: datetime | None = None
    call_end: datetime | None = None


class Transcript(BaseModel):
    schema_version: str = SCHEMA_VERSION
    metadata: CallMetadata
    segments: list[Segment]

    def segment_ids(self) -> set[str]:
        return {s.id for s in self.segments}

    def by_id(self, seg_id: str) -> Segment | None:
        return next((s for s in self.segments if s.id == seg_id), None)

    def low_confidence_ids(self) -> list[str]:
        return [s.id for s in self.segments if s.low_confidence]

    def full_text(self) -> str:
        return "\n".join(f"{s.text_original}\n{s.text_translated or ''}" for s in self.segments)

    def render(self, segments: list[Segment] | None = None) -> str:
        """One line per segment: `[seg_001] CUSTOMER: text` with low-confidence lines flagged."""
        lines = []
        for s in segments if segments is not None else self.segments:
            flag = " [LOW CONFIDENCE]" if s.low_confidence else ""
            lines.append(f"[{s.id}] {s.speaker.value.upper()}{flag}: {s.text}")
        return "\n".join(lines)
