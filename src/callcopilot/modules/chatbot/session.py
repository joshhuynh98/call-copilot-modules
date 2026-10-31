"""In-app chatbot for ONE meeting: grounded, tool-using, streaming, injection-safe.

Per question:
  1. Build messages: system prompt (rules + transcript/summary/recommendations as untrusted data) + history + question.
  2. Stream a turn. The model may call tools (prompted `TOOL_CALL {json}` line by default, or native tool calling).
  3. GUARD: if the model starts answering "I don't know / can't tell" without having called a search tool, we
     discard that answer, run search_docs + search_jira + search_confluence ourselves, and let it answer again.
  4. Post-process the final text: strip links that came from nowhere, sanitise <email> blocks to the plain-text
     email rules, add a "what I checked" footer to not-found answers, collect citations and warnings.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass

from callcopilot import policy
from callcopilot.checks import EmailPolicy, enforce_email_text
from callcopilot.checks.chat import looks_like_unknown
from callcopilot.contracts import (
    ChatEvent, ChatResult, Citation, MeetingContext, SearchHit, SourceType, ToolBackend, ToolUse,
)
from callcopilot.llm import LLMClient, LLMConfig, LLMError, Message, Tag, ToolCall, ToolSpec
from callcopilot.llm.structured import extract_json
from callcopilot.modules.summary.summarize import estimate_tokens
from callcopilot.prompt_loader import load_prompt
from callcopilot.safety import UNTRUSTED_NOTICE, wrap_untrusted
from callcopilot.tools import SEARCH_TOOLS, ToolError, ToolRegistry

TOOL_CALL_PREFIX = "TOOL_CALL"
HOLD_FOR_GUARD = 240  # chars of a fresh answer we hold back to check it isn't an unsearched "I don't know"
MAX_TOOL_ROUNDS = 3
HISTORY_LIMIT = 12
KEEP_FIRST = 5  # opening segments always kept in-prompt when the transcript exceeds the token budget

PROTOCOL_PROMPTED = """Tools (read-only): search_docs(query, product?), search_jira(query), search_confluence(query), similar_cases(query), customer_history(customer){extra}.
To call a tool, reply with EXACTLY one line and nothing else:
TOOL_CALL {{"name": "<tool>", "arguments": {{...}}}}
You will then receive a TOOL_RESULT message. You may call up to {rounds} tools per question. When you have what you need, answer normally (a final answer never contains a TOOL_CALL line)."""
PROTOCOL_NATIVE = "Use the provided tools when you need to look something up. You may call up to {rounds} tools per question."
FORCED_NOTE = ("NOTE: You were about to say you did not know without searching. The searches above were run for you. "
               "Answer using them if they help; if they found nothing, say so and state exactly what was checked.")


@dataclass
class _TurnEnd:
    text: str = ""
    tool_call: ToolCall | None = None
    suppressed: bool = False  # unsearched "I don't know" held back and discarded


def parse_tool_call(text: str) -> ToolCall | None:
    try:
        obj = extract_json(text[len(TOOL_CALL_PREFIX):])
    except ValueError:
        return None
    name = obj.get("name")
    args = obj.get("arguments")
    if not isinstance(name, str) or not name:
        return None
    return ToolCall(name, args if isinstance(args, dict) else {})


class ChatSession:
    def __init__(self, meeting: MeetingContext, llm: LLMClient, backend: ToolBackend, config: LLMConfig | None = None):
        self.meeting, self.llm, self.config = meeting, llm, config or LLMConfig()
        self.case_id = meeting.transcript.metadata.case_id
        self.history: list[Message] = []
        self.sources: dict[str, Citation] = {}
        self.jira_keys: dict[str, Citation] = {}
        for s in meeting.recommendations.sources() if meeting.recommendations else []:
            self._remember(Citation(label=s.label, url=s.url, jira_key=s.jira_key, type=s.type))

        self._transcript_text = meeting.transcript.render()
        self._truncated = estimate_tokens(self._transcript_text) > self.config.transcript_token_budget
        local = {}
        if self._truncated:
            local["search_transcript"] = (
                ToolSpec("search_transcript", "Search the full call transcript for a keyword.",
                         {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}),
                self._search_transcript,
            )
        self.tools = ToolRegistry(backend, local)

    def reset(self) -> None:
        self.history.clear()

    # -- helpers -----------------------------------------------------------------------------
    def _remember(self, c: Citation) -> None:
        if c.url:
            self.sources.setdefault(c.url, c)
        if c.jira_key:
            self.jira_keys.setdefault(c.jira_key, c)

    def _search_transcript(self, args: dict) -> list[dict]:
        words = [w for w in re.findall(r"\w{3,}", str(args.get("query", "")).lower())]
        hits = [s for s in self.meeting.transcript.segments if any(w in s.text.lower() for w in words)]
        return [{"segment": s.id, "speaker": s.speaker.value, "text": s.text} for s in hits[:8]]

    def _transcript_block(self) -> str:
        t = self.meeting.transcript
        if not self._truncated:
            return "Transcript:\n" + wrap_untrusted("TRANSCRIPT", self._transcript_text)
        head = t.segments[:KEEP_FIRST]
        used, tail = estimate_tokens(t.render(head)), []
        for seg in reversed(t.segments[KEEP_FIRST:]):  # fill the rest of the budget with the most recent segments
            cost = estimate_tokens(t.render([seg]))
            if used + cost > self.config.transcript_token_budget:
                break
            tail.insert(0, seg)
            used += cost
        omitted = len(t.segments) - len(head) - len(tail)
        note = f"[... {omitted} segments omitted; use search_transcript to look them up ...]"
        return "Transcript (partial):\n" + wrap_untrusted("TRANSCRIPT", f"{t.render(head)}\n{note}\n{t.render(tail)}")

    def _recs_text(self) -> str:
        recs = self.meeting.recommendations
        if not recs or not recs.recommendations:
            return "(none provided)"
        lines = []
        for r in sorted(recs.recommendations, key=lambda r: r.rank):
            srcs = "; ".join(f"[{s.type.value}] {s.label} {s.url}" + (f" ({s.jira_key})" if s.jira_key else "") for s in r.sources)
            lines.append(f"{r.rank}. {r.action} Why: {r.rationale} Sources: {srcs}")
        return wrap_untrusted("RECOMMENDATIONS", "\n".join(lines))

    def _system_prompt(self) -> str:
        m = self.meeting
        meta = m.transcript.metadata
        native = self.config.tool_mode == "native"
        extra = ", search_transcript(query)" if self._truncated else ""
        protocol = (PROTOCOL_NATIVE if native else PROTOCOL_PROMPTED).format(rounds=MAX_TOOL_ROUNDS, extra=extra)
        return load_prompt("chat").render_system(
            untrusted_notice=UNTRUSTED_NOTICE, style_rules=load_prompt("style_rules").system, tool_protocol=protocol,
            case_line=f"{meta.case_id}, customer {meta.customer_name}, product {meta.product or 'unknown'}",
            summary=json.dumps(m.summary.model_dump(exclude={"meta", "recommendation_notes"}), indent=1) if m.summary else "(not generated yet)",
            recommendations=self._recs_text(),
            email=(f"Subject: {m.email_draft.subject}\n\n{m.email_draft.body}" if m.email_draft else "(none yet)"),
            transcript_block=self._transcript_block(),
        )

    def _email_policy(self) -> EmailPolicy:
        return EmailPolicy.build(self.meeting.recommendations, self.meeting.summary)

    def _run_tool(self, name: str, args: dict, use: ToolUse) -> str:
        try:
            hits = self.tools.call(name, args)
            use.result_count = len(hits)
            for h in hits:
                if isinstance(h, SearchHit):
                    self._remember(Citation(label=h.title, url=h.url, jira_key=h.jira_key, type=h.source_type))
            return ToolRegistry.render_result(name, hits)
        except ToolError as exc:
            use.error = str(exc)
        except Exception as exc:  # backend failure must not crash the chat
            use.error = f"{type(exc).__name__}: the {name} backend failed"
        return ToolRegistry.render_error(name, use.error or "failed")

    # -- one model turn (streams tokens; reports tool calls / suppressed answers) --------------
    def _turn(self, messages: list[Message], *, guard: bool, tools_enabled: bool) -> Iterator[ChatEvent | _TurnEnd]:
        prompted = tools_enabled and self.config.tool_mode == "prompted"
        native = self.tools.specs if tools_enabled and self.config.tool_mode == "native" else None
        hold = max(len(TOOL_CALL_PREFIX) + 2 if prompted else 0, HOLD_FOR_GUARD if guard else 0)
        text, emitted, flushing, native_call = "", 0, hold == 0, None
        stream = self.llm.stream(messages, tools=native, tag=Tag("chat", self.case_id))
        try:
            for ev in stream:
                if ev.type == "tool_call":
                    native_call = ev.tool_call
                    continue
                text += ev.text
                if prompted and text.lstrip().startswith(TOOL_CALL_PREFIX):
                    continue
                if not flushing:
                    if len(text) < hold:
                        continue
                    if guard and looks_like_unknown(text):
                        yield _TurnEnd(text, suppressed=True)
                        return
                    flushing = True
                yield ChatEvent(type="token", text=text[emitted:])
                emitted = len(text)
        finally:
            close = getattr(stream, "close", None)
            if close:
                close()

        if native_call:
            yield _TurnEnd(text, native_call)
            return
        if prompted and text.lstrip().startswith(TOOL_CALL_PREFIX):
            yield _TurnEnd(text, parse_tool_call(text.lstrip()) or ToolCall("__invalid__"))
            return
        if not flushing and text:
            if guard and looks_like_unknown(text):
                yield _TurnEnd(text, suppressed=True)
                return
            yield ChatEvent(type="token", text=text)
        yield _TurnEnd(text)

    # -- public API --------------------------------------------------------------------------
    def ask(self, question: str) -> Iterator[ChatEvent]:
        try:
            yield from self._ask(question)
        except LLMError as exc:
            yield ChatEvent(type="error", error=str(exc))

    def _ask(self, question: str) -> Iterator[ChatEvent]:
        messages = [Message("system", self._system_prompt()), *self.history[-HISTORY_LIMIT:], Message("user", question)]
        used: list[ToolUse] = []
        searched = False
        final = ""
        for _ in range(MAX_TOOL_ROUNDS + 4):
            voluntary = sum(1 for u in used if not u.forced)
            end = _TurnEnd()
            for item in self._turn(messages, guard=not searched, tools_enabled=voluntary < MAX_TOOL_ROUNDS):
                if isinstance(item, _TurnEnd):
                    end = item
                else:
                    yield item

            if end.tool_call and end.tool_call.name == "__invalid__":
                used.append(ToolUse(tool="__invalid__", error="malformed TOOL_CALL line"))
                messages += [Message("assistant", end.text),
                             Message("user", 'NOTE: that TOOL_CALL line was not valid. Use exactly: TOOL_CALL {"name": "...", "arguments": {...}}')]
                continue
            if end.tool_call:
                call = end.tool_call
                use = ToolUse(tool=call.name, arguments=call.arguments)
                yield ChatEvent(type="tool_call", tool=use)
                result = self._run_tool(call.name, call.arguments, use)
                used.append(use)
                yield ChatEvent(type="tool_result", tool=use)
                messages += [Message("assistant", f"{TOOL_CALL_PREFIX} {json.dumps({'name': call.name, 'arguments': call.arguments})}"),
                             Message("user", result)]
                searched = searched or call.name in SEARCH_TOOLS
                continue
            if end.suppressed:
                results = []
                for name in SEARCH_TOOLS:
                    use = ToolUse(tool=name, arguments={"query": question}, forced=True)
                    yield ChatEvent(type="tool_call", tool=use)
                    results.append(self._run_tool(name, use.arguments, use))
                    used.append(use)
                    yield ChatEvent(type="tool_result", tool=use)
                messages += [Message("user", "\n\n".join(results)), Message("user", FORCED_NOTE)]
                searched = True
                yield ChatEvent(type="token", text="")  # keeps SSE clients alive while the second pass starts
                continue
            final = end.text
            break
        else:
            final = final or "Sorry, I could not complete that request. Please try rephrasing."

        result = self._finish(final, used)
        self.history += [Message("user", question), Message("assistant", result.text)]
        yield ChatEvent(type="done", result=result)

    # -- post-processing ---------------------------------------------------------------------
    def _finish(self, text: str, used: list[ToolUse]) -> ChatResult:
        text = text.strip()
        warnings: list[str] = []
        pol = self._email_policy()

        def clean_email(m: re.Match) -> str:
            body, fixes = enforce_email_text(m.group(1).strip(), pol)
            warnings.extend(f"email block: {f}" for f in fixes)
            return f"<email>\n{body}\n</email>"

        text = re.sub(r"<email>(.*?)</email>", clean_email, text, flags=re.DOTALL | re.IGNORECASE)

        for url in dict.fromkeys(policy.extract_urls(text)):
            if url not in self.sources:
                text = text.replace(url, "[link removed: not from a known source]")
                warnings.append(f"removed unknown link {url}")

        names = [n.lower() for n in (c.name for c in (self.meeting.summary.third_party_components if self.meeting.summary else []))]
        bad = policy.third_party_troubleshooting(text, names + list(policy.third_party_terms()))
        warnings += [f"possible third-party troubleshooting advice (out of OPSWAT scope): {s}" for s in bad]

        searches = [u for u in used if u.tool in SEARCH_TOOLS or u.tool in ("similar_cases", "customer_history")]
        checked = [f"{u.tool}({u.arguments.get('query') or u.arguments.get('customer', '')!r}) -> "
                   + (f"error: {u.error}" if u.error else f"{u.result_count} result(s)") for u in searches]
        if checked and looks_like_unknown(text) and "checked" not in text.lower():
            text += "\n\nWhat I checked: " + "; ".join(checked)

        citations: dict[str, Citation] = {}
        for url in policy.extract_urls(text):
            if url in self.sources:
                citations[url] = self.sources[url]
        for key in policy.find_jira_keys(text, set(self.jira_keys)):
            if key in self.jira_keys:
                citations.setdefault(self.jira_keys[key].url or key, self.jira_keys[key])
        return ChatResult(text=text, citations=list(citations.values()), tools_used=used, checked=checked, warnings=warnings)


__all__ = ["ChatSession", "SourceType"]
