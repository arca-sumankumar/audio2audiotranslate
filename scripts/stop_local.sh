#!/usr/bin/env bash
# Stop the local (no-Docker) STTS stack started by ./scripts/run_local.sh.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATA="$ROOT/data"
PIDFILE="$DATA/.stts.pids"

if [ -f "$PIDFILE" ]; then
  echo "[local] stopping services..."
  while read -r pid; do
    [ -n "$pid" ] && kill "$pid" 2>/dev/null && echo "  stopped pid $pid" || true
  done <"$PIDFILE"
  rm -f "$PIDFILE"
fi

# Reap leftover stack processes even if PIDFILE was clobbered by a second
# run_local.sh (PWD env pinpoints processes launched from this repo).
stray() { ps eww -axo pid,command 2>/dev/null | grep "$1" | grep -v grep | awk '{print $1}'; }
for pid in $(stray "PWD=$ROOT/services" | grep -E "(worker|main|app)\.py"); do
  kill "$pid" 2>/dev/null && echo "  reaped stray pid $pid" || true
done
for pid in $(stray "nats-server -c $DATA/nats-server.conf"); do
  kill "$pid" 2>/dev/null && echo "  stopped nats-server (pid $pid)" || true
done

# nats-server started by run_local.sh is stopped too (leaves ./data/nats intact)
if [ -f "$DATA/nats.pid" ]; then
  pid=$(cat "$DATA/nats.pid")
  if kill -0 "$pid" 2>/dev/null; then
    echo "[local] stopping nats-server (pid $pid)"
    kill "$pid" 2>/dev/null || true
  fi
  rm -f "$DATA/nats.pid"
fi

echo "[local] done. Data preserved under $DATA (nats store, gateway.db, output/)."
