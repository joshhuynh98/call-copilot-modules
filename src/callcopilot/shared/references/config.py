"""Configuration for reviewing recommendation sources (public docs, Jira, Confluence). Environment variables only.

  REFERENCE_REVIEW            auto (default) | on | off | fixtures
                                auto = on, except with AI_PROVIDER=fake where the offline fixture reader is used
  REFERENCE_DOCS_MODE         auto (default) | http | browser   how public docs pages are read
  REFERENCE_ALLOWED_HOSTS     comma list; default docs.opswat.com,www.opswat.com. Only these hosts are ever fetched
  REFERENCE_TIMEOUT_SECONDS   per source, default 20
  REFERENCE_EXCERPT_CHARS     how much of each source is kept, default 1200
  REFERENCE_BROWSER_CHANNEL   optional: msedge | chrome (use an installed browser instead of Playwright's own)
  ATLASSIAN_MCP_URL           Atlassian MCP server endpoint; blank = Jira/Confluence sources are not opened
  ATLASSIAN_MCP_AUTH          full value of the Authorization header for that server (e.g. "Bearer <token>")
  ATLASSIAN_CLOUD_ID          optional; defaults to the site host of each link (e.g. yourorg.atlassian.net)
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from callcopilot.shared.llm.config import ConfigError

DEFAULT_ALLOWED_HOSTS = ("docs.opswat.com", "www.opswat.com")
MODES = ("auto", "on", "off", "fixtures")
DOCS_MODES = ("auto", "http", "browser")


@dataclass(frozen=True)
class ReferenceConfig:
    mode: str = "off"  # resolved: on | off | fixtures
    docs_mode: str = "auto"
    allowed_hosts: tuple[str, ...] = DEFAULT_ALLOWED_HOSTS
    timeout_s: float = 20.0
    excerpt_chars: int = 1200
    cache_ttl_s: int = 3600
    max_sources: int = 12
    browser_channel: str = ""
    atlassian_mcp_url: str = ""
    atlassian_mcp_auth: str = ""
    atlassian_cloud_id: str = ""

    @property
    def enabled(self) -> bool:
        return self.mode in ("on", "fixtures")


def _get(env, name: str) -> str:
    return (env.get(name) or "").strip()


def load_reference_config(env=None, *, provider: str | None = None) -> ReferenceConfig:
    env = os.environ if env is None else env
    provider = (provider if provider is not None else _get(env, "AI_PROVIDER") or "openai").lower()
    mode = (_get(env, "REFERENCE_REVIEW") or "auto").lower()
    if mode not in MODES:
        raise ConfigError(f"REFERENCE_REVIEW must be one of {', '.join(MODES)}, got '{mode}'.")
    if mode == "auto":
        mode = "fixtures" if provider == "fake" else "on"
    docs_mode = (_get(env, "REFERENCE_DOCS_MODE") or "auto").lower()
    if docs_mode not in DOCS_MODES:
        raise ConfigError(f"REFERENCE_DOCS_MODE must be one of {', '.join(DOCS_MODES)}, got '{docs_mode}'.")

    def number(name, cast, default):
        raw = _get(env, name)
        try:
            return cast(raw) if raw else default
        except ValueError:
            raise ConfigError(f"{name} must be a number, got '{raw}'.") from None

    hosts = tuple(h.strip().lower() for h in _get(env, "REFERENCE_ALLOWED_HOSTS").split(",") if h.strip()) or DEFAULT_ALLOWED_HOSTS
    return ReferenceConfig(
        mode=mode, docs_mode=docs_mode, allowed_hosts=hosts,
        timeout_s=number("REFERENCE_TIMEOUT_SECONDS", float, 20.0),
        excerpt_chars=number("REFERENCE_EXCERPT_CHARS", int, 1200),
        browser_channel=_get(env, "REFERENCE_BROWSER_CHANNEL"),
        atlassian_mcp_url=_get(env, "ATLASSIAN_MCP_URL"),
        atlassian_mcp_auth=_get(env, "ATLASSIAN_MCP_AUTH"),
        atlassian_cloud_id=_get(env, "ATLASSIAN_CLOUD_ID"),
    )
