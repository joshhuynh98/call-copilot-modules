"""AI server configuration. Environment variables ONLY; nothing is hardcoded.

  AI_PROVIDER                   openai (default) | anthropic | fake
  AI_SERVER_URL                 base URL of the AI server (required unless fake)
  AI_SERVER_API_KEY             API key; may be empty for local servers
  AI_MODEL                      model name (required unless fake)
  AI_REQUEST_TIMEOUT_SECONDS    optional, default 60
  AI_MAX_RETRIES                optional, default 2
  AI_MAX_CONTEXT_TOKENS         optional; transcript token budget before map-reduce kicks in
  AI_MAX_OUTPUT_TOKENS          optional; required by Anthropic (default 4096)
  AI_TOOL_MODE                  prompted (default) | native
  AI_JUDGE_*                    same names with AI_JUDGE_ prefix (e.g. AI_JUDGE_MODEL); unset fields fall back to AI_*
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from callcopilot.paths import REPO_ROOT

PROVIDERS = ("openai", "anthropic", "fake")
TOOL_MODES = ("prompted", "native")
DEFAULT_TRANSCRIPT_TOKEN_BUDGET = 12000


class ConfigError(Exception):
    """Raised with an actionable message; entry points print it without a stack trace."""


@dataclass(frozen=True)
class LLMConfig:
    provider: str = "fake"
    url: str = ""
    api_key: str = ""
    model: str = ""
    timeout_s: float = 60.0
    max_retries: int = 2
    max_context_tokens: int | None = None
    max_output_tokens: int | None = None
    tool_mode: str = "prompted"

    @property
    def is_fake(self) -> bool:
        return self.provider == "fake"

    @property
    def transcript_token_budget(self) -> int:
        return self.max_context_tokens or DEFAULT_TRANSCRIPT_TOKEN_BUDGET

    def describe(self) -> str:
        if self.is_fake:
            return "fake (offline, deterministic)"
        return f"{self.provider} @ {self.url} model={self.model}"


def load_dotenv(path: Path | None = None) -> None:
    """Tiny .env loader (KEY=VALUE lines). Real environment variables win over the file."""
    for candidate in [path] if path else [Path.cwd() / ".env", REPO_ROOT / ".env"]:
        if candidate and candidate.is_file():
            for line in candidate.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                value = value.strip().strip('"').strip("'")
                os.environ.setdefault(key.strip(), value)
            return


def _get(env, name: str) -> str:
    return (env.get(name) or "").strip()


def _num(env, name: str, cast, default):
    raw = _get(env, name)
    if not raw:
        return default
    try:
        return cast(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a number, got '{raw}'.") from None


def load_config(prefix: str = "AI", env=None) -> LLMConfig:
    """Read config for `prefix` ('AI' or 'AI_JUDGE'). Raises ConfigError naming every missing variable."""
    env = os.environ if env is None else env
    provider = (_get(env, f"{prefix}_PROVIDER") or "openai").lower()
    if provider not in PROVIDERS:
        raise ConfigError(f"{prefix}_PROVIDER must be one of {', '.join(PROVIDERS)}, got '{provider}'.")
    tool_mode = (_get(env, "AI_TOOL_MODE") or "prompted").lower()
    if tool_mode not in TOOL_MODES:
        raise ConfigError(f"AI_TOOL_MODE must be one of {', '.join(TOOL_MODES)}, got '{tool_mode}'.")

    cfg = LLMConfig(
        provider=provider,
        url=_get(env, f"{prefix}_SERVER_URL"),
        api_key=_get(env, f"{prefix}_SERVER_API_KEY"),
        model=_get(env, f"{prefix}_MODEL"),
        timeout_s=_num(env, f"{prefix}_REQUEST_TIMEOUT_SECONDS", float, 60.0),
        max_retries=_num(env, f"{prefix}_MAX_RETRIES", int, 2),
        max_context_tokens=_num(env, "AI_MAX_CONTEXT_TOKENS", int, None),
        max_output_tokens=_num(env, "AI_MAX_OUTPUT_TOKENS", int, None),
        tool_mode=tool_mode,
    )
    if not cfg.is_fake:
        missing = [n for n, v in ((f"{prefix}_SERVER_URL", cfg.url), (f"{prefix}_MODEL", cfg.model)) if not v]
        if missing:
            raise ConfigError(
                f"Missing required environment variable(s): {', '.join(missing)}. "
                "Copy .env.example to .env and fill them in, or set AI_PROVIDER=fake for offline mode "
                "(CLI: add --fake)."
            )
    return cfg


def load_judge_config(env=None) -> LLMConfig | None:
    """Config for the eval judge, or None if no real server is configured for it.

    Uses AI_JUDGE_* where set, falling back field-by-field to AI_*. The fake provider is never a judge.
    """
    env = os.environ if env is None else env
    merged = dict(env)
    for suffix in ("PROVIDER", "SERVER_URL", "SERVER_API_KEY", "MODEL", "REQUEST_TIMEOUT_SECONDS", "MAX_RETRIES"):
        if not _get(env, f"AI_JUDGE_{suffix}"):
            merged[f"AI_JUDGE_{suffix}"] = _get(env, f"AI_{suffix}")
    if (_get(merged, "AI_JUDGE_PROVIDER") or "openai").lower() == "fake":
        return None
    if not (_get(merged, "AI_JUDGE_SERVER_URL") and _get(merged, "AI_JUDGE_MODEL")):
        return None
    return load_config("AI_JUDGE", merged)
