"""NATS JetStream transport helpers.

Thin wrapper around ``nats-py`` providing:
- idempotent publish (Nats-Msg-Id dedup header),
- durable push consumers with per-message ack,
- stream bootstrap for the admin script.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Awaitable, Callable, Optional

import nats
from nats.aio.msg import Msg
from nats.js import JetStreamContext
from nats.js.api import StreamConfig, StorageType, RetentionPolicy
from nats.js.client import JetStreamManager

from stts_core.config import BaseConfig
from stts_core.envelope import Envelope

log = logging.getLogger("stts.nats")

Handler = Callable[[Envelope, Msg], Awaitable[None]]


class NatsClient:
    def __init__(self, cfg: BaseConfig):
        self._cfg = cfg
        self._nc: Optional[nats.aio.client.Client] = None
        self._js: Optional[JetStreamContext] = None

    async def connect(self) -> "NatsClient":
        user = self._cfg.natsUser or None
        password = self._cfg.natsPass or None
        self._nc = await nats.connect(
            self._cfg.natsUrl,
            user=user,
            password=password,
            max_reconnect_attempts=-1,
            reconnect_time_wait=2,
        )
        self._js = self._nc.jetstream()
        log.info("connected to NATS at %s", self._cfg.natsUrl)
        return self

    async def close(self) -> None:
        if self._nc:
            await self._nc.close()

    @property
    def is_connected(self) -> bool:
        return bool(self._nc and self._nc.is_connected)

    async def ensure_streams(self) -> None:
        """Idempotently create the JetStream streams this pipeline needs.

        Every service calls this at startup, so stream bootstrap never
        depends on a separate init container or on ordering.
        """
        if self._js is None:
            raise RuntimeError("NATS not connected")
        await ensure_streams(self._js)

    async def publish(self, subject: str, envelope: Envelope) -> None:
        """Publish envelope with dedup header (at-least-once delivery)."""
        if self._js is None:
            raise RuntimeError("NATS not connected")
        headers = {"Nats-Msg-Id": envelope.dedup_id}
        await self._js.publish(
            subject,
            json.dumps(envelope.to_dict()).encode("utf-8"),
            headers=headers,
        )

    async def consume(
        self,
        stream: str,
        subject: str,
        handler: Handler,
        durable: Optional[str] = None,
        queue: Optional[str] = None,
        max_redeliveries: int = 3,
    ) -> None:
        """Push consumer with ack-after-handling.

        ``durable=None`` creates an ephemeral consumer (auto-cleaned when the
        subscription is closed) - suitable for per-session inboxes.
        """
        if self._js is None:
            raise RuntimeError("NATS not connected")

        async def _on_msg(msg: Msg) -> None:
            try:
                data = json.loads(msg.data.decode("utf-8"))
                envelope = Envelope.from_dict(data)
                await handler(envelope, msg)
                await msg.ack()
            except Exception as exc:  # noqa: BLE001 - redeliver on failure
                log.exception("handler failed for %s: %s", subject, exc)
                try:
                    await msg.nak(delay=1)
                except Exception:
                    await msg.term()

        await self._js.subscribe(
            subject,
            stream=stream,
            durable=durable,
            queue=queue,
            cb=_on_msg,
            manual_ack=True,
            config=StreamConsumerConfig(max_redeliveries=max_redeliveries),
        )
        log.info(
            "consuming stream=%s subject=%s durable=%s queue=%s",
            stream, subject, durable or "(ephemeral)", queue,
        )


def StreamConsumerConfig(max_redeliveries: int):
    from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy  # noqa: PLC0415

    return ConsumerConfig(
        deliver_policy=DeliverPolicy.ALL,
        ack_policy=AckPolicy.EXPLICIT,
        max_deliver=max_redeliveries,
    )


async def ensure_streams(jsc: JetStreamManager) -> None:
    """Create all streams + retention settings. Idempotent."""
    streams = [
        ("audio_in", ["audio_in.>"], 60),
        ("asr_out", ["asr_out.>"], 60),
        ("mt_out", ["mt_out.>"], 60),
        ("tts_out", ["tts_out.>"], 60),
        ("output_events", ["output_events.>"], 600),
        ("jobs", ["jobs.>"], 86400),
    ]
    for name, subjects, max_age in streams:
        try:
            await jsc.add_stream(
                StreamConfig(
                    name=name,
                    subjects=subjects,
                    retention=RetentionPolicy.LIMITS,
                    storage=StorageType.FILE,
                    max_age=max_age,
                    duplicate_window=max_age,
                )
            )
            log.info("stream %s created", name)
        except Exception as exc:  # noqa: BLE001 - stream may already exist
            log.info("stream %s exists or error: %s", name, exc)
