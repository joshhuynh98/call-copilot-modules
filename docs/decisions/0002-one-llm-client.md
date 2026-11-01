# 0002: One LLM interface; OpenAI-compatible default

**Status:** accepted

**Context:** The team has not decided between the Claude API and a local model (vLLM, Ollama, llama.cpp). Switching must never touch module logic.

**Decision:**
- All calls go through `LLMClient` (`complete`, `stream`). Config is env vars only (`AI_*`).
- Default wire protocol: **OpenAI-compatible chat completions** (`AI_PROVIDER=openai`). vLLM, Ollama, llama.cpp and LiteLLM speak it, and so do several Claude gateways.
- `AI_PROVIDER=anthropic` selects a ~100-line Messages API adapter for the Claude API directly.
- `AI_PROVIDER=fake` is a deterministic offline provider for tests, the demo and the evals' deterministic checks.
- Streaming is normalised to `StreamEvent(text | tool_call)`; the chat API re-emits it as SSE.
- We do not depend on provider-specific features (JSON modes, native tool calling, huge contexts).

**Consequences:** A new provider = one adapter + one line in `shared/llm/factory.py`. The adapters are covered by unit tests using `httpx.MockTransport` (no server needed). `AI_JUDGE_*` allows a different server for the eval judge.
