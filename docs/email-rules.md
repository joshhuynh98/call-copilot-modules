# Email wrap-up: rules and behaviour

`draft_email(transcript, summary, style, recommendations, llm)` returns an `EmailDraft`: `subject`, plain-text `body` (signature included), `claims_needing_verification`, `links_used`, `warnings`, `meta`. **It is always a draft** for the agent to review and edit.

## The AI decides the format and length

There are **no email settings**: nothing for the user or the UI to choose (no thank-you toggle, no length setting, no meeting-offer switch). The prompt (`email@v4`) tells the model to pick the structure and length that suit this call and this reader, as long as the email is:

- **Informative:** the customer learns what was found (or what is still unconfirmed), what happens next and who does what, without other notes.
- **Courteous:** warm, professional and natural, in the agent's voice (style profile tone, glossary, sample emails). One brief thank-you for the customer's time, placed wherever it reads best. No other pleasantries.
- **Concise:** the fewest words that stay clear and complete. Most emails need about 80 to 150 words.
- **Easy to scan:** short lines when there is more than one point: numbered lines for ordered steps, dash lines for separate points, a plain line ending in a colon as a section label. A single point does not need a list.

The prompt shows two shapes that work, to adapt rather than copy. For example, a simple call gets a short note:

```
Hi Dana,

Thanks for your time on the call today. Uploads over 50 MB fail because ICAP Server stops waiting
for Core after 60 seconds.

1. You: send the full ICAP Server log for one failed upload.
2. Me: send the exact timeout steps.
```

and a call with several findings and an unclear version can use labelled sections ("What we found:", "Still to confirm:", "Next steps:"). The model chooses.

## Fixed guardrails (enforced in code, not settings)

| Rule | Check name | Notes |
|---|---|---|
| Plain text only | `no_markdown` | No `**`, `__`, backticks, `#` headings, `*`/`+`/`•` bullets, markdown links, tables, HTML. Numbered lines (`1. ...`) and dash lines (`- ...`) are plain text and allowed. |
| No meetings or calls | `no_meeting_offer` | The email never offers or requests a call, meeting or demo. Plain references like "during our call" are allowed. |
| Length ceiling | `length_limit` (hard), `target_length` (soft) | Hard: more than 220 words (signature not counted). Soft warning above 150. Paragraphs over 60 words and subjects over 100 characters also fail. |
| Public links only, from the provided sources | `urls_public_and_provided`, `no_internal_links` | Only `public_doc` sources of the *triaged* recommendations, and only pages that were opened and exist: dead links and links whose recommendation the opened content does not support are dropped, moved links are replaced by their final URL, links that could not be checked stay with a "link not verified" warning. Never invented URLs. See [reference-review.md](reference-review.md). |
| No ticket ids | `no_jira_keys` | Every key in the provided sources (even from set-aside recommendations) plus anything key-shaped (`ABC-123`; common non-Jira tokens like `SHA-256` are ignored). |
| Third-party scope | `third_party_scope_statement`, `no_third_party_troubleshooting` | If the summary lists third-party components, the email states they are outside OPSWAT's scope and gives no fixes for them. If the model forgets, a standard sentence is appended and flagged. |

## Soft style checks

Warnings, not failures. They trigger a repair round and show as WARN in the eval report; the judge rubric `clarity_conciseness` scores the same qualities.

- `courtesy_line`: exactly one brief thank-you (anywhere in the email). None, or more than one, is flagged.
- `no_filler`: no "Here is a recap", "Please do not hesitate...", "As discussed", "Thank you for your patience", and similar padding.
- `concise_bullets` (list lines of about 30 words or fewer) and `plain_sentences` (average sentence of about 22 words or fewer).
- `target_length`: more than 150 words.

## Pipeline

1. Prompt `email@v5` gets: the grouped summary (JSON), the agent's style profile, the public links of the **triaged and verified** recommendations, and reference notes: each kept recommendation's action plus a one-sentence gist of what its opened sources say, **without** ids or links. Raw page or ticket text never goes into this prompt.
2. Structured output (`subject`, `body`, `claims_needing_verification`), validated with repair.
3. Checks, hard and soft style, then up to 2 repair rounds quoting the problems.
4. Last resort: `enforce_email_text` removes markdown, disallowed links, ticket ids and meeting-offer sentences (and turns `*`/`+`/`•` bullets into dash lines). Each fix is added to `warnings`.
5. The signature from the style profile is appended in code.
6. `claims_needing_verification` = the model's list + every uncertain claim from the summary + an unclear product/version.

## Re-checking an edited draft

`review_email(subject, body, policy, signature)` (HTTP: `POST /v1/email/check`, or `/v1/meetings/{id}/email/check` using the stored meeting) runs the same checks on any text, so a UI can validate the agent's edits. It returns `passed`, per-rule `checks` (soft ones have `soft: true`), the `fixes` the sanitiser would apply and a `suggested_body` when something can be auto-fixed.

## Why there are no settings

Earlier versions had `allow_meeting_offer`, `max_words` and `courtesy_line` options. They were removed (product-owner decision): the model should decide what suits each call, guided by principles, with fixed guardrails for the things that must never happen. "No meeting offers" stays a fixed guardrail, since that was the original rule and nobody asked to relax it. If you ever want the model to be allowed to offer a call, change rule 2 in `prompts/email` and the `no_meeting_offer` check together.
