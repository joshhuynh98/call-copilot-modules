from __future__ import annotations

from dataclasses import dataclass, field


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
