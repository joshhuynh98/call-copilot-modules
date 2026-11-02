# Reviewing recommendation sources before writing

Before the summary and email are written, the tool opens every source the recommendations cite and reads it: public documentation pages, Jira tickets and Confluence pages. What it reads is used to judge the recommendations, to keep dead or unsupported links out of the customer email, and to give the chatbot real content to answer from.

```
recommendations ──► ReferenceReviewer ──► SourceReview[] ──► organise (judges relevance, "supported?", one-line gist)
  (with sources)      │                                    ├─► email (links only to pages that exist and support the action)
                      ├─ public_doc  → DocsReader (docs platform JSON API, then plain HTTP, then a real browser)
                      ├─ jira        → AtlassianReader (MCP getJiraIssue)
                      ├─ confluence  → AtlassianReader (MCP getConfluencePage)
                      └─ other types → skipped (no reader)
```

It runs in `run_summary` (`app/pipeline.py`), so the CLI, the HTTP API and the evals all get it. Code: `src/callcopilot/shared/references/`.

## What each source becomes: a `SourceReview`

| Field | Meaning |
|---|---|
| `status` | `ok` (opened), `redirected` (opened, the link moved), `not_found` (dead link), `blocked` (the site refused us or the page needs JavaScript and no browser could run), `no_access` (Atlassian refused or is not configured), `skipped` (no reader, host not allowed, or no ticket/page id), `error` |
| `final_url` | where the link really ends up |
| `title`, `excerpt` | the passage(s) most relevant to the recommendation, trimmed (default 1200 characters). A ticket's status, resolution and fix versions are always kept |
| `note` | why it was skipped or failed, safe to show the agent |

`MeetingSummary.source_reviews` carries them. `POST /v1/recommendations/review` returns them for a recommendation set on its own.

## How the content is used

- **Organise stage:** the model sees `OPENED. It says: ...` (or `NOT FOUND`, `could not be read`) under each recommendation. It must use opened content only to judge recommendations, never as something said on the call, and never claim anything about a source that was not opened. For each recommendation it returns `supported` (does the opened content back the action for this call, right product and version?) and a one-sentence `gist`.
- **Code guard:** a recommendation whose sources were not opened can never carry a `supported` verdict or a gist (the model's claim is discarded). Gists have ticket ids and links stripped.
- **Customer email:** it gets each kept recommendation's action plus the **gist** only. Raw page or ticket text never goes into the email prompt, so internal ticket content and other customers' details cannot leak into it. Links are limited to pages that exist: `not_found` links are dropped, links whose recommendation is `supported: false` are dropped, redirected links are replaced by the page they lead to. A link that could not be checked (`blocked`, `error`) stays, and the draft warns the agent: "link not verified".
- **Chat:** the agent sees the opened excerpts, the supported / not supported verdict, and which sources could not be verified.
- **Summary:** reviewed content never appears in the summary's call facts.

## Configuration (environment variables, all optional)

| Variable | Meaning |
|---|---|
| `REFERENCE_REVIEW` | `auto` (default): on, but with `AI_PROVIDER=fake` it reads the fixture copies so demos work offline. `on`, `off`, `fixtures` |
| `REFERENCE_DOCS_MODE` | `auto` (default): the docs platform's public JSON API first, then plain HTTP, then a browser. `devhub`, `http` or `browser` to force one step |
| `REFERENCE_DOCS_API` | optional override of the docs platform API base URL (default `https://api.developerhub.io/api/public`); for tests or a mirror |
| `REFERENCE_ALLOWED_HOSTS` | the only hosts ever fetched; default `docs.opswat.com,www.opswat.com`. https only. Every redirect hop is re-checked |
| `REFERENCE_BROWSER_CHANNEL` | `msedge` or `chrome` to use an installed browser; blank uses Playwright's own (`playwright install chromium`) |
| `REFERENCE_TIMEOUT_SECONDS`, `REFERENCE_EXCERPT_CHARS` | per source (default 20 s, 1200 characters) |
| `ATLASSIAN_MCP_URL`, `ATLASSIAN_MCP_AUTH`, `ATLASSIAN_CLOUD_ID` | the Atlassian MCP server, the full `Authorization` header value it expects, and an optional cloud id (defaults to each link's site host). Blank = Jira and Confluence sources report `no_access` and everything else carries on |

Optional install: `uv pip install -e ".[browser]"` for Playwright.

## Try it on your own setup

```powershell
.venv\Scripts\callcopilot review --url https://www.opswat.com/docs/mdicap/configuration/additional-tuning
.venv\Scripts\callcopilot review --url https://yourorg.atlassian.net/browse/SUP-123 --jira SUP-123
.venv\Scripts\callcopilot review --scenario icap_upload_timeouts      # every source of a recommendation set
.venv\Scripts\callcopilot review --scenario icap_upload_timeouts --live-docs   # same, with real docs.opswat.com links
```

It prints each source's status, where it leads, its title, and the excerpt, plus whether the browser fallback and Atlassian MCP are configured. This is the quickest way to see what works in your environment.

## Security

- **SSRF guard:** recommendations come from another module, so their URLs are untrusted. Only https URLs on the allowlist are requested, and redirects to other hosts are stopped without contacting them.
- **Untrusted content:** fetched text is wrapped as data (`<<<BEGIN UNTRUSTED ...>>>`), like the transcript, and is redacted like everything else before reaching the AI server.
- **Data minimisation:** internal ticket text reaches the organise stage and the agent's chat only. The customer email sees gists, with ids and links removed.
- **Read-only:** the only Atlassian tools called are `getJiraIssue` and `getConfluencePage`.

## What was verified, and what was not

| Piece | Status |
|---|---|
| HTML to text, excerpt selection, redirect and allowlist handling, status mapping, caching | Unit-tested offline |
| Real browser (Playwright) rendering a JavaScript-only page, where plain HTTP sees nothing | Tested locally against a test page (skipped if no browser is installed) |
| MCP client (handshake, session, headers, JSON and SSE replies, tool errors) | Tested against a mock server and against a real MCP server built with the official SDK |
| Reading Atlassian's real tools (`getJiraIssue`, `getConfluencePage`) | **Not tested against the live Atlassian MCP server.** It needs a credential this repo does not have. The argument names come from the published tool signatures |
| Reading the real docs (MetaDefender Core and ICAP Server pages, versions, old host, dead links) | **Works, verified live** through the platform's public API (opt-in live test: `RUN_LIVE_DOCS=1`). The website itself still refuses automated access; see below |

## How the public docs are read: the platform's JSON API

The OPSWAT docs website refuses automated access (tried 2026-11-01): plain HTTP returns an empty JavaScript shell for every URL, even a page that does not exist, and a headless browser with default settings gets **HTTP 403**. This tool does **not** try to get around that (no disguised browser identity, no stealth settings).

Instead it uses what the docs are hosted on. They are served by DeveloperHub, which exposes every page as JSON through a **public, unauthenticated API** meant for programmatic use (the `opswat-docs-reader` skill documents it). `devhub.py` resolves a docs URL in three requests and caches the result:

```
GET https://api.developerhub.io/api/public/project/www.opswat.com?path=/docs/<product>   versions and sections (300 to 700 KB: cached per product)
GET https://api.developerhub.io/api/public/documentation/<section id>/index              the section's pages, each with id, slug and title (cached)
GET https://api.developerhub.io/api/public/page/<page id>                                the page: title and body (a structured JSON document)
```

Address shapes handled: `www.opswat.com/docs/<product>[/<version>][/<section>[/<page>]]` and the old `docs.opswat.com/<product>/...`. A page URL gives the real page text (lists and tables are turned into plain lines, one list item or table row per line); a product or section URL gives a short description of what is there.

Behaviours worth knowing (all tested, offline and against the live API):

- **Unknown products silently fall back to another project** (`/docs/zzzz` returns MetaDefender Core). The reader checks the returned project's `basepath` itself, so an unknown product is `not_found`.
- **A page is matched by the section index's slug**, not by the page JSON's own slug (they can differ).
- **`not_found` is definitive** (no such product, section or page) and is not second-guessed by the website. A docs *version* the API does not serve is reported as `error`, not `not_found`, because the website may still have it; the pipeline then falls back to plain HTTP and the browser.
- **Safety:** only the configured API host is contacted, only a validated product slug and numeric ids are placed in API URLs, and page slugs are only compared, never interpolated.
- **Order in `auto` mode:** the API, then plain HTTP, then a browser (Playwright). `REFERENCE_DOCS_MODE=devhub|http|browser` forces one step.

Verified live on 2026-11-01: product, section and page URLs for MetaDefender Core and ICAP Server (latest, versioned such as `v5.14.1`, and the old `docs.opswat.com` form), dead pages, and an unknown product. Reproduce with `RUN_LIVE_DOCS=1 pytest tests/test_devhub.py -k live`, or `callcopilot review --url <docs link>`.

**Dependency to confirm:** this is a third-party platform's public API. It could change or be rate-limited without notice, so the docs owners should confirm it as the supported route for this service. If they prefer another (an allowlisted IP, a token header, an internal mirror, or the docs source files), the reader needs only a different base URL or extra headers; `REFERENCE_DOCS_API` already overrides the base URL.

## Limits

- One MCP session per client, created on first use. Each summary opens sources in parallel (4 at a time); docs results are cached for an hour in memory.
- Source types without a reader (`internal_kb`, `past_case`) are reported as `skipped`.
- Only the first 12 distinct sources of a recommendation set are opened.
- Excerpts are chosen by keyword overlap with the recommendation, not by meaning, so a long page can still lose the best passage; the model judges only what it is shown.
