# Analysis Log

Post-submission product development log. The interview/grading milestone (M1–M11, see `RUNBOOK.md`/`ARCHITECTURE.md`/`error_analysis.md` for that history) is complete; this file tracks the enhancement program that continues on top of it — one entry per phase, in the order built. Each entry: what was built, why, and any design decision made along the way.

## Phase A — Quick UI improvements (2026-07-17)

**What:** Four additions to `frontend/components/ChatWindow.tsx`:

1. Retry-on-failure — a failed `/chat` call (network error, backend unreachable) now renders a "Retry" button on that message instead of forcing the user to retype it. The original query is kept on the error message (`retryQuery`) and resubmitted in place via `runQuery()` on click, without re-adding a duplicate user bubble.
2. Colored route indication — the response bubble gets a 3px left border colored by `route` (`bubbleRoute_information` / `_action` / `_escalate`), reusing the same `--badge-*-fg` CSS variables the existing route badges already use, so the color mapping is consistent rather than a second one invented for the border.
3. Copy button — a small clipboard-icon button on each agent response, using `navigator.clipboard.writeText()`, with a transient checkmark on success.
4. Timestamps — every message (user and agent) now carries a `timestamp` (set at creation) rendered via `toLocaleTimeString()` under the bubble.

The fifth item asked for, an animated typing indicator, was already implemented (three pulsing dots, `typingBubble`/`typingDot`/`typing-bounce` in `ChatWindow.module.css`) — nothing to do there.

**Why:** Low-risk UX polish requested as the first phase of a larger enhancement program; no backend or routing changes required.

**Verification:** Ran the real dev stack (`scripts/run_backend.sh` + `scripts/run_frontend.sh`) and drove it with Playwright (Chromium) against the live app, not just code review:
- Typing indicator appears while a request is in flight.
- Simulated a network failure (aborted the first `/chat` call) → error bubble with a Retry button appeared; clicking Retry resent the same query and produced a normal assistant response, confirmed visually via screenshot.
- Route border color confirmed via computed style (`rgb(29, 78, 216)` = `--badge-info-fg`, matching the "Information" badge) on an information-routed response, and visually confirmed a green border on an action-routed response.
- Copy button confirmed to place the exact response text on the clipboard (read back via `navigator.clipboard.readText()`).
- Timestamps confirmed present under both user and assistant bubbles (rendered in the host locale — e.g. "10:39 م" under an Arabic-locale browser — which is `toLocaleTimeString()` behaving correctly, not a bug).

**Design decisions:** None requiring a call — this phase had no open design questions per the program brief.

**Incidental finding (not part of Phase A's scope, flagged and resolved before continuing):** local `main` was 16 commits behind `origin/main` (missing, among other things, real content for `ARCHITECTURE.md`/`RUNBOOK.md`/`DATASET_CARD.md` and a hybrid-retriever logging change) and had 1 unpushed local commit. Per user direction, pulled/rebased local `main` onto `origin/main` (no push — pushing to this repo's origin is out of scope going forward, see project conventions). Rebase was clean, no conflicts. Backend was restarted afterward so Phase B's retrieval investigation runs against current code.

## Phase B — Arabic language support (2026-07-17)

### Investigation: is cross-lingual retrieval actually a problem?

Before writing any of the feature, ran 6 realistic Arabic customer questions (Arabic translations of real support queries) through the exact production path, `HybridRetriever.retrieve()`, against the real Weaviate/Neo4j corpus, using the current embedding model (`bge-large-en-v1.5`), and compared against their English equivalents:

| Query topic | EN confidence / top score | AR confidence / top score | AR retrieved the right topic? |
|---|---|---|---|
| Shipping time | Medium / 0.711 | Medium / 0.465 | No — Delivery + Payment chunks, missed the specific delivery-time chunk |
| Return policy | Medium / 0.688 | Medium / 0.501 | No — Delivery + Payment, missed Refund entirely |
| Cancel order | High / 0.864 | Medium / 0.473 | No — Delivery + Account, missed Order/Cancel entirely |
| Refund for damaged item | Medium / 0.723 | Medium / 0.486 | No — Delivery + Payment, missed Refund entirely |
| Update account info | High / 0.778 | Medium / 0.488 | No — Delivery + Invoice, missed Account entirely |
| Payment methods | High / 0.764 | Medium / 0.483 | No — mostly Delivery, one Payment chunk landed 3rd |

Two compounding causes, both confirmed in code and in the live data:
1. `hybrid_retriever.py`'s BM25 tokenizer is `re.compile(r"[a-z0-9]+")` — matches nothing in Arabic script, so `bm25_max_score` was `0.0000` on every Arabic query. Retrieval degraded to dense-only.
2. Dense retrieval itself collapsed toward one generic chunk ("Available Delivery Options" appeared in the top-3 for 5 of 6 unrelated Arabic queries) — the Arabic embeddings weren't landing near their true topic in `bge-large-en-v1.5`'s English-only embedding space, they were landing in a generic region of it.

Critically, `MIN_ESCALATION_DENSE_FLOOR` (0.55) and `MIN_ESCALATION_BM25_FLOOR` (4.0) meant every one of the 6 untranslated Arabic queries — despite being perfectly valid, answerable questions — would have failed **both** hard floors in `agent.py`'s `_low_confidence_reason` gate and been clarified-then-escalated, never reaching a real answer. This isn't a quality-of-answer nuance; it's a hard routing failure for essentially all Arabic information requests.

**Decision (confirmed with evidence before building):** proceed with the brief's suggested fix — detect Arabic via Unicode range, translate the query to English via a Groq call purely for the retrieval step, retrieve using the translated query, generate the final answer in Arabic from the correctly-retrieved English chunks. The corpus itself is never translated or duplicated.

### Implementation

- **`agent/language.py`** (new): `detect_language(text)` — Unicode-range detection (Arabic script + supplement + presentation-forms blocks), zero-cost, no LLM call, same free-per-turn philosophy as `agent/smalltalk.py`'s pre-Groq detection.
- **`agent/translation.py`** (new): `translate_to_english(llm, text)` — used ONLY to bridge the retrieval step; never shown to the customer, never the source of the final answer's wording. **Design correction found during evidence-gathering:** the default 8B model (`GROQ_MODEL_DEFAULT`) was unreliable here — it sometimes echoed the Arabic text back unchanged, or prepended a "Translation:" label despite explicit instructions not to. Switched to `llm.upgrade_model` (the 70B model) for this call specifically, plus a one-retry loop and a defensive label-stripper. After the fix, all 6 documented queries translated cleanly.
- **`agent/agent.py`**: `handle_message()` now computes `language = detect_language(query)` once per turn and, if Arabic, builds `retrieval_query = translate_to_english(...)` — used for both the initial `retrieve()` call and the `broaden_via_graph()` retry. `language` is threaded through the existing call chain (`_route` → `_route_confident` → `_answer_information`/`_do_action`/`_ask_for_slot` → escalation methods) purely to select which language's copy to return — **no threshold, cap, or routing condition was touched**; the M8 escalation floors and M9 clarify-then-escalate structure are exactly as before, just now fed a correctly-retrieved input for Arabic turns too.
- **`agent/generator.py`**: `generate_answer()` takes a `language` param; the Arabic system prompt instructs the model to answer fluently in Arabic while treating the (English) policy excerpts as its only factual source — one generation call in the target language, not generate-then-translate.
- **`agent/smalltalk.py`**: added a curated Arabic phrase set (greeting/thanks/farewell/disengagement) and Arabic reply variants; `classify_smalltalk()` picks the reply language via `detect_language()` internally, so its call site in `agent.py` needed no changes.
- **`agent/tools.py`**: `cancel_order()`/`track_order()` take a `language` param with hardcoded Arabic message variants for every branch (status/shipping-method/ETA vocab included) — hardcoded rather than a live translation pass, since these messages carry dollar amounts and order IDs a translation call could mangle.
- **Escalation/clarify copy in `agent.py`** (`_CLARIFY_QUESTIONS`, `_CLARIFICATION_QUESTION`, `_CONTACT_QUESTION`, `_escalation_detail`, `_delivery_dispute_escalation_detail`): same hardcoded-variant treatment, same reasoning.
- **Second bug found via end-to-end verification, not the original investigation:** `agent/groundedness.py`'s `is_grounded()` embeds the *generated answer* with the same English-only embedder to compare against the English reference vectors. A fluent, correctly-grounded Arabic answer was failing this check and incorrectly escalating (found live testing the return-policy question — it escalated despite a 0.69 retrieval score). Fixed the same way as retrieval: `_answer_information()` now grounds against an English back-translation of the answer (via the same `translate_to_english()`) when `language == "ar"`, while the Arabic `answer` itself is what's still returned to the customer. The groundedness threshold and the hard one-retry cap are untouched.
- **Frontend (`frontend/lib/language.ts`, `ChatWindow.tsx`/`.module.css`):** `isArabicText()` mirrors the backend's Unicode ranges. Each message bubble independently detects its own text's direction (not a conversation-wide toggle) and applies `dir="rtl"`, right-aligned text, mirrored bubble-row position (own message and agent response swap which side of the screen they're on, same convention as other RTL chat UIs), and a mirrored speech-bubble tail corner.

### Verification

Re-ran the exact 6 documented Arabic queries against live retrieval, before vs. after:

| Query | BEFORE conf/dense/bm25 | AFTER conf/dense/bm25 | AFTER top chunk |
|---|---|---|---|
| Shipping time | Medium/0.465/0.0 | Medium/0.706/6.8 | DELIVERY — correct |
| Return policy | Medium/0.501/0.0 | Medium/0.688/7.2 | REFUND — correct |
| Cancel order | Medium/0.473/0.0 | **High**/0.852/8.7 | ORDER — correct |
| Refund for damaged item | Medium/0.486/0.0 | Medium/0.723/12.3 | REFUND — correct |
| Update account info | Medium/0.488/0.0 | **High**/0.780/7.8 | ACCOUNT — correct |
| Payment methods | Medium/0.483/0.0 | **High**/0.764/14.8 | PAYMENT — correct |

6/6 now retrieve the correct topic with a real (non-zero) BM25 score, up from 0/6.

Ran 5 real conversations end-to-end through the actual `/chat` endpoint, covering every route:
- **Information** ("ما هي سياسة الإرجاع الخاصة بكم؟" — return policy): fluent, grounded Arabic answer listing the actual refund conditions from the corpus (defect, 30-day cancellation window, unauthorized charges, event cancellation, etc.).
- **Action / track** ("أين طلبي ORD-1003؟"): correct Arabic status + ETA for a real mock order.
- **Action / cancel with fee** ("أريد إلغاء طلبي ORD-1003"): correct Arabic message with the correct 10% restocking fee dollar amount.
- **Escalation** (off-topic stock-price question, 2 turns): correctly asked one Arabic clarifying question first, then escalated in Arabic on the second still-unclear turn — the M9 clarify-then-escalate structure working unchanged, just in Arabic.
- **Smalltalk + information** (greeting → shipping question → thanks): all three turns replied fluently in Arabic.

Verified the frontend in a live browser (Playwright): sent an Arabic message, confirmed via computed style that both the user's and the agent's bubbles get `dir="rtl"`, `text-align: right`, and mirrored bubble-row position; then sent an English message in the same conversation and confirmed it reverted to standard LTR layout — RTL is genuinely per-message, not a sticky conversation-level toggle. Screenshots taken confirming both states visually.

Full backend test suite (133 tests) still passes — existing test fakes (`FakeTools`, `_patch_generate`) needed a `language`/optional-kwarg update to match the new signatures, but no test assertions changed.

## Phase C — Feedback & admin visibility (2026-07-17)

**What:**

1. **Thumbs up/down feedback** — a 👍/👎 control under every agent response bubble (`frontend/components/ChatWindow.tsx`'s `FeedbackButtons`). Clicking posts to a new `POST /feedback` with the session id, the preceding customer query (found by scanning backward through the transcript for the last user message), the response text, route, and intent. Storage: `agent/feedback.py`'s `FeedbackHandler` — same SQLite file and pattern as `agent/escalation.py` (a new `feedback` table in the same `.db`), per the brief's explicit instruction not to introduce a third storage mechanism. `GET /feedback` lists everything, newest first.
2. **Admin dashboard** (`frontend/app/admin/page.tsx` → `AdminDashboard.tsx`), reachable via a small "Admin" link on the main chat page:
   - `/metrics` (a Prometheus text-exposition endpoint, not JSON — `lib/api.ts`'s `fetchMetrics()` is a minimal 3-line-parser for the 3 gauges this app actually publishes, not a general Prometheus client) rendered as stat tiles: Groq calls today, total escalations, undelivered-to-Slack count, positive-feedback rate.
   - A Slack-delivery comparison bar (delivered vs. undelivered) and a feedback-by-route grouped bar chart (👍 vs 👎 per route), both hand-rolled (no charting dependency added — a handful of horizontal bars didn't justify one) following the dataviz skill's procedure: form picked by the data's job (stat tile for a single current-day number with no time series to plot; comparison bars for magnitude comparisons), color reused from this app's *existing* semantic tokens rather than a new palette (`--badge-action-fg` green = good/up/delivered, `--error-fg` red = bad/down/undelivered — status colors, since up/down and delivered/undelivered are state, not identity), rounded bar ends, direct value labels, a legend-free single-series stat tile.
   - `/escalations` as a real table (timestamp, query, reason, contact, Slack status badge), with the existing `slack_delivered=false` filter exposed as a checkbox toggle that re-fetches.
3. **Auth decision:** left unauthenticated. Flagging the tradeoff explicitly: this project has no login/session system anywhere else (the chat's `session_id` is an anonymous client-generated UUID, not an identity), so adding real auth just for this one page would be new surface area disproportionate to a local/demo-stage project, and the dashboard only reads endpoints (`/metrics`, `/escalations`, now `/feedback`) that were already unauthenticated GET routes before this phase — this page adds no new exposure. This is a real gap to close before ever deploying this project somewhere publicly reachable.

**Why:** Gives the team (and, later, the M9/M10 escalation-Slack-delivery gap) a way to see whether the agent's answers are actually landing well with customers, and turns the existing `/metrics`/`/escalations` JSON/Prometheus endpoints — previously only viewable as raw text — into something a human can actually read at a glance.

**Verification:** Ran the real stack end-to-end, not just component tests:
- `agent/feedback.py`: 4 new tests (`test/test_feedback.py`) covering record/list, thumbs-down not being dropped, and sharing one SQLite file with `EscalationHandler` without table collisions. Full suite: 137 passed (up from 133), 2 pre-existing skips.
- Backend smoke-tested directly via `curl`: `POST /feedback` and `GET /feedback` round-trip correctly.
- Browser (Playwright): clicked 👍 on a real chat response, confirmed the button's `aria-pressed` flips and — checked directly against the backend — that the exact preceding query, full response text, route, and intent landed in `GET /feedback`.
- Admin dashboard loaded against the real backend with real data (11 escalations, 5 feedback entries, 59 Groq calls that day): stat tiles show real numbers, the Slack-undelivered checkbox toggle correctly re-filters the table (confirmed going from 11 rows to the appropriate empty state), and the "Admin" link is reachable from the main chat page. Checked both light and dark `prefers-color-scheme` (this app already supports both) — confirmed via computed style, not just eyeballing a screenshot, that bar colors and widths render correctly in dark mode too (a pale-green/red on near-black initially looked like it might be washed out in a screenshot, but computed `background-color` and `width` were both correct — this app's own pre-existing dark-mode badge colors, not a new bug).

## Phase D, sub-project 1 — Voice input (2026-07-17)

Phase D's five sub-projects (chat history persistence, file attachments, voice input, live human handoff, usage analytics) are independent and scoped one at a time per the program brief — this is the first, chosen as the smallest/lowest-risk.

**Design (proposed and confirmed before building):** browser-native Web Speech API (`SpeechRecognition`/`webkitSpeechRecognition`), entirely client-side — no backend change. Feature-detected on mount; the mic button simply doesn't render on a browser with no support (Firefox has none natively) rather than showing a control that would fail. Click-to-talk, single-utterance (not continuous), live-fills the text input as interim results arrive. Requires a secure context (HTTPS or `localhost`) for the browser's mic-permission prompt.

**What:** `frontend/lib/speech.ts` (new) — `isSpeechRecognitionSupported()` and `createVoiceInput()`, with minimal local TypeScript types for the parts of this API this file actually touches (the DOM lib doesn't ship types for this non-standard, vendor-prefixed API). `ChatWindow.tsx`: a mic button next to the text input, rendered only when supported; clicking starts/stops recognition; `onresult` fills the input live, `onerror` shows a small inline message (mic denied / no speech / no microphone / network), `onend` resets the listening state. No backend files touched.

**Verification — what was actually automated (Playwright) vs. what needs a human:**
- Confirmed headless Chromium *does* expose `SpeechRecognition` (informational finding — not guaranteed across environments, but true here).
- Patched `SpeechRecognition.prototype.start`/`stop` (not the constructor, which is non-configurable on `window`) so the app's real `new Ctor()` still produces a real instance with real `onresult`/`onerror`/`onend` property assignment, but starting it never touches actual audio in this headless, mic-less box. This let interaction be tested exactly as production code runs it, not a hand-built mock of the whole API surface.
- Confirmed: mic button renders when supported; clicking calls `recognition.start()`; the button and input placeholder switch to a listening state; a simulated `onresult` event fills the input with the transcript; a simulated `onend` reverts the listening state; a simulated `onerror("not-allowed")` shows "Microphone access was denied." inline and resets the button — all via screenshots and direct DOM/attribute assertions, not just visual inspection.
- Confirmed: with `SpeechRecognition`/`webkitSpeechRecognition` deleted from `window` (simulating Firefox), the mic button does not render at all.
- **What this does NOT verify, and can't be automated:** whether real spoken audio is actually transcribed correctly by the browser's speech backend. See RUN.md/SETUP.md-adjacent manual test below — this needs a human with a real microphone.

**Manual verification steps (for the user, not automatable):**
1. Use Chrome or Edge (not Firefox — no support) on `http://localhost:3000`, with a working microphone.
2. Click the 🎙️ button next to the message input. The browser will prompt for microphone permission the first time — allow it.
3. Say something like "What's your return policy?" out loud.
4. Expect: the input placeholder shows "Listening…" while the mic button shows a pulsing red-tinted state; after you stop talking, the recognized text should appear in the input box; click Send (or it may auto-populate and you send manually — recognition does not auto-submit).
5. Try denying the microphone permission once (or test in a browser/profile where it's blocked) and confirm a small red "Microphone access was denied." message appears under the input instead of a silent failure.

**Result:** confirmed working by the user with real speech (2026-07-17) — voice input correctly transcribes spoken messages before the automated checks above were even the full picture.

## Phase D, sub-project 2 — File attachments (2026-07-17)

### Design (investigated and proposed before building)

Two explicit design questions from the brief, both resolved by investigating the actual current environment rather than assuming one:

1. **Storage: local disk, not a cloud bucket.** Investigated first: searched this entire repo (all docs, `docker-compose.yml`, `.env`/`.env.example`, and every common deployment config file — `render.yaml`, `fly.toml`, `vercel.json`, `Procfile`, Terraform, k8s manifests) for any real hosted deployment target. Found none — `Executive_Briefing.md` explicitly states "The full stack runs as a single Docker Compose deployment," and the only prior "cloud deployment" language (Phase B's `bge-small-en-v1.5` comment) is aspirational prose, not implemented anywhere (no `DEPLOYMENT_ENV` flag, no swap logic, `.env` hardcodes `bge-large-en-v1.5`). Building against a specific cloud provider now would be speculative infrastructure with nothing to configure it against. Followed the exact precedent already established by `ESCALATION_STORE_PATH`/`MOCK_ORDERS_PATH`: local disk, env-var-configurable path, kept behind one narrow module (`agent/attachments.py`) so a real bucket can be swapped in later — if this project ever gets a real hosted deployment — without touching every caller.
2. **LLM processing: forward/log to a human, never analyzed.** This app has no vision-capable model configured anywhere — both Groq models (`llama-3.1-8b-instant`, `llama-3.3-70b-versatile`) are text-only. A "does this look damaged" judgment call is exactly the kind of thing this system's whole escalate-on-uncertainty design (M8/M9) already defers to a human for. Adding real image understanding would mean a new model tier, new prompt design, and new reliability/cost unknowns — for a call this system is explicitly designed to hand off, not make itself. So: an attachment is stored, and its reference is forwarded to Slack only if — and only if — the turn it was attached to actually escalates. The text routing/retrieval/generation pipeline (`agent/agent.py`'s core decision logic, untouched) never sees it.

Proposed to the user with this reasoning before writing any code; confirmed as-is.

### What was built

- **`agent/attachments.py`** (new): `AttachmentStore` — `save()` validates content-type (jpeg/png/webp only) and size (5 MB default, both configurable via `ATTACHMENTS_MAX_BYTES`/`ATTACHMENTS_STORE_PATH`), writes to local disk with a UUID filename; `path_for()` resolves an id back to a real file for serving. Rejections raise `AttachmentRejected`, mapped to a 400, not a 500.
- **`app/main.py`**: `POST /attachments` (multipart upload → `{attachment_id, url}`), `GET /attachments/{id}` (serves the file back — needed so a Slack link actually resolves to something).
- **`app/schemas.py`**: `ChatRequest.attachment_id` (optional) and `EscalationPacket.attachment_id` (optional); new `AttachmentUploadResponse`.
- **`agent/agent.py`**: `attachment_id` threaded through `handle_message()` → `_route`/`_route_confident`/`_answer_information`/`_do_action`/`_resolve_pending_slot`/`_resolve_pending_clarification`/`_begin_delivery_dispute_escalation` → `_begin_escalation`/`_finalize_escalation`, purely as a passthrough value — no routing condition, threshold, or decision branch was touched. When the `awaiting_contact` round-trip is needed, `attachment_id` is stashed in `pending_context` so a LATER contact-providing reply still forwards the image from the ORIGINAL triggering turn, not whatever (if anything) that reply itself attached.
- **`agent/escalation.py`**: `finalize()` takes `attachment_id`; the SQLite schema gets an additive `attachment_id` column (same `ALTER TABLE`-if-missing pattern as M9's `attempted_summary`); the Slack Block Kit message gets an extra "*Attachment:* <link|View image>" section only when one is present, built from a new `BACKEND_PUBLIC_URL` config value (defaults to `localhost` — a real hosted deployment would need to override it for Slack's link to actually resolve for whoever's reading it, a limitation explicitly flagged, not silently assumed away).
- **Frontend**: a 📎 button next to the input; picking a file uploads immediately (`lib/api.ts`'s `uploadAttachment()`) and shows a removable thumbnail chip; sending includes the `attachment_id` and clears the chip; the sent message's own bubble shows a small thumbnail of what was attached (a local `blob:` object URL — session-only, same as the rest of this app's chat history, nothing persists across a reload).

### Verification

- New tests: `test/test_attachments.py` (5 tests — save/path_for/rejections) and 4 new tests in `test/test_escalation.py` (attachment_id round-trips through `finalize()`/`list_escalations()`, and the Slack message includes/omits the "*Attachment:*" block correctly). Full suite: **146 passed** (up from 137), 2 pre-existing skips.
- Backend smoke-tested directly: uploaded a real PNG via `curl`, downloaded it back byte-identical via `GET /attachments/{id}`, confirmed a disallowed content-type (`text/plain`) is rejected with a 400.
- Full real flow via `curl`: uploaded an image, then sent a `/chat` message that triggers `order_not_found` (cancel a nonexistent order, with an email in the message so contact resolves immediately) — confirmed via `GET /escalations` that the resulting escalation record's `attachment_id` matches the uploaded file.
- Browser (Playwright): clicked the 📎 button, picked a real file via the native file chooser, confirmed the thumbnail chip appears; sent the message and confirmed the chip clears and the sent message's own bubble shows the thumbnail; confirmed (via screenshot) the whole flow end-to-end — attached image, escalation banner, correct route coloring, all together in one real conversation.

## Phase D, sub-project 3 — Chat history persistence (2026-07-18)

### Design (proposed and confirmed before building)

Two explicit questions from the brief:

1. **Identity — unverified email, opt-in, confirmed with the user.** True cross-device history is structurally impossible with today's mechanism alone (`session_id` is a client-generated UUID in `localStorage` — device/browser-bound by nature). Checked before proposing: this project has **no email-sending capability anywhere** (no smtplib, no SendGrid/Postmark/Resend/etc. in `requirements.txt` or the codebase), so a verified magic-code flow would mean adding a brand-new external dependency first. Presented both options to the user — unverified email (zero new infrastructure, ships immediately, but anyone who knows/guesses the email could look up that customer's history) vs. verified magic-code (correct, but needs a new email provider) — and unverified email was chosen, explicitly accepting the tradeoff. This mirrors the risk posture already accepted elsewhere in this project (Phase C's admin dashboard is unauthenticated by the same reasoning; `Executive_Briefing.md`'s own gap list already flags "no ownership check" on order actions).
2. **Storage — same SQLite file as escalations/feedback**, not a new store: no cloud deployment target exists for this project (re-confirmed, same finding as Phase D-2's attachments investigation), so a managed database would be speculative infrastructure. A new `messages` table plus a small `session_identity` mapping table, both in `escalations.db`.

> **⚠️ KNOWN LIMITATION — not fixed, intentionally deferred, must be revisited before any real deployment:**
> `POST /conversations/identify` accepts an email with **zero verification** — no code, no confirmation link, nothing. Anyone who knows or correctly guesses a customer's email address can type it into the History panel from any device and read that customer's entire saved conversation history (order numbers, complaints, whatever they've discussed). This is a genuine account-takeover-style privacy gap, not a theoretical one.
>
> This was a conscious, confirmed-with-the-user tradeoff (see point 1 above) made because this project has no email-sending capability today and adding one was out of scope for "simplest viable." **It is only acceptable at this project's current stage** (local development, no real customer data, consistent with the no-auth posture already accepted elsewhere — the Phase C admin dashboard, the pre-existing "no ownership check on order actions" gap). **If this project is ever deployed somewhere reachable by real customers, this must be replaced with a verified flow (magic-code/link email, or real login) before that happens — not an incremental nice-to-have.**

### What was built

- **`agent/conversation_history.py`** (new): `ConversationHistoryStore` — `record_message()` persists every `/chat` turn (both the customer's message and the agent's reply) unconditionally, keyed by `session_id`; `identify_session()` links a `session_id` to an email (unverified) and backfills every message already recorded under it; every message recorded on that session_id afterward is auto-tagged with the linked email by `record_message()`'s own lookup — the caller never re-passes the email. `list_by_session()` / `list_by_email()` read it back.
- **`app/main.py`**: the `/chat` route records both halves of the turn *after* calling `Agent.handle_message()` — this is a pure side-channel transcript log, not a routing input, so `agent/agent.py`'s core decision logic was not touched at all (no threading needed here, unlike Phase D-2's `attachment_id`, since this only needs the request/response pair the route handler already has). New `POST /conversations/identify` and `GET /conversations` (by `session_id` and/or `email`).
- **`app/schemas.py`**: `MessageEntry`, `IdentifySessionRequest`.
- **Frontend**:
  - A rehydration effect in `ChatWindow.tsx` fetches `GET /conversations?session_id=...` on mount — this alone fixes an existing baseline gap (refreshing the page lost the entire visible transcript even though the same `session_id` already persisted in `localStorage`), with **no identity concept needed** for this part.
  - `HistoryPanel.tsx` (new component): a "History" button in the header opens a panel — if no email is linked yet, an inline "save my history" email field (no verification step, by design); once linked (stored in `localStorage` so it persists on that browser), it lists every past conversation ever linked to that email, grouped by session, with a snippet + date, expandable to the full read-only transcript.
  - Rehydrated/historical assistant messages get a lighter badge (just the route label, since only `route`/`intent` are persisted, not the live confidence scores) and skip feedback buttons — the copy button still works, since it only needs the text.

### Verification

- New tests: `test/test_conversation_history.py` (6 tests — record/list round-trips, `identify_session()` backfilling existing rows AND auto-tagging future ones, cross-session lookup by email, and sharing the SQLite file with `EscalationHandler`/`FeedbackHandler` without table collisions). Full suite: **152 passed** (up from 146), 2 pre-existing skips.
- Browser (Playwright), the full real scenario:
  1. Sent a real message on "Device A" (a fresh browser context), confirmed 2 messages visible.
  2. **Refreshed the page** — confirmed the empty-state quick-replies were gone and the original message was still visible: the baseline "lost on refresh" bug is fixed, with zero identity involved.
  3. Opened the History panel, linked the session to a fresh test email — confirmed the panel showed that email as linked.
  4. Opened a **brand-new, separate browser context** ("Device B" — a genuinely different `session_id`, empty `localStorage`, its own empty live transcript showing the normal empty-state quick-replies) and, *without* ever having talked to the agent on this device, opened its History panel and entered the SAME test email — confirmed Device A's past conversation appeared and was expandable into its full "You:"/"Agent:" transcript, screenshotted at each step.

## Phase D, sub-project 4 — Usage analytics (2026-07-18)

### Design (proposed and confirmed before building)

The brief's question: does this extend `/metrics`, or need a proper analytics store? **Neither, exactly.**

- **Not `/metrics`**: that's a Prometheus text-exposition endpoint — single current-state gauges (today's Groq calls, total escalations), meant for a real Prometheus/Grafana scraper this project doesn't have. "Most common questions" (a ranked breakdown) and "peak times" (an hourly histogram) don't fit that shape, and forcing them in would mean rewriting the admin dashboard's line-based `fetchMetrics()` parser to handle labeled series it was never built for.
- **Not a new store either**: no cloud deployment target exists (same finding as every other Phase D storage decision) — but more to the point, **Phase D-3's `messages` table already has exactly the raw data this needs** (`intent`, `route`, `timestamp` on every turn). Usage analytics here is pure aggregation over data already being collected for chat-history persistence, not a new collection concern.
- Landed on: a **new `GET /analytics` endpoint** (same pattern as `/escalations`/`/feedback` — a dedicated JSON route, not Prometheus text), backed by new read-only aggregation methods on the existing `ConversationHistoryStore`.

### What was built

- **`agent/conversation_history.py`**: three new read-only methods, all pure `SELECT`/`GROUP BY` over `messages`, no new tables:
  - `top_intents(limit)` — most common questions, ranked by frequency. Deliberately **not** filtered to "real" questions only (smalltalk/unknown included) — hiding categories would be an editorial call this method has no business making; the raw counts are the honest answer.
  - `hourly_volume()` — peak times as an **hour-of-day (0–23, UTC) histogram**, not a calendar time series. Chose this deliberately: at this project's actual data volume (dozens of messages), a real time series would be too sparse to show anything; hour-of-day is also the conventional "when do people contact us" view for this kind of report. Always returns all 24 buckets, zero-filled.
  - `dropoff_summary()` — counts single-user-turn sessions (asked once, never followed up) vs. multi-turn ones. **Explicitly labeled as a proxy, not a funnel**, both in the docstring and in the admin UI's copy: this can't distinguish "got a complete answer and left satisfied" from "gave up confused" — that's the honest limit of what this data alone can tell.
- **`app/schemas.py`**: `IntentCount`, `HourlyCount`, `DropoffSummary`, `AnalyticsSummary`.
- **`app/main.py`**: `GET /analytics` (optional `top_intents_limit` query param).
- **Frontend**: extended the *existing* `/admin` dashboard (Phase C) with three new sections, reusing its established `HorizontalBarChart` component plus one new `HourlyBarChart` (24 thin vertical bars, hour labels every 6 hours, a native `title` tooltip per bar) — no new page, no new dependency. A single neutral accent color (`--accent`) is used for these single-series ranked/bucketed charts, since — unlike the existing Slack-delivery/feedback charts — there's no good/bad or route-identity dimension to encode with color here.

### Verification

- New tests: 5 in `test/test_conversation_history.py` (`top_intents` ranking/limit/unknown-labeling, `hourly_volume`'s 24-zero-filled-buckets guarantee, `dropoff_summary`'s single-vs-multi-turn split). Full suite: **157 passed** (up from 152), 2 pre-existing skips.
- Backend smoke-tested directly: `curl`'d `/analytics` against the real (tiny) dataset, confirmed the shape and values matched what was actually in the database.
- Sent 5 additional real chat messages across different intents via `curl` to give the dashboard something visually meaningful to render, then verified in a live browser (Playwright + screenshot): all three new sections render with real data — 5 distinct ranked intents, an hourly histogram showing bars only at the hours messages were actually sent, and a 6-single-turn/0-multi-turn engagement split matching the test traffic sent. Fixed one real cosmetic bug found during this pass — a long intent label (`check_payment_methods`) was breaking mid-word inside the shared bar-chart label column; widened the column and switched to whole-word wrapping.
- **Honest caveat, stated directly in the admin UI itself** (not just here): this project's current data is test/demo traffic, not real usage — the "peak times" section says so explicitly rather than implying the pattern shown means anything yet.

## Phase D, sub-project 5 — Live human handoff (2026-07-18)

The last of Phase D's five sub-projects, and — per the program brief — "the most architecturally significant." Investigated the current escalation/Slack architecture in full before proposing anything.

### Investigation finding that shaped the whole design

`agent/escalation.py`'s Slack integration is a plain incoming webhook (`requests.post` to `SLACK_WEBHOOK_URL`, fire-and-forget) — confirmed by a repo-wide search: no `slack_bolt`, `slack_sdk`, `SLACK_BOT_TOKEN`, Events API, or Socket Mode listener exists anywhere. **It is genuinely one-way.** A human replying inside the Slack thread today goes nowhere — there is no code path that could ever see it. Any "live handoff" design had to start from that fact, not from an assumption that Slack could just be extended.

### Design (proposed and confirmed before building)

Two explicit choices, both put to the user rather than decided silently:

1. **Workflow: admin-dashboard-driven, not real two-way Slack.** A genuine two-way integration (a human replying directly in the Slack thread) would require: creating a new Slack App with a bot token, Socket Mode (the only option without a public HTTPS endpoint, since no cloud deployment target exists — see every prior Phase D storage decision), a new `slack-bolt` dependency, and — the real cost — **a second long-running listener process**, a genuine change in this project's deployment shape, not an incremental addition. Chose instead to keep Slack exactly as it is (unchanged, one-way notification) and close the loop through the *already-built* Phase C admin dashboard: a human sees the Slack alert (which already prints the escalation ID prominently in its header), opens `/admin`, and acts there. Slack = notification, admin dashboard = the actual response loop — a deliberate, common real-world split, not a workaround.
2. **Auth: a minimal shared-secret token, `ADMIN_API_TOKEN`, gating only the new write endpoint.** Every other admin-dashboard route (Phase C, Phase D-4) stays exactly as unauthenticated as before — this one is different in kind, not degree: it lets someone post text that appears directly inside a customer's live conversation, a real step up from a read-only exposure. Flagged this distinction explicitly to the user rather than silently extending Phase C's "leave it open" precedent to a write action, and they chose the gate. **Fails closed**: an unconfigured token refuses every request, rather than silently becoming open — confirmed by testing (see below).

### What was built

- **`agent/escalation.py`**: 3 new columns (`human_status`: waiting/picked_up/responded, `human_reply`, `responded_at`), same additive `ALTER TABLE` pattern as every prior column addition. `respond(id, status, reply)` updates them (only sets `responded_at` when status is "responded"; a `COALESCE` keeps an existing reply if a later call doesn't supply a new one). `get_status(id)` — the customer-facing poll — deliberately returns a minimal view (`EscalationStatus`: no query/contact/retrieved_context), not the full packet, so an anonymous poller who only knows their own escalation ID can't pull anyone else's contact info.
- **`app/main.py`**: `POST /escalations/{id}/respond` (gated — `X-Admin-Token` checked with `hmac.compare_digest` against `ADMIN_API_TOKEN`; missing/wrong/unconfigured all fail identically with a 403) and the unauthenticated `GET /escalations/{id}/status`. The human's reply is *also* written into Phase D-3's `messages` table (tagged `role="human_agent"`) — not a poll-only, here-and-gone artifact; it survives a page refresh and shows up in chat history the same way any other message does.
- **`MessageEntry.role`** extended to include `"human_agent"`, both in the schema and the frontend's `ChatMessage` type — a customer should always be able to tell whether they're talking to the bot or a person, so it gets distinct styling (a blue-accented bubble, explicitly labeled "👤 A human agent replied"), not the bot's route-colored one.
- **Frontend polling** (`ChatWindow.tsx`): once a turn escalates, poll `GET /escalations/{id}/status` every 10s (capped at ~180 attempts, ~30 minutes) until `responded`; a "picked_up" status shows an inline green indicator on the original escalation bubble; a "responded" status stops polling and appends the reply as its own distinctly-styled message. Cleaned up on unmount.
- **Admin dashboard**: an admin-token input (stored in `localStorage`, entered once per browser — not a login, a shared secret per the confirmed design), and a new "Human status"/"Respond" column pair on the Escalations table — "Mark picked up" plus a reply box + Send, calling the gated endpoint.

### A known, stated limitation

Polling only runs for the lifetime of the browser tab. If the customer closes the tab (or refreshes) while an escalation is still "waiting", that specific live "waiting…" indicator does not resume — but nothing is actually lost: once a human does respond, it's durably in the `messages` table (Phase D-3), so revisiting the same session later (or rehydrating on a fresh load) will show the reply in history regardless. Chose not to build resume-polling-after-reload (would need persisting pending escalation IDs client-side too) for this pass — a reasonable, explicitly-flagged scope cut, not a silent gap.

### Verification

- New tests: 7 in `test/test_escalation.py` (`respond()`'s status/reply/responded_at behavior, picked_up not setting responded_at, unknown-ID handling, reply-preservation across calls, `get_status()`'s minimal shape). Full suite: **164 passed** (up from 157), 2 pre-existing skips.
- Backend smoke-tested directly via `curl`, in this order: (1) confirmed **fail-closed** — with no `ADMIN_API_TOKEN` configured, `/respond` returns 403 regardless of what header is sent; (2) spun up a *separate, temporary* backend instance on another port with a test token (main instance on :8000 untouched throughout) and confirmed the full success path — wrong token still 403, correct token 200, status transitions correctly through picked_up → responded, and the reply lands in `GET /conversations` tagged `human_agent`.
- **Full real browser end-to-end** (Playwright, two simultaneous browser tabs — one as the customer, one as an admin): customer escalates a cancel-order request → admin dashboard shows the row → admin clicks "Mark picked up" → **customer's own tab, polling independently, shows the green "An agent has picked up your case" indicator within 15 seconds, with no manual refresh** → admin types and sends a real reply → customer's tab shows it appear as a distinctly-labeled human-agent message within 15 seconds. Screenshotted at every step. This required temporarily setting a real `ADMIN_API_TOKEN` — done via a throwaway addition to `.env` for the verification window only, removed immediately after, backend restarted, and fail-closed behavior re-confirmed as restored — the user's real environment was not left in a different state than before this verification.

---

**Phase D is now complete — all five sub-projects built, verified, and documented**: voice input, file attachments, chat history persistence, usage analytics, and live human handoff. Combined with Phases A–C, this closes out the full post-submission enhancement program.
