import re

import pytest

from callcopilot.llm import LLMClient, LLMResponse, StreamEvent


class ScriptedLLM(LLMClient):
    """Replies from a list (strings or callables taking the messages). The last reply repeats."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []  # (messages, tag, tools)

    def _next(self, messages, tag, tools):
        self.calls.append((list(messages), tag, tools))
        r = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(r, Exception):
            raise r
        return r(messages) if callable(r) else r

    def complete(self, messages, *, tools=None, tag=None, temperature=0.2):
        return LLMResponse(text=self._next(messages, tag, tools))

    def stream(self, messages, *, tools=None, tag=None, temperature=0.2):
        text = self._next(messages, tag, tools)
        for chunk in re.findall(r".{1,4}", text, flags=re.S):
            yield StreamEvent("text", text=chunk)


class EmptyBackend:
    """Tool backend that finds nothing."""

    def search_docs(self, query, product=None, limit=5): return []
    def search_jira(self, query, limit=5): return []
    def search_confluence(self, query, limit=5): return []
    def similar_cases(self, query, limit=5): return []
    def customer_history(self, customer, limit=5): return []


@pytest.fixture
def icap():
    from callcopilot.sources import load_scenario
    return load_scenario("icap_upload_timeouts")


@pytest.fixture
def vdi():
    from callcopilot.sources import load_scenario
    return load_scenario("third_party_vdi_issue")
