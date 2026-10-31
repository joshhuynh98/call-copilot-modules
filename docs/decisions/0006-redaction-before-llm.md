# 0006: Reversible redaction before any LLM call

**Status:** accepted

**Decision:** `pipeline.make_llm` wraps the provider client in `RedactingClient`. Emails, phone numbers, IPv4 addresses, card-like numbers, API keys/tokens/passwords and the names from the call metadata (company, contact, agent, including short forms) become placeholders like `[EMAIL_1]`. The same `Redactor` restores them in replies and in streams (placeholders split across chunks are held back until complete). `REDACTION_MODE=off` disables it; `REDACTION_CATEGORIES` restricts it.

**Why:** call content is customer data and the AI server may be an external API. Restoring placeholders means the email still greets the customer by name.

**Limits:** regex-based, not NER. Free-text personal names that are not in the call metadata are not caught. A dotted number that looks like an IPv4 address (four parts) is redacted, so write versions with three parts or drop the `ip` category. Raw transcripts are never logged by default.
