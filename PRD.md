# Product Requirements Specification (PRD)

## 1. Overview

Build a speech translation service that accepts WAV or MP3 audio in a source language and produces audio/transcript in a target language.

The service MUST support two modes:

1. **Batch mode**: upload/ingest a complete audio file via REST API, get translated result.
2. **Streaming mode**: accept real-time audio chunks (200–500 ms) over a streaming connection, translate incrementally, and emit translated audio + English transcription as a live stream.

All outputs (transcripts and audio) MUST be forwarded to downstream SDKs via a WebSocket connection for further processing.

The service MUST be fully deployable offline (air-gapped) on a laptop or a local datacenter server with no internet access. Only local/offline models are allowed.

## 2. Functional Requirements

### 2.1 REST API — Batch Audio Translation

`POST /translate`

Request body (JSON):

| Field           | Type   | Required | Default | Range / Allowed Values      | Description                                  |
|-----------------|--------|----------|---------|-----------------------------|----------------------------------------------|
| `filePath`      | string | Yes      | —       | valid local file path       | Path to the WAV or MP3 audio file.           |
| `fileFormat`    | string | Yes      | —       | `wav`, `mp3`                | Encoding format of the input audio file.     |
| `sourceLanguage`| string | Yes      | —       | language code (see §5.2)    | Language of the input audio.                 |
| `targetLanguage`| string | Yes      | —       | language code (see §5.2)    | Language to translate into.                  |

Success response `200`:

```json
{
  "transcript": "translated text in target language",
  "audioFile": "/path/to/output/audio.wav",
  "audioFormat": "wav",
  "durationSeconds": 12.5
}
```

Failure response (e.g. `400` invalid input, `404` file not found, `500` model/processing error) MUST include a machine-readable error code and human-readable message:

```json
{ "error": { "code": "INVALID_FILE_FORMAT", "message": "..." } }
```

### 2.2 Streaming Mode — Real-Time Translation

- Client connects via **WebSocket** (`/stream`).
- Client sends audio chunks of 200–500 ms in the input format.
- The service MUST:
  - chunk audio continuously,
  - transcribe incrementally,
  - translate each partial transcript into the target language,
  - stream back English transcription AND translated audio in real time.
- Streaming messages (JSON envelope) MUST use a `type` field so downstream clients can distinguish message kinds:
  - `type: "partial_transcript"` — interim transcript of current chunk,
  - `type: "final_transcript"` — finalized segment,
  - `type: "audio_chunk"` — translated audio bytes (base64-encoded),
  - `type: "error"` — error information,
  - `type: "end"` — stream complete.

### 2.3 Downstream WebSocket Forwarding

- Every transcript event and audio chunk produced by the service MUST be re-broadcast over an outbound WebSocket connection to downstream SDK(s).
- Outbound connection parameters (URL, protocol) MUST be configurable (see §3).
- If the outbound socket is disconnected, the service MUST buffer events and retry the connection (configurable backoff); events SHOULD NOT be silently dropped.

### 2.4 Configuration

All runtime parameters MUST be configurable via a config file (e.g. YAML/JSON/env). Each parameter MUST have:

- a **default value**, and
- a comment documenting its **valid range** and meaning.

Minimal config keys (add more as needed):

| Key                              | Default             | Range / Notes                                    |
|----------------------------------|---------------------|--------------------------------------------------|
| `server.host`                    | `0.0.0.0`           | any valid IP                                      |
| `server.port`                    | `52000`             | IANA dynamic range **49152–65535** (excludes reserved well-known ports 0–1023 and IANA-registered ports 1024–49151; avoids collisions with common services) |
| `audio.sampleRate`               | `16000`             | e.g. 8000–48000 Hz                               |
| `audio.chunkDurationMs`          | `300`               | 200–500 ms per streaming chunk                   |
| `audio.allowedFormats`           | `["wav","mp3"]`     | wav, mp3                                         |
| `audio.outputFormat`             | `wav`               | wav, mp3                                         |
| `model.offlinePath`              | local model dir     | local path, must NOT require internet            |
| `model.supportedSourceLanguages` | e.g. `["en","es"]`  | language codes the offline model supports        |
| `model.supportedTargetLanguages` | e.g. `["en","hi"]`  | language codes the offline model supports        |
| `socket.enabled`                 | `true`              | true/false; enables outbound forwarding           |
| `socket.url`                     | `ws://localhost:52100` | valid WebSocket URL; use a port in the IANA dynamic range 49152–65535 |
| `socket.reconnectDelayMs`        | `1000`              | >= 0 ms                                          |
| `socket.maxRetries`              | `-1`                | -1 = infinite retry, else >= 0                   |

### 2.5 Offline / Air-Gapped Deployment

- The service MUST NOT depend on any cloud API or internet access at runtime.
- Only locally-hosted models (e.g. Whisper variants run locally) are allowed.
- Startup MUST succeed with a pure offline stack (no network call at any point).
- Document the local model artifacts and how to vendor them for offline install (see §6).

## 3. Non-Functional Requirements

- **Latency (streaming)**: end-to-end partial result per chunk MUST be delivered with minimal overhead; design for real-time feel.
- **Concurrency**: service MUST handle concurrent REST requests and multiple streaming connections.
- **Error handling**: input validation errors return `4xx`; internal/processing errors return `5xx`; never crash on malformed input.
- **Graceful shutdown**: in-flight streaming connections must be drained/closed cleanly.

## 4. Deliverables

The repository MUST contain:

1. Runnable service source code implementing §2.
2. A default config file with documented ranges/defaults (§2.4).
3. Documentation:
   - API reference with request/response examples (including streaming),
   - setup & run instructions for an offline/local environment,
   - full example code showing how a client calls batch + streaming endpoints and how a downstream SDK consumes the forwarded WebSocket stream.
4. Test suite covering: batch translation, streaming, config validation, and error cases.

## 5. Glossary / Reference

- **Language codes**: use ISO 639-1 codes (e.g. `en`, `es`, `hi`). Supported set is constrained by the offline model.
- **Chunk**: a fixed-duration slice of raw audio sent during streaming.

## 6. Constraints

- Language: choose the stack best suited for offline audio models + streaming (e.g. Python w/ FastAPI + WebSockets is acceptable; justify if another stack is chosen).
- Any external model/audio library used MUST be vendored or pre-installable without internet at deploy time.

