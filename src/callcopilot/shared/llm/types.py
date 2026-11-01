from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class LLMError(Exception):
    """Any failure talking to the AI server; the message is safe to show the user."""


@dataclass
class Message:
    role: str  # system | user | assistant
    content: str


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema object


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    id: str | None = None


@dataclass
class Tag:
    """Metadata for logging/tracing and for the fake provider. Never sent to a real server."""
    task: str  # summary | summary_reduce | email | chat | judge ...
    case_id: str | None = None


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)


@dataclass
class StreamEvent:
    type: str  # "text" | "tool_call"
    text: str = ""
    tool_call: ToolCall | None = None
