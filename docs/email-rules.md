# Email wrap-up: rules and behaviour

`draft_email(transcript, summary, style, recommendations, llm, options)` returns an `EmailDraft`: `subject`, plain-text `body` (signature included), `claims_needing_verification`, `links_used`, `warnings`, `meta`. **It is always a draft** for the agent to review and edit.

## Hard rules (checked in code, see ADR 0005)

| Rule | Check name | Notes |
|---|---|---|
| Plain text only | `no_markdown` | No `**`, `__`, backticks, `#` headings, `-`/`*`/`+` bullets, markdown links, tables, HTML. Numbered lines like `1. ...` are fine. |
| Concise | `length_limit`, `short_paragraphs`, `subject_ok` | `options.max_words` (default 150, signature not counted); paragraphs ≤ 60 words; subject ≤ 100 chars. |
| No meetings/calls | `no_meeting_offer` | Off unless `allow_meeting_offer=true`. Plain references like "during our call" are allowed. |
| Public links only, from the provided sources | `urls_public_and_provided`, `no_internal_links` | Only `public_doc` sources of the provided recommendations. Never invented URLs. |
| No ticket ids | `no_jira_keys` | Every key in the provided sources plus anything key-shaped (`ABC-123`; common non-Jira tokens like `SHA-256` are ignored). |
| Third-party scope | `third_party_scope_statement`, `no_third_party_troubleshooting` | If the summary lists third-party components, the email states they are outside OPSWAT's scope and gives no fixes for them. If the model forgets, a standard sentence is appended and flagged. |

## Pipeline

1. Prompt `email@v1` gets: the summary (JSON), the agent's style profile (tone, glossary, sample emails), the public links list, and internal notes **without** ids/links.
2. Structured output (`subject`, `body`, `claims_needing_verification`), validated with repair.
3. Checks → up to 2 repair rounds quoting the violations.
4. Last resort: `enforce_email_text` removes markdown, disallowed links, ticket ids and meeting-offer sentences. Each fix is added to `warnings`.
5. The signature from the style profile is appended in code.
6. `claims_needing_verification` = the model's list + every uncertain claim from the summary + an unclear product/version.

## Re-checking an edited draft

`review_email(subject, body, policy, signature)` (HTTP: `POST /v1/email/check`, or `/v1/meetings/{id}/email/check` using the stored meeting) runs the same checks on any text, so a UI can validate the agent's edits. It returns `passed`, per-rule `checks`, the `fixes` the sanitiser would apply and a `suggested_body` when something can be auto-fixed.

## Options

`EmailOptions(allow_meeting_offer=False, max_words=150)`. CLI: `callcopilot email --scenario X --allow-meeting-offer --max-words 120`.
