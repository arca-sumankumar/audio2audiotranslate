# Deployment

How the stack is deployed: local laptop (no Docker), docker compose, and the
single-node k3s lab deployment.

## Local development (no Docker)

For fast iteration, run the whole stack directly on the laptop — no images to
rebuild, edits apply on the next restart. Prereqs: Python 3.9+ and a NATS
JetStream server (`brew install nats-server`).

```bash
brew install nats-server

./scripts/run_local.sh     # venv + deps + nats-server + all 6 services + demo
make local-smoke           # same E2E batch + streaming test, against localhost
./scripts/stop_local.sh    # stop everything (keeps ./data: nats store, gateway.db, audio)
```

- `run_local.sh` creates `.venv/`, installs `stts-core` (editable) + all service
  deps, starts `nats-server -js` (data in `./data/nats`, monitor `:8222`) and
  launches ingest/gateway/asr/mt/tts/forwarder/demo with `STTS_*` env overrides.
- Logs go to `./data/<service>.log` (`make local-logs` to tail them).
- Config comes from env (service `config.yaml` is baked into the Docker images,
  not used locally). Overrides are documented in `common/stts_core/config.py`.
- The demo UI and the `INTEGRATION.md` examples work unchanged, pointing at
  the same `localhost:50010` / `51000` / `50060` ports.

Don't run the local stack and the docker stack at the same time — they share
ports 4222/50010/51000/50060.

## Docker compose (laptop)

Prereqs: Docker with Compose v2.

```bash
./start_all.sh  # build images (if needed) + start broker + all services
make smoke      # end-to-end batch + streaming test (creates sample.wav itself)
./stop_all.sh   # stop everything, NO data loss (JetStream store + gateway DB kept)
make logs
```

`start_all.sh` / `stop_all.sh` are thin wrappers around compose:

- `start_all.sh` runs `docker compose up -d --wait` for broker + all services
  (streams self-bootstrap; images are built automatically if missing).
- `stop_all.sh` runs `docker compose stop`, which halts containers but keeps
  the named volumes `stts_nats-data` (NATS JetStream store) and `stts_data`
  (gateway SQLite DB + output audio). Nothing is deleted, so a later
  `./start_all.sh` resumes with all prior jobs and data intact.
- To fully wipe everything (data loss), use `make clean` (`down -v`).

`make smoke` runs `scripts/smoke_test.py` inside a container that shares the
`/data` volume, so `POST /translate` can read the generated file.

## Lab deployment (k3s)

Prereqs: a single-node k3s cluster, images imported into containerd, and
host dirs prepared:

```bash
# 1. build the six images + tools image
make build

# 2. load them into k3s containerd (images have tag :local)
k3s ctr images import <image>.tar    # see note below

# 3. prepare host directories (single-node lab)
sudo mkdir -p /opt/stts/models /opt/stts/data /opt/stts/nats
sudo chown -R 1000:1000 /opt/stts/nats

# 4. deploy
make k3s-apply
make k3s-status
make k3s-smoke
```

Exposed NodePorts: ingest `30010`, gateway `30051` (default k3s NodePort
range 30000–32767). The `stts-config` ConfigMap drives runtime overrides
(see `deploy/k3s/configmap.yaml`).

To vendor model weights offline: put them under `/opt/stts/models` and switch
`STTS_MODEL_BACKEND` away from `mock` once the corresponding backend is
wired in `common/stts_core/models.py` (ASR/MT/TTS ABCs are the extension
points).

### Importing images into k3s (offline)

```bash
docker build -f services/ingest/Dockerfile -t stts/ingest:local .
# ... repeat for asr, mt, tts, forwarder, gateway, and Dockerfile.tools -> stts/tools:local
docker save stts/ingest:local stts/asr:local stts/mt:local stts/tts:local \
            stts/forwarder:local stts/gateway:local stts/tools:local | \
  k3s ctr images import -
```
