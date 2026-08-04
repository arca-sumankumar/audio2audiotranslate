# STTS — Offline Speech Translation Service

Speech translation pipeline (batch + real-time streaming) that runs **fully
offline** (air-gapped laptop or datacenter). Architecture per
[HLD2](HLD2.md) / [LLD2](LLD2.md): an event-driven microservices pipeline
decoupled by a NATS JetStream broker. The default `mock` model backend lets
you run and test the entire system with zero model downloads.

## Architecture

```
Client ──REST──▶ ingest ─▶ [audio.in] ─▶ asr ─▶ [asr.out] ─▶ mt ─▶ [mt.out] ─▶ tts ─▶
Client ──WS───▶                          (NATS JetStream)                          │
                                                                                   ▼
Gateway ◀────────────────────────────── [output.events] ◀──────────────────────────┘
                                                      │
                                              forwarder ──WS──▶ downstream SDK
```

Services (all Python + FastAPI; ports in IANA dynamic range 49152–65535):

| Service    | Port  | Role                                                    |
|------------|-------|---------------------------------------------------------|
| broker     | 4222  | NATS JetStream (streams: audio_in, asr_out, mt_out, tts_out, output_events, jobs) |
| ingest     | 50010 | REST + WebSocket terminator, validation, chunking        |
| asr        | 50020 | offline ASR → incremental transcripts                    |
| mt         | 50030 | offline MT → translated text                             |
| tts        | 50040 | offline TTS → target-language audio                      |
| forwarder  | 50050 | reorder output.events, rebroadcast to SDK over WebSocket |
| gateway    | 51000 | job status, health, metrics, SQLite state                |
| demo       | 50060 | browser demo UI + upload proxy (lightweight Flask)       |

## Quick start (laptop, docker compose)

Prereqs: Docker with Compose v2.

```bash
./start_all.sh  # build images (if needed) + start broker + all services
make smoke      # end-to-end batch + streaming test (creates sample.wav itself)
./stop_all.sh   # stop everything, NO data loss (JetStream store + gateway DB kept)
make logs
```

`start_all.sh`/`stop_all.sh` are thin wrappers around `docker compose`
(`up -d --wait` / `stop`, keeping the `stts_nats-data` and `stts_data`
volumes; `make clean` wipes everything). Full details:
**[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)**.

## Demo UI + integration docs

The `demo` service (lightweight Flask, port 50060) serves a browser UI with two
demos, and is included in `./start_all.sh` / `make up`:

- **Batch (REST)**: upload a `.wav`/`.mp3` → job runs through the pipeline →
  transcript + translated audio playback.
- **Streaming (WebSocket)**: speak into the mic; live (partial/final)
  transcripts and translated audio stream back in real time.

The batch demo also comes with a built-in **doctor–patient test corpus** from
`data/test_audio/`: five English and five Malayalam recordings
(Piper-synthesized, with inline English medical terms) shown as `en-fever`,
`en-cough`, ..., `ms-fever`, `ms-cough`, ... , plus three **real Hindi
clinical recordings** (`hi-followup`, `hi-consult`, `hi-fever`) ready for the
Hindi → English path. Selecting a preset auto-sets the source language.
Gujarati transcripts exist too but have no Piper voice, so they only become
demo presets once you record a `NN_gu_<symptom>.wav` and drop it in
`data/test_audio/`.

```bash
open http://localhost:50060
```

Full client examples (curl, Python, downstream WebSocket SDK, event envelope
reference) are in **[docs/INTEGRATION.md](docs/INTEGRATION.md)**.

Supported languages: `en` + Indian languages `bn` (Bengali), `gu` (Gujarati),
`hi` (Hindi), `kn` (Kannada), `ml` (Malayalam), `mr` (Marathi), `pa`
(Punjabi), `ta` (Tamil), `te` (Telugu), `ur` (Urdu) — selectable as both
source and target in the demo UI and the APIs.

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

`run_local.sh` creates `.venv/`, installs `stts-core` (editable) + all service
deps, starts `nats-server -js` (data in `./data/nats`, monitor `:8222`) and
launches the services with `STTS_*` env overrides (logs in `./data/*.log`).
Details: **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)**.

Don't run the local stack and the docker stack at the same time — they share
ports 4222/50010/51000/50060.

## Real models (optional)

By default the pipeline runs with deterministic **mock** backends so the full
flow works with zero model downloads. To use real offline models —
faster-whisper large-v3-turbo (ASR, int8), NLLB-200 distilled 600M (MT, int8),
Piper (TTS), plus the Bergamot / IndicTrans2 MT candidates — download them
once and start with `make local-real`. Full model catalogue, download/
gating notes, the shipped gu/ml/mr ASR routing, and the MT-model evaluation
workflow are in **[docs/MODELS.md](docs/MODELS.md)** (large-file sizes +
procedures: [`list_of_lf.txt`](list_of_lf.txt)).

## Documentation

| Topic | Doc |
|---|---|
| **What each doc is for** | [docs/list.md](docs/list.md) |
| Use the APIs / integrate a client | [docs/INTEGRATION.md](docs/INTEGRATION.md) |
| Models, backends, ASR routing, MT-model eval | [docs/MODELS.md](docs/MODELS.md) |
| Run/test the pipeline (corpus, smoke, harnesses) | [docs/TESTING.md](docs/TESTING.md) |
| Local / docker / k3s deployment | [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) |
| Config env vars + concurrency tuning | [docs/CONFIGURATION.md](docs/CONFIGURATION.md) |
| STT quality on real Indic speech | [docs/REAL_NATIVE_EVAL.md](docs/REAL_NATIVE_EVAL.md) |
| Translation quality on the real-native chain | [docs/MT_EVAL.md](docs/MT_EVAL.md) |
| ASR routing integration verification | [docs/E2E_ROUTING_VERIFICATION.md](docs/E2E_ROUTING_VERIFICATION.md) |
| Design (HLD / LLD) | [HLD2.md](HLD2.md) · [LLD2.md](LLD2.md) · [PRD.md](PRD.md) |

## MT model evaluation

The project's main goal is evaluating candidate **machine-translation** models
(NLLB / Bergamot / IndicTrans2) in batch and live streaming modes, via the demo
UI's **Translation model** dropdown (per-session `model` WS/REST field). The
model catalogue, per-model notes (gating, licenses, runtime), and the
evaluation workflow are in **[docs/MODELS.md](docs/MODELS.md)**; measured
per-language quality on real native speech is in
**[docs/MT_EVAL.md](docs/MT_EVAL.md)**.

## Test corpus + smoke tests

The demo presets draw from a mixed-language `data/test_audio/` corpus
(English + Malayalam Piper-synthesized with inline medical terms, real clinical
Hindi, and the gu/ml/mr routing presets). Corpus layout, `make test-audio` /
`make test-batch`, `make smoke` / `make k3s-smoke`, the eval harness scripts,
and how to generate a test WAV for the batch API are in
**[docs/TESTING.md](docs/TESTING.md)**.

## Real-native ASR evaluation (STT quality)

The pipeline is measured against gold-standard **real human speech** — not
synthetic TTS — for both the batch English leg and the product chain
`native audio → STT in native script → MT to English`. Full numbers, error
analysis, and a repeatable re-run procedure are in
**[docs/REAL_NATIVE_EVAL.md](docs/REAL_NATIVE_EVAL.md)**; raw runs are logged
in `data/research.log`.

Baseline model: faster-whisper large-v3-turbo (int8, CPU). Language is forced
(never auto-detected), Silero VAD, beam 5, no English prompt on the Indic leg.

**Real-native STT verdict (mean WER / CER):**

| Lang | Source | WER / CER | Verdict |
|---|---|---|---|
| hi | ekacare (real clinical) | **0.638 / 0.375** | viable — with beam5 + native-script Hindi medical prompt |
| hi | eQOURSE (retail CS) | 0.833 / 0.568 | moderate (beam5, no prompt) |
| ml | eQOURSE | 1.12 / 0.81 | broken (wrong-script output) |
| mr | eQOURSE | 1.80 / 1.31 | broken (hallucination) |
| gu | eQOURSE | 2.19 / 2.9x | catastrophic (hallucination) |

**Key findings**

- The native-script Hindi `initial_prompt` (`common/stts_core/medical.py`,
  `MEDICAL_PROMPT["hi"]`) improves clinical Hindi (WER −0.016, CER −0.073) and
  recovers digits: **0/9 → 6/9** (clinical) and **60% → 80%** (retail).
- **Hotwords hurt Hindi** (0.638 → 0.792 WER): the `sot_prev` injection biases
  decoding toward English terms — keep them off for the `hi` leg.
- **ml/mr/gu are not viable** with large-v3-turbo on real speech (hallucination,
  script-mixing); MT then renders the garbage as fluent English, so poor STT is
  silently masked. These languages need a dedicated Indic ASR (e.g.
  IndicConformer / IndicASR) or a fine-tuned model.
- English gold-standard baseline on the simulated doctor–patient corpus:
  **0.285 WER / 0.123 CER** (full-file, medical context).

## Real-native MT evaluation (translation quality)

The translation leg of the same chain (`native audio → STT in native script →
MT to English`) is measured reference-free on the real-native sets with the
shipped ASR routing. Full method, per-clip numbers, error analysis and rerun
procedure: **[docs/MT_EVAL.md](docs/MT_EVAL.md)**.

**Condensed verdict** (cascade WER between EN(gold) and EN(STT); higher =
more STT noise reaches the customer):

| lang | NLLB cascWER | IndicTrans2 cascWER | English-term recall in EN(STT) | Recommended MT |
|---|---|---|---|---|
| hi (ekacare) | 0.931 | **0.815** | 0.50 / **0.60** | IndicTrans2 |
| hi (eQOURSE) | 0.929 | **0.784** | 0.55 / 0.55 | IndicTrans2 |
| gu | **0.819** | 0.911 | 0.52 / 0.55 | NLLB (keeps digits) |
| ml | 1.062 | 1.230 | 0.00 / 0.00 | neither — normalize number-words first |
| mr | **0.867** | 0.937 | 0.42 / **0.69** | mixed (IT2 terms vs NLLB numbers) |

Key findings:

- STT noise propagates through MT and MT *compounds* it (NLLB truncates longer
  inputs, occasionally emits garbage; Malayalam number-words re-derive wrong
  numbers, e.g. `299` → `Rs. 2,999`). Translation cannot repair upstream ASR
  loss.
- Inline English terms are lost for the routed languages (ml word recall 0.00
  under both MT models) because IndicConformer phoneticizes them into native
  script.
- NLLB preserves numerals as digits, IndicTrans2 spells them as words; only
  25% of ekacare dosage spans survive under either model.
- Model agreement is low (WER 0.66–1.38), so per-language choices above were
  confirmed by reading outputs, not scores alone.

## Manual API calls

Quick batch + streaming examples (curl, WebSocket JSON frames), the `model`
field, and a complete working client (`scripts/smoke_test.py`) are in
**[docs/INTEGRATION.md](docs/INTEGRATION.md)** (§1 batch, §2 streaming, §4
event envelope, §5 ports). The gateway is at `:51000/api/v1/jobs/<jobId>` for
batch status.

## Deployment

Local (no Docker), docker compose, and single-node **k3s** lab deployment —
including the offline image-import procedure and model-weights vendoring — are
in **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)**.

## Configuration & concurrency

Every service reads `/app/config.yaml` with `STTS_` env overrides
(`STTS_NATS_URL`, `STTS_AUDIO_CHUNKDURATIONMS`, `STTS_FORWARDER_REORDERWINDOW`,
`STTS_MODEL_BACKEND`, ...). Backend threading, per-service scale-out limits
(session affinity, reorder buffers), and ordering/delivery guarantees are in
**[docs/CONFIGURATION.md](docs/CONFIGURATION.md)**.
