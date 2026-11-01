"""Pick the passages of a long page that matter for a recommendation (deterministic keyword overlap)."""
from __future__ import annotations

import re

_STOP = set("the a an and or to of in on for with is are was be it this that we you your our at as by from i can how do if not no "
            "have has will should would could what when which who why".split())


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9][a-z0-9._\-]{2,}", text.lower()) if w not in _STOP}


def select_excerpt(text: str, query: str, max_chars: int) -> str:
    """The best-matching paragraphs, in their original order, up to `max_chars`. Falls back to the start of the page."""
    paragraphs = [p.strip() for p in text.split("\n") if len(p.strip()) >= 25]
    if not paragraphs:
        return text.strip()[:max_chars]
    q = _tokens(query)
    scored = sorted(((len(q & _tokens(p)) / (1 + len(p) / 400), i) for i, p in enumerate(paragraphs)), key=lambda t: (-t[0], t[1]))
    chosen, used = [], 0
    for score, i in scored:
        if score <= 0 and chosen:
            break
        room = max_chars - used
        if room < 40:
            break
        piece = paragraphs[i][:room]
        chosen.append((i, piece))
        used += len(piece) + 1
    return "\n".join(p for _, p in sorted(chosen))
