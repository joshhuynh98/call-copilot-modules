from __future__ import annotations

from pydantic import BaseModel

from .outputs import EmailDraft, MeetingSummary
from .recommendations import RecommendationSet
from .style import StyleProfile
from .transcript import Transcript


class MeetingContext(BaseModel):
    """Everything the chatbot is grounded in for one meeting."""
    transcript: Transcript
    recommendations: RecommendationSet | None = None
    style_profile: StyleProfile | None = None
    summary: MeetingSummary | None = None
    email_draft: EmailDraft | None = None
