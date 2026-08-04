# HLD2 — Microservices Pipeline with Message Broker

## 1. Architectural Style

**Async event-driven pipeline** — separate stateless services (ingest, ASR, MT, TTS, forwarder) decoupled by a message broker. Communication is asynchronous over a broker (e.g. NATS, Redis Streams, RabbitMQ), plus an outbound WebSocket fan-out.

## 2. High-Level Diagram

```
 Clients ──REST/WS──▶ [INGEST SERVICE]            ┌──────────────┐
                         │ push audio chunks       │   BROKER     │
                         ▼                         │  (NATS /     │
                 [Broker Topic: audio.in] ──────▶  │   Redis      │
                         │                         │   Streams)   │
                         ▼                         └──────┬───────┘
                 [ASR SERVICE] ◀── consumes audio.in        │
                 [MT SERVICE]  ◀── consumes asr.out ───────▶│ topics:
                 [TTS SERVICE] ◀── consumes mt.out ────────▶│  audio.in
                         │                                  │  asr.out
                         ▼                                  │  mt.out
                 [Broker Topic: output.events]              │  tts.out
                         │                                  │  output.events
                         ▼                                  │
                 [FORWARDER SERVICE] ──WS──▶ Downstream SDK  │
                 [REST Gateway]  ──── restores batch results ┴──▶ DB/File Store
```

## 3. Components

| Component          | Responsibility                                                        | Tech Candidates (all offline-capable)      |
|--------------------|-----------------------------------------------------------------------|---------------------------------------------|
| Ingest Service     | Terminates REST + WS; validates; resamples; publishes chunks to broker| FastAPI + `nats-py`/`redis-py`               |
| ASR Service        | Consumes `audio.in`; incremental transcription; publishes `asr.out`   | faster-whisper / sherpa-onnx (worker)        |
| MT Service         | Consumes `asr.out`; translates segments; publishes `mt.out`           | CTranslate2 OPUS-MT / NLLB worker            |
| TTS Service        | Consumes `mt.out`; synthesizes target audio; publishes `tts.out`      | piper / Coqui TTS worker                     |
| Forwarder Service  | Consumes `output.events`; rebroadcasts to downstream SDK over WS w/ retry | `websockets` client + broker consumer   |
| Broker             | Durable/queued transport between services                             | NATS (JetStream), Redis Streams, RabbitMQ    |
| Config/Discovery   | Per-service config w/ defaults + ranges (PRD §2.4); service registry  | pydantic-settings + env; static config       |
| Object/File Store  | Batch output artifacts (target audio files)                           | local disk or MinIO (self-hosted)            |

> **As built:** NATS JetStream (streams `audio_in`, `asr_out`, `mt_out`,
> `output_events`, `jobs`; see LLD2 §3.1), faster-whisper **large-v3-turbo**
> (ASR), CTranslate2 NLLB-200 distilled **600M** (MT), Piper (TTS), plus a
> pluggable **mock** backend per stage so the stack runs with zero model
> downloads. Measured findings and tuning are recorded in LLD2 §12.

## 4. Data Flow

**Batch**: Ingest validates → publishes full-job message → ASR/MT/TTS consume sequentially via topic chain → final artifact written to store → REST Gateway replies or downstream polls a job status topic.

**Streaming**: Ingest splits input into 200–500 ms chunks → each chunk flows ASR→MT→TTS→Forwarder as independent events → Forwarder holds per-session ordering (sequence numbers) and emits `partial/final/audio_chunk/end` to the downstream socket.

## 5. Concurrency & Scaling

- **Scale per stage independently**: 1 ASR worker on a GPU node, 5 MT workers on CPU nodes, etc.
- **Load shedding**: broker provides buffering; a slow stage backpressures instead of dropping input.
- **Horizontal scale-out**: add workers for any hot stage without touching other services.
- **No shared state** between workers; ordering enforced via per-session keys (e.g. sessionId sharding).

## 6. Deployment (Offline)

- Every service packaged as its own container; broker container included in a `docker-compose` bundle.
- All model weights + ffmpeg vendored in the image layer.
- Air-gapped runtime: compose file is pre-baked; only port 49152–65535 exposed for clients + one internal broker port.

## 7. Pros

- **Horizontal scalability**: each stage scales independently to match load (important for multiple datacenter nodes).
- **Fault tolerance**: workers crash and restart independently; broker buffers messages so work is not lost.
- **Durable queue**: batch jobs survive restarts (configurable retention).
- **Language-pair isolation**: run different ASR/MT models in separate worker pools (memory efficient across many pairs).
- **Loose coupling**: upgrade/replace any stage (e.g. ASR model) with zero downtime for other stages.

## 8. Cons

- **Higher end-to-end latency**: every chunk crosses ASR→MT→TTS over the
  broker — hard to hit true real-time streaming on a single laptop. Measured
  on an 8-core Apple silicon laptop (CPU, int8): a 28 s stream decodes in
  ~35 s wall (≈1.25× realtime) after throttling partials to a 4 s cadence with
  greedy/beam decoding and a VAD silence gate; a naive per-chunk decode was
  ~3× realtime. Reaching ≤1× realtime on CPU needs incremental/streaming
  decoding (LLD2 §12.3).
- **Operational complexity**: 5+ services + broker to deploy, configure, and monitor offline.
- **Ordering complexity**: must shard by session and track sequence numbers to avoid reordering partial transcripts.
- **Resource overhead**: broker + multiple runtimes consume more RAM/CPU than one monolithic process.
- **More failure modes**: network partitions, broker disk, per-service config drift.

## 9. When to Choose HLD2

Choose HLD2 when: expected concurrency is high, deployment is a multi-node datacenter, durability/restart-resilience matters more than sub-second latency, and you need to scale ASR/MT independently.

---

## 10. Decision Matrix (HLD1 vs HLD2)

| Criterion                 | HLD1 (Monolith)                | HLD2 (Pipeline + Broker)         |
|---------------------------|--------------------------------|----------------------------------|
| Latency (streaming)       | Best (in-process)              | Higher (broker hops)             |
| Concurrency ceiling       | Low–moderate (vertical)        | High (horizontal per stage)      |
| Operational complexity    | Low                            | High                             |
| Fault tolerance/durability| Low (restart drops streams)    | High (broker buffers)            |
| Scaling memory for pairs  | All models in one process      | Models isolated per worker pool  |
| Offline install size      | Smallest (1 image)             | Larger (N images + broker)       |
| Best fit                  | Laptop / single node, low load | Multi-node datacenter, high load |
