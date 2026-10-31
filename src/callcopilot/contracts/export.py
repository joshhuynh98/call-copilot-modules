"""Export JSON Schema for every contract to ./contracts/*.schema.json (for non-Python teammates)."""
import json

from pydantic import BaseModel

from callcopilot import contracts as c
from callcopilot.paths import REPO_ROOT

MODELS: dict[str, type[BaseModel]] = {
    "transcript": c.Transcript,
    "recommendation_set": c.RecommendationSet,
    "style_profile": c.StyleProfile,
    "email_options": c.EmailOptions,
    "meeting_context": c.MeetingContext,
    "meeting_summary": c.MeetingSummary,
    "email_draft": c.EmailDraft,
    "chat_result": c.ChatResult,
    "chat_event": c.ChatEvent,
    "search_hit": c.SearchHit,
}


def export(out_dir=REPO_ROOT / "contracts") -> list[str]:
    out_dir.mkdir(exist_ok=True)
    names = []
    for name, model in MODELS.items():
        (out_dir / f"{name}.schema.json").write_text(
            json.dumps(model.model_json_schema(), indent=2) + "\n", encoding="utf-8"
        )
        names.append(name)
    return names


if __name__ == "__main__":
    print("exported:", ", ".join(export()))
