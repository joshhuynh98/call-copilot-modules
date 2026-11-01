"""Opens public documentation pages (docs.opswat.com / www.opswat.com/docs) and returns their readable text.

Order in `auto` mode: (1) the docs platform's public JSON API (devhub.py), which the website's bot protection does not block;
(2) plain HTTP; (3) a real browser through Playwright (optional dependency), for pages that need JavaScript. A definitive answer
from the API ("opened" or "no such page") is final; any other outcome falls through to the next step.
`REFERENCE_DOCS_MODE=devhub|http|browser` forces one step.

Safety: only https URLs on `REFERENCE_ALLOWED_HOSTS` are ever requested, and every redirect hop is re-checked, so a bad
link in a recommendation cannot make the server fetch internal addresses (SSRF). Content is untrusted data.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from urllib.parse import urljoin, urlparse

import httpx

from .config import ReferenceConfig
from .devhub import DeveloperHubReader
from .html_text import extract_page
from .types import Fetched

MIN_TEXT_CHARS = 200  # less than this and the page is probably a JavaScript shell
MAX_REDIRECTS = 5
USER_AGENT = "Mozilla/5.0 (compatible; CallCopilotReview/1.0; support-tool link review)"
REDIRECTS = {301, 302, 303, 307, 308}

BrowserFetch = Callable[[str], tuple[int, str, str]]  # url -> (status, final_url, html)


class BrowserUnavailable(Exception):
    pass


def _same(a: str, b: str) -> bool:
    pa, pb = urlparse(a), urlparse(b)
    return (pa.scheme, pa.netloc.lower(), pa.path.rstrip("/"), pa.query) == (pb.scheme, pb.netloc.lower(), pb.path.rstrip("/"), pb.query)


class DocsReader:
    def __init__(self, cfg: ReferenceConfig, *, transport: httpx.BaseTransport | None = None, browser_fetch: BrowserFetch | None = None):
        self.cfg = cfg
        self._http = httpx.Client(timeout=cfg.timeout_s, transport=transport, follow_redirects=False,
                                  headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
        self._devhub = DeveloperHubReader(cfg, transport=transport)
        self._browser_fetch = browser_fetch or self._playwright_fetch
        self._browser_lock = threading.Lock()  # Playwright's sync API is not thread-safe
        self._cache: dict[str, tuple[float, Fetched]] = {}
        self._cache_lock = threading.Lock()

    # -- public ----------------------------------------------------------------------------------------
    def allowed(self, url: str) -> bool:
        p = urlparse(url)
        return p.scheme == "https" and (p.hostname or "").lower() in self.cfg.allowed_hosts

    def read(self, url: str) -> Fetched:
        if not self.allowed(url):
            host = urlparse(url).hostname or "?"
            return Fetched("skipped", note=f"not opened: {host} is not on the allowed host list (REFERENCE_ALLOWED_HOSTS) or the link is not https")
        cached = self._cached(url)
        if cached:
            return cached
        mode = self.cfg.docs_mode
        if mode in ("auto", "devhub"):
            via_api = self._devhub.read(url)  # None: not an OPSWAT docs address
            if via_api is not None and (via_api.status in ("ok", "not_found") or mode == "devhub"):
                self._remember(url, via_api)
                return via_api
            if mode == "devhub":
                return Fetched("skipped", note="not an OPSWAT docs address the docs API can resolve")
        result = self._read_http(url) if mode in ("http", "auto") else None
        if mode == "browser" or (mode == "auto" and result is not None and result.status == "blocked"):
            browsed = self._read_browser(url)
            result = browsed if browsed.status != "error" or result is None else _with_note(result, browsed.note)
        assert result is not None
        self._remember(url, result)
        return result

    def _remember(self, url: str, result: Fetched) -> None:
        if result.status in ("ok", "redirected", "not_found"):
            with self._cache_lock:
                self._cache[url] = (time.monotonic(), result)

    # -- plain HTTP ----------------------------------------------------------------------------------------
    def _read_http(self, url: str) -> Fetched:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            try:
                r = self._http.get(current)
            except httpx.TransportError as exc:
                return Fetched("error", note=f"could not connect ({type(exc).__name__})")
            if r.status_code in REDIRECTS:
                target = urljoin(current, r.headers.get("location", ""))
                if not r.headers.get("location") or not self.allowed(target):
                    return Fetched("blocked", note=f"redirects to a host that is not allowed: {urlparse(target).hostname}")
                current = target
                continue
            return self._from_response(url, current, r.status_code, r.text)
        return Fetched("error", note="too many redirects")

    # -- browser ---------------------------------------------------------------------------------------------
    def _read_browser(self, url: str) -> Fetched:
        try:
            status, final, html = self._browser_fetch(url)
        except BrowserUnavailable as exc:
            return Fetched("error", note=f"browser not available: {exc}")
        except Exception as exc:  # a failed page load must not break the review
            return Fetched("error", note=f"browser fetch failed ({type(exc).__name__})")
        if not self.allowed(final):
            return Fetched("blocked", note=f"ended on a host that is not allowed: {urlparse(final).hostname}")
        return self._from_response(url, final, status, html)

    def _playwright_fetch(self, url: str) -> tuple[int, str, str]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise BrowserUnavailable("Playwright is not installed (pip install playwright)") from exc
        with self._browser_lock, sync_playwright() as p:
            launch: dict = {"headless": True}
            if self.cfg.browser_channel:
                launch["channel"] = self.cfg.browser_channel
            try:
                browser = p.chromium.launch(**launch)
            except Exception as exc:
                raise BrowserUnavailable(f"cannot start the browser ({type(exc).__name__}); run `playwright install chromium` "
                                         "or set REFERENCE_BROWSER_CHANNEL=msedge") from exc
            try:
                page = browser.new_page()

                def guard(route):  # never navigate to a host that is not allowed, even through a redirect
                    req = route.request
                    if req.is_navigation_request() and not self.allowed(req.url):
                        return route.abort()
                    return route.continue_()

                page.route("**/*", guard)
                resp = page.goto(url, wait_until="networkidle", timeout=int(self.cfg.timeout_s * 1000))
                return (resp.status if resp else 200), page.url, page.content()
            finally:
                browser.close()

    # -- shared ------------------------------------------------------------------------------------------------
    def _from_response(self, requested: str, final: str, code: int, html: str) -> Fetched:
        if code in (404, 410):
            return Fetched("not_found", final_url=final, note=f"HTTP {code}: the page does not exist")
        if code in (401, 403, 429):
            return Fetched("blocked", final_url=final, note=f"HTTP {code}: the site refused the request")
        if code >= 400:
            return Fetched("error", final_url=final, note=f"HTTP {code}")
        title, text = extract_page(html)
        if len(text) < MIN_TEXT_CHARS:
            return Fetched("blocked", final_url=final, title=title or None, note="the page shows no readable text without JavaScript")
        return Fetched("ok" if _same(requested, final) else "redirected", final_url=final, title=title or None, text=text)

    def _cached(self, url: str) -> Fetched | None:
        with self._cache_lock:
            hit = self._cache.get(url)
        if hit and time.monotonic() - hit[0] < self.cfg.cache_ttl_s:
            return hit[1]
        return None


def _with_note(result: Fetched, extra: str) -> Fetched:
    return Fetched(result.status, result.final_url, result.title, result.text, f"{result.note}; {extra}".strip("; "))
