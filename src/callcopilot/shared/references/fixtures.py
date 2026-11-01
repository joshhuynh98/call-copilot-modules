"""Offline reader for demos and tests: serves the fictional `kb.json` entries of the fixture scenarios as if they were the pages."""
from __future__ import annotations

from callcopilot.shared.sources import list_scenarios, load_scenario

from .types import Fetched


class FixtureReader:
    def __init__(self) -> None:
        self._pages: dict[str, tuple[str, str]] = {}
        for sid in list_scenarios():
            kb = load_scenario(sid).kb
            for key in ("docs", "jira", "confluence", "cases"):
                for e in kb.get(key, []):
                    self._pages[e["url"]] = (e["title"], e.get("snippet", ""))

    def read(self, url: str) -> Fetched:
        if url not in self._pages:
            return Fetched("skipped", note="no offline copy of this source in the fixtures")
        title, text = self._pages[url]
        return Fetched("ok", final_url=url, title=title, text=text)
