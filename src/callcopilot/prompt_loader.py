"""Versioned prompt loader. Prompts live in src/callcopilot/prompts/<name>/v<N>.md, separate from code.

File format:  ---SYSTEM---  ...system text...  ---USER---  ...user text...
Placeholders: {{name}} (single pass, so inserted values are never re-scanned).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from callcopilot.llm.types import Message
from callcopilot.paths import PROMPTS_DIR

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


def load_prompt(name: str, version: int | None = None) -> PromptTemplate:
    folder = PROMPTS_DIR / name
    if not folder.is_dir():
        raise KeyError(f"unknown prompt '{name}'")
    versions = sorted(int(p.stem[1:]) for p in folder.glob("v*.md") if p.stem[1:].isdigit())
    chosen = version or versions[-1]
    text = (folder / f"v{chosen}.md").read_text(encoding="utf-8")
    head, _, rest = text.partition("---SYSTEM---")
    system, _, user = rest.partition("---USER---")
    return PromptTemplate(name, chosen, system.strip(), user.strip())
