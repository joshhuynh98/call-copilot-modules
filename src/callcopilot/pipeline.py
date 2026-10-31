"""Glue: build the LLM client for a meeting (provider adapter + redaction) and run the module chain."""
from __future__ import annotations

import re

from callcopilot.contracts import EmailDraft, EmailOptions, MeetingSummary, RecommendationSet, StyleProfile, Transcript
from callcopilot.llm import LLMClient, LLMConfig, create_client
from callcopilot.modules.email import draft_email
from callcopilot.modules.summary import summarize
from callcopilot.safety import RedactingClient, Redactor, categories_from_env


_LEGAL_SUFFIX = re.compile(r"[\s,]+(co\.?|inc\.?|ltd\.?|llc|gmbh|corp\.?|corporation|group|plc)$", re.IGNORECASE)


def known_names(transcript: Transcript | None) -> dict[str, list[str]]:
    """Names from the call metadata to redact, with the short forms people actually say."""
    if transcript is None:
        return {}
    m = transcript.metadata
    people = [n for n in (m.customer_contact, m.agent_name) if n]
    people += [p.split()[0] for p in people if len(p.split()[0]) >= 3]  # first names too
    company = m.customer_name.strip()
    variants = [company]
    while _LEGAL_SUFFIX.search(variants[-1]):
        variants.append(_LEGAL_SUFFIX.sub("", variants[-1]).strip())
    if len(variants[-1].split()) > 1 and len(variants[-1].split()[0]) >= 5:
        variants.append(variants[-1].split()[0])  # "Kestrel Freight" -> "Kestrel"
    return {"CUSTOMER": list(dict.fromkeys(variants)), "PERSON": people}


def make_llm(config: LLMConfig, transcript: Transcript | None = None, env=None) -> LLMClient:
    """Provider adapter wrapped with reversible redaction (REDACTION_MODE=off|basic, default basic)."""
    base = create_client(config)
    redactor = Redactor(known_names(transcript), categories_from_env(env))
    return RedactingClient(base, redactor) if redactor.enabled else base


def run_summary(transcript: Transcript, config: LLMConfig, llm: LLMClient | None = None,
                recommendations: RecommendationSet | None = None) -> MeetingSummary:
    return summarize(transcript, llm or make_llm(config, transcript), config, recommendations)


def run_email(transcript: Transcript, summary: MeetingSummary, style: StyleProfile, recs: RecommendationSet | None,
              config: LLMConfig, options: EmailOptions | None = None, llm: LLMClient | None = None) -> EmailDraft:
    return draft_email(transcript, summary, style, recs, llm or make_llm(config, transcript), options)
