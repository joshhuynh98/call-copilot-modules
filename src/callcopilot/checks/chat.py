from __future__ import annotations

import re

from callcopilot import policy
from callcopilot.contracts import ChatResult

from .base import CheckResult

SEARCH_TOOLS = {"search_docs", "search_jira", "search_confluence"}

# Phrases that mean "I don't know / can't tell". Checked on the start of an answer.
UNKNOWN_RE = re.compile(
    r"(i (do not|don't|dont) know|i('m| am) not sure|i can(no|')t (tell|find|determine|say|see)|i could(n't| not) (find|tell|determine)|"
    r"i (did not|didn't) find|not (mentioned|stated|discussed|specified|clear)|no (information|mention|record)|"
    r"unable to (find|determine|tell)|isn't (mentioned|stated)|doesn't (say|mention)|cannot (tell|determine|find)|"
    r"there('s| is) no (mention|information|record))",
    re.IGNORECASE,
)


def looks_like_unknown(text: str, window: int = 240) -> bool:
    return bool(UNKNOWN_RE.search(text[:window]))


def check_chat_turn(result: ChatResult, *, known_urls: set[str], expect_contains: list[str] | None = None) -> list[CheckResult]:
    out: list[CheckResult] = []
    searched = any(t.tool in SEARCH_TOOLS for t in result.tools_used)

    if looks_like_unknown(result.text):
        out.append(CheckResult("searched_before_unknown", searched, [] if searched else ["answered 'don't know' without calling a search tool"]))
        says_checked = "checked" in result.text.lower() or bool(result.checked)
        out.append(CheckResult("says_what_was_checked", says_checked, [] if says_checked else ["'not found' answer does not say what was checked"]))

    stray = [u for u in policy.extract_urls(result.text) if u not in known_urls]
    out.append(CheckResult("urls_from_known_sources", not stray, [f"unknown URL: {u}" for u in stray]))

    if expect_contains:
        low = result.text.lower()
        missing = [e for e in expect_contains if e.lower() not in low]
        out.append(CheckResult("answer_contains_expected", not missing, [f"missing: {m}" for m in missing]))
    return out
