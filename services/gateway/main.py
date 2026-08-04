"""STTS gateway service.

Public REST surface for batch job status + health/metrics, backed by SQLite.
Subscribes to ``output_events.>`` to record transcripts, audio and lifecycle
events, and to finalize jobs/sessions when the ``end`` marker arrives.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import threading
from contextlib import asynccontextmanager
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from stts_core.config import BaseConfig
from stts_core.envelope import Envelope
from stts_core.nats import NatsClient

log = logging.getLogger("stts.gateway")


class GatewaySettings(BaseModel):
    dbPath: str = "/data/gateway.db"
    jobTtlSeconds: int = 86400  # 300-2592000
    maxRedeliveries: int = 3    # >= 1


class GatewayConfig(BaseConfig):
    gateway: GatewaySettings = GatewaySettings()


class Db:
    """Minimal thread-safe SQLite wrapper."""

    def __init__(self, path: str):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id  TEXT PRIMARY KEY,
                    job_id      TEXT,
                    source_lang TEXT NOT NULL,
                    target_lang TEXT NOT NULL,
                    status      TEXT NOT NULL DEFAULT 'streaming',
                    created_at  TEXT NOT NULL,
                    updated_at  TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id       TEXT PRIMARY KEY,
                    status       TEXT NOT NULL,
                    input_path   TEXT,
                    output_path  TEXT,
                    transcript   TEXT,
                    error_code   TEXT,
                    error_message TEXT,
                    created_at   TEXT NOT NULL,
                    updated_at   TEXT NOT NULL
                );
                """
            )
            self._conn.commit()

    def exec(self, sql: str, params: tuple = ()) -> None:
        with self._lock:
            self._conn.execute(sql, params)
            self._conn.commit()

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            cur = self._conn.execute(sql, params)
            return cur.fetchall()


class Gateway:
    def __init__(self, cfg: GatewayConfig):
        self.cfg = cfg
        self.db = Db(cfg.gateway.dbPath)
        self.nats: Optional[NatsClient] = None
        self._transcripts: dict[str, list[str]] = {}
        self._metrics = {"events": 0, "errors": 0}

    # ------------------------------------------------------------ consumers
    async def handle_event(self, env: Envelope, msg) -> None:
        self._metrics["events"] += 1
        try:
            self._apply(env)
        except Exception as exc:  # noqa: BLE001
            self._metrics["errors"] += 1
            log.exception("gateway event failed: %s", exc)
            raise

    def _apply(self, env: Envelope) -> None:
        now = env.timestamp
        sid, jid, typ = env.sessionId, env.jobId, env.type
        payload = env.payload

        if typ == "session_started":
            self.db.exec(
                "INSERT INTO sessions (session_id, job_id, source_lang, target_lang,"
                " status, created_at, updated_at)"
                " VALUES (?,?,?,?,'streaming',?,?)",
                (sid, jid, env.sourceLanguage, env.targetLanguage, now, now))
        elif typ == "job_started":
            self.db.exec(
                "INSERT INTO jobs (job_id, status, input_path, created_at, updated_at)"
                " VALUES (?, 'processing', ?, ?, ?)",
                (jid, payload.get("inputPath"), now, now))
            self._transcripts.setdefault(jid, [])
        elif typ in ("partial_transcript", "final_transcript"):
            # Aggregated job transcript uses the translated (mt-stage) text only.
            text = payload.get("text", "")
            key = jid or sid
            if payload.get("stage") == "mt":
                self._transcripts.setdefault(key, []).append(text)
            if typ == "final_transcript":
                self.db.exec(
                    "INSERT OR IGNORE INTO sessions (session_id, job_id, source_lang,"
                    " target_lang, status, created_at, updated_at)"
                    " VALUES (?,?,?,?,'streaming',?,?)",
                    (sid, jid, env.sourceLanguage, env.targetLanguage, now, now))
        elif typ == "audio_output" and jid and payload.get("isFinal"):
            data = bytes.fromhex(payload.get("data", ""))
            fname = f"{jid}.{self.cfg.audio.outputFormat}"
            path = os.path.join(self.cfg.audio.outputDir, fname)
            os.makedirs(self.cfg.audio.outputDir, exist_ok=True)
            with open(path, "wb") as fh:
                fh.write(data)
            self.db.exec(
                "UPDATE jobs SET output_path=? WHERE job_id=?", (path, jid))
        elif typ == "job_done":
            self._finalize_job(jid, sid, now)
        elif typ == "end":
            # Session lifecycle: streaming sessions finalize on `end`.
            # Batch jobs finalize via `job_done` (emitted by TTS after the last
            # segment) so the aggregated transcript/audio are complete.
            self.db.exec(
                "UPDATE sessions SET status='completed', updated_at=?"
                " WHERE session_id=?", (now, sid))
        elif typ == "error":
            if jid:
                self.db.exec(
                    "UPDATE jobs SET status='failed', error_code=?, error_message=?,"
                    " updated_at=? WHERE job_id=?",
                    (payload.get("code"), payload.get("message"), now, jid))

    def _finalize_job(self, job_id: str, session_id: str, now: str) -> None:
        key = job_id or session_id
        transcript = " ".join(self._transcripts.get(key, [])).strip()
        if job_id:
            self.db.exec(
                "UPDATE jobs SET status='done', transcript=?, updated_at=?"
                " WHERE job_id=? AND status='processing'",
                (transcript, now, job_id))
        self._transcripts.pop(key, None)

    # -------------------------------------------------------------- queries
    def job_status(self, job_id: str) -> dict:
        rows = self.db.query(
            "SELECT * FROM jobs WHERE job_id=?", (job_id,))
        if not rows:
            raise KeyError(job_id)
        row = rows[0]
        return {
            "jobId": row["job_id"],
            "status": row["status"],
            "inputPath": row["input_path"],
            "outputPath": row["output_path"],
            "transcript": row["transcript"],
            "error": ({"code": row["error_code"], "message": row["error_message"]}
                      if row["error_code"] else None),
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }


gateway_singleton: Optional[Gateway] = None


def make_app() -> FastAPI:
    app = FastAPI(title="STTS Gateway", version="0.1.0")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        global gateway_singleton
        cfg = GatewayConfig.load("/app/config.yaml")
        logging.basicConfig(level=cfg.logLevel.upper())
        gw = Gateway(cfg)
        gateway_singleton = gw
        nats = NatsClient(cfg)
        await nats.connect()
        await nats.ensure_streams()
        gw.nats = nats
        app.state.gw = gw
        consumer = asyncio.create_task(
            nats.consume(
                stream="output_events",
                subject="output_events.>",
                durable="gateway",
                handler=gw.handle_event,
                max_redeliveries=cfg.gateway.maxRedeliveries,
            ))
        log.info("gateway ready on %s:%s", cfg.server.host, cfg.server.port)
        yield
        consumer.cancel()
        await nats.close()

    app.router.lifespan_context = lifespan

    @app.get("/api/v1/health")
    async def health():
        return {"status": "ok"}

    @app.get("/api/v1/ready")
    async def ready():
        gw = gateway_singleton
        if not gw or not gw.nats or not gw.nats.is_connected:
            raise HTTPException(status_code=503, detail="broker unavailable")
        return {"status": "ready"}

    @app.get("/api/v1/jobs/{job_id}")
    async def get_job(job_id: str):
        gw = gateway_singleton
        try:
            return gw.job_status(job_id)
        except KeyError:
            raise HTTPException(status_code=404, detail={
                "code": "JOB_NOT_FOUND", "message": f"unknown job {job_id}"})

    @app.post("/api/v1/jobs/{job_id}/cancel")
    async def cancel_job(job_id: str):
        gw = gateway_singleton
        gw.db.exec(
            "UPDATE jobs SET status='failed', error_code='CANCELLED',"
            " error_message='cancelled by user', updated_at=? WHERE job_id=?",
            (Envelope().timestamp, job_id))
        return {"jobId": job_id, "status": "failed", "reason": "cancelled"}

    @app.get("/api/v1/metrics")
    async def metrics():
        gw = gateway_singleton
        return {**gw._metrics, "activeSessions": len(gw._transcripts)}

    @app.get("/api/v1/config")
    async def config():
        gw = gateway_singleton
        return gw.cfg.model_dump()

    return app


if __name__ == "__main__":
    _cfg = GatewayConfig.load("/app/config.yaml")
    uvicorn.run(make_app(), host=_cfg.server.host, port=_cfg.server.port,
                log_level="info")
