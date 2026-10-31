# call-copilot-modules

Modules I own for the **Live Call Copilot for Support Agents** (OPSWAT AgentX 2026): meeting summary, customer email wrap-up, in-app chatbot, and the spec + eval harness for recommendations. The transcript and recommendation engines are built by teammates; this repo defines the contracts for them and ships fictional mock data to build against.

Everything runs **offline with a deterministic fake AI** until an AI server is chosen (Claude API or a local OpenAI-compatible server).

## Quick start (Windows, PowerShell)

```powershell
uv venv
uv pip install -e ".[dev]"

.venv\Scripts\callcopilot scenarios                  # list the 5 fictional calls
.venv\Scripts\callcopilot demo --fake                # transcript -> recommendations -> summary -> email -> chatbot
.venv\Scripts\callcopilot eval --fake                # all scenarios, deterministic checks, report in evals/reports/
.venv\Scripts\python -m pytest                       # tests (never need an AI server)
.venv\Scripts\callcopilot serve --fake               # HTTP API + demo page at http://127.0.0.1:8000
```

## Configure the AI server

Configuration is **environment variables only** (or a `.env` file, which is gitignored). Copy `.env.example` to `.env`; all values there are blank on purpose.

| Variable | Meaning |
|---|---|
| `AI_PROVIDER` | `openai` (default; any OpenAI-compatible chat-completions server), `anthropic` (Claude Messages API), or `fake` (offline) |
| `AI_SERVER_URL` | Base URL, e.g. ends in `/v1` for OpenAI-compatible servers. Required unless `fake` |
| `AI_SERVER_API_KEY` | API key. **May be empty** (many local servers need none) |
| `AI_MODEL` | Model name. Required unless `fake` |
| `AI_REQUEST_TIMEOUT_SECONDS` / `AI_MAX_RETRIES` | Optional (default 60 / 2) |
| `AI_MAX_CONTEXT_TOKENS` | Optional transcript budget before long calls are map-reduced (default 12000 est. tokens) |
| `AI_MAX_OUTPUT_TOKENS` | Optional; Anthropic requires one (default 4096) |
| `AI_TOOL_MODE` | `prompted` (default, works on any model) or `native` (provider function calling) |
| `AI_JUDGE_*` | Same names with an `AI_JUDGE_` prefix for the eval judge; unset fields fall back to `AI_*` |
| `REDACTION_MODE` | `basic` (default) or `off`. `REDACTION_CATEGORIES` restricts to e.g. `email,phone` |
| `COPILOT_CORS_ORIGINS` | HTTP API: browser origins allowed to call it (comma-separated, or `*`). Blank = no CORS |
| `COPILOT_API_KEY` | HTTP API: if set, `/v1` requests need an `X-API-Key` header. Blank = open |

If a required variable is missing you get a one-line message naming it (no stack trace). Switching provider is a config change: nothing in the module logic knows which provider is used.

## What is in here

| Module | Entry point | Doc |
|---|---|---|
| Meeting summary | `callcopilot summary`, `POST /v1/summary`, `summarize()` | [docs/architecture.md](docs/architecture.md) |
| Email wrap-up | `callcopilot email`, `POST /v1/email`, `draft_email()` | [docs/email-rules.md](docs/email-rules.md) |
| Chatbot | `callcopilot chat`, `POST /v1/meetings/{id}/chat` (SSE), `ChatSession` | [docs/chatbot.md](docs/chatbot.md) |
| Recommendation spec + eval | `callcopilot recs-eval file.json`, `POST /v1/recommendations/evaluate` | [docs/recommendations-spec.md](docs/recommendations-spec.md) |
| Evals | `callcopilot eval` | [docs/evals.md](docs/evals.md) |
| Web front end | HTTP API + SSE chat, OpenAPI in `contracts/openapi.json` | [docs/frontend.md](docs/frontend.md) |

Start with [docs/PLAN.md](docs/PLAN.md) (decisions and open questions), then [docs/integration.md](docs/integration.md) if you are integrating a module, [docs/frontend.md](docs/frontend.md) if you are building the UI, and [docs/BUILD_LOG.md](docs/BUILD_LOG.md) for status.

## Plugging in the real modules

Our code only depends on two small interfaces in `src/callcopilot/sources/sources.py` (`TranscriptSource`, `RecommendationSource`) and on the Pydantic contracts in `src/callcopilot/contracts/` (JSON Schema copies in `contracts/`). Details and the open questions for the transcript and recommendation owners are in [docs/integration.md](docs/integration.md).

## Layout

```
src/callcopilot/
  contracts/   Pydantic models (inputs + outputs); `callcopilot schemas` exports JSON Schema to contracts/
  llm/         config, one LLMClient interface, openai/anthropic/fake adapters, structured output + repair
  safety/      redaction (reversible), untrusted-content wrapping
  modules/     summary/, email/, chatbot/
  checks/      deterministic checks shared by generators and evals (email, summary, chat, recommendations)
  evals/       runner, judge, report
  tools/       tool registry + fixture-backed stubs (search_docs, search_jira, ...)
  sources/     transcript/recommendation source interfaces + fixture scenarios
  prompts/     versioned prompts (<name>/vN.md), separate from code
  policy.py    shared content rules (markdown, meeting offers, Jira keys, third-party terms)
fixtures/scenarios/<id>/   fictional transcript, recommendations, style profile, kb, golden/
docs/          plan, architecture, ADRs, integration, rules, evals, build log
tests/         unit tests, offline
```

All customer names, people and hosts in `fixtures/` are fictional (hosts end in `.invalid`).
