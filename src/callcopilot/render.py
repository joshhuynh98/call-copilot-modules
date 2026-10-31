"""Plain-text renderers for the CLI / demo."""
from __future__ import annotations

from callcopilot.contracts import EmailDraft, MeetingSummary, RecommendationSet
from callcopilot.contracts.recommendations import SOURCE_LABELS


def _ids(ids: list[str]) -> str:
    return f" ({', '.join(ids)})" if ids else ""


def render_cheatsheet(recs: RecommendationSet) -> str:
    lines = ["RECOMMENDATIONS (cheatsheet)"]
    for r in sorted(recs.recommendations, key=lambda r: r.rank):
        lines.append(f"\n{r.rank}. {r.action}")
        lines.append(f"   Why: {r.rationale}")
        for s in r.sources:
            key = f" {s.jira_key}" if s.jira_key else ""
            lines.append(f"   [{SOURCE_LABELS[s.type]}]{key} {s.label} - {s.url}")
    return "\n".join(lines)


def render_summary(s: MeetingSummary) -> str:
    def claims(title, items):
        if not items:
            return []
        return [f"\n{title}"] + [f"  - {c.text}{' [uncertain]' if c.uncertain else ''}{_ids(c.segment_ids)}" for c in items]

    out = ["MEETING SUMMARY", s.plain_language, "", f"Issue: {s.issue.text}{_ids(s.issue.segment_ids)}"]
    p = s.product
    out.append(f"Product: {p.name or 'unknown'} {p.version or ''}{' [uncertain]' if p.uncertain else ''}{_ids(p.segment_ids)}")
    out += claims("Steps taken", s.steps_taken) + claims("Findings", s.findings)
    if s.next_actions:
        out.append("\nNext actions")
        out += [f"  - [{a.owner}] {a.action}{f' (due {a.due})' if a.due else ''}{_ids(a.segment_ids)}" for a in s.next_actions]
    out += claims("Open questions", s.open_questions)
    if s.third_party_components:
        out.append("\nThird-party components (out of OPSWAT scope)")
        out += [f"  - {c.name}: {c.role}{_ids(c.segment_ids)}" for c in s.third_party_components]
    if s.low_confidence_segment_ids:
        out.append(f"\nLow-confidence segments: {', '.join(s.low_confidence_segment_ids)}")
    return "\n".join(out)


def render_email(d: EmailDraft) -> str:
    out = ["EMAIL DRAFT (review and edit before sending)", f"Subject: {d.subject}", "", d.body]
    if d.claims_needing_verification:
        out.append("\nClaims needing your verification")
        out += [f"  - {c.claim}" + (f" ({c.reason})" if c.reason else "") for c in d.claims_needing_verification]
    if d.warnings:
        out.append("\nWarnings")
        out += [f"  - {w}" for w in d.warnings]
    return "\n".join(out)
