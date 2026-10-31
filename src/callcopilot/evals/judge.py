"""LLM-as-judge: versioned rubrics in prompts/judge_*/vN.md. Skipped (with a message) if no real server is set."""
from __future__ import annotations

import json
from dataclasses import dataclass

from pydantic import BaseModel, Field

from callcopilot.contracts import EmailDraft, MeetingSummary, RecommendationSet, StyleProfile, Transcript
from callcopilot.llm import LLMClient, LLMConfig, Tag, create_client, generate_structured
from callcopilot.prompt_loader import load_prompt
from callcopilot.safety import wrap_untrusted

SKIP_MESSAGE = ("Judge checks skipped: no real AI server configured for judging. Set AI_SERVER_URL + AI_MODEL "
                "(or AI_JUDGE_SERVER_URL + AI_JUDGE_MODEL) and a non-fake AI_PROVIDER to enable them.")


class JudgeScore(BaseModel):
    score: int = Field(ge=1, le=5)
    reasons: str = ""


@dataclass
class JudgeResult:
    rubric: str
    score: int
    reasons: str
    version: str


class Judge:
    def __init__(self, config: LLMConfig, client: LLMClient | None = None):
        self.config = config
        self.client = client or create_client(config)

    def _score(self, rubric: str, case_id: str, **vars) -> JudgeResult:
        prompt = load_prompt(rubric)
        msgs = prompt.render(**{k: wrap_untrusted(k.upper(), v) if isinstance(v, str) and len(v) > 200 else v for k, v in vars.items()})
        result, _ = generate_structured(self.client, msgs, JudgeScore, tag=Tag("judge", case_id), temperature=0.0)
        return JudgeResult(rubric.removeprefix("judge_"), result.score, result.reasons, prompt.label)

    def summary_faithfulness(self, t: Transcript, s: MeetingSummary) -> JudgeResult:
        return self._score("judge_summary_faithfulness", t.metadata.case_id, transcript=t.render(), candidate=s.model_dump_json(exclude={"meta"}))

    def next_actions(self, t: Transcript, s: MeetingSummary, golden: MeetingSummary) -> JudgeResult:
        pick = lambda m: json.dumps({"next_actions": [a.model_dump() for a in m.next_actions],  # noqa: E731
                                     "open_questions": [q.text for q in m.open_questions]})
        return self._score("judge_next_actions", t.metadata.case_id, transcript=t.render(), golden=pick(golden), candidate=pick(s))

    def email_tone(self, case_id: str, style: StyleProfile, email: EmailDraft) -> JudgeResult:
        return self._score("judge_email_tone", case_id, style=style.model_dump_json(), subject=email.subject, body=email.body)

    def clarity_conciseness(self, case_id: str, summary: MeetingSummary, email: EmailDraft) -> JudgeResult:
        return self._score("judge_clarity_conciseness", case_id, summary=summary.model_dump_json(exclude={"meta", "recommendation_notes"}),
                           subject=email.subject, body=email.body)

    def recommendation_relevance(self, t: Transcript, recs: RecommendationSet) -> JudgeResult:
        return self._score("judge_recommendation_relevance", t.metadata.case_id, transcript=t.render(), candidate=recs.model_dump_json())
