#!/usr/bin/env python3
"""Quantify digit recovery on real-native Hindi clips: beam5 vs beam5+prompt.

Extracts normalized numeric spans (ASCII + Devanagari digits, ignoring spacing
and script) from gold transcripts and hypotheses and reports per-clip
recovery of gold numbers, plus overall WER/CER for each config.

Usage:
    scripts/analyze_digits.py --dirs data/eval/real_native/ekacare_hi,data/eval/real_native/eqourse_hi
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
SCRIPTS = os.path.join(ROOT, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from stts_core.config import ModelConfig  # noqa: E402
from stts_core.medical import MEDICAL_PROMPT  # noqa: E402
import eval_asr  # noqa: E402
import eval_indic_chain as chain  # noqa: E402

DEV = "०१२३४५६७८९"
NUM_RE = re.compile(rf"[0-9{DEV}][0-9{DEV}\s]*[0-9{DEV}]")


def norm_nums(text: str) -> list[str]:
    out = []
    for m in NUM_RE.finditer(text):
        raw = m.group(0)
        s = re.sub(r"\s", "", raw)
        s = s.translate(str.maketrans(DEV, "0123456789"))
        out.append(s)
    return out


def transcribe(asr, wav_path: str, lang: str, beam: int, prompt: str | None) -> str:
    return chain.transcribe(asr, wav_path, lang, beam, prompt)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dirs", required=True, help="comma-separated dirs")
    args = ap.parse_args()

    from stts_core.backends import WhisperASR  # noqa: PLC0415
    asr = WhisperASR(ModelConfig(backend="whisper",
                                 offlinePath=os.path.join(ROOT, "models")))

    configs = [
        ("beam5", 5, None),
        ("beam5+prompt", 5, None),
    ]
    for d in args.dirs.split(","):
        d = d.strip()
        lang = chain.LANG_RE.match(os.path.basename(d))
        lang = lang.group(1) if lang else "??"
        prompt = MEDICAL_PROMPT.get(lang) or MEDICAL_PROMPT["DEFAULT"]
        configs[1] = ("beam5+prompt", 5, prompt)
        wavs = sorted(glob.glob(os.path.join(d, "*.wav")))
        print("=" * 100)
        print(f"{d}  ({lang})  {len(wavs)} clips")
        for name, beam, pr in configs:
            wers, cers, gold_n, hit_n = [], [], 0, 0
            misses = []
            for wav in wavs:
                cid = os.path.splitext(os.path.basename(wav))[0]
                gold = open(os.path.join(d, cid + ".txt"), encoding="utf-8").read() \
                    .strip().replace("\n", " ")
                hyp = transcribe(asr, wav, lang, beam, pr)
                wers.append(eval_asr.wer(gold, hyp))
                cers.append(chain.cer(gold, hyp))
                gn = norm_nums(gold)
                hn = norm_nums(hyp)
                gold_n += len(gn)
                for g in gn:
                    if g in hn:
                        hit_n += 1
                    else:
                        misses.append((cid, g, gn, hn))
            print(f"  {name:<14} WER {np.mean(wers):.3f}  CER {np.mean(cers):.3f}"
                  f"  | digits: {hit_n}/{gold_n} recovered ({hit_n / max(gold_n, 1):.0%})")
            for cid, g, gn, hn in misses:
                print(f"      {cid}: gold {g}  (hyp digits: {hn or '-'})")


if __name__ == "__main__":
    main()
