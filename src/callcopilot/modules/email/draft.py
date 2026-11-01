"""Email wrap-up: summary -> plain-text customer follow-up DRAFT.

Pipeline: generate (LLM) -> deterministic checks -> up to 2 repair rounds -> last-resort enforcement.
The model chooses the format and length; there are no per-email settings. The returned draft always satisfies the hard rules (plain text, no meeting offers, only public
links from the provided sources, no ticket ids, third-party scope statement); anything that had to be
auto-fixed or couldn't be fixed is listed in `warnings` for the agent.
"""
from __future__ import annotations

import json

from callcopilot.shared import policy
from callcopilot.modules.email.checks import EmailPolicy, check_email, enforce_email_text
from callcopilot.shared.checks import failed
from callcopilot.shared.contracts import (
    ClaimToVerify, EmailCore, EmailDraft, MeetingSummary, RecNote, RecommendationSet, StyleProfile, Transcript,
)
from callcopilot.shared.llm import LLMClient, Message, Tag, generate_structured
from callcopilot.modules.recommendations import email_link_recs
from callcopilot.shared.prompt_loader import load_prompt
from callcopilot.shared.safety import UNTRUSTED_NOTICE

MAX_REPAIRS = 2
SCOPE_SENTENCE = ("Please note that {names} is a third-party product and outside OPSWAT's scope, "
                  "so your own team or the vendor will need to handle that part.")

THIRD_PARTY_RULE_ON = ("This call involves third-party product(s): {names}. In one or two plain sentences say that this part is a "
                       "third-party product outside OPSWAT's scope and that the customer's own team or the vendor should handle it. "
                       "Do NOT suggest fixes, settings or troubleshooting steps for it.")
THIRD_PARTY_RULE_OFF = "If a third-party product comes up, state it is outside OPSWAT's scope; never give fix or troubleshooting steps for it."


def _clean_name(name: str) -> str:
    return name.strip().rstrip(".")


def build_links(recs: RecommendationSet | None) -> list[dict]:
    seen, out = set(), []
    for s in recs.public_sources() if recs else []:
        if policy.is_internal_url(s.url) or s.url in seen:
            continue
        seen.add(s.url)
        out.append({"title": s.label, "url": s.url})
    return out


def internal_notes(recs: RecommendationSet | None, notes: list[RecNote] | None = None) -> str:
    """Each kept recommendation's action, plus the one-sentence gist of what its opened sources say.
    Ids and links are stripped: context for the model, nothing quotable. Raw source text never reaches the email prompt."""
    if not recs:
        return "(none)"
    gist = {n.rec_id: n.gist for n in notes or [] if n.gist}
    return "\n".join(f"- {r.action}" + (f" (what the checked sources say: {gist[r.id]})" if r.id in gist else "") for r in recs.recommendations) or "(none)"


def _first_name(contact: str | None) -> str:
    return contact.split()[0] if contact else "there"


def draft_email(
    transcript: Transcript,
    summary: MeetingSummary,
    style: StyleProfile,
    recs: RecommendationSet | None,
    llm: LLMClient,
) -> EmailDraft:
    meta = transcript.metadata
    # Triage: drop duplicates and unrelated items; keep only links whose pages exist and support the recommendation.
    link_recs = email_link_recs(recs, summary.recommendation_notes, summary.source_reviews)
    pol = EmailPolicy.build(recs, summary, link_recs)
    links = build_links(link_recs)
    components = [_clean_name(c.name) for c in summary.third_party_components]
    names = ", ".join(components)
    prompt = load_prompt("email")

    msgs = prompt.render(
        untrusted_notice=UNTRUSTED_NOTICE, style_rules=load_prompt("style_rules").system,
        third_party_rule=THIRD_PARTY_RULE_ON.format(names=names) if components else THIRD_PARTY_RULE_OFF,
        customer=meta.customer_name, contact=meta.customer_contact or "unknown", agent=style.name,
        tone=style.tone,
        glossary="; ".join(f"{g.term} -> {g.preferred}" + (f" (avoid '{g.avoid}')" if g.avoid else "") for g in style.glossary) or "(none)",
        samples="\n---\n".join(style.sample_emails) or "(none)",
        summary=json.dumps(summary.model_dump(exclude={"meta", "recommendation_notes", "source_reviews"}), indent=1),
        links="\n".join(f"- {l['title']}: {l['url']}" for l in links) or "(none: include no link)",
        internal_notes=internal_notes(link_recs, summary.recommendation_notes),
    )

    core, repairs = generate_structured(llm, msgs, EmailCore, tag=Tag("email", meta.case_id))
    greeting_ok = lambda body: body.lstrip().lower().startswith(("hi", "hello", "dear"))  # noqa: E731

    def as_draft(c: EmailCore) -> EmailDraft:
        sig = style.signature.strip()
        return EmailDraft(subject=c.subject.strip(), body=(c.body.strip() + (f"\n\n{sig}" if sig else "")))

    for _ in range(MAX_REPAIRS):
        problems = [r for r in check_email(as_draft(core), pol, signature=style.signature) if not r.passed]  # hard + soft style
        if not problems:
            break
        feedback = "\n".join(f"- {p.name}: {'; '.join(p.details) or 'failed'}" for p in problems)
        msgs = msgs + [
            Message("assistant", core.model_dump_json()),
            Message("user", f"The draft has these problems:\n{feedback}\nRewrite the email so it follows every rule. "
                            "Reply with ONLY the corrected JSON object."),
        ]
        core, extra = generate_structured(llm, msgs, EmailCore, tag=Tag("email", meta.case_id))
        repairs += extra + 1

    # Last resort: enforce the hard rules in code, and tell the agent what we touched.
    warnings: list[str] = []
    body, fixes = enforce_email_text(core.body, pol)
    subject, subject_fixes = enforce_email_text(core.subject.replace("\n", " "), pol)
    warnings += fixes + [f"subject: {f}" for f in subject_fixes]
    if components and not policy.SCOPE_STATEMENT_RE.search(body):
        body = f"{body}\n\n{SCOPE_SENTENCE.format(names=names)}"
        warnings.append("added a standard out-of-scope statement for the third-party product(s)")
    draft = as_draft(EmailCore(subject=subject, body=body, claims_needing_verification=core.claims_needing_verification))
    if not greeting_ok(draft.body):
        warnings.append("email does not start with a greeting; check before sending")

    remaining = failed(check_email(draft, pol, signature=style.signature))
    warnings += [f"needs your attention: {r.name}: {'; '.join(r.details)}" for r in remaining]

    claims = list(core.claims_needing_verification)
    covered = {i for c in claims for i in c.segment_ids}
    for c in summary.all_claims():
        if c.uncertain and not (c.segment_ids and set(c.segment_ids) <= covered):
            claims.append(ClaimToVerify(claim=c.text, reason="marked uncertain in the call summary", segment_ids=c.segment_ids))
            covered |= set(c.segment_ids)
    if summary.product.uncertain:
        claims.append(ClaimToVerify(claim=f"Product/version: {summary.product.name or '?'} {summary.product.version or '(unknown version)'}",
                                    reason="product or version was unclear on the call", segment_ids=summary.product.segment_ids))

    used = [u for u in policy.extract_urls(draft.body) if u in pol.allowed_urls]
    unverified = {}
    for rv in summary.source_reviews:
        if rv.status in ("blocked", "error"):
            unverified[rv.url] = rv
    warnings += [f"link not verified (could not open it: {unverified[u].note or unverified[u].status}): {u}"
                 for u in dict.fromkeys(used) if u in unverified]
    draft.case_id = meta.case_id
    draft.claims_needing_verification = claims
    draft.links_used = list(dict.fromkeys(used))
    draft.warnings = warnings
    draft.meta = {"prompt": prompt.label, "repairs": repairs, "words": len(draft.body.split()) - len(style.signature.split())}
    return draft
