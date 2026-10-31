"""HTTP API (FastAPI). Run: `callcopilot serve` (or `uvicorn callcopilot.api:app`). Docs at /docs.

Stateless endpoints take everything in the body; chat is per-meeting state held in memory.
Front-end integration notes: docs/frontend.md.

Optional environment variables (blank = off):
  COPILOT_CORS_ORIGINS   comma-separated allowed browser origins, or * for any
  COPILOT_API_KEY        if set, every /v1 request must send it in the X-API-Key header
"""
from __future__ import annotations

import hmac
import os
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from callcopilot.checks import EmailPolicy, check_recommendations
from callcopilot.contracts import (
    EmailDraft, MeetingContext, MeetingSummary, RecommendationSet, StyleProfile, Transcript,
)
from callcopilot.llm import ConfigError, LLMConfig, LLMError, load_config, load_dotenv
from callcopilot.modules.chatbot import ChatSession
from callcopilot.modules.email import EmailReview, review_email
from callcopilot.pipeline import make_llm, run_email, run_summary
from callcopilot.sources import list_scenarios, load_scenario
from callcopilot.tools import FixtureToolBackend

STATIC = Path(__file__).parent / "static"


class SummaryRequest(BaseModel):
    transcript: Transcript
    recommendations: RecommendationSet | None = None  # triaged (duplicates / unrelated items flagged) when given


class EmailRequest(BaseModel):
    transcript: Transcript
    style_profile: StyleProfile
    recommendations: RecommendationSet | None = None
    summary: MeetingSummary | None = None  # generated if omitted


class EmailCheckRequest(BaseModel):
    """An email (typically the agent's edited draft) to check against the hard rules."""
    subject: str
    body: str
    recommendations: RecommendationSet | None = None  # defines which links are allowed
    summary: MeetingSummary | None = None  # enables the third-party scope check
    signature: str = ""  # not counted towards the length ceiling


class MeetingEmailCheckRequest(BaseModel):
    subject: str
    body: str


class EvaluateRequest(BaseModel):
    recommendations: RecommendationSet
    transcript: Transcript | None = None


class ChatRequest(BaseModel):
    message: str


def _origins_from_env() -> list[str]:
    return [o.strip() for o in (os.environ.get("COPILOT_CORS_ORIGINS") or "").split(",") if o.strip()]


def create_app(config: LLMConfig | None = None, backend=None, *, cors_origins: list[str] | None = None,
               api_key: str | None = None) -> FastAPI:
    load_dotenv()
    cors_origins = _origins_from_env() if cors_origins is None else cors_origins
    api_key = (os.environ.get("COPILOT_API_KEY") or "").strip() if api_key is None else api_key

    app = FastAPI(title="Call Copilot modules", version="0.1.0")
    if cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=cors_origins, allow_methods=["*"], allow_headers=["*"])
    state: dict = {"config": config, "meetings": {}, "sessions": {}}
    backend = backend or FixtureToolBackend()

    def cfg() -> LLMConfig:
        if state["config"] is None:
            state["config"] = load_config()
        return state["config"]

    def require_key(x_api_key: str | None = Header(default=None)) -> None:
        if api_key and not hmac.compare_digest((x_api_key or "").encode(), api_key.encode()):
            raise HTTPException(401, "Missing or invalid X-API-Key header.")

    router = APIRouter(dependencies=[Depends(require_key)])

    # One error shape for the UI: {"error": "..."} (request-validation errors stay FastAPI's 422 {"detail": [...]}).
    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_, exc):
        return JSONResponse({"error": str(exc.detail)}, status_code=exc.status_code)

    @app.exception_handler(ConfigError)
    async def _config_error(_, exc):
        return JSONResponse({"error": str(exc)}, status_code=503)

    @app.exception_handler(LLMError)
    async def _llm_error(_, exc):
        return JSONResponse({"error": str(exc)}, status_code=502)

    @app.get("/health")
    def health():
        try:
            return {"status": "ok", "ai": cfg().describe(), "auth": "api-key" if api_key else "none"}
        except ConfigError as exc:
            return JSONResponse({"status": "misconfigured", "error": str(exc)}, status_code=503)

    # -- stateless ---------------------------------------------------------------------------
    @router.post("/v1/summary", response_model=MeetingSummary)
    def summary(req: SummaryRequest):
        return run_summary(req.transcript, cfg(), recommendations=req.recommendations)

    @router.post("/v1/email", response_model=EmailDraft)
    def email(req: EmailRequest):
        llm = make_llm(cfg(), req.transcript)
        s = req.summary or run_summary(req.transcript, cfg(), llm, req.recommendations)
        return run_email(req.transcript, s, req.style_profile, req.recommendations, cfg(), llm)

    @router.post("/v1/email/check", response_model=EmailReview)
    def email_check(req: EmailCheckRequest):
        pol = EmailPolicy.build(req.recommendations, req.summary)
        return review_email(req.subject, req.body, pol, req.signature)

    @router.post("/v1/recommendations/evaluate")
    def evaluate(req: EvaluateRequest):
        results = check_recommendations(req.recommendations, req.transcript)
        return {"passed": all(r.passed or r.soft for r in results),
                "checks": [{"name": r.name, "passed": r.passed, "scope": r.scope, "details": r.details} for r in results]}

    # -- per-meeting (chat) --------------------------------------------------------------------
    def meeting(meeting_id: str) -> MeetingContext:
        if meeting_id not in state["meetings"]:
            raise HTTPException(404, f"Unknown meeting '{meeting_id}'. PUT /v1/meetings/{meeting_id} first.")
        return state["meetings"][meeting_id]

    @router.put("/v1/meetings/{meeting_id}")
    def put_meeting(meeting_id: str, ctx: MeetingContext):
        state["meetings"][meeting_id] = ctx
        state["sessions"].pop(meeting_id, None)
        return {"meeting_id": meeting_id, "case_id": ctx.transcript.metadata.case_id}

    @router.post("/v1/meetings/{meeting_id}/summary", response_model=MeetingSummary)
    def meeting_summary(meeting_id: str):
        ctx = meeting(meeting_id)
        ctx.summary = run_summary(ctx.transcript, cfg(), recommendations=ctx.recommendations)
        return ctx.summary

    @router.post("/v1/meetings/{meeting_id}/email", response_model=EmailDraft)
    def meeting_email(meeting_id: str):
        ctx = meeting(meeting_id)
        if ctx.style_profile is None:
            raise HTTPException(422, "This meeting has no style_profile; PUT it with one first.")
        llm = make_llm(cfg(), ctx.transcript)
        ctx.summary = ctx.summary or run_summary(ctx.transcript, cfg(), llm, ctx.recommendations)
        ctx.email_draft = run_email(ctx.transcript, ctx.summary, ctx.style_profile, ctx.recommendations, cfg(), llm)
        return ctx.email_draft

    @router.post("/v1/meetings/{meeting_id}/email/check", response_model=EmailReview)
    def meeting_email_check(meeting_id: str, req: MeetingEmailCheckRequest):
        ctx = meeting(meeting_id)
        pol = EmailPolicy.build(ctx.recommendations, ctx.summary)
        return review_email(req.subject, req.body, pol, ctx.style_profile.signature if ctx.style_profile else "")

    @router.post("/v1/meetings/{meeting_id}/chat")
    def chat(meeting_id: str, req: ChatRequest):
        ctx = meeting(meeting_id)
        session = state["sessions"].get(meeting_id)
        if session is None:
            session = state["sessions"][meeting_id] = ChatSession(ctx, make_llm(cfg(), ctx.transcript), backend, cfg())

        def sse():
            for ev in session.ask(req.message):
                yield f"event: {ev.type}\ndata: {ev.model_dump_json(exclude_none=True)}\n\n"

        return StreamingResponse(sse(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @router.delete("/v1/meetings/{meeting_id}/chat")
    def reset_chat(meeting_id: str):
        state["sessions"].pop(meeting_id, None)
        return {"reset": True}

    # -- demo helpers ----------------------------------------------------------------------------
    @router.get("/v1/scenarios")
    def scenarios():
        return [{"id": s, "case_id": load_scenario(s).transcript.metadata.case_id,
                 "customer": load_scenario(s).transcript.metadata.customer_name} for s in list_scenarios()]

    @router.get("/v1/scenarios/{scenario_id}", response_model=MeetingContext)
    def scenario(scenario_id: str):
        try:
            sc = load_scenario(scenario_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc.args[0])) from exc
        return MeetingContext(transcript=sc.transcript, recommendations=sc.recommendations, style_profile=sc.style)

    app.include_router(router)

    @app.get("/", response_class=HTMLResponse)
    def index():
        return (STATIC / "demo.html").read_text(encoding="utf-8")

    return app


app = create_app()
