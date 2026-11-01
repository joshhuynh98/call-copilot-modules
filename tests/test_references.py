"""Opening the sources behind recommendations before writing: docs pages, Jira, Confluence (all offline or local)."""
import http.server
import json
import threading

import httpx
import pytest

from callcopilot.modules.email import draft_email
from callcopilot.modules.recommendations import complete_notes, email_link_recs
from callcopilot.modules.summary import summarize
from callcopilot.shared.contracts import MeetingSummary, RecNote, SourceReview, SourceType
from callcopilot.shared.llm import LLMConfig
from callcopilot.shared.references import ReferenceReviewer, load_reference_config, make_reviewer
from callcopilot.shared.references.atlassian import AtlassianReader
from callcopilot.shared.references.config import ReferenceConfig
from callcopilot.shared.references.docs import BrowserUnavailable, DocsReader
from callcopilot.shared.references.excerpt import select_excerpt
from callcopilot.shared.references.html_text import extract_page
from callcopilot.shared.references.mcp_client import McpError, McpHttpCaller
from conftest import ScriptedLLM

FAKE = LLMConfig(provider="fake")
HOST = "docs.example-opswat.invalid"
DOC = f"https://{HOST}/icap/configuration/scan-timeouts"
LONG = "The scan timeout setting controls how long ICAP Server waits for Core to finish scanning a large archive before it gives up. " * 4


def page(title="Scan timeouts", body=LONG):
    return (f"<html><head><title>{title} - MetaDefender ICAP Server</title><script>var x=1</script></head><body>"
            f"<nav>Home | Products | Pricing</nav><main><h1>{title}</h1><p>{body}</p><p>Unrelated text about mail servers and logging levels in the product.</p></main>"
            f"<footer>Copyright</footer></body></html>")


def cfg(**kw):
    base = dict(mode="on", docs_mode="http", allowed_hosts=(HOST,), timeout_s=5.0, excerpt_chars=400)
    return ReferenceConfig(**{**base, **kw})


def docs_reader(routes, **kw):
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        status, body, headers = routes.get(str(req.url), (404, "", {}))
        return httpx.Response(status, text=body, headers=headers)

    return DocsReader(cfg(**kw.pop("cfg", {})), transport=httpx.MockTransport(handler), **kw), calls


# -- HTML to text and excerpts ---------------------------------------------------------------------------------
def test_extract_prefers_main_content_and_drops_chrome():
    title, text = extract_page(page())
    assert title == "Scan timeouts - MetaDefender ICAP Server"
    assert "scan timeout setting" in text and "Pricing" not in text and "Copyright" not in text and "var x" not in text


def test_excerpt_keeps_the_relevant_paragraphs_in_order():
    text = "\n".join(["Intro paragraph about licensing and nothing else of interest here.",
                      "The request timeout defines how long ICAP Server waits for a Core scan result.",
                      "Mail server configuration needs an SMTP host and a port number to work.",
                      "Increase the timeout for large archives so the scan can finish in time."])
    out = select_excerpt(text, "raise the ICAP request timeout for large archives", 200)
    assert "request timeout defines" in out and "large archives" in out and "SMTP" not in out
    assert out.index("request timeout") < out.index("large archives")
    assert select_excerpt("", "x", 100) == "" and select_excerpt("short bits", "zzz", 50) == "short bits"


# -- config -----------------------------------------------------------------------------------------------------
def test_reference_config_modes():
    assert load_reference_config({}, provider="fake").mode == "fixtures"  # offline demos read the fixture copies
    assert load_reference_config({}, provider="openai").mode == "on"
    assert not load_reference_config({"REFERENCE_REVIEW": "off"}).enabled
    c = load_reference_config({"REFERENCE_ALLOWED_HOSTS": "A.example, b.example", "REFERENCE_TIMEOUT_SECONDS": "7"}, provider="openai")
    assert c.allowed_hosts == ("a.example", "b.example") and c.timeout_s == 7.0
    assert "docs.opswat.com" in load_reference_config({}, provider="openai").allowed_hosts
    for bad in ({"REFERENCE_REVIEW": "maybe"}, {"REFERENCE_DOCS_MODE": "x"}, {"REFERENCE_EXCERPT_CHARS": "lots"}):
        with pytest.raises(Exception):
            load_reference_config(bad, provider="openai")


# -- public docs reader ------------------------------------------------------------------------------------------
def test_docs_page_is_read():
    reader, _ = docs_reader({DOC: (200, page(), {})})
    r = reader.read(DOC)
    assert r.status == "ok" and r.title.startswith("Scan timeouts") and "scan timeout setting" in r.text


def test_hosts_outside_the_allowlist_and_plain_http_are_never_requested():
    reader, calls = docs_reader({})
    for url in ("https://evil.example.invalid/x", "http://" + HOST + "/x", "https://169.254.169.254/latest/meta-data", "ftp://" + HOST):
        assert reader.read(url).status == "skipped"
    assert calls == []  # nothing left the machine


def test_redirects_are_followed_inside_the_allowlist_and_stopped_outside_it():
    new = f"https://{HOST}/icap/configuration/new-timeouts"
    reader, calls = docs_reader({DOC: (301, "", {"location": new}), new: (200, page(), {})})
    r = reader.read(DOC)
    assert r.status == "redirected" and r.final_url == new and r.text

    evil = "https://evil.example.invalid/steal"
    reader, calls = docs_reader({DOC: (302, "", {"location": evil})})
    r = reader.read(DOC)
    assert r.status == "blocked" and "not allowed" in r.note and calls == [DOC]  # the evil host was never contacted


@pytest.mark.parametrize("code,status", [(404, "not_found"), (410, "not_found"), (403, "blocked"), (429, "blocked"), (500, "error")])
def test_http_errors_map_to_clear_statuses(code, status):
    reader, _ = docs_reader({DOC: (code, "nope", {})})
    assert reader.read(DOC).status == status


def test_javascript_shell_is_reported_as_unreadable_without_a_browser():
    reader, _ = docs_reader({DOC: (200, "<html><head><title>Docs</title></head><body><div id='app'></div><script src='a.js'></script></body></html>", {})})
    r = reader.read(DOC)
    assert r.status == "blocked" and "JavaScript" in r.note


def test_auto_mode_falls_back_to_the_browser_for_javascript_pages():
    shell = "<html><body><div id='app'></div></body></html>"
    seen = []

    def browser(url):
        seen.append(url)
        return 200, url, page()

    reader, _ = docs_reader({DOC: (200, shell, {})}, cfg={"docs_mode": "auto"}, browser_fetch=browser)
    r = reader.read(DOC)
    assert r.status == "ok" and "scan timeout setting" in r.text and seen == [DOC]


def test_auto_mode_keeps_the_http_result_when_no_browser_is_installed():
    def no_browser(url):
        raise BrowserUnavailable("Playwright is not installed")

    reader, _ = docs_reader({DOC: (403, "", {})}, cfg={"docs_mode": "auto"}, browser_fetch=no_browser)
    r = reader.read(DOC)
    assert r.status == "blocked" and "Playwright is not installed" in r.note


def test_browser_that_ends_on_a_foreign_host_is_rejected():
    reader, _ = docs_reader({}, cfg={"docs_mode": "browser"}, browser_fetch=lambda u: (200, "https://evil.example.invalid/", page()))
    assert reader.read(DOC).status == "blocked"


def test_results_are_cached_but_failures_are_retried():
    reader, calls = docs_reader({DOC: (200, page(), {})})
    reader.read(DOC), reader.read(DOC)
    assert len(calls) == 1
    flaky, calls = docs_reader({DOC: (500, "x", {})})
    flaky.read(DOC), flaky.read(DOC)
    assert len(calls) == 2


def _browser_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            for channel in ("msedge", "chrome", None):
                try:
                    p.chromium.launch(**({"channel": channel} if channel else {})).close()
                    return True
                except Exception:
                    continue
    except Exception:
        pass
    return False


@pytest.fixture
def js_site():
    """A local page whose text only exists after JavaScript runs, like the real docs site."""
    body = ("<html><head><title>JS docs</title></head><body><main id='m'></main><script>"
            "document.getElementById('m').innerText = 'Rendered by JavaScript. ' + 'The scan timeout setting controls how long ICAP Server waits. '.repeat(8);"
            "</script></body></html>")

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body.encode())

        def log_message(self, *a):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/docs"
    server.shutdown()


@pytest.mark.skipif(not _browser_available(), reason="no Playwright browser available (install one or use msedge/chrome)")
def test_real_browser_renders_a_javascript_page(js_site, monkeypatch):
    """The whole point of the browser fallback: plain HTTP sees an empty shell, the browser sees the text."""
    channel = next(c for c in ("msedge", "chrome", "") if _channel_works(c))
    config = cfg(docs_mode="auto", allowed_hosts=("127.0.0.1",), browser_channel=channel)
    monkeypatch.setattr(DocsReader, "allowed", lambda self, url: url.startswith("http://127.0.0.1"))  # local test page is plain http
    result = DocsReader(config).read(js_site)
    assert result.status == "ok" and "Rendered by JavaScript" in result.text
    plain = DocsReader(cfg(docs_mode="http", allowed_hosts=("127.0.0.1",))).read(js_site)
    assert plain.status == "blocked"  # without the browser the same page is unreadable


def _channel_works(channel: str) -> bool:
    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as p:
            p.chromium.launch(**({"channel": channel} if channel else {})).close()
        return True
    except Exception:
        return False


# -- MCP client --------------------------------------------------------------------------------------------------
def mcp_server(handler_log, *, sse=False, expire_once=False, status=None):
    """A mock MCP server over httpx: initialize, notifications/initialized, tools/call."""
    state = {"expired": False}

    def respond(payload, headers=None):
        if sse:
            return httpx.Response(200, text=f"event: message\ndata: {json.dumps(payload)}\n\n", headers={"content-type": "text/event-stream", **(headers or {})})
        return httpx.Response(200, json=payload, headers=headers)

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        handler_log.append((body.get("method"), dict(req.headers)))
        if status:
            return httpx.Response(status)
        if body["method"] == "initialize":
            return respond({"jsonrpc": "2.0", "id": body["id"], "result": {"protocolVersion": "2025-06-18", "capabilities": {}}}, {"mcp-session-id": "sess-1"})
        if body["method"] == "notifications/initialized":
            return httpx.Response(202)
        if expire_once and not state["expired"]:
            state["expired"] = True
            return httpx.Response(404)
        args = body["params"]["arguments"]
        if args.get("issueIdOrKey") == "BAD-1":
            return respond({"jsonrpc": "2.0", "id": body["id"], "result": {"isError": True, "content": [{"type": "text", "text": "no such issue"}]}})
        return respond({"jsonrpc": "2.0", "id": body["id"], "result": {"content": [{"type": "text", "text": json.dumps({"echo": args})}]}})

    return httpx.MockTransport(handler)


@pytest.mark.parametrize("sse", [False, True])
def test_mcp_client_handshake_session_and_headers(sse):
    log = []
    caller = McpHttpCaller("https://mcp.example.invalid/v1/mcp", "Bearer abc", transport=mcp_server(log, sse=sse))
    out = json.loads(caller("getJiraIssue", {"issueIdOrKey": "SUP-1"}))
    assert out == {"echo": {"issueIdOrKey": "SUP-1"}}
    assert [m for m, _ in log] == ["initialize", "notifications/initialized", "tools/call"]
    assert all(h["authorization"] == "Bearer abc" for _, h in log)
    assert log[2][1]["mcp-session-id"] == "sess-1" and log[2][1]["mcp-protocol-version"] == "2025-06-18"
    caller("getJiraIssue", {"issueIdOrKey": "SUP-2"})
    assert [m for m, _ in log].count("initialize") == 1  # the session is reused


def test_mcp_client_errors_and_session_recovery():
    with pytest.raises(McpError, match="refused the credentials") as e:
        McpHttpCaller("https://mcp.example.invalid", "Bearer no", transport=mcp_server([], status=401))("t", {})
    assert e.value.status == 401
    with pytest.raises(McpError, match="no such issue"):
        McpHttpCaller("https://mcp.example.invalid", transport=mcp_server([]))("getJiraIssue", {"issueIdOrKey": "BAD-1"})
    log = []
    caller = McpHttpCaller("https://mcp.example.invalid", transport=mcp_server(log, expire_once=True))
    assert json.loads(caller("t", {"a": 1})) == {"echo": {"a": 1}}  # session expired once, a new one was started
    assert [m for m, _ in log].count("initialize") == 2


def test_mcp_client_against_the_official_sdk_server():
    """The same client, a real MCP server built with the official SDK (skipped if the SDK is not installed)."""
    pytest.importorskip("mcp")
    from mcp_test_server import running_server
    with running_server() as url:
        reader = AtlassianReader(cfg(), McpHttpCaller(url, "Bearer test"))
        jira = reader.read("jira", "https://acme.atlassian.example.invalid/browse/SUP-48211", "SUP-48211")
        page_ = reader.read("confluence", "https://acme.atlassian.example.invalid/wiki/spaces/SUP/pages/20114/Sizing", None)
        missing = reader.read("jira", "https://acme.atlassian.example.invalid/browse/SUP-1", "SUP-1")
    assert jira.status == "ok" and "Fix versions: 5.9.0" in jira.lead and "Workaround: raise the request timeout" in jira.text
    assert page_.status == "ok" and page_.title == "Sizing archive scans on Core 5.x" and "one second per MB" in page_.text
    assert missing.status in ("not_found", "no_access", "error")  # never raises


# -- Atlassian reader -----------------------------------------------------------------------------------------------
ADF = {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Uploads fail after the 60 second timeout."}]}]}
JIRA = {"key": "SUP-48211", "fields": {"summary": "ICAP 500 on large uploads", "status": {"name": "Resolved"}, "resolution": {"name": "Fixed"},
                                       "fixVersions": [{"name": "5.9.0"}], "description": ADF,
                                       "comment": {"comments": [{"body": {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Raise it to 300s."}]}]}}]}}}


def test_jira_issue_is_read_through_mcp_with_the_right_arguments():
    calls = []

    def call(name, args):
        calls.append((name, args))
        return json.dumps(JIRA)

    r = AtlassianReader(cfg(), call).read("jira", "https://acme.atlassian.example.invalid/browse/SUP-48211", "SUP-48211")
    assert calls[0][0] == "getJiraIssue" and calls[0][1]["issueIdOrKey"] == "SUP-48211" and calls[0][1]["cloudId"] == "acme.atlassian.example.invalid"
    assert r.status == "ok" and r.title == "SUP-48211: ICAP 500 on large uploads"
    for needle in ("Status: Resolved", "Resolution: Fixed", "Fix versions: 5.9.0", "Uploads fail after the 60 second timeout.", "Raise it to 300s."):
        assert needle in r.lead + r.text
    assert r.lead == "Status: Resolved. Resolution: Fixed. Fix versions: 5.9.0."  # status and fix version are never trimmed away


def test_confluence_page_is_read_by_id_from_the_link():
    calls = []

    def call(name, args):
        calls.append((name, args))
        return json.dumps({"title": "Sizing", "body": "Allow one second per MB of archive."})

    r = AtlassianReader(cfg(atlassian_cloud_id="my-site"), call).read("confluence", "https://acme.atlassian.example.invalid/wiki/spaces/SUP/pages/20114/Sizing", None)
    assert calls == [("getConfluencePage", {"cloudId": "my-site", "pageId": "20114", "contentFormat": "markdown"})]
    assert r.status == "ok" and r.title == "Sizing" and "one second per MB" in r.text


def test_atlassian_reader_degrades_gracefully():
    assert AtlassianReader(cfg()).read("jira", "https://x/browse/SUP-1", "SUP-1").status == "no_access"  # not configured
    boom = lambda *_: (_ for _ in ()).throw(McpError("the MCP server refused the credentials (HTTP 403)", status=403))  # noqa: E731
    assert AtlassianReader(cfg(), boom).read("jira", "https://x/browse/SUP-1", "SUP-1").status == "no_access"
    gone = lambda *_: (_ for _ in ()).throw(McpError("Issue does not exist"))  # noqa: E731
    assert AtlassianReader(cfg(), gone).read("jira", "https://x/browse/SUP-1", "SUP-1").status == "not_found"
    odd = lambda *_: (_ for _ in ()).throw(RuntimeError("kaboom"))  # noqa: E731
    assert AtlassianReader(cfg(), odd).read("jira", "https://x/browse/SUP-1", "SUP-1").status == "error"
    assert AtlassianReader(cfg(), lambda *_: "{}").read("confluence", "https://x/wiki/overview", None).status == "skipped"  # no page id


# -- reviewer ---------------------------------------------------------------------------------------------------------
def reviewer_for(icap, routes, call_tool=None):
    docs, _ = docs_reader(routes)
    return ReferenceReviewer(cfg(), docs=docs, atlassian=AtlassianReader(cfg(), call_tool))


def test_reviewer_opens_each_source_type_with_the_right_reader(icap):
    routes = {DOC: (200, page(), {}), f"https://{HOST}/core/configuration/archive-handling": (404, "", {})}
    reviews = {r.url: r for r in reviewer_for(icap, routes, lambda n, a: json.dumps(JIRA) if n == "getJiraIssue" else json.dumps({"title": "T", "body": "Sizing rule of thumb text."})).review(icap.recommendations)}
    by_type = {}
    for r in reviews.values():
        by_type.setdefault(r.type, []).append(r)
    ok_doc = reviews[DOC]
    assert ok_doc.status == "ok" and "scan timeout setting" in ok_doc.excerpt and "mail servers" not in ok_doc.excerpt
    assert reviews[f"https://{HOST}/core/configuration/archive-handling"].status == "not_found"
    assert by_type[SourceType.jira][0].status == "ok" and "Resolved" in by_type[SourceType.jira][0].excerpt
    assert by_type[SourceType.confluence][0].status == "ok"
    assert by_type[SourceType.internal_kb][0].status == "skipped" and "no reader" in by_type[SourceType.internal_kb][0].note
    assert len(reviews) == 5  # every distinct source, once


def test_reviewer_survives_a_reader_that_explodes(icap):
    class Boom:
        def read(self, *a, **k):
            raise RuntimeError("kaboom")

    reviews = ReferenceReviewer(cfg(), docs=Boom(), atlassian=Boom()).review(icap.recommendations)
    assert reviews and all(r.status in ("error", "skipped") for r in reviews)


def test_make_reviewer_switches_and_fixture_mode_is_offline(icap):
    assert make_reviewer(load_reference_config({"REFERENCE_REVIEW": "off"})) is None
    reviews = make_reviewer(provider="fake").review(icap.recommendations)
    assert any(r.status == "ok" and r.excerpt for r in reviews) and any(r.status == "skipped" for r in reviews)
    assert make_reviewer(provider="fake").review(None) == []


# -- pipeline: reviews flow into the organise stage, triage, email and chat ------------------------------------------------
def rv(url, type_, status="ok", excerpt="", **kw):
    return SourceReview(url=url, type=type_, label=kw.pop("label", url), status=status, excerpt=excerpt, **kw)


def reviews_for(icap):
    return [
        rv(DOC, SourceType.public_doc, excerpt="The request timeout defines how long ICAP Server waits for a Core scan result. Raise it for archives.", title="Scan timeouts"),
        rv(f"https://{HOST}/core/configuration/archive-handling", SourceType.public_doc, "not_found", note="HTTP 404: the page does not exist"),
        rv("https://opswat-internal.atlassian.example.invalid/browse/SUP-48211", SourceType.jira, excerpt="Status: Resolved. Fix versions: 5.9.0. Raise the timeout to 300 seconds.", jira_key="SUP-48211"),
        rv("https://opswat-internal.atlassian.example.invalid/wiki/spaces/SUP/pages/20114", SourceType.confluence, "no_access", note="no access to this source"),
        rv("https://kb.internal.example.invalid/articles/icap-500-large-archives", SourceType.internal_kb, "skipped", note="no reader for internal_kb sources"),
    ]


def brief_json(icap, notes):
    raw = json.loads(icap.golden_summary.model_dump_json())
    raw.pop("plain_language")
    raw["recommendation_notes"] = notes
    return json.dumps(raw)


def test_organise_prompt_contains_what_the_sources_say_and_what_could_not_be_read(icap):
    llm = ScriptedLLM([brief_json(icap, []), icap.golden_summary.model_dump_json()])
    s = summarize(icap.transcript, llm, FAKE, icap.recommendations, reviews_for(icap))
    prompt = "\n".join(m.content for m in llm.calls[0][0])
    assert "OPENED. It says: The request timeout defines how long ICAP Server waits" in prompt
    assert "NOT FOUND (HTTP 404" in prompt and "could not be read (no_access" in prompt and "REFERENCE CONTENT" in prompt
    assert "<<<BEGIN UNTRUSTED RECOMMENDATIONS>>>" in prompt  # fetched text is data, never instructions
    assert [r.status for r in s.source_reviews] == [r.status for r in reviews_for(icap)]
    no_reviews = ScriptedLLM([brief_json(icap, []), icap.golden_summary.model_dump_json()])
    summarize(icap.transcript, no_reviews, FAKE, icap.recommendations)
    data = no_reviews.calls[0][0][1].content  # the user message holds the data; the system message holds the rules
    assert "OPENED. It says" not in data and "NOT FOUND" not in data  # no reviews, no review lines


def test_notes_can_only_claim_things_about_sources_that_were_opened(icap):
    notes = [RecNote(rec_id="rec_1", supported=True, gist="The doc says to raise the timeout. See SUP-48211 at https://x.example/y for more."),
             RecNote(rec_id="rec_2", supported=True, gist="Invented claim about a page that does not exist."),
             RecNote(rec_id="rec_3", supported=False, gist="Ticket says something.")]
    out = {n.rec_id: n for n in complete_notes(icap.recommendations, notes, 1, reviews_for(icap))}
    assert out["rec_1"].supported is True and "SUP-48211" not in out["rec_1"].gist and "http" not in out["rec_1"].gist  # no ids or links in a gist
    assert out["rec_2"].supported is None and out["rec_2"].gist == ""  # the page was not found: nothing can be claimed
    assert out["rec_3"].supported is False  # the Jira ticket was opened, so a verdict is allowed


def test_email_links_only_to_pages_that_exist_and_support_the_action(icap):
    notes = [RecNote(rec_id="rec_1"), RecNote(rec_id="rec_2"), RecNote(rec_id="rec_3")]
    kept = email_link_recs(icap.recommendations, notes, reviews_for(icap))
    urls = {s.url for s in kept.public_sources()}
    assert DOC in urls and f"https://{HOST}/core/configuration/archive-handling" not in urls  # dead link dropped

    unsupported = email_link_recs(icap.recommendations, [RecNote(rec_id="rec_1", supported=False)], reviews_for(icap))
    assert DOC not in {s.url for s in unsupported.public_sources()}  # the docs do not back the action: do not send the link

    moved = [rv(DOC, SourceType.public_doc, "redirected", "text " * 20, final_url=f"https://{HOST}/icap/configuration/new-page")]
    assert f"https://{HOST}/icap/configuration/new-page" in {s.url for s in email_link_recs(icap.recommendations, [], moved).public_sources()}
    assert email_link_recs(None, [], None) is None


def email_reply(body):
    return json.dumps({"subject": "Recap", "body": body})


def test_email_prompt_gets_gists_not_raw_source_text_and_no_dead_links(icap):
    summary = MeetingSummary(**icap.golden_summary.model_dump(), source_reviews=reviews_for(icap),
                             recommendation_notes=[RecNote(rec_id="rec_1", gist="The doc says to raise the request timeout for large archives."),
                                                   RecNote(rec_id="rec_2"), RecNote(rec_id="rec_3", gist="Raising the timeout fixed it in a later release.")])
    llm = ScriptedLLM([email_reply("Hi Dana,\n\nThank you for your time during the meeting.\n\nNext steps:\n1. Me: send the steps.")])
    draft_email(icap.transcript, summary, icap.style, icap.recommendations, llm)
    prompt = "\n".join(m.content for m in llm.calls[0][0])
    assert "what the checked sources say: The doc says to raise the request timeout" in prompt
    assert "Fix versions: 5.9.0" not in prompt and "The request timeout defines how long ICAP Server waits" not in prompt  # raw excerpts stay out
    assert "archive-handling" not in prompt and "SUP-48211" not in prompt and "atlassian" not in prompt
    assert DOC in prompt


def test_email_warns_about_links_that_could_not_be_checked(icap):
    blocked = rv(DOC, SourceType.public_doc, "blocked", note="HTTP 403: the site refused the request")
    summary = MeetingSummary(**icap.golden_summary.model_dump(), source_reviews=[blocked])
    body = f"Hi Dana,\n\nThank you for your time during the meeting.\n\nBackground: {DOC}"
    d = draft_email(icap.transcript, summary, icap.style, icap.recommendations, ScriptedLLM([email_reply(body)]))
    assert DOC in d.links_used and any("link not verified" in w and "HTTP 403" in w for w in d.warnings)
    clean = MeetingSummary(**icap.golden_summary.model_dump(), source_reviews=[rv(DOC, SourceType.public_doc, excerpt="text " * 20)])
    d2 = draft_email(icap.transcript, clean, icap.style, icap.recommendations, ScriptedLLM([email_reply(body)]))
    assert not any("link not verified" in w for w in d2.warnings)


def test_chat_context_shows_what_was_opened_and_what_was_not(icap):
    from callcopilot.modules.chatbot import ChatSession
    from callcopilot.shared.contracts import MeetingContext
    from callcopilot.modules.chatbot.tools import FixtureToolBackend
    summary = MeetingSummary(**icap.golden_summary.model_dump(), source_reviews=reviews_for(icap),
                             recommendation_notes=[RecNote(rec_id="rec_1", supported=True, gist="Docs back this.")])
    ctx = MeetingContext(transcript=icap.transcript, recommendations=icap.recommendations, summary=summary)
    prompt = ChatSession(ctx, ScriptedLLM(["x"]), FixtureToolBackend(), FAKE)._system_prompt()
    assert "Opened ICAP Server: configuring scan timeouts: The request timeout defines" in prompt
    assert "Checked against the opened sources: supported. Docs back this." in prompt
    assert "NOT_FOUND - HTTP 404" in prompt and "NO_ACCESS - no access to this source" in prompt
    assert "Source review" not in prompt  # (labels only; no stray template text)


# -- API and CLI -----------------------------------------------------------------------------------------------------------
class StubReviewer:
    def __init__(self, reviews):
        self.reviews = reviews

    def review(self, recs):
        return self.reviews


def test_api_summary_and_review_endpoint_carry_the_source_reviews(icap):
    from fastapi.testclient import TestClient

    import callcopilot.app.api as api
    stub = StubReviewer(reviews_for(icap))
    c = TestClient(api.create_app(FAKE, cors_origins=[], api_key="", reviewer=stub))
    ctx = c.get("/v1/scenarios/icap_upload_timeouts").json()
    s = c.post("/v1/summary", json={"transcript": ctx["transcript"], "recommendations": ctx["recommendations"]}).json()
    assert [r["status"] for r in s["source_reviews"]] == ["ok", "not_found", "ok", "no_access", "skipped"]
    listed = c.post("/v1/recommendations/review", json=ctx["recommendations"]).json()
    assert len(listed) == 5 and listed[0]["status"] == "ok" and listed[1]["note"].startswith("HTTP 404")

    c.put("/v1/meetings/m", json=ctx)  # the meeting flow keeps the reviews on the summary, so email and chat reuse them
    assert c.post("/v1/meetings/m/summary").json()["source_reviews"]
    email = c.post("/v1/meetings/m/email").json()
    assert not any("archive-handling" in link for link in email["links_used"])

    off = TestClient(api.create_app(FAKE, cors_origins=[], api_key="", reviewer=None))  # REFERENCE_REVIEW=off
    assert off.post("/v1/recommendations/review", json=ctx["recommendations"]).json() == []
    assert off.post("/v1/summary", json={"transcript": ctx["transcript"], "recommendations": ctx["recommendations"]}).json()["source_reviews"] == []


def test_cli_review_command(capsys, monkeypatch):
    from callcopilot.app.cli import main
    monkeypatch.setenv("REFERENCE_REVIEW", "fixtures")
    main(["review", "--scenario", "icap_upload_timeouts"])
    out = capsys.readouterr().out
    assert "status: ok" in out and "sources opened" in out and "excerpt:" in out
    monkeypatch.setenv("REFERENCE_REVIEW", "off")
    with pytest.raises(SystemExit, match="off"):
        main(["review", "--url", "https://www.opswat.com/docs/x"])
    monkeypatch.setenv("REFERENCE_REVIEW", "on")
    with pytest.raises(SystemExit, match="--url"):
        main(["review"])
