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

- `run_local.sh` creates `.venv/`, installs `stts-core` (editable) + all service
  deps, starts `nats-server -js` (data in `./data/nats`, monitor `:8222`) and
  launches ingest/gateway/asr/mt/tts/forwarder/demo with `STTS_*` env overrides.
- Logs go to `./data/<service>.log` (`make local-logs` to tail them).
- Config comes from env (service `config.yaml` is baked into the Docker images,
  not used locally). Overrides are documented in
  `common/stts_core/config.py`.
- The demo UI and the `docs/INTEGRATION.md` examples work unchanged, pointing at
  the same `localhost:50010` / `51000` / `50060` ports.

Don't run the local stack and the docker stack at the same time — they share
ports 4222/50010/51000/50060.

## Real models (optional)

By default the pipeline runs with deterministic **mock** backends (fake
transcripts, beep TTS) so the full flow works with zero model downloads. To
use real offline models — faster-whisper large-v3-turbo (ASR, int8),
NLLB-200 distilled 600M (MT, int8), Piper (TTS), plus the Bergamot / IndicTrans2
MT candidates — download them once, then start the stack with real backends:

```bash
make models        # whisper, nllb-600m, piper, bergamot/ (per-pair), indictrans2/ (2 x ~1 GB)
make local-real    # start the stack with STTS_MODEL_REAL=1
```

- TTS voices ship for **English**, **Hindi** and **Malayalam**; other target
  languages fall back to the mock beep (Piper has no Tamil/Telugu/etc. voices
  yet).
- The source language is taken from the caller — the WS `sourceLanguage`
  query param, the batch `sourceLanguage` field, or the demo preset — and
  used to seed Whisper. It only falls back to auto-detection when absent,
  and auto-detection can mislabel Malayalam as Tamil, so pass the language
  explicitly when you know it.
- Piper's synthetic Malayalam (`ml_IN-arjun`) is out-of-distribution for
  Whisper, so `ms-*` preset translations are unreliable. Use real human
  recordings for usable `ml → …` output; the Gujarati presets already expect
  recorded audio.
- `make local-smoke` still uses the mock stack (`make local-up`); it uploads a
  sine tone, which a real Whisper correctly transcribes as silence.
- Model backends are also selectable per service via
  `STTS_MODEL_BACKEND={mock|whisper|nllb|piper|bergamot|indictrans2}` and
  `STTS_MODEL_OFFLINEPATH=<dir>`; the MT model per *session* comes from the
  demo dropdown (`model` WS/REST field), overriding the worker default.
- When a known `sourceLanguage` is supplied, the ASR applies a medical-domain
  context — a per-language `initial_prompt` plus hotwords (symptoms, drugs,
  units) and a conservative `hallucination_silence_threshold` — to preserve the
  English medical terms spoken inside Indian-language audio. Override any of
  them with `STTS_ASR_INITIAL_PROMPT`, `STTS_ASR_HOTWORDS`,
  `STTS_ASR_HALLUCINATION_SILENCE_THRESHOLD` (see `common/stts_core/medical.py`
  and LLD2 §12.6). For **Hindi the hotwords are disabled by default** — the
  `sot_prev` injection measurably hurts Hindi WER on real audio (0.638 → 0.792,
  see [docs/REAL_NATIVE_EVAL.md](docs/REAL_NATIVE_EVAL.md) §4.3) — while the
  native-script prompt stays on.

## Model evaluation (batch + streaming)

Evaluating candidate **machine-translation** models in BOTH batch and live
streaming modes is the project's main goal. Every admitted model must cover
**Gujarati and Tamil** (plus the other Indic languages) and be open source.

The demo UI has a **Translation model** dropdown at the top that applies to
both demos (batch and streaming); the selection flows through as the WS
`model` query param or the batch `model` JSON field and switches the MT worker
per session.

| Model (`id`) | Languages | flores200-plus BLEU (en→xx / xx→en) | Size | License | Runtime |
|---|---|---|---|---|---|
| **NLLB-200 distilled 600M** (`nllb`, baseline) | all 11 | — | ~1.1 GB | CC-BY-NC-4.0 | CTranslate2 int8 (CPU) |
| **Mozilla Firefox Translations** (`bergamot`, tiny) | en↔gu/hi/kn/ml/ta | gu 24.1 / 32.5 · hi 35.6 / 36.1 · kn 21.1 / 28.9 · ml 18.7 / 30.0 · ta 22.5 / 27.9 | ~17 MB/pair | MPL-2.0 | `fxtranslate` (native, CPU-fast) |
| **AI4Bharat IndicTrans2** (`indictrans2`, 1.1B) | en↔all 11 | — | 2 × ~1 GB weights (≈8 GB disk, ships both `pytorch_model.bin` and `model.safetensors`) | MIT | transformers + torch (CPU-slow) |

- `bergamot` pairs are English-centric (en→xx / xx→en only) and per-direction
  model files; `indictrans2` is likewise English↔Indic — use `nllb` for
  indic→indic pairs.
- Download them with `make models` (Bergamot files are pulled from Mozilla's
  registry and hash-verified; IndicTrans2 pulls the two official ai4bharat
  checkpoints, which are **gated** on Hugging Face — accept the terms on
  `ai4bharat/indictrans2-en-indic-1B` and `-indic-en-1B` and run
  `huggingface-cli login` first). Then: `pip install fxtranslate` for
  `bergamot`, and `pip install torch transformers` for `indictrans2`.
- IndicTrans2 uses `trust_remote_code=True` (the checkpoints ship custom
  modeling/tokenization code) and its en→indic checkpoints emit Devanagari as
  an intermediate script: the backend transliterates the output to the native
  script (gu/ta/kn/ml/bn/te/pa/or/as) before returning it. KV cache is disabled
  (`use_cache=False`) because the custom modeling code is incompatible with the
  modern `transformers` cache format; expect ~2-10 s/sentence on CPU, so it is
  practical for batch demos but slower than `bergamot`/`nllb` for streaming.
- Selecting a model whose files/packages aren't installed does not crash the
  stack: the MT worker surfaces an `error` event, the batch job is marked
  `failed` with the reason, and the streaming client is told to stop.
- Evaluation workflow: pick a model in the dropdown, run the batch preset
  (source + translated transcript boxes) and a streaming mic session, and
  compare. Streaming correctness criteria are (1) no repeated words and (2) no
  missed words across the confirm/trim boundaries; batch is scored against the
  recorded `data/test_audio/*.txt` transcripts via `make test-batch`.

For a **Hindi → English** demo, select **NLLB** (the configured default; fast)
or **IndicTrans2** (best hi→en quality per [docs/MT_EVAL.md](docs/MT_EVAL.md);
slow on CPU, ~2–10 s/sentence).
**Bergamot** hi↔en requires `make models` + `pip install fxtranslate` first —
without them the job fails with a clear `MT_ERROR`.

## Doctor–patient test corpus

`data/test_audio/` is a mixed-language corpus used by the demo presets and the
batch test script. Each transcript mixes natural speech with inline English
medical terms (fever, temperature, blood test, prescription, headache, ...).

- `01_fever` … `05_backpain` — English (Piper `en_US-lessac`).
- `06_ml_fever` … `10_ml_jointpain` — Malayalam (Piper `ml_IN-arjun`).
- `11_gu_fever` … `15_gu_jointpain` — Gujarati transcripts only (no Piper gu
  voice); record `.wav` files as `NN_gu_<symptom>.wav` to enable them.
- `16_hi_followup` … `18_hi_fever` — **real clinical Hindi** recordings from
  the ekacare/eka-medical-asr-evaluation-dataset (auto-sets source `hi`), for
  the Hindi → English demo path.

```bash
make models       # once, needed for synthesis
make test-audio   # synthesize the missing WAVs from the .txt transcripts
make test-batch   # run every .wav through the real stack, print per-file results
```

`make test-batch` requires the **real** stack (`make local-real`) and submits
each WAV via the batch REST API, printing the source transcript next to the
translation plus a PASS/FAIL sanity check (English is reliable; Malayalam
passes the script-level check but its content remains unreliable per the note
above). Pass a target language to the script to translate elsewhere:
`.venv/bin/python scripts/test_batch_audio.py ta`.

To measure what the medical context does to the raw ASR output, run
`.venv/bin/python scripts/eval_asr.py [en|ml]` (decodes each WAV with and
without the context and reports glossary-term/number retention and WER deltas).

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

```bash
# generate a 5s WAV on the host, copy into the shared data volume
docker run --rm -v "$(pwd)/data:/data" stts/tools:local \
  python -c "from stts_core.audio import synth_tone_wav; open('/data/sample.wav','wb').write(synth_tone_wav(5000))"

# batch
curl -s -X POST localhost:50010/api/v1/translate \
  -H 'Content-Type: application/json' \
  -d '{"filePath":"/data/sample.wav","fileFormat":"wav","sourceLanguage":"en","targetLanguage":"hi","model":"nllb"}'
# -> {"jobId":"...","status":"queued"}   ("model" is optional; '' = configured default)

curl -s localhost:51000/api/v1/jobs/<jobId>
# -> {"jobId":"...","status":"done","transcript":"...","outputPath":"/data/output/<jobId>.wav",...}
```

### Streaming (WebSocket)

Connect to `ws://localhost:50010/api/v1/stream?sourceLanguage=en&targetLanguage=hi&format=wav&model=bergamot`,
send JSON text frames:

```json
{"type":"audio_chunk","seqNo":1,"data":"<base64 wav>","isFinal":false}
```

Receive `ack`, `partial_transcript`, `final_transcript`, `audio_output`
(hex wav), `end`. See `scripts/smoke_test.py` for a working client.

- `sourceLanguage` seeds Whisper — pass it explicitly, since auto-detection
  can mislabel Malayalam as Tamil (see docs/INTEGRATION.md §2).
- Live partials are emitted roughly every ~4 s of **new** speech and are
  silence-gated (Silero VAD), so the transcript does not hallucinate while you
  are quiet.
- A streaming session emits **one `final_transcript` per confirmed segment**
  (plus their `mt` translations), then a final marked `sessionEnd: true`, then
  `end`. Confirmed text is immutable and the unconfirmed audio tail stays
  bounded (~7.5 s peak for a 28 s session) — see `common/stts_core/backends.py`
  `_pending`/`_confirmed` and LLD2.md §4.2.

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

## Configuration

Every service reads `/app/config.yaml` (defaults + documented ranges) with
`STTS_` env overrides, e.g.:

```bash
STTS_NATS_URL=nats://localhost:4222
STTS_AUDIO_CHUNKDURATIONMS=400
STTS_FORWARDER_REORDERWINDOW=64
```

See `common/stts_core/config.py` and each `services/*/config.yaml`.

## Concurrency notes

- **ingest**: async, one receive + one publish task per WS session, bounded by
  `ingest.maxSessions`; publisher waits on broker ack (backpressure).
- **asr / tts**: blocking model inference runs in a thread pool
  (`asr.inferenceThreads` / `tts.inferenceThreads`). Sessions require replica
  affinity in real backends; keep replicas at 1 for now (see LLD2 §4.2).
- **mt**: stateless, no session affinity; scale freely (`--scale mt=4`).
- **forwarder**: per-session reorder buffers by `seqNo`, single-writer
  outbound socket, retry with exponential backoff.
- Ordering/delivery guarantees are documented in LLD2 §6.

## Tests

```bash
make smoke          # E2E batch + streaming over docker compose
make k3s-smoke      # same suite as a k3s Job
```

Unit tests are planned; the smoke test covers the full
ingest→asr→mt→tts→gateway/forwarder path.
