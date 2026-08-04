#!/usr/bin/env python3
"""Measure hallucination rate of IndicConformer-600M on real-native clips.

Uses the same operational hallucination definition as measure_hallucination.py
(empty decode OR 4-gram repeated >=3x OR char-bigram Dice < --sim), so the
numbers are directly comparable to the Whisper baseline.

IndicConformer is 16 kHz. Clips in the real-native eval set are already
16 kHz mono RIFF WAV, so we read them straight into a float32 tensor of shape
(1, N) and skip torchaudio.

Usage:
    scripts/eval_indic_conformer.py --dirs data/eval/real_native/eqourse_gu
    scripts/eval_indic_conformer.py --dirs ... --decode rnnt --sim 0.1
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
import wave

import numpy as np
import torch  # noqa: F401

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
SCRIPTS = os.path.join(ROOT, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

sys.path.insert(0, os.path.join(ROOT, "models", "indic-conformer-600m"))

import eval_asr  # noqa: E402
import eval_indic_chain as chain  # noqa: E402

MODEL_DIR = os.path.join(ROOT, "models", "indic-conformer-600m")
DECODES = ("ctc", "rnnt")

NORM = str.maketrans("", "", " \t\r\n.,!?;:\"'()[]{}" + "\u0970\u0964\u0965")


def norm(s: str) -> str:
    return s.translate(NORM).lower()


def bigrams(s: str) -> list[str]:
    return [s[i:i + 2] for i in range(len(s) - 1)]


def dice(a: str, b: str) -> float:
    ga, gb = bigrams(a), bigrams(b)
    if not ga or not gb:
        return 1.0 if norm(a) == norm(b) else 0.0
    sa, sb = set(ga), set(gb)
    return 2 * len(sa & sb) / (len(sa) + len(sb))


def is_repetition_loop(h: str) -> bool:
    words = h.split()
    if len(words) < 12:
        return False
    seen: dict[tuple, int] = {}
    for i in range(len(words) - 3):
        k = tuple(words[i:i + 4])
        seen[k] = seen.get(k, 0) + 1
    return max(seen.values()) >= 3


def classify(gold: str, hyp: str, sim: float) -> tuple[bool, str]:
    if not hyp:
        return True, "empty"
    if is_repetition_loop(hyp):
        return True, "repetition-loop"
    if dice(norm(gold), norm(hyp)) < sim:
        return True, f"no-overlap (dice {dice(norm(gold), norm(hyp)):.2f})"
    return False, "ok"


def load_wav_tensor(path: str) -> np.ndarray:
    with wave.open(path, "rb") as w:
        assert w.getframerate() == 16000, f"{path}: sr={w.getframerate()}"
        assert w.getnchannels() == 1, f"{path}: ch={w.getnchannels()}"
        frames = w.readframes(w.getnframes())
    audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    return audio[None, :]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dirs", required=True, help="comma-separated clip dirs")
    ap.add_argument("--decode", default="ctc", choices=DECODES)
    ap.add_argument("--sim", type=float, default=0.1)
    args = ap.parse_args()

    from model_onnx import IndicASRConfig, IndicASRModel
    model = IndicASRModel(IndicASRConfig(ts_folder=MODEL_DIR))
    print(f"model loaded from {MODEL_DIR}", flush=True)

    for d in args.dirs.split(","):
        d = d.strip()
        lang = chain.LANG_RE.match(os.path.basename(d))
        lang = lang.group(1) if lang else "??"
        wavs = sorted(glob.glob(os.path.join(d, "*.wav")))
        if not wavs:
            print(f"[{d}] no clips")
            continue
        n_hall = 0
        wers, cers = [], []
        print("=" * 100)
        print(f"{d}  ({lang})  {len(wavs)} clips  [decode={args.decode}]")
        for wav in wavs:
            cid = os.path.splitext(os.path.basename(wav))[0]
            gold = open(os.path.join(d, cid + ".txt"), encoding="utf-8").read() \
                .strip().replace("\n", " ")
            try:
                hyp = model(torch.from_numpy(load_wav_tensor(wav)), lang, args.decode)
                hyp = hyp.strip() if isinstance(hyp, str) else str(hyp).strip()
            except Exception as e:  # noqa: BLE001
                print(f"  ERROR {cid}: {type(e).__name__}: {e}")
                continue
            hall, why = classify(gold, hyp, args.sim)
            wers.append(eval_asr.wer(gold, hyp))
            cers.append(chain.cer(gold, hyp))
            if hall:
                n_hall += 1
                print(f"  HALL {cid}: {why}")
                print(f"    GOLD : {gold}")
                print(f"    STT  : {hyp}")
        if wers:
            print(f"  -> hallucinated {n_hall}/{len(wavs)} "
                  f"({n_hall / len(wavs):.0%})  WER {np.mean(wers):.3f}  "
                  f"CER {np.mean(cers):.3f}")


if __name__ == "__main__":
    main()
