from __future__ import annotations

import re

from callcopilot.contracts import MeetingSummary, Transcript

from .base import CheckResult

MAX_PLAIN_WORDS = 120


def check_summary(summary: MeetingSummary, transcript: Transcript) -> list[CheckResult]:
    out: list[CheckResult] = []
    valid_ids = transcript.segment_ids()

    bad = [(c.text[:40], i) for c in summary.all_claims() for i in c.segment_ids if i not in valid_ids]
    bad += [(a.action[:40], i) for a in summary.next_actions for i in a.segment_ids if i not in valid_ids]
    out.append(CheckResult("segment_ids_exist", not bad, [f"'{t}' cites unknown {i}" for t, i in bad]))

    ungrounded = [c.text[:50] for c in summary.all_claims() if not c.segment_ids and not c.uncertain]
    out.append(CheckResult("claims_grounded_or_uncertain", not ungrounded, [f"no evidence and not marked uncertain: {t}" for t in ungrounded]))

    missing = []
    if not summary.issue.text.strip():
        missing.append("issue")
    if not summary.next_actions:
        missing.append("next_actions")
    if not summary.plain_language.strip():
        missing.append("plain_language")
    out.append(CheckResult("required_sections_present", not missing, [f"empty: {', '.join(missing)}"] if missing else []))

    n = len(summary.plain_language.split())
    out.append(CheckResult("plain_language_short", n <= MAX_PLAIN_WORDS, [f"{n} words > {MAX_PLAIN_WORDS}"] if n > MAX_PLAIN_WORDS else []))

    version = summary.product.version
    if version:
        stated = bool(re.search(rf"(?<![\d.]){re.escape(version)}(?![\d])", transcript.full_text())) or version == transcript.metadata.product_version
        ok = stated or summary.product.uncertain
        out.append(CheckResult("version_not_invented", ok, [] if ok else [f"version '{version}' is not in the transcript or case metadata and not marked uncertain"]))

    flagged = set(summary.low_confidence_segment_ids)
    expected = set(transcript.low_confidence_ids())
    out.append(CheckResult("low_confidence_segments_listed", flagged >= expected, [f"missing: {sorted(expected - flagged)}"] if flagged < expected else []))
    return out


def must_mention(texts: list[str], needles: list[str]) -> CheckResult:
    """Golden expectation helper: every needle (case-insensitive) appears somewhere in the given texts."""
    blob = " ".join(texts).lower()
    missing = [n for n in needles if not re.search(re.escape(n.lower()), blob)]
    return CheckResult("must_mention", not missing, [f"missing: {m}" for m in missing])
