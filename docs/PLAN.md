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
| Meetings in emails | No offer/request for calls or meetings unless `allow_meeting_offer=true`. |
| Third-party products | OPSWAT is not responsible. Email/chat state it is out of scope and give no troubleshooting for it. |
| AI server | Undecided (Claude API or local OpenAI-compatible server). Design works for either via env vars only. |

## Assumptions

- Python 3.11 + FastAPI + Pydantic v2; teammates can import the library or call HTTP.
- Recommendations reach the chatbot as a snapshot per request, not a live stream.
- `case_id` identifies a call; segment ids are stable within a call.
- The third-party term list in `src/callcopilot/data/third_party_terms.txt` is a draft to be agreed with the team.

## Open questions for the other module owners

1. Transcript timestamps: relative ms from call start (what we assume) or wall clock?
2. Are segment ids stable between interim and final transcripts? Recommendations reference them.
3. Is `text_translated` null when the language is already English? Is a per-segment confidence available?
4. Are product and version taken from the case record, or detected during the call? They may be missing or wrong.
5. Who owns the third-party scope list (VDI, proxies, firewalls, SSL inspection, other vendors)?
6. Recommendations: `rank`, `trigger_segment_ids` and `sources[].type` are required by our quality bar. Can the recommendation module produce them?
7. How are internal vs public docs distinguished at source? We rely on `sources[].type == public_doc` for customer-visible links.

## Build order (all done unless marked)

See [BUILD_LOG.md](BUILD_LOG.md).
