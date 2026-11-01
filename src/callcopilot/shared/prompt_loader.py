"""Versioned prompt loader. Each prompt lives in a `prompts/<name>/v<N>.md` folder next to the code that uses it
(modules/<module>/prompts for a module's own prompts, shared/prompts for style rules and shared rubrics), separate from the code.

File format:  ---SYSTEM---  ...system text...  ---USER---  ...user text...
Placeholders: {{name}} (single pass, so inserted values are never re-scanned).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from callcopilot.shared.llm.types import Message
from callcopilot.shared.paths import PACKAGE_DIR

_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


@dataclass(frozen=True)
class PromptTemplate:
    name: str
    version: int
    system: str
    user: str

    @property
    def label(self) -> str:
        return f"{self.name}@v{self.version}"

    def _fill(self, text: str, values: dict) -> str:
        def sub(m):
            if m.group(1) not in values:
                raise KeyError(f"prompt {self.label} needs variable '{m.group(1)}'")
            return str(values[m.group(1)])
        return _PLACEHOLDER.sub(sub, text)

    def render(self, **values) -> list[Message]:
        msgs = [Message("system", self._fill(self.system, values))]
        if self.user.strip():
            msgs.append(Message("user", self._fill(self.user, values)))
        return msgs

    def render_system(self, **values) -> str:
        return self._fill(self.system, values)


@lru_cache
def _prompt_folders() -> dict[str, Path]:
    """prompt name -> folder, found under any `prompts/` directory in the package. Names must be unique."""
    found: dict[str, Path] = {}
    for root in sorted(PACKAGE_DIR.rglob("prompts")):
        for folder in sorted(p for p in root.iterdir() if p.is_dir()):
            if folder.name in found:
                raise RuntimeError(f"duplicate prompt name '{folder.name}': {found[folder.name]} and {folder}")
            found[folder.name] = folder
    return found


def load_prompt(name: str, version: int | None = None) -> PromptTemplate:
    folder = _prompt_folders().get(name)
    if folder is None:
        raise KeyError(f"unknown prompt '{name}'")
    versions = sorted(int(p.stem[1:]) for p in folder.glob("v*.md") if p.stem[1:].isdigit())
    chosen = version or versions[-1]
    text = (folder / f"v{chosen}.md").read_text(encoding="utf-8")
    head, _, rest = text.partition("---SYSTEM---")
    system, _, user = rest.partition("---USER---")
    return PromptTemplate(name, chosen, system.strip(), user.strip())
