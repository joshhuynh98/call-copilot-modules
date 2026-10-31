# Chatbot

`ChatSession(meeting: MeetingContext, llm, backend, config)`; `session.ask(text)` yields `ChatEvent`s: `token`, `tool_call`, `tool_result`, `done` (with the canonical `ChatResult`), or `error`. One session = one meeting; history is in memory and capped at 12 messages.

## Grounding

The system prompt contains the case line, the summary (JSON), the recommendations (rank, action, why, sources with links and Jira keys), the current email draft if any, and the transcript. All of these are wrapped as untrusted data. If the transcript exceeds the token budget, the first 5 segments plus the most recent ones that fit are included and a `search_transcript` tool is added for the rest.

## Behaviour rules

- Cites sources (segment ids, links, Jira keys). Links not present in the context or in tool results are removed from the answer and listed in `warnings`.
- **Search before "I don't know"** (ADR 0004): enforced by a guard in code. `ChatResult.tools_used[].forced` shows when the guard ran the searches; `checked` lists what was searched; not-found answers get a "What I checked:" footer.
- **Prompt injection:** transcript text and tool results cannot close their untrusted block; tools are read-only; the prompt tells the model to treat them as data.
- **Third-party scope:** same rule as the email; a heuristic flags troubleshooting advice for third-party products in `warnings`.
- **Customer-pasteable text** (rewritten email, next steps) must be inside `<email>...</email>` and is sanitised to the email rules (plain text, no ids/internal links, no meeting offers). The rest of a reply may use light formatting. `done.result.text` is the final text; the UI should replace streamed text with it.

## HTTP

```
PUT    /v1/meetings/{id}          body: MeetingContext (transcript, recommendations?, style_profile?, summary?, email_draft?)
POST   /v1/meetings/{id}/chat     body: {"message": "..."}  -> text/event-stream
DELETE /v1/meetings/{id}/chat     reset the conversation
```

SSE events are `event: token|tool_call|tool_result|done|error` with a JSON `ChatEvent` as data.

## Tools

`search_docs`, `search_jira`, `search_confluence`, `similar_cases`, `customer_history` are the `ToolBackend` Protocol in `contracts/tools.py`. `FixtureToolBackend` (keyword search over each scenario's `kb.json`) is the stub; implement the Protocol for real integrations and pass it to `ChatSession`.
