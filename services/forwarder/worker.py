"""Forwarder worker: reorders output_events per session and rebroadcasts
them to a downstream SDK over WebSocket.

Ordering: per-session buffer keyed by seqNo; flushed in order when the next
sequence arrives or after ``reorderTimeoutMs``. Multiple events may share a
seqNo (asr + mt transcripts); all are flushed together before advancing.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field

import websockets
from pydantic import BaseModel

from stts_core.config import BaseConfig
from stts_core.envelope import Envelope
from stts_core.nats import NatsClient

log = logging.getLogger("stts.forwarder")


class ForwarderSettings(BaseModel):
    reorderWindow: int = 32         # 1-1024
    reorderTimeoutMs: int = 500     # 10-5000
    bufferSize: int = 10000         # 100-100000
    sessionIdleSeconds: int = 300   # 60-3600
    maxRedeliveries: int = 3        # >= 1


class ForwarderConfig(BaseConfig):
    forwarder: ForwarderSettings = ForwarderSettings()


@dataclass
class SessionOrdering:
    """Per-session in-order dispatch buffer."""
    seq_next: int = 0
    buckets: dict[int, list[Envelope]] = field(default_factory=dict)
    flushed: int = 0

    def push(self, env: Envelope) -> list[Envelope]:
        """Return envelopes that can be emitted in order (may be empty)."""
        seq = env.seqNo
        if seq >= self.seq_next:
            self.buckets.setdefault(seq, []).append(env)
        else:
            log.warning("forwarder late event seq=%d next=%d", seq, self.seq_next)
        return self.flush()

    def flush(self) -> list[Envelope]:
        out: list[Envelope] = []
        while self.buckets.get(self.seq_next):
            out.extend(self.buckets.pop(self.seq_next))
            self.seq_next += 1
        if out:
            self.flushed += 1
        return out

    def flush_all(self) -> list[Envelope]:
        out: list[Envelope] = []
        for seq in sorted(self.buckets):
            out.extend(self.buckets[seq])
        self.buckets.clear()
        self.seq_next = max(self.seq_next, max((e.seqNo for e in out), default=0) + 1)
        return out


class OutboundSocket:
    """Durable WebSocket writer to the downstream SDK with retry/backoff."""

    def __init__(self, cfg: BaseConfig):
        self.cfg = cfg
        self.queue: asyncio.Queue[Envelope] = asyncio.Queue(
            maxsize=cfg.forwarder.bufferSize)
        self._ws = None
        self._stop = False

    async def send(self, env: Envelope) -> None:
        try:
            self.queue.put_nowait(env)
        except asyncio.QueueFull:
            log.error("forwarder outbound buffer full; dropping seq=%d", env.seqNo)

    async def run(self) -> None:
        if not self.cfg.socket.enabled:
            log.info("outbound socket disabled (log-only mode)")
            return
        delay = max(0, self.cfg.socket.reconnectDelayMs) / 1000
        attempts = 0
        while not self._stop:
            try:
                async with websockets.connect(self.cfg.socket.url) as ws:
                    self._ws = ws
                    attempts = 0
                    delay = max(0, self.cfg.socket.reconnectDelayMs) / 1000
                    log.info("downstream socket connected: %s", self.cfg.socket.url)
                    while not self._stop:
                        env = await self.queue.get()
                        await ws.send(json.dumps(env.to_dict()))
            except Exception as exc:  # noqa: BLE001 - reconnect on any failure
                attempts += 1
                if (self.cfg.socket.maxRetries >= 0
                        and attempts > self.cfg.socket.maxRetries):
                    log.error("giving up downstream socket after %d retries", attempts)
                    return
                log.warning("downstream socket error: %s; retry in %.1fs", exc, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, self.cfg.socket.maxReconnectDelayMs / 1000)


class ForwarderWorker:
    def __init__(self, cfg: ForwarderConfig):
        self.cfg = cfg
        self.sessions: dict[str, SessionOrdering] = defaultdict(SessionOrdering)
        self.sessions_last: dict[str, float] = {}
        self.outbound = OutboundSocket(cfg)
        self.nats = None

    async def handle(self, env: Envelope, msg) -> None:
        order = self.sessions[env.sessionId]
        self.sessions_last[env.sessionId] = time.time()

        to_emit = order.push(env)
        if not to_emit:
            # wait for the reorder window to advance, then force flush
            if (time.time() - self.sessions_last[env.sessionId]) * 1000 >= \
                    self.cfg.forwarder.reorderTimeoutMs:
                to_emit = order.flush()
        for item in to_emit:
            await self._emit(item)

        if env.type == "end":
            for item in order.flush_all():
                await self._emit(item)
            self.sessions.pop(env.sessionId, None)
            self.sessions_last.pop(env.sessionId, None)

    async def _emit(self, env: Envelope) -> None:
        if self.cfg.socket.enabled and self.cfg.socket.url:
            await self.outbound.send(env)
        else:
            log.info("FWD %s %s seq=%d %s",
                     env.sessionId, env.type, env.seqNo,
                     json.dumps(env.payload.get("text", env.payload.get("data", ""))[:80]))

    async def _sweep_idle(self) -> None:
        while True:
            await asyncio.sleep(30)
            cutoff = time.time() - self.cfg.forwarder.sessionIdleSeconds
            idle = [sid for sid, ts in self.sessions_last.items() if ts < cutoff]
            for sid in idle:
                self.sessions.pop(sid, None)
                self.sessions_last.pop(sid, None)

    async def run(self) -> None:
        self.nats = NatsClient(self.cfg)
        await self.nats.connect()
        await self.nats.ensure_streams()
        writer = asyncio.create_task(self.outbound.run())
        sweeper = asyncio.create_task(self._sweep_idle())
        await self.nats.consume(
            stream="output_events",
            subject="output_events.>",
            durable="forwarder",
            handler=self.handle,
            max_redeliveries=self.cfg.forwarder.maxRedeliveries,
        )
        try:
            await asyncio.Event().wait()
        finally:
            writer.cancel()
            sweeper.cancel()


if __name__ == "__main__":
    logging.basicConfig(level="INFO")
    cfg = ForwarderConfig.load("/app/config.yaml")
    asyncio.run(ForwarderWorker(cfg).run())
