import os
import tempfile

from agent.conversation_history import ConversationHistoryStore


def _store():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)  # let ConversationHistoryStore create it fresh
    return ConversationHistoryStore(store_path=path), path


def test_record_message_roundtrips():
    store, path = _store()
    entry = store.record_message(session_id="s1", role="user", text="Where's my order?")

    assert entry.message_id
    assert entry.session_id == "s1"
    assert entry.role == "user"
    assert entry.customer_email is None
    os.remove(path)


def test_list_by_session_returns_messages_in_order():
    store, path = _store()
    store.record_message(session_id="s1", role="user", text="first")
    store.record_message(session_id="s1", role="assistant", text="second", route="information", intent="delivery_options")
    store.record_message(session_id="s2", role="user", text="different session")

    messages = store.list_by_session("s1")
    assert [m.text for m in messages] == ["first", "second"]
    assert messages[1].route == "information"
    assert messages[1].intent == "delivery_options"
    os.remove(path)


def test_identify_session_backfills_existing_messages():
    store, path = _store()
    store.record_message(session_id="s1", role="user", text="q1")
    store.record_message(session_id="s1", role="assistant", text="a1")

    linked = store.identify_session("s1", "customer@example.com")

    assert linked == 2
    messages = store.list_by_session("s1")
    assert all(m.customer_email == "customer@example.com" for m in messages)
    os.remove(path)


def test_identify_session_then_future_messages_auto_tagged():
    store, path = _store()
    store.record_message(session_id="s1", role="user", text="q1")
    store.identify_session("s1", "customer@example.com")

    later = store.record_message(session_id="s1", role="assistant", text="a1 after identify")
    assert later.customer_email == "customer@example.com"
    os.remove(path)


def test_list_by_email_spans_multiple_sessions():
    store, path = _store()
    # Same customer, two different devices/sessions.
    store.record_message(session_id="device-a", role="user", text="from device A")
    store.identify_session("device-a", "customer@example.com")
    store.record_message(session_id="device-b", role="user", text="from device B")
    store.identify_session("device-b", "customer@example.com")
    store.record_message(session_id="unrelated-session", role="user", text="someone else")

    messages = store.list_by_email("customer@example.com")
    texts = {m.text for m in messages}
    assert texts == {"from device A", "from device B"}
    os.remove(path)


# --- Phase D: usage analytics -------------------------------------------------


def test_top_intents_ranks_by_frequency():
    store, path = _store()
    store.record_message(session_id="s1", role="assistant", text="a", route="information", intent="check_refund_policy")
    store.record_message(session_id="s2", role="assistant", text="a", route="information", intent="check_refund_policy")
    store.record_message(session_id="s3", role="assistant", text="a", route="action", intent="track_order")
    # Only "assistant" rows count — a "user" row must not be double-counted.
    store.record_message(session_id="s1", role="user", text="q")

    top = store.top_intents(limit=10)
    assert top[0] == {"intent": "check_refund_policy", "count": 2}
    assert {"intent": "track_order", "count": 1} in top
    os.remove(path)


def test_top_intents_respects_limit():
    store, path = _store()
    for i, intent in enumerate(["a", "b", "c"]):
        store.record_message(session_id=f"s{i}", role="assistant", text="x", route="information", intent=intent)

    assert len(store.top_intents(limit=2)) == 2
    os.remove(path)


def test_top_intents_labels_missing_intent_as_unknown():
    store, path = _store()
    store.record_message(session_id="s1", role="assistant", text="a")  # no intent given

    top = store.top_intents(limit=10)
    assert top == [{"intent": "unknown", "count": 1}]
    os.remove(path)


def test_hourly_volume_always_returns_24_zero_filled_hours():
    store, path = _store()
    store.record_message(session_id="s1", role="user", text="q1")
    store.record_message(session_id="s1", role="assistant", text="a1")  # assistant row must not count
    store.record_message(session_id="s2", role="user", text="q2")

    hours = store.hourly_volume()
    assert len(hours) == 24
    assert [h["hour"] for h in hours] == list(range(24))
    assert sum(h["count"] for h in hours) == 2  # only the 2 user messages
    os.remove(path)


def test_dropoff_summary_distinguishes_single_and_multi_turn_sessions():
    store, path = _store()
    # session A: asked once, never followed up
    store.record_message(session_id="A", role="user", text="q1")
    # session B: two customer turns
    store.record_message(session_id="B", role="user", text="q1")
    store.record_message(session_id="B", role="assistant", text="a1")
    store.record_message(session_id="B", role="user", text="q2")

    summary = store.dropoff_summary()
    assert summary == {"single_turn_sessions": 1, "multi_turn_sessions": 1}
    os.remove(path)


def test_reuses_same_db_file_as_escalations_and_feedback_without_colliding_tables():
    from agent.escalation import EscalationHandler
    from agent.feedback import FeedbackHandler

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)

    escalation_handler = EscalationHandler(store_path=path, slack_webhook_url=None)
    feedback_handler = FeedbackHandler(store_path=path)
    history_store = ConversationHistoryStore(store_path=path)

    escalation_handler.finalize(
        query="help", session_id="s1", reason="low_intent_confidence",
        retrieved_context=[], slots={}, attempted_summary="unknown",
    )
    feedback_handler.record(
        session_id="s1", query="q1", response_text="a1", route="information",
        intent="check_refund_policy", thumbs_up=True,
    )
    history_store.record_message(session_id="s1", role="user", text="q1")

    assert len(escalation_handler.list_escalations()) == 1
    assert len(feedback_handler.list_feedback()) == 1
    assert len(history_store.list_by_session("s1")) == 1
    os.remove(path)
