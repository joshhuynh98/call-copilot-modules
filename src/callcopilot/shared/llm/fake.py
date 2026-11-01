"""Deterministic FAKE provider (AI_PROVIDER=fake). Offline; serves the scenario goldens.

It exists so the demo, unit tests and the evals' deterministic checks run with no AI server. It proves
the plumbing and the checkers, not model quality; judge-based quality evals need a real server.

It finds the scenario via `Tag.case_id` (the transcript's case id) and answers by `Tag.task`:
  organize / organize_reduce -> the golden summary's grouped content as a brief (+ recommendation notes)
  summary                  -> golden/summary.json
  email                    -> golden/email.json
  chat                     -> rules in golden/chat.json (plus a tiny tool-result-aware fallback)
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterator

from callcopilot.shared.contracts import CallBrief
from callcopilot.shared.sources import find_scenario_by_case_id

from .client import LLMClient
from .types import LLMError, LLMResponse, Message, StreamEvent, Tag, ToolCall, ToolSpec

NON_QUESTION_PREFIXES = ("TOOL_RESULT", "NOTE:")


class FakeLLM(LLMClient):
    def complete(self, messages: list[Message], *, tools: list[ToolSpec] | None = None,
                 tag: Tag | None = None, temperature: float = 0.2) -> LLMResponse:
        text, call = self._respond(messages, tools, tag)
        return LLMResponse(text=text, tool_calls=[call] if call else [])

    def stream(self, messages: list[Message], *, tools: list[ToolSpec] | None = None,
               tag: Tag | None = None, temperature: float = 0.2) -> Iterator[StreamEvent]:
        text, call = self._respond(messages, tools, tag)
        for chunk in re.findall(r"\S+\s*", text):
            yield StreamEvent("text", text=chunk)
        if call:
            yield StreamEvent("tool_call", tool_call=call)

    # -- internals ---------------------------------------------------------------------------
    def _respond(self, messages, tools, tag) -> tuple[str, ToolCall | None]:
        if not tag or not tag.case_id:
            raise LLMError("The fake AI provider needs a known scenario case id (it only answers for fixture scenarios).")
        scenario = find_scenario_by_case_id(tag.case_id)
        if scenario is None:
            raise LLMError(f"The fake AI provider has no fixture scenario for case '{tag.case_id}'. Use a real AI server for other data.")
        if tag.task in ("organize", "organize_reduce"):
            return self._brief(scenario).model_dump_json(), None
        if tag.task == "summary":
            return scenario.golden_summary.model_dump_json(), None
        if tag.task == "email":
            return scenario.golden_email.model_dump_json(), None
        if tag.task == "chat":
            text, call = self._chat(scenario, messages)
            if call and not tools:  # prompted protocol: tool call is a TOOL_CALL line in the text
                return "TOOL_CALL " + json.dumps({"name": call.name, "arguments": call.arguments}), None
            return text, call
        raise LLMError(f"The fake AI provider has no fixture for task '{tag.task}'.")

    @staticmethod
    def _brief(scenario) -> CallBrief:
        """Brief = the golden summary's grouped content; notes come from the golden file, else 'all relevant'."""
        raw = scenario._json("golden/summary.json")
        notes = raw.get("recommendation_notes") or [
            {"rec_id": r.id, "relevant": True, "issue": 0 if raw.get("issues") else None} for r in scenario.recommendations.recommendations
        ]
        return CallBrief.model_validate({**{k: v for k, v in raw.items() if k != "plain_language"}, "recommendation_notes": notes})

    @staticmethod
    def _split_turn(messages: list[Message]) -> tuple[str, list[str]]:
        results: list[str] = []
        for m in reversed(messages):
            if m.role != "user":
                continue
            if m.content.startswith(NON_QUESTION_PREFIXES):
                if m.content.startswith("TOOL_RESULT"):
                    results.append(m.content)
                continue
            return m.content, results
        return "", results

    def _chat(self, scenario, messages) -> tuple[str, ToolCall | None]:
        question, results = self._split_turn(messages)
        q = question.lower()
        for rule in scenario.chat_script["rules"]:
            if any(k in q for k in rule["when_any"]):
                tc = rule.get("tool_call")
                if tc and not results:
                    return "", ToolCall(tc["name"], tc.get("arguments", {}))
                return rule["answer"], None
        if results:
            blob = "\n".join(results)
            urls = re.findall(r'"url":\s*"([^"]+)"', blob)
            titles = re.findall(r'"title":\s*"([^"]+)"', blob)
            if urls:
                return f"Based on a search: {titles[0] if titles else 'result'} - {urls[0]}", None
            return "I couldn't find anything relevant for that.", None
        return "I don't know based on the call so far.", None
