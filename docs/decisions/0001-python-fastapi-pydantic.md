# 0001: Python 3.11, FastAPI, Pydantic v2

**Status:** accepted

**Decision:** Python 3.11 + FastAPI + Pydantic v2, with `httpx` as the only HTTP client. Dependencies: pydantic, httpx, fastapi, uvicorn (tests: pytest). Argparse for the CLI.

**Why:** Python and `uv` are already installed here. Pydantic models give validation, typed contracts and JSON Schema export in one place, so teammates in any language can validate against `schemas/*.schema.json`. Raw `httpx` instead of the OpenAI/Anthropic SDKs keeps both wire formats under our control and the dependency list short.

**Alternatives:** TypeScript + Hono + Zod (good, but no Node here and thinner eval tooling); LangChain/LlamaIndex (too heavy and provider-shaped for a competition build).

**Consequences:** Teammates integrate by import (`callcopilot.summarize`, `draft_email`, `ChatSession`) or by HTTP (`callcopilot serve`).
