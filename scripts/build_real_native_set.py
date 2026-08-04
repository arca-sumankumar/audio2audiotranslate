#!/usr/bin/env python3
"""Assemble a small REAL-native-speech doctor-patient/conversational eval set
from public sources (a few minutes per language, no synthetic audio):

eqourse   - eQOURSE/multilingual-speech: real spontaneous two-speaker
            conversations, native-script transcripts, one recording per
            language. We extract every segment for the requested languages.
ekacare   - ekacare/eka-medical-asr-evaluation-dataset (config=hi): REAL
            clinical Hindi recordings with Devanagari(+English) transcripts.

Output layout (consumed by scripts/eval_indic_chain.py)::

    data/eval/real_native/eqourse_<LANG>/<segid>.wav + <segid>.txt
    data/eval/real_native/ekacare_hi/<NNNN>.wav   + <NNNN>.txt

Usage:
    scripts/build_real_native_set.py eqourse --langs gu,ml,mr,hi
    scripts/build_real_native_set.py ekacare --max-clips 20
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_ROOT = os.path.join(ROOT, "data", "eval", "real_native")

EQ_DATASET = "eQOURSE/multilingual-speech"
EK_DATASET = "ekacare/eka-medical-asr-evaluation-dataset"


def fetch_rows(dataset: str, config: str | None, split: str) -> list[dict]:
    rows: list[dict] = []
    offset = 0
    while True:
        url = (f"https://datasets-server.huggingface.co/rows?dataset={dataset}"
               f"&config={config}&split={split}&offset={offset}&length=100")
        with urllib.request.urlopen(url, timeout=60) as r:
            d = json.load(r)
        batch = d.get("rows", [])
        rows.extend(rr["row"] for rr in batch)
        if d.get("truncated") is False or not batch:
            break
        offset += len(batch)
    return rows


def wget(url: str, out: str) -> None:
    with urllib.request.urlopen(url, timeout=300) as r:
        data = r.read()
    with open(out, "wb") as f:
        f.write(data)


def cut(wav_in: str, out: str, start: float, end: float) -> None:
    dur = max(end - start, 0.2)
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", wav_in,
         "-ar", "16000", "-ac", "1", out],
        check=True)


# eQOURSE uses full English language names in metadata and file paths.
LANG_ALIASES = {
    "gu": "Gujarati", "ml": "Malayalam", "mr": "Marathi", "hi": "Hindi",
    "bn": "Bengali", "as": "Assamese", "ta": "Tamil", "kn": "Kannada",
    "te": "Telugu", "pa": "Punjabi", "or": "Odia", "ne": "Nepali", "ur": "Urdu",
}


def build_eqourse(langs: list[str]) -> None:
    rows = fetch_rows(EQ_DATASET, "default", "train")
    total = 0
    for lang in langs:
        name = LANG_ALIASES.get(lang, lang)
        lrows = sorted(
            (r for r in rows if (r.get("language") or "") == name),
            key=lambda r: float(r.get("start_seconds") or 0))
        if not lrows:
            print(f"eqourse: no rows for {lang} ({name})")
            continue
        code = lang if len(lang) == 2 else next(
            (k for k, v in LANG_ALIASES.items() if v.lower() == lang.lower()), lang)
        wav = os.path.join(OUT_ROOT, f"eqourse_{code}.wav")
        os.makedirs(os.path.dirname(wav), exist_ok=True)
        if not os.path.isfile(wav):
            print(f"eqourse: downloading {name} full recording...")
            wget(f"https://huggingface.co/datasets/{EQ_DATASET}/resolve/main/audio/{name}.wav",
                 wav)
        outdir = os.path.join(OUT_ROOT, f"eqourse_{code}")
        os.makedirs(outdir, exist_ok=True)
        for r in lrows:
            segid = r.get("segment_id") or r.get("recording_id")
            segid = segid.replace("/", "_")
            w = os.path.join(outdir, f"{segid}.wav")
            t = os.path.join(outdir, f"{segid}.txt")
            if not os.path.isfile(w):
                cut(wav, w, float(r.get("start_seconds") or 0),
                    float(r.get("end_seconds") or 0))
            if not os.path.isfile(t):
                with open(t, "w", encoding="utf-8") as f:
                    f.write((r.get("transcript") or "").strip() + "\n")
            total += 1
        print(f"eqourse {code} ({name}): {len(lrows)} segments "
              f"({sum(float(r.get('duration_seconds') or 0) for r in lrows):.0f}s) "
              f"-> {outdir}")


def build_ekacare(max_clips: int) -> None:
    rows = fetch_rows(EK_DATASET, "hi", "test")
    outdir = os.path.join(OUT_ROOT, "ekacare_hi")
    os.makedirs(outdir, exist_ok=True)
    n = 0
    for i, r in enumerate(rows[:max_clips]):
        audio = r.get("audio") or []
        src = audio[0]["src"] if audio else None
        if not src:
            continue
        w = os.path.join(outdir, f"{i:04d}.wav")
        t = os.path.join(outdir, f"{i:04d}.txt")
        if not os.path.isfile(w):
            wget(src, w)
        if not os.path.isfile(t):
            with open(t, "w", encoding="utf-8") as f:
                f.write((r.get("text") or "").strip() + "\n")
        n += 1
    dur = sum(float(r.get("duration") or 0) for r in rows[:max_clips])
    print(f"ekacare hi: {n} clips ({dur:.0f}s) -> {outdir}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", choices=["eqourse", "ekacare"])
    ap.add_argument("--langs", default="gu,ml,mr,hi",
                    help="languages for eqourse, ISO codes or full names "
                         "(default gu,ml,mr,hi)")
    ap.add_argument("--max-clips", type=int, default=20,
                    help="clips for ekacare (default 20, ~4 min)")
    args = ap.parse_args()
    if args.source == "eqourse":
        build_eqourse([x.strip() for x in args.langs.split(",") if x.strip()])
    else:
        build_ekacare(args.max_clips)


if __name__ == "__main__":
    main()
