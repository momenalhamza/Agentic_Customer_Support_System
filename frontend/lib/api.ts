// Typed client for the FastAPI backend (ARCHITECTURE.md §11). Pure client
// fetch — no Next.js API routes, no server-side data fetching.

export type AgentRoute = "information" | "action" | "escalate";

export interface AgentResponse {
  route: AgentRoute;
  intent: string;
  intent_confidence: number;
  retrieval_confidence: number;
  detail: string;
  escalation_id: string | null;
  session_id: string;
}

export class ChatApiError extends Error {}

function getApiUrl(): string {
  const apiUrl = process.env.NEXT_PUBLIC_API_URL;
  if (!apiUrl) {
    throw new ChatApiError(
      "NEXT_PUBLIC_API_URL is not set. Copy frontend/.env.local.example to frontend/.env.local."
    );
  }
  return apiUrl;
}

export async function sendMessage(
  query: string,
  sessionId: string,
  attachmentId?: string | null
): Promise<AgentResponse> {
  const apiUrl = getApiUrl();
  let response: Response;

  try {
    response = await fetch(`${apiUrl}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, session_id: sessionId, attachment_id: attachmentId ?? null }),
    });
  } catch {
    throw new ChatApiError("Could not reach the support backend. Check your connection and try again.");
  }

  if (!response.ok) {
    throw new ChatApiError(`The support backend returned an error (status ${response.status}).`);
  }

  return (await response.json()) as AgentResponse;
}

// --- Phase D: attachments ------------------------------------------------------

export interface AttachmentUploadResult {
  attachmentId: string;
  url: string;
}

export async function uploadAttachment(file: File): Promise<AttachmentUploadResult> {
  const apiUrl = getApiUrl();
  const formData = new FormData();
  formData.append("file", file);

  let response: Response;
  try {
    response = await fetch(`${apiUrl}/attachments`, { method: "POST", body: formData });
  } catch {
    throw new ChatApiError("Could not reach the support backend to upload the attachment.");
  }

  if (!response.ok) {
    let detail = `Upload failed (status ${response.status}).`;
    try {
      const body = await response.json();
      if (body?.detail) detail = body.detail;
    } catch {
      // Non-JSON error body — fall back to the generic message above.
    }
    throw new ChatApiError(detail);
  }

  const body = (await response.json()) as { attachment_id: string; url: string };
  return { attachmentId: body.attachment_id, url: body.url };
}

// --- Phase C: feedback -------------------------------------------------------

export interface FeedbackEntry {
  feedback_id: string;
  session_id: string;
  query: string;
  response_text: string;
  route: AgentRoute;
  intent: string;
  thumbs_up: boolean;
  timestamp: string;
}

export interface SubmitFeedbackInput {
  sessionId: string;
  query: string;
  responseText: string;
  route: AgentRoute;
  intent: string;
  thumbsUp: boolean;
}

export async function submitFeedback(input: SubmitFeedbackInput): Promise<FeedbackEntry> {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/feedback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      session_id: input.sessionId,
      query: input.query,
      response_text: input.responseText,
      route: input.route,
      intent: input.intent,
      thumbs_up: input.thumbsUp,
    }),
  });
  if (!response.ok) {
    throw new ChatApiError(`Could not submit feedback (status ${response.status}).`);
  }
  return (await response.json()) as FeedbackEntry;
}

export async function fetchFeedback(): Promise<FeedbackEntry[]> {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/feedback`);
  if (!response.ok) {
    throw new ChatApiError(`Could not load feedback (status ${response.status}).`);
  }
  return (await response.json()) as FeedbackEntry[];
}

// --- Phase C: admin dashboard -------------------------------------------------

export type HumanStatus = "waiting" | "picked_up" | "responded";

export interface EscalationEntry {
  escalation_id: string;
  session_id: string;
  contact: string | null;
  contact_captured: boolean;
  slack_delivered: boolean;
  query: string;
  retrieved_context: string[];
  reason: string;
  timestamp: string;
  attempted_summary: string;
  attachment_id: string | null;
  human_status: HumanStatus;
  human_reply: string | null;
  responded_at: string | null;
}

export async function fetchEscalations(slackDelivered?: boolean): Promise<EscalationEntry[]> {
  const apiUrl = getApiUrl();
  const url = new URL(`${apiUrl}/escalations`);
  if (slackDelivered !== undefined) {
    url.searchParams.set("slack_delivered", String(slackDelivered));
  }
  const response = await fetch(url.toString());
  if (!response.ok) {
    throw new ChatApiError(`Could not load escalations (status ${response.status}).`);
  }
  return (await response.json()) as EscalationEntry[];
}

// --- Phase D: live human handoff -------------------------------------------------

export interface EscalationStatus {
  escalation_id: string;
  human_status: HumanStatus;
  human_reply: string | null;
  responded_at: string | null;
}

// Deliberately unauthenticated — the customer-facing poll. The backend
// only ever returns this escalation's own status fields, never the full
// packet (no contact info) — see app/schemas.py's EscalationStatus.
export async function fetchEscalationStatus(escalationId: string): Promise<EscalationStatus> {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/escalations/${escalationId}/status`);
  if (!response.ok) {
    throw new ChatApiError(`Could not check escalation status (status ${response.status}).`);
  }
  return (await response.json()) as EscalationStatus;
}

// The admin action — gated server-side by ADMIN_API_TOKEN (see
// ANALYSIS.md's Phase D entry for why this one write endpoint is gated
// when every other admin-dashboard route today isn't).
export async function respondToEscalation(
  escalationId: string,
  status: "picked_up" | "responded",
  adminToken: string,
  reply?: string
): Promise<EscalationEntry> {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/escalations/${escalationId}/respond`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Admin-Token": adminToken },
    body: JSON.stringify({ status, reply: reply ?? null }),
  });
  if (!response.ok) {
    let detail = `Could not respond (status ${response.status}).`;
    try {
      const body = await response.json();
      if (body?.detail) detail = body.detail;
    } catch {
      // Non-JSON error body — fall back to the generic message above.
    }
    throw new ChatApiError(detail);
  }
  return (await response.json()) as EscalationEntry;
}

// --- Phase D: chat history persistence -----------------------------------------

export interface MessageEntry {
  message_id: string;
  session_id: string;
  customer_email: string | null;
  role: "user" | "assistant";
  text: string;
  route: AgentRoute | null;
  intent: string | null;
  timestamp: string;
}

export async function fetchConversationHistory(params: {
  sessionId?: string;
  email?: string;
}): Promise<MessageEntry[]> {
  const apiUrl = getApiUrl();
  const url = new URL(`${apiUrl}/conversations`);
  if (params.sessionId) url.searchParams.set("session_id", params.sessionId);
  if (params.email) url.searchParams.set("email", params.email);
  const response = await fetch(url.toString());
  if (!response.ok) {
    throw new ChatApiError(`Could not load conversation history (status ${response.status}).`);
  }
  return (await response.json()) as MessageEntry[];
}

export async function identifySession(sessionId: string, email: string): Promise<{ linked_messages: number }> {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/conversations/identify`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, email }),
  });
  if (!response.ok) {
    throw new ChatApiError(`Could not save your history (status ${response.status}).`);
  }
  return (await response.json()) as { linked_messages: number };
}

export interface MetricsSummary {
  groqDailyCalls: number;
  escalationsTotal: number;
  escalationsSlackUndelivered: number;
}

// /metrics is a Prometheus text-exposition endpoint, not JSON — this is a
// minimal line parser for the 3 gauges app/main.py actually publishes, not
// a general Prometheus client (no need for one just to read 3 numbers).
export async function fetchMetrics(): Promise<MetricsSummary> {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/metrics`);
  if (!response.ok) {
    throw new ChatApiError(`Could not load metrics (status ${response.status}).`);
  }
  const text = await response.text();
  const valueFor = (name: string): number => {
    const match = text.match(new RegExp(`^${name}\\s+([0-9.eE+-]+)$`, "m"));
    return match ? parseFloat(match[1]) : 0;
  };
  return {
    groqDailyCalls: valueFor("groq_daily_calls_total"),
    escalationsTotal: valueFor("escalations_total"),
    escalationsSlackUndelivered: valueFor("escalations_slack_undelivered_total"),
  };
}

// --- Phase D: usage analytics ---------------------------------------------------

export interface IntentCount {
  intent: string;
  count: number;
}

export interface HourlyCount {
  hour: number;
  count: number;
}

export interface DropoffSummary {
  single_turn_sessions: number;
  multi_turn_sessions: number;
}

export interface AnalyticsSummary {
  top_intents: IntentCount[];
  hourly_volume: HourlyCount[];
  dropoff: DropoffSummary;
}

// A dedicated JSON endpoint, not /metrics — see ANALYSIS.md's Phase D entry
// for why a Prometheus gauge shape doesn't fit ranked/bucketed results.
export async function fetchAnalytics(): Promise<AnalyticsSummary> {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/analytics`);
  if (!response.ok) {
    throw new ChatApiError(`Could not load analytics (status ${response.status}).`);
  }
  return (await response.json()) as AnalyticsSummary;
}
