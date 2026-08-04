"""End-to-end smoke test: batch + streaming through the full pipeline.

Works both inside a container sharing the ``/data`` volume (Makefile) and
directly on the laptop against a local stack::

  STTS_INGEST_URL=http://localhost:50010 \
  STTS_GATEWAY_URL=http://localhost:51000 \
  STTS_DATA_DIR="$PWD/data" .venv/bin/python scripts/smoke_test.py

  python scripts/smoke_test.py
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request

import websockets

from stts_core.audio import synth_tone_wav

INGEST = os.environ.get("STTS_INGEST_URL", "http://ingest:50010")
GATEWAY = os.environ.get("STTS_GATEWAY_URL", "http://gateway:51000")
DATA_DIR = os.environ.get("STTS_DATA_DIR", "/data")


def _http_json(method: str, url: str, body: dict | None = None):
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode() if body else None,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return json.loads(exc.read())


def wait_ready(url: str, timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if _http_json("GET", f"{url}/api/v1/ready").get("status") == "ready":
                return
        except Exception:
            pass
        time.sleep(1)
    raise SystemExit(f"timeout waiting for {url}")


async def test_batch() -> str:
    sample = os.path.join(DATA_DIR, "sample.wav")
    with open(sample, "wb") as fh:
        fh.write(synth_tone_wav(5000))
    resp = _http_json("POST", f"{INGEST}/api/v1/translate", {
        "filePath": sample,
        "fileFormat": "wav",
        "sourceLanguage": "en",
        "targetLanguage": "hi",
    })
    job_id = resp["jobId"]
    print(f"[batch] job queued: {job_id}")

    deadline = time.time() + 60
    while time.time() < deadline:
        status = _http_json("GET", f"{GATEWAY}/api/v1/jobs/{job_id}")
        if status["status"] in ("done", "failed"):
            break
        await asyncio.sleep(1)
    print(f"[batch] status={status['status']}")
    if status["status"] != "done":
        raise SystemExit(f"batch job failed: {status}")
    assert status["transcript"], "empty transcript"
    print(f"[batch] transcript: {status['transcript']}")
    print(f"[batch] audio: {status['outputPath']}")
    return job_id


async def test_stream() -> None:
    uri = f"ws://{INGEST.split('://')[1]}/api/v1/stream" \
          "?sourceLanguage=en&targetLanguage=hi&format=wav"
    seen = []
    async with websockets.connect(uri) as ws:
        for i in range(4):
            chunk = synth_tone_wav(300)
            await ws.send(json.dumps({
                "type": "audio_chunk",
                "seqNo": i + 1,
                "data": base64.b64encode(chunk).decode(),
                "isFinal": i == 3,
            }))
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=10)
            msg = json.loads(raw)
            seen.append(msg["type"])
            if msg["type"] == "end":
                break
    print(f"[stream] received: {seen}")
    assert "final_transcript" in seen, "no final transcript received"
    assert "audio_output" in seen, "no audio output received"
    assert "partial_transcript" in seen, "no partial transcript received"


async def main() -> None:
    wait_ready(f"{INGEST}")
    wait_ready(f"{GATEWAY}")
    await test_batch()
    await test_stream()
    print("SMOKE TEST PASSED")


if __name__ == "__main__":
    asyncio.run(main())
