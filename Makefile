.PHONY: build up down logs smoke ps clean \
	local-up local-down local-logs local-smoke local-real models test-audio test-batch \
	k3s-apply k3s-delete k3s-logs k3s-status k3s-smoke

# ---- laptop (docker compose) ----
build:
	docker compose -f deploy/docker-compose.yml build

up: build
	docker compose -f deploy/docker-compose.yml up -d --wait broker ingest asr mt tts forwarder gateway demo

demo-up: build
	docker compose -f deploy/docker-compose.yml up -d --wait demo

down:
	docker compose -f deploy/docker-compose.yml down

logs:
	docker compose -f deploy/docker-compose.yml logs -f --tail=100

ps:
	docker compose -f deploy/docker-compose.yml ps

smoke:
	docker compose -f deploy/docker-compose.yml run --rm smoke

clean:
	docker compose -f deploy/docker-compose.yml down -v

# ---- laptop (no Docker) ----
local-up:
	./scripts/run_local.sh

local-down:
	./scripts/stop_local.sh

local-logs:
	tail -f data/ingest.log data/asr.log data/mt.log data/tts.log data/forwarder.log data/gateway.log data/demo.log

local-smoke:
	STTS_INGEST_URL=http://localhost:50010 STTS_GATEWAY_URL=http://localhost:51000 \
	STTS_DATA_DIR=$(abspath data) .venv/bin/python scripts/smoke_test.py

# real models (whisper/nllb/piper) — run `make models` first
models:
	.venv/bin/python scripts/download_models.py

local-real:
	STTS_MODEL_REAL=1 ./scripts/run_local.sh

# doctor-patient test audio (data/test_audio/*.txt -> *.wav via Piper) + batch check
test-audio:
	.venv/bin/python scripts/make_test_audio.py

test-batch:
	.venv/bin/python scripts/test_batch_audio.py

# ---- lab (k3s) ----
k3s-apply:
	kubectl apply -k deploy/k3s

k3s-delete:
	kubectl delete -k deploy/k3s

k3s-logs:
	kubectl -n stts logs -f -l app.kubernetes.io/part-of=stts --all-containers=true

k3s-status:
	kubectl -n stts get pods,svc,jobs

k3s-smoke:
	kubectl -n stts delete job smoke --ignore-not-found
	kubectl -n stts apply -f deploy/k3s/smoke-job.yaml
	kubectl -n stts wait --for=condition=complete job/smoke --timeout=120s
	kubectl -n stts logs job/smoke
