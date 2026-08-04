#!/usr/bin/env python3
"""Run a batch translation on every doctor-patient test audio in
``data/test_audio/*.wav`` and print the source transcript next to the
translated one for review, plus an automatic sanity check.

Usage:
    scripts/test_batch_audio.py                # en -> hi
    scripts/test_batch_audio.py ta             # en -> ta
    scripts/test_batch_audio.py hi --src bn    # bn -> hi

The source language for each file is taken from the filename when it
matches ``NN_<lang>_<symptom>`` (e.g. ``06_ml_fever`` -> Malayalam);
otherwise the ``--src`` default (``en``) is used.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUDIO_DIR = os.path.join(ROOT, "data", "test_audio")
INGEST = os.environ.get("STTS_INGEST_URL", "http://localhost:50010")
GATEWAY = os.environ.get("STTS_GATEWAY_URL", "http://localhost:51000")

LANG_RE = re.compile(r"^\d+_([a-z]{2})_")


def lang_of(name: str) -> str | None:
    m = LANG_RE.match(name)
    return m.group(1) if m else None


def _req(method: str, url: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"} if body else {})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def wait_done(job_id: str, timeout: float = 300.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = _req("GET", f"{GATEWAY}/api/v1/jobs/{job_id}")
        if job.get("status") in ("done", "failed"):
            return job
        time.sleep(1.5)
    return {"status": "timeout", "jobId": job_id}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("target", nargs="?", default="hi", help="target language")
    ap.add_argument("--src", default="en", help="source language")
    args = ap.parse_args()

    wavs = sorted(glob.glob(os.path.join(AUDIO_DIR, "*.wav")))
    if not wavs:
        sys.exit(f"no *.wav under {AUDIO_DIR} - run scripts/make_test_audio.py first")

    failures = []
    for wav in wavs:
        name = os.path.splitext(os.path.basename(wav))[0]
        txt = os.path.join(AUDIO_DIR, name + ".txt")
        source = open(txt, encoding="utf-8").read().strip() if os.path.isfile(txt) else "(no .txt)"
        src = lang_of(name) or args.src

        job_id = _req("POST", f"{INGEST}/api/v1/translate", {
            "filePath": wav,
            "fileFormat": "wav",
            "sourceLanguage": src,
            "targetLanguage": args.target,
        })["jobId"]

        job = wait_done(job_id)
        translated = (job.get("transcript") or "").strip()
        status = job.get("status")

        ok = status == "done" and translated and "mock-asr" not in translated
        if not ok:
            failures.append(name)
        flag = "PASS" if ok else "FAIL"
        print("=" * 78)
        print(f"[{flag}] {name}.wav  ({src}->{args.target})  status={status}")
        print("-" * 78)
        print("SOURCE     :", source.replace("\n", " "))
        print("TRANSLATED :", translated)
        if status == "failed":
            print("ERROR      :", job.get("error"))

    print("=" * 78)
    print(f"{len(wavs) - len(failures)}/{len(wavs)} passed"
          + (f", failed: {failures}" if failures else ""))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
