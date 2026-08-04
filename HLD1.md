# HLD1 — Monolithic Single-Service Architecture

## 1. Architectural Style

**Monolithic service** — one deployable process exposing REST, WebSocket, and outbound socket forwarding, with offline ASR + MT models loaded **in-process**.

## 2. High-Level Diagram

```
                       ┌─────────────────────────────────────-───────--─┐
                       │              TRANSLATION SERVICE               │
                       │                                                │
 Client ──REST──▶  ┌───▼───----─┐   ┌─────────--──┐   ┌───────────────┐ │
 (batch file)      │  HTTP      │─-▶│  Ingestion  │──▶│  ASR Engine   │ │
                   │ Server     │   │  (validate. │   │ (offline,     │ │
 Client ──WS───▶   │ (FastAPI.  │   │  /decouple).│   │  in-process)  │ │
 (audio chunks)    │  + uvicorn)|   └───────────--┘   └──────┬────────┘ │
                   └───▲───----─┘                            │          │
                       │          ┌───────────┐    ┌──────-──▼──────┐   │
                       │          │ Outbound  │◀───│  MT Engine     │   │
                       │          │ WebSocket │    │ (offline,      │   │
                       │          │ Forwarder │    │  in-process)   │   │
                       │          └───────────┘    └────────────────┘   │
                       │                 │                              │
                       └───────┬─────────┘                              │
                               ▼                                        │
                        Downstream SDK(s)  /  output artifacts (files)  │
                        └───────────────────────────────────────--──────┘
```

## 3. Components

| Component             | Responsibility                                                       | Tech Candidates (all offline-capable)          |
|-----------------------|----------------------------------------------------------------------|------------------------------------------------|
| HTTP/REST Server      | `POST /translate`, validation, auth (optional), error mapping        | FastAPI + uvicorn (or Flask + gunicorn)         |
| WebSocket Server      | `/stream` session lifecycle, chunk receive, partial/final emit       | Starlette WebSocket / `websockets` lib          |
| Audio Ingestion       | Format detection, resample to model sample rate, buffering to chunk  | pydub / soundfile / ffmpeg (vendored binary)    |
| ASR Engine            | Speech→text, incremental (streaming) transcription                   | faster-whisper (CTranslate2), Vosk, sherpa-onnx |
| MT Engine             | Source→target text translation, incremental on partial segments      | CTranslate2 OPUS-MT, NLLB-200 (local), Argos    |
| Audio Synthesis (TTS) | Target-language text→audio chunks (for `audio_chunk` messages)       | piper (ONNX), Coqui TTS (offline)               |
| Outbound Forwarder    | Re-broadcast transcript/audio events to downstream SDK over WS       | `websockets` client with retry/backoff          |
| Config Store          | YAML config w/ defaults + ranges (see PRD §2.4)                      | pydantic-settings + YAML                        |

## 4. Data Flow

**Batch (`POST /translate`)**:

1. Validate `filePath`, `fileFormat`, languages → `400` on failure.
2. Load file, resample to config `audio.sampleRate`.
3. Run ASR → source transcript.
4. Run MT → target transcript.
5. Run TTS → target audio file (config `audio.outputFormat`).
6. Return JSON response; publish events to outbound socket (if enabled).

**Streaming (`/stream`)**:

1. Client connects, sends `sourceLanguage`/`targetLanguage` metadata.
2. Each 200–500 ms audio chunk (config `audio.chunkDurationMs`) is decoded + resampled.
3. ASR emits partial transcript → MT translates → server sends `partial_transcript` (and optionally `audio_chunk` from TTS).
4. Segment finalization → `final_transcript` + `audio_chunk`.
5. All events forwarded to downstream socket in parallel.
6. Client close → `end` message, session teardown.

## 5. Concurrency & Scaling

- One process; uvicorn workers (e.g. 2–4) for HTTP; WS handled via async event loop.
- Model inference runs in a dedicated thread pool / process pool so the event loop never blocks.
- In-process model loading shared across requests (single copy per worker).
- **Scaling is vertical**: one worker per model instance.

## 6. Deployment (Offline)

- Single container or venv bundle; all model weights + ffmpeg vendored into the image.
- `docker build` performed on an internet-connected host; runtime container is air-gapped.
- Single entrypoint `uvicorn app.main:app`; config via YAML/env.

## 7. Pros

- **Simplest to build and debug**: one process, one codebase, one deploy unit.
- **Lowest latency**: no IPC/network hops between ASR→MT→TTS; ideal for the 200–500 ms real-time requirement.
- **Cheap** for laptop / single datacenter node; no broker or extra services to install offline.
- **Model weights loaded once**, shared across all sessions (efficient memory).
- Easy end-to-end testing and offline verification.

## 8. Cons

- **Scaling ceiling**: model inference is CPU/GPU-heavy; horizontal scale requires spawning full copies (wasted duplicated ASR/MT instances).
- **Single point of failure**: crash or restart drops all in-flight WS streams.
- **No backpressure/durable queue**: bursts of input can stall the event loop unless pooling is carefully done; events not persisted.
- **Language pairs multiply model memory**: all models must fit in one process (can exceed laptop RAM with many pairs).
- **Tight coupling**: an ASR upgrade means redeploying the whole service.

## 9. When to Choose HLD1

Choose HLD1 when: target is a single offline laptop/server, expected concurrency is low–moderate, latency is the top priority, and operational simplicity beats elasticity.
