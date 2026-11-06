# Agentic Customer Support Assistant
 
> AI.SPIRE Capstone Project 
 
---
 
## Problem Statement
 
Customer support conversations mix two fundamentally different needs: a
question that just needs an accurate explanation, and a request that
needs a real action taken. Most simple chatbots treat both the same way
— and worse, most don't know when to admit they can't help and hand off
to a human instead of guessing.
 
This project builds a customer support agent that:
 
- **Understands intent.** Tells the difference between an information
  request and an action request, and routes each correctly.
- **Answers with evidence.** Information questions are answered using
  retrieval-grounded generation over a real knowledge base (Weaviate
  dense + keyword search, fused with a Neo4j concept graph) — with a
  self-check that verifies the generated answer is actually supported by
  what was retrieved before it's ever sent back to the customer.
- **Acts, not just describes.** Action requests (cancel or track an
  order) call real functions against a mock order store, rather than
  producing a plausible-sounding description of what it would do.
- **Knows its limits.** Anything it isn't confident about is escalated
  to a human, logged permanently, and posted live to a Slack channel —
  instead of fabricating an answer or guessing at an action.
- **Speaks Arabic too.** A customer writing in Arabic gets a fluent
  Arabic reply, grounded in the same English knowledge base — the query
  is translated for retrieval only, never the corpus itself. See
  ANALYSIS.md's Phase B entry for the retrieval-quality investigation
  behind this.
- **Takes feedback and reports on itself.** A 👍/👎 under every answer
  feeds a small admin dashboard (`/admin` in the frontend) showing Groq
  usage, escalation/Slack-delivery status, feedback trends, and usage
  analytics (most common questions, peak hours, session engagement) —
  see ANALYSIS.md's Phase C/D entries, including the (currently
  unauthenticated) access tradeoff.
- **Supports voice input.** A mic button (Chrome/Edge — the browser's
  native Web Speech API, no server round-trip) lets a customer speak
  their message instead of typing it. See ANALYSIS.md's Phase D entry
  for browser-support caveats and manual test steps.
- **Accepts image attachments.** A customer can attach a photo (e.g. of
  a damaged product); it's stored on local disk and forwarded as a
  Slack link only if the conversation escalates — never analyzed by
  the LLM (this project has no vision-capable model configured). See
  ANALYSIS.md's Phase D entry for the storage/processing tradeoffs.
- **Remembers your conversation.** Refreshing the page no longer loses
  the transcript, and a customer can optionally save their history to
  view past conversations from another device.
  ⚠️ **Known limitation:** that cross-device link is an **unverified
  email** — no confirmation code, no login. Anyone who knows or guesses
  a customer's email can read their saved history from any device. This
  is an accepted tradeoff for this project's current local-development
  stage only (see ANALYSIS.md's Phase D entry) — it must be replaced
  with a verified flow before any real deployment.
- **Closes the loop after escalation.** Slack still gets a one-way
  notification (unchanged), but a human can now mark a case picked up
  or send a reply from the admin dashboard, and the customer sees it
  live — no more silent "a team member has been notified" with no
  follow-up. The reply-posting endpoint requires a shared `ADMIN_API_TOKEN`
  (unset by default, so it's off until deliberately configured); see
  ANALYSIS.md's Phase D entry for why real two-way Slack wasn't built
  instead.
Full technical design: **[ARCHITECTURE.md](ARCHITECTURE.md)** — the
single source of truth for this project. This README summarizes; it
doesn't duplicate that document.
 
---
 
## Architecture Summary
 
```
data/          raw dataset (never edited) + finalized corpus + mock order store
ingestion/     chunking, embedding, Weaviate/Neo4j loading (python -m ingestion.ingest)
retrieval/     hybrid (BM25 + dense) retrieval, graph-based query broadening
agent/         intent classification, generation, groundedness, routing, escalation
app/           FastAPI backend (/chat /health /metrics /escalations)
frontend/      Next.js chat UI
eval/          held-out set, plain-RAG baseline, evaluation harness
scripts/       fetch_data.py (dataset download), build_corpus.py (historical reference only)
```
 
At a glance: an incoming message is classified by intent and slot-extracted
in one call to Groq's free-tier LLM API. Information questions flow through
hybrid retrieval → generation → a local groundedness check, retried once
via graph-broadened retrieval before ever escalating. Action requests call
real functions against the mock order store. Anything low-confidence,
ungrounded, or unresolvable triggers escalation: a clarifying question is
asked first, contact info is captured, and the escalation is logged and
posted to Slack.
 
Full annotated file tree, data flow, and every routing decision are
documented in **[ARCHITECTURE.md](ARCHITECTURE.md)**.
 
---
 
## How to Run Locally
 
A complete, beginner-friendly walkthrough — with Windows, macOS, and
Linux instructions, and expected output at every step — lives in
**[SETUP.md](SETUP.md)**. It's designed to take a new machine from zero
to a fully running chat interface in under 30 minutes.
 
Quickest path, if you're already comfortable with the stack:
 
```bash
cp .env.example .env            # fill in GROQ_API_KEY, NEO4J_PASSWORD (SLACK_WEBHOOK_URL optional)
docker compose up -d weaviate neo4j redis
 
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
 
python -m ingestion.ingest       # populates Weaviate + Neo4j from data/corpus/
uvicorn app.main:app --host 0.0.0.0 --port 8000 &
 
cd frontend
cp .env.local.example .env.local
npm install && npm run dev
```
 
Then visit `http://localhost:3000`.
 
See **[SETUP.md](SETUP.md)** for the full step-by-step guide, and
**[RUN.md](RUN.md)** for advanced usage: running the backend alone,
Docker-only startup, ingestion internals, and the test suite.
 
---

 
## License
 
This project's own code is licensed under the **MIT License** — see
[`LICENSE`](LICENSE) for the full text.
 
The raw training data (`data/bitext_customer_support.csv`) is a
separate work, licensed by its creator under **CDLA-Sharing-1.0**, not
MIT — see [`NOTICE.md`](NOTICE.md) for the required attribution and
license link.
 
