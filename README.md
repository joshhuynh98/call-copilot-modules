# call-copilot-modules

Modules I own for the **Live Call Copilot for Support Agents** (OPSWAT AgentX 2026): meeting summary, customer email wrap-up, in-app chatbot, and the spec + eval harness for recommendations. The transcript and recommendation engines are built by teammates; this repo defines the contracts for them and ships fictional mock data to build against.

Everything runs **offline with a deterministic fake AI** until an AI server is chosen (Claude API or a local OpenAI-compatible server).

## Getting started (new teammate)

You need Windows PowerShell, Python 3.11 or newer, and [uv](https://docs.astral.sh/uv/getting-started/installation/) (`winget install --id=astral-sh.uv -e`).

```powershell
git clone https://github.com/joshhuynh98/call-copilot-modules
cd call-copilot-modules
uv venv
uv pip install -e ".[dev]"                       # about 10 seconds
.venv\Scripts\python -m pytest                   # tests
.venv\Scripts\callcopilot demo --fake            # full flow, offline
```

- The tests and every `--fake` command are offline and need no AI server and no key. This is enough to try the modules.
- To use a real AI server, copy `.env.example` to `.env` and fill in your own values (see [Configure the AI server](#configure-the-ai-server)), then [run the API](#run-the-http-api-serve) or drop `--fake` from a command.
- Never commit `.env` (it holds your API key) or `.venv/` (it is tied to your machine's paths and Python). Both are gitignored; everyone creates their own `.venv` with the commands above.
- With a reasoning model (for example Qwen3.5 in LM Studio) a real summary plus email can take several minutes. See [the note on timeouts](#run-the-http-api-serve).

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
| `REFERENCE_REVIEW` and friends | Opening the sources behind recommendations (docs pages, Jira, Confluence) before writing; see [docs/reference-review.md](docs/reference-review.md). `ATLASSIAN_MCP_URL` / `ATLASSIAN_MCP_AUTH` enable Jira and Confluence |
| `COPILOT_CORS_ORIGINS` | HTTP API: browser origins allowed to call it (comma-separated, or `*`). Blank = no CORS |
| `COPILOT_API_KEY` | HTTP API: if set, `/v1` requests need an `X-API-Key` header. Blank = open |

If a required variable is missing you get a one-line message naming it (no stack trace). Switching provider is a config change: nothing in the module logic knows which provider is used.

## Run the HTTP API (`serve`)

```powershell
.venv\Scripts\callcopilot serve --fake        # offline fixtures, no AI server needed
.venv\Scripts\callcopilot serve               # uses the AI server configured in .env
.venv\Scripts\callcopilot serve --port 9000   # default is 127.0.0.1:8000; --host changes the address it listens on
```

Without `--fake`, `serve` reads the AI settings from `.env` (see above), so fill those in first. Then open:

| URL | What |
|---|---|
| `http://127.0.0.1:8000/` | Demo page. Shortcut that generates a summary straight away: `/?scenario=icap_upload_timeouts&tab=summary&gen=summary` |
| `http://127.0.0.1:8000/docs` | Interactive API docs |
| `http://127.0.0.1:8000/health` | Which AI the API is using, e.g. `{"status":"ok","ai":"openai @ http://<host>:1234/v1 model=<model>","auth":"none"}` |

- **Real docs:** the demo scenarios link to made-up pages, so the source review has nothing real to open. Tick **Real docs links** in the demo header (or open `/?scenario=core_service_wont_start&live=1`) to load the same call with real docs.opswat.com pages; the API equivalent is `GET /v1/scenarios/<id>?live_docs=true`. It needs internet and a real AI server (with `--fake` the review stays offline). The CLI equivalent is `--live-docs` (`callcopilot summary --scenario core_service_wont_start --live-docs`). Details: [fixtures/live_docs/README.md](fixtures/live_docs/README.md).
- **Startup:** if a required variable is missing, `serve` stops at once with a one-line message naming it. It checks the settings only; it does not contact the AI server.
- **Reachability:** `/health` shows the configured server but does not test it. If the server is down or the URL is wrong, the first summary, email or chat request returns `502` with an `{"error": ...}` message that names `AI_SERVER_URL`.
- **Timeouts:** summary and email are plain requests with no progress events, and a reasoning model is slow. In one test on `icap_upload_timeouts` with Qwen3.5-9B in LM Studio, the summary took about 4 minutes and the email about 11. Set `AI_REQUEST_TIMEOUT_SECONDS` high enough (600 worked there) and make sure your client does not give up earlier. Chat replies are much quicker.
- **State:** meetings and chats are held in memory. Run a single process; a restart loses them.
- **Sharing it:** it listens on localhost by default. If you use `--host` to let other machines reach it, set `COPILOT_API_KEY`, because transcripts contain customer data. Set `COPILOT_CORS_ORIGINS` for a browser front end on another origin.

The endpoints, error codes and the chat stream are described in [docs/frontend.md](docs/frontend.md).

## What is in here

| Module | Entry point | Doc |
|---|---|---|
| Meeting summary | `callcopilot summary`, `POST /v1/summary`, `summarize()` | [docs/architecture.md](docs/architecture.md) |
| Email wrap-up | `callcopilot email`, `POST /v1/email`, `draft_email()` | [docs/email-rules.md](docs/email-rules.md) |
| Chatbot | `callcopilot chat`, `POST /v1/meetings/{id}/chat` (SSE), `ChatSession` | [docs/chatbot.md](docs/chatbot.md) |
| Recommendation spec + eval | `callcopilot recs-eval file.json`, `POST /v1/recommendations/evaluate` | [docs/recommendations-spec.md](docs/recommendations-spec.md) |
| Evals | `callcopilot eval` | [docs/evals.md](docs/evals.md) |
| Source review | `callcopilot review --url ...`, `POST /v1/recommendations/review`; runs automatically before writing | [docs/reference-review.md](docs/reference-review.md) |
| Web front end | HTTP API + SSE chat, OpenAPI in `schemas/openapi.json` | [docs/frontend.md](docs/frontend.md) |

Start with [docs/PLAN.md](docs/PLAN.md) (decisions and open questions), then [docs/integration.md](docs/integration.md) if you are integrating a module, [docs/frontend.md](docs/frontend.md) if you are building the UI, and [docs/BUILD_LOG.md](docs/BUILD_LOG.md) for status.

## Plugging in the real modules

Our code only depends on two small interfaces in `src/callcopilot/shared/sources/sources.py` (`TranscriptSource`, `RecommendationSource`) and on the Pydantic contracts in `src/callcopilot/shared/contracts/` (JSON Schema copies in `schemas/`). Details and the open questions for the transcript and recommendation owners are in [docs/integration.md](docs/integration.md).

## Layout

Everything functional is under `src/callcopilot/`, in three folders. The module code is in `modules/`; open [src/callcopilot/README.md](src/callcopilot/README.md) for the full map.

```
src/callcopilot/
  modules/        THE MODULES. Each folder holds its code, its checks and its prompts.
    summary/        organise, then write the meeting summary
    email/          customer follow-up email (draft + re-check)
    chatbot/        grounded chat with streaming and tool use (tools/ holds the search stubs)
    recommendations/  quality bar + checker + triage for the recommendation module's output
  shared/         Used by every module: data contracts, LLM client, safety, policy, sources
  app/            Ways to run it: HTTP API (+ demo page), CLI, evals
schemas/          Exported JSON Schema and OpenAPI spec (generated, for non-Python teammates)
fixtures/         Fictional call scenarios used as mock data (transcript, recommendations, goldens)
docs/             Plan, architecture, decisions, integration, rules, build log
tests/            Offline tests
```

All customer names, people and hosts in `fixtures/` are fictional (hosts end in `.invalid`).
