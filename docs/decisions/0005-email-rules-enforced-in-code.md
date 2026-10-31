# 0005: Email hard rules are enforced in code

**Status:** accepted

**Decision:** The email prompt states the rules, but `draft_email` also enforces them: generate → deterministic checks (`checks/email.py`) → up to 2 repair rounds that quote the violations → last-resort sanitiser (`enforce_email_text`) → re-check. Anything the sanitiser had to fix, or could not fix, is listed in `EmailDraft.warnings` for the agent.

**Rules:** plain text only (numbered and dash lists are allowed, other markdown is not; see ADR 0007); length ceiling (220 words hard, 150 soft; paragraphs ≤ 60 words); never an offer or request for a call or meeting (fixed, no setting); **links only from `public_doc` sources in the provided recommendations**; no Jira keys; no Atlassian/internal links; if the summary found a third-party component the email must state it is outside OPSWAT's scope and must not suggest fixes for it.

**Product-owner decision (internal references):** the email may refer to internal work generically ("I will check with our engineering team"), but never with ticket ids or internal links. The prompt therefore receives only the action text of recommendations, never internal ids/URLs (tested).

**Known limit:** whether a sentence is troubleshooting advice for a third-party product is judged by a heuristic (term list + verbs + scope phrases). The LLM judge covers nuance.
