"use client";

import { useEffect, useState } from "react";
import {
  AgentRoute,
  AnalyticsSummary,
  ChatApiError,
  EscalationEntry,
  FeedbackEntry,
  MetricsSummary,
  fetchAnalytics,
  fetchEscalations,
  fetchFeedback,
  fetchMetrics,
  respondToEscalation,
} from "@/lib/api";
import styles from "./AdminDashboard.module.css";

const ADMIN_TOKEN_KEY = "meridian_admin_token";

const ROUTES: AgentRoute[] = ["information", "action", "escalate"];

const ROUTE_LABEL: Record<AgentRoute, string> = {
  information: "Information",
  action: "Action",
  escalate: "Escalate",
};

export default function AdminDashboard() {
  const [metrics, setMetrics] = useState<MetricsSummary | null>(null);
  const [escalations, setEscalations] = useState<EscalationEntry[]>([]);
  const [feedback, setFeedback] = useState<FeedbackEntry[]>([]);
  const [analytics, setAnalytics] = useState<AnalyticsSummary | null>(null);
  const [showOnlyUndelivered, setShowOnlyUndelivered] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Phase D (live human handoff): a shared-secret token, not a login — kept
  // in localStorage so it's entered once per browser. Only ever sent on
  // POST /escalations/{id}/respond; every other route here stays exactly
  // as unauthenticated as it was in Phase C. See ANALYSIS.md's Phase D
  // entry for why this one write action is gated when reads aren't.
  const [adminToken, setAdminToken] = useState("");

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setAdminToken(window.localStorage.getItem(ADMIN_TOKEN_KEY) ?? "");
  }, []);

  function handleAdminTokenChange(value: string) {
    setAdminToken(value);
    window.localStorage.setItem(ADMIN_TOKEN_KEY, value);
  }

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setLoading(true);
      setError(null);
      try {
        const [metricsResult, escalationsResult, feedbackResult, analyticsResult] = await Promise.all([
          fetchMetrics(),
          fetchEscalations(showOnlyUndelivered ? false : undefined),
          fetchFeedback(),
          fetchAnalytics(),
        ]);
        if (cancelled) return;
        setMetrics(metricsResult);
        setEscalations(escalationsResult);
        setFeedback(feedbackResult);
        setAnalytics(analyticsResult);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof ChatApiError ? err.message : "Failed to load dashboard data.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    load();
    return () => {
      cancelled = true;
    };
  }, [showOnlyUndelivered]);

  async function refreshEscalations() {
    try {
      setEscalations(await fetchEscalations(showOnlyUndelivered ? false : undefined));
    } catch {
      // The row-level respond UI already surfaces its own error; a failed
      // refresh just means the table shows slightly stale data until the
      // next poll/reload — not worth a second error banner for.
    }
  }

  const totalUp = feedback.filter((f) => f.thumbs_up).length;
  const positiveRate = feedback.length > 0 ? Math.round((totalUp / feedback.length) * 100) : null;
  const feedbackByRoute = ROUTES.map((route) => {
    const forRoute = feedback.filter((f) => f.route === route);
    return {
      route,
      up: forRoute.filter((f) => f.thumbs_up).length,
      down: forRoute.filter((f) => !f.thumbs_up).length,
    };
  });

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <h1 className={styles.title}>Admin Dashboard</h1>
        <p className={styles.subtitle}>Groq usage, escalations, and customer feedback.</p>
        <div className={styles.adminTokenRow}>
          <label className={styles.adminTokenLabel} htmlFor="admin-token-input">
            Admin token (for responding to escalations):
          </label>
          <input
            id="admin-token-input"
            type="password"
            className={styles.adminTokenInput}
            value={adminToken}
            onChange={(e) => handleAdminTokenChange(e.target.value)}
            placeholder="paste ADMIN_API_TOKEN here"
            autoComplete="off"
          />
        </div>
      </header>

      {error && <div className={styles.errorBanner}>{error}</div>}

      <section className={styles.statRow}>
        <StatTile label="Groq calls today" value={metrics ? metrics.groqDailyCalls : null} loading={loading} />
        <StatTile label="Total escalations" value={metrics ? metrics.escalationsTotal : null} loading={loading} />
        <StatTile
          label="Undelivered to Slack"
          value={metrics ? metrics.escalationsSlackUndelivered : null}
          loading={loading}
          tone={metrics && metrics.escalationsSlackUndelivered > 0 ? "warning" : "good"}
        />
        <StatTile
          label="Positive feedback rate"
          value={positiveRate === null ? null : positiveRate}
          suffix={positiveRate === null ? "" : "%"}
          loading={loading}
        />
      </section>

      <section className={styles.panel}>
        <h2 className={styles.panelTitle}>Slack delivery</h2>
        <HorizontalBarChart
          bars={[
            {
              label: "Delivered",
              value: (metrics?.escalationsTotal ?? 0) - (metrics?.escalationsSlackUndelivered ?? 0),
              colorVar: "--badge-action-fg",
            },
            {
              label: "Undelivered",
              value: metrics?.escalationsSlackUndelivered ?? 0,
              colorVar: "--error-fg",
            },
          ]}
        />
      </section>

      <section className={styles.panel}>
        <h2 className={styles.panelTitle}>Feedback by route</h2>
        {feedback.length === 0 && !loading ? (
          <p className={styles.emptyNote}>No feedback recorded yet.</p>
        ) : (
          <div className={styles.groupedChart}>
            {feedbackByRoute.map(({ route, up, down }) => (
              <div key={route} className={styles.groupedChartRow}>
                <span className={styles.groupedChartLabel}>{ROUTE_LABEL[route]}</span>
                <HorizontalBarChart
                  bars={[
                    { label: "👍", value: up, colorVar: "--badge-action-fg" },
                    { label: "👎", value: down, colorVar: "--error-fg" },
                  ]}
                  compact
                />
              </div>
            ))}
          </div>
        )}
      </section>

      <section className={styles.panel}>
        <h2 className={styles.panelTitle}>Most common questions</h2>
        {!analytics || analytics.top_intents.length === 0 ? (
          <p className={styles.emptyNote}>{loading ? "Loading…" : "No messages recorded yet."}</p>
        ) : (
          <HorizontalBarChart
            bars={analytics.top_intents.map((i) => ({ label: i.intent, value: i.count, colorVar: "--accent" }))}
          />
        )}
      </section>

      <section className={styles.panel}>
        <h2 className={styles.panelTitle}>Peak times (hour of day, UTC)</h2>
        {!analytics ? (
          <p className={styles.emptyNote}>{loading ? "Loading…" : "No data yet."}</p>
        ) : (
          <HourlyBarChart hours={analytics.hourly_volume} />
        )}
        <p className={styles.emptyNote}>
          Note: this project has very little real traffic so far — this shows when THIS test/demo data happened to
          be sent, not a real usage pattern yet.
        </p>
      </section>

      <section className={styles.panel}>
        <h2 className={styles.panelTitle}>Session engagement</h2>
        {!analytics ? (
          <p className={styles.emptyNote}>{loading ? "Loading…" : "No data yet."}</p>
        ) : (
          <>
            <HorizontalBarChart
              bars={[
                { label: "Single message", value: analytics.dropoff.single_turn_sessions, colorVar: "--accent" },
                { label: "Multi-turn", value: analytics.dropoff.multi_turn_sessions, colorVar: "--accent" },
              ]}
            />
            <p className={styles.emptyNote}>
              A rough proxy, not a funnel: &quot;single message&quot; sessions asked once and never followed up —
              that can mean they got a complete answer right away, or gave up. This data alone can&apos;t tell those
              apart.
            </p>
          </>
        )}
      </section>

      <section className={styles.panel}>
        <div className={styles.panelHeaderRow}>
          <h2 className={styles.panelTitle}>Escalations</h2>
          <label className={styles.filterToggle}>
            <input
              type="checkbox"
              checked={showOnlyUndelivered}
              onChange={(e) => setShowOnlyUndelivered(e.target.checked)}
            />
            Show only Slack-undelivered
          </label>
        </div>

        {loading ? (
          <p className={styles.emptyNote}>Loading…</p>
        ) : escalations.length === 0 ? (
          <p className={styles.emptyNote}>No escalations match this filter.</p>
        ) : (
          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th>Timestamp</th>
                  <th>Query</th>
                  <th>Reason</th>
                  <th>Contact</th>
                  <th>Slack</th>
                  <th>Human status</th>
                  <th>Respond</th>
                </tr>
              </thead>
              <tbody>
                {escalations.map((e) => (
                  <tr key={e.escalation_id}>
                    <td className={styles.tableTimestamp}>{new Date(e.timestamp).toLocaleString()}</td>
                    <td className={styles.tableQuery}>{e.query}</td>
                    <td>{e.reason}</td>
                    <td>{e.contact ?? "—"}</td>
                    <td>
                      <span className={`${styles.statusBadge} ${e.slack_delivered ? styles.statusGood : styles.statusWarning}`}>
                        {e.slack_delivered ? "Delivered" : "Undelivered"}
                      </span>
                    </td>
                    <td>
                      <span
                        className={`${styles.statusBadge} ${
                          e.human_status === "responded" ? styles.statusGood : e.human_status === "picked_up" ? styles.statusInfo : styles.statusNeutral
                        }`}
                      >
                        {e.human_status}
                      </span>
                      {e.human_reply && <div className={styles.humanReplyPreview}>{e.human_reply}</div>}
                    </td>
                    <td>
                      <EscalationRespondCell
                        escalationId={e.escalation_id}
                        adminToken={adminToken}
                        onResponded={refreshEscalations}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

function EscalationRespondCell({
  escalationId,
  adminToken,
  onResponded,
}: {
  escalationId: string;
  adminToken: string;
  onResponded: () => void;
}) {
  const [reply, setReply] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handlePickedUp() {
    setSubmitting(true);
    setError(null);
    try {
      await respondToEscalation(escalationId, "picked_up", adminToken);
      onResponded();
    } catch (err) {
      setError(err instanceof ChatApiError ? err.message : "Failed.");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleSendReply() {
    if (!reply.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      await respondToEscalation(escalationId, "responded", adminToken, reply.trim());
      setReply("");
      onResponded();
    } catch (err) {
      setError(err instanceof ChatApiError ? err.message : "Failed.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className={styles.respondCell}>
      <button type="button" className={styles.respondButton} onClick={handlePickedUp} disabled={submitting || !adminToken}>
        Mark picked up
      </button>
      <div className={styles.respondReplyRow}>
        <input
          type="text"
          className={styles.respondReplyInput}
          placeholder="Reply to customer…"
          value={reply}
          onChange={(e) => setReply(e.target.value)}
          disabled={submitting || !adminToken}
        />
        <button
          type="button"
          className={styles.respondButton}
          onClick={handleSendReply}
          disabled={submitting || !adminToken || !reply.trim()}
        >
          Send
        </button>
      </div>
      {!adminToken && <p className={styles.respondHint}>Enter the admin token above to respond.</p>}
      {error && <p className={styles.respondError}>{error}</p>}
    </div>
  );
}

function StatTile({
  label,
  value,
  loading,
  suffix = "",
  tone,
}: {
  label: string;
  value: number | null;
  loading: boolean;
  suffix?: string;
  tone?: "good" | "warning";
}) {
  return (
    <div className={styles.statTile}>
      <span className={styles.statLabel}>{label}</span>
      <span className={`${styles.statValue} ${tone === "good" ? styles.statGood : tone === "warning" ? styles.statWarning : ""}`}>
        {loading || value === null ? "—" : `${value}${suffix}`}
      </span>
    </div>
  );
}

function HorizontalBarChart({
  bars,
  compact = false,
}: {
  bars: { label: string; value: number; colorVar: string }[];
  compact?: boolean;
}) {
  const max = Math.max(1, ...bars.map((b) => b.value));
  return (
    <div className={`${styles.barChart} ${compact ? styles.barChartCompact : ""}`}>
      {bars.map((bar) => (
        <div key={bar.label} className={styles.barRow}>
          <span className={styles.barLabel}>{bar.label}</span>
          <div className={styles.barTrack}>
            <div
              className={styles.barFill}
              style={{ width: `${(bar.value / max) * 100}%`, background: `var(${bar.colorVar})` }}
            />
          </div>
          <span className={styles.barValue}>{bar.value}</span>
        </div>
      ))}
    </div>
  );
}

function HourlyBarChart({ hours }: { hours: { hour: number; count: number }[] }) {
  const max = Math.max(1, ...hours.map((h) => h.count));
  return (
    <div className={styles.hourlyChart}>
      {hours.map((h) => (
        <div key={h.hour} className={styles.hourlyBarWrap} title={`${h.hour}:00 UTC — ${h.count} message${h.count === 1 ? "" : "s"}`}>
          <div className={styles.hourlyBarTrack}>
            <div className={styles.hourlyBarFill} style={{ height: `${(h.count / max) * 100}%` }} />
          </div>
          {h.hour % 6 === 0 && <span className={styles.hourlyLabel}>{h.hour}</span>}
        </div>
      ))}
    </div>
  );
}
