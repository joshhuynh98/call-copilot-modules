"""A local MCP server (official SDK) that imitates the two Atlassian tools we call. Test helper only: lets the hand-written MCP client
in shared/references/mcp_client.py be checked against the real protocol implementation."""
from __future__ import annotations

import json
import socket
import threading
import time
from contextlib import contextmanager

import uvicorn
from mcp.server.mcpserver import MCPServer

ISSUES = {
    "SUP-48211": {"key": "SUP-48211", "fields": {
        "summary": "ICAP: 500 returned on uploads over 50 MB",
        "status": {"name": "Resolved"}, "resolution": {"name": "Fixed"},
        "fixVersions": [{"name": "5.9.0"}],
        "description": {"type": "doc", "content": [{"type": "paragraph", "content": [
            {"type": "text", "text": "Large uploads fail after 60 seconds because the ICAP request timeout is shorter than the archive scan time."}]}]},
        "comment": {"comments": [{"body": {"type": "doc", "content": [{"type": "paragraph", "content": [
            {"type": "text", "text": "Workaround: raise the request timeout to 300 seconds."}]}]}}]},
    }},
}
PAGES = {"20114": {"title": "Sizing archive scans on Core 5.x", "body": "Rule of thumb: allow one second per MB of archive plus extraction time."}}


def build_server() -> MCPServer:
    server = MCPServer("fake-atlassian")

    @server.tool()
    def getJiraIssue(cloudId: str, issueIdOrKey: str, fields: list[str] | None = None, responseContentFormat: str = "markdown") -> str:
        if issueIdOrKey not in ISSUES:
            raise ValueError(f"Issue does not exist or you do not have permission to see it: {issueIdOrKey}")
        return json.dumps(ISSUES[issueIdOrKey])

    @server.tool()
    def getConfluencePage(cloudId: str, pageId: str, contentFormat: str = "markdown") -> str:
        if pageId not in PAGES:
            raise ValueError("Page not found")
        return json.dumps(PAGES[pageId])

    return server


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def running_server():
    """Yields the MCP endpoint URL of a server running on a free local port."""
    port = _free_port()
    config = uvicorn.Config(build_server().streamable_http_app(), host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
