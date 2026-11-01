"""Schema-validated generation with a repair loop. Does not rely on provider JSON modes."""
from __future__ import annotations

import json
import re
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from .client import LLMClient
from .types import LLMError, Message, Tag

T = TypeVar("T", bound=BaseModel)


class StructuredOutputError(LLMError):
    pass


def schema_hint(model_cls: type[BaseModel]) -> str:
    return json.dumps(model_cls.model_json_schema(), separators=(",", ":"))


def extract_json(text: str):
    """First JSON object in `text` (tolerates ```json fences and chatter around it)."""
    text = re.sub(r"```(?:json)?", "", text)
    decoder = json.JSONDecoder()
    for m in re.finditer(r"\{", text):
        try:
            obj, _ = decoder.raw_decode(text[m.start():])
        except ValueError:
            continue
        if isinstance(obj, dict):
            return obj
    raise ValueError("no JSON object found in the model output")


def _errors(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:8])
    return str(exc)


def generate_structured(llm: LLMClient, messages: list[Message], model_cls: type[T], *, tag: Tag,
                        max_repairs: int = 2, temperature: float = 0.1) -> tuple[T, int]:
    """Returns (validated model, number of repair rounds used). Raises StructuredOutputError if still invalid."""
    convo = list(messages)
    last_error = ""
    for attempt in range(max_repairs + 1):
        text = llm.complete(convo, tag=tag, temperature=temperature).text
        try:
            return model_cls.model_validate(extract_json(text)), attempt
        except (ValueError, ValidationError) as exc:
            last_error = _errors(exc)
            convo = convo + [
                Message("assistant", text),
                Message("user", f"Your previous output was not valid. Problems: {last_error}\n"
                                "Reply with ONLY the corrected JSON object that matches the schema. No commentary."),
            ]
    raise StructuredOutputError(
        f"The AI server did not return valid {model_cls.__name__} JSON after {max_repairs} repair attempt(s): {last_error}. "
        "Try a more capable model or raise AI_MAX_RETRIES / context size."
    )
