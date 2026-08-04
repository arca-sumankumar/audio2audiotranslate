"""STTS ingest service entrypoint.

Terminates REST + WebSocket, validates input, chunkifies audio and publishes
to NATS JetStream topics (audio_in.<sessionId>).
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from stts_core.config import BaseConfig
from stts_core.nats import NatsClient

from app.api import router as api_router
from app.chunker import Chunker
from app.ws import router as ws_router

log = logging.getLogger("stts.ingest")


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = BaseConfig.load("/app/config.yaml")
    logging.basicConfig(level=cfg.logLevel.upper())
    nats = NatsClient(cfg)
    await nats.connect()
    await nats.ensure_streams()
    app.state.cfg = cfg
    app.state.nats = nats
    app.state.chunker_factory = lambda: Chunker(
        sample_rate=cfg.audio.sampleRate,
        chunk_duration_ms=cfg.audio.chunkDurationMs,
    )
    log.info("ingest ready on %s:%s", cfg.server.host, cfg.server.port)
    yield
    await nats.close()


app = FastAPI(title="STTS Ingest", version="0.1.0", lifespan=lifespan)
app.include_router(api_router)
app.include_router(ws_router)


if __name__ == "__main__":
    import os
    _cfg = BaseConfig.load(os.environ.get("STTS_CONFIG", "/app/config.yaml"))
    uvicorn.run(app, host=_cfg.server.host, port=_cfg.server.port, log_level="info")
