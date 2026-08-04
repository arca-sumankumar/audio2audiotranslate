#!/usr/bin/env python3
"""Evaluate Whisper ASR against the gold-standard "Simulated patient-physician
medical interviews" dataset (downloaded by download_eval_dataset.py).

For each case directory ``data/eval/simulated_doctor_patient/<ID>/`` containing
``audio.mp3`` + ``transcript.txt`` (D:/P: labeled gold text) it:

- decodes the MP3 to 16 kHz mono WAV (ffmpeg, cached as ``audio.wav``),
- transcribes the FULL file once as a plain baseline and once with the
  medical-domain context (initial_prompt + hotwords + silence guard) using the
  exact same code path as scripts/eval_asr.py,
- reports WER and CER vs the full gold transcript, plus glossary hit-rate and
  digit accuracy, then a summary table.

Usage:
    scripts/eval_asr_dataset.py CAR0004              # one case
    scripts/eval_asr_dataset.py CAR0004 MSK0008     # several
    scripts/eval_asr_dataset.py --all                # every downloaded case
    scripts/eval_asr_dataset.py --all --beam 1       # greedy decode
    scripts/eval_asr_dataset.py --all --no-context   # baseline only (2x faster)
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import subprocess
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
SCRIPTS = os.path.join(ROOT, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from stts_core.config import ModelConfig  # noqa: E402

import eval_asr  # noqa: E402  (reuse transcribe(), wer(), numbers_in())

CASES_DIR = os.path.join(ROOT, "data", "eval", "simulated_doctor_patient")

GLOSSARY = eval_asr.GLOSSARY + [
    "chest pain", "shortness of breath", "heart attack", "sharp pain",
    "palpitations", "swelling", "rash", "dizziness", "nausea", "vomiting",
    "diarrhea", "constipation", "sore throat", "runny nose", "wheeze",
    "dyspnea", "bronchitis", "pneumonia", "antibiotic", "inhaler",
    "urinary", "dull", "constant", "intermittent",
]


def cer(ref: str, hyp: str) -> float:
    a, b = list(ref), list(hyp)
    if not a:
        return 0.0 if not b else 1.0
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, y in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y))
        prev = cur
    return prev[-1] / len(a)


def gold_text(txt_path: str) -> str:
    """Strip 'D:'/'P:' speaker labels, join lines, collapse whitespace."""
    words: list[str] = []
    for line in open(txt_path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^[DP]:\s*(.*)$", line)
        words.append((m.group(1) if m else line).strip())
    return " ".join(w for w in words if w)


def decode_mp3_to_wav(mp3_path: str, wav_path: str) -> str:
    if not os.path.isfile(wav_path):
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-i", mp3_path, "-ar", "16000", "-ac", "1", wav_path],
            check=True)
    return wav_path


def glossary_stats(source: str, hyp: str) -> tuple[int, int]:
    s, h = source.lower(), hyp.lower()
    hit = total = 0
    for term in GLOSSARY:
        if term in s:
            total += 1
            if term in h:
                hit += 1
    return hit, total


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cases", nargs="*", help="case IDs to evaluate")
    ap.add_argument("--all", action="store_true", help="evaluate every case dir")
    ap.add_argument("--beam", type=int, default=5, help="beam size (default 5)")
    ap.add_argument("--no-context", action="store_true",
                    help="skip the +medical-context run (baseline only)")
    ap.add_argument("--dir", default=CASES_DIR, help=f"cases dir (default {CASES_DIR})")
    args = ap.parse_args()

    if args.all:
        case_dirs = sorted(glob.glob(os.path.join(args.dir, "*")))
        case_dirs = [d for d in case_dirs if os.path.isdir(d)]
    else:
        case_dirs = [os.path.join(args.dir, c) for c in args.cases]
        missing = [d for d in case_dirs if not os.path.isdir(d)]
        if missing:
            sys.exit("no such case dirs: " + ", ".join(missing))
    if not case_dirs:
        sys.exit(f"no case dirs under {args.dir} (run download_eval_dataset.py first)")

    from stts_core.backends import WhisperASR  # noqa: PLC0415
    asr = WhisperASR(ModelConfig(backend="whisper", offlinePath=os.path.join(ROOT, "models")))

    tot = {"wer_b": [], "wer_c": [], "cer_b": [], "cer_c": [],
           "gloss": [0, 0], "num": [0, 0, 0]}
    t0 = time.time()
    for case_dir in case_dirs:
        cid = os.path.basename(case_dir)
        wav_path = decode_mp3_to_wav(os.path.join(case_dir, "audio.mp3"),
                                     os.path.join(case_dir, "audio.wav"))
        source = gold_text(os.path.join(case_dir, "transcript.txt"))
        dur = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", wav_path],
            capture_output=True, text=True).stdout.strip()
        print("=" * 80)
        print(f"{cid}  (audio {float(dur):.0f}s, {len(source.split())} gold words)")

        base = eval_asr.transcribe(asr, wav_path, "en", args.beam, use_context=False)
        print("-" * 80)
        print("GOLD     :", source)
        print(f"BASELINE : {base}   [gloss {glossary_stats(source, base)[0]}/{glossary_stats(source, base)[1]}]")

        wb, cb = eval_asr.wer(source, base), cer(source, base)
        tot["wer_b"].append(wb)
        tot["cer_b"].append(cb)
        src_nums = eval_asr.numbers_in(source)
        b_missing = [n for n in src_nums if n not in eval_asr.numbers_in(base)]
        tot["num"][0] += len(src_nums)
        tot["num"][1] += len(b_missing)

        if not args.no_context:
            ctx = eval_asr.transcribe(asr, wav_path, "en", args.beam, use_context=True)
            print(f"+CONTEXT : {ctx}   [gloss {glossary_stats(source, ctx)[0]}/{glossary_stats(source, ctx)[1]}]")
            wc, cc = eval_asr.wer(source, ctx), cer(source, ctx)
            tot["wer_c"].append(wc)
            tot["cer_c"].append(cc)
            c_missing = [n for n in src_nums if n not in eval_asr.numbers_in(ctx)]
            tot["num"][2] += len(c_missing)
            print(f"WER        : baseline {wb:.3f}  +context {wc:.3f}  (delta {wc - wb:+.3f})")
            print(f"CER        : baseline {cb:.3f}  +context {cc:.3f}")
            gh, gt = glossary_stats(source, ctx)
            tot["gloss"][0] += gh
            tot["gloss"][1] += gt
        else:
            gh, gt = glossary_stats(source, base)
            tot["gloss"][0] += gh
            tot["gloss"][1] += gt
            print(f"WER        : baseline {wb:.3f}")
            print(f"CER        : baseline {cb:.3f}")

    print("=" * 80)
    n = len(case_dirs)
    if tot["wer_b"]:
        print(f"cases={n}  mean WER: baseline {np.mean(tot['wer_b']):.3f}"
              + (f"  +context {np.mean(tot['wer_c']):.3f}"
                 f"  (delta {np.mean(tot['wer_c']) - np.mean(tot['wer_b']):+.3f})"
                 if tot["wer_c"] else ""))
    if tot["cer_b"]:
        print(f"cases={n}  mean CER: baseline {np.mean(tot['cer_b']):.3f}"
              + (f"  +context {np.mean(tot['cer_c']):.3f}" if tot["cer_c"] else ""))
    if tot["gloss"][1]:
        print(f"glossary terms kept: {tot['gloss'][0]}/{tot['gloss'][1]}")
    if tot["num"][0]:
        print(f"digits in gold: {tot['num'][0]}  missing (baseline/context): "
              f"{tot['num'][1]}/{tot['num'][2]}")
    print(f"evaluated {n} cases in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
