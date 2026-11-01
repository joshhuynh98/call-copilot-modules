"""Quality-bar checks for ANY recommendations payload (mock or real). Spec: docs/recommendations-spec.md."""
from __future__ import annotations

import re
from urllib.parse import urlparse

from callcopilot.shared import policy
from callcopilot.shared.contracts import RecommendationSet, SourceType, Transcript

from callcopilot.shared.checks import CheckResult

MAX_ACTION_CHARS = 120
MAX_RATIONALE_CHARS = 240
MIN_SOURCES, MAX_SOURCES = 1, 4
MIN_RELEVANCE_OVERLAP = 0.05  # fraction of a recommendation's keywords found in its trigger segments
DUPLICATE_SIMILARITY = 0.8

_STOP = set("the a an and or to of in on for with is are was be it this that we you your our at as by from i can how do if not no".split())
_JIRA_URL = re.compile(r"/browse/([A-Z][A-Z0-9]+-\d+)")


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9][a-z0-9.\-_]{2,}", text.lower()) if w not in _STOP}


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def _url_ok(url: str) -> bool:
    p = urlparse(url)
    return p.scheme in ("http", "https") and bool(p.netloc) and " " not in url


def find_duplicates(recs) -> dict[str, str]:
    """{duplicate rec id: id of the earlier rec it duplicates}: near-identical title+action, or the same source set."""
    seen: list[tuple[str, set[str], set[str]]] = []
    dups: dict[str, str] = {}
    for r in recs:
        toks = _tokens(f"{r.title} {r.action}")
        urls = {s.url for s in r.sources}
        for rid, t2, u2 in seen:
            if _jaccard(toks, t2) >= DUPLICATE_SIMILARITY or (urls and urls == u2):
                dups.setdefault(r.id, rid)
        seen.append((r.id, toks, urls))
    return dups


def check_recommendations(
    recset: RecommendationSet,
    transcript: Transcript | None = None,
    *,
    third_party_terms=None,
) -> list[CheckResult]:
    terms = list(third_party_terms if third_party_terms is not None else policy.third_party_terms())
    out: list[CheckResult] = []
    recs = recset.recommendations

    out.append(CheckResult("has_recommendations", bool(recs), [] if recs else ["empty recommendation list"]))
    ranks = [r.rank for r in recs]
    out.append(CheckResult("ranks_ordered", ranks == sorted(ranks) and len(set(ranks)) == len(ranks) and (not ranks or ranks[0] == 1),
                           [] if ranks == sorted(ranks) and len(set(ranks)) == len(ranks) and (not ranks or ranks[0] == 1) else [f"ranks {ranks} must be unique, ascending, starting at 1"]))

    for r in recs:
        sc = r.id
        d = []
        if not r.action.strip() or "\n" in r.action or len(r.action) > MAX_ACTION_CHARS:
            d.append(f"action must be one line <= {MAX_ACTION_CHARS} chars (is {len(r.action)})")
        if not r.rationale.strip() or len(r.rationale) > MAX_RATIONALE_CHARS:
            d.append(f"rationale must be 1-{MAX_RATIONALE_CHARS} chars (is {len(r.rationale)})")
        out.append(CheckResult("glanceable_text", not d, d, sc))

        n = len(r.sources)
        out.append(CheckResult("source_count", MIN_SOURCES <= n <= MAX_SOURCES,
                               [] if MIN_SOURCES <= n <= MAX_SOURCES else [f"{n} sources; need {MIN_SOURCES}-{MAX_SOURCES}"], sc))

        d = []
        for s in r.sources:
            if not _url_ok(s.url):
                d.append(f"invalid URL: {s.url!r}")
            if not s.label.strip():
                d.append(f"source without label: {s.url}")
            if s.type == SourceType.jira:
                m = _JIRA_URL.search(s.url)
                if not s.jira_key:
                    d.append(f"jira source without jira_key: {s.url}")
                elif m and m.group(1) != s.jira_key:
                    d.append(f"jira_key {s.jira_key} does not match URL key {m.group(1)}")
            elif s.jira_key:
                d.append(f"jira_key set on non-jira source: {s.url}")
            if s.type == SourceType.public_doc and policy.is_internal_url(s.url):
                d.append(f"public_doc points at an internal-looking URL: {s.url}")
            if s.type in (SourceType.confluence, SourceType.jira) and not policy.is_internal_url(s.url):
                d.append(f"{s.type.value} source does not look like an Atlassian URL: {s.url}")
        out.append(CheckResult("source_types_and_urls", not d, d, sc))

        if transcript is not None:
            known = transcript.segment_ids()
            unknown = [i for i in r.trigger_segment_ids if i not in known]
            d = [f"unknown trigger segment {i}" for i in unknown]
            if not r.trigger_segment_ids:
                d.append("no trigger_segment_ids")
            out.append(CheckResult("trigger_segments_valid", not d, d, sc))

            segs = [transcript.by_id(i) for i in r.trigger_segment_ids if transcript.by_id(i)]
            if segs:
                seg_tokens = _tokens(" ".join(s.text for s in segs) + " " + (transcript.metadata.product or ""))
                rec_tokens = _tokens(f"{r.title} {r.action} {r.rationale} " + " ".join(f"{s.label} {s.snippet or ''}" for s in r.sources))
                overlap = len(rec_tokens & seg_tokens) / max(len(rec_tokens), 1)
                out.append(CheckResult("relevance_to_trigger_segments", overlap >= MIN_RELEVANCE_OVERLAP,
                                       [] if overlap >= MIN_RELEVANCE_OVERLAP else [f"keyword overlap {overlap:.2f} < {MIN_RELEVANCE_OVERLAP} (judge should confirm)"], sc))

        bad = policy.third_party_troubleshooting(f"{r.action}. {r.rationale}", terms)
        out.append(CheckResult("no_third_party_troubleshooting", not bad, [f"'{s}'" for s in bad], sc))

    dup = [f"{rid} duplicates {orig}" for rid, orig in find_duplicates(recs).items()]
    out.append(CheckResult("no_duplicates", not dup, dup))

    if recs and recs[0].confidence is not None:
        top_conf = recs[0].confidence
        higher = [r.id for r in recs[1:] if r.confidence is not None and r.confidence > top_conf + 0.25]
        out.append(CheckResult("ordering_plausible", not higher, [f"{i} has much higher confidence than rank 1" for i in higher]))
    return out
