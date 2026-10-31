"""Tool registry: specs for the model, safe argument handling, and untrusted-wrapped results."""
from __future__ import annotations

import json
from collections.abc import Callable

from callcopilot.contracts import SearchHit, ToolBackend
from callcopilot.llm import ToolSpec
from callcopilot.safety import wrap_untrusted

SEARCH_TOOLS = ("search_docs", "search_jira", "search_confluence")  # must run before any "I don't know"
MAX_QUERY_CHARS = 300


class ToolError(Exception):
    pass


def _spec(name: str, description: str, **props: str) -> ToolSpec:
    return ToolSpec(
        name, description,
        {"type": "object", "properties": {k: {"type": "string", "description": v} for k, v in props.items()},
         "required": [next(iter(props))]},
    )


TOOL_SPECS = [
    _spec("search_docs", "Search public OPSWAT documentation.", query="What to look for", product="Optional product name"),
    _spec("search_jira", "Search internal Jira tickets.", query="What to look for"),
    _spec("search_confluence", "Search internal Confluence pages.", query="What to look for"),
    _spec("similar_cases", "Find past support cases similar to a description.", query="Problem description"),
    _spec("customer_history", "List this customer's earlier cases.", customer="Customer name"),
]


class ToolRegistry:
    def __init__(self, backend: ToolBackend, local_tools: dict[str, tuple[ToolSpec, Callable[[dict], list[dict]]]] | None = None):
        self.backend = backend
        self.local = local_tools or {}

    @property
    def specs(self) -> list[ToolSpec]:
        return TOOL_SPECS + [spec for spec, _ in self.local.values()]

    def call(self, name: str, arguments: dict) -> list[SearchHit] | list[dict]:
        if name in self.local:
            return self.local[name][1](arguments)
        if name not in {s.name for s in TOOL_SPECS}:
            raise ToolError(f"unknown tool '{name}'")
        arg = "customer" if name == "customer_history" else "query"
        value = str(arguments.get(arg) or "").strip()[:MAX_QUERY_CHARS]
        if not value:
            raise ToolError(f"{name} needs a non-empty '{arg}'")
        if name == "search_docs":
            return self.backend.search_docs(value, product=(arguments.get("product") or None))
        return getattr(self.backend, name)(value)

    @staticmethod
    def render_result(name: str, hits: list) -> str:
        """What the model sees: JSON inside an untrusted wrapper, so retrieved text can't act as instructions."""
        items = [h.model_dump(exclude={"score"}, exclude_none=True) if isinstance(h, SearchHit) else h for h in hits]
        payload = json.dumps(items, indent=1)
        return f"TOOL_RESULT {name}\n" + wrap_untrusted(f"TOOL RESULT {name}", payload)

    @staticmethod
    def render_error(name: str, message: str) -> str:
        return f"TOOL_RESULT {name}\nERROR: {message}"
