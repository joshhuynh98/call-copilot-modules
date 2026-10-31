"""Shared content policy used by the generators AND the eval checks (single source of truth)."""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)
_TRAILING = ".,;:!?"

# Jira-style keys (ABC-123) minus common non-Jira tokens that look the same.
JIRA_KEY_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d+\b")
NOT_JIRA_PREFIXES = {
    "CVE", "SHA", "UTF", "TLS", "SSL", "ISO", "RFC", "AES", "MD", "IPV", "HTTP", "FIPS", "PCI",
    "TCP", "UDP", "OS", "WIN", "X", "PDF", "ECDSA", "RSA", "KB", "GB", "MB", "TB", "SQL",
}

# Markdown that must never appear in a customer email. Plain-text lists are allowed: numbered ("1. ...") and dash ("- ...") lines.
MARKDOWN_PATTERNS = {
    "bold/italic (**)": re.compile(r"\*\*|__"),
    "backticks": re.compile(r"`"),
    "heading (#)": re.compile(r"(?m)^\s*#{1,6}\s"),
    "bullet (* + •)": re.compile(r"(?m)^\s*[*+•]\s+"),
    "markdown link": re.compile(r"\[[^\]]+\]\([^)]+\)"),
    "table": re.compile(r"(?m)^\s*\|.*\|\s*$|^\s*\|?\s*-{3,}"),
    "html tag": re.compile(r"</?[a-zA-Z][^>]*>"),
}

# Offers/requests for a call or meeting. Deliberately avoids matching plain references like "during our call".
MEETING_OFFER_RE = re.compile(
    r"\b(schedul\w*|set(ting)?\s+up|arrang\w*|organi[sz]\w*|book\w*|propos\w*|hop(ping)?\s+on|jump(ing)?\s+on|get(ting)?\s+on)\b"
    r"[^.!?\n]{0,30}\b(a|an|another|new|quick|short|brief|follow[- ]?up|zoom|teams|video|phone|google)\b"
    r"[^.!?\n]{0,15}\b(call|calls|meeting|meetings|chat|session|sync|catch[- ]?up)\b"
    r"|\b(follow[- ]?up|quick|short|brief)\s+(call|chat|meeting|sync)\b"
    r"|\b(call|phone|ring)\s+you\b"
    r"|\blet'?s\s+(meet|talk|connect|jump|hop|have a)\b"
    r"|\bwhen\s+(are|would)\s+you\s+(free|available)\b"
    r"|\b(zoom|google meet|teams meeting|webex)\b"
    r"|\b(available|free)\s+(for|to)\s+(a|an)?\s*(call|meeting|chat)\b"
    r"|\b(happy|glad|willing)\s+to\s+(discuss|talk|chat|walk you through)[^.!?\n]{0,30}\b(call|phone|live|together)\b",
    re.IGNORECASE,
)

SCOPE_STATEMENT_RE = re.compile(
    r"(out of (our |opswat'?s? )?scope|outside (of )?(our |opswat'?s? )?(scope|support)|not (something )?(opswat|we)\b[^.]{0,40}(responsible|support|cover)"
    r"|third[- ]party|(your|their|the customer'?s) (\w+ ){0,3}(teams?|vendors?|administrators?|admins?)|the vendor|its vendor|their vendor)",
    re.IGNORECASE,
)

TROUBLESHOOT_VERB_RE = re.compile(
    r"\b(restart|reboot|reconfigure|configure|change|check|update|upgrade|disable|enable|adjust|increase|"
    r"decrease|set|whitelist|allowlist|bypass|exclude|reset|clear|install|try|modify|edit|open|turn)\b",
    re.IGNORECASE,
)


# Pleasantries and padding the product owner does not want in summaries or emails (soft check + prompt rule).
FILLER_RE = re.compile(
    r"\b(i hope (this|you)[^.!?\n]{0,30}|hope you('re| are) (doing )?well|"
    r"thank(s| you) for (reaching out|contacting|your patience|getting in touch)|"
    r"here is a (quick |short |brief )?(recap|summary|overview)|here'?s a (quick |short |brief )?(recap|summary|overview)|"
    r"(please )?(do not|don'?t) hesitate|feel free to (reach out|contact|ask)|let me know if you have any (further |more |other )?questions|"
    r"i wanted to (follow up|reach out|touch base|check in)|touch base|at your earliest convenience|"
    r"as per our (call|conversation|discussion)|per our (call|conversation|discussion)|rest assured|"
    r"needless to say|it is worth (noting|mentioning)|as you (may )?know|i appreciate your patience|basically|essentially)\b",
    re.IGNORECASE,
)


# The one courtesy a customer email includes ("Thank you for your time during the meeting."), wherever the model thinks it
# reads best. Counted, not banned: exactly one is expected in a customer email, none in summaries and chat.
COURTESY_RE = re.compile(r"\b(thank(s| you)( again| so much| very much)? for (your time|taking the time|speaking with (me|us)|joining|meeting with (me|us))|i appreciate (your|the) time)", re.IGNORECASE)


def extract_urls(text: str) -> list[str]:
    return [m.group(0).rstrip(_TRAILING) for m in URL_RE.finditer(text)]


def is_internal_url(url: str) -> bool:
    u = url.lower()
    return "atlassian" in u or "/browse/" in u or "/wiki/" in u or "/jira" in u or "confluence" in u


def find_jira_keys(text: str, known_keys: set[str] | None = None) -> list[str]:
    """Jira-like keys in text: every known key, plus anything key-shaped that isn't a common non-Jira token."""
    found = [k for k in JIRA_KEY_RE.findall(text) if k.split("-")[0] not in NOT_JIRA_PREFIXES]
    if known_keys:
        found += [k for k in known_keys if re.search(rf"\b{re.escape(k)}\b", text)]
    return sorted(set(found))


@lru_cache
def third_party_terms(extra_path: str | None = None) -> tuple[str, ...]:
    path = Path(__file__).parent / "data" / "third_party_terms.txt"
    lines = path.read_text(encoding="utf-8").splitlines()
    if extra_path:
        lines += Path(extra_path).read_text(encoding="utf-8").splitlines()
    return tuple(l.strip().lower() for l in lines if l.strip() and not l.startswith("#"))


def mentions_term(text: str, terms) -> list[str]:
    low = text.lower().replace("-", " ")
    return [t for t in terms if re.search(rf"(?<![a-z0-9]){re.escape(t.lower().replace('-', ' '))}(?![a-z0-9])", low)]


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]


def third_party_troubleshooting(text: str, names, ) -> list[str]:
    """Sentences that mention a third-party product and give it a troubleshooting instruction
    without framing it as the customer's/vendor's responsibility. Heuristic; the judge covers nuance."""
    bad = []
    for sentence in split_sentences(text):
        if mentions_term(sentence, names) and TROUBLESHOOT_VERB_RE.search(sentence) and not SCOPE_STATEMENT_RE.search(sentence):
            bad.append(sentence)
    return bad
