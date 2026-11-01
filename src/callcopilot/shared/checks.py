"""Check results and the soft style checks every module shares.

Each module keeps its own checks next to its code (modules/<name>/checks.py); they all report with `CheckResult`.
Soft results (`soft=True`) are warnings: they never fail a run, but they can trigger a repair round.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from callcopilot.shared import policy


@dataclass
class CheckResult:
    name: str
    passed: bool
    details: list[str] = field(default_factory=list)
    scope: str = ""  # e.g. a recommendation id, so reports can point at the item
    soft: bool = False  # reported as a warning; does not fail the run

    def __str__(self) -> str:
        mark = "PASS" if self.passed else ("WARN" if self.soft else "FAIL")
        where = f" [{self.scope}]" if self.scope else ""
        extra = f": {'; '.join(self.details)}" if self.details else ""
        return f"{mark} {self.name}{where}{extra}"


def failed(results: list[CheckResult]) -> list[CheckResult]:
    return [r for r in results if not r.passed and not r.soft]


def summarize(results: list[CheckResult]) -> str:
    bad = failed(results)
    return f"{len(results) - len(bad)}/{len(results)} checks passed"


# -- soft style checks: concise, no filler, plain sentences ------------------------------------------------------
MAX_BULLET_WORDS = 30
MAX_AVG_SENTENCE_WORDS = 22
_LIST_LINE = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s+(.*)$")


def check_style(text: str, *, courtesy_line: bool = False) -> list[CheckResult]:
    """`courtesy_line=True` (customer email): exactly one brief thank-you is expected, wherever the model places it.
    False (summaries, chat): none."""
    out: list[CheckResult] = []

    filler = sorted({m.group(0).lower() for m in policy.FILLER_RE.finditer(text)})
    out.append(CheckResult("no_filler", not filler, [f"filler: '{f}'" for f in filler], soft=True))

    n = len(policy.COURTESY_RE.findall(text))
    if courtesy_line:
        detail = [] if n == 1 else ["no courtesy line: briefly thank the customer for their time" if n == 0 else f"{n} thank-you lines; one is enough"]
    else:
        detail = [] if n == 0 else ["thank-you line not wanted here"]
    out.append(CheckResult("courtesy_line", not detail, detail, soft=True))

    long_items = []
    for line in text.splitlines():
        m = _LIST_LINE.match(line)
        if m and len(m.group(1).split()) > MAX_BULLET_WORDS:
            long_items.append(f"{len(m.group(1).split())} words: {m.group(1)[:50]}...")
    out.append(CheckResult("concise_bullets", not long_items, [f"over {MAX_BULLET_WORDS}: {i}" for i in long_items], soft=True))

    sentences = [s for s in policy.split_sentences(text) if len(s.split()) > 3]
    avg = sum(len(s.split()) for s in sentences) / len(sentences) if sentences else 0
    out.append(CheckResult("plain_sentences", avg <= MAX_AVG_SENTENCE_WORDS,
                           [f"average sentence is {avg:.0f} words (> {MAX_AVG_SENTENCE_WORDS})"] if avg > MAX_AVG_SENTENCE_WORDS else [], soft=True))
    return out
