# Email wrap-up: rules and behaviour

`draft_email(transcript, summary, style, recommendations, llm, options)` returns an `EmailDraft`: `subject`, plain-text `body` (signature included), `claims_needing_verification`, `links_used`, `warnings`, `meta`. **It is always a draft** for the agent to review and edit.

## Layout

Short labelled sections, lists instead of paragraphs, and no opener or recap sentence:

```
Hi Dana,

Issue: uploads over about 50 MB fail after roughly a minute.

What we found:
- ICAP Server stops waiting for Core after 60 seconds.
- The failing files are mostly large zip archives.

Next steps:
1. You: send the full ICAP Server log for one failed upload.
2. Me: confirm the exact settings and send you the steps.

Background on the timeout setting: https://...
```

Omit a section that has nothing to say. Messy calls get a "Still to confirm:" list instead of stated facts.

## Writing style (prompt `style_rules`, soft-checked)

- Brief: one short sentence per list line (about 25 words at most), no padding.
- No filler or pleasantries: no "Thanks for your time" opener, no "Here is a recap", no "Please do not hesitate...", no "As discussed".
- Plain English for an intelligent non-expert: short sentences, active voice, concrete words, a technical term explained in a few words only when needed. Not childish.
- These are **soft checks** (`no_filler`, `concise_bullets`, `plain_sentences`). They never fail the run, but they trigger a repair round and show up as WARN in the eval report. The judge rubric `clarity_conciseness` scores them.

## Hard rules (checked in code, see ADR 0005)

| Rule | Check name | Notes |
|---|---|---|
| Plain text only | `no_markdown` | No `**`, `__`, backticks, `#` headings, `*`/`+`/`•` bullets, markdown links, tables, HTML. **Allowed:** numbered lines (`1. ...`) and dash lines (`- ...`). |
| Concise | `length_limit`, `short_paragraphs`, `subject_ok` | `options.max_words` (default 150, signature not counted); paragraphs ≤ 60 words; subject ≤ 100 chars. |
| No meetings/calls | `no_meeting_offer` | Off unless `allow_meeting_offer=true`. Plain references like "during our call" are allowed. |
| Public links only, from the provided sources | `urls_public_and_provided`, `no_internal_links` | Only `public_doc` sources of the *triaged* recommendations. Never invented URLs. |
| No ticket ids | `no_jira_keys` | Every key in the provided sources (even from set-aside recommendations) plus anything key-shaped (`ABC-123`; common non-Jira tokens like `SHA-256` are ignored). |
| Third-party scope | `third_party_scope_statement`, `no_third_party_troubleshooting` | If the summary lists third-party components, the email states they are outside OPSWAT's scope and gives no fixes for them. If the model forgets, a standard sentence is appended and flagged. |

## Pipeline

1. Prompt `email@v2` gets: the grouped summary (JSON), the agent's style profile (tone, glossary, sample emails), the public links of the **triaged** recommendations, and internal notes **without** ids/links.
2. Structured output (`subject`, `body`, `claims_needing_verification`), validated with repair.
3. Checks, hard and soft style → up to 2 repair rounds quoting the problems.
4. Last resort: `enforce_email_text` removes markdown, disallowed links, ticket ids and meeting-offer sentences (and turns `*`/`+`/`•` bullets into dash lines). Each fix is added to `warnings`.
5. The signature from the style profile is appended in code.
6. `claims_needing_verification` = the model's list + every uncertain claim from the summary + an unclear product/version.

## Re-checking an edited draft

`review_email(subject, body, policy, signature)` (HTTP: `POST /v1/email/check`, or `/v1/meetings/{id}/email/check` using the stored meeting) runs the same checks on any text, so a UI can validate the agent's edits. It returns `passed`, per-rule `checks` (soft ones have `soft: true`), the `fixes` the sanitiser would apply and a `suggested_body` when something can be auto-fixed.

## Options

`EmailOptions(allow_meeting_offer=False, max_words=150)`. CLI: `callcopilot email --scenario X --allow-meeting-offer --max-words 120`.
