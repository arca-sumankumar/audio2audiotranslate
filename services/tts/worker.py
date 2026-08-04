"""TTS worker: consumes mt_out, synthesizes target-language audio.

Consumes ``mt_out.<sessionId>`` and publishes ``output_events.<sessionId>``
with type ``audio_output`` (WAV bytes hex-encoded).
"""
from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from pydantic import BaseModel

from stts_core.config import BaseConfig
from stts_core.envelope import Envelope
from stts_core.models import TTSBackend, make_tts
from stts_core.nats import NatsClient

log = logging.getLogger("stts.tts")


class TtsSettings(BaseModel):
    inferenceThreads: int = 2   # >= 1
    onPartial: bool = False     # true | false
    maxRedeliveries: int = 3    # >= 1


class TtsConfig(BaseConfig):
    tts: TtsSettings = TtsSettings()


class TtsWorker:
    def __init__(self, cfg: TtsConfig):
        self.cfg = cfg
        self.backend: TTSBackend = make_tts(cfg.model)
        self.executor = ThreadPoolExecutor(
            max_workers=cfg.tts.inferenceThreads,
            thread_name_prefix="tts-infer",
        )
        self.nats = None

    async def handle(self, env: Envelope, msg) -> None:
        payload = env.payload
        is_final = payload.get("isFinal", False)
        if not is_final and not self.cfg.tts.onPartial:
            log.debug("tts skip partial %s seq=%d", env.sessionId, env.seqNo)
            return

        text = payload.get("text", "")
        if text:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                self.executor,
                self.backend.synthesize,
                env.targetLanguage or "en",
                text,
                payload.get("startMs", 0),
                payload.get("endMs", 0),
            )

            out = Envelope(
                sessionId=env.sessionId,
                jobId=env.jobId,
                seqNo=env.seqNo,
                type="audio_output",
                sourceLanguage=env.sourceLanguage,
                targetLanguage=env.targetLanguage,
                payload={
                    "format": result.format,
                    "startMs": result.start_ms,
                    "endMs": result.end_ms,
                    "isFinal": is_final,
                    "isLast": payload.get("isLast", False),
                    "text": text,
                    "data": result.data.hex(),
                },
            )
            await self.nats.publish(f"output_events.{env.sessionId}", out)
            log.debug("tts %s seq=%d audio=%d bytes",
                      env.sessionId, env.seqNo, len(result.data))

        # Streaming sessions (no jobId) end when the *last* segment is voiced.
        # Every confirmed segment is a final now (incremental continuation), so
        # only the one carrying `sessionEnd` closes the session.
        session_end = is_final and env.jobId is None and payload.get("sessionEnd")
        if session_end:
            await self.nats.publish(
                f"output_events.{env.sessionId}",
                Envelope(
                    sessionId=env.sessionId,
                    jobId=env.jobId,
                    seqNo=env.seqNo,
                    type="end",
                    sourceLanguage=env.sourceLanguage,
                    targetLanguage=env.targetLanguage,
                    payload={"reason": "complete"},
                ),
            )
        # Batch jobs finish when the last segment is voiced.
        elif is_final and payload.get("isLast"):
            await self.nats.publish(
                f"output_events.{env.sessionId}",
                Envelope(
                    sessionId=env.sessionId,
                    jobId=env.jobId,
                    seqNo=env.seqNo,
                    type="job_done",
                    sourceLanguage=env.sourceLanguage,
                    targetLanguage=env.targetLanguage,
                    payload={"reason": "batch"},
                ),
            )

    async def run(self) -> None:
        self.nats = NatsClient(self.cfg)
        await self.nats.connect()
        await self.nats.ensure_streams()
        await self.nats.consume(
            stream="mt_out",
            subject="mt_out.>",
            durable="tts",
            queue="tts",
            handler=self.handle,
            max_redeliveries=self.cfg.tts.maxRedeliveries,
        )
        await asyncio.Event().wait()


if __name__ == "__main__":
    logging.basicConfig(level="INFO")
    cfg = TtsConfig.load("/app/config.yaml")
    asyncio.run(TtsWorker(cfg).run())
