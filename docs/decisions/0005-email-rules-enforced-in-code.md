# 0005: Email hard rules are enforced in code

**Status:** accepted

**Decision:** The email prompt states the rules, but `draft_email` also enforces them: generate → deterministic checks (`modules/email/checks.py`) → up to 2 repair rounds that quote the violations → last-resort sanitiser (`enforce_email_text`) → re-check. Anything the sanitiser had to fix, or could not fix, is listed in `EmailDraft.warnings` for the agent.

**Rules:** plain text only (numbered and dash lists are allowed, other markdown is not; see ADR 0007); paragraphs ≤ 60 words (there is no overall length limit: see the 2026-11-02 update below); never an offer or request for a call or meeting (fixed, no setting); **links only from `public_doc` sources in the provided recommendations**; no Jira keys; no Atlassian/internal links; if the summary found a third-party component the email must state it is outside OPSWAT's scope and must not suggest fixes for it.

**Product-owner decision (internal references):** the email may refer to internal work generically ("I will check with our engineering team"), but never with ticket ids or internal links. The prompt therefore receives only the action text of recommendations, never internal ids/URLs (tested).

**Update 2026-11-02: email length is the model's call.** The original rule had a hard 220-word ceiling and a soft 150-word target; both reached the model as numbers ("never exceed 220") and the soft target still drove rewrites, so the model was not really free to choose. Both are removed, and no word count acts on the draft any more. Conciseness is steered by the prompt (fewest words that stay clear and complete, no padding, no filler) and by the qualitative checks and judge rubric, not by a cap. The prompt keeps "most emails need about 80 to 150 words" as a rule of thumb only, and one advisory check, `long_email` (over 220 words), shows the agent a warning; it never fails, cuts or rewrites anything.

**Known limit:** whether a sentence is troubleshooting advice for a third-party product is judged by a heuristic (term list + verbs + scope phrases). The LLM judge covers nuance.
