#!/usr/bin/env python3
"""Download the "Simulated patient-physician medical interviews" dataset
(lordpatil, Kaggle) one case at a time.

Kaggle layout (from the dataset metadata):
    Data/Audio Recordings/<ID>.mp3       272 mp3 (16 kHz mono, ~2-7 MB)
    Data/Clean Transcripts/<ID>.txt      272 gold transcripts, "D:"/"P:" labels

Auth uses the Kaggle API token via the ``KAGGLE_API_TOKEN`` environment
variable (new-style ``KGAT_`` token). Nothing is written to disk except the
downloaded files; the token is never logged.

Usage:
    scripts/download_eval_dataset.py --list
    scripts/download_eval_dataset.py CAR0004 MSK0008 [--force]
    scripts/download_eval_dataset.py --all [--force]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "data", "eval", "simulated_doctor_patient")

OWNER = "lordpatil"
DATASET = "simulated-patient-physician-medical-interviews"
LIST_URL = f"https://www.kaggle.com/api/v1/datasets/list/{OWNER}/{DATASET}"
DL_URL = f"https://www.kaggle.com/api/v1/datasets/download/{OWNER}/{DATASET}/{{path}}"
AUDIO_PREFIX = "Data/Audio Recordings/"
TXT_PREFIX = "Data/Clean Transcripts/"

CASE_RE = re.compile(r"^[A-Z]{3}\d{4}$")


def _token() -> str:
    tok = os.environ.get("KAGGLE_API_TOKEN", "").strip()
    if not tok:
        sys.exit("KAGGLE_API_TOKEN env var not set (export KAGGLE_API_TOKEN=...)")
    return tok


def _request(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {_token()}"})
    with urllib.request.urlopen(req) as r:
        return r.read()


def list_case_ids() -> list[str]:
    ids: list[str] = []
    page_token = None
    while True:
        url = LIST_URL + (f"?pageToken={urllib.parse.quote(page_token)}" if page_token else "")
        meta = json.loads(_request(url).decode("utf-8"))
        for f in meta.get("datasetFiles", []):
            name = f.get("nameNullable") or ""
            if name.startswith(AUDIO_PREFIX) and name.endswith(".mp3"):
                ids.append(os.path.splitext(os.path.basename(name))[0])
        page_token = meta.get("nextPageTokenNullable")
        if not page_token:
            break
    return sorted(ids)


def download_file(case_id: str, prefix: str, out_path: str, force: bool) -> str:
    if os.path.isfile(out_path) and not force:
        return "cached"
    rel = prefix + case_id + (".mp3" if prefix == AUDIO_PREFIX else ".txt")
    url = DL_URL.format(path=urllib.parse.quote(rel, safe=""))
    data = _request(url)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(data)
    return f"{len(data)} B"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cases", nargs="*", help="case IDs to download (e.g. CAR0004)")
    ap.add_argument("--list", action="store_true", help="list all available case IDs")
    ap.add_argument("--all", action="store_true", help="download every case")
    ap.add_argument("--force", action="store_true", help="re-download even if present")
    ap.add_argument("--out", default=OUT_DIR, help=f"output dir (default {OUT_DIR})")
    args = ap.parse_args()

    ids = list_case_ids()
    if args.list:
        print(f"{len(ids)} cases available:")
        for i in range(0, len(ids), 8):
            print("  " + "  ".join(ids[i:i + 8]))
        return

    if args.all:
        cases = ids
    else:
        cases = args.cases
        bad = [c for c in cases if not CASE_RE.match(c)]
        if bad:
            sys.exit(f"invalid case IDs: {', '.join(bad)} (expected e.g. CAR0004)")

    if not cases:
        sys.exit("no cases given (use --all, --list, or pass case IDs)")

    for cid in cases:
        if cid not in ids:
            print(f"!! {cid}: not in dataset (skipping)")
            continue
        case_dir = os.path.join(args.out, cid)
        audio_status = download_file(
            cid, AUDIO_PREFIX, os.path.join(case_dir, "audio.mp3"), args.force)
        txt_status = download_file(
            cid, TXT_PREFIX, os.path.join(case_dir, "transcript.txt"), args.force)
        print(f"{cid}: audio {audio_status}, transcript {txt_status} -> {case_dir}")


if __name__ == "__main__":
    main()
