# LLD2 — Low-Level Design for HLD2 (Microservices Pipeline + Message Broker)

## 1. Scope

This LLD specifies the concrete implementation of the HLD2 architecture: an event-driven, horizontally scalable, offline speech translation pipeline. It covers per-service APIs, broker topics and message schemas, concurrency model, ordering guarantees, storage, configuration, and deployment.

## 2. System Components

| Service     | Port (dynamic range 49152–65535) | Role                                      |
|-------------|----------------------------------|-------------------------------------------|
| `ingest`    | 50010                            | REST + WebSocket terminator, validation, chunking, publishes to broker |
| `asr`       | 50020 (internal)                 | Consumes `audio.in`, produces incremental transcripts |
| `mt`        | 50030 (internal)                 | Consumes `asr.out`, translates segments   |
| `tts`       | 50040 (internal)                 | Consumes `mt.out`, synthesizes target audio |
| `forwarder` | 50050 (internal)                 | Consumes `output.events`, rebroadcasts to downstream SDK over WS |
| `gateway`   | 51000                            | Public REST endpoint for batch results + health |
| `broker`    | NATS: 4222 / JetStream store     | Durable async transport                    |

## 3. Broker Design (NATS JetStream)

### 3.1 Streams & Subjects

| Stream      | Subject        | Retention | Max Age | Dedup      | Purpose                          |
|-------------|----------------|-----------|---------|------------|----------------------------------|
| `audio_in`  | `audio_in.*`   | Limits    | 60 s    | msg-id     | Raw decoded chunks per session   |
| `asr_out`   | `asr_out.*`    | Limits    | 60 s    | msg-id     | Partial/final source transcripts |
| `mt_out`    | `mt_out.*`     | Limits    | 60 s    | msg-id     | Translated target text segments  |
| `tts_out`   | `tts_out.*`    | Limits    | 60 s    | msg-id     | Reserved (see note)              |
| `output_events` | `output_events.*` | Limits | 10 min | msg-id     | Consolidated events for forwarder |
| `jobs`      | `jobs.*`       | Limits    | 1 day   | msg-id     | Batch jobs lifecycle             |

- **Subject names use underscores** (`audio_in.<sessionId>`), matching the
  actual NATS stream config (`common/stts_core/nats.py`); the diagram/README
  may show dotted aliases for readability.
- **Streams are sharded by session** using subject wildcards: `audio_in.<sessionId>`.
- **Durable consumer per worker pool** so offsets survive worker restarts.
- **Dedup window**: all producers set `Nats-Msg-Id = messageId` (a fresh UUID
  per event) so producer retries deduplicate without colliding across stages.
- **`tts_out` is reserved but unused**: the TTS worker publishes its
  `audio_output` directly to `output_events.<sessionId>`; the stream exists so
  a future TTS→downstream path can be added without a broker change.

### 3.2 Message Envelope (all services)

```json
{
  "schemaVersion": "1.0",
  "messageId": "uuid",
  "sessionId": "uuid",
  "jobId": "uuid-or-null",
  "seqNo": 42,
  "type": "audio_in|partial_transcript|final_transcript|audio_output|job_started|job_done|job_failed|session_started|end|error",
  "sourceLanguage": "en",
  "targetLanguage": "hi",
  "payload": {},
  "timestamp": "ISO-8601-UTC"
}
```

## 4. Service Specifications

### 4.1 `ingest`

#### REST endpoints
- `POST /api/v1/translate` — batch job. Request/response per PRD §2.1. Response is `202 Accepted` with `jobId`; client polls `GET /api/v1/jobs/{jobId}` (gateway) for status.
- `GET /api/v1/health` — liveness.
- `GET /api/v1/ready` — readiness (broker reachable).

#### WebSocket `/api/v1/stream`
Handshake query params: `?sourceLanguage=en&targetLanguage=hi&sampleRate=16000&format=wav`.

`sourceLanguage` **must match the spoken language** — it seeds Whisper in the
ASR stage. When absent, Whisper auto-detects, which can mislabel Malayalam as
Tamil (measured `ta` at ~0.4 probability), so clients should pass it whenever
the language is known.

Session state machine:
```
CONNECTING → AUTHENTICATED → STREAMING ⇄ PAUSED → COMPLETED / ERROR
```

Frame protocol (JSON text frames; audio as base64):

Client → Server (only `audio_chunk` is implemented; `pause`/`resume`/`close`
are not — any other `type` is rejected with `INVALID_FRAME`):
```json
{ "type": "audio_chunk", "seqNo": 1, "data": "<base64 wav>", "isFinal": false }
```

Server → Client (envelope; stage-specific fields live under `payload`):
```json
{ "type": "session_started", "payload": { "format": "wav" } }
{ "type": "ack", "seqNo": 1 }
{ "type": "partial_transcript", "payload": { "stage": "asr|mt", "text": "...", "isFinal": false } }
{ "type": "final_transcript", "payload": { "stage": "asr|mt", "text": "...", "startMs": 0, "endMs": 300, "isFinal": true, "sessionEnd": false } }
{ "type": "audio_output", "payload": { "format": "wav", "startMs": 0, "endMs": 300, "data": "<hex wav>" } }
{ "type": "error", "payload": { "code": "ERR_CODE", "message": "..." } }
{ "type": "end", "payload": { "reason": "client_closed|complete" } }
```

#### Chunking
- Accumulate input bytes until `audio.chunkDurationMs` (default 300 ms, range
  200–500) of decoded audio is reached, then publish to `audio_in.<sessionId>`
  with `seqNo` incremented.
- The **client** drives finalization: the last chunk carries `isFinal: true`;
  the chunker marks the final emitted chunk `is_final` (including a partial
  trailing chunk). There is **no auto-finalize on a silence gap** — silence
  handling happens downstream in the ASR stage (Silero VAD gates partials, see
  §4.2). If the client closes the socket before sending a final chunk, ingest
  publishes an `end` (reason `client_closed`) from its `finally` block.

#### Concurrency model
- Async: single event loop per process; one receive task + one publisher task per WS connection.
- Backpressure: publisher task waits on broker ack before reading the next chunk; flow control via `ack` to client.
- Max concurrent WS sessions per instance = configurable (`ingest.maxSessions`, default 1000); beyond that, reject with `503`.

### 4.2 `asr` (worker)

#### Input
Consumes `audio_in.<sessionId>`.

#### Processing
1. Decode PCM chunk → run offline ASR (faster-whisper large-v3-turbo, int8)
   seeded with `sourceLanguage`.
2. Streaming uses **incremental continuation** — per-session memory stays
   bounded and already-heard words can never be lost:
   - Keep only the unconfirmed audio **tail** (`_pending`) plus the immutable
     confirmed transcript (`_confirmed`); `_abs_ms` records the tail's offset.
   - Decode the tail seeded with `initial_prompt` = medical prompt + the last
     `CONFIRM_PROMPT_WORDS` (80) confirmed words, so Whisper *continues* the
     transcript instead of re-transcribing the whole buffer.
   - Decodes run on a **throttled cadence**, not per chunk: only when ≥ `4 s`
     of **new** audio has arrived (`PARTIAL_RECHECK_SAMPLES`) **and** the
     previous decode is not still busy **and** the new audio contains speech
     (Silero VAD gate; silence produces no partials).
   - Confirm (immutable) the words older than `CONFIRM_KEEP_BACK_MS` (3 s)
     before the tail end, emit them as a per-segment `final_transcript`, trim
     the tail up to the confirmation boundary, and re-seed from the new offset.
     Partials are the words after the boundary. A leading seam-echo duplicate
     (prompt echo / boundary overlap) is stripped before emitting.
   - All streaming decodes use `beam_size=5`; the confirmed segments are cut
     straight from these decodes, so greedy artifacts would get committed as
     immutable.
3. On the final chunk (`isFinal`), confirm the whole remaining tail, reset
   session state, and emit the last `final_transcript` with `sessionEnd: true`
   — this is what lets TTS emit the session `end` exactly once.
4. Whisper decode options: `condition_on_previous_text=False` and
   `no_repeat_ngram_size=3` (added after observed "word word word" repetition
   loops), `vad_filter=True`.
5. **Medical-domain context** is applied whenever `sourceLanguage` is known
   (never while auto-detecting, to avoid biasing language selection):
   `initial_prompt` (per-language native-script prompt with the spoken English
   medical terms embedded), `hotwords` (corpus terms + common drugs + units),
   and a conservative `hallucination_silence_threshold` (0.7 s). All three are
   overridable via `STTS_ASR_INITIAL_PROMPT`, `STTS_ASR_HOTWORDS`,
   `STTS_ASR_HALLUCINATION_SILENCE_THRESHOLD`. See
   `common/stts_core/medical.py` and §12.7.
6. Publish to `asr_out.<sessionId>` and `output_events.<sessionId>`.

#### Batch mode
For batch jobs the same worker aggregates: partials are suppressed and only
the final transcript is emitted once (the gateway stores it), avoiding
duplication in the stored transcript.

#### Concurrency model
- Model inference is blocking → run in a **thread pool**
  (`asr.inferenceThreads`, default 2).
- Number of model replicas in a pool is independent of sessions; sessions are
  sharded across replicas by `sessionId` hash (`replica = hash(sessionId) % replicaCount`).
- A session's chunks MUST be consumed by the **same replica** (replicas are
  keyed consumers) to preserve model state/order. The shipped config pins
  `replicaCount: 1` (see §12, streaming-performance note).

#### Performance characteristics (measured, 8-core Apple silicon, int8)
- Each `transcribe()` call carries ~3.5–4 s of **fixed overhead** on this
  hardware, plus cost proportional to the buffered audio length.
- Naive per-chunk re-transcription made a 28 s session cost 15 full decodes
  (~85 s wall ≈ 3× realtime) and kept draining after the stream ended.
- Incremental continuation decodes only the unconfirmed tail (~7.5 s peak for
  a 28 s session, measured max `_pending` 119 360 samples) seeded with the
  confirmed prefix: ~7 decodes / ~30 s wall for the same session. Because each
  decode's fixed overhead dominates and the tail is short, `beam_size=5` is
  affordable for every streaming decode and keeps confirmed segments accurate.
- Confirmed words are immutable: once emitted as a `final_transcript` they are
  never re-decoded, so a later decode can neither drop them nor repeat them.

#### Out-of-order safety
- Never reorder: `asr` writes `seqNo` from input to output unchanged.
- If a chunk is lost (broker delivery gap), `asr` emits `final_transcript` with `partial: false` and marks `gap=true`, then continues.

### 4.3 `mt` (worker)

#### Input
Consumes `asr.out.<sessionId>`.

#### Processing
1. Translate each `partial_transcript`/`final_transcript` text via offline MT (CTranslate2 OPUS-MT / NLLB) `sourceLanguage`→`targetLanguage`.
2. Preserve `seqNo`, `startMs`, `endMs`, and pass `sessionEnd` through unchanged (TTS relies on it to end the streaming session exactly once).
3. Publish to `mt.out.<sessionId>`.

#### Concurrency model
- Stateless → **no session affinity**. Any replica can process any segment.
- Parallel batch translation: collect up to `mt.batchSize` (default 16) segments and translate in one inference call for throughput.
- Thread pool: `mt.inferenceThreads` (default vCPUs − 1).

#### Ordering
- MT is unordered-safe because `seqNo` is carried through; downstream reorders.

### 4.4 `tts` (worker)

#### Input
Consumes `mt.out.<sessionId>`.

#### Processing
1. For `final_transcript` (and optionally partial when `tts.onPartial` = true), synthesize target-language audio via offline TTS (Piper, ONNX).
2. Emit `audio_output` with `data` (WAV **hex**), `seqNo`, `startMs`, `endMs`.
3. Publish to `output_events.<sessionId>` (the `tts_out` stream is reserved, not used — see §3.1). For streaming sessions (`jobId == null`), TTS emits the session `end` (reason `complete`) only when a `final_transcript` carries `sessionEnd: true` — earlier per-segment finals must NOT end the session. (Batch jobs keep `job_done`/`job_failed` semantics; `end` is not emitted for them.)

#### Concurrency model
- Like `asr`: **thread-pool bound**, no session affinity needed if synthesis is stateless (piper is); if stateful TTS chosen, apply same replica affinity as ASR.
- `tts.inferenceThreads` (default vCPUs − 1).

### 4.5 `forwarder`

#### Input
Consumes `output.events.<sessionId>`.

#### Processing
1. Reorders events by `seqNo` per session using an in-memory ordered buffer (window `forwarder.reorderWindow`, default 32).
2. Maintains an outbound WS connection per downstream SDK URL (`socket.url`).
3. Rebroadcasts each event verbatim (JSON) to the downstream socket in `seqNo` order.
4. On socket drop: buffer up to `forwarder.bufferSize` (default 10_000 events) and reconnect with backoff `socket.reconnectDelayMs` (exponential, capped at `socket.maxReconnectDelayMs`); drop-and-log when buffer full and log a warning.

#### Message format sent downstream (same as §3.2 envelope).

#### Concurrency model
- Async; one consumer goroutine/task per session; a shared ordered-dispatch loop for the outbound socket per downstream URL (single writer → preserves order).
- Per-session reorder buffers are memory-bounded (LRU eviction of idle sessions after `forwarder.sessionIdleSeconds`, default 300).

### 4.6 `gateway`

#### Endpoints
- `GET /api/v1/jobs/{jobId}` — job status: `queued | processing | done | failed` + result artifact path + transcript.
- `POST /api/v1/jobs/{jobId}/cancel` — cancel batch job.
- `GET /api/v1/config` — effective resolved config (for debugging).
- `GET /api/v1/metrics` — Prometheus metrics.

#### Batch job lifecycle (broker `jobs` stream)
```
queued → processing (asr → mt → tts) → done (artifact URL + transcript)
                                   └→ failed (error code + message, retryable flag)
```
- Job TTL default 24 h; artifacts cleaned by TTL sweeper.
- `gateway` writes result row and publishes `output.events.<jobId>` for downstream notification.

## 5. Storage Schema (gateway DB)

SQLite (single node) or PostgreSQL (multi-node). Table:

```sql
CREATE TABLE sessions (
  session_id   TEXT PRIMARY KEY,
  job_id       TEXT,
  source_lang  TEXT NOT NULL,
  target_lang  TEXT NOT NULL,
  status       TEXT NOT NULL DEFAULT 'streaming', -- streaming|completed|failed
  created_at   TIMESTAMP NOT NULL,
  updated_at   TIMESTAMP NOT NULL
);

CREATE TABLE events (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id   TEXT NOT NULL REFERENCES sessions(session_id),
  seq_no       INTEGER NOT NULL,
  type         TEXT NOT NULL,
  payload      TEXT NOT NULL,           -- JSON
  UNIQUE(session_id, seq_no)
);

CREATE TABLE jobs (
  job_id       TEXT PRIMARY KEY,
  status       TEXT NOT NULL,
  input_path   TEXT,
  output_path  TEXT,
  transcript   TEXT,
  error_code   TEXT,
  error_message TEXT,
  created_at   TIMESTAMP NOT NULL,
  updated_at   TIMESTAMP NOT NULL
);
```

## 6. Ordering & Delivery Guarantees

| Guarantee              | Mechanism                                                    |
|------------------------|--------------------------------------------------------------|
| At-least-once delivery| Broker dedup `messageId`; consumers idempotent on `seqNo`    |
| Per-session ordering   | Subject sharding `.<sessionId>` + replica affinity in ASR/TTS |
| Reordering fix         | `forwarder` reorder window by `seqNo`                        |
| No silent loss         | `gap=true` flag emitted when a seqNo gap is detected         |
| Backpressure           | ingest publisher waits for broker ack; forwarder bounded buffer |

## 7. Concurrency Configuration (per service, all offline)

| Key                            | Default | Range / Notes                          |
|--------------------------------|---------|----------------------------------------|
| `ingest.maxSessions`           | 1000    | 1–10 000 concurrent WS sessions/instance |
| `ingest.chunkDurationMs`       | 300     | 200–500 ms                             |
| `asr.inferenceThreads`         | 2       | >= 1; thread pool for model inference  |
| `asr.replicaCount`             | 1       | >= 1; sessions sharded by hash         |
| `mt.inferenceThreads`          | 2       | >= 1                                  |
| `mt.batchSize`                 | 16      | 1–128 segments per inference call (future) |
| `tts.inferenceThreads`         | 2       | >= 1                                  |
| `tts.onPartial`                | false   | synthesize partial transcripts too     |
| `forwarder.reorderWindow`      | 32      | 1–1024 events buffered for reorder     |
| `forwarder.reorderTimeoutMs`   | 500     | flush window when no higher seq arrives |
| `forwarder.bufferSize`         | 10 000  | outbound events before dropping        |
| `forwarder.sessionIdleSeconds` | 300     | idle reorder-buffer eviction           |
| `broker.maxRedeliveries`       | 3       | redeliver then DLQ                     |

> Defaults reflect the shipped `services/*/config.yaml`. `mt.batchSize` is
> configured but the MT worker currently translates one segment per call.

## 8. Error Handling

| Condition                         | Behavior                                                            |
|-----------------------------------|---------------------------------------------------------------------|
| Invalid file format / language    | `400` with error code (`INVALID_FILE_FORMAT`, `UNSUPPORTED_LANGUAGE`) |
| File not found                    | `404` (`FILE_NOT_FOUND`)                                           |
| Broker unreachable                | `ingest` returns `503` (`BROKER_UNAVAILABLE`); readiness fails       |
| Model load failure                | `500` (`MODEL_LOAD_FAILED`); service refuses to start               |
| Chunk decode failure              | `error` frame `INVALID_AUDIO_CHUNK`; session paused                 |
| Outbound socket down              | forwarder retries w/ backoff; buffered events                        |
| Segment gap detected              | `final_transcript` with `gap=true`                                  |
| Worker redelivery exhausted       | route to `output.events.<sessionId>.dlq`; log + metrics              |

## 9. Sequence Diagram — Streaming Session (happy path)

```
Client        ingest        broker        asr        mt        tts        forwarder   SDK
  │ POST /stream │             │           │          │          │            │          │
  │(session meta)│             │           │          │          │            │          │
  │◀─── ack ─────┤             │           │          │          │            │          │
  │ audio_chunk  │             │           │          │          │            │          │
  │ seqNo=1 ─────▶ pub audio.in.1 │        │          │          │            │          │
  │              │             │────────▶  ASR(1)    │          │            │          │
  │              │             │          │ pub asr.out.1          │            │          │
  │              │             │          │────────▶  MT(1)       │            │          │
  │              │             │          │          │ pub mt.out.1           │          │
  │              │             │          │          │────────▶ TTS(1)       │          │
  │              │             │          │          │          │ pub tts.out.1           │
  │              │             │          │          │          │──────────▶ FWD reorder    │
  │◀─ partial_transcript ──────┼──────────┼──────────┼──────────┼───────────────────────────▶│
  │◀─ audio_chunk ─────────────┼──────────┼──────────┼──────────┼───────────────────────────▶│
  │ close                       │          │          │          │            │          │
  │◀── end ────────────────────┤          │          │          │            │          │
```

## 10. Deployment (Offline `docker-compose`)

```yaml
services:
  broker:
    image: nats:alpine            # vendored locally
    command: ["-js", "-m", "8222"]
    ports: ["4222:4222"]
  ingest:
    build: ./services/ingest
    ports: ["50010:50010"]
    depends_on: [broker]
    volumes: ["./models:/models:ro"]
  asr:    { build: ./services/asr,    depends_on: [broker], volumes: ["./models:/models:ro"] }
  mt:     { build: ./services/mt,     depends_on: [broker], volumes: ["./models:/models:ro"] }
  tts:    { build: ./services/tts,    depends_on: [broker], volumes: ["./models:/models:ro"] }
  forwarder: { build: ./services/forwarder, depends_on: [broker] }
  gateway: { build: ./services/gateway, ports: ["51000:51000"], depends_on: [broker] }
```

- Scale hot stages: `docker compose up --scale asr=4 --scale mt=8`.
- Air-gap: `docker build` on internet host; push images to a local registry; runtime hosts pull only from that registry.

## 11. Testing Strategy

| Level     | Scope                                                                 |
|-----------|-----------------------------------------------------------------------|
| Unit      | chunker, seqNo ordering, reorder buffer, config validation (ranges)   |
| Contract  | REST + WS schema fixtures against each service                        |
| Integration | Broker up; drive chunk → final_transcript across asr→mt→tts→forwarder |
| E2E       | Batch + streaming over full compose stack; assert downstream SDK receives ordered events |
| Load      | k6/`websocat` floods WS sessions; verify backpressure, no event loss  |
| Offline   | boot compose with network namespace `--network none`; assert zero internet calls |

## 12. Implementation Notes & Measured Findings

Findings from building and benchmarking the real backend (vs. the mock
default); these refine the design above.

### 12.1 Model backends & mock mode
- Every model stage is a pluggable backend selected by `STTS_MODEL_BACKEND`
  per service: `mock | whisper | nllb | piper` (`common/stts_core/models.py`).
  The default is **`mock`** (deterministic fake transcripts, beep TTS) so the
  full stack runs with zero downloads; set `STTS_MODEL_REAL=1` /
  `make local-real` to switch all services to real backends.
- Real model set (~2.3 GB, `make models`): faster-whisper **large-v3-turbo**
  int8 (1.5 GB), NLLB-200 distilled **600M** int8 (0.6 GB), Piper voices for
  **English / Hindi / Malayalam** (`en_US-lessac`, `hi_IN-pratham`,
  `ml_IN-arjun`). Other Piper voices (Tamil, Telugu, Gujarati, ...) do not
  exist on `rhasspy/piper-voices@v1.0.0`.
- HF access: `Systran/faster-whisper-*` repos return **401 (gated)**; the
  pipeline uses the `mobiuslabsgmbh/faster-whisper-large-v3-turbo` mirror.

### 12.2 Language handling
- `sourceLanguage` is threaded from the caller (WS query param, batch field,
  demo preset) all the way to `WhisperModel.transcribe(language=...)`. Whisper
  only falls back to auto-detection when absent — which measuredly mislabels
  Malayalam as **Tamil** (`ta`, ~0.4 probability), so callers should always
  pass the known language.

### 12.3 Streaming decode cost (the main performance finding)
- On the reference 8-core Apple silicon laptop (CPU, int8), each whisper
  `transcribe()` call has ~3.5–4 s **fixed overhead** plus cost proportional
  to the buffered audio; `vad_filter` and beam 1→5 contribute little at short
  lengths. `cpu_threads` beyond the default gains nothing (all cores already
  used).
- The original "re-transcribe whole buffer every 2 s" design made a 28 s
  session run **15 decodes ≈ 85 s wall (~3× realtime)**, and the worker kept
  draining a catch-up backlog after the final chunk.
- Fixes (see §4.2): 4 s recheck cadence, never start a partial while a decode
  is busy, greedy (beam 1) partials with beam-5 final, plus a **Silero VAD
  silence gate** (`faster_whisper.vad.get_speech_timestamps`) so silence emits
  no partials. Result: **8 decodes ≈ 35 s wall (~1.25× realtime)** for the
  same session; completion after Stop ≈ final beam-5 decode + MT + TTS
  (~15–20 s).
- Implication for §5 scaling: with fixed per-decode overhead this high, ASR is
  the throughput bottleneck and the **keyed-consumer `replicaCount` machinery
  is not yet wired** — the shipped config pins `asr.replicaCount: 1`. True
  real-time (≤1× realtime) on CPU would need incremental/streaming decoding
  rather than full-buffer re-transcription, or a GPU/`compute_type` bump.

### 12.4 Whisper robustness settings
- Observed "word word word" repetition loops → fixed with
  `no_repeat_ngram_size=3` and `condition_on_previous_text=False`.

### 12.5 Synthetic speech caveat
- Piper's synthetic Malayalam (`ml_IN-arjun`) is out-of-distribution for
  Whisper: batch translation of the `ms-*` demo presets is unreliable, while
  the English (`en-*`) presets are 5/5. Malayalam/Gujarati content should be
  validated against **real human recordings**; Gujarati has no Piper voice at
  all (transcript-only corpus).

### 12.6 Medical-domain context (Indian languages)
- Goal: preserve the English medical terms, drug names, and numbers that are
  spoken inside the Indian-language audio (the corpus is heavily code-switched).
  Measured with `scripts/eval_asr.py` (each WAV decoded once plain and once with
  the medical context; glossary = English medical terms present in the source
  transcript).
- English audio was already clean (12/12 glossary terms kept either way; mean
  WER -0.017 with context). The gain is on the Indian-language audio: baseline
  kept **0/27** glossary terms across the five `ml` files, with the context
  **5/27** (`BP`, `gastritis`, `medicine`, `muscle strain`, `prescription`).
- Prompt style: native script with the English terms embedded (e.g. Malayalam
  prompt contains "high fever", "headache", "blood test", "102" inside the
  native sentence) to mirror how patients actually speak. Numbers are steered by
  the prompt and the unit hotwords, not listed as hotword tokens (ineffective).
- Anti-hallucination tuning is deliberately **conservative**: only
  `hallucination_silence_threshold=0.7` is set (drops decoded segments that
  appear over pure silence); the confidence thresholds stay at faster-whisper
  defaults so low-confidence Indian-language words are not dropped.
- Hotwords sit inside whisper's 224-token context window after `<|sot_prev|>`
  and the library truncates them to half the window; the per-language lists are
  short enough to avoid evicting the prompt.
- Synthetic-speech caveat still dominates: Piper Malayalam is OOD for Whisper,
  so most of the `ml` transcript remains garbled regardless of context. Expect
  the context to matter on real human recordings (see §12.5).

### 12.7 Streaming end-of-session protocol
- The client sends the final chunk with `isFinal: true` but keeps the socket
  open; TTS emits `end` (reason `complete`) after the trailing events, and
  ingest emits `end` (reason `client_closed`) if the client drops. The demo
  stops the mic on the Stop button, so no post-final chunks are sent (earlier
  those created a fresh ASR session buffer and kept producing partials).
