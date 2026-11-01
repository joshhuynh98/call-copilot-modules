"""Reads OPSWAT documentation through DeveloperHub's public JSON API (the platform the docs are hosted on).

The website refuses automated access, but the platform serves every page as JSON through a public, unauthenticated API meant
for programmatic use. A docs URL is resolved in three steps:

  1. project  GET {api}/project/www.opswat.com?path=/docs/<product>   versions and sections (large: cached per product)
  2. index    GET {api}/documentation/<section id>/index              the section's pages with slug and title (cached)
  3. page     GET {api}/page/<page id>                                 title and body (a structured JSON document)

URL shapes handled:  www.opswat.com/docs/<product>[/<version>][/<section>[/<page-slug>]]  and the old docs.opswat.com/<product>/...
Only the product slug (validated) and numeric ids are ever put into API URLs; page slugs are only compared, never interpolated.
Everything it returns is untrusted data, like any fetched page.
"""
from __future__ import annotations

import re
import threading
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from .config import ReferenceConfig
from .types import Fetched

PRODUCT_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
VERSION_RE = re.compile(r"^v?\d+(\.\d+)*$")
USER_AGENT = "CallCopilotReview/1.0 (support-tool docs review)"
MAX_TEXT_CHARS = 60_000

_BLOCKS = {"paragraph", "heading", "blockquote", "codeBlock", "code_block", "callout", "panel", "table", "tableRow", "table_row",
           "bulletList", "bullet_list", "orderedList", "ordered_list", "doc", "horizontalRule", "horizontal_rule"}
_ITEMS = {"listItem", "list_item"}
_CELLS = {"tableCell", "table_cell", "tableHeader", "table_header"}


# -- page body to text ----------------------------------------------------------------------------------------------
def _flat(node: Any) -> str:
    """All the text under a node on ONE line (used for list items and table cells)."""
    parts: list[str] = []

    def walk(n: Any) -> None:
        if isinstance(n, list):
            for x in n:
                walk(x)
        elif isinstance(n, dict):
            if n.get("type") == "text":
                parts.append(str(n.get("text", "")))
            elif n.get("type") in ("hardBreak", "hard_break"):
                parts.append(" ")
            else:
                walk(n.get("content"))
                if n.get("type") in _BLOCKS or n.get("type") in _ITEMS:
                    parts.append(" ")

    walk(node)
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def body_text(node: Any) -> str:
    """Readable text from a DeveloperHub page body (ProseMirror-style JSON): paragraphs, headings, lists, code, tables, callouts.
    A list item is one "- " line, a table row is one " | " separated line, so the structure survives as plain text."""
    out: list[str] = []

    def walk(n: Any) -> None:
        if isinstance(n, list):
            for x in n:
                walk(x)
            return
        if not isinstance(n, dict):
            return
        kind = n.get("type")
        if kind == "text":
            out.append(str(n.get("text", "")))
        elif kind in ("hardBreak", "hard_break"):
            out.append("\n")
        elif kind in ("bulletList", "bullet_list"):
            for item in n.get("content") or []:
                out.append("\n- " + _flat(item))
            out.append("\n")
        elif kind in ("orderedList", "ordered_list"):
            for i, item in enumerate(n.get("content") or [], 1):
                out.append(f"\n{i}. " + _flat(item))
            out.append("\n")
        elif kind == "table":
            for row in n.get("content") or []:
                cells = [_flat(c) for c in (row or {}).get("content") or []]
                out.append("\n" + " | ".join(cells))
            out.append("\n")
        elif kind in ("codeBlock", "code_block"):
            out.append("\n" + "".join(str(t.get("text", "")) for t in n.get("content") or [] if isinstance(t, dict)) + "\n")
        elif kind == "image":
            alt = (n.get("attrs") or {}).get("alt")
            if alt:
                out.append(f"[image: {alt}]")
        else:
            if kind in _BLOCKS:
                out.append("\n")
            walk(n.get("content"))
            if kind in _BLOCKS:
                out.append("\n")

    walk(node)
    text = "".join(out)
    lines = [re.sub(r"[ \t\xa0]+", " ", ln).strip() for ln in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _plugin_text(plugin: Any) -> str:
    if isinstance(plugin, dict) and plugin.get("type") == "table" and isinstance(plugin.get("contents"), list):
        rows = [r for r in plugin["contents"] if isinstance(r, list)]  # row-major: one line per row
        return "\n".join(" | ".join(_plugin_text(c) for c in row) for row in rows)
    if isinstance(plugin, str):
        return plugin
    if isinstance(plugin, list):
        return " | ".join(t for t in (_plugin_text(p) for p in plugin) if t)
    if isinstance(plugin, dict):
        parts = [_plugin_text(v) for k, v in plugin.items() if k not in ("id", "type", "widths", "cols", "src", "url", "width", "align", "language")]
        return "\n".join(p for p in parts if p)
    return ""


def legacy_content_text(content: str, plugins: list[dict]) -> str:
    """Older pages carry markdown in `content` with `$plugin[id]` placeholders for tables, code blocks and callouts."""
    by_id = {str(p.get("id")): p for p in plugins or []}
    return re.sub(r"\$plugin\[([^\]]+)\]", lambda m: "\n" + _plugin_text(by_id.get(m.group(1), "")) + "\n", content).strip()


def page_text(page: dict) -> str:
    text = body_text(page.get("body")) if page.get("body") else ""
    if not text and page.get("content"):
        text = legacy_content_text(page["content"], page.get("plugins") or [])
    return text[:MAX_TEXT_CHARS]


# -- the reader ------------------------------------------------------------------------------------------------------
class DeveloperHubReader:
    def __init__(self, cfg: ReferenceConfig, *, transport: httpx.BaseTransport | None = None):
        self.cfg = cfg
        self.api = cfg.docs_api.rstrip("/")
        self._http = httpx.Client(timeout=cfg.timeout_s, transport=transport, headers={"User-Agent": USER_AGENT})
        self._lock = threading.Lock()  # guards the caches
        self._fetch_lock = threading.Lock()  # one thread downloads a missing project/index; the others wait and reuse it
        self._projects: dict[str, tuple[float, dict]] = {}
        self._indexes: dict[int, tuple[float, list[dict]]] = {}

    # -- public --------------------------------------------------------------------------------------------------
    def parse(self, url: str) -> list[str] | None:
        """The path segments below `/docs` (product first), or None if this is not an OPSWAT docs URL we know how to resolve."""
        p = urlparse(url)
        host, parts = (p.hostname or "").lower(), [s for s in p.path.split("/") if s]
        if host == "www.opswat.com" and parts[:1] == ["docs"]:
            parts = parts[1:]
        elif host == "docs.opswat.com":
            pass
        else:
            return None
        return parts if parts and PRODUCT_RE.match(parts[0]) else None

    def read(self, url: str) -> Fetched | None:
        """None when the URL is not a docs address (the caller then uses another reader)."""
        segs = self.parse(url)
        if segs is None:
            return None
        try:
            return self._resolve(url, segs)
        except _ApiError as exc:
            return Fetched("error", note=str(exc))
        except httpx.TransportError as exc:
            return Fetched("error", note=f"could not reach the docs API ({type(exc).__name__})")

    # -- resolution ------------------------------------------------------------------------------------------------
    def _resolve(self, url: str, segs: list[str]) -> Fetched:
        product, rest = segs[0], segs[1:]
        project = self._project(product)
        if project is None:
            return Fetched("not_found", note=f"there is no '{product}' product in the OPSWAT docs")
        versions = project["versions"]
        version, requested_version = versions[0], None
        if rest and VERSION_RE.match(rest[0]):
            requested_version, rest = rest[0], rest[1:]
            match = next((v for v in versions if v["slug"].lstrip("v") == requested_version.lstrip("v")), None)
            if match is None:
                return Fetched("error", note=f"docs version {requested_version} of {product} is not served by the public docs API")
            version = match
        if not rest:  # product (or version) landing page
            return Fetched("ok", final_url=_clean(url), title=project["title"],
                           text=f"{project['title']} documentation, version {version['name']}. Sections: " + ", ".join(d["title"] for d in version["docs"]) + ".")
        section = next((d for d in version["docs"] if d["slug"] == rest[0]), None)
        if section is None:
            return Fetched("not_found", note=f"{project['title']} {version['name']} has no '{rest[0]}' section")
        pages = self._index(section["id"])
        if len(rest) == 1:  # section landing page
            titles = [p["title"] for p in pages if p.get("listed", True)][:40]
            return Fetched("ok", final_url=_clean(url), title=f"{project['title']}: {section['title']}",
                           text=f"{section['title']} section of {project['title']} {version['name']}. Pages: " + "; ".join(titles) + ".")
        slug = rest[-1]
        entry = next((p for p in pages if p["slug"] == slug), None)
        if entry is None:
            return Fetched("not_found", note=f"no page '{slug}' in {project['title']} {version['name']} / {section['title']}")
        page = self._get(f"page/{entry['id']}", missing_ok=True)
        if page is None:
            return Fetched("not_found", note="the page is listed but the docs API does not serve it")
        text = page_text(page)
        if not text:
            return Fetched("error", note="the page was served but has no readable text")
        return Fetched("ok", final_url=_clean(url), title=page.get("title") or entry["title"], text=text)

    def _project(self, product: str) -> dict | None:
        fresh = lambda hit: hit is not None and time.monotonic() - hit[0] < self.cfg.cache_ttl_s  # noqa: E731
        with self._lock:
            hit = self._projects.get(product)
        if fresh(hit):
            return hit[1]
        with self._fetch_lock:
            with self._lock:
                hit = self._projects.get(product)
            return hit[1] if fresh(hit) else self._load_project(product)

    def _load_project(self, product: str) -> dict | None:
        raw = self._get("project/www.opswat.com", params={"path": f"/docs/{product}"})
        base = str(raw.get("basepath") or "")
        if base.rstrip("/").split("/")[-1] != product:  # an unknown product silently falls back to another project: not a match
            data = None
        else:
            versions = sorted(raw.get("publicVersions") or [], key=lambda v: v.get("ordr", 0))  # lowest ordr = newest
            data = {"title": raw.get("title") or product, "versions": [
                {"slug": str(v.get("slug") or v.get("name")), "name": str(v.get("name") or v.get("slug")),
                 "docs": [{"id": d["id"], "slug": d["slug"], "title": d.get("title") or d["slug"]} for d in v.get("publicDocumentations") or []]}
                for v in versions]}
            if not data["versions"]:
                data = None
        with self._lock:
            self._projects[product] = (time.monotonic(), data)
        return data

    def _index(self, section_id: int) -> list[dict]:
        fresh = lambda hit: hit is not None and time.monotonic() - hit[0] < self.cfg.cache_ttl_s  # noqa: E731
        with self._lock:
            hit = self._indexes.get(section_id)
        if fresh(hit):
            return hit[1]
        with self._fetch_lock:
            with self._lock:
                hit = self._indexes.get(section_id)
            return hit[1] if fresh(hit) else self._load_index(section_id)

    def _load_index(self, section_id: int) -> list[dict]:
        raw = self._get(f"documentation/{int(section_id)}/index")
        items = raw if isinstance(raw, list) else raw.get("pages") or raw.get("index") or []
        pages = [e["page"] for e in items if isinstance(e, dict) and isinstance(e.get("page"), dict) and e["page"].get("id") and e["page"].get("slug")]
        with self._lock:
            self._indexes[section_id] = (time.monotonic(), pages)
        return pages

    def _get(self, endpoint: str, params: dict | None = None, missing_ok: bool = False) -> Any:
        r = self._http.get(f"{self.api}/{endpoint}", params=params)
        if r.status_code == 404 and missing_ok:
            return None
        if r.status_code in (401, 403, 429):
            raise _ApiError(f"the docs API refused the request (HTTP {r.status_code})")
        if r.status_code >= 400:
            raise _ApiError(f"the docs API returned HTTP {r.status_code}")
        try:
            return r.json()
        except ValueError as exc:
            raise _ApiError("the docs API returned something that is not JSON") from exc


class _ApiError(Exception):
    pass


def _clean(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}{p.path}"
