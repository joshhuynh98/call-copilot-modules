import json

import httpx
import pytest
from fastapi.testclient import TestClient

import callcopilot.app.api as api
from callcopilot.app.evals.judge import Judge
from callcopilot.app.evals.runner import render_report, run_all, run_scenario
from callcopilot.shared.llm import LLMConfig
from conftest import ScriptedLLM

FAKE = LLMConfig(provider="fake")


@pytest.fixture
def client():
    return TestClient(api.create_app(FAKE))


def test_health_and_scenarios(client):
    assert client.get("/health").json()["status"] == "ok"
    ids = [s["id"] for s in client.get("/v1/scenarios").json()]
    assert "icap_upload_timeouts" in ids and len(ids) >= 5
    assert "<title>Call Copilot demo</title>" in client.get("/").text


def test_misconfigured_server_gives_clear_503(monkeypatch):
    for k in ("AI_PROVIDER", "AI_SERVER_URL", "AI_MODEL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(api, "load_dotenv", lambda *a, **k: None)
    r = TestClient(api.create_app(None)).post("/v1/summary", json={"transcript": {"metadata": {"case_id": "c", "customer_name": "x"}, "segments": []}})
    assert r.status_code == 503 and "AI_SERVER_URL" in r.json()["error"]


def test_stateless_summary_and_email(client):
    ctx = client.get("/v1/scenarios/icap_upload_timeouts").json()
    s = client.post("/v1/summary", json={"transcript": ctx["transcript"]})
    assert s.status_code == 200 and s.json()["product"]["version"] == "5.8.0"
    e = client.post("/v1/email", json={"transcript": ctx["transcript"], "style_profile": ctx["style_profile"], "recommendations": ctx["recommendations"]})
    assert e.status_code == 200 and e.json()["subject"] and "SUP-" not in e.json()["body"]


def test_meeting_flow_and_streaming_chat(client):
    ctx = client.get("/v1/scenarios/icap_upload_timeouts").json()
    assert client.put("/v1/meetings/m1", json=ctx).status_code == 200
    assert client.post("/v1/meetings/m1/summary").status_code == 200
    assert client.post("/v1/meetings/m1/email").status_code == 200
    r = client.post("/v1/meetings/m1/chat", json={"message": "What did the customer say about the version?"})
    assert r.headers["content-type"].startswith("text/event-stream")
    events = [(blk.split("\n")[0][7:], json.loads(blk.split("\n")[1][6:])) for blk in r.text.strip().split("\n\n")]
    assert events[-1][0] == "done" and "5.8.0" in events[-1][1]["result"]["text"]
    assert any(t == "token" for t, _ in events)
    assert client.get("/v1/meetings/nope/summary").status_code in (404, 405)
    assert client.post("/v1/meetings/nope/chat", json={"message": "x"}).status_code == 404


def test_recommendations_evaluate_endpoint(client):
    ctx = client.get("/v1/scenarios/icap_upload_timeouts").json()
    ok = client.post("/v1/recommendations/evaluate", json={"recommendations": ctx["recommendations"], "transcript": ctx["transcript"]}).json()
    assert ok["passed"] is True
    ctx["recommendations"]["recommendations"][0]["sources"] = []
    bad = client.post("/v1/recommendations/evaluate", json={"recommendations": ctx["recommendations"]}).json()
    assert bad["passed"] is False and any(c["name"] == "source_count" and not c["passed"] for c in bad["checks"])


def test_all_scenarios_pass_deterministic_evals_on_fake():
    reports, judge = run_all(FAKE, None)
    assert judge is None and len(reports) >= 5
    for r in reports:
        assert not r.errors, r.errors
        assert not r.hard_failures, [str(c) for _, c in r.hard_failures]
    text = render_report(reports, FAKE, judge)
    assert "Judge checks skipped" in text and "PASS" in text


def test_eval_report_flags_a_regression(icap, monkeypatch):
    """If the model output breaks a rule the report must say FAIL (here: golden email with a leaked ticket id)."""
    import callcopilot.app.evals.runner as runner
    real = runner.run_email

    def leaky(*a, **k):
        d = real(*a, **k)
        d.body += "\n\nSee SUP-48211."
        return d

    monkeypatch.setattr(runner, "run_email", leaky)
    rep = run_scenario("icap_upload_timeouts", FAKE, None)
    assert any(c.name == "no_jira_keys" for _, c in rep.hard_failures)
    assert "FAIL" in render_report([rep], FAKE, None)


def test_judge_scores_when_a_server_is_available():
    judge = Judge(LLMConfig(provider="openai", url="http://x", model="m"), client=ScriptedLLM(['{"score": 4, "reasons": "ok"}']))
    rep = run_scenario("icap_upload_timeouts", FAKE, judge)
    assert [j["rubric"] for j in rep.judge] == ["summary_faithfulness", "next_actions", "email_tone", "clarity_conciseness", "recommendation_relevance"]
    assert all(j["score"] == 4 for j in rep.judge)
