#!/usr/bin/env python3
"""Download the real offline models into ``./models`` from Hugging Face / Mozilla.

Run from the repo root (or anywhere)::

    .venv/bin/python scripts/download_models.py      # or: make models

Downloads:
  - whisper-large-v3-turbo/  mobiuslabsgmbh/faster-whisper-large-v3-turbo  (~1.6 GB, int8 ASR)
  - nllb-600m/     mijuanlo/nllb-200-distilled-600M-ct2-int8  (~1.1 GB)
  - piper/         en_US-lessac + hi_IN-pratham + ml_IN-arjun voices (~190 MB)
  - bergamot/      Mozilla Firefox Translations (fxtranslate) en<->gu/hi/kn/ml/ta pairs (~17 MB/pair)
  - indictrans2/   AI4Bharat IndicTrans2 1.1B en-indic + indic-en checkpoints (~2 x 1 GB)

Models are cached, so re-running only fetches what is missing.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import urllib.request

from huggingface_hub import hf_hub_download, snapshot_download

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODELS = os.path.join(ROOT, "models")

WHISPER_REPO = "mobiuslabsgmbh/faster-whisper-large-v3-turbo"
WHISPER_DIR = "whisper-large-v3-turbo"

# Gated: accept access at https://huggingface.co/ai4bharat/indic-conformer-600m-multilingual
INDICCONFORMER_REPO = "ai4bharat/indic-conformer-600m-multilingual"
INDICCONFORMER_DIR = "indic-conformer-600m"

NLLB_REPO = "mijuanlo/nllb-200-distilled-600M-ct2-int8"
NLLB_DIR = "nllb-600m"

PIPER_REPO = "rhasspy/piper-voices"
PIPER_REV = "v1.0.0"
PIPER_FILES = [
    ("en/en_US/lessac/medium/en_US-lessac-medium.onnx", ".onnx"),
    ("en/en_US/lessac/medium/en_US-lessac-medium.onnx.json", ".json"),
    ("hi/hi_IN/pratham/medium/hi_IN-pratham-medium.onnx", ".onnx"),
    ("hi/hi_IN/pratham/medium/hi_IN-pratham-medium.onnx.json", ".json"),
    ("ml/ml_IN/arjun/medium/ml_IN-arjun-medium.onnx", ".onnx"),
    ("ml/ml_IN/arjun/medium/ml_IN-arjun-medium.onnx.json", ".json"),
]

INDICTRANS2_DIR = "indictrans2"
INDICTRANS2_REPOS = {
    "en-indic-1b": "ai4bharat/indictrans2-en-indic-1B",
    "indic-en-1b": "ai4bharat/indictrans2-indic-en-1B",
}

BERGAMOT_DIR = "bergamot"
# English<->Indic pairs available in Mozilla's models registry (gu/ta/ml/hi/kn).
BERGAMOT_PAIRS = [
    ("en", "gu"), ("gu", "en"),
    ("en", "hi"), ("hi", "en"),
    ("en", "kn"), ("kn", "en"),
    ("en", "ml"), ("ml", "en"),
    ("en", "ta"), ("ta", "en"),
]
BERGAMOT_MODELS_URL = ("https://storage.googleapis.com/moz-fx-translations-data--"
                       "303e-prod-translations-data/db/models.json")


def _http_get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=120) as resp:
        return resp.read()


def _pick_model(entries: list[dict]) -> dict | None:
    """Prefer the released entry; fall back to any available one."""
    for e in entries:
        if e.get("releaseStatus") == "Release":
            return e
    return entries[0] if entries else None


def _download_bergamot() -> None:
    print("fetching Mozilla translations model registry...")
    data = json.loads(_http_get(BERGAMOT_MODELS_URL))
    base = data.get("baseUrl", "")
    registry: dict[str, list[dict]] = data.get("models", {})
    for src, tgt in BERGAMOT_PAIRS:
        entries = registry.get(f"{src}-{tgt}")
        model = _pick_model(entries) if entries else None
        if model is None:
            print(f"  skipping {src}-{tgt}: not in registry")
            continue
        out_dir = os.path.join(MODELS, BERGAMOT_DIR, f"{src}-{tgt}")
        os.makedirs(out_dir, exist_ok=True)
        tag = f"{src}{tgt}"
        files = model.get("files", {})
        downloads = []
        if "model" in files:
            downloads.append(
                (files["model"]["path"],
                 os.path.join(out_dir, f"model.{tag}.intgemm.alphas.bin"),
                 files["model"].get("uncompressedHash")))
        if "vocab" in files:
            downloads.append(
                (files["vocab"]["path"],
                 os.path.join(out_dir, f"vocab.{tag}.spm"), None))
        for rel_path, out_path, sha256 in downloads:
            if os.path.isfile(out_path):
                print(f"  {src}-{tgt}: {os.path.basename(out_path)} cached")
                continue
            url = f"{base}/{rel_path}"
            print(f"  {src}-{tgt}: downloading {os.path.basename(out_path)}")
            raw = _http_get(url)
            content = gzip.decompress(raw) if rel_path.endswith(".gz") else raw
            if sha256 and hashlib.sha256(content).hexdigest() != sha256:
                raise RuntimeError(f"hash mismatch for {rel_path}")
            with open(out_path, "wb") as fh:
                fh.write(content)
    print(f"bergamot models -> {os.path.join(MODELS, BERGAMOT_DIR)}/")


def _download_indictrans2() -> None:
    for subdir, repo in INDICTRANS2_REPOS.items():
        print(f"downloading {repo} -> {subdir}/ (~1 GB)")
        snapshot_download(repo, local_dir=os.path.join(MODELS, INDICTRANS2_DIR, subdir))


def main() -> None:
    os.makedirs(MODELS, exist_ok=True)
    print(f"models dir: {MODELS}")

    print(f"downloading Whisper large-v3-turbo -> {WHISPER_DIR}/")
    snapshot_download(WHISPER_REPO, local_dir=os.path.join(MODELS, WHISPER_DIR))

    print(f"downloading NLLB-600M int8 -> {NLLB_DIR}/")
    snapshot_download(NLLB_REPO, local_dir=os.path.join(MODELS, NLLB_DIR))

    print(f"downloading IndicConformer-600M -> {INDICCONFORMER_DIR}/ (~2.5 GB)")
    snapshot_download(INDICCONFORMER_REPO,
                      local_dir=os.path.join(MODELS, INDICCONFORMER_DIR))

    piper = os.path.join(MODELS, "piper")
    print(f"downloading piper voices -> piper/")
    for rel, _ in PIPER_FILES:
        hf_hub_download(PIPER_REPO, rel, revision=PIPER_REV, local_dir=piper)

    _download_bergamot()
    _download_indictrans2()

    print("done. Start the real-model stack with:  make local-real")


if __name__ == "__main__":
    main()
