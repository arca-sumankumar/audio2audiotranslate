#!/usr/bin/env bash
# Run the full STTS stack directly on the laptop (NO Docker).
#
# - creates a .venv and installs all service deps (first run)
# - starts nats-server with JetStream (brew install nats-server if missing)
# - launches all 6 services + the demo UI, logs under ./data/*.log
#
# Stop everything with ./scripts/stop_local.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATA="$ROOT/data"
VENV="$ROOT/.venv"
PY="$VENV/bin/python"
PIDFILE="$DATA/.stts.pids"

mkdir -p "$DATA/nats" "$DATA/output" "$DATA/uploads"

# --- abort if another stack is already up (prevents mock/real workers from
# --- sharing NATS subjects and stealing each other's chunks) ---
if nc -z 127.0.0.1 4222 >/dev/null 2>&1; then
  echo "[local] ERROR: NATS already listening on :4222 - an STTS stack is likely already running." >&2
  echo "[local] Stop it first with ./scripts/stop_local.sh (it now also reaps orphaned stack processes), then re-run." >&2
  exit 1
fi

# --- virtualenv + dependencies (first run, then cached) ---
if [ ! -x "$PY" ]; then
  echo "[local] creating virtualenv $VENV"
  python3 -m venv "$VENV"
fi
echo "[local] installing/updating python deps..."
"$PY" -m pip install --quiet --upgrade pip
"$PY" -m pip install --quiet \
  -e "$ROOT/common" \
  fastapi "uvicorn[standard]" pydantic PyYAML nats-py websockets flask requests huggingface_hub

# --- NATS JetStream server ---
if command -v nats-server >/dev/null 2>&1; then
  echo "[local] starting nats-server -js (monitor on :8222)"
  # max_payload 16MB: the TTS worker publishes whole WAVs (hex-encoded) in a
  # single message; the 1MB default is too small for long sessions.
  cat >"$DATA/nats-server.conf" <<EOF
port: 4222
http_port: 8222
max_payload: 16777216
jetstream {
  store_dir: "$DATA/nats"
}
EOF
  nats-server -c "$DATA/nats-server.conf" >"$DATA/nats.log" 2>&1 &
  echo $! >"$DATA/nats.pid"
  sleep 1
else
  echo "[local] ERROR: nats-server not found. Install it: brew install nats-server" >&2
  exit 1
fi

# --- services ---
: >"$PIDFILE"
export STTS_NATS_URL="nats://localhost:4222"
export STTS_AUDIO_OUTPUT_DIR="$DATA/output"
export STTS_MODEL_OFFLINEPATH="${STTS_MODELS_DIR:-$ROOT/models}"

# Model backends: mock (default, zero downloads) or real engines
# (STTS_MODEL_REAL=1, requires `make models` first).
if [ "${STTS_MODEL_REAL:-0}" = "1" ]; then
  ASR_BACKEND=whisper
  MT_BACKEND=nllb
  TTS_BACKEND=piper
  echo "[local] real models enabled (whisper/nllb/piper, offlinePath=$STTS_MODEL_OFFLINEPATH)"
else
  ASR_BACKEND=mock
  MT_BACKEND=mock
  TTS_BACKEND=mock
fi

start() {  # name cwd logfile [KEY=VAL...] -- cmd...
  local name=$1 cwd=$2 log=$3
  shift 3
  local assigns=()
  while [ "$1" != "--" ]; do assigns+=("$1"); shift; done
  shift
  (
    cd "$ROOT/$cwd"
    if [ ${#assigns[@]} -gt 0 ]; then export "${assigns[@]}"; fi
    exec "$PY" "$@"
  ) >"$log" 2>&1 &
  echo $! >>"$PIDFILE"
  echo "[local] $name up (pid $!) -> ${log##*/}"
}

start ingest   services/ingest    "$DATA/ingest.log" -- main.py
start gateway  services/gateway   "$DATA/gateway.log" \
  STTS_SERVER_PORT=51000 \
  STTS_GATEWAY_DBPATH="$DATA/gateway.db" -- main.py
start asr      services/asr       "$DATA/asr.log" \
  STTS_MODEL_BACKEND="$ASR_BACKEND" \
  STTS_ASR_INFERENCE_THREADS=1 -- worker.py
start mt       services/mt        "$DATA/mt.log" \
  STTS_MODEL_BACKEND="$MT_BACKEND" \
  STTS_MT_INFERENCE_THREADS=1 -- worker.py
start tts      services/tts       "$DATA/tts.log" \
  STTS_MODEL_BACKEND="$TTS_BACKEND" \
  STTS_TTS_INFERENCE_THREADS=1 -- worker.py
start forwarder services/forwarder "$DATA/forwarder.log" -- worker.py
start demo     services/demo      "$DATA/demo.log" \
  PORT=50060 \
  STTS_INGEST_URL=http://localhost:50010 \
  STTS_GATEWAY_URL=http://localhost:51000 \
  STTS_UPLOAD_DIR="$DATA/uploads" \
  STTS_TEST_AUDIO_DIR="$DATA/test_audio" -- app.py

echo
echo "STTS local stack starting. Wait a few seconds, then:"
echo "  REST/WS ingest : http://localhost:50010"
echo "  gateway (jobs) : http://localhost:51000"
echo "  demo UI        : http://localhost:50060"
echo "  NATS monitor   : http://localhost:8222"
echo "  logs           : tail -f $DATA/<name>.log"
echo "  e2e test       : STTS_INGEST_URL=http://localhost:50010 STTS_GATEWAY_URL=http://localhost:51000 STTS_DATA_DIR=$DATA .venv/bin/python scripts/smoke_test.py"
echo "  real models    : make models && make local-real   (stop first)"
echo "  stop           : ./scripts/stop_local.sh"
