"""Every Pydantic request/response model, exactly per ARCHITECTURE.md §9.

Pulled forward into M4 (Owner C) because agent/agent.py needs a return type
now — M5 (Owner D, real app/ scope) imports these, it does not recreate
them. Pure data contracts only: no route handlers, no business logic.
"""

from typing import Literal, Optional

from pydantic import BaseModel


class ChatRequest(BaseModel):
    query: str
    session_id: str
    # Phase D: set only when the customer attached an image to THIS turn
    # (see agent/attachments.py's docstring) — never read by the text
    # routing/retrieval/generation pipeline, only passed through to an
    # escalation packet if this turn happens to escalate.
    attachment_id: Optional[str] = None


class SessionState(BaseModel):
    session_id: str
    # "awaiting_clarification" added in M9: a one-shot human-worded
    # clarifying question asked before ever escalating on low
    # intent-confidence or low retrieval-relevance (see ANALYSIS.md's M9
    # entry). "awaiting_contact" was defined back in M4 but never actually
    # used until M9 — it's now a real one-shot round-trip asking for
    # contact info BEFORE the escalation packet is built/stored/posted to
    # Slack, replacing M5's same-turn cosmetic ask that could never be
    # answered before Slack already fired.
    pending: Optional[Literal["awaiting_slot", "awaiting_clarification", "awaiting_contact"]]
    pending_context: dict
    turn_count: int
    # M11: the most recently completed track_order/cancel_order action's
    # order_id/intent/status, kept for exactly one follow-up turn (with
    # natural chaining if that follow-up is itself another successful
    # action) so a direct reference ("my order"/"it") or a delivery
    # dispute right after a "delivered" result doesn't get treated as a
    # brand-new request missing its slot. {} means no remembered order.
    # Distinct from `pending_context`: this is a resolved fact kept around
    # for grounding, not an unresolved question being tracked.
    last_order_context: dict = {}


class AgentResponse(BaseModel):
    route: Literal["information", "action", "escalate"]
    intent: str
    intent_confidence: float
    retrieval_confidence: float
    detail: str  # the generated answer, or the tool result
    escalation_id: Optional[str]
    session_id: str


class EscalationPacket(BaseModel):
    escalation_id: str
    session_id: str
    contact: Optional[str]
    contact_captured: bool
    slack_delivered: bool
    query: str
    retrieved_context: list
    reason: str
    timestamp: str
    # M9: a plain-English summary of what the agent tried before
    # escalating (intent classified, whether a clarifying question was
    # asked and what the reply was, etc.) — replaces a bare reason enum
    # in the Slack notification. See ANALYSIS.md's M9 entry.
    attempted_summary: str = ""
    # Phase D: set only when the customer attached an image on the turn
    # that triggered this escalation — a relative URL path the frontend/
    # Slack can resolve via GET /attachments/{id}. Never analyzed by the
    # LLM, only forwarded for a human to look at.
    attachment_id: Optional[str] = None
    # Phase D (live human handoff, see ANALYSIS.md's Phase D entry):
    # "waiting" until a human acts on it via the admin dashboard's
    # POST /escalations/{id}/respond, updated to "picked_up"/"responded".
    # Slack itself stays one-way (unchanged) — this is what actually closes
    # the loop back to the customer.
    human_status: Literal["waiting", "picked_up", "responded"] = "waiting"
    human_reply: Optional[str] = None
    responded_at: Optional[str] = None


class FeedbackRequest(BaseModel):
    session_id: str
    query: str
    response_text: str
    route: Literal["information", "action", "escalate"]
    intent: str
    thumbs_up: bool


class FeedbackEntry(BaseModel):
    feedback_id: str
    session_id: str
    query: str
    response_text: str
    route: Literal["information", "action", "escalate"]
    intent: str
    thumbs_up: bool
    timestamp: str


class AttachmentUploadResponse(BaseModel):
    attachment_id: str
    url: str


class MessageEntry(BaseModel):
    message_id: str
    session_id: str
    # Phase D: null until the customer opts in via POST /conversations/identify
    # (see agent/conversation_history.py's docstring) — an unverified email,
    # by design (see ANALYSIS.md's Phase D entry for the tradeoff).
    customer_email: Optional[str]
    # Phase D (live human handoff): "human_agent" is a reply logged via
    # POST /escalations/{id}/respond — visually distinguished from the
    # bot's own "assistant" messages in the frontend, since a customer
    # should always be able to tell whether they're talking to the AI or
    # a person.
    role: Literal["user", "assistant", "human_agent"]
    text: str
    route: Optional[Literal["information", "action", "escalate"]]
    intent: Optional[str]
    timestamp: str


class IdentifySessionRequest(BaseModel):
    session_id: str
    email: str


class RespondToEscalationRequest(BaseModel):
    status: Literal["picked_up", "responded"]
    reply: Optional[str] = None


class EscalationStatus(BaseModel):
    # Deliberately minimal — this is the customer-facing poll response, not
    # the full EscalationPacket (no query/contact/retrieved_context): an
    # anonymous poller who only knows their own escalation_id shouldn't get
    # back more than their own status/reply.
    escalation_id: str
    human_status: Literal["waiting", "picked_up", "responded"]
    human_reply: Optional[str] = None
    responded_at: Optional[str] = None


class IntentCount(BaseModel):
    intent: str
    count: int


class HourlyCount(BaseModel):
    hour: int  # 0-23, UTC
    count: int


class DropoffSummary(BaseModel):
    single_turn_sessions: int
    multi_turn_sessions: int


class AnalyticsSummary(BaseModel):
    top_intents: list[IntentCount]
    hourly_volume: list[HourlyCount]
    dropoff: DropoffSummary


class HeldoutExample(BaseModel):
    id: str
    input: str
    expected_answer: str
    expected_route: Literal["information", "action", "escalate"]
    metadata: dict  # category, difficulty, etc.
