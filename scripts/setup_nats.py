"""Idempotent NATS JetStream stream bootstrap.

Run once after the broker is up (and after any upgrade). Creates all
streams with retention/age settings from LLD2 section 3.1.
"""
from __future__ import annotations

import asyncio
import logging
import os

import nats

from stts_core.nats import ensure_streams

log = logging.getLogger("stts.setup")


async def main() -> None:
    url = os.environ.get("NATS_URL", "nats://broker:4222")
    nc = await nats.connect(url)
    jsc = nc.jetstream()
    await ensure_streams(jsc)
    log.info("NATS streams ensured at %s", url)
    await nc.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
