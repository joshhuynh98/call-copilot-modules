"""Fixture scenarios: fully fictional calls with transcript, recommendations, style profile and goldens."""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from callcopilot.shared.contracts import EmailCore, RecommendationSet, StyleProfile, SummaryCore, Transcript
from callcopilot.shared.paths import LIVE_DOCS_DIR, SCENARIOS_DIR


@dataclass
class Scenario:
    id: str
    dir: Path

    def _json(self, rel: str) -> dict:
        return json.loads((self.dir / rel).read_text(encoding="utf-8"))

    @cached_property
    def transcript(self) -> Transcript:
        return Transcript.model_validate(self._json("transcript.json"))

    @cached_property
    def recommendations(self) -> RecommendationSet:
        return RecommendationSet.model_validate(self._json("recommendations.json"))

    @cached_property
    def live_recommendations(self) -> RecommendationSet:
        """The same recommendations with real docs links (fixtures/live_docs); the source review opens the real pages."""
        path = LIVE_DOCS_DIR / f"{self.id}.recommendations.json"
        if not path.exists():
            raise KeyError(f"Scenario '{self.id}' has no live-docs variant ({path.name} in fixtures/live_docs).")
        return RecommendationSet.model_validate(json.loads(path.read_text(encoding="utf-8")))

    def recommendations_for(self, live_docs: bool = False) -> RecommendationSet:
        return self.live_recommendations if live_docs else self.recommendations

    @cached_property
    def style(self) -> StyleProfile:
        return StyleProfile.model_validate(self._json("style_profile.json"))

    @cached_property
    def golden_summary(self) -> SummaryCore:
        return SummaryCore.model_validate(self._json("golden/summary.json"))

    @cached_property
    def golden_email(self) -> EmailCore:
        return EmailCore.model_validate(self._json("golden/email.json"))

    @cached_property
    def chat_script(self) -> dict:
        return self._json("golden/chat.json")

    @cached_property
    def expectations(self) -> dict:
        return self._json("golden/expectations.json")

    @cached_property
    def kb(self) -> dict:
        return self._json("kb.json")


def list_scenarios() -> list[str]:
    return sorted(p.name for p in SCENARIOS_DIR.iterdir() if (p / "transcript.json").exists())


def load_scenario(scenario_id: str) -> Scenario:
    path = SCENARIOS_DIR / scenario_id
    if not (path / "transcript.json").exists():
        raise KeyError(f"Unknown scenario '{scenario_id}'. Available: {', '.join(list_scenarios())}")
    return Scenario(scenario_id, path)


def has_live_docs(scenario_id: str) -> bool:
    return (LIVE_DOCS_DIR / f"{scenario_id}.recommendations.json").exists()


def find_scenario_by_case_id(case_id: str) -> Scenario | None:
    for sid in list_scenarios():
        sc = load_scenario(sid)
        if sc.transcript.metadata.case_id == case_id:
            return sc
    return None
