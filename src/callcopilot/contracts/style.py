"""INPUT contract: agent style profile and per-draft email options."""
from __future__ import annotations

from pydantic import BaseModel, Field

from .transcript import SCHEMA_VERSION


class GlossaryEntry(BaseModel):
    term: str
    preferred: str | None = Field(default=None, description="Wording the agent prefers for this term.")
    avoid: str | None = None


class StyleProfile(BaseModel):
    schema_version: str = SCHEMA_VERSION
    agent_id: str
    name: str
    tone: str = Field(default="friendly and professional", description="Free text, e.g. 'warm, direct, no jargon'.")
    preferred_language: str = "en"
    glossary: list[GlossaryEntry] = Field(default_factory=list)
    sample_emails: list[str] = Field(default_factory=list)
    signature: str = ""
