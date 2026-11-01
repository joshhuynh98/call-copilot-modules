"""A small MCP client over Streamable HTTP (JSON-RPC 2.0), enough to call tools such as Atlassian's `getJiraIssue`.

Written directly on httpx so it does not depend on one version of the MCP SDK. One session per client, created on first use
and re-created if the server forgets it. Not verified against Atlassian's live server from this repository (it needs a real
credential); it is verified against a local MCP server built with the official SDK (see tests/test_references.py).
"""
from __future__ import annotations

import json
import threading
from typing import Any

import httpx

PROTOCOL_VERSION = "2025-06-18"


class McpError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _sse_messages(text: str) -> list[dict]:
    """Parse a Server-Sent-Events body into JSON-RPC messages."""
    messages, data = [], []
    for line in text.splitlines() + [""]:
        if line.startswith("data:"):
            data.append(line[5:].strip())
        elif not line.strip() and data:
            try:
                messages.append(json.loads("\n".join(data)))
            except ValueError:
                pass
            data = []
    return messages


class McpHttpCaller:
    """Callable `(tool_name, arguments) -> text`. Raises McpError on protocol, auth or tool errors."""

    def __init__(self, url: str, auth_header: str = "", timeout: float = 20.0, transport: httpx.BaseTransport | None = None):
        self.url, self.auth_header = url, auth_header
        self._http = httpx.Client(timeout=timeout, transport=transport)
        self._lock = threading.Lock()
        self._next_id = 1
        self._session_id: str | None = None
        self._protocol: str | None = None

    def __call__(self, name: str, arguments: dict[str, Any]) -> str:
        self._ensure_session()
        try:
            result = self._rpc("tools/call", {"name": name, "arguments": arguments})
        except McpError as exc:
            if exc.status != 404:  # 404 on an existing session = the server dropped it: start a fresh one once
                raise
            self._reset()
            self._ensure_session()
            result = self._rpc("tools/call", {"name": name, "arguments": arguments})
        text = "\n".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")
        if result.get("isError"):
            raise McpError(text or "the tool reported an error")
        return text

    # -- session ----------------------------------------------------------------------------------------------
    def _reset(self) -> None:
        with self._lock:
            self._session_id = self._protocol = None

    def _ensure_session(self) -> None:
        with self._lock:
            if self._protocol is not None:
                return
        init = self._rpc("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                                        "clientInfo": {"name": "callcopilot", "version": "0.1"}}, initializing=True)
        with self._lock:
            self._protocol = init.get("protocolVersion") or PROTOCOL_VERSION
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, want_response=False)

    def _headers(self, initializing: bool = False) -> dict[str, str]:
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self.auth_header:
            h["Authorization"] = self.auth_header
        if self._session_id:
            h["Mcp-Session-Id"] = self._session_id
        if self._protocol and not initializing:
            h["MCP-Protocol-Version"] = self._protocol
        return h

    def _rpc(self, method: str, params: dict, initializing: bool = False) -> dict:
        with self._lock:
            rid = self._next_id
            self._next_id += 1
        message = self._post({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}, want_response=True,
                             initializing=initializing, rid=rid)
        if "error" in message:
            raise McpError(str((message["error"] or {}).get("message", "MCP error")))
        return message.get("result") or {}

    def _post(self, payload: dict, *, want_response: bool, initializing: bool = False, rid: int | None = None) -> dict:
        try:
            r = self._http.post(self.url, json=payload, headers=self._headers(initializing))
        except httpx.TransportError as exc:
            raise McpError(f"cannot reach the MCP server ({type(exc).__name__})") from exc
        if r.status_code in (401, 403):
            raise McpError(f"the MCP server refused the credentials (HTTP {r.status_code})", status=r.status_code)
        if r.status_code == 404 and self._session_id:
            raise McpError("MCP session expired", status=404)
        if r.status_code >= 400:
            raise McpError(f"the MCP server returned HTTP {r.status_code}", status=r.status_code)
        if initializing and r.headers.get("mcp-session-id"):
            with self._lock:
                self._session_id = r.headers["mcp-session-id"]
        if not want_response:
            return {}
        ctype = r.headers.get("content-type", "")
        messages = _sse_messages(r.text) if "text/event-stream" in ctype else [r.json()]
        for m in messages:
            if m.get("id") == rid and ("result" in m or "error" in m):
                return m
        raise McpError("the MCP server sent no answer to the request")
