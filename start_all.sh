#!/usr/bin/env bash
# Start the full STTS stack (laptop, docker compose).
# Safe to re-run: builds images if missing, then brings everything up.
set -euo pipefail
cd "$(dirname "$0")"

docker compose -f deploy/docker-compose.yml up -d --wait broker ingest asr mt tts forwarder gateway demo

echo
echo "STTS stack is up."
echo "  REST/WS ingest : http://localhost:50010"
echo "  gateway (jobs) : http://localhost:51000"
echo "  demo UI        : http://localhost:50060"
echo "  NATS monitor   : http://localhost:8222"
echo "  e2e test       : make smoke"
