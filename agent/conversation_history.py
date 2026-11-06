"""Chat transcript persistence (Phase D, see ANALYSIS.md's Phase D entry).

Reuses agent/escalation.py's storage pattern and database file exactly —
SQLite, not a new store — same reasoning as agent/feedback.py: no cloud
deployment target exists for this project today, and a durable local file
already does this job for two other features.

Two concerns, two tables:
- `messages`: every /chat turn (both the customer's message and the
  agent's reply), keyed by session_id. Recorded unconditionally, by
  default — this is what makes "reload the page and your conversation is
  still there" work with zero identity concept at all.
- `session_identity`: an OPTIONAL, customer-initiated link from a
  session_id to an email, created only via identify_session() (the
  frontend's "save my history" action). Deliberately UNVERIFIED — no
  confirmation code, no password. This project has no email-sending
  capability anywhere (checked before proposing this design), and this
  tradeoff was confirmed with the user rather than assumed: anyone who
  knows/guesses a customer's email could look up their past conversations
  by it, which mirrors this project's already-accepted no-auth posture
  elsewhere (the Phase C admin dashboard is unauthenticated by the same
  reasoning). A verified magic-code flow is the correct alternative if
  this project ever adds a real email-sending dependency.

Once a session is identified, every subsequent message recorded on that
SAME session_id is auto-tagged with the linked email (record_message()
looks up session_identity itself) — the caller (app/main.py's /chat route)
never needs to know or pass the email on every turn.
"""

from __future__ import annotations

import datetime
import os
import sqlite3
import uuid
from typing import Optional

from app.config import ESCALATION_STORE_PATH
from app.schemas import MessageEntry


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class ConversationHistoryStore:
    def __init__(self, store_path: str = ESCALATION_STORE_PATH):
        self._store_path = store_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        directory = os.path.dirname(self._store_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        return sqlite3.connect(self._store_path)

    def _init_db(self) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    message_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    customer_email TEXT,
                    role TEXT NOT NULL,
                    text TEXT NOT NULL,
                    route TEXT,
                    intent TEXT,
                    timestamp TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_session_id ON messages(session_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_customer_email ON messages(customer_email)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS session_identity (
                    session_id TEXT PRIMARY KEY,
                    customer_email TEXT NOT NULL
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    def record_message(
        self, session_id: str, role: str, text: str, route: str = None, intent: str = None,
    ) -> MessageEntry:
        email = self._email_for_session(session_id)
        entry = MessageEntry(
            message_id=str(uuid.uuid4()),
            session_id=session_id,
            customer_email=email,
            role=role,
            text=text,
            route=route,
            intent=intent,
            timestamp=_utc_now_iso(),
        )
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO messages
                    (message_id, session_id, customer_email, role, text, route, intent, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.message_id, entry.session_id, entry.customer_email, entry.role,
                    entry.text, entry.route, entry.intent, entry.timestamp,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return entry

    def identify_session(self, session_id: str, email: str) -> int:
        """Links `session_id` to `email` (unverified) and backfills every
        message already recorded under it. Returns the number of rows
        backfilled. Future messages on this session_id are auto-tagged by
        `record_message()`'s own lookup — the caller never has to pass
        `email` again."""
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO session_identity (session_id, customer_email) VALUES (?, ?)
                ON CONFLICT(session_id) DO UPDATE SET customer_email = excluded.customer_email
                """,
                (session_id, email),
            )
            cursor = conn.execute(
                "UPDATE messages SET customer_email = ? WHERE session_id = ?", (email, session_id)
            )
            conn.commit()
            return cursor.rowcount
        finally:
            conn.close()

    def list_by_session(self, session_id: str) -> list:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM messages WHERE session_id = ? ORDER BY timestamp ASC", (session_id,)
            ).fetchall()
        finally:
            conn.close()
        return [self._row_to_entry(row) for row in rows]

    def list_by_email(self, email: str) -> list:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM messages WHERE customer_email = ? ORDER BY timestamp ASC", (email,)
            ).fetchall()
        finally:
            conn.close()
        return [self._row_to_entry(row) for row in rows]

    # ------------------------------------------------------------------

    def _email_for_session(self, session_id: str) -> Optional[str]:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT customer_email FROM session_identity WHERE session_id = ?", (session_id,)
            ).fetchone()
        finally:
            conn.close()
        return row[0] if row else None

    def _row_to_entry(self, row) -> MessageEntry:
        message_id, session_id, customer_email, role, text, route, intent, timestamp = row
        return MessageEntry(
            message_id=message_id, session_id=session_id, customer_email=customer_email,
            role=role, text=text, route=route, intent=intent, timestamp=timestamp,
        )

    # ------------------------------------------------------------------
    # Phase D usage analytics (see ANALYSIS.md's Phase D entry) — pure
    # read-only aggregation over `messages`, the same table already
    # populated for chat-history persistence. No new collection, no new
    # store: this project has no cloud deployment target (same finding as
    # every other Phase D storage decision), and these are ranked/bucketed
    # results that don't fit /metrics's Prometheus single-gauge shape.
    # ------------------------------------------------------------------

    def top_intents(self, limit: int = 10) -> list:
        """Most common questions — every classified intent (including
        "smalltalk"/None), ranked by frequency. Deliberately NOT filtered
        to "real" questions only — hiding smalltalk would be an editorial
        judgment call this method has no business making; the caller/UI
        decides what to do with the raw counts."""
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT COALESCE(intent, 'unknown') AS intent, COUNT(*) AS count
                FROM messages
                WHERE role = 'assistant'
                GROUP BY intent
                ORDER BY count DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        finally:
            conn.close()
        return [{"intent": row[0], "count": row[1]} for row in rows]

    def hourly_volume(self) -> list:
        """Peak times as an hour-of-day (0-23, UTC) histogram over every
        customer message ever recorded — not a calendar time series, which
        would be too sparse to be meaningful at this project's current
        data volume. Always returns all 24 hours, zero-filled, so the
        caller doesn't have to handle missing buckets."""
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT CAST(strftime('%H', timestamp) AS INTEGER) AS hour, COUNT(*) AS count
                FROM messages
                WHERE role = 'user'
                GROUP BY hour
                """
            ).fetchall()
        finally:
            conn.close()
        counts_by_hour = {row[0]: row[1] for row in rows}
        return [{"hour": h, "count": counts_by_hour.get(h, 0)} for h in range(24)]

    def dropoff_summary(self) -> dict:
        """A proxy for drop-off, not a rigorous funnel: counts sessions
        with exactly one customer message (asked once, never followed up)
        against sessions with more than one. This can't distinguish "got
        their answer and left satisfied" from "gave up confused" — it's
        the honest limit of what this data alone can show."""
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT session_id, COUNT(*) AS user_turns
                FROM messages
                WHERE role = 'user'
                GROUP BY session_id
                """
            ).fetchall()
        finally:
            conn.close()
        single_turn = sum(1 for _, turns in rows if turns == 1)
        multi_turn = sum(1 for _, turns in rows if turns > 1)
        return {"single_turn_sessions": single_turn, "multi_turn_sessions": multi_turn}
