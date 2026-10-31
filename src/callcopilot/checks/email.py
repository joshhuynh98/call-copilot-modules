"""Deterministic checks (and last-resort enforcement) for customer email drafts.

`check_email` is used by the eval harness and by the generator's repair loop.
`enforce_email_text` guarantees the hard rules even if the model ignores them after repair.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from callcopilot import policy
from callcopilot.contracts import EmailDraft, EmailOptions, MeetingSummary, RecommendationSet

from .base import CheckResult

MAX_PARAGRAPH_WORDS = 60
MAX_SUBJECT_CHARS = 100


@dataclass
class EmailPolicy:
    options: EmailOptions = field(default_factory=EmailOptions)
    allowed_urls: set[str] = field(default_factory=set)  # public_doc URLs only
    jira_keys: set[str] = field(default_factory=set)  # every key from the provided sources
    third_party_names: list[str] = field(default_factory=list)  # from the summary + policy terms
    has_third_party: bool = False  # summary found a third-party component -> scope statement required

    @classmethod
    def build(cls, options: EmailOptions, recs: RecommendationSet | None, summary: MeetingSummary | None) -> "EmailPolicy":
        allowed = set()
        keys = set()
        for s in recs.sources() if recs else []:
            if s.jira_key:
                keys.add(s.jira_key)
            if s.customer_visible and not policy.is_internal_url(s.url):
                allowed.add(s.url)
        components = [c.name for c in summary.third_party_components] if summary else []
        names = [n.lower() for n in components] + list(policy.third_party_terms())
        return cls(options, allowed, keys, names, bool(components))


def _words(text: str) -> int:
    return len(text.split())


def check_email(draft: EmailDraft, pol: EmailPolicy, *, signature: str = "") -> list[CheckResult]:
    body = draft.body
    if signature and body.rstrip().endswith(signature.strip()):
        body = body.rstrip()[: -len(signature.strip())].rstrip()
    text = f"{draft.subject}\n{body}"
    out: list[CheckResult] = []

    hits = [name for name, rx in policy.MARKDOWN_PATTERNS.items() if rx.search(draft.body) or rx.search(draft.subject)]
    out.append(CheckResult("no_markdown", not hits, [f"found: {', '.join(hits)}"] if hits else []))

    if pol.options.allow_meeting_offer:
        out.append(CheckResult("no_meeting_offer", True, ["skipped: allow_meeting_offer=true"]))
    else:
        m = policy.MEETING_OFFER_RE.search(text)
        out.append(CheckResult("no_meeting_offer", not m, [f"found: '{m.group(0)}'"] if m else []))

    n = _words(body)
    out.append(CheckResult("length_limit", n <= pol.options.max_words, [f"{n} words > max {pol.options.max_words}"] if n > pol.options.max_words else []))

    long_paras = [p for p in re.split(r"\n\s*\n", body) if _words(p) > MAX_PARAGRAPH_WORDS]
    out.append(CheckResult("short_paragraphs", not long_paras, [f"{len(long_paras)} paragraph(s) over {MAX_PARAGRAPH_WORDS} words"] if long_paras else []))

    subj_ok = bool(draft.subject.strip()) and len(draft.subject) <= MAX_SUBJECT_CHARS and "\n" not in draft.subject
    out.append(CheckResult("subject_ok", subj_ok, [] if subj_ok else ["subject empty, multi-line or too long"]))

    urls = policy.extract_urls(text)
    bad_urls = [u for u in urls if u not in pol.allowed_urls]
    out.append(CheckResult("urls_public_and_provided", not bad_urls, [f"not an allowed public-doc URL: {u}" for u in bad_urls]))

    internal = [u for u in urls if policy.is_internal_url(u)]
    out.append(CheckResult("no_internal_links", not internal, [f"internal link: {u}" for u in internal]))

    keys = policy.find_jira_keys(text, pol.jira_keys)
    out.append(CheckResult("no_jira_keys", not keys, [f"ticket id in email: {k}" for k in keys]))

    if pol.has_third_party:
        ok = bool(policy.SCOPE_STATEMENT_RE.search(body))
        out.append(CheckResult("third_party_scope_statement", ok, [] if ok else ["summary has third-party components but email has no out-of-scope statement"]))
    bad = policy.third_party_troubleshooting(body, pol.third_party_names)
    out.append(CheckResult("no_third_party_troubleshooting", not bad, [f"sentence: {s}" for s in bad]))
    return out


def _strip_markdown(text: str) -> str:
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 \2", text)
    text = re.sub(r"</?[a-zA-Z][^>]*>", "", text)
    text = re.sub(r"(\*\*|__|`)", "", text)
    text = re.sub(r"(?m)^\s*#{1,6}\s+", "", text)
    text = re.sub(r"(?m)^(\s*)[-*+•]\s+", r"\1", text)
    text = re.sub(r"(?m)^\s*\|?\s*-{3,}[-|\s]*$", "", text)
    return text


def enforce_email_text(text: str, pol: EmailPolicy) -> tuple[str, list[str]]:
    """Last-resort sanitiser for customer-facing text. Returns (clean_text, human-readable fixes applied)."""
    fixes: list[str] = []
    cleaned = _strip_markdown(text)
    if cleaned != text:
        fixes.append("removed markdown formatting")

    for url in dict.fromkeys(policy.extract_urls(cleaned)):
        if url not in pol.allowed_urls:
            cleaned = cleaned.replace(url, "")
            fixes.append(f"removed link not allowed in customer email: {url}")
    for key in policy.find_jira_keys(cleaned, pol.jira_keys):
        cleaned = re.sub(rf"\b{re.escape(key)}\b", "", cleaned)
        fixes.append(f"removed ticket id {key}")

    if not pol.options.allow_meeting_offer:
        kept, dropped = [], []
        for line in cleaned.split("\n"):
            parts = re.split(r"(?<=[.!?])\s+", line)
            ok = [p for p in parts if not policy.MEETING_OFFER_RE.search(p)]
            dropped += [p for p in parts if policy.MEETING_OFFER_RE.search(p)]
            kept.append(" ".join(ok))
        if dropped:
            cleaned = "\n".join(kept)
            fixes.append("removed meeting/call offer sentence(s)")

    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r" {2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, fixes
