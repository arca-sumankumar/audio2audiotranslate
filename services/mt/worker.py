"""MT worker: consumes asr_out, produces translated text.

Consumes ``asr_out.<sessionId>`` and publishes:
- ``mt_out.<sessionId>`` -> for the TTS stage
- ``output_events.<sessionId>`` -> translated partial/final transcripts

MT is stateless: no session affinity required, any replica may translate
any segment (seqNo is carried through for downstream reordering).
"""
from __future__ import annotations

import asyncio
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from pydantic import BaseModel

from stts_core.config import BaseConfig
from stts_core.envelope import Envelope
from stts_core.models import MTBackend, make_mt
from stts_core.nats import NatsClient

log = logging.getLogger("stts.mt")


class MtSettings(BaseModel):
    inferenceThreads: int = 2   # >= 1
    batchSize: int = 16         # 1-128
    maxRedeliveries: int = 3    # >= 1


class MtConfig(BaseConfig):
    mt: MtSettings = MtSettings()


class MtWorker:
    def __init__(self, cfg: MtConfig):
        self.cfg = cfg
        # The backend configured for this worker (STTS_MODEL_BACKEND / config
        # `model.backend`) is the "default" that runs when a message does not
        # select a catalog model. Catalog models requested per-session (demo
        # dropdown) are lazily built and cached, keyed by model id.
        self.default_backend: MTBackend = make_mt(cfg.model)
        self.backends: dict[str, MTBackend] = {}
        self._backend_lock = threading.Lock()
        self.executor = ThreadPoolExecutor(
            max_workers=cfg.mt.inferenceThreads,
            thread_name_prefix="mt-infer",
        )
        self.nats = None

    def _backend_for(self, model_id: str) -> MTBackend:
        if not model_id or model_id == "default":
            return self.default_backend
        backend = self.backends.get(model_id)
        if backend is not None:
            return backend
        with self._backend_lock:
            backend = self.backends.get(model_id)
            if backend is None:
                log.info("building MT backend for model '%s'", model_id)
                backend = make_mt(self.cfg.model, model_id)
                self.backends[model_id] = backend
            return backend

    async def handle(self, env: Envelope, msg) -> None:
        payload = env.payload
        model_id = payload.get("model", "")
        try:
            backend = self._backend_for(model_id)
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                self.executor,
                backend.translate,
                env.sourceLanguage or "en",
                env.targetLanguage or "en",
                payload.get("text", ""),
                payload.get("startMs", 0),
                payload.get("endMs", 0),
                payload.get("isFinal", False),
            )
        except Exception as exc:  # noqa: BLE001
            # Permanent failures (missing model dir / package, unsupported
            # pair) must not redeliver forever: surface an `error` event so the
            # gateway fails the batch job and the streaming client sees it.
            log.exception("mt translation failed for %s (model=%r): %s",
                          env.sessionId, model_id, exc)
            await self.nats.publish(
                f"output_events.{env.sessionId}",
                Envelope(
                    sessionId=env.sessionId,
                    jobId=env.jobId,
                    seqNo=env.seqNo,
                    type="error",
                    sourceLanguage=env.sourceLanguage,
                    targetLanguage=env.targetLanguage,
                    payload={"code": "MT_ERROR",
                             "model": model_id or self.cfg.model.backend,
                             "message": f"MT backend '{model_id or 'default'}': {exc}"},
                ),
            )
            return

        out = Envelope(
            sessionId=env.sessionId,
            jobId=env.jobId,
            seqNo=env.seqNo,
            type=("final_transcript" if result.is_final else "partial_transcript"),
            sourceLanguage=env.sourceLanguage,
            targetLanguage=env.targetLanguage,
            payload={
                "stage": "mt",
                "text": result.text,
                "startMs": result.start_ms,
                "endMs": result.end_ms,
                "isFinal": result.is_final,
                "sessionEnd": payload.get("sessionEnd", False),
                "isLast": payload.get("isLast", False),
                "model": model_id or self.cfg.model.backend,
            },
        )
        await self.nats.publish(f"mt_out.{env.sessionId}", out)
        await self.nats.publish(f"output_events.{env.sessionId}", out)
        log.debug("mt %s seq=%d final=%s model=%s", env.sessionId, env.seqNo,
                  result.is_final, model_id or self.cfg.model.backend)

    async def run(self) -> None:
        self.nats = NatsClient(self.cfg)
        await self.nats.connect()
        await self.nats.ensure_streams()
        await self.nats.consume(
            stream="asr_out",
            subject="asr_out.>",
            durable="mt",
            queue="mt",
            handler=self.handle,
            max_redeliveries=self.cfg.mt.maxRedeliveries,
        )
        await asyncio.Event().wait()


if __name__ == "__main__":
    logging.basicConfig(level="INFO")
    cfg = MtConfig.load("/app/config.yaml")
    asyncio.run(MtWorker(cfg).run())
