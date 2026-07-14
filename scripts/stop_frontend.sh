#!/usr/bin/env bash
# Stops the frontend started by scripts/run_frontend.sh.
set -euo pipefail

PID_FILE="/tmp/main-repo-frontend.pid"

if [ ! -f "$PID_FILE" ] || ! kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "Frontend is not running (no active PID found)."
  rm -f "$PID_FILE"
  exit 0
fi

PID="$(cat "$PID_FILE")"
kill "$PID"
rm -f "$PID_FILE"
echo "Frontend stopped (PID $PID)."
