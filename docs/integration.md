# Integration guide (for teammates)

## As a Python library

```python
from callcopilot.contracts import Transcript, RecommendationSet, StyleProfile, MeetingContext
from callcopilot.llm import load_config
from callcopilot.pipeline import make_llm, run_summary, run_email
from callcopilot.modules.chatbot import ChatSession

cfg = load_config()                       # env vars; raises ConfigError with an actionable message
llm = make_llm(cfg, transcript)           # provider adapter + redaction
summary = run_summary(transcript, cfg, llm)
draft = run_email(transcript, summary, style, recommendations, cfg, llm)

chat = ChatSession(MeetingContext(transcript=transcript, recommendations=recommendations,
                                  style_profile=style, summary=summary, email_draft=draft),
                   llm, my_tool_backend, cfg)
for event in chat.ask("What should I try next?"):
    ...
```

## Over HTTP (`callcopilot serve`, OpenAPI at `/docs`)

| Endpoint | Body | Returns |
|---|---|---|
| `POST /v1/summary` | `{transcript, recommendations?}` | `MeetingSummary` (grouped, v2.0; includes `recommendation_notes` when recommendations are given) |
| `POST /v1/email` | `{transcript, style_profile, recommendations?, summary?}` | `EmailDraft` |
| `POST /v1/email/check` | `{subject, body, recommendations?, summary?, signature?}` | `EmailReview`: `{passed, checks[], fixes[], suggested_body?}` for an edited draft |
| `POST /v1/recommendations/evaluate` | `{recommendations, transcript?}` | `{passed, checks[]}` |
| `PUT /v1/meetings/{id}` | `MeetingContext` | registers a meeting for chat |
| `POST /v1/meetings/{id}/summary` / `/email` | none | generates and stores on the meeting |
| `POST /v1/meetings/{id}/email/check` | `{subject, body}` | `EmailReview` using the meeting's recommendations and summary |
| `POST /v1/meetings/{id}/chat` | `{message}` | SSE stream of `ChatEvent` |

Errors: `{"error": "..."}` with `401` (bad `X-API-Key`), `404`, `502` (AI server failed or returned unusable output) or `503` (AI server not configured). Front-end specifics (CORS, API key, SSE, what to render): [frontend.md](frontend.md).

## Inputs we expect

JSON Schema for all contracts is in `contracts/*.schema.json` (regenerate with `callcopilot schemas`). Fixture examples: `fixtures/scenarios/*/`.

- **Transcript** (`transcript.schema.json`): `metadata` (case id, customer, contact, agent, product, version, times) and `segments[]` with `id`, `speaker` (`agent`|`customer`), `start_ms`, `end_ms`, `text_original`, `text_translated` (null if English), `language`, `confidence`, `is_final`. We reason over the translated text when present and flag segments with confidence < 0.6.
- **Recommendations** (`recommendation_set.schema.json`): see [recommendations-spec.md](recommendations-spec.md).
- **Style profile** (`style_profile.schema.json`): tone, glossary, sample emails, preferred language, signature.

The exact format of the real modules is not decided. Write an adapter that produces these models (or ask an AI to); our code only depends on the models and on the two source interfaces in `sources/sources.py`:

```python
class TranscriptSource(Protocol):     def get_transcript(self, case_id: str) -> Transcript: ...
class RecommendationSource(Protocol): def get_recommendations(self, case_id: str) -> RecommendationSet: ...
```

Today they are backed by fixture files (`FileTranscriptSource`, `FileRecommendationSource`).

## Outputs

`MeetingSummary`, `EmailDraft`, `ChatEvent`/`ChatResult` (schemas in `contracts/`). Everything user-facing is plain data; the UI decides rendering. `EmailDraft` must be shown as an editable draft with `claims_needing_verification` and `warnings` visible to the agent.

## Open questions

See [PLAN.md](PLAN.md#open-questions-for-the-other-module-owners).
