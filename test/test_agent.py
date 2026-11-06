import re
from dataclasses import dataclass, field

from agent.agent import _CLARIFICATION_QUESTION, Agent
from agent.intent_classifier import IntentResult
from agent.tools import CancelResult, TrackResult
from app.schemas import EscalationPacket, SessionState

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


# --- Fakes -------------------------------------------------------------------


@dataclass
class FakeChunk:
    chunk_id: str
    text: str
    category: str = "CANCEL"
    heading: str = "Cancellation Fee Policy"
    # M13: agent.py's information route now reads c.vector unconditionally
    # to pass reference_vectors through to is_grounded() -- a real
    # (fake-embedder-scale) vector, not None, so tests exercise the same
    # "vectors available" path production hits after the M13 fix.
    vector: list = field(default_factory=lambda: [0.1, 0.2, 0.3])


@dataclass
class FakeRetrievalResult:
    chunks: list
    confidence: str
    top_dense_score: float
    related_concepts: set = field(default_factory=set)
    # Defaults well above both M8 floors so existing call sites that don't
    # care about this dimension keep behaving exactly as before.
    bm25_max_score: float = 100.0


class FakeRetriever:
    """`retrieve_result` may be a single FakeRetrievalResult (returned every
    call) or a list consumed in order across successive calls, for tests
    that need retrieval to change between turns of a conversation."""

    def __init__(self, retrieve_result, broaden_result=None):
        self._sequence = retrieve_result if isinstance(retrieve_result, list) else None
        self._single = retrieve_result
        self._broaden_result = broaden_result or (self._sequence[-1] if self._sequence else retrieve_result)
        self.retrieve_calls = 0
        self.broaden_calls = 0

    def retrieve(self, query):
        self.retrieve_calls += 1
        if self._sequence is not None:
            return self._sequence[min(self.retrieve_calls - 1, len(self._sequence) - 1)]
        return self._single

    def broaden_via_graph(self, query, chunks):
        self.broaden_calls += 1
        return self._broaden_result


class FakeLLM:
    """Not actually called directly by Agent — generator/intent_classifier
    call it, but tests stub those two modules' outputs via monkeypatch-free
    fakes injected as `intent_result` and canned answers instead. Kept here
    only so Agent's constructor has something to hold; unused in practice
    because we drive the classify/generate steps deterministically."""

    pass


class FakeSessionStore:
    def __init__(self, initial_state=None):
        self._state = initial_state
        self.saved_states = []

    def get_state(self, session_id):
        return self._state or SessionState(session_id=session_id, pending=None, pending_context={}, turn_count=0)

    def set_state(self, session_id, state):
        self._state = state
        self.saved_states.append(state)

    def clear_pending(self, session_id):
        self._state.pending = None
        self._state.pending_context = {}
        return self._state


class FakeEscalation:
    """Mirrors agent/escalation.py's check_known_contact()/finalize() split
    closely enough to exercise agent.py's branching (known contact ->
    finalize immediately; unknown -> agent.py arms `awaiting_contact`)."""

    def __init__(self):
        self.finalize_calls = []

    def check_known_contact(self, slots, query):
        for key in ("email", "phone"):
            if slots.get(key):
                return slots[key]
        match = _EMAIL_RE.search(query)
        return match.group(0) if match else None

    def finalize(self, query, session_id, reason, retrieved_context, slots, attempted_summary, explicit_contact=None, attachment_id=None):
        contact = explicit_contact or self.check_known_contact(slots, query)
        self.finalize_calls.append(
            {
                "query": query, "session_id": session_id, "reason": reason,
                "attempted_summary": attempted_summary, "contact": contact, "attachment_id": attachment_id,
            }
        )
        return EscalationPacket(
            escalation_id=f"esc-{len(self.finalize_calls)}",
            session_id=session_id,
            contact=contact,
            contact_captured=bool(contact),
            slack_delivered=False,
            query=query,
            retrieved_context=retrieved_context,
            reason=reason,
            timestamp="2026-07-06T00:00:00+00:00",
            attempted_summary=attempted_summary,
        )


class FakeTools:
    def __init__(self, cancel_result=None, track_result=None):
        self._cancel_result = cancel_result
        self._track_result = track_result
        self.cancel_calls = []
        self.track_calls = []

    def cancel_order(self, order_id, language="en"):
        self.cancel_calls.append(order_id)
        return self._cancel_result

    def track_order(self, order_id, language="en"):
        self.track_calls.append(order_id)
        return self._track_result


def _make_agent(retriever, session_store=None, escalation=None, tools=None):
    return Agent(
        retriever=retriever,
        llm=FakeLLM(),
        embedder=object(),
        session_store=session_store or FakeSessionStore(),
        escalation=escalation or FakeEscalation(),
        tools_module=tools or FakeTools(),
    )


def _patch_intent(monkeypatch, intent_result_or_sequence):
    """Accepts a single IntentResult (returned every call) or a list
    consumed in order across successive calls."""
    sequence = intent_result_or_sequence if isinstance(intent_result_or_sequence, list) else None
    calls = {"n": 0}

    def _fake_classify(llm, query):
        calls["n"] += 1
        if sequence is not None:
            return sequence[min(calls["n"] - 1, len(sequence) - 1)]
        return intent_result_or_sequence

    monkeypatch.setattr("agent.agent.classify_intent_and_slots", _fake_classify)
    return calls


def _patch_intent_must_not_be_called(monkeypatch):
    def _fail(llm, query):
        raise AssertionError("classify_intent_and_slots should not have been called")

    monkeypatch.setattr("agent.agent.classify_intent_and_slots", _fail)


def _patch_generate(monkeypatch, answers):
    """`answers` is a list consumed in order across successive calls."""
    calls = {"n": 0}

    def _fake_generate(llm, query, chunks, language="en", model=None):
        i = calls["n"]
        calls["n"] += 1
        return answers[min(i, len(answers) - 1)]

    monkeypatch.setattr("agent.agent.generate_answer", _fake_generate)
    return calls


def _patch_grounded(monkeypatch, results):
    """`results` is a list of booleans consumed in order."""
    calls = {"n": 0}

    def _fake_is_grounded(embedder, answer, references, threshold=None, reference_vectors=None):
        i = calls["n"]
        calls["n"] += 1
        ok = results[min(i, len(results) - 1)]
        return ok, (1.0 if ok else 0.0)

    monkeypatch.setattr("agent.agent.is_grounded", _fake_is_grounded)
    return calls


# --- Smalltalk short-circuit (M9) --------------------------------------------


def test_smalltalk_greeting_short_circuits_before_classification_or_retrieval(monkeypatch):
    _patch_intent_must_not_be_called(monkeypatch)
    retriever = FakeRetriever(FakeRetrievalResult(chunks=[], confidence="High", top_dense_score=0.9))
    agent = _make_agent(retriever)

    response = agent.handle_message("hi", "s1")

    assert response.route == "information"
    assert response.intent == "smalltalk"
    assert retriever.retrieve_calls == 0


def test_smalltalk_does_not_false_positive_on_a_request_that_starts_with_a_greeting(monkeypatch):
    _patch_intent(monkeypatch, IntentResult(intent="cancel_order", intent_confidence=0.95, slots={"order_id": "ORD-1001"}))
    tools = FakeTools(cancel_result=CancelResult(outcome="free_no_fee", order_id="ORD-1001", message="cancelled free"))
    retrieval = FakeRetrievalResult(chunks=[], confidence="High", top_dense_score=0.9)
    agent = _make_agent(FakeRetriever(retrieval), tools=tools)

    response = agent.handle_message("hi, can you cancel order ORD-1001?", "s1")

    assert response.route == "action"
    assert response.detail == "cancelled free"


def test_smalltalk_farewell_clears_any_pending_state(monkeypatch):
    session_store = FakeSessionStore(
        initial_state=SessionState(
            session_id="s1", pending="awaiting_slot",
            pending_context={"original_intent": "cancel_order", "partial_slots": {}}, turn_count=1,
        )
    )
    _patch_intent_must_not_be_called(monkeypatch)
    agent = _make_agent(FakeRetriever(FakeRetrievalResult(chunks=[], confidence="High", top_dense_score=0.9)), session_store=session_store)

    response = agent.handle_message("nevermind", "s1")

    assert response.route == "information"
    assert session_store.get_state("s1").pending is None


def test_greeting_during_pending_clarification_falls_through_as_the_reply(monkeypatch):
    # Real bug found via live testing: sending "hi" as the reply to a
    # clarifying question was resetting to a bare greeting instead of being
    # treated as the (still unclear) answer. Unlike disengagement, a bare
    # greeting/thanks/farewell mid-conversation must not silently discard an
    # active pending question.
    intents = [
        IntentResult(intent="track_order", intent_confidence=0.2, slots={}),
        IntentResult(intent="unknown", intent_confidence=0.0, slots={}),
    ]
    _patch_intent(monkeypatch, intents)
    retrieval = FakeRetrievalResult(chunks=[FakeChunk("c1", "text")], confidence="High", top_dense_score=0.9)
    session_store = FakeSessionStore()
    escalation = FakeEscalation()
    agent = _make_agent(FakeRetriever(retrieval), session_store=session_store, escalation=escalation)

    first = agent.handle_message("gibberish query", "s1")
    assert session_store.get_state("s1").pending == "awaiting_clarification"

    second = agent.handle_message("hi", "s1")

    # "hi" was reclassified like any other reply, still came back unclear,
    # and the one clarify attempt is now exhausted -- escalates, rather than
    # resetting to a canned greeting.
    assert second.route == "escalate"
    assert second.intent != "smalltalk"
    assert session_store.get_state("s1").pending == "awaiting_contact"


def test_greeting_during_pending_slot_falls_through_as_the_reply(monkeypatch):
    session_store = FakeSessionStore()
    retrieval = FakeRetrievalResult(chunks=[], confidence="High", top_dense_score=0.9)
    tools = FakeTools(cancel_result=CancelResult(outcome="free_no_fee", order_id="ORD-1001", message="cancelled free"))
    agent = _make_agent(FakeRetriever(retrieval), session_store=session_store, tools=tools)

    _patch_intent(monkeypatch, IntentResult(intent="cancel_order", intent_confidence=0.95, slots={}))
    first = agent.handle_message("I want to cancel my order", "s1")
    assert session_store.get_state("s1").pending == "awaiting_slot"

    # "hi" is not the order number, but it must be evaluated as the reply
    # (and thus escalate, the slot-clarify attempt already being used up),
    # not reset the conversation to a greeting.
    _patch_intent(monkeypatch, IntentResult(intent="unknown", intent_confidence=0.0, slots={}))
    second = agent.handle_message("hi", "s1")

    assert second.intent != "smalltalk"
    assert second.route == "escalate"


def test_disengagement_during_pending_slot_still_wins_unconditionally(monkeypatch):
    # Contrast with the two tests above: "nevermind" is unambiguous
    # regardless of context, so it keeps overriding pending state.
    session_store = FakeSessionStore(
        initial_state=SessionState(
            session_id="s1", pending="awaiting_slot",
            pending_context={"original_intent": "cancel_order", "partial_slots": {}}, turn_count=1,
        )
    )
    _patch_intent_must_not_be_called(monkeypatch)
    agent = _make_agent(FakeRetriever(FakeRetrievalResult(chunks=[], confidence="High", top_dense_score=0.9)), session_store=session_store)

    response = agent.handle_message("forget it", "s1")

    assert response.route == "information"
    assert response.intent == "smalltalk"
    assert session_store.get_state("s1").pending is None


# --- Low confidence -> clarify first, escalate only if still unclear (M9) ---


def test_low_intent_confidence_asks_clarifying_question_first(monkeypatch):
    _patch_intent(monkeypatch, IntentResult(intent="track_order", intent_confidence=0.2, slots={"order_id": "ORD-1001"}))
    retrieval = FakeRetrievalResult(chunks=[FakeChunk("c1", "text")], confidence="High", top_dense_score=0.9)
    session_store = FakeSessionStore()
    agent = _make_agent(FakeRetriever(retrieval), session_store=session_store)

    response = agent.handle_message("gibberish query", "s1")

    assert response.route == "information"
    assert response.detail == _CLARIFICATION_QUESTION["en"]
    assert session_store.get_state("s1").pending == "awaiting_clarification"


def test_low_retrieval_relevance_asks_clarifying_question_first(monkeypatch):
    _patch_intent(monkeypatch, IntentResult(intent="delivery_period", intent_confidence=0.9, slots={}))
    retrieval = FakeRetrievalResult(chunks=[FakeChunk("c1", "text")], confidence="Low", top_dense_score=0.3)
    session_store = FakeSessionStore()
    agent = _make_agent(FakeRetriever(retrieval), session_store=session_store)

    response = agent.handle_message("some vague question", "s1")

    assert response.route == "information"
    assert response.detail == _CLARIFICATION_QUESTION["en"]
    assert session_store.get_state("s1").pending == "awaiting_clarification"


def test_low_bm25_relevance_asks_clarifying_question_first_despite_ok_dense_score(monkeypatch):
    # M8's fix: a query that scores fine on dense similarity but has almost
    # no literal vocabulary overlap with the corpus (low BM25) still counts
    # as low-relevance — now routed through the M9 clarify-first step too.
    _patch_intent(monkeypatch, IntentResult(intent="delivery_period", intent_confidence=0.9, slots={}))
    retrieval = FakeRetrievalResult(
        chunks=[FakeChunk("c1", "text")], confidence="Medium", top_dense_score=0.9, bm25_max_score=1.0
    )
    session_store = FakeSessionStore()
    agent = _make_agent(FakeRetriever(retrieval), session_store=session_store)

    response = agent.handle_message("asdf1234 zzz order thing help???", "s1")

    assert response.route == "information"
    assert response.detail == _CLARIFICATION_QUESTION["en"]
    assert session_store.get_state("s1").pending == "awaiting_clarification"


def test_information_passes_when_both_dense_and_bm25_floors_are_cleared(monkeypatch):
    _patch_intent(monkeypatch, IntentResult(intent="delivery_period", intent_confidence=0.9, slots={}))
    _patch_generate(monkeypatch, ["a good answer"])
    _patch_grounded(monkeypatch, [True])
    # Exactly at both floors — boundary is inclusive (>=).
    retrieval = FakeRetrievalResult(
        chunks=[FakeChunk("c1", "text")], confidence="Medium", top_dense_score=0.55, bm25_max_score=4.0
    )
    agent = _make_agent(FakeRetriever(retrieval))

    response = agent.handle_message("how long does delivery take?", "s1")

    assert response.route == "information"


def test_action_route_unaffected_by_low_dense_or_bm25_score(monkeypatch):
    # The M8/M9 relevance gate only applies to the INFORMATION branch — an
    # action intent with a complete slot must still execute directly
    # regardless of how low retrieval's scores happen to be.
    tools = FakeTools(cancel_result=CancelResult(outcome="free_no_fee", order_id="ORD-1001", message="cancelled free"))
    retrieval = FakeRetrievalResult(chunks=[], confidence="Low", top_dense_score=0.1, bm25_max_score=0.0)
    agent = _make_agent(FakeRetriever(retrieval), tools=tools)
    _patch_intent(monkeypatch, IntentResult(intent="cancel_order", intent_confidence=0.95, slots={"order_id": "ORD-1001"}))

    response = agent.handle_message("cancel my order ORD-1001", "s1")

    assert response.route == "action"
    assert response.detail == "cancelled free"


def test_clarification_reply_resolves_to_information_answer(monkeypatch):
    intents = [
        IntentResult(intent="delivery_period", intent_confidence=0.3, slots={}),
        IntentResult(intent="delivery_period", intent_confidence=0.9, slots={}),
    ]
    _patch_intent(monkeypatch, intents)
    _patch_generate(monkeypatch, ["a good answer"])
    _patch_grounded(monkeypatch, [True])
    retrieval = FakeRetrievalResult(chunks=[FakeChunk("c1", "text")], confidence="High", top_dense_score=0.9)
    session_store = FakeSessionStore()
    agent = _make_agent(FakeRetriever(retrieval), session_store=session_store)

    first = agent.handle_message("uh", "s1")
    assert first.route == "information"
    assert session_store.get_state("s1").pending == "awaiting_clarification"

    second = agent.handle_message("how long does standard shipping take?", "s1")
    assert second.route == "information"
    assert second.detail == "a good answer"
    assert session_store.get_state("s1").pending is None


def test_clarification_reply_resolves_to_action(monkeypatch):
    intents = [
        IntentResult(intent="unknown", intent_confidence=0.0, slots={}),
        IntentResult(intent="cancel_order", intent_confidence=0.95, slots={"order_id": "ORD-1001"}),
    ]
    _patch_intent(monkeypatch, intents)
    tools = FakeTools(cancel_result=CancelResult(outcome="free_no_fee", order_id="ORD-1001", message="cancelled free"))
    retrieval = FakeRetrievalResult(chunks=[], confidence="High", top_dense_score=0.9)
    session_store = FakeSessionStore()
    agent = _make_agent(FakeRetriever(retrieval), session_store=session_store, tools=tools)

    first = agent.handle_message("uh", "s1")
    assert first.route == "information"
    assert session_store.get_state("s1").pending == "awaiting_clarification"

    second = agent.handle_message("cancel order ORD-1001", "s1")
    assert second.route == "action"
    assert second.detail == "cancelled free"


def test_still_unclear_after_clarification_begins_escalation_awaiting_contact(monkeypatch):
    intents = [
        IntentResult(intent="track_order", intent_confidence=0.2, slots={}),
        IntentResult(intent="unknown", intent_confidence=0.0, slots={}),
    ]
    _patch_intent(monkeypatch, intents)
    retrieval = FakeRetrievalResult(chunks=[FakeChunk("c1", "text")], confidence="High", top_dense_score=0.9)
    session_store = FakeSessionStore()
    escalation = FakeEscalation()
    agent = _make_agent(FakeRetriever(retrieval), session_store=session_store, escalation=escalation)

    first = agent.handle_message("gibberish query", "s1")
    assert first.route == "information"
    assert session_store.get_state("s1").pending == "awaiting_clarification"

    second = agent.handle_message("still gibberish", "s1")
    assert second.route == "escalate"
    assert second.escalation_id is None  # not finalized yet -- no contact known
    assert session_store.get_state("s1").pending == "awaiting_contact"
    # THE hard cap: the clarify question is never asked a second time —
    # a still-unclear reply always proceeds to escalation, never back to
    # awaiting_clarification.
    assert escalation.finalize_calls == []


def test_escalation_preserves_original_query_not_the_clarify_reply(monkeypatch):
    # Real gap found during M9's live verification: the finalized record's
    # `query` must be what the customer originally opened with, not their
    # reply to the clarifying question (which is still preserved, just in
    # the summary instead).
    intents = [
        IntentResult(intent="unknown", intent_confidence=0.0, slots={}),
        IntentResult(intent="unknown", intent_confidence=0.0, slots={"email": "j.smith@example.com"}),
    ]
    _patch_intent(monkeypatch, intents)
    retrieval = FakeRetrievalResult(chunks=[], confidence="Low", top_dense_score=0.1, bm25_max_score=0.0)
    session_store = FakeSessionStore()
    escalation = FakeEscalation()
    agent = _make_agent(FakeRetriever(retrieval), session_store=session_store, escalation=escalation)

    agent.handle_message("the thing with my stuff is broken idk what to do", "s1")
    second = agent.handle_message("it just stopped working, my email is j.smith@example.com", "s1")

    assert second.route == "escalate"
    assert second.escalation_id is not None  # contact was already in this reply's slots -- finalizes immediately
    assert escalation.finalize_calls[0]["query"] == "the thing with my stuff is broken idk what to do"
    assert "it just stopped working" in escalation.finalize_calls[0]["attempted_summary"]


# --- Contact round-trip before finalizing (M9) -------------------------------


def test_escalation_finalizes_immediately_when_contact_already_known(monkeypatch):
    intent_with_email = IntentResult(intent="unknown", intent_confidence=0.0, slots={"email": "j.smith@example.com"})
    _patch_intent(monkeypatch, intent_with_email)
    retrieval = FakeRetrievalResult(chunks=[], confidence="Low", top_dense_score=0.1, bm25_max_score=0.0)
    session_store = FakeSessionStore()
    escalation = FakeEscalation()
    agent = _make_agent(FakeRetriever(retrieval), session_store=session_store, escalation=escalation)

    first = agent.handle_message("gibberish, my email is j.smith@example.com", "s1")
    assert first.route == "information"
    assert session_store.get_state("s1").pending == "awaiting_clarification"

    second = agent.handle_message("still gibberish", "s1")

    assert second.route == "escalate"
    assert second.escalation_id is not None  # contact already known -- no awaiting_contact needed
    assert escalation.finalize_calls[0]["contact"] == "j.smith@example.com"
    assert session_store.get_state("s1").pending is None


def test_awaiting_contact_finalizes_with_provided_email(monkeypatch):
    intents = [
        IntentResult(intent="unknown", intent_confidence=0.0, slots={}),
        IntentResult(intent="unknown", intent_confidence=0.0, slots={}),
        IntentResult(intent="unknown", intent_confidence=0.0, slots={}),
    ]
    _patch_intent(monkeypatch, intents)
    retrieval = FakeRetrievalResult(chunks=[], confidence="Low", top_dense_score=0.1, bm25_max_score=0.0)
    session_store = FakeSessionStore()
    escalation = FakeEscalation()
    agent = _make_agent(FakeRetriever(retrieval), session_store=session_store, escalation=escalation)

    agent.handle_message("asdkfj qwerty nonsense", "s1")  # -> awaiting_clarification
    second = agent.handle_message("still nonsense zzz", "s1")  # -> awaiting_contact
    assert second.escalation_id is None
    assert session_store.get_state("s1").pending == "awaiting_contact"

    third = agent.handle_message("sure, it's j.smith@example.com", "s1")

    assert third.route == "escalate"
    assert third.escalation_id is not None
    assert escalation.finalize_calls[0]["contact"] == "j.smith@example.com"
    assert session_store.get_state("s1").pending is None


def test_awaiting_contact_finalizes_even_when_still_not_provided(monkeypatch):
    # THE hard cap: contact is asked for exactly once — a reply that still
    # doesn't provide it finalizes anyway rather than asking again.
    intents = [
        IntentResult(intent="unknown", intent_confidence=0.0, slots={}),
        IntentResult(intent="unknown", intent_confidence=0.0, slots={}),
        IntentResult(intent="unknown", intent_confidence=0.0, slots={}),
    ]
    _patch_intent(monkeypatch, intents)
    retrieval = FakeRetrievalResult(chunks=[], confidence="Low", top_dense_score=0.1, bm25_max_score=0.0)
    session_store = FakeSessionStore()
    escalation = FakeEscalation()
    agent = _make_agent(FakeRetriever(retrieval), session_store=session_store, escalation=escalation)

    agent.handle_message("asdkfj qwerty nonsense", "s1")
    agent.handle_message("still nonsense zzz", "s1")
    third = agent.handle_message("I don't have one, sorry", "s1")

    assert third.route == "escalate"
    assert third.escalation_id is not None  # finalized anyway
    assert escalation.finalize_calls[0]["contact"] is None
    assert session_store.get_state("s1").pending is None


# --- Branch (a): information + groundedness retry cap ------------------------


def test_information_grounded_on_first_try(monkeypatch):
    _patch_intent(monkeypatch, IntentResult(intent="delivery_period", intent_confidence=0.9, slots={}))
    _patch_generate(monkeypatch, ["a good answer"])
    _patch_grounded(monkeypatch, [True])
    retrieval = FakeRetrievalResult(chunks=[FakeChunk("c1", "text")], confidence="High", top_dense_score=0.9)
    retriever = FakeRetriever(retrieval)
    agent = _make_agent(retriever)

    response = agent.handle_message("how long does delivery take?", "s1")

    assert response.route == "information"
    assert response.detail == "a good answer"
    assert retriever.broaden_calls == 0  # no retry needed


def test_information_ungrounded_then_grounded_after_one_broaden_retry(monkeypatch):
    _patch_intent(monkeypatch, IntentResult(intent="delivery_period", intent_confidence=0.9, slots={}))
    _patch_generate(monkeypatch, ["bad answer", "better answer"])
    _patch_grounded(monkeypatch, [False, True])
    retrieval = FakeRetrievalResult(chunks=[FakeChunk("c1", "text")], confidence="High", top_dense_score=0.9)
    broadened = FakeRetrievalResult(chunks=[FakeChunk("c1", "text"), FakeChunk("c2", "more")], confidence="High", top_dense_score=0.9)
    retriever = FakeRetriever(retrieval, broaden_result=broadened)
    agent = _make_agent(retriever)

    response = agent.handle_message("how long does delivery take?", "s1")

    assert response.route == "information"
    assert response.detail == "better answer"
    assert retriever.broaden_calls == 1


def test_information_still_ungrounded_after_retry_begins_escalation_awaiting_contact(monkeypatch):
    _patch_intent(monkeypatch, IntentResult(intent="delivery_period", intent_confidence=0.9, slots={}))
    _patch_generate(monkeypatch, ["bad", "still bad"])
    _patch_grounded(monkeypatch, [False, False])
    retrieval = FakeRetrievalResult(chunks=[FakeChunk("c1", "text")], confidence="High", top_dense_score=0.9)
    escalation = FakeEscalation()
    retriever = FakeRetriever(retrieval)
    agent = _make_agent(retriever, escalation=escalation)

    response = agent.handle_message("how long does delivery take?", "s1")

    assert response.route == "escalate"
    assert response.escalation_id is None  # awaiting_contact -- not finalized on this turn
    # THE hard cap: broaden_via_graph was called exactly once, never more,
    # no matter how many times generation kept failing groundedness.
    assert retriever.broaden_calls == 1
    assert escalation.finalize_calls == []


# --- Branch (b)/(b2): action ---------------------------------------------------


def test_action_with_complete_slots_calls_tool_directly(monkeypatch):
    tools = FakeTools(cancel_result=CancelResult(outcome="free_no_fee", order_id="ORD-1001", message="cancelled free"))
    retrieval = FakeRetrievalResult(chunks=[], confidence="High", top_dense_score=0.9)
    agent = _make_agent(FakeRetriever(retrieval), tools=tools)
    _patch_intent(monkeypatch, IntentResult(intent="cancel_order", intent_confidence=0.95, slots={"order_id": "ORD-1001"}))

    response = agent.handle_message("cancel my order ORD-1001", "s1")

    assert response.route == "action"
    assert response.detail == "cancelled free"
    assert tools.cancel_calls == ["ORD-1001"]


def test_action_missing_slot_asks_once_then_begins_escalation_awaiting_contact(monkeypatch):
    session_store = FakeSessionStore()
    retrieval = FakeRetrievalResult(chunks=[], confidence="High", top_dense_score=0.9)
    escalation = FakeEscalation()
    agent = _make_agent(FakeRetriever(retrieval), session_store=session_store, escalation=escalation)

    # Turn 1: action intent, no order_id -> exactly one clarify question.
    _patch_intent(monkeypatch, IntentResult(intent="cancel_order", intent_confidence=0.95, slots={}))
    first = agent.handle_message("I want to cancel my order", "s1")
    assert first.route == "action"
    assert "order number" in first.detail.lower()
    assert session_store.get_state("s1").pending == "awaiting_slot"

    # Turn 2: still no order_id -> the ONE slot-clarify attempt is used up,
    # so this begins escalation (not a second, general clarify question —
    # that would double up on an already-exhausted ask).
    _patch_intent(monkeypatch, IntentResult(intent="cancel_order", intent_confidence=0.4, slots={}))
    second = agent.handle_message("I don't remember it", "s1")

    assert second.route == "escalate"
    assert second.escalation_id is None  # no contact known yet -- awaiting_contact
    assert session_store.get_state("s1").pending == "awaiting_contact"
    assert escalation.finalize_calls == []


def test_action_missing_slot_then_provided_resolves_to_action(monkeypatch):
    session_store = FakeSessionStore()
    retrieval = FakeRetrievalResult(chunks=[], confidence="High", top_dense_score=0.9)
    tools = FakeTools(track_result=TrackResult(outcome="found", order_id="ORD-1002", status="placed", eta="3-5 business days", message="tracked"))
    agent = _make_agent(FakeRetriever(retrieval), session_store=session_store, tools=tools)

    _patch_intent(monkeypatch, IntentResult(intent="track_order", intent_confidence=0.95, slots={}))
    first = agent.handle_message("where is my order?", "s1")
    assert first.route == "action"
    assert session_store.get_state("s1").pending == "awaiting_slot"

    _patch_intent(monkeypatch, IntentResult(intent="track_order", intent_confidence=0.6, slots={"order_id": "ORD-1002"}))
    second = agent.handle_message("it's ORD-1002", "s1")

    assert second.route == "action"
    assert second.detail == "tracked"
    assert tools.track_calls == ["ORD-1002"]
    assert session_store.get_state("s1").pending is None


def test_topic_change_during_pending_drops_pending_and_routes_fresh(monkeypatch):
    session_store = FakeSessionStore(
        initial_state=SessionState(
            session_id="s1", pending="awaiting_slot",
            pending_context={"original_intent": "cancel_order", "partial_slots": {}}, turn_count=1,
        )
    )
    retrieval = FakeRetrievalResult(chunks=[FakeChunk("c1", "text")], confidence="High", top_dense_score=0.9)
    _patch_generate(monkeypatch, ["an answer about refunds"])
    _patch_grounded(monkeypatch, [True])
    # A clearly different, high-confidence intent — not an order number.
    _patch_intent(monkeypatch, IntentResult(intent="check_refund_policy", intent_confidence=0.9, slots={}))
    agent = _make_agent(FakeRetriever(retrieval), session_store=session_store)

    response = agent.handle_message("actually, what's your refund policy?", "s1")

    assert response.route == "information"
    assert response.intent == "check_refund_policy"


def test_action_not_found_order_begins_escalation_awaiting_contact(monkeypatch):
    tools = FakeTools(cancel_result=CancelResult(outcome="not_found", order_id="ORD-9999", message="not found"))
    retrieval = FakeRetrievalResult(chunks=[], confidence="High", top_dense_score=0.9)
    escalation = FakeEscalation()
    agent = _make_agent(FakeRetriever(retrieval), escalation=escalation, tools=tools)
    _patch_intent(monkeypatch, IntentResult(intent="cancel_order", intent_confidence=0.95, slots={"order_id": "ORD-9999"}))

    response = agent.handle_message("cancel ORD-9999", "s1")

    assert response.route == "escalate"
    # M9 fix: a bare order_id is no longer trusted as contact info, so this
    # goes to the contact round-trip instead of finalizing immediately.
    assert response.escalation_id is None
    assert escalation.finalize_calls == []

    # The customer explicitly answers "email or order number" with the
    # same order_id -- accepted here, since it's now a direct answer to
    # that explicit ask (unlike the passive, incidental extraction above).
    followup = agent.handle_message("sure, it's ORD-9999", "s1")
    assert followup.route == "escalate"
    assert followup.escalation_id is not None
    assert escalation.finalize_calls[0]["reason"] == "order_not_found"
    assert escalation.finalize_calls[0]["contact"] == "ORD-9999"


# --- Remembered order context & delivery dispute (M11) -----------------------


def test_remembered_order_id_fills_missing_slot_on_next_turn(monkeypatch):
    # Real bug: "what is my order number?" right after tracking ORD-1003
    # was treated as a brand-new track_order request missing its slot.
    intents = [
        IntentResult(intent="track_order", intent_confidence=1.0, slots={"order_id": "ORD-1003"}),
        IntentResult(intent="track_order", intent_confidence=0.8, slots={}),
    ]
    _patch_intent(monkeypatch, intents)
    tools = FakeTools(
        track_result=TrackResult(outcome="found", order_id="ORD-1003", status="shipped", eta="1-2 business days", message="This order is shipped.")
    )
    retrieval = FakeRetrievalResult(chunks=[], confidence="High", top_dense_score=0.9)
    session_store = FakeSessionStore()
    agent = _make_agent(FakeRetriever(retrieval), session_store=session_store, tools=tools)

    first = agent.handle_message("Can you track my order? It's ORD-1003", "s1")
    assert first.route == "action"
    assert session_store.get_state("s1").last_order_context == {
        "order_id": "ORD-1003", "intent": "track_order", "status": "shipped",
    }

    second = agent.handle_message("What is my order number?", "s1")

    assert second.route == "action"
    assert second.detail == "Your order number is ORD-1003. This order is shipped."
    assert tools.track_calls == ["ORD-1003", "ORD-1003"]  # re-resolved using the remembered id


def test_remembered_order_context_does_not_survive_a_second_turn(monkeypatch):
    # The hard limit: an unrelated turn in between clears the memory, so a
    # THIRD turn's missing slot is asked for normally, not silently filled
    # from stale context two turns old.
    intents = [
        IntentResult(intent="track_order", intent_confidence=1.0, slots={"order_id": "ORD-1003"}),
        IntentResult(intent="delivery_period", intent_confidence=0.9, slots={}),
        IntentResult(intent="track_order", intent_confidence=0.8, slots={}),
    ]
    _patch_intent(monkeypatch, intents)
    _patch_generate(monkeypatch, ["an answer about delivery"])
    _patch_grounded(monkeypatch, [True])
    tools = FakeTools(track_result=TrackResult(outcome="found", order_id="ORD-1003", status="shipped", message="This order is shipped."))
    retrieval = FakeRetrievalResult(chunks=[FakeChunk("c1", "text")], confidence="High", top_dense_score=0.9)
    session_store = FakeSessionStore()
    agent = _make_agent(FakeRetriever(retrieval), session_store=session_store, tools=tools)

    agent.handle_message("track ORD-1003", "s1")
    assert session_store.get_state("s1").last_order_context != {}

    middle = agent.handle_message("how long does delivery take?", "s1")
    assert middle.route == "information"
    assert session_store.get_state("s1").last_order_context == {}  # cleared by the unrelated turn

    third = agent.handle_message("what is my order number?", "s1")
    assert third.route == "action"
    assert "order number" in third.detail.lower()
    assert not third.detail.startswith("Your order number")  # genuinely re-asked, not memory-filled
    assert tools.track_calls == ["ORD-1003"]  # only the first, real tracking call ever happened


def test_remembered_order_context_is_cleared_when_a_pending_state_gets_armed_instead(monkeypatch):
    # Confirms memory never leaks into an unrelated pending-state flow:
    # once a DIFFERENT pending question gets armed, the stale remembered
    # order must not silently resolve that flow's own missing slot.
    intents = [
        IntentResult(intent="track_order", intent_confidence=1.0, slots={"order_id": "ORD-1003"}),
        IntentResult(intent="unknown", intent_confidence=0.0, slots={}),
        IntentResult(intent="cancel_order", intent_confidence=0.95, slots={}),
    ]
    _patch_intent(monkeypatch, intents)
    tools = FakeTools(track_result=TrackResult(outcome="found", order_id="ORD-1003", status="shipped", message="This order is shipped."))
    retrieval = FakeRetrievalResult(chunks=[FakeChunk("c1", "text")], confidence="High", top_dense_score=0.9)
    session_store = FakeSessionStore()
    agent = _make_agent(FakeRetriever(retrieval), session_store=session_store, tools=tools)

    agent.handle_message("track ORD-1003", "s1")
    assert session_store.get_state("s1").last_order_context != {}

    agent.handle_message("asdkfj qwerty nonsense", "s1")
    assert session_store.get_state("s1").pending == "awaiting_clarification"
    assert session_store.get_state("s1").last_order_context == {}

    third = agent.handle_message("actually I want to cancel an order", "s1")
    assert third.route == "action"
    assert "order number" in third.detail.lower()
    assert session_store.get_state("s1").pending == "awaiting_slot"


def test_delivery_dispute_after_delivered_tracking_escalates_with_reassuring_reply(monkeypatch):
    intents = [
        IntentResult(intent="track_order", intent_confidence=1.0, slots={"order_id": "ORD-1004"}),
        IntentResult(intent="track_order", intent_confidence=0.8, slots={}),
    ]
    _patch_intent(monkeypatch, intents)
    tools = FakeTools(track_result=TrackResult(outcome="found", order_id="ORD-1004", status="delivered", message="This order has already been delivered."))
    retrieval = FakeRetrievalResult(chunks=[], confidence="High", top_dense_score=0.9)
    session_store = FakeSessionStore()
    escalation = FakeEscalation()
    agent = _make_agent(FakeRetriever(retrieval), session_store=session_store, escalation=escalation, tools=tools)

    agent.handle_message("Track my order ORD-1004", "s1")
    second = agent.handle_message("but I didn't receive it !!", "s1")

    assert second.route == "escalate"
    assert second.escalation_id is None  # awaiting_contact -- no contact known yet
    assert second.detail == (
        "I'm sorry to hear that — I understand order ORD-1004 shows as delivered on our end, but "
        "that's clearly not right if it hasn't reached you. I've flagged this for a team member to "
        "look into the actual delivery. Before they reach out, could you share your email or order "
        "number so they can follow up directly?"
    )
    assert session_store.get_state("s1").pending == "awaiting_contact"
    assert escalation.finalize_calls == []

    third = agent.handle_message("sure, it's j.smith@example.com", "s1")

    assert third.route == "escalate"
    assert third.escalation_id is not None
    assert escalation.finalize_calls[0]["reason"] == "delivery_dispute_after_tracking"
    assert escalation.finalize_calls[0]["contact"] == "j.smith@example.com"
    summary = escalation.finalize_calls[0]["attempted_summary"]
    assert "ORD-1004" in summary
    assert "delivered" in summary.lower()
    assert "didn't receive it" in summary
    assert third.detail == (
        "Thanks — I've passed this along to our team with your contact info. They'll look into "
        "exactly what happened with order ORD-1004's delivery and follow up directly."
    )


def test_delivery_dispute_phrase_for_a_different_order_gets_a_fresh_lookup_not_a_dispute_escalation(monkeypatch):
    # Confirms the dispute check is scoped to the SAME order just reported
    # delivered -- a dispute phrase attached to a genuinely different order
    # number needs its own fresh tracking result, not to be swept into the
    # previous order's dispute escalation.
    intents = [
        IntentResult(intent="track_order", intent_confidence=1.0, slots={"order_id": "ORD-1004"}),
        IntentResult(intent="track_order", intent_confidence=0.9, slots={"order_id": "ORD-1005"}),
    ]
    _patch_intent(monkeypatch, intents)
    tools = FakeTools(track_result=TrackResult(outcome="found", order_id="ORD-1004", status="delivered", message="This order has already been delivered."))
    retrieval = FakeRetrievalResult(chunks=[], confidence="High", top_dense_score=0.9)
    session_store = FakeSessionStore()
    escalation = FakeEscalation()
    agent = _make_agent(FakeRetriever(retrieval), session_store=session_store, escalation=escalation, tools=tools)

    agent.handle_message("Track my order ORD-1004", "s1")
    second = agent.handle_message("ORD-1005 says delivered but I never received it", "s1")

    assert second.route == "action"  # a fresh lookup, NOT a dispute escalation
    assert tools.track_calls == ["ORD-1004", "ORD-1005"]
    assert escalation.finalize_calls == []
    assert session_store.get_state("s1").pending is None
