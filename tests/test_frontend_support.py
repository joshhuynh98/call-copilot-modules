"""Front-end support: CORS, optional API key, uniform errors, and re-checking an edited email draft."""
import json

import pytest
from fastapi.testclient import TestClient

import callcopilot.app.api as api
from callcopilot.shared.contracts import MeetingSummary
from callcopilot.modules.email.checks import EmailPolicy
from callcopilot.shared.llm import LLMConfig
from callcopilot.modules.email import review_email

FAKE = LLMConfig(provider="fake")
ORIGIN = "http://localhost:5173"
BAD_BODY = ("Hi Dana,\n\n**Next steps**\n- See SUP-48211 and https://opswat-internal.atlassian.example.invalid/browse/SUP-48211\n"
            "Let's schedule a call to review.")
GOOD_BODY = "Hi Dana,\n\nThanks for your time today.\n\nNext steps:\n1. Me: send the steps.\n2. You: send the log."


def app(**kw):
    return TestClient(api.create_app(FAKE, **kw))


# -- CORS ---------------------------------------------------------------------------------------------
def test_cors_is_off_by_default():
    r = app(cors_origins=[], api_key="").get("/health", headers={"Origin": ORIGIN})
    assert "access-control-allow-origin" not in r.headers


def test_cors_allows_configured_origin_for_preflight_and_requests():
    c = app(cors_origins=[ORIGIN], api_key="")
    pre = c.options("/v1/summary", headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST",
                                            "Access-Control-Request-Headers": "content-type,x-api-key"})
    assert pre.status_code == 200 and pre.headers["access-control-allow-origin"] == ORIGIN
    assert "x-api-key" in pre.headers["access-control-allow-headers"].lower()
    assert c.get("/v1/scenarios", headers={"Origin": ORIGIN}).headers["access-control-allow-origin"] == ORIGIN


def test_cors_rejects_other_origins():
    c = app(cors_origins=[ORIGIN], api_key="")
    r = c.get("/v1/scenarios", headers={"Origin": "http://evil.example.invalid"})
    assert "access-control-allow-origin" not in r.headers


def test_cors_origins_come_from_env(monkeypatch):
    monkeypatch.setenv("COPILOT_CORS_ORIGINS", f"{ORIGIN}, http://other.example.invalid")
    monkeypatch.setattr(api, "load_dotenv", lambda *a, **k: None)
    c = TestClient(api.create_app(FAKE))
    assert c.get("/health", headers={"Origin": "http://other.example.invalid"}).headers["access-control-allow-origin"] == "http://other.example.invalid"


# -- API key -----------------------------------------------------------------------------------------
def test_api_key_protects_v1_but_not_health_or_demo_page():
    c = app(cors_origins=[], api_key="s3cret")
    assert c.get("/health").json()["auth"] == "api-key" and c.get("/").status_code == 200
    no_key = c.get("/v1/scenarios")
    assert no_key.status_code == 401 and "X-API-Key" in no_key.json()["error"]
    assert c.get("/v1/scenarios", headers={"X-API-Key": "wrong"}).status_code == 401
    assert c.get("/v1/scenarios", headers={"X-API-Key": "s3cret"}).status_code == 200


def test_api_key_also_protects_chat_stream_and_email_check():
    c = app(cors_origins=[], api_key="k")
    assert c.post("/v1/email/check", json={"subject": "s", "body": "b"}).status_code == 401
    assert c.post("/v1/meetings/x/chat", json={"message": "hi"}).status_code == 401


def test_api_key_from_env(monkeypatch):
    monkeypatch.setenv("COPILOT_API_KEY", "envkey")
    monkeypatch.setattr(api, "load_dotenv", lambda *a, **k: None)
    c = TestClient(api.create_app(FAKE))
    assert c.get("/v1/scenarios").status_code == 401 and c.get("/v1/scenarios", headers={"X-API-Key": "envkey"}).status_code == 200


def test_no_key_configured_means_open():
    assert app(cors_origins=[], api_key="").get("/v1/scenarios").status_code == 200


def test_errors_share_one_shape():
    c = app(cors_origins=[], api_key="")
    r = c.post("/v1/meetings/nope/chat", json={"message": "x"})
    assert r.status_code == 404 and set(r.json()) == {"error"}
    assert c.get("/v1/scenarios/nope").json()["error"].startswith("Unknown scenario")
    assert c.post("/v1/email/check", json={"subject": "s"}).status_code == 422  # validation errors keep FastAPI's shape


# -- email re-check -------------------------------------------------------------------------------------
def test_review_email_function(icap):
    summary = MeetingSummary(**icap.golden_summary.model_dump())
    pol = EmailPolicy.build(icap.recommendations, summary)
    ok = review_email("Recap", GOOD_BODY, pol)
    assert ok.passed and ok.suggested_body is None and ok.fixes == []
    bad = review_email("Recap", BAD_BODY, pol)
    assert not bad.passed and {c.name for c in bad.checks if not c.passed} >= {"no_markdown", "no_jira_keys", "no_meeting_offer"}
    assert bad.fixes and "SUP-48211" not in bad.suggested_body and "**" not in bad.suggested_body
    assert review_email("Recap", bad.suggested_body, pol).passed  # applying the suggestion fixes it


def test_stateless_email_check_endpoint(icap):
    c = app(cors_origins=[], api_key="")
    recs = json.loads(icap.recommendations.model_dump_json())
    ok = c.post("/v1/email/check", json={"subject": "Recap", "body": GOOD_BODY, "recommendations": recs}).json()
    assert ok["passed"] is True and ok["suggested_body"] is None
    bad = c.post("/v1/email/check", json={"subject": "Recap", "body": BAD_BODY, "recommendations": recs}).json()
    assert bad["passed"] is False and bad["suggested_body"] and any(not x["passed"] for x in bad["checks"])


def test_email_check_respects_signature_and_summary(icap, vdi):
    c = app(cors_origins=[], api_key="")
    offer = "Hi Dana,\n\nHappy to jump on a quick call."
    assert c.post("/v1/email/check", json={"subject": "s", "body": offer}).json()["passed"] is False
    sig = "Best,\nSam " + "word " * 200
    long = c.post("/v1/email/check", json={"subject": "s", "body": "Hi Dana,\n\nShort.\n\n" + sig, "signature": sig}).json()
    assert next(x for x in long["checks"] if x["name"] == "long_email")["passed"]  # signature not counted
    summary = json.loads(MeetingSummary(**vdi.golden_summary.model_dump()).model_dump_json())
    scope = c.post("/v1/email/check", json={"subject": "s", "body": "Hi Marcus,\n\nWe looked at the logs.", "summary": summary}).json()
    assert any(x["name"] == "third_party_scope_statement" and not x["passed"] for x in scope["checks"])


def test_meeting_scoped_email_check_uses_stored_context(icap):
    c = app(cors_origins=[], api_key="")
    ctx = c.get("/v1/scenarios/icap_upload_timeouts").json()
    c.put("/v1/meetings/m", json=ctx)
    assert c.post("/v1/meetings/m/email/check", json={"subject": "Recap", "body": GOOD_BODY}).json()["passed"] is True
    leaked = c.post("/v1/meetings/m/email/check", json={"subject": "Recap", "body": GOOD_BODY + "\n\nSee SUP-48211."}).json()
    assert leaked["passed"] is False
    assert c.post("/v1/meetings/nope/email/check", json={"subject": "s", "body": "b"}).status_code == 404


def test_export_writes_openapi_spec_for_client_generation(tmp_path):
    from callcopilot.app.export_schemas import export
    names = export(tmp_path)
    spec = json.loads((tmp_path / "openapi.json").read_text(encoding="utf-8"))
    assert "openapi" in names and "/v1/email/check" in spec["paths"] and "/v1/meetings/{meeting_id}/chat" in spec["paths"]
    assert (tmp_path / "meeting_context.schema.json").exists()


def test_key_and_cors_together_like_a_real_browser_deployment():
    c = app(cors_origins=[ORIGIN], api_key="k")
    # Preflight carries no key and must still succeed, or the browser never sends the real request.
    pre = c.options("/v1/meetings/m/chat", headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST",
                                                    "Access-Control-Request-Headers": "content-type,x-api-key"})
    assert pre.status_code == 200 and pre.headers["access-control-allow-origin"] == ORIGIN
    # A rejected request still carries CORS headers so the page can read the 401 message.
    denied = c.get("/v1/scenarios", headers={"Origin": ORIGIN})
    assert denied.status_code == 401 and denied.headers["access-control-allow-origin"] == ORIGIN and "error" in denied.json()
    ok = c.get("/v1/scenarios", headers={"Origin": ORIGIN, "X-API-Key": "k"})
    assert ok.status_code == 200 and ok.headers["access-control-allow-origin"] == ORIGIN


def test_chat_stream_works_through_cors_and_key():
    c = app(cors_origins=[ORIGIN], api_key="k")
    h = {"Origin": ORIGIN, "X-API-Key": "k"}
    c.put("/v1/meetings/m", json=c.get("/v1/scenarios/icap_upload_timeouts", headers=h).json(), headers=h)
    r = c.post("/v1/meetings/m/chat", json={"message": "What did the customer say about the version?"}, headers=h)
    assert r.headers["access-control-allow-origin"] == ORIGIN and r.headers["content-type"].startswith("text/event-stream")
    assert "event: done" in r.text and "5.8.0" in r.text


def test_demo_page_only_calls_endpoints_that_exist():
    """The static demo page must stay in sync with the API it renders."""
    import re
    page = (api.STATIC / "demo.html").read_text(encoding="utf-8")
    called = {re.sub(r"\$\{[^}]*\}", "x", m).split("?")[0] for m in re.findall(r"/v1/[A-Za-z0-9_/${}.\-]+", page)}
    routes = [re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", path) + "$") for path in api.create_app(FAKE, cors_origins=[], api_key="").openapi()["paths"]]
    assert called and all(any(r.match(p) for r in routes) for p in called), sorted(called)
    for needle in ("Draft email", "Generate summary", "The AI chooses the format and length", "Outside OPSWAT's scope", "Support (you)", "data-copy"):
        assert needle in page
    for gone in ("o-courtesy", "o-meeting", "o-words", "allow meeting offer", "max words"):  # the AI decides; no email settings in the UI
        assert gone not in page
