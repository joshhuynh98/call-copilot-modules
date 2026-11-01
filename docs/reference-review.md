# Reviewing recommendation sources before writing

Before the summary and email are written, the tool opens every source the recommendations cite and reads it: public documentation pages, Jira tickets and Confluence pages. What it reads is used to judge the recommendations, to keep dead or unsupported links out of the customer email, and to give the chatbot real content to answer from.

```
recommendations ──► ReferenceReviewer ──► SourceReview[] ──► organise (judges relevance, "supported?", one-line gist)
  (with sources)      │                                    ├─► email (links only to pages that exist and support the action)
                      ├─ public_doc  → DocsReader (HTTP, then a real browser)
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
| `REFERENCE_DOCS_MODE` | `auto` (default): plain HTTP first, a browser if that is blocked or the page has no text without JavaScript. `http` or `browser` to force one |
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
| Reading the real docs site | **Does not work from this machine yet. See below** |

## The docs site currently refuses automated access

Tried on 2026-11-01 against `docs.opswat.com` (it redirects to `www.opswat.com/docs`):

- **Plain HTTP:** every URL returns an empty JavaScript shell with no readable text, including a page that does not exist. So plain HTTP cannot even tell a live page from a dead one.
- **A real browser (headless Edge, default settings):** the site answers **HTTP 403** to every page.

So today the docs reader reports `blocked` for these links, the pipeline carries on, the email keeps the link and warns the agent that it could not be verified, and `callcopilot review` shows exactly that.

**A route that does work (found 2026-11-01, not built into the tool yet):** the docs are hosted on DeveloperHub, which serves every page as JSON through a public, unauthenticated API (the `opswat-docs-reader` skill documents it). Reading `https://www.opswat.com/docs/mdcore` this way worked: the lookup returned the MetaDefender Core project (30 public versions, latest v5.23.0, 22 sections such as Configuration, Installation, Troubleshooting, Release Notes), a section index (Configuration: 84 pages) and a real page ("Management Console - Login", whose text says the console is at `http://<server>:<port>`, default `http://localhost:8008`).

```
GET https://api.developerhub.io/api/public/project/www.opswat.com?path=/docs/mdcore        project, versions, sections
GET https://api.developerhub.io/api/public/documentation/{section_id}/index                 the section's page tree
GET https://api.developerhub.io/api/public/page/{page_id}                                   one page: title, slug, body (JSON document)
```

Things to know before building a reader on it: the project lookup resolves a URL to its **product**, not to a page, so a page URL has to be matched by walking the section index and reading pages until the slug matches (cacheable); a page's text is in `body` (a structured JSON document), and `content` can be empty; entries of type `category` are folders; the lookup response is large (300 to 700 KB), so cache it per product; a dead page URL still returns the product, so "page not found" must be decided by failing to match the slug. This route is a documented public API, not a way around the site's protection, but it is a third party's API, so it should be confirmed with the docs owners as the supported route.

**This tool does not try to get around the site's bot protection** (no disguised browser identity, no stealth settings). That should be solved with the owners of the docs site. Options, roughly in order of preference:

0. **Use the DeveloperHub public API described above** (needs a small reader built; confirm with the docs owners).
1. Ask the docs/web team for a sanctioned route for this service: an allowlisted IP, an API key or token header, or an internal mirror of the docs.
2. Read from the docs' source (a repository or export of the pages) instead of the website.
3. Use a search or sitemap API if the site exposes one.

Once there is a route, the change is small: the reader would take extra headers or a different base URL (not built yet, since it depends on what is offered).

## Limits

- One MCP session per client, created on first use. Each summary opens sources in parallel (4 at a time); docs results are cached for an hour in memory.
- Source types without a reader (`internal_kb`, `past_case`) are reported as `skipped`.
- Only the first 12 distinct sources of a recommendation set are opened.
- Excerpts are chosen by keyword overlap with the recommendation, not by meaning, so a long page can still lose the best passage; the model judges only what it is shown.
