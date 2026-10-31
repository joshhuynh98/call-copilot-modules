"""OUTPUT contracts: what our modules hand to teammates / the UI."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from .recommendations import SourceType
from .transcript import SCHEMA_VERSION


class Claim(BaseModel):
    text: str
    segment_ids: list[str] = Field(default_factory=list, description="Transcript segments that support this claim.")
    uncertain: bool = Field(default=False, description="True if the transcript is ambiguous, noisy or contradictory here.")


class ProductInfo(BaseModel):
    name: str | None = None
    version: str | None = Field(default=None, description="Only a version actually stated in the transcript; never inferred.")
    segment_ids: list[str] = Field(default_factory=list)
    uncertain: bool = False


class NextAction(BaseModel):
    owner: Literal["agent", "customer", "opswat_other"]
    action: str
    due: str | None = None
    segment_ids: list[str] = Field(default_factory=list)


class ThirdPartyComponent(BaseModel):
    name: str = Field(description="Non-OPSWAT product involved in the issue (VDI, proxy, firewall, SSL inspection, other vendor).")
    role: str = Field(description="What it does in the customer's setup / why it matters to the issue.")
    segment_ids: list[str] = Field(default_factory=list)


class SummaryCore(BaseModel):
    """The part the LLM produces."""
    plain_language: str = Field(description="2-4 sentence plain-language wrap-up (<=100 words).")
    issue: Claim
    product: ProductInfo
    steps_taken: list[Claim] = Field(default_factory=list)
    findings: list[Claim] = Field(default_factory=list)
    next_actions: list[NextAction] = Field(default_factory=list)
    open_questions: list[Claim] = Field(default_factory=list)
    third_party_components: list[ThirdPartyComponent] = Field(default_factory=list)


class MeetingSummary(SummaryCore):
    """SummaryCore plus fields we fill in deterministically in code."""
    schema_version: str = SCHEMA_VERSION
    case_id: str = ""
    low_confidence_segment_ids: list[str] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)

    def all_claims(self) -> list[Claim]:
        return [self.issue, *self.steps_taken, *self.findings, *self.open_questions]


class ClaimToVerify(BaseModel):
    claim: str
    reason: str = ""
    segment_ids: list[str] = Field(default_factory=list)


class EmailCore(BaseModel):
    """The part the LLM produces. `body` excludes the signature (added in code)."""
    subject: str
    body: str
    claims_needing_verification: list[ClaimToVerify] = Field(default_factory=list)


class EmailDraft(BaseModel):
    """Always a DRAFT: the agent reviews and edits before sending."""
    schema_version: str = SCHEMA_VERSION
    case_id: str = ""
    subject: str
    body: str = Field(description="Plain text only, signature included.")
    claims_needing_verification: list[ClaimToVerify] = Field(default_factory=list)
    links_used: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list, description="Auto-fixes we applied or issues the agent should look at.")
    meta: dict[str, Any] = Field(default_factory=dict)


class Citation(BaseModel):
    label: str
    url: str | None = None
    jira_key: str | None = None
    type: SourceType | None = None


class ToolUse(BaseModel):
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    result_count: int | None = None
    error: str | None = None
    forced: bool = Field(default=False, description="True if our guard ran it because the model was about to say 'I don't know'.")


class ChatResult(BaseModel):
    text: str
    citations: list[Citation] = Field(default_factory=list)
    tools_used: list[ToolUse] = Field(default_factory=list)
    checked: list[str] = Field(default_factory=list, description="What was searched, for 'not found' answers.")
    warnings: list[str] = Field(default_factory=list)


class ChatEvent(BaseModel):
    """One SSE event of a chat reply."""
    type: Literal["token", "tool_call", "tool_result", "done", "error"]
    text: str | None = None
    tool: ToolUse | None = None
    result: ChatResult | None = Field(default=None, description="Set on `done`; `result.text` is the canonical final text.")
    error: str | None = None
