# Plan, decisions and open questions

Approved plan for the modules I own. Timeline: 2 weeks; build fast, leave time to review and improve.

## Scope

1. **Meeting summary**: structured wrap-up (issue, product/version, steps, findings, next actions with owner, open questions) with evidence links to transcript segments.
2. **Email wrap-up**: a DRAFT follow-up to the customer. Plain text, concise, in the agent's style.
3. **In-app chatbot**: grounded in transcript, summary and recommendations; searches docs/Jira/Confluence through tool interfaces.
4. **Recommendation spec + eval harness**: I own the spec and the scoring, not the engine.

## Decisions given by the product owner

| Topic | Decision |
|---|---|
| Language | Emails always English. Transcript and recommendations fed in are English. Demo strictly English. |
| Data format between modules | Unknown yet. We define our own contracts now; an AI-assisted adapter can convert/integrate later. Contracts are lenient so adapters have room. |
| Links in customer emails | **Public docs only.** The email may refer to internal work generically but must not contain Jira ticket ids or Atlassian/internal-KB links. Enforced in code (see [email-rules.md](email-rules.md)). |
| Meetings in emails | Never offer or request a call or meeting. A fixed rule with no setting (it was an option at first; the option was removed). |
| Style of summary and email | Concise, minimal or no filler. Plain English: easy to understand, not childish. Lists (bullets or numbered) where applicable. See [ADR 0007](decisions/0007-organise-first-summary.md). |
| Messy input | The AI reviews it first and groups it logically (issues, what happened before and during the call, findings, next steps by owner, open questions) before anything is written. Duplicate or unrelated recommendations are set aside. |
| Email format | **The AI decides** the format and length of each email, guided by principles: informative, courteous (one brief thank-you for the customer's time), concise, easy to scan. No user-facing email settings (no thank-you toggle, length or meeting-offer settings). |
| Email lists | Plain-text numbered and dash lines are allowed; other markdown is still not. |
| Reviewing sources | Before writing, the tool opens and reads every source the recommendations cite: docs pages (a real browser when the page needs JavaScript) and Jira/Confluence through Atlassian MCP when it has access. Dead or unsupported links never reach the customer; the email sees one-line gists, never raw page or ticket text. See [reference-review.md](reference-review.md) and [ADR 0008](decisions/0008-open-sources-before-writing.md). |
| Third-party products | OPSWAT is not responsible. Email/chat state it is out of scope and give no troubleshooting for it. |
| AI server | Undecided (Claude API or local OpenAI-compatible server). Design works for either via env vars only. |

## Assumptions

- Python 3.11 + FastAPI + Pydantic v2; teammates can import the library or call HTTP.
- Recommendations reach the chatbot as a snapshot per request, not a live stream.
- `case_id` identifies a call; segment ids are stable within a call.
- The third-party term list in `src/callcopilot/shared/data/third_party_terms.txt` is a draft to be agreed with the team.

## Open questions for the other module owners

1. Transcript timestamps: relative ms from call start (what we assume) or wall clock?
2. Are segment ids stable between interim and final transcripts? Recommendations reference them.
3. Is `text_translated` null when the language is already English? Is a per-segment confidence available?
4. Are product and version taken from the case record, or detected during the call? They may be missing or wrong.
5. Who owns the third-party scope list (VDI, proxies, firewalls, SSL inspection, other vendors)?
6. Recommendations: `rank`, `trigger_segment_ids` and `sources[].type` are required by our quality bar. Can the recommendation module produce them?
7. How are internal vs public docs distinguished at source? We rely on `sources[].type == public_doc` for customer-visible links.
8. **Docs site access:** www.opswat.com/docs answers plain HTTP with an empty JavaScript shell and a headless browser with HTTP 403. A public DeveloperHub JSON API (`api.developerhub.io/api/public`) does serve the pages; can the docs owners confirm it as the supported route for this service (or offer an allowlist, token or mirror)? We do not bypass the protection.
9. **Atlassian MCP access:** which MCP endpoint and `Authorization` header can a server use non-interactively (service account, API token, or OAuth)? Which Jira projects and Confluence spaces should it be allowed to read?

## Build order

See [BUILD_LOG.md](BUILD_LOG.md).
