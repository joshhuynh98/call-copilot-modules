# Architecture

```
 transcript module ──► Transcript ─┐
 recommendation module ► RecommendationSet ─┤
 agent profile ───────► StyleProfile ───────┤
                                            ▼
          ┌──────────────────────────── pipeline.make_llm ───────────────────────────┐
          │  RedactingClient( OpenAIAdapter | AnthropicAdapter | FakeLLM )            │
          └──────────────────────────────────────────────────────────────────────────┘
                 ▲                 ▲                    ▲
        summary module     email module          chatbot (ChatSession)
        (map-reduce if     (generate → check →   (stream, tools, search-before-
         long)              repair → enforce)     unknown guard, post-process)
                 │                 │                    │
          MeetingSummary      EmailDraft           ChatEvent stream / ChatResult
```

Every LLM call goes through one interface, `LLMClient` (`complete()` and `stream()`), in `src/callcopilot/llm/client.py`. Module code never imports a provider.

## Pieces

- **Contracts** (`contracts/`): Pydantic models are the source of truth; JSON Schema is exported to the repo-root `contracts/` folder. Inputs are lenient (so the eval can report item-level failures); outputs are strict.
- **Config** (`llm/config.py`): env vars only, fail-fast with an actionable message, blank key allowed, separate `AI_JUDGE_*`.
- **Adapters**: `openai_adapter` (default; chat completions incl. SSE streaming and tool calls), `anthropic_adapter` (Messages API), `fake` (offline). Shared retry/backoff/readable errors live in `HTTPAdapter`. Add a provider by writing one adapter and one line in `llm/factory.py`.
- **Structured output** (`llm/structured.py`): prompt with schema → extract JSON (tolerates fences/chatter) → validate with Pydantic → up to 2 repair turns feeding the errors back → clear error. No dependency on provider JSON modes.
- **Prompts** (`prompts/<name>/vN.md`): versioned, separate from code, `{{placeholder}}` substitution. Prompt versions are recorded in outputs (`meta`).
- **Safety**: reversible redaction before every call (`safety/redaction.py`), untrusted-content wrapping (`safety/untrusted.py`), logs never include raw transcripts.
- **Policy** (`policy.py`) + **checks** (`checks/`): one set of content rules used by both the generators (repair/enforce) and the evals.
- **Fake provider**: serves each fixture scenario's golden outputs (keyed by `Tag.task` + `Tag.case_id`, which are never sent to real servers). It proves plumbing and checkers, not model quality.

## Summary module (organise first, then write; ADR 0007)

1. **Organise** (`organize` prompt): the model reads the transcript (`[seg_id] SPEAKER: text` lines, translated text, `[LOW CONFIDENCE]` flags, wrapped as untrusted data) and the recommendations, and sorts them into a `CallBrief`: issues, before the call, during the call, findings, next actions by owner, open questions, third-party components. It merges repeats, orders items by time, uses a speaker's correction (or marks the item uncertain), and flags duplicate or unrelated recommendations. If the transcript exceeds the token budget (`AI_MAX_CONTEXT_TOKENS`, default 12000) it is organised chunk by chunk (2-segment overlap) and merged with `organize_reduce`.
2. **Write** (`summary` prompt + `style_rules`): rewrites the brief as short plain-English bullets without changing facts.
3. **Guards in code:** facts the writer dropped are restored from the brief (sections, next actions, product, third-party parts) and doubt flags are never lost; evidence ids that do not exist are removed; claims without evidence become `uncertain`; `case_id`, `low_confidence_segment_ids` and the recommendation triage notes are filled in.

Cost: two model calls for a normal call, more for a long one. The email uses the triaged recommendations (duplicates and unrelated items never reach it); the chatbot still shows the agent the full list.

## Decision records

| # | Decision | Why |
|---|---|---|
| [0001](decisions/0001-python-fastapi-pydantic.md) | Python 3.11, FastAPI, Pydantic v2, few dependencies | Installed here, fastest to build, easy for teammates to import, typed contracts → JSON Schema |
| [0002](decisions/0002-one-llm-client.md) | One LLM interface; OpenAI-compatible default, Anthropic adapter, fake | Server undecided; switching must be config or one adapter |
| [0003](decisions/0003-validate-and-repair.md) | Validate + repair instead of provider JSON modes | Local models have weaker structured output |
| [0004](decisions/0004-tools-prompted-by-default.md) | Prompted tool protocol by default, native opt-in; search-before-unknown guard in code | Works on any model; the rule is enforced, not just requested |
| [0005](decisions/0005-email-rules-enforced-in-code.md) | Email hard rules enforced by checks + repair + last-resort sanitiser | Prompts alone are not reliable; customers must never see ticket ids or internal links |
| [0006](decisions/0006-redaction-before-llm.md) | Reversible redaction wrapper around any client | Call content is customer data; the AI server may be external |
| [0007](decisions/0007-organise-first-summary.md) | Organise first, then write; concise plain-English style; recommendation triage | Messy input must be grouped before it is written up; style is a product requirement |
