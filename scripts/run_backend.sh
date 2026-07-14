#!/usr/bin/env bash
# One-command backend startup: brings up weaviate/neo4j/redis, starts uvicorn,
# waits for everything to be ready, then prints a single consolidated status
# message instead of scattering docker/model-loading/httpx logs on screen.
set -euo pipefail
cd "$(dirname "$0")/.."

BACKEND_PORT="${BACKEND_PORT:-8000}"
BACKEND_LOG="$(mktemp -t backend-XXXXXX.log)"
PID_FILE="/tmp/main-repo-backend.pid"

if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "Backend already running (PID $(cat "$PID_FILE")). Stop it first with: kill $(cat "$PID_FILE")"
  exit 1
fi

echo "Starting weaviate, neo4j, redis..."
docker compose up -d weaviate neo4j redis >/dev/null

echo "Waiting for docker services to become healthy..."
for _ in $(seq 1 60); do
  unhealthy=$(docker compose ps --format json weaviate neo4j redis 2>/dev/null \
    | jq -s '[.[] | select(.Health != "healthy")] | length')
  [ "$unhealthy" = "0" ] && break
  sleep 2
done

echo "Starting backend (uvicorn on :${BACKEND_PORT})..."
source venv/bin/activate
nohup uvicorn app.main:app --host 0.0.0.0 --port "$BACKEND_PORT" >"$BACKEND_LOG" 2>&1 &
BACKEND_PID=$!
echo "$BACKEND_PID" >"$PID_FILE"

echo "Waiting for backend /health..."
health_json="{}"
for _ in $(seq 1 60); do
  if health_json=$(curl -sf "http://localhost:${BACKEND_PORT}/health"); then
    break
  fi
  sleep 1
done

docker_status=$(docker compose ps --format json weaviate neo4j redis 2>/dev/null \
  | jq -s '[.[] | {service: .Service, status: .Status}]')

cat <<EOF

============================== Backend Ready ==============================
Backend URL:   http://localhost:${BACKEND_PORT}
API docs:      http://localhost:${BACKEND_PORT}/docs
PID:           ${BACKEND_PID}  (stop with: kill ${BACKEND_PID})
Log file:      ${BACKEND_LOG}

Docker services:
$(echo "$docker_status" | jq -r '.[] | "  - \(.service): \(.status)"')

Backend /health:
$(echo "$health_json" | jq -r '"  overall: \(.status // "unreachable")\n  dependencies:\n" + ((.dependencies // {}) | to_entries | map("    - \(.key): \(.value)") | join("\n"))')
=============================================================================
EOF
