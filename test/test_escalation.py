import os
import tempfile

import pytest
import requests

from agent.escalation import EscalationHandler


def _handler(slack_webhook_url=None):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)  # let EscalationHandler create it fresh
    return EscalationHandler(store_path=path, slack_webhook_url=slack_webhook_url), path


def test_finalize_writes_record_before_attempting_slack():
    handler, path = _handler(slack_webhook_url=None)
    packet = handler.finalize(
        query="I need help", session_id="s1", reason="low_intent_confidence",
        retrieved_context=[], slots={}, attempted_summary="Classified as intent 'unknown'.",
    )

    assert packet.escalation_id
    assert packet.slack_delivered is False  # no webhook configured
    stored = handler.list_escalations()
    assert len(stored) == 1
    assert stored[0].escalation_id == packet.escalation_id
    os.remove(path)


# --- check_known_contact() — passive check, email/phone only (M9 fix) -------


def test_check_known_contact_from_email_slot():
    handler, path = _handler()
    assert handler.check_known_contact({"email": "j.smith@example.com"}, "help") == "j.smith@example.com"
    os.remove(path)


def test_check_known_contact_from_phone_slot():
    handler, path = _handler()
    assert handler.check_known_contact({"phone": "555-123-4567"}, "help") == "555-123-4567"
    os.remove(path)


def test_check_known_contact_ignores_order_id_slot():
    # M9 bug fix: a bare order_id is NOT trustworthy contact info — it can
    # survive slot-grounding by literal accident (e.g. gibberish that
    # happens to be a substring of the message) without being a genuine
    # way to reach the customer.
    handler, path = _handler()
    assert handler.check_known_contact({"order_id": "asdf1234"}, "asdf1234 zzz order thing help???") is None
    os.remove(path)


def test_check_known_contact_from_email_in_raw_query():
    handler, path = _handler()
    assert handler.check_known_contact({}, "please reach me at j.smith@example.com") == "j.smith@example.com"
    os.remove(path)


def test_check_known_contact_returns_none_when_absent():
    handler, path = _handler()
    assert handler.check_known_contact({}, "I need help") is None
    os.remove(path)


# --- finalize() — contact resolution end to end -----------------------------


def test_finalize_without_explicit_contact_uses_passive_check():
    handler, path = _handler()
    packet = handler.finalize(
        query="please reach me at j.smith@example.com", session_id="s1", reason="low_intent_confidence",
        retrieved_context=[], slots={}, attempted_summary="summary",
    )
    assert packet.contact_captured is True
    assert packet.contact == "j.smith@example.com"
    os.remove(path)


def test_finalize_with_explicit_contact_overrides_passive_check():
    # Simulates agent.py's awaiting_contact round-trip: an order_id IS
    # trusted here, since it's a direct answer to "please share your email
    # or order number," unlike the general passive check.
    handler, path = _handler()
    packet = handler.finalize(
        query="the thing with my stuff is broken", session_id="s1", reason="still_unclear_after_clarification",
        retrieved_context=[], slots={}, attempted_summary="summary", explicit_contact="ORD-1002",
    )
    assert packet.contact_captured is True
    assert packet.contact == "ORD-1002"
    os.remove(path)


def test_finalize_contact_not_captured_when_absent():
    handler, path = _handler()
    packet = handler.finalize(
        query="I need help", session_id="s1", reason="low_intent_confidence",
        retrieved_context=[], slots={}, attempted_summary="summary",
    )
    assert packet.contact_captured is False
    assert packet.contact is None
    os.remove(path)


def test_finalize_retrieved_context_and_summary_roundtrip():
    handler, path = _handler()
    handler.finalize(
        query="q", session_id="s1", reason="ungrounded_after_retry",
        retrieved_context=["chunk one text", "chunk two text"], slots={},
        attempted_summary="Retrieved twice, still ungrounded.",
    )
    stored = handler.list_escalations()
    assert stored[0].retrieved_context == ["chunk one text", "chunk two text"]
    assert stored[0].attempted_summary == "Retrieved twice, still ungrounded."
    os.remove(path)


# --- Phase D: attachment_id ---------------------------------------------------


def test_finalize_attachment_id_roundtrips():
    handler, path = _handler()
    packet = handler.finalize(
        query="it arrived damaged", session_id="s1", reason="low_retrieval_relevance",
        retrieved_context=[], slots={}, attempted_summary="summary", attachment_id="abc-123",
    )
    assert packet.attachment_id == "abc-123"
    stored = handler.list_escalations()
    assert stored[0].attachment_id == "abc-123"
    os.remove(path)


def test_finalize_without_attachment_id_stores_none():
    handler, path = _handler()
    handler.finalize(
        query="q", session_id="s1", reason="low_intent_confidence",
        retrieved_context=[], slots={}, attempted_summary="summary",
    )
    stored = handler.list_escalations()
    assert stored[0].attachment_id is None
    os.remove(path)


# --- Slack format ------------------------------------------------------------


def test_slack_message_includes_summary_not_bare_reason(monkeypatch):
    handler, path = _handler(slack_webhook_url="https://hooks.slack.test/fake")
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

    def _post(url, json, timeout):
        captured["payload"] = json
        return FakeResponse()

    monkeypatch.setattr("agent.escalation.requests.post", _post)
    handler.finalize(
        query="the thing with my stuff is broken idk what to do", session_id="s1",
        reason="still_unclear_after_clarification_low_intent_confidence", retrieved_context=[], slots={},
        attempted_summary="Classified as intent 'complaint' (confidence 40%). Asked one clarifying "
        "question; the reply was still unclear.",
    )
    block_texts = [b["text"]["text"] for b in captured["payload"]["blocks"] if "text" in b]
    all_text = "\n".join(block_texts)
    assert "Classified as intent 'complaint'" in all_text
    assert "Asked one clarifying question" in all_text
    assert "the thing with my stuff is broken idk what to do" in all_text
    os.remove(path)


def test_slack_message_includes_attachment_link_when_present(monkeypatch):
    handler, path = _handler(slack_webhook_url="https://hooks.slack.test/fake")
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

    def _post(url, json, timeout):
        captured["payload"] = json
        return FakeResponse()

    monkeypatch.setattr("agent.escalation.requests.post", _post)
    handler.finalize(
        query="it arrived damaged", session_id="s1", reason="low_retrieval_relevance",
        retrieved_context=[], slots={}, attempted_summary="summary", attachment_id="abc-123",
    )
    block_texts = [b["text"]["text"] for b in captured["payload"]["blocks"] if "text" in b]
    all_text = "\n".join(block_texts)
    assert "abc-123" in all_text
    assert "Attachment" in all_text
    os.remove(path)


def test_slack_message_omits_attachment_block_when_absent(monkeypatch):
    handler, path = _handler(slack_webhook_url="https://hooks.slack.test/fake")
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

    def _post(url, json, timeout):
        captured["payload"] = json
        return FakeResponse()

    monkeypatch.setattr("agent.escalation.requests.post", _post)
    handler.finalize(
        query="q", session_id="s1", reason="low_intent_confidence",
        retrieved_context=[], slots={}, attempted_summary="summary",
    )
    block_texts = [b["text"]["text"] for b in captured["payload"]["blocks"] if "text" in b]
    all_text = "\n".join(block_texts)
    assert "Attachment" not in all_text
    os.remove(path)


def test_slack_message_uses_block_kit_structure(monkeypatch):
    # M10: bold header line with the escalation ID, a divider, then each
    # field (Customer Query / What We Tried / Contact) as its own section
    # with a bold label -- not a wall of concatenated text.
    handler, path = _handler(slack_webhook_url="https://hooks.slack.test/fake")
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

    def _post(url, json, timeout):
        captured["payload"] = json
        return FakeResponse()

    monkeypatch.setattr("agent.escalation.requests.post", _post)
    packet = handler.finalize(
        query="the thing with my stuff is broken idk what to do", session_id="s1",
        reason="ungrounded_after_retry", retrieved_context=[], slots={},
        attempted_summary="Retrieved twice, still ungrounded.", explicit_contact="j.smith@example.com",
    )

    payload = captured["payload"]
    assert "text" in payload  # plain-text fallback for notifications/screen readers
    blocks = payload["blocks"]

    assert blocks[0]["type"] == "header"
    assert packet.escalation_id in blocks[0]["text"]["text"]

    assert blocks[1] == {"type": "divider"}

    section_texts = [b["text"]["text"] for b in blocks[2:]]
    assert any(t.startswith("*Customer Query:*") and "the thing with my stuff" in t for t in section_texts)
    assert any(t.startswith("*What We Tried:*") and "Retrieved twice" in t for t in section_texts)
    assert any(t.startswith("*Contact:*") and "j.smith@example.com" in t for t in section_texts)
    os.remove(path)


def test_slack_message_shows_not_captured_when_contact_missing(monkeypatch):
    handler, path = _handler(slack_webhook_url="https://hooks.slack.test/fake")
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

    def _post(url, json, timeout):
        captured["payload"] = json
        return FakeResponse()

    monkeypatch.setattr("agent.escalation.requests.post", _post)
    handler.finalize(
        query="q", session_id="s1", reason="low_intent_confidence",
        retrieved_context=[], slots={}, attempted_summary="summary",
    )
    section_texts = [b["text"]["text"] for b in captured["payload"]["blocks"][2:]]
    assert any(t == "*Contact:*\nNot captured" for t in section_texts)
    os.remove(path)


def test_slack_message_truncates_long_query_but_storage_is_untouched(monkeypatch):
    handler, path = _handler(slack_webhook_url="https://hooks.slack.test/fake")
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

    def _post(url, json, timeout):
        captured["payload"] = json
        return FakeResponse()

    monkeypatch.setattr("agent.escalation.requests.post", _post)
    long_query = "the thing with my stuff is broken " * 10  # well over 200 chars
    packet = handler.finalize(
        query=long_query, session_id="s1", reason="low_intent_confidence",
        retrieved_context=[], slots={}, attempted_summary="summary",
    )

    # Slack display is capped and marked with "...".
    section_texts = [b["text"]["text"] for b in captured["payload"]["blocks"][2:]]
    query_block = next(t for t in section_texts if t.startswith("*Customer Query:*"))
    displayed_query = query_block.removeprefix("*Customer Query:*\n")
    assert len(displayed_query) < len(long_query)
    assert displayed_query.endswith("...")

    # The stored record and the returned packet are the full, untruncated text.
    assert packet.query == long_query
    stored = handler.list_escalations()
    assert stored[0].query == long_query
    os.remove(path)


def test_slack_message_does_not_truncate_short_query(monkeypatch):
    handler, path = _handler(slack_webhook_url="https://hooks.slack.test/fake")
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

    def _post(url, json, timeout):
        captured["payload"] = json
        return FakeResponse()

    monkeypatch.setattr("agent.escalation.requests.post", _post)
    handler.finalize(
        query="a short message", session_id="s1", reason="low_intent_confidence",
        retrieved_context=[], slots={}, attempted_summary="summary",
    )
    section_texts = [b["text"]["text"] for b in captured["payload"]["blocks"][2:]]
    assert any(t == "*Customer Query:*\na short message" for t in section_texts)
    os.remove(path)


def test_slack_success_updates_delivered_flag(monkeypatch):
    handler, path = _handler(slack_webhook_url="https://hooks.slack.test/fake")

    class FakeResponse:
        def raise_for_status(self):
            pass

    monkeypatch.setattr("agent.escalation.requests.post", lambda *a, **k: FakeResponse())

    packet = handler.finalize(
        query="q", session_id="s1", reason="low_intent_confidence",
        retrieved_context=[], slots={}, attempted_summary="summary",
    )
    assert packet.slack_delivered is True
    assert handler.list_escalations()[0].slack_delivered is True
    os.remove(path)


def test_slack_failure_does_not_break_response_or_lose_the_record(monkeypatch):
    handler, path = _handler(slack_webhook_url="https://hooks.slack.test/unreachable")

    def _raise(*a, **k):
        raise requests.ConnectionError("simulated webhook outage")

    monkeypatch.setattr("agent.escalation.requests.post", _raise)

    packet = handler.finalize(
        query="q", session_id="s1", reason="low_intent_confidence",
        retrieved_context=[], slots={}, attempted_summary="summary",
    )

    assert packet.slack_delivered is False  # degrades gracefully, no exception raised
    assert packet.escalation_id  # the record still exists
    stored = handler.list_escalations()
    assert len(stored) == 1
    assert stored[0].slack_delivered is False
    os.remove(path)


def test_list_escalations_filters_by_slack_delivered(monkeypatch):
    handler, path = _handler(slack_webhook_url="https://hooks.slack.test/fake")

    responses = iter([True, False])

    def _post(*a, **k):
        if next(responses):
            class Ok:
                def raise_for_status(self):
                    pass
            return Ok()
        raise requests.ConnectionError("fail")

    monkeypatch.setattr("agent.escalation.requests.post", _post)

    handler.finalize(query="q1", session_id="s1", reason="r1", retrieved_context=[], slots={}, attempted_summary="s")
    handler.finalize(query="q2", session_id="s2", reason="r2", retrieved_context=[], slots={}, attempted_summary="s")

    delivered = handler.list_escalations(slack_delivered=True)
    failed = handler.list_escalations(slack_delivered=False)

    assert len(delivered) == 1 and delivered[0].query == "q1"
    assert len(failed) == 1 and failed[0].query == "q2"
    os.remove(path)


# --- Phase D: live human handoff ----------------------------------------------


def test_respond_updates_status_and_reply():
    handler, path = _handler()
    packet = handler.finalize(
        query="q1", session_id="s1", reason="low_intent_confidence",
        retrieved_context=[], slots={}, attempted_summary="s",
    )
    assert packet.human_status == "waiting"

    updated = handler.respond(packet.escalation_id, status="responded", reply="I've looked into this for you.")

    assert updated.human_status == "responded"
    assert updated.human_reply == "I've looked into this for you."
    assert updated.responded_at is not None
    os.remove(path)


def test_respond_picked_up_does_not_set_responded_at():
    handler, path = _handler()
    packet = handler.finalize(
        query="q1", session_id="s1", reason="low_intent_confidence",
        retrieved_context=[], slots={}, attempted_summary="s",
    )
    updated = handler.respond(packet.escalation_id, status="picked_up")

    assert updated.human_status == "picked_up"
    assert updated.human_reply is None
    assert updated.responded_at is None
    os.remove(path)


def test_respond_unknown_escalation_id_returns_none():
    handler, path = _handler()
    assert handler.respond("does-not-exist", status="responded", reply="hi") is None
    os.remove(path)


def test_respond_without_reply_keeps_existing_reply():
    handler, path = _handler()
    packet = handler.finalize(
        query="q1", session_id="s1", reason="low_intent_confidence",
        retrieved_context=[], slots={}, attempted_summary="s",
    )
    handler.respond(packet.escalation_id, status="picked_up")
    updated = handler.respond(packet.escalation_id, status="responded", reply="final answer")

    assert updated.human_reply == "final answer"
    os.remove(path)


def test_get_status_returns_minimal_view():
    handler, path = _handler()
    packet = handler.finalize(
        query="q1", session_id="s1", reason="low_intent_confidence",
        retrieved_context=[], slots={}, attempted_summary="s",
    )
    handler.respond(packet.escalation_id, status="responded", reply="done")

    status = handler.get_status(packet.escalation_id)

    assert status.escalation_id == packet.escalation_id
    assert status.human_status == "responded"
    assert status.human_reply == "done"
    os.remove(path)


def test_get_status_unknown_id_returns_none():
    handler, path = _handler()
    assert handler.get_status("does-not-exist") is None
    os.remove(path)


def test_new_escalations_default_to_waiting_status():
    handler, path = _handler()
    packet = handler.finalize(
        query="q1", session_id="s1", reason="low_intent_confidence",
        retrieved_context=[], slots={}, attempted_summary="s",
    )
    assert packet.human_status == "waiting"
    assert packet.human_reply is None
    assert packet.responded_at is None
    os.remove(path)
