"""INPUT contract: recommendations module -> us.

The contract is lenient (limits are NOT enforced by the schema) so the eval harness can report
exactly which items miss the quality bar instead of rejecting a whole payload.
See docs/recommendations-spec.md for the quality bar.
"""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from .transcript import SCHEMA_VERSION


class SourceType(StrEnum):
    public_doc = "public_doc"
    internal_kb = "internal_kb"
    confluence = "confluence"
    jira = "jira"
    past_case = "past_case"


SOURCE_LABELS = {
    SourceType.public_doc: "Public doc",
    SourceType.internal_kb: "Internal KB",
    SourceType.confluence: "Confluence",
    SourceType.jira: "Jira",
    SourceType.past_case: "Past case",
}


class Source(BaseModel):
    type: SourceType
    label: str = Field(description="Human-readable title of the page/ticket.")
    url: str
    jira_key: str | None = None
    snippet: str | None = None

    @property
    def customer_visible(self) -> bool:
        """Only public docs may be linked from customer-facing text."""
        return self.type == SourceType.public_doc


class Recommendation(BaseModel):
    id: str
    rank: int = Field(description="1 = most relevant / likely next step.")
    title: str
    action: str = Field(description="One-line action the agent can take now (quality bar: <=120 chars).")
    rationale: str = Field(description="One-to-two-line why (quality bar: <=240 chars).")
    sources: list[Source] = Field(default_factory=list, description="Quality bar: 1-4 sources.")
    confidence: float | None = Field(default=None, ge=0, le=1)
    trigger_segment_ids: list[str] = Field(default_factory=list, description="Transcript segments that triggered this.")


class RecommendationSet(BaseModel):
    schema_version: str = SCHEMA_VERSION
    case_id: str
    recommendations: list[Recommendation]

    def sources(self) -> list[Source]:
        return [s for r in self.recommendations for s in r.sources]

    def public_sources(self) -> list[Source]:
        return [s for s in self.sources() if s.customer_visible]
