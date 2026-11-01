"""Reads internal Jira tickets and Confluence pages through an Atlassian MCP server (`getJiraIssue`, `getConfluencePage`).

If no MCP server is configured, or it refuses access, the source is reported as `no_access` and the pipeline carries on
without it. It never raises. Everything it returns is internal and untrusted data: it informs the agent and the model's
judgement, and is never copied into customer-facing text (ticket ids and internal links stay out of emails).
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

from .config import ReferenceConfig
from .mcp_client import McpError, McpHttpCaller
from .types import Fetched

JIRA_KEY_IN_URL = re.compile(r"/browse/([A-Z][A-Z0-9]+-\d+)")
CONFLUENCE_PAGE_ID = re.compile(r"/pages/(\d+)")
CONFLUENCE_TINY = re.compile(r"/wiki/x/([A-Za-z0-9_-]+)")
JIRA_FIELDS = ["summary", "description", "status", "resolution", "issuetype", "priority", "labels", "components", "fixVersions", "comment"]
NOT_CONFIGURED = "Atlassian MCP is not configured (set ATLASSIAN_MCP_URL and ATLASSIAN_MCP_AUTH), so this source was not opened"

CallTool = Callable[[str, dict], str]


def adf_text(node: Any) -> str:
    """Plain text out of Atlassian Document Format (or any nested JSON that carries 'text' leaves)."""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "\n".join(t for t in (adf_text(n) for n in node) if t)
    if isinstance(node, dict):
        if "text" in node and isinstance(node["text"], str):
            return node["text"]
        sep = "\n" if node.get("type") in ("paragraph", "heading", "listItem", "bulletList", "orderedList", "codeBlock", "tableRow", "doc") else ""
        return sep.join(t for t in (adf_text(v) for k, v in node.items() if k in ("content", "body", "value", "storage", "atlas_doc_format")) if t)
    return ""


def _loads(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return text


class AtlassianReader:
    def __init__(self, cfg: ReferenceConfig, call_tool: CallTool | None = None):
        self.cfg = cfg
        self._call = call_tool or (McpHttpCaller(cfg.atlassian_mcp_url, cfg.atlassian_mcp_auth, cfg.timeout_s) if cfg.atlassian_mcp_url else None)

    @property
    def configured(self) -> bool:
        return self._call is not None

    def read(self, source_type: str, url: str, jira_key: str | None = None) -> Fetched:
        if self._call is None:
            return Fetched("no_access", note=NOT_CONFIGURED)
        cloud = self.cfg.atlassian_cloud_id or (urlparse(url).hostname or "")
        try:
            if source_type == "jira" or jira_key:
                return self._jira(cloud, jira_key or (JIRA_KEY_IN_URL.search(url) or [None, None])[1], url)
            return self._confluence(cloud, url)
        except McpError as exc:
            return self._classify(exc)
        except Exception as exc:  # reading a source must never break the review
            return Fetched("error", note=f"unexpected {type(exc).__name__} while reading the source")

    # -- Jira ----------------------------------------------------------------------------------------------------
    def _jira(self, cloud: str, key: str | None, url: str) -> Fetched:
        if not key:
            return Fetched("skipped", note="no Jira ticket key found for this source")
        raw = self._call("getJiraIssue", {"cloudId": cloud, "issueIdOrKey": key, "fields": JIRA_FIELDS, "responseContentFormat": "markdown"})
        data = _loads(raw)
        fields = data.get("fields", data) if isinstance(data, dict) else {}
        if not isinstance(fields, dict):
            return Fetched("ok", final_url=url, title=key, text=str(raw))
        name = lambda v: v.get("name", "") if isinstance(v, dict) else (v or "")  # noqa: E731
        summary = adf_text(fields.get("summary"))
        facts = [f"Status: {name(fields.get('status'))}"]  # the lead: always kept when the excerpt is trimmed
        if fields.get("resolution"):
            facts.append(f"Resolution: {name(fields['resolution'])}")
        versions = [name(v) for v in fields.get("fixVersions") or []]
        if versions:
            facts.append("Fix versions: " + ", ".join(versions))
        parts = [f"Summary: {summary}"]
        if fields.get("description"):
            parts.append("Description:\n" + adf_text(fields["description"]))
        comments = (fields.get("comment") or {}).get("comments", []) if isinstance(fields.get("comment"), dict) else []
        for c in comments[-5:]:
            parts.append("Comment: " + adf_text(c.get("body")))
        return Fetched("ok", final_url=url, title=f"{key}: {summary}".strip(": "), text="\n".join(p for p in parts if p.strip()),
                       lead=". ".join(facts) + ".")

    # -- Confluence ------------------------------------------------------------------------------------------------
    def _confluence(self, cloud: str, url: str) -> Fetched:
        m = CONFLUENCE_PAGE_ID.search(url) or CONFLUENCE_TINY.search(url)
        if not m:
            return Fetched("skipped", note="no Confluence page id found in this link")
        raw = self._call("getConfluencePage", {"cloudId": cloud, "pageId": m.group(1), "contentFormat": "markdown"})
        data = _loads(raw)
        if isinstance(data, dict):
            title = data.get("title") or None
            body = adf_text(data.get("body") if "body" in data else data)
        else:
            title, body = None, str(raw)
        return Fetched("ok", final_url=url, title=title, text=body)

    # -- errors -------------------------------------------------------------------------------------------------------
    @staticmethod
    def _classify(exc: McpError) -> Fetched:
        msg = str(exc)
        low = msg.lower()
        if exc.status in (401, 403) or any(w in low for w in ("permission", "unauthor", "forbidden", "not allowed", "credentials", "access")):
            return Fetched("no_access", note=f"no access to this source ({msg[:100]})")
        if exc.status == 404 or any(w in low for w in ("not found", "does not exist", "no issue", "404")):
            return Fetched("not_found", note="the ticket or page does not exist")
        return Fetched("error", note=f"Atlassian MCP error: {msg[:120]}")
