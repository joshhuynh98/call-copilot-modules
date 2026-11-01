"""Re-check an email draft the agent has edited (or any email text) against the hard rules."""
from __future__ import annotations

from pydantic import BaseModel

from callcopilot.modules.email.checks import EmailPolicy, check_email, enforce_email_text
from callcopilot.shared.contracts import EmailDraft


class CheckItem(BaseModel):
    name: str
    passed: bool
    soft: bool = False
    details: list[str] = []


class EmailReview(BaseModel):
    passed: bool  # all hard checks pass
    checks: list[CheckItem]
    fixes: list[str] = []  # what the auto-fixer would change
    suggested_body: str | None = None  # auto-fixed text, only present when it differs from the input


def review_email(subject: str, body: str, pol: EmailPolicy, signature: str = "") -> EmailReview:
    results = check_email(EmailDraft(subject=subject, body=body), pol, signature=signature)
    checks = [CheckItem(name=r.name, passed=r.passed, soft=r.soft, details=r.details) for r in results]
    cleaned, fixes = enforce_email_text(body, pol)
    return EmailReview(
        passed=all(c.passed or c.soft for c in checks),
        checks=checks,
        fixes=fixes,
        suggested_body=cleaned if fixes else None,
    )
