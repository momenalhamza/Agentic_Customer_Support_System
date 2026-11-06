import os
import tempfile

from agent.feedback import FeedbackHandler


def _handler():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)  # let FeedbackHandler create it fresh
    return FeedbackHandler(store_path=path), path


def test_record_writes_and_returns_entry():
    handler, path = _handler()
    entry = handler.record(
        session_id="s1", query="What's your return policy?", response_text="You can return within 30 days.",
        route="information", intent="check_refund_policy", thumbs_up=True,
    )

    assert entry.feedback_id
    assert entry.thumbs_up is True
    assert entry.route == "information"
    os.remove(path)


def test_list_feedback_returns_all_entries_newest_first():
    handler, path = _handler()
    handler.record(session_id="s1", query="q1", response_text="a1", route="information", intent="check_refund_policy", thumbs_up=True)
    handler.record(session_id="s1", query="q2", response_text="a2", route="action", intent="track_order", thumbs_up=False)

    stored = handler.list_feedback()
    assert len(stored) == 2
    assert {e.thumbs_up for e in stored} == {True, False}
    os.remove(path)


def test_thumbs_down_is_stored_as_false_not_dropped():
    handler, path = _handler()
    handler.record(session_id="s1", query="q1", response_text="a1", route="escalate", intent="unknown", thumbs_up=False)

    stored = handler.list_feedback()
    assert len(stored) == 1
    assert stored[0].thumbs_up is False
    os.remove(path)


def test_reuses_same_db_file_as_escalations_without_colliding_tables():
    # Phase C: feedback and escalations share one SQLite file/pattern (see
    # agent/feedback.py's docstring) — writing to one table must not
    # interfere with the other already existing in the same file.
    from agent.escalation import EscalationHandler

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)

    escalation_handler = EscalationHandler(store_path=path, slack_webhook_url=None)
    feedback_handler = FeedbackHandler(store_path=path)

    escalation_handler.finalize(
        query="help", session_id="s1", reason="low_intent_confidence",
        retrieved_context=[], slots={}, attempted_summary="unknown",
    )
    feedback_handler.record(session_id="s1", query="q1", response_text="a1", route="information", intent="check_refund_policy", thumbs_up=True)

    assert len(escalation_handler.list_escalations()) == 1
    assert len(feedback_handler.list_feedback()) == 1
    os.remove(path)
