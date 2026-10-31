"""HTTP API (FastAPI). Run: `callcopilot serve` (or `uvicorn callcopilot.api:app`). Docs at /docs.

Stateless endpoints take everything in the body; chat is per-meeting state held in memory.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from callcopilot.checks import check_recommendations
from callcopilot.contracts import (
    EmailDraft, EmailOptions, MeetingContext, MeetingSummary, RecommendationSet, StyleProfile, Transcript,
)
from callcopilot.llm import ConfigError, LLMConfig, LLMError, load_config, load_dotenv
from callcopilot.modules.chatbot import ChatSession
from callcopilot.pipeline import make_llm, run_email, run_summary
from callcopilot.sources import list_scenarios, load_scenario
from callcopilot.tools import FixtureToolBackend

STATIC = Path(__file__).parent / "static"


class SummaryRequest(BaseModel):
    transcript: Transcript


class EmailRequest(BaseModel):
    transcript: Transcript
    style_profile: StyleProfile
    recommendations: RecommendationSet | None = None
    summary: MeetingSummary | None = None  # generated if omitted
    options: EmailOptions | None = None


class EvaluateRequest(BaseModel):
    recommendations: RecommendationSet
    transcript: Transcript | None = None


class ChatRequest(BaseModel):
    message: str


class MeetingEmailRequest(BaseModel):
    options: EmailOptions | None = None


def create_app(config: LLMConfig | None = None, backend=None) -> FastAPI:
    app = FastAPI(title="Call Copilot modules", version="0.1.0")
    state: dict = {"config": config, "meetings": {}, "sessions": {}}
    backend = backend or FixtureToolBackend()

    def cfg() -> LLMConfig:
        if state["config"] is None:
            load_dotenv()
            state["config"] = load_config()
        return state["config"]

    @app.exception_handler(ConfigError)
    async def _config_error(_, exc):
        return JSONResponse({"error": str(exc)}, status_code=503)

    @app.exception_handler(LLMError)
    async def _llm_error(_, exc):
        return JSONResponse({"error": str(exc)}, status_code=502)

    @app.get("/health")
    def health():
        try:
            return {"status": "ok", "ai": cfg().describe()}
        except ConfigError as exc:
            return JSONResponse({"status": "misconfigured", "error": str(exc)}, status_code=503)

    # -- stateless ---------------------------------------------------------------------------
    @app.post("/v1/summary", response_model=MeetingSummary)
    def summary(req: SummaryRequest):
        return run_summary(req.transcript, cfg())

    @app.post("/v1/email", response_model=EmailDraft)
    def email(req: EmailRequest):
        llm = make_llm(cfg(), req.transcript)
        s = req.summary or run_summary(req.transcript, cfg(), llm)
        return run_email(req.transcript, s, req.style_profile, req.recommendations, cfg(), req.options, llm)

    @app.post("/v1/recommendations/evaluate")
    def evaluate(req: EvaluateRequest):
        results = check_recommendations(req.recommendations, req.transcript)
        return {"passed": all(r.passed or r.soft for r in results),
                "checks": [{"name": r.name, "passed": r.passed, "scope": r.scope, "details": r.details} for r in results]}

    # -- per-meeting (chat) --------------------------------------------------------------------
    def meeting(meeting_id: str) -> MeetingContext:
        if meeting_id not in state["meetings"]:
            raise HTTPException(404, f"Unknown meeting '{meeting_id}'. PUT /v1/meetings/{meeting_id} first.")
        return state["meetings"][meeting_id]

    @app.put("/v1/meetings/{meeting_id}")
    def put_meeting(meeting_id: str, ctx: MeetingContext):
        state["meetings"][meeting_id] = ctx
        state["sessions"].pop(meeting_id, None)
        return {"meeting_id": meeting_id, "case_id": ctx.transcript.metadata.case_id}

    @app.post("/v1/meetings/{meeting_id}/summary", response_model=MeetingSummary)
    def meeting_summary(meeting_id: str):
        ctx = meeting(meeting_id)
        ctx.summary = run_summary(ctx.transcript, cfg())
        return ctx.summary

    @app.post("/v1/meetings/{meeting_id}/email", response_model=EmailDraft)
    def meeting_email(meeting_id: str, req: MeetingEmailRequest | None = None):
        ctx = meeting(meeting_id)
        if ctx.style_profile is None:
            raise HTTPException(422, "This meeting has no style_profile; PUT it with one first.")
        llm = make_llm(cfg(), ctx.transcript)
        ctx.summary = ctx.summary or run_summary(ctx.transcript, cfg(), llm)
        ctx.email_options = (req.options if req else None) or ctx.email_options
        ctx.email_draft = run_email(ctx.transcript, ctx.summary, ctx.style_profile, ctx.recommendations, cfg(), ctx.email_options, llm)
        return ctx.email_draft

    @app.post("/v1/meetings/{meeting_id}/chat")
    def chat(meeting_id: str, req: ChatRequest):
        ctx = meeting(meeting_id)
        session = state["sessions"].get(meeting_id)
        if session is None:
            session = state["sessions"][meeting_id] = ChatSession(ctx, make_llm(cfg(), ctx.transcript), backend, cfg())

        def sse():
            for ev in session.ask(req.message):
                yield f"event: {ev.type}\ndata: {ev.model_dump_json(exclude_none=True)}\n\n"

        return StreamingResponse(sse(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.delete("/v1/meetings/{meeting_id}/chat")
    def reset_chat(meeting_id: str):
        state["sessions"].pop(meeting_id, None)
        return {"reset": True}

    # -- demo helpers ----------------------------------------------------------------------------
    @app.get("/v1/scenarios")
    def scenarios():
        return [{"id": s, "case_id": load_scenario(s).transcript.metadata.case_id,
                 "customer": load_scenario(s).transcript.metadata.customer_name} for s in list_scenarios()]

    @app.get("/v1/scenarios/{scenario_id}", response_model=MeetingContext)
    def scenario(scenario_id: str):
        try:
            sc = load_scenario(scenario_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc.args[0])) from exc
        return MeetingContext(transcript=sc.transcript, recommendations=sc.recommendations, style_profile=sc.style)

    @app.get("/", response_class=HTMLResponse)
    def index():
        return (STATIC / "demo.html").read_text(encoding="utf-8")

    return app


app = create_app()
