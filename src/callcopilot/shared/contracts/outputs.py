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


class SourceReview(BaseModel):
    """What happened when we opened a recommendation's source (public doc, Jira ticket, Confluence page) before writing."""
    url: str
    type: SourceType
    label: str = ""
    jira_key: str | None = None
    status: Literal["ok", "redirected", "not_found", "blocked", "no_access", "skipped", "error"] = "skipped"
    final_url: str | None = Field(default=None, description="Where the link really ends up (after redirects).")
    title: str | None = None
    excerpt: str = Field(default="", description="The passage(s) of the source most relevant to the recommendation, trimmed.")
    note: str = Field(default="", description="Why it was skipped, blocked or failed. Safe to show the agent.")


class RecNote(BaseModel):
    """Triage of one provided recommendation, made while organising the call."""
    rec_id: str
    relevant: bool = Field(default=True, description="False for duplicates or items unrelated to this call.")
    issue: int | None = Field(default=None, description="Index into `issues` this recommendation addresses, if any.")
    note: str = ""
    supported: bool | None = Field(default=None, description="Whether the reviewed source content supports the recommended action. None = not reviewed.")
    gist: str = Field(default="", description="One plain sentence on what the reviewed sources say that matters here. No names, ids or links.")


class SummaryContent(BaseModel):
    """The grouped content of a call: what both the organise stage and the final summary contain."""
    issues: list[Claim] = Field(default_factory=list, description="Problems the customer reported, most important first. One short line each.")
    product: ProductInfo = Field(default_factory=ProductInfo)
    before_call: list[Claim] = Field(default_factory=list, description="What was tried or done BEFORE the call.")
    in_call: list[Claim] = Field(default_factory=list, description="What was checked or done DURING the call.")
    findings: list[Claim] = Field(default_factory=list)
    next_actions: list[NextAction] = Field(default_factory=list, description="After the call; owner = agent (support), customer or opswat_other.")
    open_questions: list[Claim] = Field(default_factory=list)
    third_party_components: list[ThirdPartyComponent] = Field(default_factory=list)


class CallBrief(SummaryContent):
    """Stage 1 output (organise): grouped, de-duplicated, chronological, with recommendation triage."""
    recommendation_notes: list[RecNote] = Field(default_factory=list)


class SummaryCore(SummaryContent):
    """Stage 2 output (write): the concise, plain-English wording."""
    plain_language: str = Field(description="2-3 plain sentences (<=60 words): what happened and what comes next.")


class MeetingSummary(SummaryCore):
    """SummaryCore plus fields we fill in deterministically in code."""
    schema_version: str = "2.0"
    case_id: str = ""
    recommendation_notes: list[RecNote] = Field(default_factory=list)
    source_reviews: list[SourceReview] = Field(default_factory=list, description="What opening each recommendation source showed.")
    low_confidence_segment_ids: list[str] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)

    def all_claims(self) -> list[Claim]:
        return [*self.issues, *self.before_call, *self.in_call, *self.findings, *self.open_questions]


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
