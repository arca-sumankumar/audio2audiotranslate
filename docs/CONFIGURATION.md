# Configuration

Service configuration, env-var overrides, and concurrency/tuning notes.

## Configuration

Every service reads `/app/config.yaml` (defaults + documented ranges) with
`STTS_` env overrides, e.g.:

```bash
STTS_NATS_URL=nats://localhost:4222
STTS_AUDIO_CHUNKDURATIONMS=400
STTS_FORWARDER_REORDERWINDOW=64
```

See `common/stts_core/config.py` and each `services/*/config.yaml`.

Model backends and paths:
`STTS_MODEL_BACKEND={mock|whisper|nllb|piper|bergamot|indictrans2}` and
`STTS_MODEL_OFFLINEPATH=<dir>`. The per-session MT model comes from the demo
dropdown (`model` WS/REST field). The medical ASR context is tunable via
`STTS_ASR_INITIAL_PROMPT`, `STTS_ASR_HOTWORDS`,
`STTS_ASR_HALLUCINATION_SILENCE_THRESHOLD` (see [MODELS.md](MODELS.md) and
`common/stts_core/medical.py`).

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
