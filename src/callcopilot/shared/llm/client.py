"""The ONE interface every module uses to talk to a model. Providers live in adapters."""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Iterator

import httpx

from .config import LLMConfig
from .types import LLMError, LLMResponse, Message, StreamEvent, Tag, ToolSpec

_sleep = time.sleep  # patched in tests
RETRY_STATUS = {429, 500, 502, 503, 504}


class LLMClient(ABC):
    @abstractmethod
    def complete(self, messages: list[Message], *, tools: list[ToolSpec] | None = None,
                 tag: Tag | None = None, temperature: float = 0.2) -> LLMResponse: ...

    @abstractmethod
    def stream(self, messages: list[Message], *, tools: list[ToolSpec] | None = None,
               tag: Tag | None = None, temperature: float = 0.2) -> Iterator[StreamEvent]: ...


class HTTPAdapter(LLMClient):
    """Shared plumbing for HTTP providers: timeouts, retries with backoff, readable errors."""

    def __init__(self, config: LLMConfig, transport: httpx.BaseTransport | None = None):
        self.config = config
        self._http = httpx.Client(timeout=config.timeout_s, transport=transport)

    def _fail_status(self, status: int, body: str) -> LLMError:
        if status in (401, 403):
            return LLMError(f"The AI server rejected the credentials ({status}). Check AI_SERVER_API_KEY.")
        if status == 404:
            return LLMError(f"The AI server returned 404. Check AI_SERVER_URL ({self.config.url}) and AI_MODEL ({self.config.model}).")
        return LLMError(f"The AI server returned HTTP {status}: {body[:300]}")

    def _unreachable(self, exc: Exception) -> LLMError:
        return LLMError(
            f"Cannot reach the AI server at {self.config.url} ({type(exc).__name__}). "
            "Check AI_SERVER_URL, that the server is running, and AI_REQUEST_TIMEOUT_SECONDS."
        )

    def _post_json(self, url: str, headers: dict, payload: dict) -> dict:
        retries = max(self.config.max_retries, 0)
        for attempt in range(retries + 1):
            try:
                r = self._http.post(url, headers=headers, json=payload)
            except httpx.TransportError as exc:
                if attempt == retries:
                    raise self._unreachable(exc) from exc
                _sleep(min(0.5 * 2**attempt, 4))
                continue
            if r.status_code in RETRY_STATUS and attempt < retries:
                _sleep(min(0.5 * 2**attempt, 4))
                continue
            if r.status_code >= 400:
                raise self._fail_status(r.status_code, r.text)
            try:
                return r.json()
            except ValueError as exc:
                raise LLMError("The AI server returned a non-JSON response. Is AI_SERVER_URL the API base URL?") from exc
        raise LLMError("unreachable")  # pragma: no cover

    def _stream_lines(self, url: str, headers: dict, payload: dict) -> Iterator[str]:
        try:
            with self._http.stream("POST", url, headers=headers, json=payload) as r:
                if r.status_code >= 400:
                    r.read()
                    raise self._fail_status(r.status_code, r.text)
                yield from r.iter_lines()
        except httpx.TransportError as exc:
            raise self._unreachable(exc) from exc
