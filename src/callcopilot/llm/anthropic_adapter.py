"""Anthropic Messages API adapter. Same interface as the OpenAI adapter; only the wire format differs."""
from __future__ import annotations

import json
from collections.abc import Iterator

from .client import HTTPAdapter
from .types import LLMError, LLMResponse, Message, StreamEvent, Tag, ToolCall, ToolSpec

API_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 4096


class AnthropicAdapter(HTTPAdapter):
    def _endpoint(self) -> str:
        u = self.config.url.rstrip("/")
        if u.endswith("/messages"):
            return u
        return f"{u}/messages" if u.endswith("/v1") else f"{u}/v1/messages"

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json", "anthropic-version": API_VERSION}
        if self.config.api_key:
            h["x-api-key"] = self.config.api_key
        return h

    def _payload(self, messages, tools, temperature, stream) -> dict:
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        p = {
            "model": self.config.model,
            "max_tokens": self.config.max_output_tokens or DEFAULT_MAX_TOKENS,
            "temperature": temperature,
            "stream": stream,
            "messages": [{"role": m.role, "content": m.content} for m in messages if m.role != "system"],
        }
        if system:
            p["system"] = system
        if tools:
            p["tools"] = [{"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools]
        return p

    def complete(self, messages: list[Message], *, tools: list[ToolSpec] | None = None,
                 tag: Tag | None = None, temperature: float = 0.2) -> LLMResponse:
        data = self._post_json(self._endpoint(), self._headers(), self._payload(messages, tools, temperature, False))
        text, calls = [], []
        for block in data.get("content") or []:
            if block.get("type") == "text":
                text.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                calls.append(ToolCall(block["name"], block.get("input") or {}, block.get("id")))
        if not text and not calls and "content" not in data:
            raise LLMError("Unexpected response shape from the AI server (no content blocks).")
        return LLMResponse(text="".join(text), tool_calls=calls, usage=data.get("usage") or {})

    def stream(self, messages: list[Message], *, tools: list[ToolSpec] | None = None,
               tag: Tag | None = None, temperature: float = 0.2) -> Iterator[StreamEvent]:
        current: dict | None = None
        for line in self._stream_lines(self._endpoint(), self._headers(), self._payload(messages, tools, temperature, True)):
            line = line.strip()
            if not line.startswith("data:"):
                continue
            try:
                ev = json.loads(line[5:].strip())
            except ValueError:
                continue
            kind = ev.get("type")
            if kind == "content_block_start" and (ev.get("content_block") or {}).get("type") == "tool_use":
                cb = ev["content_block"]
                current = {"id": cb.get("id"), "name": cb.get("name", ""), "json": ""}
            elif kind == "content_block_delta":
                delta = ev.get("delta") or {}
                if delta.get("type") == "text_delta":
                    yield StreamEvent("text", text=delta.get("text", ""))
                elif delta.get("type") == "input_json_delta" and current is not None:
                    current["json"] += delta.get("partial_json", "")
            elif kind == "content_block_stop" and current is not None:
                try:
                    args = json.loads(current["json"] or "{}")
                except ValueError:
                    args = {}
                yield StreamEvent("tool_call", tool_call=ToolCall(current["name"], args, current["id"]))
                current = None
            elif kind == "error":
                raise LLMError(f"The AI server reported an error: {(ev.get('error') or {}).get('message', 'unknown')}")
            elif kind == "message_stop":
                break
