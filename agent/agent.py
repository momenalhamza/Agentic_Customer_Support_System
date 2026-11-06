"""The routing decision — ties retrieval, intent classification, generation,
groundedness, tools, and escalation together (ARCHITECTURE.md §8).

Guardrails (§8.1) are enforced here as hard Python-level structure, not
configuration knobs:
- Groundedness retry: `_answer_information` calls generate+check at most
  twice (once, then once more after `broaden_via_graph`) — there is no loop
  variable or config value that could raise this; it's two sequential calls
  written directly in the function body.
- Clarify attempts (slot, general-clarification, and contact — all three
  follow the identical pattern): a slot/clarification/contact-ask can only
  ever be pending once. `_resolve_pending_slot`/`_resolve_pending_clarification`/
  `_resolve_pending_contact` are the only places that consume their
  respective pending state, and each always clears it (success or
  escalate) — none ever re-arms its own kind of question, so a second miss
  can only ever end in escalation.

Resolved routing ambiguities (documented fully in ANALYSIS.md's M4 entry):
- "high intent_confidence" (§8) = `intent_confidence >= MIN_INTENT_CONFIDENCE`
  (new config value, not in ARCHITECTURE.md's original config.py snippet).
- "high ... retrieval_confidence" (§8) is read as "at or above
  MIN_ESCALATION_DENSE_FLOOR AND MIN_ESCALATION_BM25_FLOOR" (M8 revision —
  originally "not Low" per MIN_CONFIDENCE_MED/HIGH's relative bucketing;
  M6's eval found that too permissive for fluent-but-irrelevant input, see
  ANALYSIS.md's M8 entry for the evidence and why two independent floors,
  not one, are needed). The groundedness check remains the downstream
  safety net for anything that does pass this gate.
- §8's b2 "still incomplete after clarify" ambiguity is resolved as
  ESCALATE (not "proceed with a details-unconfirmed flag") — consistent
  with the project's general escalate-on-uncertainty posture (§14).

M9 revision — two real behavior changes, both evidence-driven (see
ANALYSIS.md's M9 entry for the full "hi" bug investigation):
1. Pure greetings/small-talk/disengagement (`agent/smalltalk.py`) are
   detected and answered BEFORE intent classification or retrieval ever
   run — they were previously falling through to the low-confidence
   escalate path because neither the classifier nor the retriever has
   anything real to work with for "hi", and correctly reports as much.
2. Low intent-confidence and low retrieval-relevance no longer escalate
   immediately — one human-worded clarifying question is asked first
   (`awaiting_clarification`, a new pending state). Only if the reply is
   STILL unclear does it escalate. Separately, `awaiting_contact` (defined
   in the schema since M4, never implemented until now) is a real
   round-trip asking for contact info BEFORE the escalation packet is
   built/stored/posted to Slack — replacing M5's same-turn cosmetic ask
   that could never be answered before Slack had already fired. Scoping
   decision: the NEW general clarify-first step applies only to the two
   "can't even tell what they want" gates above — it does NOT apply to
   `order_not_found` or the existing slot-clarify's own exhaustion
   (`clarify_attempt_exhausted_still_missing_slot`), since those already
   represent a well-understood situation or an already-exhausted
   one-shot ask; adding a second, vaguer clarify on top would violate the
   hard-cap-at-1 discipline for no benefit. All escalation paths — new
   and pre-existing — now go through the same `awaiting_contact` step.
"""

from __future__ import annotations

from agent import tools
from agent.delivery_dispute import detect_delivery_dispute
from agent.generator import generate_answer
from agent.groundedness import is_grounded
from agent.intent_classifier import ACTION_INTENTS, classify_intent_and_slots
from agent.language import detect_language
from agent.smalltalk import classify_smalltalk
from agent.translation import translate_to_english
from app.config import MIN_ESCALATION_BM25_FLOOR, MIN_ESCALATION_DENSE_FLOOR, MIN_INTENT_CONFIDENCE
from app.schemas import AgentResponse, SessionState

_REQUIRED_SLOT_BY_INTENT = {
    "cancel_order": "order_id",
    "track_order": "order_id",
}

# Phase B (Arabic support, see ANALYSIS.md's Phase B entry): every piece of
# customer-facing copy this module owns directly (as opposed to
# LLM-generated text, handled in agent/generator.py) gets a hardcoded
# Arabic variant, keyed the same way as _REPLY_BY_CATEGORY in
# agent/smalltalk.py — a fixed, bounded set, not a live translation call.
_CLARIFY_QUESTIONS = {
    "en": {"order_id": "Before I can help with that, could you share your order number?"},
    "ar": {"order_id": "قبل أن أتمكن من مساعدتك في ذلك، هل يمكنك مشاركة رقم طلبك؟"},
}

_GENERIC_SLOT_QUESTION = {
    "en": "Could you share your {slot}?",
    "ar": "هل يمكنك مشاركة {slot}؟",
}

_CLARIFICATION_QUESTION = {
    "en": "Sorry, I didn't quite catch that — could you tell me a bit more about what you're trying to do?",
    "ar": "عذرًا، لم أفهم ذلك تمامًا — هل يمكنك إخباري بمزيد من التفاصيل عمّا تحاول القيام به؟",
}

_CONTACT_QUESTION = {
    "en": "Before they reach out, could you share your email or order number so they can follow up directly?",
    "ar": "قبل أن يتواصلوا معك، هل يمكنك مشاركة بريدك الإلكتروني أو رقم طلبك حتى يتمكنوا من المتابعة مباشرة؟",
}

_DEFAULT_ESCALATION_ACK = {
    "en": "I've connected you with a member of our team who can help with this. ",
    "ar": "لقد قمت بتوصيلك بأحد أعضاء فريقنا الذي يمكنه مساعدتك في هذا الأمر. ",
}


def _pick(language: str, en: str, ar: str) -> str:
    return ar if language == "ar" else en


class Agent:
    def __init__(self, retriever, llm, embedder, session_store, escalation, tools_module=tools):
        self.retriever = retriever
        self.llm = llm
        self.embedder = embedder
        self.session_store = session_store
        self.escalation = escalation
        self.tools = tools_module

    def handle_message(self, query: str, session_id: str, attachment_id: str = None) -> AgentResponse:
        state = self.session_store.get_state(session_id)
        smalltalk = classify_smalltalk(query)
        if smalltalk is not None:
            category, reply = smalltalk
            # Disengagement ("nevermind") always wins, unconditionally, even
            # mid-flow — its meaning is unambiguous regardless of context.
            # Greeting/thanks/farewell are social filler that could double
            # as a (nonsensical) reply to an active pending question, so
            # those only short-circuit when nothing is pending; otherwise
            # they fall through and get treated as the customer's reply,
            # same as any other unclear text (found via live testing: a
            # gibberish message followed by "hi" was resetting to a bare
            # greeting instead of counting as the still-unclear reply to
            # the clarifying question — see ANALYSIS.md's M9 entry).
            if category == "disengagement" or state.pending is None:
                self._save_state(session_id, pending=None, pending_context={}, turn_count=state.turn_count + 1)
                return AgentResponse(
                    route="information",
                    intent="smalltalk",
                    intent_confidence=1.0,
                    retrieval_confidence=0.0,
                    detail=reply,
                    escalation_id=None,
                    session_id=session_id,
                )

        # Phase B (Arabic support): bge-large-en-v1.5 (bge-small-en-v1.5 in
        # the cloud deployment) is English-only and retrieves poorly
        # against an Arabic query embedded as-is (see ANALYSIS.md's Phase B
        # entry for the evidence — wrong-topic chunks, dense/BM25 scores
        # both below the escalation floors, for queries that are perfectly
        # answerable once translated). `retrieval_query` is used ONLY for
        # retrieval; the customer's own `query` and the final answer's
        # language are untouched by this.
        language = detect_language(query)
        retrieval_query = translate_to_english(self.llm, query) if language == "ar" else query
        retrieval_result = self.retriever.retrieve(retrieval_query)
        # Always re-classify the new message first, regardless of pending
        # state — §8's named failure mode is forcing a parse onto a message
        # that's actually the customer changing the subject.
        intent_result = classify_intent_and_slots(self.llm, query)
        next_turn_count = state.turn_count + 1

        if state.pending == "awaiting_slot":
            return self._resolve_pending_slot(query, retrieval_query, session_id, state, intent_result, retrieval_result, next_turn_count, language, attachment_id)
        if state.pending == "awaiting_clarification":
            return self._resolve_pending_clarification(query, retrieval_query, session_id, state, intent_result, retrieval_result, next_turn_count, language, attachment_id)
        if state.pending == "awaiting_contact":
            return self._resolve_pending_contact(query, session_id, state, intent_result, next_turn_count, language)

        return self._route(query, retrieval_query, session_id, intent_result, retrieval_result, next_turn_count, state.last_order_context, language, attachment_id)

    # ------------------------------------------------------------------
    # Pending-state resolution
    # ------------------------------------------------------------------

    def _resolve_pending_slot(self, query, retrieval_query, session_id, state, intent_result, retrieval_result, turn_count, language, attachment_id=None) -> AgentResponse:
        original_intent = state.pending_context.get("original_intent")

        if intent_result.intent != original_intent and intent_result.intent_confidence >= MIN_INTENT_CONFIDENCE:
            # Clearly a different, high-confidence intent — drop the pending
            # question and treat this as a brand-new query instead of
            # force-parsing it as an answer to what we just asked.
            return self._route(query, retrieval_query, session_id, intent_result, retrieval_result, turn_count, state.last_order_context, language, attachment_id)

        merged_slots = {**state.pending_context.get("partial_slots", {}), **intent_result.slots}
        required_slot = _REQUIRED_SLOT_BY_INTENT.get(original_intent)

        if required_slot and merged_slots.get(required_slot):
            return self._do_action(original_intent, merged_slots, session_id, retrieval_result, turn_count, language)

        # The one allowed clarify attempt is used up (we already asked once
        # to reach this pending state) — escalate rather than ask again.
        intent = original_intent or intent_result.intent
        return self._begin_escalation(
            query, session_id, reason="clarify_attempt_exhausted_still_missing_slot",
            retrieved_context=[c.text for c in retrieval_result.chunks], slots=merged_slots,
            intent=intent, intent_confidence=intent_result.intent_confidence,
            retrieval_confidence=retrieval_result.top_dense_score, turn_count=turn_count,
            attempted_summary=(
                f"Customer wanted to {intent}; asked once for their order number; still not provided."
            ),
            language=language,
            attachment_id=attachment_id,
        )

    def _resolve_pending_clarification(self, query, retrieval_query, session_id, state, intent_result, retrieval_result, turn_count, language, attachment_id=None) -> AgentResponse:
        reason = self._low_confidence_reason(intent_result, retrieval_result)
        if reason is None:
            # The clarifying reply resolved it — proceed exactly like a
            # fresh, confident turn (may answer, act, or ask for a slot).
            return self._route_confident(query, retrieval_query, session_id, intent_result, retrieval_result, turn_count, state.last_order_context, language, attachment_id)

        # The one clarify attempt is used up — escalate for real this time.
        # Uses the ORIGINAL query (what the customer actually opened with),
        # not this reply, as the escalation's `query` field — a real gap
        # found during M9's live verification: using the reply here lost
        # the customer's original complaint entirely. The reply itself is
        # still preserved, in the summary.
        original_query = state.pending_context.get("original_query", query)
        return self._begin_escalation(
            original_query, session_id, reason=f"still_unclear_after_clarification_{reason}",
            retrieved_context=[c.text for c in retrieval_result.chunks], slots=intent_result.slots,
            intent=intent_result.intent, intent_confidence=intent_result.intent_confidence,
            retrieval_confidence=retrieval_result.top_dense_score, turn_count=turn_count,
            attempted_summary=(
                self._describe_low_confidence(intent_result, reason)
                + f' Asked one clarifying question; customer replied "{query}", which was still unclear.'
            ),
            language=language,
            attachment_id=attachment_id,
        )

    def _resolve_pending_contact(self, query, session_id, state, intent_result, turn_count, language) -> AgentResponse:
        ctx = state.pending_context
        # The customer was explicitly asked for "email or order number," so
        # an order_id IS acceptable here (unlike the passive check used
        # everywhere else) — see agent/escalation.py's finalize() docstring.
        explicit_contact = (
            intent_result.slots.get("email")
            or intent_result.slots.get("phone")
            or intent_result.slots.get("order_id")
            or self.escalation.check_known_contact({}, query)
        )
        # The one contact-ask attempt is used up either way — finalize now.
        # `attachment_id` (Phase D) is whatever was captured on the ORIGINAL
        # triggering turn (stored in pending_context by `_begin_escalation`),
        # not this contact-providing reply turn.
        packet = self.escalation.finalize(
            query=ctx["original_query"],
            session_id=session_id,
            reason=ctx["reason"],
            retrieved_context=ctx["retrieved_context"],
            slots=ctx["slots"],
            attempted_summary=ctx["attempted_summary"],
            explicit_contact=explicit_contact,
            attachment_id=ctx.get("attachment_id"),
        )
        self._save_state(session_id, pending=None, pending_context={}, turn_count=turn_count)
        return AgentResponse(
            route="escalate",
            intent=ctx["intent"],
            intent_confidence=ctx["intent_confidence"],
            retrieval_confidence=ctx["retrieval_confidence"],
            detail=self._build_escalation_detail(ctx.get("escalation_kind"), ctx["slots"], packet.contact_captured, language),
            escalation_id=packet.escalation_id,
            session_id=session_id,
        )

    # ------------------------------------------------------------------
    # Fresh routing decision (§8's diagram)
    # ------------------------------------------------------------------

    def _low_confidence_reason(self, intent_result, retrieval_result) -> str:
        """Returns a reason string if the request is too unclear to act on
        yet, None if it's confident enough to proceed. Factored out of
        `_route` so `_resolve_pending_clarification` can re-apply the exact
        same check to a clarifying reply (M9)."""
        if intent_result.intent_confidence < MIN_INTENT_CONFIDENCE:
            return "low_intent_confidence"
        # Two independent hard floors (M8) — a query must clear BOTH to be
        # considered relevant enough to answer. Only applies to
        # information-bound intents; action intents don't need retrieved
        # content, they need a slot (checked separately in `_route_confident`).
        if intent_result.intent not in ACTION_INTENTS and (
            retrieval_result.top_dense_score < MIN_ESCALATION_DENSE_FLOOR
            or retrieval_result.bm25_max_score < MIN_ESCALATION_BM25_FLOOR
        ):
            return "low_retrieval_relevance"
        return None

    def _describe_low_confidence(self, intent_result, reason: str) -> str:
        if reason == "low_intent_confidence":
            return f"Classified as intent '{intent_result.intent}' (confidence {intent_result.intent_confidence:.0%})."
        return "Retrieval found no clearly relevant policy content for this request."

    def _route(self, query, retrieval_query, session_id, intent_result, retrieval_result, turn_count, remembered_order, language, attachment_id=None) -> AgentResponse:
        reason = self._low_confidence_reason(intent_result, retrieval_result)
        if reason is not None:
            # M9: ask ONE human-worded clarifying question before ever
            # escalating on unclear input — see this module's docstring.
            # M11's remembered-order memory deliberately does NOT bypass
            # this gate (see ANALYSIS.md's M11 entry) — a low-confidence
            # message is cleared here exactly as before this milestone.
            self._save_state(
                session_id, pending="awaiting_clarification",
                pending_context={"original_query": query}, turn_count=turn_count,
            )
            return AgentResponse(
                route="information",
                intent=intent_result.intent,
                intent_confidence=intent_result.intent_confidence,
                retrieval_confidence=retrieval_result.top_dense_score,
                detail=_CLARIFICATION_QUESTION[language],
                escalation_id=None,
                session_id=session_id,
            )
        return self._route_confident(query, retrieval_query, session_id, intent_result, retrieval_result, turn_count, remembered_order, language, attachment_id)

    def _route_confident(self, query, retrieval_query, session_id, intent_result, retrieval_result, turn_count, remembered_order, language, attachment_id=None) -> AgentResponse:
        """The original routing body for a request already known to be
        clear enough to act on — reached either directly (fresh turn,
        passed `_low_confidence_reason`) or after a clarifying reply
        resolved things (`_resolve_pending_clarification`).

        M11: `remembered_order` (from `SessionState.last_order_context`,
        `{}` if nothing to remember) lets a follow-up that's missing its
        order_id slot reuse the order just discussed instead of being
        treated as a brand-new request missing information — and lets a
        delivery-dispute follow-up about that SAME order be recognized
        and escalated instead of silently repeating the flat tracking
        message. A different order_id (explicit or absent) never matches
        `remembered_order`, so it always gets a fresh, ordinary lookup."""
        if intent_result.intent in ACTION_INTENTS:
            required_slot = _REQUIRED_SLOT_BY_INTENT[intent_result.intent]
            slots = dict(intent_result.slots)
            filled_from_memory = False
            if not slots.get(required_slot) and remembered_order and remembered_order.get("order_id"):
                slots[required_slot] = remembered_order["order_id"]
                filled_from_memory = True

            if slots.get(required_slot):
                if (
                    intent_result.intent == "track_order"
                    and remembered_order
                    and slots[required_slot] == remembered_order.get("order_id")
                    and remembered_order.get("status") == "delivered"
                    and detect_delivery_dispute(query)
                ):
                    return self._begin_delivery_dispute_escalation(
                        slots[required_slot], query, session_id, retrieval_result, turn_count, language, attachment_id
                    )
                return self._do_action(
                    intent_result.intent, slots, session_id, retrieval_result, turn_count, language,
                    filled_from_memory=filled_from_memory, attachment_id=attachment_id,
                )
            return self._ask_for_slot(session_id, intent_result, required_slot, retrieval_result, turn_count, language)

        return self._answer_information(query, retrieval_query, session_id, intent_result, retrieval_result, turn_count, language, attachment_id)

    # ------------------------------------------------------------------
    # Branch (a): information
    # ------------------------------------------------------------------

    def _answer_information(self, query, retrieval_query, session_id, intent_result, retrieval_result, turn_count, language, attachment_id=None) -> AgentResponse:
        answer = generate_answer(self.llm, query, retrieval_result.chunks, language=language)
        # M13: reuse the vectors Weaviate already computed at ingestion
        # instead of re-embedding retrieved chunk text on every request —
        # see agent/groundedness.py's docstring and ANALYSIS.md's M13 entry.
        #
        # Phase B: the embedder is the same English-only bge model as
        # retrieval — embedding an Arabic `answer` directly against English
        # reference vectors reintroduces the exact cross-lingual problem
        # Phase B's investigation found at the retrieval step, just on the
        # output side (a genuinely well-grounded Arabic answer would still
        # fail this check and wrongly escalate — found via this phase's
        # end-to-end verification, see ANALYSIS.md's Phase B entry). Grounds
        # against an English back-translation of the answer instead; the
        # hard retry cap and the threshold itself are untouched.
        grounding_text = translate_to_english(self.llm, answer) if language == "ar" else answer
        grounded, _ = is_grounded(
            self.embedder, grounding_text, [c.text for c in retrieval_result.chunks],
            reference_vectors=[c.vector for c in retrieval_result.chunks],
        )

        if not grounded:
            # Hard-capped at exactly one retry — written as one more direct
            # call, not a loop, so there is no way to configure more.
            # Phase B: broadens using `retrieval_query` (English, translated
            # if the customer wrote Arabic), same reasoning as the initial
            # retrieve() call above it.
            retrieval_result = self.retriever.broaden_via_graph(retrieval_query, retrieval_result.chunks)
            answer = generate_answer(self.llm, query, retrieval_result.chunks, language=language)
            grounding_text = translate_to_english(self.llm, answer) if language == "ar" else answer
            grounded, _ = is_grounded(
                self.embedder, grounding_text, [c.text for c in retrieval_result.chunks],
                reference_vectors=[c.vector for c in retrieval_result.chunks],
            )

        if not grounded:
            return self._begin_escalation(
                query, session_id, reason="ungrounded_after_retry",
                retrieved_context=[c.text for c in retrieval_result.chunks], slots=intent_result.slots,
                intent=intent_result.intent, intent_confidence=intent_result.intent_confidence,
                retrieval_confidence=retrieval_result.top_dense_score, turn_count=turn_count,
                attempted_summary=(
                    f"Classified as intent '{intent_result.intent}'; retrieved policy content twice "
                    "(including a broadened search) but couldn't generate an answer actually "
                    "supported by it."
                ),
                language=language,
                attachment_id=attachment_id,
            )

        self._save_state(session_id, pending=None, pending_context={}, turn_count=turn_count)
        return AgentResponse(
            route="information",
            intent=intent_result.intent,
            intent_confidence=intent_result.intent_confidence,
            retrieval_confidence=retrieval_result.top_dense_score,
            detail=answer,
            escalation_id=None,
            session_id=session_id,
        )

    # ------------------------------------------------------------------
    # Branch (b) / (b2): action
    # ------------------------------------------------------------------

    def _do_action(self, intent, slots, session_id, retrieval_result, turn_count, language, filled_from_memory=False, attachment_id=None) -> AgentResponse:
        order_id = slots.get("order_id")
        if intent == "cancel_order":
            result = self.tools.cancel_order(order_id, language=language)
        elif intent == "track_order":
            result = self.tools.track_order(order_id, language=language)
        else:
            raise ValueError(f"_do_action called with a non-action intent: {intent}")

        if result.outcome == "not_found":
            return self._begin_escalation(
                f"[{intent}] order_id={order_id}", session_id, reason="order_not_found",
                retrieved_context=[], slots=slots, intent=intent, intent_confidence=1.0,
                retrieval_confidence=retrieval_result.top_dense_score, turn_count=turn_count,
                attempted_summary=f"Customer requested to {intent} order {order_id}, but no matching order was found.",
                language=language,
                attachment_id=attachment_id,
            )

        # M11: remembered for exactly one follow-up turn (see app/schemas.py
        # and this module's docstring) — refreshed here on every successful
        # action so a chain of follow-ups about the same order keeps working,
        # and cleared by every other _save_state call site's default.
        # `TrackResult.status` exists; `CancelResult` has no `status` field
        # (its `outcome` already conveys the equivalent, e.g. "fee_applied") —
        # fall back to `outcome` so this works for both action intents.
        remembered_status = getattr(result, "status", None) or result.outcome
        self._save_state(
            session_id, pending=None, pending_context={}, turn_count=turn_count,
            last_order_context={"order_id": order_id, "intent": intent, "status": remembered_status},
        )
        message = result.message
        if filled_from_memory:
            # M11: the customer didn't restate their order number -- it was
            # silently reused from the order just discussed. Say so, since
            # the trigger for this branch is often literally "what is my
            # order number?" (see ANALYSIS.md's M11 entry).
            prefix = _pick(language, f"Your order number is {order_id}. ", f"رقم طلبك هو {order_id}. ")
            message = prefix + message
        return AgentResponse(
            route="action",
            intent=intent,
            intent_confidence=1.0,
            retrieval_confidence=retrieval_result.top_dense_score,
            detail=message,
            escalation_id=None,
            session_id=session_id,
        )

    def _ask_for_slot(self, session_id, intent_result, required_slot, retrieval_result, turn_count, language) -> AgentResponse:
        self._save_state(
            session_id,
            pending="awaiting_slot",
            pending_context={"original_intent": intent_result.intent, "partial_slots": intent_result.slots},
            turn_count=turn_count,
        )
        question = _CLARIFY_QUESTIONS[language].get(required_slot, _GENERIC_SLOT_QUESTION[language].format(slot=required_slot))
        return AgentResponse(
            route="action",
            intent=intent_result.intent,
            intent_confidence=intent_result.intent_confidence,
            retrieval_confidence=retrieval_result.top_dense_score,
            detail=question,
            escalation_id=None,
            session_id=session_id,
        )

    # ------------------------------------------------------------------
    # ESCALATE (reached from branches a, b2, or c)
    # ------------------------------------------------------------------

    def _begin_escalation(
        self, query, session_id, reason, retrieved_context, slots, intent, intent_confidence,
        retrieval_confidence, turn_count, attempted_summary, language, pending_detail=None, escalation_kind=None,
        attachment_id=None,
    ) -> AgentResponse:
        """Decides whether contact is already known (finalize immediately)
        or a real one-shot round-trip is needed first (M9) — the escalation
        packet is never built/stored/posted to Slack until contact has
        either been captured or the one ask is exhausted.

        M11: `pending_detail`/`escalation_kind` let a specific escalation
        cause (currently just a delivery dispute) use reassuring, specific
        customer-facing copy instead of the generic text below, WITHOUT a
        second escalation mechanism — `escalation_kind` is a plain string
        stashed in `pending_context` (JSON-serializable, same pattern as
        every other value already stored there) so `_resolve_pending_contact`
        can build the matching final reply too.

        Phase D: `attachment_id`, when the round-trip is needed, is stashed
        in `pending_context` the same way — so if contact resolves on a
        LATER turn, `_resolve_pending_contact` still forwards the image
        from the ORIGINAL triggering turn, not whatever (if anything) the
        contact-providing reply itself attached."""
        known_contact = self.escalation.check_known_contact(slots, query)
        if known_contact:
            return self._finalize_escalation(
                query, session_id, reason, retrieved_context, slots, intent, intent_confidence,
                retrieval_confidence, turn_count, attempted_summary, language, explicit_contact=known_contact,
                escalation_kind=escalation_kind, attachment_id=attachment_id,
            )

        self._save_state(
            session_id,
            pending="awaiting_contact",
            pending_context={
                "original_query": query,
                "reason": reason,
                "retrieved_context": retrieved_context,
                "slots": slots,
                "intent": intent,
                "intent_confidence": intent_confidence,
                "retrieval_confidence": retrieval_confidence,
                "attempted_summary": attempted_summary,
                "escalation_kind": escalation_kind,
                "attachment_id": attachment_id,
            },
            turn_count=turn_count,
        )
        return AgentResponse(
            route="escalate",
            intent=intent,
            intent_confidence=intent_confidence,
            retrieval_confidence=retrieval_confidence,
            detail=pending_detail or (_DEFAULT_ESCALATION_ACK[language] + _CONTACT_QUESTION[language]),
            escalation_id=None,  # not created yet — no packet, no Slack post until contact resolves
            session_id=session_id,
        )

    def _finalize_escalation(
        self, query, session_id, reason, retrieved_context, slots, intent, intent_confidence,
        retrieval_confidence, turn_count, attempted_summary, language, explicit_contact=None, escalation_kind=None,
        attachment_id=None,
    ) -> AgentResponse:
        packet = self.escalation.finalize(
            query=query, session_id=session_id, reason=reason, retrieved_context=retrieved_context,
            slots=slots, attempted_summary=attempted_summary, explicit_contact=explicit_contact,
            attachment_id=attachment_id,
        )
        self._save_state(session_id, pending=None, pending_context={}, turn_count=turn_count)
        return AgentResponse(
            route="escalate",
            intent=intent,
            intent_confidence=intent_confidence,
            retrieval_confidence=retrieval_confidence,
            detail=self._build_escalation_detail(escalation_kind, slots, packet.contact_captured, language),
            escalation_id=packet.escalation_id,
            session_id=session_id,
        )

    def _build_escalation_detail(self, escalation_kind, slots, contact_captured: bool, language: str) -> str:
        if escalation_kind == "delivery_dispute":
            return self._delivery_dispute_escalation_detail(slots.get("order_id"), contact_captured, language)
        return self._escalation_detail(contact_captured, language)

    def _escalation_detail(self, contact_captured: bool, language: str) -> str:
        if contact_captured:
            return _pick(
                language,
                "I've connected you with a member of our team who can help with this. They'll follow up using the contact info you shared.",
                "لقد قمت بتوصيلك بأحد أعضاء فريقنا الذي يمكنه مساعدتك في هذا الأمر. سيتابعون معك باستخدام معلومات التواصل التي شاركتها.",
            )
        return _pick(
            language,
            "I've connected you with a member of our team who can help with this. They'll do their best to follow up if you reach out again.",
            "لقد قمت بتوصيلك بأحد أعضاء فريقنا الذي يمكنه مساعدتك في هذا الأمر. سيبذلون قصارى جهدهم للمتابعة إذا تواصلت معنا مرة أخرى.",
        )

    # ------------------------------------------------------------------
    # Delivery dispute (M11) — a dedicated ESCALATE cause, reusing every
    # existing escalation mechanism (contact round-trip, Slack Block Kit
    # summary) with only the customer-facing copy specialized.
    # ------------------------------------------------------------------

    def _begin_delivery_dispute_escalation(self, order_id, query, session_id, retrieval_result, turn_count, language, attachment_id=None) -> AgentResponse:
        return self._begin_escalation(
            query, session_id, reason="delivery_dispute_after_tracking",
            retrieved_context=[], slots={"order_id": order_id},
            intent="track_order", intent_confidence=1.0,
            retrieval_confidence=retrieval_result.top_dense_score, turn_count=turn_count,
            attempted_summary=(
                f"Customer tracked order {order_id} (status: delivered per our system), then reported "
                f'not receiving it ("{query}"). Tracking shows delivered but the customer disputes '
                "physical receipt — needs a team member to investigate the actual delivery, not a "
                "policy question."
            ),
            language=language,
            attachment_id=attachment_id,
            pending_detail=_pick(
                language,
                f"I'm sorry to hear that — I understand order {order_id} shows as delivered on our end, "
                "but that's clearly not right if it hasn't reached you. I've flagged this for a team "
                "member to look into the actual delivery. " + _CONTACT_QUESTION["en"],
                f"يؤسفني سماع ذلك — أفهم أن الطلب {order_id} يظهر لدينا على أنه تم توصيله، لكن من الواضح "
                "أن هذا غير صحيح إذا لم يصلك. لقد قمت بتمييز هذا الأمر ليتحقق أحد أعضاء الفريق من عملية "
                "التوصيل الفعلية. " + _CONTACT_QUESTION["ar"],
            ),
            escalation_kind="delivery_dispute",
        )

    def _delivery_dispute_escalation_detail(self, order_id, contact_captured: bool, language: str) -> str:
        if contact_captured:
            return _pick(
                language,
                f"Thanks — I've passed this along to our team with your contact info. They'll look "
                f"into exactly what happened with order {order_id}'s delivery and follow up directly.",
                f"شكرًا — لقد أحلت هذا الأمر إلى فريقنا مع معلومات التواصل الخاصة بك. سيتحققون بالضبط "
                f"مما حدث في عملية توصيل الطلب {order_id} وسيتابعون معك مباشرة.",
            )
        return _pick(
            language,
            f"I've flagged order {order_id}'s delivery for our team to investigate. They'll do their "
            "best to follow up if you reach out again.",
            f"لقد قمت بتمييز عملية توصيل الطلب {order_id} ليقوم فريقنا بالتحقيق فيها. سيبذلون قصارى "
            "جهدهم للمتابعة إذا تواصلت معنا مرة أخرى.",
        )

    # ------------------------------------------------------------------
    def _save_state(self, session_id, pending, pending_context, turn_count, last_order_context=None) -> None:
        # M11: defaults to {} (cleared) at every call site except
        # `_do_action`'s success path, which explicitly passes the
        # just-completed order's context — this is what structurally
        # guarantees the remembered order never survives more than one
        # extra turn without a fresh action re-setting it (see
        # ANALYSIS.md's M11 entry).
        self.session_store.set_state(
            session_id,
            SessionState(
                session_id=session_id, pending=pending, pending_context=pending_context,
                turn_count=turn_count, last_order_context=last_order_context or {},
            ),
        )
