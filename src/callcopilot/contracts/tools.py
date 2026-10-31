"""Tool interfaces the chatbot calls. Stubbed behind this Protocol now; real integrations later."""
from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel

from .recommendations import SourceType


class SearchHit(BaseModel):
    title: str
    url: str
    snippet: str = ""
    source_type: SourceType
    jira_key: str | None = None
    score: float | None = None


class ToolBackend(Protocol):
    def search_docs(self, query: str, product: str | None = None, limit: int = 5) -> list[SearchHit]: ...
    def search_jira(self, query: str, limit: int = 5) -> list[SearchHit]: ...
    def search_confluence(self, query: str, limit: int = 5) -> list[SearchHit]: ...
    def similar_cases(self, query: str, limit: int = 5) -> list[SearchHit]: ...
    def customer_history(self, customer: str, limit: int = 5) -> list[SearchHit]: ...
