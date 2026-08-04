#!/usr/bin/env bash
# Stop the full STTS stack WITHOUT data loss.
# Uses `docker compose stop` (not `down -v`), so containers, networks,
# the NATS JetStream store (stts_nats-data) and the gateway DB / output
# audio (stts_data) are all preserved and re-used on the next start.
set -euo pipefail
cd "$(dirname "$0")"

docker compose -f deploy/docker-compose.yml stop

echo
echo "STTS stack stopped. Data preserved:"
echo "  - NATS JetStream store : volume stts_nats-data"
echo "  - gateway DB + audio   : volume stts_data"
echo "Restart with ./start_all.sh"
