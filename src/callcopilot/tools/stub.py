"""Fixture-backed ToolBackend: keyword search over every scenario's kb.json. Replace with real integrations later."""
from __future__ import annotations

import re

from callcopilot.contracts import SearchHit
from callcopilot.sources import list_scenarios, load_scenario

_STOP = set("the a an and or to of in on for with is are was be it this that we you your our at as by from how what did does do about can".split())


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9][a-z0-9.\-]+", text.lower()) if w not in _STOP}


class FixtureToolBackend:
    def __init__(self):
        self._kb: dict[str, list[dict]] = {"docs": [], "jira": [], "confluence": [], "cases": []}
        self._customers: dict[str, list[dict]] = {}
        for sid in list_scenarios():
            kb = load_scenario(sid).kb
            for key in self._kb:
                self._kb[key] += kb.get(key, [])
            for c in kb.get("customers", []):
                self._customers.setdefault(c["customer"].lower(), []).extend(c["history"])

    @staticmethod
    def _rank(entries: list[dict], query: str, limit: int) -> list[SearchHit]:
        q = _tokens(query)
        scored = []
        for e in entries:
            overlap = len(q & _tokens(f"{e['title']} {e.get('snippet', '')}"))
            if overlap:
                scored.append((overlap / max(len(q), 1), e))
        scored.sort(key=lambda t: -t[0])
        return [SearchHit(**e, score=round(s, 2)) for s, e in scored[:limit]]

    def search_docs(self, query: str, product: str | None = None, limit: int = 5) -> list[SearchHit]:
        return self._rank(self._kb["docs"], f"{query} {product or ''}", limit)

    def search_jira(self, query: str, limit: int = 5) -> list[SearchHit]:
        return self._rank(self._kb["jira"], query, limit)

    def search_confluence(self, query: str, limit: int = 5) -> list[SearchHit]:
        return self._rank(self._kb["confluence"], query, limit)

    def similar_cases(self, query: str, limit: int = 5) -> list[SearchHit]:
        return self._rank(self._kb["cases"], query, limit)

    def customer_history(self, customer: str, limit: int = 5) -> list[SearchHit]:
        entries = self._customers.get(customer.lower(), [])
        return [SearchHit(**e) for e in entries[:limit]]
