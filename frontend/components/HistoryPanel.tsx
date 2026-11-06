"use client";

import { useEffect, useState } from "react";
import { ChatApiError, MessageEntry, fetchConversationHistory, identifySession } from "@/lib/api";
import styles from "./HistoryPanel.module.css";

const HISTORY_EMAIL_KEY = "meridian_support_history_email";

export function getSavedHistoryEmail(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(HISTORY_EMAIL_KEY);
}

interface ConversationSummary {
  sessionId: string;
  messages: MessageEntry[];
  lastAt: string;
}

function groupBySession(messages: MessageEntry[]): ConversationSummary[] {
  const bySession = new Map<string, MessageEntry[]>();
  for (const m of messages) {
    const list = bySession.get(m.session_id) ?? [];
    list.push(m);
    bySession.set(m.session_id, list);
  }
  const summaries: ConversationSummary[] = [];
  for (const [sessionId, msgs] of bySession) {
    const sorted = [...msgs].sort((a, b) => a.timestamp.localeCompare(b.timestamp));
    summaries.push({ sessionId, messages: sorted, lastAt: sorted[sorted.length - 1].timestamp });
  }
  return summaries.sort((a, b) => b.lastAt.localeCompare(a.lastAt));
}

// Phase D (chat history, see ANALYSIS.md's Phase D entry): unverified email
// only — no confirmation code. This project has no email-sending capability
// anywhere; the tradeoff (anyone who knows/guesses the email could look up
// that customer's history) was confirmed with the user rather than assumed.
export default function HistoryPanel({ sessionId }: { sessionId: string }) {
  const [open, setOpen] = useState(false);
  const [email, setEmail] = useState<string | null>(null);
  const [emailInput, setEmailInput] = useState("");
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [conversations, setConversations] = useState<ConversationSummary[] | null>(null);
  const [openSessionId, setOpenSessionId] = useState<string | null>(null);

  useEffect(() => {
    // Client-only: localStorage isn't available during server rendering.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setEmail(getSavedHistoryEmail());
  }, []);

  async function loadHistory(forEmail: string) {
    setLoading(true);
    setError(null);
    try {
      const messages = await fetchConversationHistory({ email: forEmail });
      setConversations(groupBySession(messages));
    } catch (err) {
      setError(err instanceof ChatApiError ? err.message : "Could not load your history.");
    } finally {
      setLoading(false);
    }
  }

  async function handleSave() {
    const trimmed = emailInput.trim();
    if (!trimmed) return;
    setSaving(true);
    setError(null);
    try {
      await identifySession(sessionId, trimmed);
      window.localStorage.setItem(HISTORY_EMAIL_KEY, trimmed);
      setEmail(trimmed);
      await loadHistory(trimmed);
    } catch (err) {
      setError(err instanceof ChatApiError ? err.message : "Could not save your history.");
    } finally {
      setSaving(false);
    }
  }

  function handleTogglePanel() {
    const next = !open;
    setOpen(next);
    if (next && email && conversations === null) {
      loadHistory(email);
    }
  }

  return (
    <div className={styles.wrapper}>
      <button type="button" className={styles.toggleButton} onClick={handleTogglePanel}>
        History
      </button>

      {open && (
        <div className={styles.panel}>
          {!email && (
            <div className={styles.identifyForm}>
              <p className={styles.hint}>
                Save your conversation history to view it from another device — just enter your email, no
                verification needed.
              </p>
              <div className={styles.identifyRow}>
                <input
                  type="email"
                  className={styles.emailInput}
                  placeholder="you@example.com"
                  value={emailInput}
                  onChange={(e) => setEmailInput(e.target.value)}
                  disabled={saving}
                />
                <button type="button" className={styles.saveButton} onClick={handleSave} disabled={saving || !emailInput.trim()}>
                  {saving ? "Saving…" : "Save"}
                </button>
              </div>
            </div>
          )}

          {email && (
            <div className={styles.emailKnown}>
              Showing history for <strong>{email}</strong>
            </div>
          )}

          {error && <p className={styles.error}>{error}</p>}
          {loading && <p className={styles.hint}>Loading…</p>}

          {conversations && conversations.length === 0 && !loading && (
            <p className={styles.hint}>No past conversations yet.</p>
          )}

          {conversations && conversations.length > 0 && (
            <ul className={styles.conversationList}>
              {conversations.map((conv) => (
                <li key={conv.sessionId} className={styles.conversationItem}>
                  <button
                    type="button"
                    className={styles.conversationSummary}
                    onClick={() => setOpenSessionId(openSessionId === conv.sessionId ? null : conv.sessionId)}
                  >
                    <span className={styles.conversationSnippet}>{conv.messages[0].text.slice(0, 60)}</span>
                    <span className={styles.conversationDate}>{new Date(conv.lastAt).toLocaleDateString()}</span>
                  </button>
                  {openSessionId === conv.sessionId && (
                    <div className={styles.conversationMessages}>
                      {conv.messages.map((m) => (
                        <div key={m.message_id} className={styles.historyMessage}>
                          <span className={styles.historyMessageRole}>{m.role === "user" ? "You" : "Agent"}:</span>{" "}
                          {m.text}
                        </div>
                      ))}
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
