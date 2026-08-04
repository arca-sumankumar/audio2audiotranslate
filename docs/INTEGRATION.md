# STTS Integration Guide

This guide shows how to call the service programmatically, in both modes:

| Mode | Transport | Endpoint | Use case |
|------|-----------|----------|----------|
| **Batch** | REST | `POST http://<host>:50010/api/v1/translate` | Translate an existing audio file on the shared volume |
| **Batch status** | REST | `GET http://<host>:51000/api/v1/jobs/{jobId}` | Poll a batch job (also `POST .../cancel`) |
| **Streaming** | WebSocket | `ws://<host>:50010/api/v1/stream?sourceLanguage=&targetLanguage=&format=` | Real-time, low-latency translation |
| **Downstream SDK** | WebSocket | `ws://<forwarder host>:<port>` | Your own server receives all events |
| **Demo UI** | HTTP | `http://<host>:50060` | Browser demo (upload + live mic) |
| **Test corpus presets** | REST | `http://<host>:50060/api/test_audio` | List demo presets; submit them as batch jobs (§1.4) |

Language codes: `en`, `bn`, `gu`, `hi`, `kn`, `ml`, `mr`, `pa`, `ta`, `te`,
`ur` (English + Indian languages: Bengali, Gujarati, Hindi, Kannada,
Malayalam, Marathi, Punjabi, Tamil, Telugu, Urdu). Audio formats: `wav` (any
rate, mono or stereo, 8/16/24/32-bit — automatically normalized to 16 kHz mono
16-bit PCM), `mp3` (declared but not decoded in this build).

---

## 1. Batch translation (REST)

Batch jobs translate a file that already lives on the **shared `/data` volume**
(mounted into `ingest`, `gateway` and `demo`). There is no file-upload endpoint on
ingest itself; either place the file in `/data` directly, or use the demo
service's upload proxy (section 1.3).

### 1.1 curl

```bash
curl -X POST http://localhost:50010/api/v1/translate \
  -H 'Content-Type: application/json' \
  -d '{
    "filePath": "/data/audio/speech.wav",
    "fileFormat": "wav",
    "sourceLanguage": "en",
    "targetLanguage": "hi",
    "model": "nllb"
  }'
```

`model` is optional (`nllb` | `bergamot` | `indictrans2`; empty string = the
worker's configured default) and switches the MT model for that job only. See
[MODELS.md](MODELS.md) for the model catalogue.

Response:

```json
{ "jobId": "3f0d..." }
```

Poll the gateway until `status` is `done` or `failed`:

```bash
curl http://localhost:51000/api/v1/jobs/3f0d...
```

```json
{
  "jobId": "3f0d...",
  "status": "done",
  "inputPath": "/data/audio/speech.wav",
  "outputPath": "/data/output/3f0d....wav",
  "transcript": "[hi] mock-asr [en] segment 1 #7521 ...",
  "error": null
}
```

The translated audio is written to `outputPath` on the shared volume.

### 1.2 Python (requests)

```python
import requests, time

INGEST = "http://localhost:50010"
GATEWAY = "http://localhost:51000"

r = requests.post(f"{INGEST}/api/v1/translate", json={
    "filePath": "/data/audio/speech.wav",
    "fileFormat": "wav",
    "sourceLanguage": "en",
    "targetLanguage": "hi",
}, timeout=30)
r.raise_for_status()
job_id = r.json()["jobId"]

while True:
    job = requests.get(f"{GATEWAY}/api/v1/jobs/{job_id}", timeout=10).json()
    if job["status"] in ("done", "failed"):
        break
    time.sleep(0.5)

if job["status"] == "done":
    print("transcript:", job["transcript"])
    print("audio at :", job["outputPath"])
else:
    raise RuntimeError(job["error"])
```

### 1.3 Upload via the demo service (multipart)

The demo service accepts a browser-style multipart upload, stores it in the
shared volume and submits the job for you:

```python
import requests

r = requests.post("http://localhost:50060/api/translate",
                  data={"sourceLanguage": "en", "targetLanguage": "hi"},
                  files={"file": open("speech.wav", "rb")}, timeout=30)
r.raise_for_status()
job_id = r.json()["jobId"]
```

### 1.4 Test-corpus presets (demo service)

The demo serves the `data/test_audio/` corpus (English + Malayalam WAVs and
their transcripts; see the README) with small REST helpers:

- `GET http://localhost:50060/api/test_audio` — lists the presets as
  `{"name", "sourceLanguage", "symptom", "label", "hasTranscript"}`; `label`
  is what the demo dropdown shows (`en-fever`, `ms-cough`, ...).
- `GET http://localhost:50060/api/test_audio/<name>/audio` — the WAV bytes.
- `GET http://localhost:50060/api/test_audio/<name>/transcript` — the source
  transcript text.
- `POST http://localhost:50060/api/translate` with `preset=<name>` instead of
  a file upload — the source language is auto-derived from the name
  (`06_ml_fever` → `ml`), and `targetLanguage` defaults to `hi`.

```python
import requests

presets = requests.get("http://localhost:50060/api/test_audio").json()["files"]
print([p["label"] for p in presets])
# ['en-fever', 'en-cough', 'en-headache', 'en-stomach', 'en-backpain',
#  'ms-fever', 'ms-cough', 'ms-headache', 'ms-stomach', 'ms-jointpain']

r = requests.post("http://localhost:50060/api/translate",
                  data={"preset": "06_ml_fever"}, timeout=30)   # ml -> hi
job_id = r.json()["jobId"]
```

---

## 2. Streaming translation (WebSocket)

Connect to the ingest WS endpoint, then push audio as base64 WAV chunks.
Example URL: `ws://localhost:50010/api/v1/stream?sourceLanguage=en&targetLanguage=hi&format=wav&model=nllb`
(`model` is optional, as in batch). A complete working client is
`scripts/smoke_test.py`.

- **Request frames**: `{"type": "audio_chunk", "seqNo": N, "data": "<base64 wav>", "isFinal": false}`
- **Response frames** (envelopes): `session_started`, `ack`, `partial_transcript`,
  `final_transcript`, `audio_output`, `end`, `error`.
- Chunks should be ~300 ms of 16 kHz 16-bit mono PCM WAV. The last chunk must be
  sent with `"isFinal": true` so the pipeline flushes and emits `end`.
- A `session_started` envelope arrives before the first `ack` — don't treat it as an ack.
- `sourceLanguage` **must match the spoken language**: it seeds Whisper. When
  absent, Whisper auto-detects, which can mislabel Malayalam as Tamil, so pass
  it explicitly whenever you know the language.
- Supplying a known `sourceLanguage` also activates the medical-domain ASR
  context (per-language `initial_prompt` + hotwords + a conservative
  hallucination-silence guard) that helps keep English medical terms spoken in
  Indian-language audio. Overrides: `STTS_ASR_INITIAL_PROMPT`,
  `STTS_ASR_HOTWORDS`, `STTS_ASR_HALLUCINATION_SILENCE_THRESHOLD`
  (see `common/stts_core/medical.py`).
- Live `partial_transcript` frames are provisional: emitted roughly every ~4 s
  of **new** audio, and silenced by a Silero VAD gate while the speaker is
  quiet. Use the `final_transcript` (`payload.isFinal: true`) for anything that
  needs accuracy.
- **There are multiple `final_transcript` frames per session** — one per
  confirmed segment (ASR emits a final for each segment that is older than the
  ~3 s keep-back window, then a `sessionEnd`-marked final on the last chunk).
  A streaming session produces a sequence like:
  `final_transcript(stage=asr)`, `final_transcript(stage=mt)`,
  `final_transcript(stage=asr, sessionEnd=true)`, `final_transcript(stage=mt, sessionEnd=true)`,
  then `end`. The `end` frame arrives **after** the sessionEnd finals; do not
  treat an earlier final as the end of the stream.
- Confirmed words are immutable — once emitted as a `final_transcript` they
  are never re-decoded, so later finals can't drop or repeat them. The
  `sessionEnd: true` flag is what tells the pipeline to close the session
  (TTS emits `end` exactly once).

### 2.1 Python (websockets)

```python
import asyncio, base64, json, struct, wave, io

import websockets

RATE, CHUNK_MS = 16000, 300

def wav_bytes(pcm_ints):
    bio = io.BytesIO()
    with wave.open(bio, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE)
        w.writeframes(struct.pack(f"<{len(pcm_ints)}h", *pcm_ints))
    return bio.getvalue()

async def translate_stream(chunks_pcm):
    url = ("ws://localhost:50010/api/v1/stream"
           "?sourceLanguage=en&targetLanguage=hi&format=wav")
    async with websockets.connect(url) as ws:
        seq = 0
        for i, pcm in enumerate(chunks_pcm):
            seq += 1
            is_final = i == len(chunks_pcm) - 1
            await ws.send(json.dumps({
                "type": "audio_chunk", "seqNo": seq,
                "data": base64.b64encode(wav_bytes(pcm)).decode(),
                "isFinal": is_final,
            }))
        while True:
            msg = json.loads(await ws.recv())
            if msg["type"] == "final_transcript" and msg["payload"].get("stage") == "mt":
                print("  ", msg["payload"]["text"])
            if msg["type"] == "audio_output":
                print("translated audio chunk received:",
                      len(msg["payload"]["data"]) // 2, "bytes")
            if msg["type"] == "end":
                break

# 2 s of 220 Hz tone split into 300 ms chunks
tone = [int(9000 * 0.5 * (1 + (i // (RATE // 220)) % 2 * -0.4)) for i in range(RATE * 2)]
chunks = [tone[i:i + RATE * CHUNK_MS // 1000]
          for i in range(0, len(tone), RATE * CHUNK_MS // 1000)]
asyncio.run(translate_stream(chunks))
```

### 2.2 Browser (this repo's demo UI)

`services/demo/static/index.html` is a self-contained example: it captures the
microphone with the Web Audio API, downsamples to 16 kHz, encodes WAV chunks in
JS, and streams them over a `WebSocket` while rendering live transcripts and
playing back the `audio_output` frames. Open `http://localhost:50060`.

---

## 3. Downstream SDK (receive events on your own WebSocket server)

The **forwarder** service can push every event envelope to a downstream WebSocket
endpoint of yours. Point the forwarder at your server, then it becomes a client
that replays `output_events` (transcripts, audio, lifecycle) in sequence order.

### 3.1 Configure the forwarder

Set these env vars (or edit `services/forwarder/config.yaml`):

```yaml
socket:
  enabled: true
  url: "ws://host.docker.internal:9000/stts-events"
```

```bash
docker compose up -d
# or on a running stack:
docker compose -f deploy/docker-compose.yml run --rm -e STTS_SOCKET_URL=ws://... forwarder
```

### 3.2 Example receiver (Python, `aiohttp` or `websockets`)

```python
import asyncio, json
from aiohttp import web

async def handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    async for msg in ws:
        if msg.type == web.WSMsgType.TEXT:
            ev = json.loads(msg.data)
            if ev["type"] == "final_transcript":
                print(f"[{ev['sessionId'][:8]}] {ev['payload']['text']}")
            if ev["type"] == "audio_output":
                print(f"[{ev['sessionId'][:8]}] audio {len(ev['payload']['data'])//2} bytes")
            if ev["type"] == "end":
                print(f"[{ev['sessionId'][:8]}] session ended")
    return ws

app = web.Application()
app.router.add_get("/stts-events", handler)
web.run_app(app, port=9000)
```

---

## 4. Event envelope reference

Every event is a JSON envelope (`schemaVersion: "1.0"`):

```json
{
  "schemaVersion": "1.0",
  "messageId": "uuid (dedup id)",
  "sessionId": "uuid",
  "jobId": null,
  "seqNo": 3,
  "type": "final_transcript",
  "sourceLanguage": "en",
  "targetLanguage": "hi",
  "payload": { "...": "type-specific" },
  "timestamp": "ISO-8601 UTC"
}
```

| type | payload | emitted by |
|------|---------|-----------|
| `audio_in` | `format, sampleRate, durationMs, isFinal, data` (hex PCM) | ingest |
| `partial_transcript` / `final_transcript` | `stage` (`asr`=source, `mt`=translated), `text`, `startMs`, `endMs`, `isFinal`, `isLast` | asr, mt |
| `audio_output` | `format, startMs, endMs, isFinal, isLast, text, data` (hex WAV) | tts |
| `job_started` / `job_done` / `job_failed` | reason / error info | pipeline |
| `session_started` | `format` | ingest (on WS connect) |
| `end` | `reason` | pipeline (complete) / ingest (client closed) |
| `error` | `code`, `message` | ingest (protocol errors) |

For batch jobs use the aggregated `transcript` from the gateway; for streaming,
filter on `payload.stage == "mt"` to get translated text only (both asr and mt
stages publish transcripts). Every envelope echoes the session's
`sourceLanguage` / `targetLanguage`, so a downstream consumer can filter or
route by language without tracking it separately.

---

## 5. Ports

| Service | Internal | Host | Purpose |
|---------|----------|------|---------|
| broker | 4222 / 8222 | 4222 / 8222 | NATS client / monitor |
| ingest | 50010 | 50010 | REST + WebSocket streaming |
| asr / mt / tts / forwarder | 50020-50050 | - | internal (not published) |
| gateway | 51000 | 51000 | job status REST |
| demo | 50060 | 50060 | demo UI + upload proxy |

All ports are inside the IANA dynamic range (49152-65535).
