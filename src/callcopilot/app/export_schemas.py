"""Export JSON Schema for every contract, plus the HTTP API's OpenAPI spec, to ./schemas/ (for non-Python teammates)."""
import json

from pydantic import BaseModel

from callcopilot.shared import contracts as c
from callcopilot.shared.paths import SCHEMAS_DIR

MODELS: dict[str, type[BaseModel]] = {
    "transcript": c.Transcript,
    "recommendation_set": c.RecommendationSet,
    "style_profile": c.StyleProfile,
    "meeting_context": c.MeetingContext,
    "meeting_summary": c.MeetingSummary,
    "email_draft": c.EmailDraft,
    "chat_result": c.ChatResult,
    "chat_event": c.ChatEvent,
    "search_hit": c.SearchHit,
}


def export(out_dir=SCHEMAS_DIR) -> list[str]:
    out_dir.mkdir(exist_ok=True)
    names = []
    for name, model in MODELS.items():
        (out_dir / f"{name}.schema.json").write_text(
            json.dumps(model.model_json_schema(), indent=2) + "\n", encoding="utf-8"
        )
        names.append(name)
    from callcopilot.app.api import create_app
    from callcopilot.shared.llm import LLMConfig
    spec = create_app(LLMConfig(provider="fake"), cors_origins=[], api_key="").openapi()
    (out_dir / "openapi.json").write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    names.append("openapi")
    return names


if __name__ == "__main__":
    print("exported:", ", ".join(export()))
