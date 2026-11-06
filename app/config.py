"""Loads and validates every environment variable the backend/agent runtime needs.

This module and ingestion/config.py are the ONLY two places in this codebase
allowed to read os.environ directly (see ARCHITECTURE.md §10.1). Every
app/ and agent/ module imports its settings from here.
"""

import os

from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            f"Copy .env.example to .env and fill in a real value."
        )
    return value


# --- Groq LLM API ------------------------------------------------------------
GROQ_API_KEY = _require("GROQ_API_KEY")
GROQ_MODEL_DEFAULT = os.environ.get("GROQ_MODEL_DEFAULT", "llama-3.1-8b-instant")
GROQ_MODEL_UPGRADE = os.environ.get("GROQ_MODEL_UPGRADE", "llama-3.3-70b-versatile")
GROQ_MAX_RETRIES = int(os.environ.get("GROQ_MAX_RETRIES", 3))
GROQ_RETRY_BACKOFF_SECONDS = float(os.environ.get("GROQ_RETRY_BACKOFF_SECONDS", 1.0))
# Circuit breaker (§8.1): once a model's remaining token quota (from Groq's
# x-ratelimit-remaining-tokens response header) drops below this fraction of
# its limit, llm_client.py forces every subsequent call in this process to
# GROQ_MODEL_DEFAULT regardless of which model was requested.
GROQ_CIRCUIT_BREAKER_MIN_REMAINING_PCT = float(
    os.environ.get("GROQ_CIRCUIT_BREAKER_MIN_REMAINING_PCT", 0.10)
)

# --- Weaviate ----------------------------------------------------------------
WEAVIATE_HOST = os.environ.get("WEAVIATE_HOST", "localhost")
WEAVIATE_PORT = int(os.environ.get("WEAVIATE_PORT", 8080))
WEAVIATE_GRPC_PORT = int(os.environ.get("WEAVIATE_GRPC_PORT", 50051))
WEAVIATE_COLLECTION_NAME = os.environ.get("WEAVIATE_COLLECTION_NAME", "Chunk")

# --- Embedding model ---------------------------------------------------------
# Same variable ingestion/config.py reads — must stay identical so
# retrieval/weaviate_retriever.py and agent/groundedness.py embed into the
# exact vector space ingestion/embedder.py wrote chunks into.
EMBEDDING_MODEL_NAME = os.environ.get("EMBEDDING_MODEL_NAME", "BAAI/bge-large-en-v1.5")

# --- Neo4j ---------------------------------------------------------------------
NEO4J_URI = _require("NEO4J_URI")
NEO4J_USERNAME = _require("NEO4J_USERNAME")
NEO4J_PASSWORD = _require("NEO4J_PASSWORD")

# --- Redis ---------------------------------------------------------------------
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

# --- Retrieval / groundedness thresholds --------------------------------------
RETRIEVAL_TOP_K = int(os.environ.get("RETRIEVAL_TOP_K", 5))
MIN_CONFIDENCE_HIGH = float(os.environ.get("MIN_CONFIDENCE_HIGH", 0.75))
MIN_CONFIDENCE_MED = float(os.environ.get("MIN_CONFIDENCE_MED", 0.45))
GROUNDEDNESS_SIMILARITY_THRESHOLD = float(
    os.environ.get("GROUNDEDNESS_SIMILARITY_THRESHOLD", 0.55)
)
# Not in ARCHITECTURE.md's config.py snippet — added in M4 to make §8's
# "high intent_confidence" routing condition concrete rather than a vague
# adjective. See ANALYSIS.md's M4 entry for the resolved routing logic.
MIN_INTENT_CONFIDENCE = float(os.environ.get("MIN_INTENT_CONFIDENCE", 0.6))
# §12.3: reuses agent/groundedness.py's exact scoring mechanism for scoring
# eval/run_eval.py's answer-correctness metric — same function, a separate
# threshold, not a second implementation. Recalibrated 0.55 -> 0.60 after
# M6's first real eval run: 0.55 produced a confirmed false positive
# (heldout-003, score 0.567 for an answer that doesn't address the question)
# plus two more on manual inspection (heldout-053, heldout-037's baseline
# answer) — all in the [0.55, 0.60) band. Every example in that band was
# manually checked against its expected_answer; none were genuine matches,
# so 0.60 fixes all three without introducing any new false negatives
# (nothing between 0.55 and 0.60 was a true positive). See ANALYSIS.md's
# M6 Results section and ERRORS.md's Error #8 for the full evidence.
ANSWER_MATCH_THRESHOLD = float(os.environ.get("ANSWER_MATCH_THRESHOLD", 0.60))

# M8: two independent hard floors checked in agent.py's INFORMATION branch,
# in addition to (and now superseding — see below) the MIN_CONFIDENCE_MED/HIGH
# relative bucketing above. Added after M6's eval found 4 of 6 adversarial
# held-out examples (fluent-but-off-topic or lexically-sparse-but-dense-
# plausible messages) escaped escalation because MIN_CONFIDENCE_MED=0.45 was
# too permissive, and a single dense-similarity floor alone can't separate
# them from legitimate low-confidence queries (heldout-059's dense score
# 0.586 is statistically tied with heldout-053's legitimate 0.5855). Since
# MIN_ESCALATION_DENSE_FLOOR (0.55) is already higher than MIN_CONFIDENCE_MED
# (0.45), the old confidence=="Low" escalation check is now fully subsumed
# by the dense floor and was removed from agent.py rather than left as dead,
# redundant code. MIN_CONFIDENCE_MED/HIGH and confidence_label() themselves
# are untouched — they have no other consumer today, but are left as a
# general-purpose relative bucketing utility, not repurposed for this one
# escalation decision. Both floor values are evidence-derived from the M6
# held-out set (checked against all 60 examples, not just the 4 failures) —
# see ANALYSIS.md's M8 entry for the full evidence table.
MIN_ESCALATION_DENSE_FLOOR = float(os.environ.get("MIN_ESCALATION_DENSE_FLOOR", 0.55))
MIN_ESCALATION_BM25_FLOOR = float(os.environ.get("MIN_ESCALATION_BM25_FLOOR", 4.0))

# --- Escalation ------------------------------------------------------------
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL") or None
# SQLite, not JSONL (see ANALYSIS.md's M5 entry): the packet is written
# BEFORE the Slack attempt (§8 step 3, guaranteed-first ordering), then its
# slack_delivered column is updated after — a real UPDATE/WHERE, and
# GET /escalations?slack_delivered=false needs a real WHERE filter too.
# Both are awkward with an append-only JSONL file, trivial with SQLite.
ESCALATION_STORE_PATH = os.environ.get("ESCALATION_STORE_PATH", "./eval/escalations.db")

# --- Mock order store (agent/tools.py) ---------------------------------------
MOCK_ORDERS_PATH = os.environ.get("MOCK_ORDERS_PATH", "data/mock_orders.json")

# --- Attachments (Phase D, see ANALYSIS.md) -----------------------------------
# Local disk, same reasoning as ESCALATION_STORE_PATH above — no cloud
# deployment target exists for this project today (see ANALYSIS.md's Phase D
# entry), so a cloud bucket would be speculative infrastructure with nothing
# to point it at. Kept behind agent/attachments.py so swapping to one later,
# if this project ever gets a real hosted deployment, is a contained change.
ATTACHMENTS_STORE_PATH = os.environ.get("ATTACHMENTS_STORE_PATH", "./data/attachments")
ATTACHMENTS_MAX_BYTES = int(os.environ.get("ATTACHMENTS_MAX_BYTES", 5 * 1024 * 1024))  # 5 MB
ATTACHMENTS_ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}

# --- Admin write actions (Phase D, live human handoff — see ANALYSIS.md) -----
# Gates POST /escalations/{id}/respond only — every other admin-dashboard
# route (Phase C) stays unauthenticated by the same reasoning as before; this
# one is a WRITE (a human can post a reply that appears in a customer's live
# conversation), a meaningfully bigger exposure than a read-only dashboard,
# confirmed with the user as worth a minimal gate. Unset (the default) means
# the endpoint FAILS CLOSED — refuses every request — rather than silently
# becoming open; a real deployment must set this deliberately.
ADMIN_API_TOKEN = os.environ.get("ADMIN_API_TOKEN") or None

# --- Backend / CORS ------------------------------------------------------------
CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "http://localhost:3000").split(",")
BACKEND_HOST = os.environ.get("BACKEND_HOST", "0.0.0.0")
BACKEND_PORT = int(os.environ.get("BACKEND_PORT", 8000))
# The externally-reachable base URL for this backend — used only to build a
# full attachment link for Slack messages (agent/escalation.py), since
# Slack needs an absolute URL, not a relative path. Defaults to localhost
# because there's no real hosted deployment for this project today (see
# ANALYSIS.md's Phase D entry) — a genuine deployment must override this.
BACKEND_PUBLIC_URL = os.environ.get("BACKEND_PUBLIC_URL", f"http://localhost:{BACKEND_PORT}")

# --- Logging -------------------------------------------------------------
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")