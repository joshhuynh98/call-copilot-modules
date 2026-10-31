"""PII / secret redaction applied to every message BEFORE it reaches the AI server.

Matters most if the server is an external API. Placeholders like [EMAIL_1] are reversible: the same
Redactor restores them in the model's output, so e.g. the customer's name still reaches the email.

Limits (regex-based, not NER): free-text personal names are only caught if they come from the call
metadata (customer, contact, agent). 4-part dotted numbers that look like IPv4 are redacted too, so
write versions as 3 parts where possible, or disable the "ip" category via REDACTION_CATEGORIES.
"""
from __future__ import annotations

import os
import re
from collections.abc import Iterator

from callcopilot.llm.client import LLMClient
from callcopilot.llm.types import LLMResponse, Message, StreamEvent, Tag, ToolSpec

ALL_CATEGORIES = ("email", "phone", "ip", "secret", "card")

_PATTERNS = {
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    "secret": re.compile(r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})\b"
                         r"|(?i:(?:api[_ -]?key|token|secret|password|passwd)\s*[:=]\s*)\S{6,}"),
    "card": re.compile(r"\b(?:\d[ -]?){13,16}\b"),
    "ip": re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    "phone": re.compile(r"(?<![\w.])\+?\d[\d ()-]{8,}\d\b"),
}
_ORDER = ("email", "secret", "card", "ip", "phone")  # most specific first


def categories_from_env(env=None) -> tuple[str, ...]:
    """REDACTION_MODE=off|basic (default basic); REDACTION_CATEGORIES=comma list to restrict."""
    env = os.environ if env is None else env
    if (env.get("REDACTION_MODE") or "basic").strip().lower() == "off":
        return ()
    raw = (env.get("REDACTION_CATEGORIES") or "").strip()
    return tuple(c.strip() for c in raw.split(",") if c.strip() in ALL_CATEGORIES) if raw else ALL_CATEGORIES


class Redactor:
    def __init__(self, known_names: dict[str, list[str]] | None = None, categories: tuple[str, ...] = ALL_CATEGORIES):
        """known_names: {"CUSTOMER": ["Kestrel Freight Co."], "PERSON": ["Dana Ortiz"]} from call metadata."""
        self.categories = categories
        self.known = {label: [n for n in names if n] for label, names in (known_names or {}).items()}
        self._forward: dict[str, str] = {}  # original -> placeholder
        self._reverse: dict[str, str] = {}  # placeholder -> original
        self._counts: dict[str, int] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.categories or any(self.known.values()))

    def _placeholder(self, label: str, original: str) -> str:
        if original in self._forward:
            return self._forward[original]
        n = self._counts[label] = self._counts.get(label, 0) + 1
        ph = f"[{label}_{n}]"
        self._forward[original], self._reverse[ph] = ph, original
        return ph

    def redact(self, text: str) -> str:
        if not self.enabled:
            return text
        for label, names in self.known.items():
            for name in sorted(names, key=len, reverse=True):
                text = re.sub(re.escape(name), lambda m, l=label: self._placeholder(l, m.group(0)), text, flags=re.IGNORECASE)
        for cat in _ORDER:
            if cat in self.categories:
                text = _PATTERNS[cat].sub(lambda m, c=cat: self._placeholder(c.upper(), m.group(0)), text)
        return text

    def restore(self, text: str) -> str:
        for ph, original in self._reverse.items():
            text = text.replace(ph, original)
        return text


class RedactingClient(LLMClient):
    """Wraps any LLMClient: redacts on the way out, restores on the way back (including streams)."""

    def __init__(self, inner: LLMClient, redactor: Redactor):
        self.inner, self.redactor = inner, redactor

    def _out(self, messages: list[Message]) -> list[Message]:
        return [Message(m.role, self.redactor.redact(m.content)) for m in messages]

    def complete(self, messages: list[Message], *, tools: list[ToolSpec] | None = None,
                 tag: Tag | None = None, temperature: float = 0.2) -> LLMResponse:
        resp = self.inner.complete(self._out(messages), tools=tools, tag=tag, temperature=temperature)
        resp.text = self.redactor.restore(resp.text)
        for call in resp.tool_calls:
            call.arguments = {k: self.redactor.restore(v) if isinstance(v, str) else v for k, v in call.arguments.items()}
        return resp

    def stream(self, messages: list[Message], *, tools: list[ToolSpec] | None = None,
               tag: Tag | None = None, temperature: float = 0.2) -> Iterator[StreamEvent]:
        buf = ""
        for ev in self.inner.stream(self._out(messages), tools=tools, tag=tag, temperature=temperature):
            if ev.type != "text":
                if ev.tool_call:
                    ev.tool_call.arguments = {k: self.redactor.restore(v) if isinstance(v, str) else v
                                              for k, v in ev.tool_call.arguments.items()}
                if buf:
                    yield StreamEvent("text", text=self.redactor.restore(buf))
                    buf = ""
                yield ev
                continue
            buf += ev.text
            cut = buf.rfind("[")
            if cut != -1 and "]" not in buf[cut:] and len(buf) - cut < 24:
                safe, buf = buf[:cut], buf[cut:]  # a placeholder may be split across chunks: hold it back
            else:
                safe, buf = buf, ""
            if safe:
                yield StreamEvent("text", text=self.redactor.restore(safe))
        if buf:
            yield StreamEvent("text", text=self.redactor.restore(buf))
