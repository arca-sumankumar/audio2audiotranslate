"""ASR worker: consumes audio_in, produces incremental transcripts.

Consumes  ``audio_in.<sessionId>`` and publishes:
- ``asr_out.<sessionId>``  -> for the MT stage
- ``output_events.<sessionId>`` -> partial/final transcripts for the forwarder
"""
from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from pydantic import BaseModel

from stts_core.audio import AudioChunk
from stts_core.config import BaseConfig
from stts_core.envelope import Envelope
from stts_core.models import ASRBackend, make_asr
from stts_core.nats import NatsClient

log = logging.getLogger("stts.asr")


class AsrSettings(BaseModel):
    inferenceThreads: int = 2   # >= 1
    replicaCount: int = 1       # >= 1
    maxRedeliveries: int = 3    # >= 1


class AsrConfig(BaseConfig):
    asr: AsrSettings = AsrSettings()


class AsrWorker:
    def __init__(self, cfg: AsrConfig):
        self.cfg = cfg
        self.backend: ASRBackend = make_asr(cfg.model)
        self.executor = ThreadPoolExecutor(
            max_workers=cfg.asr.inferenceThreads,
            thread_name_prefix="asr-infer",
        )
        self.nats = None

    async def handle(self, env: Envelope, msg) -> None:
        payload = env.payload
        chunk = AudioChunk(
            seq_no=env.seqNo,
            data=bytes.fromhex(payload.get("data", "")),
            format=payload.get("format", "wav"),
            sample_rate=payload.get("sampleRate", self.cfg.audio.sampleRate),
            duration_ms=payload.get("durationMs", self.cfg.audio.chunkDurationMs),
            is_final=payload.get("isFinal", False),
        )
        loop = asyncio.get_running_loop()
        results = await loop.run_in_executor(
            self.executor, self.backend.transcribe,
            env.sessionId, chunk, env.jobId is not None, env.sourceLanguage)

        # A single chunk can yield several events: a confirmed `final` segment
        # plus the live `partial` tail (incremental continuation). The final
        # segment of the session is marked `sessionEnd` so TTS emits `end`
        # exactly once, after voicing the last segment.
        session_end = bool(payload.get("isFinal"))
        for result in results:
            out_payload = {
                "stage": "asr",
                "text": result.text,
                "startMs": result.start_ms,
                "endMs": result.end_ms,
                "isFinal": result.is_final,
                "sessionEnd": session_end and result.is_final,
                "isLast": payload.get("isLast", False),
                "gap": result.gap,
                "model": payload.get("model", ""),
            }
            out = Envelope(
                sessionId=env.sessionId,
                jobId=env.jobId,
                seqNo=env.seqNo,
                type=("final_transcript" if result.is_final else "partial_transcript"),
                sourceLanguage=env.sourceLanguage,
                targetLanguage=env.targetLanguage,
                payload=out_payload,
            )
            await self.nats.publish(f"asr_out.{env.sessionId}", out)
            await self.nats.publish(f"output_events.{env.sessionId}", out)
            log.debug("asr %s seq=%d final=%s", env.sessionId, env.seqNo, result.is_final)

    async def run(self) -> None:
        self.nats = NatsClient(self.cfg)
        await self.nats.connect()
        await self.nats.ensure_streams()
        await self.nats.consume(
            stream="audio_in",
            subject="audio_in.>",
            durable="asr",
            queue="asr",
            handler=self.handle,
            max_redeliveries=self.cfg.asr.maxRedeliveries,
        )
        await asyncio.Event().wait()


if __name__ == "__main__":
    logging.basicConfig(level="INFO")
    cfg = AsrConfig.load("/app/config.yaml")
    asyncio.run(AsrWorker(cfg).run())
