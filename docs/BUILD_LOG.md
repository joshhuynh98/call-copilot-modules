# Build log

Status as of 2026-10-31 (week 1 of 2), after the summary rework, the warmer-email change and the demo page upgrade. Nothing here has been run against a real AI server yet; all runs use the fake provider.

## Done

| Step | What | Evidence |
|---|---|---|
| Scaffold | `pyproject.toml`, `.env.example` (all blank), `.gitignore` (`.env`, `.venv`, eval reports), README | `uv pip install -e ".[dev]"` |
| Contracts | Transcript, RecommendationSet, StyleProfile, tool Protocol + SearchHit, MeetingSummary, EmailDraft, ChatEvent/ChatResult, MeetingContext; JSON Schema exported to `contracts/` | `callcopilot schemas` |
| LLM layer | env-only config with fail-fast errors, `LLMClient` interface, OpenAI-compatible + Anthropic adapters (retries, streaming, tool calls), fake provider, structured output + repair loop | `tests/test_config.py`, `test_adapters.py`, `test_structured_and_redaction.py` |
| Safety | reversible redaction wrapper (incl. streams), untrusted-content wrapping | tests as above |
| Fixtures | 5 fictional scenarios with transcript, recommendations, style profile, KB, goldens: ICAP timeouts, Core won't start, Sandbox disk full, third-party VDI/SSL-inspection issue, noisy call | `tests/test_fixtures.py` |
| Summary | one-pass and map-reduce, evidence clean-up in code | `tests/test_modules.py` |
| Email | generate → check → repair → enforce; public links only, no ticket ids, no meeting offers, third-party scope | `tests/test_checks.py`, `test_modules.py` |
| Chatbot | streaming, prompted + native tool modes, search-before-unknown guard, injection-safe wrapping, link/email sanitising, long-transcript tool | `tests/test_chat.py` |
| Recommendation spec + checker | quality bar as 9 named checks; CLI and HTTP endpoint | `tests/test_checks.py`, `docs/recommendations-spec.md` |
| Evals | runner over all scenarios, judge (4 versioned rubrics), Markdown + JSON report | `callcopilot eval --fake`, [baseline-report.md](baseline-report.md) |
| API + demo | FastAPI endpoints, SSE chat, static demo page | `tests/test_api_and_evals.py`, manual `callcopilot serve --fake` |
| Docs | plan, architecture, 6 ADRs, integration guide, email rules, chatbot, recommendations spec, evals | `docs/` |
| Front-end support | CORS via `COPILOT_CORS_ORIGINS`; optional `COPILOT_API_KEY` (`X-API-Key`, constant-time compare) on all `/v1` routes; uniform `{"error"}` responses; `POST /v1/email/check` and `/v1/meetings/{id}/email/check` (`review_email`: checks, fixes, `suggested_body`) for edited drafts; OpenAPI spec exported to `contracts/openapi.json`; demo page sends the key; [frontend.md](frontend.md) | `tests/test_frontend_support.py` |
| Summary rework (ADR 0007) | Two-stage summary: organise (group, dedupe, order, resolve corrections, triage recommendations) then write; new grouped shape (`issues`, `before_call`, `in_call`, next steps by owner; `schema_version` 2.0); shared `style_rules` prompt in summary, email and chat; soft style checks and a `clarity_conciseness` judge rubric; email layout with labelled sections and plain-text dash/numbered lists; goldens rewritten without filler | `tests/test_organise_and_style.py` |
| Warmer emails, no settings | One brief thank-you is expected in every customer email (soft `courtesy_line` check instead of a filler ban). The AI decides the format and length (`email@v4`, `style_rules@v3`); the thank-you, length and meeting-offer options were removed from the contracts, API, CLI and demo page, leaving fixed guardrails (220-word ceiling, never a meeting offer) | `tests/test_organise_and_style.py` |
| Demo page | `demo.html` now renders the JSON: grouped summary with owner columns, uncertainty badges, clickable timestamp chips that highlight the transcript line, recommendations cheatsheet (set-aside items greyed), editable email with live checks and one-click fix, streaming chat with copy-ready email boxes; URL shortcuts for demos; checked with headless-browser screenshots | `tests/test_frontend_support.py` (page only calls endpoints that exist) |

165 tests pass offline.

## Fixes found while building (kept for context)

- Email meeting-offer regex first matched plain "during our call"; narrowed and covered by positive and negative tests.
- Scope-statement regex missed "your X and Y teams" / "their team"; widened. Hyphenated terms ("SSL-inspection") now match; version check no longer matches inside longer numbers.
- Redaction missed short company forms ("Kestrel Freight's"); metadata names now include legal-suffix-stripped and first-word forms.
- A template chat rule ("next step") shadowed the "formal" rewrite rule; reordered.
- Style rules first showed up in the goldens themselves (every golden email opened with a thank-you line, one summary line ran to 32 words); the goldens were rewritten and are now checked against the style rules in tests.

## Not done / next

1. **Run against a real AI server** (Claude or local) and read the judge scores. Expect prompt tuning for weaker models; check `email_first_pass_clean` and `repairs` in `meta`.
2. Agree the third-party term list and the open questions in [PLAN.md](PLAN.md) with the other module owners.
3. Replace `FixtureToolBackend` with real docs/Jira/Confluence integrations (the Protocol is in `contracts/tools.py`).
4. Real-format adapters for the transcript and recommendation modules once their output is known.
5. Judge calibration: compare judge scores with a human read of 5–10 real outputs before trusting thresholds.
6. Optional: conversation persistence beyond in-memory, real user authentication (the API key is a shared secret), live/incremental transcript endpoint, async jobs with progress for summary/email.

## Known limits

- Fake provider proves plumbing and checks, not model quality.
- Redaction is regex-based; free-text names not in the call metadata are not caught. Four-part dotted numbers look like IPs.
- Third-party troubleshooting detection is a heuristic (term list + verbs + scope phrases).
- Style (concise, plain English, no filler) is only approximated by soft checks (filler phrases, line and sentence length); the judge rubric and a human read are the real test. The summary now costs two model calls.
- Chat state and meetings are in memory (run one worker). The API key is a single shared secret, not per-user auth; CORS and the key are off unless configured.
