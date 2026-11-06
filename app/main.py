"""FastAPI app: /chat /health /metrics /escalations (ARCHITECTURE.md §10).

Every shared resource (embedder, retriever, llm client, session store,
escalation handler, agent, and a shared Redis client for liveness/metrics)
is constructed exactly once in `lifespan()` and stored on `app.state` — no
route handler ever constructs its own client, per §10.2's wiring shape.
Route handlers are plain `def`, not `async def`: every dependency here
(Groq, Weaviate, Neo4j, Redis) uses a synchronous client, and FastAPI runs
sync route handlers in a thread pool automatically, so a slow call to any
of them doesn't block the event loop for other requests.
"""

from __future__ import annotations

import datetime
import hmac
from contextlib import asynccontextmanager

import redis as redis_lib
from fastapi import FastAPI, File, Header, HTTPException, Query, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Gauge, generate_latest

import app.config as cfg
from agent.agent import Agent
from agent.attachments import AttachmentRejected, AttachmentStore
from agent.conversation_history import ConversationHistoryStore
from agent.escalation import EscalationHandler
from agent.feedback import FeedbackHandler
from agent.llm_client import DAILY_CALL_COUNTER_KEY_PREFIX, LLMClient
from agent.session_state import SessionStateStore
from app.schemas import (
    AgentResponse,
    AnalyticsSummary,
    AttachmentUploadResponse,
    ChatRequest,
    EscalationPacket,
    EscalationStatus,
    FeedbackEntry,
    FeedbackRequest,
    IdentifySessionRequest,
    MessageEntry,
    RespondToEscalationRequest,
)
from ingestion.embedder import Embedder
from retrieval.graph_retriever import GraphRetriever
from retrieval.hybrid_retriever import HybridRetriever
from retrieval.weaviate_retriever import WeaviateRetriever
from logging_config import configure_logging

configure_logging(cfg.LOG_LEVEL)

_METRICS_REGISTRY = CollectorRegistry()
_GROQ_DAILY_CALLS = Gauge(
    "groq_daily_calls_total",
    "Number of Groq API calls made today (UTC), from agent.llm_client's shared counter.",
    registry=_METRICS_REGISTRY,
)
_ESCALATIONS_TOTAL = Gauge(
    "escalations_total", "Total escalations ever recorded.", registry=_METRICS_REGISTRY
)
_ESCALATIONS_SLACK_FAILED = Gauge(
    "escalations_slack_undelivered_total",
    "Escalations whose Slack notification failed and may need manual resending.",
    registry=_METRICS_REGISTRY,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    embedder = Embedder(model_name=cfg.EMBEDDING_MODEL_NAME)
    retriever = HybridRetriever(
        vec=WeaviateRetriever(embedder, cfg.WEAVIATE_HOST, cfg.WEAVIATE_PORT, cfg.WEAVIATE_GRPC_PORT, cfg.WEAVIATE_COLLECTION_NAME),
        graph=GraphRetriever(cfg.NEO4J_URI, cfg.NEO4J_USERNAME, cfg.NEO4J_PASSWORD),
        top_k=cfg.RETRIEVAL_TOP_K,
    )
    llm = LLMClient(
        api_key=cfg.GROQ_API_KEY,
        default_model=cfg.GROQ_MODEL_DEFAULT,
        upgrade_model=cfg.GROQ_MODEL_UPGRADE,
        redis_url=cfg.REDIS_URL,
    )
    session_store = SessionStateStore(redis_url=cfg.REDIS_URL)
    escalation = EscalationHandler(store_path=cfg.ESCALATION_STORE_PATH, slack_webhook_url=cfg.SLACK_WEBHOOK_URL)
    # Phase C: same SQLite file/pattern as escalations, a separate table —
    # see agent/feedback.py's docstring for why not a third storage mechanism.
    feedback = FeedbackHandler(store_path=cfg.ESCALATION_STORE_PATH)
    # Phase D: local disk — see agent/attachments.py's docstring for why.
    attachments = AttachmentStore(store_path=cfg.ATTACHMENTS_STORE_PATH)
    # Phase D: same SQLite file/pattern again — see
    # agent/conversation_history.py's docstring for why.
    conversation_history = ConversationHistoryStore(store_path=cfg.ESCALATION_STORE_PATH)

    app.state.agent = Agent(
        retriever=retriever, llm=llm, embedder=embedder, session_store=session_store, escalation=escalation
    )
    app.state.feedback = feedback
    app.state.attachments = attachments
    app.state.conversation_history = conversation_history
    app.state.redis = redis_lib.Redis.from_url(cfg.REDIS_URL, decode_responses=True, socket_connect_timeout=2)

    yield

    retriever.close()
    app.state.redis.close()


app = FastAPI(title="Agentic Customer Support Assistant — backend", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cfg.CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/chat", response_model=AgentResponse)
def chat(request: ChatRequest) -> AgentResponse:
    response = app.state.agent.handle_message(request.query, request.session_id, attachment_id=request.attachment_id)
    # Phase D: logged here, not inside Agent.handle_message() — this is a
    # pure side-channel transcript record, not a routing input, so it has
    # no business inside agent.py's core decision logic. See
    # agent/conversation_history.py's docstring.
    app.state.conversation_history.record_message(session_id=request.session_id, role="user", text=request.query)
    app.state.conversation_history.record_message(
        session_id=request.session_id, role="assistant", text=response.detail,
        route=response.route, intent=response.intent,
    )
    return response


@app.get("/health")
def health(response: Response):
    agent = app.state.agent
    checks = {
        "weaviate": agent.retriever.vec.is_ready(),
        "neo4j": agent.retriever.graph.is_reachable(),
        "redis": _check_redis(),
    }
    all_ok = all(checks.values())
    response.status_code = 200 if all_ok else 503
    return {"status": "ok" if all_ok else "degraded", "dependencies": checks}


def _check_redis() -> bool:
    try:
        return bool(app.state.redis.ping())
    except Exception:
        return False


@app.get("/metrics")
def metrics():
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    key = f"{DAILY_CALL_COUNTER_KEY_PREFIX}{today}"
    try:
        call_count = int(app.state.redis.get(key) or 0)
    except Exception:
        call_count = 0
    _GROQ_DAILY_CALLS.set(call_count)

    escalations = app.state.agent.escalation.list_escalations()
    _ESCALATIONS_TOTAL.set(len(escalations))
    _ESCALATIONS_SLACK_FAILED.set(sum(1 for e in escalations if not e.slack_delivered))

    return Response(generate_latest(_METRICS_REGISTRY), media_type=CONTENT_TYPE_LATEST)


@app.get("/escalations", response_model=list[EscalationPacket])
def list_escalations(slack_delivered: bool = Query(default=None)) -> list:
    return app.state.agent.escalation.list_escalations(slack_delivered=slack_delivered)


@app.post("/feedback", response_model=FeedbackEntry)
def submit_feedback(request: FeedbackRequest) -> FeedbackEntry:
    return app.state.feedback.record(
        session_id=request.session_id,
        query=request.query,
        response_text=request.response_text,
        route=request.route,
        intent=request.intent,
        thumbs_up=request.thumbs_up,
    )


@app.get("/feedback", response_model=list[FeedbackEntry])
def list_feedback() -> list:
    return app.state.feedback.list_feedback()


@app.post("/attachments", response_model=AttachmentUploadResponse)
def upload_attachment(file: UploadFile = File(...)) -> AttachmentUploadResponse:
    content = file.file.read()
    try:
        stored = app.state.attachments.save(content, file.content_type)
    except AttachmentRejected as e:
        raise HTTPException(status_code=400, detail=str(e))
    return AttachmentUploadResponse(
        attachment_id=stored.attachment_id, url=f"/attachments/{stored.attachment_id}"
    )


@app.get("/attachments/{attachment_id}")
def get_attachment(attachment_id: str):
    path = app.state.attachments.path_for(attachment_id)
    if path is None:
        raise HTTPException(status_code=404, detail="Attachment not found.")
    return FileResponse(path)


@app.post("/conversations/identify")
def identify_conversation(request: IdentifySessionRequest) -> dict:
    linked = app.state.conversation_history.identify_session(request.session_id, request.email)
    return {"linked_messages": linked}


@app.get("/conversations", response_model=list[MessageEntry])
def get_conversations(session_id: str = Query(default=None), email: str = Query(default=None)) -> list:
    if email:
        return app.state.conversation_history.list_by_email(email)
    if session_id:
        return app.state.conversation_history.list_by_session(session_id)
    raise HTTPException(status_code=400, detail="Provide session_id or email.")


@app.get("/analytics", response_model=AnalyticsSummary)
def get_analytics(top_intents_limit: int = Query(default=10)) -> AnalyticsSummary:
    history = app.state.conversation_history
    return AnalyticsSummary(
        top_intents=history.top_intents(limit=top_intents_limit),
        hourly_volume=history.hourly_volume(),
        dropoff=history.dropoff_summary(),
    )


def _check_admin_token(x_admin_token: str = None) -> None:
    # Phase D (live human handoff, see ANALYSIS.md's Phase D entry):
    # FAILS CLOSED — an unconfigured ADMIN_API_TOKEN refuses every request
    # rather than silently becoming open, unlike every other admin-dashboard
    # route (Phase C), which is unauthenticated by design. This one is a
    # WRITE that appears in a customer's live conversation, confirmed with
    # the user as worth a minimal gate. `hmac.compare_digest` avoids a
    # timing side-channel on the comparison.
    if not cfg.ADMIN_API_TOKEN or not x_admin_token or not hmac.compare_digest(x_admin_token, cfg.ADMIN_API_TOKEN):
        raise HTTPException(status_code=403, detail="Missing or invalid admin token.")


@app.post("/escalations/{escalation_id}/respond", response_model=EscalationPacket)
def respond_to_escalation(
    escalation_id: str, request: RespondToEscalationRequest, x_admin_token: str = Header(default=None),
) -> EscalationPacket:
    _check_admin_token(x_admin_token)
    updated = app.state.agent.escalation.respond(escalation_id, status=request.status, reply=request.reply)
    if updated is None:
        raise HTTPException(status_code=404, detail="Escalation not found.")
    if request.reply:
        # The reply also becomes part of the durable transcript (Phase
        # D-3's messages table) — it survives a refresh and shows up in
        # chat history, not just a poll-only, here-and-gone artifact.
        app.state.conversation_history.record_message(
            session_id=updated.session_id, role="human_agent", text=request.reply, route="escalate",
        )
    return updated


@app.get("/escalations/{escalation_id}/status", response_model=EscalationStatus)
def get_escalation_status(escalation_id: str) -> EscalationStatus:
    # Deliberately unauthenticated — this is the customer-facing poll, and
    # EscalationStatus only ever exposes that one escalation's own status
    # fields (see its docstring), not the full packet.
    status = app.state.agent.escalation.get_status(escalation_id)
    if status is None:
        raise HTTPException(status_code=404, detail="Escalation not found.")
    return status
