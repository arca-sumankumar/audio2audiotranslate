"""WebSocket streaming endpoint for the ingest service.

Protocol (JSON text frames; audio is hex-encoded):
  client -> {type: audio_chunk, seqNo, data, isFinal}
  server -> {type: ack, seqNo}
  server -> forwarded pipeline events (partial/final_transcript, audio_output, end)
"""
from __future__ import annotations

import asyncio
import base64
import logging
from typing import Optional

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ValidationError

from stts_core.audio import decode_wav
from stts_core.envelope import Envelope, new_id
from stts_core.models import MT_MODEL_IDS

log = logging.getLogger("stts.ingest.ws")

router = APIRouter()


class WsChunk(BaseModel):
    type: str
    seqNo: int = 0
    data: str = ""
    isFinal: bool = False


@router.websocket("/api/v1/stream")
async def stream(websocket: WebSocket):
    app = websocket.app
    cfg = app.state.cfg

    source_lang = websocket.query_params.get("sourceLanguage", "")
    target_lang = websocket.query_params.get("targetLanguage", "")
    fmt = websocket.query_params.get("format", "wav")
    model = websocket.query_params.get("model", "")

    if source_lang not in cfg.model.languages or target_lang not in cfg.model.languages:
        await websocket.close(code=4400, reason="unsupported language")
        return
    if fmt not in cfg.audio.allowedFormats:
        await websocket.close(code=4400, reason="unsupported format")
        return
    if model and model not in MT_MODEL_IDS:
        await websocket.close(code=4400, reason="unsupported model")
        return

    await websocket.accept()

    session_id = new_id()
    chunker = app.state.chunker_factory()
    inbox_sub = None
    seq_counter = 0

    async def forward_to_client(env: Envelope, msg=None):
        log.debug("inbox event: %s seq=%s", env.type, env.seqNo)
        try:
            await websocket.send_json(env.to_dict())
        except Exception as exc:  # noqa: BLE001
            log.warning("inbox send failed: %s", exc)

    async def consume_inbox():
        nonlocal inbox_sub
        try:
            inbox_sub = await app.state.nats.consume(
                stream="output_events",
                subject=f"output_events.{session_id}",
                handler=forward_to_client,
            )
        except Exception as exc:  # noqa: BLE001
            log.error("inbox consume failed: %s", exc)
            raise
        await asyncio.Event().wait()

    # notify gateway a session started
    await app.state.nats.publish(
        f"output_events.{session_id}",
        Envelope(
            sessionId=session_id,
            seqNo=0,
            type="session_started",
            sourceLanguage=source_lang,
            targetLanguage=target_lang,
            payload={"format": fmt, "model": model},
        ),
    )

    inbox_task = asyncio.create_task(consume_inbox())

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = WsChunk.model_validate_json(raw)
            except ValidationError:
                await websocket.send_json({"type": "error",
                                           "code": "INVALID_FRAME",
                                           "message": "malformed json"})
                continue
            if msg.type != "audio_chunk":
                await websocket.send_json({"type": "error",
                                           "code": "INVALID_FRAME",
                                           "message": f"unknown type {msg.type}"})
                continue

            try:
                pcm, rate, _ = decode_wav(base64.b64decode(msg.data))
            except Exception:
                await websocket.send_json({"type": "error",
                                           "code": "INVALID_AUDIO_CHUNK",
                                           "message": "could not decode wav"})
                continue

            for chunk in chunker.add(pcm, is_final=msg.isFinal):
                chunk.sample_rate = rate
                seq_counter += 1
                chunk.seq_no = seq_counter
                await app.state.nats.publish(
                    f"audio_in.{session_id}",
                    Envelope(
                        sessionId=session_id,
                        seqNo=chunk.seq_no,
                        type="audio_in",
                        sourceLanguage=source_lang,
                        targetLanguage=target_lang,
                        payload={
                            "format": chunk.format,
                            "sampleRate": chunk.sample_rate,
                            "durationMs": chunk.duration_ms,
                            "isFinal": chunk.is_final,
                            "model": model,
                            "data": chunk.data.hex(),
                        },
                    ),
                )

            await websocket.send_json({"type": "ack", "seqNo": msg.seqNo})

            if msg.isFinal:
                # The final chunk was published. The pipeline (TTS) emits an
                # `end` event to output_events once the final segment is done;
                # the inbox delivers it to this client. Do not send `end`
                # eagerly here or the client would miss the trailing events.
                log.info("session %s final chunk received; awaiting pipeline end", session_id)
    except WebSocketDisconnect:
        log.info("session %s disconnected", session_id)
    finally:
        if inbox_task:
            inbox_task.cancel()
        await app.state.nats.publish(
            f"output_events.{session_id}",
            Envelope(
                sessionId=session_id,
                seqNo=seq_counter + 1,
                type="end",
                sourceLanguage=source_lang,
                targetLanguage=target_lang,
                payload={"reason": "client_closed"},
            ),
        )
