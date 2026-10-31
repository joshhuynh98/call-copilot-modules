# Front-end integration guide

For whoever builds the webapp UI. Everything here is plain HTTP + JSON (+ one SSE stream). A working reference client is [demo.html](../src/callcopilot/static/demo.html) (about 80 lines of plain JavaScript, served at `/` by the API).

## Run the API

```powershell
.venv\Scripts\callcopilot serve --fake      # offline fixtures; drop --fake once an AI server is configured
```

Interactive docs: `http://127.0.0.1:8000/docs`. Health: `GET /health`.

## Types and clients

- **OpenAPI spec:** `contracts/openapi.json` (committed; regenerate with `callcopilot schemas`) or `GET /openapi.json` from a running server.
  `npx openapi-typescript contracts/openapi.json -o src/api.d.ts`
- **JSON Schema per model:** `contracts/*.schema.json` (transcript, recommendation_set, style_profile, meeting_context, meeting_summary, email_draft, chat_event, chat_result, ...).
  `npx json-schema-to-typescript contracts/email_draft.schema.json`

## Configuration for browsers

| Variable (blank = off) | Effect |
|---|---|
| `COPILOT_CORS_ORIGINS` | Comma-separated browser origins allowed to call the API, e.g. your dev server's origin, or `*`. Without it the browser blocks cross-origin calls. |
| `COPILOT_API_KEY` | If set, every `/v1/*` request must send `X-API-Key: <value>`. `/health`, the demo page and the API docs (`/docs`, `/openapi.json`; schemas only, no data) stay open. Open the demo as `/?key=<value>`. CORS preflight requests need no key, and a rejected request still carries the CORS headers so the page can read the 401. Compared in constant time. This is a shared secret for demos, not user authentication. |

Both are read from the environment or `.env` at startup. Put the key on your server side if you can; a key shipped in browser JavaScript is visible to users.

## The flow

```
1. PUT  /v1/meetings/{id}            register the call: transcript (+ recommendations, style_profile)
2. POST /v1/meetings/{id}/summary    -> MeetingSummary          (stored on the meeting)
3. POST /v1/meetings/{id}/email      -> EmailDraft              (stored on the meeting)
4. POST /v1/meetings/{id}/email/check  after the agent edits the draft -> EmailReview
5. POST /v1/meetings/{id}/chat       SSE stream of ChatEvent    (conversation scoped to the meeting)
```

`{id}` is yours to choose (call id, case id, ...). Re-`PUT` the meeting whenever the transcript, recommendations or the agent's edited email draft change; that also resets the chat history. The recommendations come from the recommendation module: pass them in the `PUT` so the chatbot and the email can use them (they define which links the email may contain).

Stateless alternatives (no meeting registration) exist for the same operations: `POST /v1/summary`, `POST /v1/email`, `POST /v1/email/check`, `POST /v1/recommendations/evaluate`.

## Endpoints

| Method and path | Body | Response |
|---|---|---|
| `GET /health` | | `{status, ai, auth}`; 503 `{status: "misconfigured", error}` if the AI server is not configured |
| `PUT /v1/meetings/{id}` | `MeetingContext` | `{meeting_id, case_id}` |
| `POST /v1/meetings/{id}/summary` | | `MeetingSummary` |
| `POST /v1/meetings/{id}/email` | optional `{options: {allow_meeting_offer, max_words}}` | `EmailDraft` (needs `style_profile` on the meeting) |
| `POST /v1/meetings/{id}/email/check` | `{subject, body}` | `EmailReview` |
| `POST /v1/meetings/{id}/chat` | `{message}` | `text/event-stream` |
| `DELETE /v1/meetings/{id}/chat` | | `{reset: true}` |
| `POST /v1/email/check` | `{subject, body, recommendations?, summary?, options?, signature?}` | `EmailReview` |
| `POST /v1/recommendations/evaluate` | `{recommendations, transcript?}` | `{passed, checks[]}` |
| `GET /v1/scenarios`, `GET /v1/scenarios/{id}` | | demo data (fictional calls) |

### Errors

Every error except request validation is `{"error": "<message safe to show>"}`:

| Status | Meaning |
|---|---|
| 401 | missing/wrong `X-API-Key` |
| 404 | unknown meeting or scenario: `PUT` it first |
| 422 | invalid request body. This is FastAPI's `{"detail": [...]}` shape |
| 502 | the AI server failed or returned unusable output after repairs |
| 503 | the AI server is not configured (the message names the missing variable) |

## Chat stream (SSE over POST)

`EventSource` only supports GET, so read the stream with `fetch`:

```js
const res = await fetch(`/v1/meetings/${id}/chat`, {
  method: "POST",
  headers: { "Content-Type": "application/json", "X-API-Key": key },   // header only if configured
  body: JSON.stringify({ message }),
});
const reader = res.body.getReader(), dec = new TextDecoder();
let buf = "";
for (;;) {
  const { done, value } = await reader.read();
  if (done) break;
  buf += dec.decode(value, { stream: true });
  let i;
  while ((i = buf.indexOf("\n\n")) >= 0) {            // events are separated by a blank line
    const raw = buf.slice(0, i); buf = buf.slice(i + 2);
    const type = raw.match(/^event: (.*)$/m)[1];
    const data = JSON.parse(raw.match(/^data: (.*)$/m)[1]);
    // handle(type, data)
  }
}
```

If the request itself fails (401, 404, 503) the body is JSON, not a stream: check `res.ok` before reading.

| Event | `data` | What to do |
|---|---|---|
| `token` | `{type, text}` | Append `text` to the reply bubble. |
| `tool_call` | `{type, tool: {tool, arguments, forced}}` | Show "searching docs/Jira/Confluence...". `forced: true` means the guard ran the searches because the model was about to say "I don't know". |
| `tool_result` | `{type, tool: {tool, result_count, error}}` | Update that indicator. |
| `done` | `{type, result: ChatResult}` | **Replace** the streamed text with `result.text` (it is the sanitised final text). Render `result.citations`, `result.warnings`, `result.checked`. |
| `error` | `{type, error}` | Show the message; the exchange is not added to the conversation history. |

An empty `token` may appear while the guard runs its second pass: ignore it.

## What the UI should render

- **Email draft:** always as an *editable draft*, never auto-sent. Show `warnings` (things we auto-fixed or could not fix) and `claims_needing_verification` (facts the agent should confirm) next to it. Show `links_used`.
- **Editing the draft:** call `/email/check` on edit (debounced) or before send. `passed: false` means a hard rule is broken (markdown, meeting offer, ticket id, internal or invented link, too long, missing third-party scope statement). `checks[]` has a `name` and `details` per rule. `suggested_body` is an auto-fixed version when one exists: offer it as a one-click fix. Soft checks (`soft: true`) are advisory.
- **Chat replies:** light markdown is fine. Text inside `<email>...</email>` is customer-pasteable plain text: show it in its own box with a copy button, without markdown rendering, and strip the tags. Show citations as links (`url`) and Jira keys (`jira_key`); these are for the agent only.
- **Summary (v2.0, already grouped):** show `plain_language` first, then the sections in this order: `issues` (one or more), `before_call`, `in_call`, `findings`, **next steps grouped by `next_actions[].owner`** (`agent` = Support, `customer`, `opswat_other`), `open_questions`. Skip empty sections. Mark claims with `uncertain: true`, show `product.uncertain`, and highlight `low_confidence_segment_ids` in the transcript view. `segment_ids` on any claim link back to transcript segments. Show `third_party_components` as "out of OPSWAT scope". `recommendation_notes` lists duplicate or unrelated recommendations (`relevant: false`, with a short `note`): show them greyed out or under "set aside" rather than hiding them.
- **Recommendations (cheatsheet):** `action` as the headline, `rationale` as one or two lines, sources as labelled links by `type` (Public doc / Internal KB / Confluence / Jira). Order by `rank`.

## Things to know

- **Latency:** with a real model, summary and email take seconds to tens of seconds. They are plain requests with no progress events: show a spinner. Chat streams.
- **State is in memory** (single process): a server restart loses meetings and chats. Run one worker. Re-`PUT` the meeting after a restart (a 404 on a known meeting means that).
- **No live transcript endpoint yet.** To refresh a summary during a call, re-`PUT` the meeting with the longer transcript and call `/summary` again.
- Customer data: the transcript contains customer PII. Names, emails, phone numbers, IPs and secrets are redacted before reaching the AI server (see ADR 0006) but are present in the API traffic and in stored meetings.
