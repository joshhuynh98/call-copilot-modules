# 0004: Prompted tool protocol by default; search-before-unknown guard

**Status:** accepted

**Decision:**
- Tools (`search_docs`, `search_jira`, `search_confluence`, `similar_cases`, `customer_history`, plus `search_transcript` for very long calls) are called through a plain-text protocol: the model replies with one line `TOOL_CALL {"name": ..., "arguments": {...}}`; results come back as `TOOL_RESULT` messages. Works on any model. `AI_TOOL_MODE=native` switches to provider function calling; history is still replayed as text, so both providers behave the same.
- The streamer holds back the first few characters of each reply to detect a `TOOL_CALL` line without showing it to the user.
- **Guard (enforced in code):** the first ~240 characters of a fresh answer are held back. If they read as "I don't know / can't tell / not mentioned" and no search tool has run, the answer is discarded, `search_docs`, `search_jira` and `search_confluence` are run for the model, and it answers again. A not-found answer gets a deterministic "What I checked: ..." footer.
- Tool output and transcript text are wrapped in `<<<BEGIN/END UNTRUSTED ...>>>` markers (delimiters inside the content are neutralised). Tools are read-only.

**Trade-off:** an answer that legitimately says "the customer did not mention X" also triggers the searches. That is intended: searching first is the requirement.
