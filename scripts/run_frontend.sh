#!/usr/bin/env bash
# One-command frontend startup: runs `next dev` in the background, waits for
# it to become reachable, then prints a single consolidated status message.
set -euo pipefail
cd "$(dirname "$0")/../frontend"

PORT="${PORT:-3000}"
FRONTEND_LOG="$(mktemp -t frontend-XXXXXX.log)"
PID_FILE="/tmp/main-repo-frontend.pid"

if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "Frontend already running (PID $(cat "$PID_FILE")). Stop it first with: kill $(cat "$PID_FILE")"
  exit 1
fi

if [ ! -f .env.local ]; then
  echo "frontend/.env.local not found — copying from .env.local.example"
  cp .env.local.example .env.local
fi

echo "Starting frontend (next dev on :${PORT})..."
nohup npm run dev -- --port "$PORT" >"$FRONTEND_LOG" 2>&1 &
FRONTEND_PID=$!
echo "$FRONTEND_PID" >"$PID_FILE"

echo "Waiting for frontend to become reachable..."
ready="no"
for _ in $(seq 1 60); do
  if curl -sf -o /dev/null "http://localhost:${PORT}"; then
    ready="yes"
    break
  fi
  sleep 1
done

api_url=$(grep -m1 '^NEXT_PUBLIC_API_URL=' .env.local | cut -d= -f2-)

cat <<EOF

============================= Frontend Ready ===============================
Status:        $([ "$ready" = "yes" ] && echo "up" || echo "NOT responding yet — check the log file below")
Frontend URL:  http://localhost:${PORT}
Backend API:   ${api_url:-<not set in .env.local>}
PID:           ${FRONTEND_PID}  (stop with: kill ${FRONTEND_PID})
Log file:      ${FRONTEND_LOG}
=============================================================================
EOF
