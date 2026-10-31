"""OpenAI-compatible chat completions (vLLM, Ollama, llama.cpp, LiteLLM, OpenAI, ...). The default."""
from __future__ import annotations

import json
from collections.abc import Iterator

from .client import HTTPAdapter
from .types import LLMError, LLMResponse, Message, StreamEvent, Tag, ToolCall, ToolSpec


def _args(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
        return value if isinstance(value, dict) else {}
    except ValueError:
        return {}


class OpenAIAdapter(HTTPAdapter):
    def _endpoint(self) -> str:
        u = self.config.url.rstrip("/")
        return u if u.endswith("/chat/completions") else f"{u}/chat/completions"

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.config.api_key:
            h["Authorization"] = f"Bearer {self.config.api_key}"
        return h

    def _payload(self, messages, tools, temperature, stream) -> dict:
        p = {
            "model": self.config.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
            "stream": stream,
        }
        if self.config.max_output_tokens:
            p["max_tokens"] = self.config.max_output_tokens
        if tools:
            p["tools"] = [{"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}} for t in tools]
        return p

    def complete(self, messages: list[Message], *, tools: list[ToolSpec] | None = None,
                 tag: Tag | None = None, temperature: float = 0.2) -> LLMResponse:
        data = self._post_json(self._endpoint(), self._headers(), self._payload(messages, tools, temperature, False))
        try:
            msg = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("Unexpected response shape from the AI server (no choices[0].message).") from exc
        calls = [
            ToolCall(c["function"]["name"], _args(c["function"].get("arguments")), c.get("id"))
            for c in msg.get("tool_calls") or []
        ]
        return LLMResponse(text=msg.get("content") or "", tool_calls=calls, usage=data.get("usage") or {})

    def stream(self, messages: list[Message], *, tools: list[ToolSpec] | None = None,
               tag: Tag | None = None, temperature: float = 0.2) -> Iterator[StreamEvent]:
        pending: dict[int, dict] = {}
        for line in self._stream_lines(self._endpoint(), self._headers(), self._payload(messages, tools, temperature, True)):
            line = line.strip()
            if not line.startswith("data:"):
                continue
            body = line[5:].strip()
            if body == "[DONE]":
                break
            try:
                chunk = json.loads(body)
            except ValueError:
                continue
            for choice in chunk.get("choices") or []:
                delta = choice.get("delta") or {}
                if delta.get("content"):
                    yield StreamEvent("text", text=delta["content"])
                for tc in delta.get("tool_calls") or []:
                    slot = pending.setdefault(tc.get("index", 0), {"id": None, "name": "", "args": ""})
                    slot["id"] = tc.get("id") or slot["id"]
                    fn = tc.get("function") or {}
                    slot["name"] += fn.get("name") or ""
                    slot["args"] += fn.get("arguments") or ""
        for slot in pending.values():
            if slot["name"]:
                yield StreamEvent("tool_call", tool_call=ToolCall(slot["name"], _args(slot["args"]), slot["id"]))
