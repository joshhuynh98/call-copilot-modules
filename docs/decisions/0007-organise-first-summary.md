# 0007: Organise first, then write; concise plain-English style

**Status:** accepted (product-owner preferences)

**Context:** Summaries and emails must be concise, free of filler, in plain English, formatted as lists, and sensible even when the transcript and recommendations are messy (out of order, repeated, contradictory, noisy). A single prompt that asks for the final structure in one pass does not reliably group messy input, especially on smaller local models.

**Decision:**
- The summary runs in two model calls. **Organise** sorts the raw material into a grouped `CallBrief` (issues, before the call, during the call, findings, next actions by owner, open questions, third-party parts) and triages the recommendations. **Write** rewrites the brief concisely under the shared `style_rules` prompt. Long calls are organised chunk by chunk and merged.
- The summary has `issues[]` (not one issue), separate `before_call` and `in_call`, and `next_actions` with an owner; UIs and renderers group next steps by owner (support, customer, other OPSWAT teams). `MeetingSummary.schema_version` is `2.0`.
- Code guards the writer: dropped facts are restored from the brief, doubt flags are never lost, product and third-party parts are taken from the brief.
- Recommendation triage: the organise stage flags duplicates and unrelated items (`recommendation_notes`); code also flags duplicates itself and never removes everything. The email uses only the triaged recommendations for links and context; the ticket ids of all provided recommendations stay forbidden. The chatbot still shows the agent the full list.
- Style is a versioned prompt (`style_rules`) used by summary, email and chat, backed by **soft** deterministic checks (filler phrases, bullet length, sentence length) and a judge rubric. Soft checks never fail a run but do trigger an email repair round.
- Email lists: numbered and dash lines are plain text and allowed; `*`, `+`, `•` bullets and other markdown are not (they are normalised or removed).

**Trade-offs:** one more model call (about double the summary latency); a breaking change to the summary shape (done before any UI exists); prose-quality rules can only be approximated by checks, so the judge must be read by a human at first.
