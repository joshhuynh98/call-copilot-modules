from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Fetched:
    """The raw result of opening one source, before it is trimmed to an excerpt."""
    status: str  # ok | redirected | not_found | blocked | no_access | skipped | error
    final_url: str | None = None
    title: str | None = None
    text: str = ""
    note: str = ""
    lead: str = ""  # key facts that must always survive trimming (e.g. a ticket's status, resolution and fix versions)

    @property
    def has_content(self) -> bool:
        return self.status in ("ok", "redirected") and bool((self.text + self.lead).strip())
