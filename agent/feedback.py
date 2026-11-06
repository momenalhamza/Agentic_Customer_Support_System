"""Thumbs up/down feedback storage (Phase C, see ANALYSIS.md's Phase C
entry). Reuses agent/escalation.py's storage pattern and database file
exactly — SQLite, not a new store — since the project already has one
durable local store and a second mechanism would just be more surface area
for the same job.
"""

from __future__ import annotations

import datetime
import os
import sqlite3
import uuid

from app.config import ESCALATION_STORE_PATH
from app.schemas import FeedbackEntry


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class FeedbackHandler:
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
                CREATE TABLE IF NOT EXISTS feedback (
                    feedback_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    query TEXT NOT NULL,
                    response_text TEXT NOT NULL,
                    route TEXT NOT NULL,
                    intent TEXT NOT NULL,
                    thumbs_up INTEGER NOT NULL,
                    timestamp TEXT NOT NULL
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    def record(
        self, session_id: str, query: str, response_text: str, route: str, intent: str, thumbs_up: bool,
    ) -> FeedbackEntry:
        entry = FeedbackEntry(
            feedback_id=str(uuid.uuid4()),
            session_id=session_id,
            query=query,
            response_text=response_text,
            route=route,
            intent=intent,
            thumbs_up=thumbs_up,
            timestamp=_utc_now_iso(),
        )
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO feedback
                    (feedback_id, session_id, query, response_text, route, intent, thumbs_up, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.feedback_id, entry.session_id, entry.query, entry.response_text,
                    entry.route, entry.intent, int(entry.thumbs_up), entry.timestamp,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return entry

    def list_feedback(self) -> list:
        conn = self._connect()
        try:
            rows = conn.execute("SELECT * FROM feedback ORDER BY timestamp DESC").fetchall()
        finally:
            conn.close()
        return [self._row_to_entry(row) for row in rows]

    def _row_to_entry(self, row) -> FeedbackEntry:
        feedback_id, session_id, query, response_text, route, intent, thumbs_up, timestamp = row
        return FeedbackEntry(
            feedback_id=feedback_id, session_id=session_id, query=query, response_text=response_text,
            route=route, intent=intent, thumbs_up=bool(thumbs_up), timestamp=timestamp,
        )
