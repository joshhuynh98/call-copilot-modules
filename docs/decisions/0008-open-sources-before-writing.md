# 0008: Open the recommendation sources before writing

**Status:** accepted (product-owner requirement)

**Context:** Recommendations arrive as titles, one-line actions and links. Without opening the links we cannot tell whether a documentation page exists, whether it supports the action, or whether a Jira ticket says the problem is fixed, and a dead link could reach a customer. The product owner wants the tool to read the docs.opswat.com pages (Playwright or similar) and any referenced Jira ticket or Confluence page (Atlassian MCP, if it has access) before the summary and email are written.

**Decision:**
- A `ReferenceReviewer` opens each distinct source before the organise stage and produces a `SourceReview` per source (status, final URL, title, trimmed excerpt, note). Public docs: plain HTTP first, a real browser (Playwright) as fallback for JavaScript pages. Jira and Confluence: an Atlassian MCP server, through a small MCP client of our own. No access, or a failure, never stops the pipeline: the source is reported and everything else carries on.
- The organise model sees the opened content and returns, per recommendation, `supported` and a one-sentence `gist`. Code refuses any verdict or gist for a recommendation whose sources were not opened, and strips ids and links from gists.
- **The customer email never sees raw page or ticket text**, only gists, so internal ticket content and other customers' details cannot leak into it. It links only to pages that exist and support the action; redirects are resolved; links that could not be checked stay but trigger a warning to the agent.
- Only https URLs on an allowlist are fetched, with every redirect hop re-checked (SSRF guard), because recommendation URLs come from another module.
- Review is on by default; `REFERENCE_REVIEW=off` disables it, and with the fake AI provider it reads offline fixture copies so demos and tests need no network.
- We do **not** work around a site's bot protection. If the docs site refuses automated access, the source is reported as `blocked` and the owners of the site are asked for a sanctioned route.

**Why our own MCP client:** the official SDK's client API changed between major versions and cannot be checked against Atlassian's server from here. The protocol part we need (initialise, session id, `tools/call`, JSON or SSE replies) is small; ours is tested against a mock and against a real server built with the official SDK.

**Trade-offs:** extra latency before writing (sources are opened in parallel, docs are cached for an hour); the live Atlassian server and the live docs site could not be tested from this repository (see [reference-review.md](../reference-review.md)); excerpts are picked by keyword overlap, not meaning; the organise prompt grows with the excerpts.
