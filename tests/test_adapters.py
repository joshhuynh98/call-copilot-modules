import json

import httpx
import pytest

from callcopilot.shared.llm import LLMConfig, LLMError, Message, ToolSpec, client as client_mod, create_client

MSGS = [Message("system", "be brief"), Message("user", "hi")]
TOOLS = [ToolSpec("search_docs", "search", {"type": "object", "properties": {"query": {"type": "string"}}})]


def openai_client(handler, **kw):
    cfg = LLMConfig(provider="openai", url="http://ai.test/v1", model="m", api_key=kw.pop("api_key", "k"), **kw)
    return create_client(cfg, httpx.MockTransport(handler))


def anthropic_client(handler, **kw):
    cfg = LLMConfig(provider="anthropic", url="http://ai.test", model="m", api_key="k", **kw)
    return create_client(cfg, httpx.MockTransport(handler))


def sse(*events: str) -> bytes:
    return ("".join(f"{e}\n\n" for e in events)).encode()


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(client_mod, "_sleep", lambda s: None)


def test_openai_request_shape_and_response():
    seen = {}

    def handler(req):
        seen["url"], seen["auth"], seen["body"] = str(req.url), req.headers.get("authorization"), json.loads(req.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "hello", "tool_calls": [
            {"id": "c1", "function": {"name": "search_docs", "arguments": "{\"query\": \"q\"}"}}]}}]})

    r = openai_client(handler).complete(MSGS, tools=TOOLS)
    assert seen["url"] == "http://ai.test/v1/chat/completions" and seen["auth"] == "Bearer k"
    assert seen["body"]["model"] == "m" and seen["body"]["tools"][0]["function"]["name"] == "search_docs"
    assert r.text == "hello" and r.tool_calls[0].arguments == {"query": "q"}


def test_openai_without_api_key_sends_no_auth_header():
    seen = {}

    def handler(req):
        seen["has_auth"] = "authorization" in req.headers
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    openai_client(handler, api_key="").complete(MSGS)
    assert seen["has_auth"] is False


def test_openai_stream_text_and_tool_call():
    body = sse('data: {"choices":[{"delta":{"content":"Hel"}}]}', 'data: {"choices":[{"delta":{"content":"lo"}}]}',
               'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","function":{"name":"search_docs","arguments":"{\\"quer"}}]}}]}',
               'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"y\\": \\"x\\"}"}}]}}]}', "data: [DONE]")
    events = list(openai_client(lambda req: httpx.Response(200, content=body)).stream(MSGS, tools=TOOLS))
    assert "".join(e.text for e in events if e.type == "text") == "Hello"
    call = [e.tool_call for e in events if e.type == "tool_call"][0]
    assert call.name == "search_docs" and call.arguments == {"query": "x"}


def test_anthropic_request_shape_and_response():
    seen = {}

    def handler(req):
        seen["url"], seen["headers"], seen["body"] = str(req.url), req.headers, json.loads(req.content)
        return httpx.Response(200, json={"content": [{"type": "text", "text": "hi"}, {"type": "tool_use", "id": "t1", "name": "search_docs", "input": {"query": "q"}}]})

    r = anthropic_client(handler).complete(MSGS, tools=TOOLS)
    assert seen["url"] == "http://ai.test/v1/messages"
    assert seen["headers"]["x-api-key"] == "k" and "anthropic-version" in seen["headers"]
    assert seen["body"]["system"] == "be brief" and seen["body"]["messages"] == [{"role": "user", "content": "hi"}]
    assert seen["body"]["max_tokens"] > 0 and seen["body"]["tools"][0]["input_schema"]["type"] == "object"
    assert r.text == "hi" and r.tool_calls[0].name == "search_docs" and r.tool_calls[0].arguments == {"query": "q"}


def test_anthropic_stream_text_and_tool_use():
    body = sse('event: content_block_delta\ndata: {"type":"content_block_delta","delta":{"type":"text_delta","text":"He"}}',
               'event: content_block_delta\ndata: {"type":"content_block_delta","delta":{"type":"text_delta","text":"llo"}}',
               'event: content_block_start\ndata: {"type":"content_block_start","content_block":{"type":"tool_use","id":"t1","name":"search_jira"}}',
               'event: content_block_delta\ndata: {"type":"content_block_delta","delta":{"type":"input_json_delta","partial_json":"{\\"query\\":"}}',
               'event: content_block_delta\ndata: {"type":"content_block_delta","delta":{"type":"input_json_delta","partial_json":" \\"z\\"}"}}',
               'event: content_block_stop\ndata: {"type":"content_block_stop"}', 'event: message_stop\ndata: {"type":"message_stop"}')
    events = list(anthropic_client(lambda req: httpx.Response(200, content=body)).stream(MSGS, tools=TOOLS))
    assert "".join(e.text for e in events if e.type == "text") == "Hello"
    assert [e.tool_call.arguments for e in events if e.type == "tool_call"] == [{"query": "z"}]


def test_retries_on_503_then_succeeds():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(503) if len(calls) == 1 else httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    assert openai_client(handler, max_retries=2).complete(MSGS).text == "ok" and len(calls) == 2


def test_auth_error_message_points_at_the_api_key():
    with pytest.raises(LLMError, match="AI_SERVER_API_KEY"):
        openai_client(lambda req: httpx.Response(401, text="no")).complete(MSGS)


def test_unreachable_server_message_points_at_the_url():
    def handler(req):
        raise httpx.ConnectError("refused")

    with pytest.raises(LLMError, match="AI_SERVER_URL"):
        openai_client(handler, max_retries=1).complete(MSGS)
