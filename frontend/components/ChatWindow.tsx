"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import {
  AgentResponse,
  ChatApiError,
  EscalationStatus,
  MessageEntry,
  fetchConversationHistory,
  fetchEscalationStatus,
  sendMessage,
  submitFeedback,
  uploadAttachment,
} from "@/lib/api";
import HistoryPanel from "@/components/HistoryPanel";
import { isArabicText } from "@/lib/language";
import { VoiceInputController, createVoiceInput } from "@/lib/speech";
import styles from "./ChatWindow.module.css";

function historyEntryToChatMessage(entry: MessageEntry): ChatMessage {
  return {
    id: entry.message_id,
    role: entry.role,
    text: entry.text,
    timestamp: new Date(entry.timestamp).getTime(),
    historicalRoute: entry.role === "assistant" ? entry.route ?? undefined : undefined,
  };
}

interface ChatMessage {
  id: string;
  // Phase D (live human handoff): "human_agent" is a reply logged via a
  // human on the admin dashboard — visually distinguished from the bot's
  // own "assistant" messages, since a customer should always be able to
  // tell whether they're talking to the AI or a person.
  role: "user" | "assistant" | "error" | "human_agent";
  text: string;
  response?: AgentResponse;
  timestamp: number;
  // Only set on "error" messages — the original query, so Retry can
  // resend it without making the user retype it.
  retryQuery?: string;
  // Phase D: set on a "user" message that had an image attached — a local
  // object URL, just for showing a thumbnail in this transcript; nothing
  // about it survives a page reload, same as the rest of this session-only
  // chat history.
  attachmentPreviewUrl?: string;
  // Phase D (chat history): set on an assistant message rehydrated from
  // GET /conversations instead of a live /chat response — only `route` is
  // persisted server-side (not intent_confidence/retrieval_confidence), so
  // a rehydrated message gets a lighter-weight badge (just the route
  // label) instead of the full live badge row, and no feedback buttons.
  historicalRoute?: AgentResponse["route"];
}

function formatTimestamp(ms: number): string {
  return new Date(ms).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

const ROUTE_LABEL: Record<AgentResponse["route"], string> = {
  information: "Information",
  action: "Action taken",
  escalate: "Escalated to a human",
};

function formatPct(value: number): string {
  return `${Math.round(value * 100)}%`;
}

function newId(): string {
  return crypto.randomUUID();
}

function findPrecedingUserQuery(messages: ChatMessage[], index: number): string {
  for (let i = index - 1; i >= 0; i--) {
    if (messages[i].role === "user") return messages[i].text;
  }
  return "";
}

interface QuickReply {
  label: string;
  query: string;
  variant?: "escalate";
}

const QUICK_REPLIES: QuickReply[] = [
  { label: "Where's my order?", query: "Where's my order?" },
  { label: "How do I cancel an order?", query: "How do I cancel an order?" },
  { label: "What's your return policy?", query: "What's your return policy?" },
  {
    label: "Ask something off-topic (demo)",
    query: "What's your company's stock ticker symbol and current share price?",
    variant: "escalate",
  },
];

export default function ChatWindow({ sessionId }: { sessionId: string }) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [inputValue, setInputValue] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [isListening, setIsListening] = useState(false);
  const [voiceError, setVoiceError] = useState<string | null>(null);
  const [voiceSupported, setVoiceSupported] = useState(false);
  const [pendingAttachment, setPendingAttachment] = useState<{ id: string; previewUrl: string } | null>(null);
  const [attachmentUploading, setAttachmentUploading] = useState(false);
  const [attachmentError, setAttachmentError] = useState<string | null>(null);
  // Phase D (live human handoff): keyed by escalation_id -> latest known
  // status, so MessageBubble can show a live "picked up" indicator on the
  // original escalation bubble without needing its own poll.
  const [escalationStatuses, setEscalationStatuses] = useState<Record<string, EscalationStatus>>({});
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const voiceControllerRef = useRef<VoiceInputController | null>(null);
  const pollTimersRef = useRef<Record<string, ReturnType<typeof setInterval>>>({});

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isLoading]);

  useEffect(() => {
    // Phase D (chat history): rehydrates THIS session's transcript on
    // mount — this is what makes "refresh the page" not lose the visible
    // conversation, even for a customer who never provides an email. No
    // identity concept needed for this part; it's keyed by the same
    // session_id that already persists in localStorage today.
    let cancelled = false;
    fetchConversationHistory({ sessionId })
      .then((entries) => {
        if (cancelled || entries.length === 0) return;
        setMessages(entries.map(historyEntryToChatMessage));
      })
      .catch(() => {
        // Rehydration is a nice-to-have, not a customer-facing failure —
        // silently fall back to an empty transcript, same as before this
        // feature existed.
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  useEffect(() => {
    // Cleanup: stop every in-flight poll when the component unmounts (tab
    // closed / navigated away) — a poll that's still "waiting" when this
    // happens is simply not resumed on a later visit; the eventual human
    // reply is still durably in chat history by then (Phase D-3's
    // messages table), so nothing is lost, just the live "waiting…"
    // indicator itself. See ANALYSIS.md's Phase D entry for this tradeoff.
    // `timers` aliases the same mutable object `startPolling` writes into
    // later — not a snapshot — so this still sees every poll started
    // after mount, silencing the lint without changing behavior.
    const timers = pollTimersRef.current;
    return () => {
      Object.values(timers).forEach(clearInterval);
    };
  }, []);

  function startPolling(escalationId: string) {
    if (pollTimersRef.current[escalationId]) return; // already polling this one
    let attempts = 0;
    const maxAttempts = 180; // ~30 minutes at a 10s interval — a generous but bounded wait
    const timer = setInterval(async () => {
      attempts += 1;
      try {
        const status = await fetchEscalationStatus(escalationId);
        setEscalationStatuses((prev) => ({ ...prev, [escalationId]: status }));
        if (status.human_status === "responded") {
          setMessages((prev) => [
            ...prev,
            {
              id: newId(),
              role: "human_agent",
              text: status.human_reply ?? "",
              timestamp: status.responded_at ? new Date(status.responded_at).getTime() : Date.now(),
            },
          ]);
          clearInterval(timer);
          delete pollTimersRef.current[escalationId];
        }
      } catch {
        // A transient network hiccup on one poll tick isn't worth surfacing
        // to the customer — just try again next tick.
      }
      if (attempts >= maxAttempts) {
        clearInterval(timer);
        delete pollTimersRef.current[escalationId];
      }
    }, 10000);
    pollTimersRef.current[escalationId] = timer;
  }

  useEffect(() => {
    // Phase D (voice input): createVoiceInput touches `window`, so it can
    // only run after mount, same reasoning as this file's session_id
    // effect in app/page.tsx — the one-time false -> real-value re-render
    // is intentional, not the derived-state anti-pattern this lint rule
    // normally targets. Returns null on browsers with no SpeechRecognition
    // support (Firefox today), which is exactly what decides whether the
    // mic button renders at all below.
    const controller = createVoiceInput({
      onResult: (transcript) => setInputValue(transcript),
      onError: (message) => {
        setVoiceError(message);
        setIsListening(false);
      },
      onEnd: () => setIsListening(false),
    });
    voiceControllerRef.current = controller;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setVoiceSupported(controller !== null);
  }, []);

  function handleMicClick() {
    const controller = voiceControllerRef.current;
    if (!controller) return;
    setVoiceError(null);
    if (isListening) {
      controller.stop();
      setIsListening(false);
    } else {
      setInputValue("");
      setIsListening(true);
      controller.start();
    }
  }

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  function handleAttachClick() {
    fileInputRef.current?.click();
  }

  async function handleAttachmentSelected(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = ""; // lets picking the same file again re-trigger onChange
    if (!file) return;

    setAttachmentError(null);
    const previewUrl = URL.createObjectURL(file);
    setAttachmentUploading(true);
    try {
      const result = await uploadAttachment(file);
      setPendingAttachment({ id: result.attachmentId, previewUrl });
    } catch (err) {
      URL.revokeObjectURL(previewUrl);
      setAttachmentError(err instanceof ChatApiError ? err.message : "Could not upload the attachment.");
    } finally {
      setAttachmentUploading(false);
    }
  }

  function handleRemoveAttachment() {
    if (pendingAttachment) URL.revokeObjectURL(pendingAttachment.previewUrl);
    setPendingAttachment(null);
  }

  async function submitQuery(query: string) {
    if (!query || isLoading) return;

    const attachment = pendingAttachment;
    setMessages((prev) => [
      ...prev,
      {
        id: newId(),
        role: "user",
        text: query,
        timestamp: Date.now(),
        attachmentPreviewUrl: attachment?.previewUrl,
      },
    ]);
    setInputValue("");
    setPendingAttachment(null);
    await runQuery(query, attachment?.id);
  }

  async function runQuery(query: string, attachmentId?: string) {
    setIsLoading(true);
    try {
      const response = await sendMessage(query, sessionId, attachmentId);
      setMessages((prev) => [
        ...prev,
        { id: newId(), role: "assistant", text: response.detail, response, timestamp: Date.now() },
      ]);
      if (response.route === "escalate" && response.escalation_id) {
        startPolling(response.escalation_id);
      }
    } catch (err) {
      const message = err instanceof ChatApiError ? err.message : "Something went wrong. Please try again.";
      setMessages((prev) => [
        ...prev,
        { id: newId(), role: "error", text: message, timestamp: Date.now(), retryQuery: query },
      ]);
    } finally {
      setIsLoading(false);
    }
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    submitQuery(inputValue.trim());
  }

  function handleQuickReply(query: string) {
    // Behaves exactly like the user typing and sending it themselves —
    // same submitQuery() path, no special-cased backend behavior.
    submitQuery(query);
  }

  function handleRetry(errorMessageId: string, query: string) {
    // Drop the failed message and re-attempt in place — the user message
    // above it is already on screen, so it isn't re-added.
    setMessages((prev) => prev.filter((m) => m.id !== errorMessageId));
    runQuery(query);
  }

  return (
    <div className={styles.card}>
      <header className={styles.header}>
        <div className={styles.headerAvatar} aria-hidden="true">
          MR
        </div>
        <div className={styles.headerText}>
          <h1 className={styles.headerTitle}>Meridian Retail Support</h1>
          <p className={styles.headerSubtitle}>Usually replies in a few seconds</p>
        </div>
        <HistoryPanel sessionId={sessionId} />
      </header>

      <div className={styles.transcript} role="log" aria-live="polite" aria-label="Conversation">
        {messages.length === 0 && (
          <div className={styles.emptyState}>
            <p className={styles.emptyStateTitle}>How can we help?</p>
            <p className={styles.emptyStateSubtitle}>
              Ask about an order, shipping, refunds, your account, or anything else.
            </p>
            <div className={styles.quickReplies}>
              {QUICK_REPLIES.map((reply) => (
                <button
                  key={reply.label}
                  type="button"
                  className={`${styles.quickReplyChip} ${reply.variant === "escalate" ? styles.quickReplyChipEscalate : ""}`}
                  onClick={() => handleQuickReply(reply.query)}
                  disabled={isLoading}
                >
                  {reply.label}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((message, index) => (
          <MessageBubble
            key={message.id}
            message={message}
            onRetry={handleRetry}
            sessionId={sessionId}
            precedingQuery={findPrecedingUserQuery(messages, index)}
            escalationStatus={message.response?.escalation_id ? escalationStatuses[message.response.escalation_id] : undefined}
          />
        ))}

        {isLoading && (
          <div className={`${styles.bubbleRow} ${styles.bubbleRowAssistant}`}>
            <div className={`${styles.bubble} ${styles.bubbleAssistant} ${styles.typingBubble}`} aria-live="polite" aria-label="Assistant is typing">
              <span className={styles.typingDot} />
              <span className={styles.typingDot} />
              <span className={styles.typingDot} />
            </div>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {voiceError && <p className={styles.voiceError}>{voiceError}</p>}
      {attachmentError && <p className={styles.voiceError}>{attachmentError}</p>}

      {pendingAttachment && (
        <div className={styles.attachmentChip}>
          {/* eslint-disable-next-line @next/next/no-img-element -- a local
              blob: object URL, not an optimizable remote image */}
          <img src={pendingAttachment.previewUrl} alt="Attachment preview" className={styles.attachmentThumb} />
          <span className={styles.attachmentChipLabel}>Image attached</span>
          <button
            type="button"
            className={styles.attachmentRemoveButton}
            onClick={handleRemoveAttachment}
            aria-label="Remove attachment"
            title="Remove attachment"
          >
            ×
          </button>
        </div>
      )}

      <form className={styles.inputBar} onSubmit={handleSubmit}>
        <input
          ref={inputRef}
          className={styles.input}
          type="text"
          value={inputValue}
          onChange={(e) => setInputValue(e.target.value)}
          placeholder={isListening ? "Listening…" : "Type your message…"}
          aria-label="Type your message"
          disabled={isLoading}
          autoComplete="off"
        />
        <input
          ref={fileInputRef}
          type="file"
          accept="image/jpeg,image/png,image/webp"
          className={styles.hiddenFileInput}
          onChange={handleAttachmentSelected}
          aria-hidden="true"
          tabIndex={-1}
        />
        <button
          type="button"
          className={styles.attachButton}
          onClick={handleAttachClick}
          disabled={isLoading || attachmentUploading || Boolean(pendingAttachment)}
          aria-label="Attach an image"
          title="Attach an image"
        >
          {attachmentUploading ? "…" : "📎"}
        </button>
        {voiceSupported && (
          <button
            type="button"
            className={`${styles.micButton} ${isListening ? styles.micButtonActive : ""}`}
            onClick={handleMicClick}
            disabled={isLoading}
            aria-label={isListening ? "Stop voice input" : "Start voice input"}
            aria-pressed={isListening}
            title={isListening ? "Stop voice input" : "Start voice input"}
          >
            🎙️
          </button>
        )}
        <button
          type="submit"
          className={styles.sendButton}
          disabled={isLoading || !inputValue.trim()}
          aria-label="Send message"
        >
          Send
        </button>
      </form>
    </div>
  );
}

function MessageBubble({
  message,
  onRetry,
  sessionId,
  precedingQuery,
  escalationStatus,
}: {
  message: ChatMessage;
  onRetry: (errorMessageId: string, query: string) => void;
  sessionId: string;
  precedingQuery: string;
  escalationStatus?: EscalationStatus;
}) {
  // Phase B: RTL is detected per-message from its own text, not a manual
  // toggle or a conversation-wide setting — a customer can switch languages
  // mid-conversation and each bubble adapts independently.
  const rtl = isArabicText(message.text);

  if (message.role === "user") {
    return (
      <div className={`${styles.bubbleRow} ${rtl ? styles.bubbleRowRtlUser : styles.bubbleRowUser}`}>
        <div className={`${styles.bubbleColumn} ${rtl ? styles.bubbleColumnRtlUser : styles.bubbleColumnUser}`}>
          <div className={`${styles.bubble} ${styles.bubbleUser} ${rtl ? styles.bubbleRtl : ""}`} dir={rtl ? "rtl" : "ltr"}>
            {message.attachmentPreviewUrl && (
              // eslint-disable-next-line @next/next/no-img-element -- local blob: object URL, not an optimizable remote image
              <img src={message.attachmentPreviewUrl} alt="Attached image" className={styles.messageAttachmentImage} />
            )}
            {message.text}
          </div>
          <span className={styles.timestamp}>{formatTimestamp(message.timestamp)}</span>
        </div>
      </div>
    );
  }

  if (message.role === "error") {
    return (
      <div className={`${styles.bubbleRow} ${rtl ? styles.bubbleRowRtlAssistant : styles.bubbleRowAssistant}`}>
        <div className={`${styles.bubbleColumn} ${rtl ? styles.bubbleColumnRtlAssistant : styles.bubbleColumnAssistant}`}>
          <div className={`${styles.bubble} ${styles.bubbleError} ${rtl ? styles.bubbleRtl : ""}`} dir={rtl ? "rtl" : "ltr"} role="alert">
            <p className={styles.bubbleText}>{message.text}</p>
            {message.retryQuery && (
              <button
                type="button"
                className={styles.retryButton}
                onClick={() => onRetry(message.id, message.retryQuery!)}
              >
                Retry
              </button>
            )}
          </div>
          <span className={styles.timestamp}>{formatTimestamp(message.timestamp)}</span>
        </div>
      </div>
    );
  }

  if (message.role === "human_agent") {
    return (
      <div className={`${styles.bubbleRow} ${rtl ? styles.bubbleRowRtlAssistant : styles.bubbleRowAssistant}`}>
        <div className={`${styles.bubbleColumn} ${rtl ? styles.bubbleColumnRtlAssistant : styles.bubbleColumnAssistant}`}>
          <div className={`${styles.bubble} ${styles.bubbleAssistant} ${styles.bubbleHumanAgent} ${rtl ? styles.bubbleRtl : ""}`} dir={rtl ? "rtl" : "ltr"}>
            <div className={styles.humanAgentLabel}>
              <span aria-hidden="true">👤</span> {rtl ? "رد أحد أعضاء الفريق" : "A human agent replied"}
            </div>
            <p className={styles.bubbleText}>{message.text}</p>
          </div>
          <span className={styles.timestamp}>{formatTimestamp(message.timestamp)}</span>
        </div>
      </div>
    );
  }

  const response = message.response;
  // Phase D (chat history): a rehydrated message has no live `response`
  // object (only `route` is persisted), so it still gets route-based
  // styling via `historicalRoute` — just without the fuller live badge
  // row or feedback buttons, which need data that isn't stored.
  const effectiveRoute = response?.route ?? message.historicalRoute;
  const isEscalation = effectiveRoute === "escalate";

  return (
    <div className={`${styles.bubbleRow} ${rtl ? styles.bubbleRowRtlAssistant : styles.bubbleRowAssistant}`}>
      <div className={`${styles.bubbleColumn} ${rtl ? styles.bubbleColumnRtlAssistant : styles.bubbleColumnAssistant}`}>
        <div
          className={`${styles.bubble} ${styles.bubbleAssistant} ${isEscalation ? styles.bubbleEscalated : ""} ${
            effectiveRoute ? styles[`bubbleRoute_${effectiveRoute}`] : ""
          } ${rtl ? styles.bubbleRtl : ""}`}
          dir={rtl ? "rtl" : "ltr"}
        >
          {isEscalation && (
            <div className={styles.escalationNotice}>
              <div className={styles.escalationNoticeRow}>
                <span aria-hidden="true">🔔</span>{" "}
                {rtl ? "تم إبلاغ أحد أعضاء الفريق وسيتابع معك." : "A team member has been notified and will follow up."}
              </div>
              {escalationStatus?.human_status === "picked_up" && (
                <div className={styles.escalationPickedUp}>
                  <span aria-hidden="true">🟢</span>{" "}
                  {rtl ? "قام أحد أعضاء الفريق بمتابعة حالتك." : "An agent has picked up your case."}
                </div>
              )}
            </div>
          )}
          <p className={styles.bubbleText}>{message.text}</p>
          <div className={styles.bubbleFooter}>
            {response && (
              <div className={styles.badgeRow}>
                <span className={`${styles.badge} ${styles[`badge_${response.route}`]}`}>
                  {ROUTE_LABEL[response.route]}
                </span>
                <span className={styles.badgeMuted}>Intent: {response.intent}</span>
                <span className={styles.badgeMuted}>Confidence: {formatPct(response.intent_confidence)}</span>
                <span className={styles.badgeMuted}>Retrieval: {formatPct(response.retrieval_confidence)}</span>
              </div>
            )}
            {!response && message.historicalRoute && (
              <div className={styles.badgeRow}>
                <span className={`${styles.badge} ${styles[`badge_${message.historicalRoute}`]}`}>
                  {ROUTE_LABEL[message.historicalRoute]}
                </span>
              </div>
            )}
            <div className={styles.footerActions}>
              {response && (
                <FeedbackButtons
                  sessionId={sessionId}
                  query={precedingQuery}
                  responseText={message.text}
                  route={response.route}
                  intent={response.intent}
                />
              )}
              <CopyButton text={message.text} />
            </div>
          </div>
        </div>
        <span className={styles.timestamp}>{formatTimestamp(message.timestamp)}</span>
      </div>
    </div>
  );
}

function FeedbackButtons({
  sessionId,
  query,
  responseText,
  route,
  intent,
}: {
  sessionId: string;
  query: string;
  responseText: string;
  route: AgentResponse["route"];
  intent: string;
}) {
  const [vote, setVote] = useState<"up" | "down" | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleVote(thumbsUp: boolean) {
    if (submitting) return;
    setSubmitting(true);
    try {
      await submitFeedback({ sessionId, query, responseText, route, intent, thumbsUp });
      setVote(thumbsUp ? "up" : "down");
    } catch {
      // Feedback is a nice-to-have signal, not a customer-facing failure —
      // silently drop it rather than showing an error bubble for this.
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className={styles.feedbackButtons}>
      <button
        type="button"
        className={`${styles.feedbackButton} ${vote === "up" ? styles.feedbackButtonActive : ""}`}
        onClick={() => handleVote(true)}
        aria-label="Good response"
        aria-pressed={vote === "up"}
        title="Good response"
      >
        👍
      </button>
      <button
        type="button"
        className={`${styles.feedbackButton} ${vote === "down" ? styles.feedbackButtonActive : ""}`}
        onClick={() => handleVote(false)}
        aria-label="Bad response"
        aria-pressed={vote === "down"}
        title="Bad response"
      >
        👎
      </button>
    </div>
  );
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard access can be denied by the browser — silently no-op,
      // there's nothing actionable to show the user.
    }
  }

  return (
    <button
      type="button"
      className={styles.copyButton}
      onClick={handleCopy}
      aria-label={copied ? "Copied" : "Copy response"}
      title={copied ? "Copied!" : "Copy response"}
    >
      {copied ? (
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
          <polyline points="20 6 9 17 4 12" />
        </svg>
      ) : (
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <rect x="9" y="9" width="13" height="13" rx="2" />
          <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
        </svg>
      )}
    </button>
  );
}
