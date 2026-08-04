"""REST endpoints for the ingest service."""
from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from stts_core.audio import AudioChunk, decode_wav
from stts_core.envelope import Envelope, new_id
from stts_core.models import MT_MODEL_IDS

log = logging.getLogger("stts.ingest.api")

router = APIRouter(prefix="/api/v1")


class TranslateRequest(BaseModel):
    filePath: str = Field(..., description="Path to WAV or MP3 file")
    fileFormat: str = Field(..., description="wav | mp3")
    sourceLanguage: str = Field(..., description="source language code")
    targetLanguage: str = Field(..., description="target language code")
    model: str = Field("", description="MT model id from the demo dropdown ('' = configured default)")


class TranslateResponse(BaseModel):
    jobId: str
    status: str = "queued"


def _app(request: Request):
    return request.app


@router.get("/health")
async def health(request: Request):
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request):
    app = request.app
    if not app.state.nats.is_connected:
        raise HTTPException(status_code=503, detail="broker unavailable")
    return {"status": "ready"}


@router.post("/translate", response_model=TranslateResponse, status_code=202)
async def translate(req: TranslateRequest, request: Request):
    app = request.app
    cfg = app.state.cfg

    if req.fileFormat not in cfg.audio.allowedFormats:
        raise HTTPException(status_code=400, detail={
            "code": "INVALID_FILE_FORMAT",
            "message": f"allowed formats: {cfg.audio.allowedFormats}",
        })
    if req.sourceLanguage not in cfg.model.languages or \
            req.targetLanguage not in cfg.model.languages:
        raise HTTPException(status_code=400, detail={
            "code": "UNSUPPORTED_LANGUAGE",
            "message": f"supported: {cfg.model.languages}",
        })
    if req.model and req.model not in MT_MODEL_IDS:
        raise HTTPException(status_code=400, detail={
            "code": "UNSUPPORTED_MODEL",
            "message": f"supported MT models: {sorted(MT_MODEL_IDS)}",
        })
    if not os.path.isfile(req.filePath):
        raise HTTPException(status_code=404, detail={
            "code": "FILE_NOT_FOUND",
            "message": f"no such file: {req.filePath}",
        })
    if os.path.getsize(req.filePath) > cfg.audio.maxFileSizeMb * 1024 * 1024:
        raise HTTPException(status_code=400, detail={
            "code": "FILE_TOO_LARGE",
            "message": f"max size {cfg.audio.maxFileSizeMb} MB",
        })

    if req.fileFormat != "wav":
        raise HTTPException(status_code=501, detail={
            "code": "NOT_IMPLEMENTED",
            "message": "only wav decoding is wired in this build",
        })

    with open(req.filePath, "rb") as fh:
        wav_data = fh.read()

    job_id = new_id()
    chunker = app.state.chunker_factory()
    try:
        chunks = chunker.chunks_from_wav(wav_data, seq_start=1)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={
            "code": "UNSUPPORTED_AUDIO",
            "message": f"could not decode {req.fileFormat}: {exc}",
        }) from exc
    if not chunks:
        raise HTTPException(status_code=400, detail={
            "code": "EMPTY_AUDIO",
            "message": "no audio samples in file",
        })

    # job metadata event for the gateway
    await app.state.nats.publish(
        f"output_events.{job_id}",
        Envelope(
            sessionId=job_id,
            jobId=job_id,
            seqNo=0,
            type="job_started",
            sourceLanguage=req.sourceLanguage,
            targetLanguage=req.targetLanguage,
            payload={"inputPath": req.filePath, "fileFormat": req.fileFormat,
                     "model": req.model},
        ),
    )

    # publish audio chunks through the ASR -> MT -> TTS chain
    for i, chunk in enumerate(chunks, start=1):
        chunk.seq_no = i
        await app.state.nats.publish(
            f"audio_in.{job_id}",
            Envelope(
                sessionId=job_id,
                jobId=job_id,
                seqNo=chunk.seq_no,
                type="audio_in",
                sourceLanguage=req.sourceLanguage,
                targetLanguage=req.targetLanguage,
                payload={
                    "format": chunk.format,
                    "sampleRate": chunk.sample_rate,
                    "durationMs": chunk.duration_ms,
                    "isFinal": chunk.is_final,
                    "isLast": i == len(chunks),
                    "model": req.model,
                    "data": chunk.data.hex(),
                },
            ),
        )

    # end-of-job marker
    await app.state.nats.publish(
        f"output_events.{job_id}",
        Envelope(
            sessionId=job_id,
            jobId=job_id,
            seqNo=len(chunks) + 1,
            type="end",
            sourceLanguage=req.sourceLanguage,
            targetLanguage=req.targetLanguage,
            payload={"reason": "batch"},
        ),
    )

    log.info("batch job %s: %d chunks queued", job_id, len(chunks))
    return TranslateResponse(jobId=job_id)
