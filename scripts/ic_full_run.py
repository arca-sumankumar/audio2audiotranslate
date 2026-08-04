#!/usr/bin/env python3
"""Full IndicConformer sweep across real-native dirs, both decodes, all clips."""
from __future__ import annotations

import glob
import os
import sys
import wave

import numpy as np
import torch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "models", "indic-conformer-600m"))

from model_onnx import IndicASRConfig, IndicASRModel  # noqa: E402
from eval_indic_chain import LANG_RE  # noqa: E402

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


def main() -> None:
    model = IndicASRModel(IndicASRConfig(ts_folder=os.path.join(ROOT, "models",
                                                                "indic-conformer-600m")))
    print("model loaded", flush=True)
    for d in ["eqourse_gu", "eqourse_ml", "eqourse_mr", "eqourse_hi", "ekacare_hi"]:
        base = os.path.join(ROOT, "data", "eval", "real_native", d)
        lang = LANG_RE.match(d).group(1)
        wavs = sorted(glob.glob(os.path.join(base, "*.wav")))
        print("=" * 100)
        print(f"{d}  ({lang})  {len(wavs)} clips", flush=True)
        for dec in ("ctc", "rnnt"):
            n_hall = 0
            print(f"--- decode={dec} ---", flush=True)
            for wav in wavs:
                cid = os.path.splitext(os.path.basename(wav))[0]
                gold = open(os.path.join(base, cid + ".txt"), encoding="utf-8") \
                    .read().strip().replace("\n", " ")
                with wave.open(wav, "rb") as w:
                    frames = w.readframes(w.getnframes())
                    dur = w.getnframes() / w.getframerate()
                audio = torch.from_numpy(
                    np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
                )[None, :]
                try:
                    hyp = model(audio, lang, dec)
                    hyp = hyp.strip() if isinstance(hyp, str) else str(hyp).strip()
                except Exception as e:  # noqa: BLE001
                    hyp = f"<ERR {type(e).__name__}: {e}>"
                hall, why = classify(gold, hyp, 0.1)
                if hall:
                    n_hall += 1
                    tag = f"HALL({why})"
                else:
                    tag = "ok"
                print(f"  [{tag}] {cid} ({dur:.1f}s)")
                print(f"    GOLD: {gold}")
                print(f"    STT : {hyp}", flush=True)
            print(f"  => {d} {dec}: hallucinated {n_hall}/{len(wavs)} "
                  f"({n_hall / len(wavs):.0%})", flush=True)


if __name__ == "__main__":
    main()
