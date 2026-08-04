"""STTS demo service - lightweight Flask UI + REST proxy.

Serves the browser demo UI and provides two helpers:
- ``POST /api/translate``  - accepts a multipart file upload, stores it in the
  shared ``/data`` volume and submits a batch job to the ingest service.
- ``GET  /api/jobs/<id>``  - proxies job status from the gateway.

The browser WebSocket (streaming demo) connects DIRECTLY to the ingest
service (no proxy needed), so this app stays dependency-light.
"""
from __future__ import annotations

import logging
import os
import re
import uuid
from typing import Optional

import requests
from flask import Flask, jsonify, request, send_from_directory

from stts_core.models import MT_MODELS

log = logging.getLogger("stts.demo")

INGEST = os.environ.get("STTS_INGEST_URL", "http://ingest:50010")
GATEWAY = os.environ.get("STTS_GATEWAY_URL", "http://gateway:51000")
UPLOAD_DIR = os.environ.get("STTS_UPLOAD_DIR", "/data/uploads")
TEST_AUDIO_DIR = os.environ.get("STTS_TEST_AUDIO_DIR", "/data/test_audio")
PORT = int(os.environ.get("PORT", "50060"))

ALLOWED_FORMATS = {"wav", "mp3"}

app = Flask(__name__, static_folder="static")


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/health")
def health():
    return jsonify(status="ok")


@app.get("/api/config")
def config():
    """Frontend needs the ingest/gateway host ports for WS + polling."""
    return jsonify(
        {
            "ingestPort": 50010,
            "gatewayPort": 51000,
            "sampleRate": 16000,
            "chunkMs": 300,
            "allowedFormats": sorted(ALLOWED_FORMATS),
            "mtModels": [
                {
                    "id": m.id,
                    "label": m.label,
                    "languages": sorted(m.languages),
                    "license": m.license,
                    "note": m.note,
                }
                for m in MT_MODELS
            ],
        }
    )


@app.get("/api/test_audio")
def test_audio():
    """List the demo doctor-patient test WAVs available in the shared volume."""
    if not os.path.isdir(TEST_AUDIO_DIR):
        return jsonify(files=[])
    files = []
    for name in sorted(os.listdir(TEST_AUDIO_DIR)):
        if name.endswith(".wav"):
            stem = name[:-4]
            m = re.match(r"^\d+_([a-z]{2})_", stem)
            src = m.group(1) if m else "en"
            symptom = re.sub(r"^\d+_(?:[a-z]{2}_)?", "", stem)
            prefix = "ms" if src == "ml" else src
            files.append({
                "name": stem,
                "sourceLanguage": src,
                "symptom": symptom,
                "label": f"{prefix}-{symptom}",
                "hasTranscript": os.path.isfile(
                    os.path.join(TEST_AUDIO_DIR, stem + ".txt")),
            })
    return jsonify(files=files)


def _test_audio_path(name: str, ext: str) -> str | None:
    path = os.path.abspath(os.path.join(TEST_AUDIO_DIR, name + "." + ext))
    if not path.startswith(os.path.abspath(TEST_AUDIO_DIR) + os.sep):
        return None
    return path if os.path.isfile(path) else None


@app.get("/api/test_audio/<name>/audio")
def test_audio_file(name: str):
    path = _test_audio_path(name, "wav")
    if path is None:
        return jsonify(error="not found"), 404
    return send_from_directory(TEST_AUDIO_DIR, name + ".wav", mimetype="audio/wav")


@app.get("/api/test_audio/<name>/transcript")
def test_audio_transcript(name: str):
    path = _test_audio_path(name, "txt")
    if path is None:
        return jsonify(error="not found"), 404
    return send_from_directory(TEST_AUDIO_DIR, name + ".txt", mimetype="text/plain")


@app.post("/api/translate")
def translate():
    preset = request.form.get("preset", "").strip()
    if preset:
        path = _test_audio_path(preset, "wav")
        if path is None:
            return jsonify(error=f"unknown test audio '{preset}'"), 400
        ext = "wav"
    else:
        f = request.files.get("file")
        if f is None or not f.filename:
            return jsonify(error="no file uploaded"), 400

        ext = os.path.splitext(f.filename)[1].lower().lstrip(".")
        if ext not in ALLOWED_FORMATS:
            return jsonify(error=f"unsupported format '{ext}', allowed: {sorted(ALLOWED_FORMATS)}"), 400

        os.makedirs(UPLOAD_DIR, exist_ok=True)
        path = os.path.join(UPLOAD_DIR, f"{uuid.uuid4()}.{ext}")
        f.save(path)

    body = {
        "filePath": path,
        "fileFormat": ext,
        "sourceLanguage": request.form.get("sourceLanguage")
            or (m.group(1) if preset and (m := re.match(r"^\d+_([a-z]{2})_", preset)) else "en"),
        "targetLanguage": request.form.get("targetLanguage", "hi"),
        "model": request.form.get("model", ""),
    }
    try:
        r = requests.post(f"{INGEST}/api/v1/translate", json=body, timeout=30)
    except requests.RequestException as exc:
        log.warning("ingest unreachable: %s", exc)
        return jsonify(error="ingest service unavailable"), 502
    try:
        return jsonify(r.json()), r.status_code
    except (ValueError, requests.RequestException):
        return jsonify(error=f"ingest returned HTTP {r.status_code}: {r.text[:200]}"), r.status_code


@app.get("/api/jobs/<job_id>")
def job(job_id: str):
    try:
        r = requests.get(f"{GATEWAY}/api/v1/jobs/{job_id}", timeout=10)
    except requests.RequestException as exc:
        log.warning("gateway unreachable: %s", exc)
        return jsonify(error="gateway service unavailable"), 502
    return jsonify(r.json()), r.status_code


@app.get("/api/jobs/<job_id>/audio")
def job_audio(job_id: str):
    """Serve the batch output audio straight from the shared /data volume."""
    try:
        r = requests.get(f"{GATEWAY}/api/v1/jobs/{job_id}", timeout=10)
    except requests.RequestException as exc:
        log.warning("gateway unreachable: %s", exc)
        return jsonify(error="gateway service unavailable"), 502
    info = r.json()
    path = info.get("outputPath") or info.get("output_path")
    if not path or not os.path.isfile(path):
        return jsonify(error="no output audio yet"), 404
    return send_from_directory(os.path.dirname(path), os.path.basename(path),
                               mimetype="audio/wav")


if __name__ == "__main__":
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    app.run(host="0.0.0.0", port=PORT)
