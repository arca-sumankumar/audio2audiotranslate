#!/usr/bin/env python3
"""Sweep Whisper STT configs (beam / initial_prompt / hotwords) over real-native dirs.

Same transcription path as eval_indic_chain.py (forced language, VAD,
condition_on_previous_text=False) but runs several configs in one pass and
prints a compact summary table per dir, plus per-clip detail for the best config.

Configs:
    greedy          beam1, no prompt/hotwords
    beam5           beam5, no prompt/hotwords (recorded baseline)
    beam5+prompt    beam5 + native-script medical initial_prompt (medical.py)
    beam5+prompt+hw beam5 + prompt + hotwords
    beam1+prompt+hw beam1 + prompt + hotwords

Usage:
    scripts/tune_indic_stt.py --dirs data/eval/real_native/ekacare_hi
    scripts/tune_indic_stt.py --dirs data/eval/real_native/ekacare_hi,data/eval/real_native/eqourse_hi
"""
from __future__ import annotations

import argparse
import glob
import os
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
from stts_core.medical import MEDICAL_PROMPT, MEDICAL_HOTWORDS, NUMERIC_HOTWORDS  # noqa: E402
import eval_asr  # noqa: E402
import eval_indic_chain as chain  # noqa: E402  (decode_to_float32, cer)

CONFIGS = [
    {"name": "greedy",          "beam": 1},
    {"name": "beam5",           "beam": 5},
    {"name": "beam5+prompt",    "beam": 5, "prompt": True},
    {"name": "beam5+prompt+hw", "beam": 5, "prompt": True, "hotwords": True},
    {"name": "beam1+prompt+hw", "beam": 1, "prompt": True, "hotwords": True},
]

LANG_RE = chain.LANG_RE


def transcribe(asr, wav_path: str, lang: str, cfg: dict) -> str:
    prompt = None
    hotwords = None
    if cfg.get("prompt"):
        prompt = MEDICAL_PROMPT.get(lang) or MEDICAL_PROMPT["DEFAULT"]
    if cfg.get("hotwords"):
        base = MEDICAL_HOTWORDS.get(lang) or MEDICAL_HOTWORDS["DEFAULT"]
        hotwords = f"{base}, {NUMERIC_HOTWORDS}"
    return chain.transcribe(asr, wav_path, lang, cfg["beam"], prompt, hotwords)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dirs", required=True,
                    help="comma-separated dirs with *.wav + *.txt")
    ap.add_argument("--max", type=int, default=0, help="limit to first N files (0=all)")
    args = ap.parse_args()

    from stts_core.backends import WhisperASR  # noqa: PLC0415
    asr = WhisperASR(ModelConfig(backend="whisper",
                                 offlinePath=os.path.join(ROOT, "models")))
    t0 = time.time()

    for d in args.dirs.split(","):
        d = d.strip()
        lang = chain.LANG_RE.match(os.path.basename(d))
        lang = lang.group(1) if lang else "??"
        wavs = sorted(glob.glob(os.path.join(d, "*.wav")))
        if args.max:
            wavs = wavs[:args.max]
        if not wavs:
            print(f"[{d}] no *.wav")
            continue
        print("=" * 100)
        print(f"{d}  ({lang})  {len(wavs)} clips")
        results = {}
        for cfg in CONFIGS:
            wers, cers = [], []
            for wav in wavs:
                cid = os.path.splitext(os.path.basename(wav))[0]
                gold = open(os.path.join(d, cid + ".txt"), encoding="utf-8").read() \
                    .strip().replace("\n", " ")
                hyp = transcribe(asr, wav, lang, cfg)
                wers.append(eval_asr.wer(gold, hyp))
                cers.append(chain.cer(gold, hyp))
            results[cfg["name"]] = (wers, cers)
            print(f"  {cfg['name']:<18} mean WER {np.mean(wers):.3f}  "
                  f"mean CER {np.mean(cers):.3f}")
        best = min(results, key=lambda n: np.mean(results[n][0]))
        print(f"  -> best: {best}")
        wers, cers = results[best]
        for wav, w, c in zip(wavs, wers, cers):
            cid = os.path.splitext(os.path.basename(wav))[0]
            gold = open(os.path.join(d, cid + ".txt"), encoding="utf-8").read() \
                .strip().replace("\n", " ")
            hyp = transcribe(asr, wav, lang, CONFIGS[list(results).index(best)])
            print(f"    {cid}  WER {w:.3f} CER {c:.3f}")
            print(f"      GOLD : {gold}")
            print(f"      STT  : {hyp}")

    print("=" * 100)
    print(f"tune_indic_stt done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
