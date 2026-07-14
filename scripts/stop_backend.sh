#!/usr/bin/env bash
# Stops the backend started by scripts/run_backend.sh.
set -euo pipefail

PID_FILE="/tmp/main-repo-backend.pid"

if [ ! -f "$PID_FILE" ] || ! kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "Backend is not running (no active PID found)."
  rm -f "$PID_FILE"
  exit 0
fi

PID="$(cat "$PID_FILE")"
kill "$PID"
rm -f "$PID_FILE"
echo "Backend stopped (PID $PID)."
