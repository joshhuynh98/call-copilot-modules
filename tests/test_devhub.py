"""Reading OPSWAT docs through the docs platform's public JSON API (offline, with a fake API)."""
import json
import os
import re

import httpx
import pytest

from callcopilot.shared.contracts import Recommendation, RecommendationSet, Source, SourceType
from callcopilot.shared.references import ReferenceReviewer
from callcopilot.shared.references.config import ReferenceConfig
from callcopilot.shared.references.devhub import DeveloperHubReader, body_text, legacy_content_text, page_text
from callcopilot.shared.references.docs import DocsReader

API = "https://api.example-docs.invalid/api/public"
LATEST = "https://www.opswat.com/docs/mdicap/configuration/additional-tuning"


def t(text, *marks):
    node = {"type": "text", "text": text}
    return {**node, "marks": list(marks)} if marks else node


def para(*nodes):
    return {"type": "paragraph", "content": list(nodes)}


def cell(text):
    return {"type": "tableCell", "content": [para(t(text))]}


BODY = {"type": "doc", "content": [
    {"type": "heading", "attrs": {"level": 2}, "content": [t("Risk of negative performance impact")]},
    para(t("Changing tunables may affect performance. See "), t("Other settings", {"type": "link", "attrs": {"href": "/configuration/other-settings"}}), t(".")),
    {"type": "bulletList", "content": [
        {"type": "listItem", "content": [para(t("[internal] section in the config file on Linux")), {"type": "bulletList", "content": [{"type": "listItem", "content": [para(t("nested point"))]}]}]},
        {"type": "listItem", "content": [para(t("Registry key on Windows"))]}]},
    {"type": "orderedList", "content": [{"type": "listItem", "content": [para(t("First step"))]}, {"type": "listItem", "content": [para(t("Second step"))]}]},
    {"type": "table", "content": [
        {"type": "tableRow", "content": [{"type": "tableHeader", "content": [para(t("parameter"))]}, {"type": "tableHeader", "content": [para(t("default"))]}, {"type": "tableHeader", "content": [para(t("description"))]}]},
        {"type": "tableRow", "content": [cell("polling_retry"), cell("0 (disabled)"), cell("Number of retries in case of errors during polling the scan results.")]},
        {"type": "tableRow", "content": [cell("polling_sleep"), cell("20"), {"type": "tableCell", "content": [para(t("Wait between polling attempts.")), para(t("Do not set to 0."))]}]}]},
    {"type": "codeBlock", "content": [t("[internal]\npolling_retry = 3")]},
    {"type": "image", "attrs": {"src": "https://uploads.example.invalid/a.png", "width": 0}},
]}

PROJECT = {"title": "MetaDefender ICAP Server", "basepath": "docs/mdicap", "publicVersions": [
    {"slug": "v5.14.1", "name": "v5.14.1", "ordr": 2, "publicDocumentations": [{"id": 11, "slug": "configuration", "title": "Configuration"}]},
    {"slug": "v5.16.1", "name": "v5.16.1", "ordr": 1, "publicDocumentations": [
        {"id": 21, "slug": "configuration", "title": "Configuration"}, {"id": 22, "slug": "installation", "title": "Installation"}]},
]}
INDEXES = {
    21: [{"type": "page", "page": {"id": 901, "slug": "additional-tuning", "title": "Additional Tuning", "listed": True}},
         {"type": "page", "page": {"id": 902, "slug": "other-settings", "title": "Other settings", "listed": True}},
         {"type": "page", "page": {"id": 903, "slug": "legacy-page", "title": "Legacy page", "listed": True}},
         {"type": "category", "page": None, "title": "A folder"}],
    11: [{"type": "page", "page": {"id": 801, "slug": "other-settings", "title": "Other settings (v5.14.1)", "listed": True}}],
    22: [],
}
PAGES = {
    901: {"id": 901, "title": "Additional Tuning", "body": BODY},
    902: {"id": 902, "title": "Other settings", "body": {"type": "doc", "content": [para(t("Global settings are under Settings, System."))]}},
    903: {"id": 903, "title": "Legacy page", "body": None, "content": "Intro text.\n$plugin[7]\nOutro text.",
          "plugins": [{"id": 7, "type": "table", "contents": [["name", "value"], ["timeout", "60"]]}]},
    801: {"id": 801, "title": "Other settings (v5.14.1)", "body": {"type": "doc", "content": [para(t("Older global settings text."))]}},
}


def fake_api(calls=None, *, fail=None, other_project=None):
    """An httpx transport that behaves like the docs platform's public API."""
    def handler(req: httpx.Request) -> httpx.Response:
        url = req.url
        if calls is not None:
            calls.append(str(url))
        if fail:
            return fail(req) if callable(fail) else httpx.Response(fail)
        path = url.path.removeprefix("/api/public/")
        if path == "project/www.opswat.com":
            product = url.params.get("path", "").rsplit("/", 1)[-1]
            if product == "mdicap":
                return httpx.Response(200, json=PROJECT)
            return httpx.Response(200, json=other_project or {"title": "MetaDefender Core", "basepath": "docs/mdcore", "publicVersions": []})  # unknown product: falls back to another project
        if m := re.fullmatch(r"documentation/(\d+)/index", path):
            return httpx.Response(200, json=INDEXES.get(int(m.group(1))) if int(m.group(1)) in INDEXES else None) if int(m.group(1)) in INDEXES else httpx.Response(404)
        if m := re.fullmatch(r"page/(\d+)", path):
            page = PAGES.get(int(m.group(1)))
            return httpx.Response(200, json=page) if page else httpx.Response(404)
        return httpx.Response(404)
    return httpx.MockTransport(handler)


def cfg(**kw):
    return ReferenceConfig(**{**dict(mode="on", docs_api=API, allowed_hosts=("www.opswat.com", "docs.opswat.com"), timeout_s=5.0, excerpt_chars=500), **kw})


def reader(calls=None, **kw):
    return DeveloperHubReader(cfg(), transport=fake_api(calls, **kw))


# -- page body to text ---------------------------------------------------------------------------------------------
def test_body_text_keeps_the_structure_as_plain_text():
    text = body_text(BODY)
    assert "Risk of negative performance impact" in text
    assert "See Other settings." in text  # link text kept, markup dropped
    assert "- [internal] section in the config file on Linux nested point" in text and "- Registry key on Windows" in text  # one line per item
    assert "1. First step" in text and "2. Second step" in text
    assert "parameter | default | description" in text
    assert "polling_retry | 0 (disabled) | Number of retries in case of errors during polling the scan results." in text  # a table row is one line
    assert "polling_sleep | 20 | Wait between polling attempts. Do not set to 0." in text  # multi-paragraph cell flattened
    assert "[internal]\npolling_retry = 3" in text  # code keeps its lines
    assert "type" not in text and "uploads.example" not in text and "\n\n\n" not in text


def test_legacy_pages_use_content_and_plugins():
    text = page_text(PAGES[903])
    assert text.startswith("Intro text.") and text.endswith("Outro text.")
    assert "name | value\ntimeout | 60" in text  # a legacy table keeps one line per row
    assert legacy_content_text("A $plugin[9] B", [{"id": 9, "type": "callout", "title": "Note", "text": "Careful"}]) == "A \nNote\nCareful\n B"
    assert page_text({"body": None, "content": ""}) == ""


# -- URL resolution --------------------------------------------------------------------------------------------------
def test_a_latest_page_is_resolved_in_three_calls_and_read():
    calls = []
    r = reader(calls).read(LATEST)
    assert r.status == "ok" and r.title == "Additional Tuning" and r.final_url == LATEST and "polling_retry" in r.text
    assert [re.sub(r"https://api.example-docs.invalid/api/public/", "", c) for c in calls] == [
        "project/www.opswat.com?path=%2Fdocs%2Fmdicap", "documentation/21/index", "page/901"]


@pytest.mark.parametrize("url,title", [
    ("https://www.opswat.com/docs/mdicap/v5.14.1/configuration/other-settings", "Other settings (v5.14.1)"),  # a named version
    ("https://www.opswat.com/docs/mdicap/v5.16.1/configuration/other-settings", "Other settings"),
    ("https://www.opswat.com/docs/mdicap/configuration/other-settings", "Other settings"),  # no version = the newest
    ("https://docs.opswat.com/mdicap/configuration/additional-tuning", "Additional Tuning"),  # the old docs host
    ("https://www.opswat.com/docs/mdicap/configuration/additional-tuning#polling?x=1", "Additional Tuning"),  # fragment and query ignored
])
def test_url_shapes(url, title):
    assert reader().read(url).title == title


def test_landing_pages_describe_what_is_there():
    r = reader()
    product = r.read("https://www.opswat.com/docs/mdicap")
    assert product.status == "ok" and "v5.16.1" in product.text and "Configuration, Installation" in product.text
    assert "v5.14.1" in r.read("https://www.opswat.com/docs/mdicap/v5.14.1").text
    section = r.read("https://www.opswat.com/docs/mdicap/configuration")
    assert section.status == "ok" and "Additional Tuning; Other settings; Legacy page" in section.text and "A folder" not in section.text


def test_definitive_misses_are_not_found():
    r = reader()
    assert r.read("https://www.opswat.com/docs/mdicap/configuration/no-such-page").status == "not_found"
    assert r.read("https://www.opswat.com/docs/mdicap/no-such-section/additional-tuning").status == "not_found"
    unknown = r.read("https://www.opswat.com/docs/zzzz/configuration/x")  # the API answers with ANOTHER project: that must not match
    assert unknown.status == "not_found" and "zzzz" in unknown.note


def test_a_version_the_api_does_not_serve_is_an_error_not_a_dead_link():
    r = reader().read("https://www.opswat.com/docs/mdicap/v1.0.0/configuration/additional-tuning")
    assert r.status == "error" and "v1.0.0" in r.note  # the website may still have it, so do not call the link dead


def test_urls_that_are_not_docs_addresses_are_left_to_other_readers():
    r = reader()
    for url in ("https://www.opswat.com/blog/x", "https://example.invalid/docs/mdicap/configuration/a", "https://www.opswat.com/docs", "https://www.opswat.com/docs/../etc/passwd",
                "https://www.opswat.com/docs/MDICAP/x", "https://www.opswat.com/docs/md icap/x"):
        assert r.read(url) is None, url


def test_page_slugs_are_only_compared_never_put_into_api_urls():
    calls = []
    r = reader(calls).read("https://www.opswat.com/docs/mdicap/configuration/..%2F..%2Fsecret%3Fx%3D1")
    assert r.status == "not_found"
    assert not any("secret" in c or ".." in c for c in calls)
    assert all(c.startswith(API) for c in calls)  # nothing but the configured API host is ever contacted


# -- failures --------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("fail,expect", [(500, "HTTP 500"), (429, "refused"), (403, "refused")])
def test_api_failures_are_errors_with_a_reason(fail, expect):
    r = reader(fail=fail).read(LATEST)
    assert r.status == "error" and expect in r.note


def test_network_failure_and_garbage_are_errors():
    def boom(req):
        raise httpx.ConnectError("refused")

    assert reader(fail=boom).read(LATEST).status == "error"
    assert reader(fail=lambda req: httpx.Response(200, text="<html>not json</html>")).read(LATEST).status == "error"


def test_a_page_listed_in_the_index_but_missing_is_not_found_and_an_empty_one_is_an_error():
    PAGES[999] = {"id": 999, "title": "Empty", "body": {"type": "doc", "content": []}}
    INDEXES[21].append({"type": "page", "page": {"id": 998, "slug": "ghost", "title": "Ghost", "listed": True}})
    INDEXES[21].append({"type": "page", "page": {"id": 999, "slug": "empty", "title": "Empty", "listed": True}})
    try:
        r = reader()
        assert r.read("https://www.opswat.com/docs/mdicap/configuration/ghost").status == "not_found"
        assert r.read("https://www.opswat.com/docs/mdicap/configuration/empty").status == "error"
    finally:
        del PAGES[999]
        del INDEXES[21][-2:]


def test_project_and_index_are_cached_across_pages():
    calls = []
    r = reader(calls)
    r.read(LATEST)
    r.read("https://www.opswat.com/docs/mdicap/configuration/other-settings")
    r.read("https://www.opswat.com/docs/mdicap/configuration/legacy-page")
    kinds = [c.split("/api/public/")[1].split("?")[0].split("/")[0] for c in calls]
    assert kinds.count("project") == 1 and kinds.count("documentation") == 1 and kinds.count("page") == 3


# -- inside the docs reader: the API first, then HTTP, then a browser ------------------------------------------------------
def both(api_calls, web_calls, *, api_fail=None, web_html="<html><body>" + "Readable page text. " * 30 + "</body></html>"):
    """One transport that answers the docs API and the website."""
    api = fake_api(api_calls, fail=api_fail)

    def handler(req):
        if req.url.host == "api.example-docs.invalid":
            return api.handle_request(req)
        web_calls.append(str(req.url))
        return httpx.Response(200, text=web_html)

    return httpx.MockTransport(handler)


def test_docs_reader_uses_the_api_first_and_never_touches_the_website():
    api_calls, web_calls = [], []
    r = DocsReader(cfg(docs_mode="auto"), transport=both(api_calls, web_calls)).read(LATEST)
    assert r.status == "ok" and "polling_retry" in r.text and api_calls and web_calls == []


def test_docs_reader_falls_back_to_plain_http_when_the_api_cannot_answer():
    api_calls, web_calls = [], []
    r = DocsReader(cfg(docs_mode="auto"), transport=both(api_calls, web_calls, api_fail=500)).read(LATEST)
    assert r.status == "ok" and "Readable page text" in r.text and web_calls == [LATEST]


def test_a_definitive_not_found_from_the_api_is_final():
    api_calls, web_calls = [], []
    r = DocsReader(cfg(docs_mode="auto"), transport=both(api_calls, web_calls)).read("https://www.opswat.com/docs/mdicap/configuration/no-such-page")
    assert r.status == "not_found" and web_calls == []  # no second opinion from the website


def test_docs_reader_modes():
    api_calls, web_calls = [], []
    DocsReader(cfg(docs_mode="http"), transport=both(api_calls, web_calls)).read(LATEST)
    assert api_calls == [] and web_calls == [LATEST]  # http mode never calls the API
    api_calls, web_calls = [], []
    only_api = DocsReader(cfg(docs_mode="devhub"), transport=both(api_calls, web_calls))
    assert only_api.read(LATEST).status == "ok" and web_calls == []
    assert only_api.read("https://www.opswat.com/blog/x").status == "skipped" and web_calls == []  # not a docs address: no fallback in this mode


def test_results_are_cached_by_the_docs_reader():
    api_calls, web_calls = [], []
    r = DocsReader(cfg(docs_mode="auto"), transport=both(api_calls, web_calls))
    r.read(LATEST)
    n = len(api_calls)
    r.read(LATEST)
    assert len(api_calls) == n


# -- the whole review step on a docs link --------------------------------------------------------------------------------
def test_the_review_keeps_the_table_row_that_matters_for_the_recommendation():
    recs = RecommendationSet(case_id="C", recommendations=[Recommendation(
        id="r1", rank=1, title="Enable polling retries", action="Set polling_retry above 0 so ICAP retries failed polls of Core.",
        rationale="Under load Core errors on polling; retries help.", trigger_segment_ids=[],
        sources=[Source(type=SourceType.public_doc, label="Additional Tuning", url=LATEST)])])
    docs = DocsReader(cfg(docs_mode="auto", excerpt_chars=300), transport=fake_api())
    review = ReferenceReviewer(cfg(excerpt_chars=300), docs=docs).review(recs)[0]
    assert review.status == "ok" and review.title == "Additional Tuning"
    assert "polling_retry | 0 (disabled)" in review.excerpt  # the relevant row, not the nav or the code block
    assert len(review.excerpt) <= 320


# -- live (opt in): the real docs through the real API --------------------------------------------------------------------
@pytest.mark.skipif(os.environ.get("RUN_LIVE_DOCS") != "1", reason="set RUN_LIVE_DOCS=1 to read the real OPSWAT docs (network)")
def test_live_docs_through_the_public_api():
    live = DeveloperHubReader(ReferenceConfig(mode="on"))
    page = live.read("https://www.opswat.com/docs/mdicap/configuration/additional-tuning")
    assert page.status == "ok" and page.title == "Additional Tuning" and "polling_sleep" in page.text and "polling_retry" in page.text
    assert live.read("https://docs.opswat.com/mdicap/configuration/additional-tuning").title == "Additional Tuning"
    assert "MetaDefender Core" in live.read("https://www.opswat.com/docs/mdcore").text
    assert live.read("https://www.opswat.com/docs/mdicap/configuration/this-page-does-not-exist-xyz").status == "not_found"
    assert live.read("https://www.opswat.com/docs/zzzz/configuration/x").status == "not_found"
    assert live.read("https://www.opswat.com/docs/mdcore/configuration/password-recovery").status == "ok"


def test_parallel_first_reads_download_each_project_and_index_only_once():
    """The reviewer opens sources in parallel; the big project lookup must not be fetched once per thread."""
    import time
    from concurrent.futures import ThreadPoolExecutor
    calls = []
    inner = fake_api(calls)

    def slow(req):
        time.sleep(0.05)  # widen the window in which several threads miss the cache together
        return inner.handle_request(req)

    r = DeveloperHubReader(cfg(), transport=httpx.MockTransport(slow))
    urls = [f"https://www.opswat.com/docs/mdicap/configuration/{s}" for s in ("additional-tuning", "other-settings", "legacy-page")] * 3
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(r.read, urls))
    assert all(x.status == "ok" for x in results)
    kinds = [c.split("/api/public/")[1].split("?")[0].split("/")[0] for c in calls]
    assert kinds.count("project") == 1 and kinds.count("documentation") == 1, kinds
