#!/usr/bin/env python3
"""Measure the hallucination rate of Whisper STT on real-native clips.

Operational definition of a HALLUCINATED clip (hypothesis H vs gold G):
  HALL = H is empty (decoded nothing)
       OR H contains a 4-gram repeated >= 3 times (repetition loop)
       OR character-bigram Dice similarity(G, H) < ``--sim`` (output unrelated
          to the gold transcript)

Reports the hallucinated fraction per language/dir plus WER/CER, and prints the
flagged clips so the classification can be eyeballed.

Decoding options mirror the product/chain path but expose the faster-whisper
anti-hallucination knobs so we can tune them:
  --no-speech-threshold, --logprob-threshold, --compression-ratio-threshold,
  --temperature / --best-of, --hallucination-silence-threshold, --prompt,
  --hotwords, --beam.

Usage:
    scripts/measure_hallucination.py --dirs data/eval/real_native/ekacare_hi,data/eval/real_native/eqourse_gu
    scripts/measure_hallucination.py --dirs data/eval/real_native/eqourse_ml --no-speech-threshold 0.4 --logprob-threshold -0.5
"""
from __future__ import annotations

import argparse
import glob
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
SCRIPTS = os.path.join(ROOT, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from stts_core.config import ModelConfig  # noqa: E402
from stts_core.medical import (MEDICAL_PROMPT, MEDICAL_HOTWORDS,  # noqa: E402
                               NUMERIC_HOTWORDS, HALLUCINATION_SILENCE_THRESHOLD)
import eval_asr  # noqa: E402
import eval_indic_chain as chain  # noqa: E402

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


def transcribe(asr, wav_path: str, lang: str, args) -> str:
    audio = chain.decode_to_float32(wav_path)
    kw = dict(language=lang, beam_size=args.beam, vad_filter=True,
              no_repeat_ngram_size=3, condition_on_previous_text=False)
    if args.prompt_text is not None:
        kw["initial_prompt"] = args.prompt_text
    elif args.prompt:
        kw["initial_prompt"] = MEDICAL_PROMPT.get(lang) or MEDICAL_PROMPT["DEFAULT"]
    if args.hotwords:
        base = MEDICAL_HOTWORDS.get(lang) or MEDICAL_HOTWORDS["DEFAULT"]
        kw["hotwords"] = f"{base}, {NUMERIC_HOTWORDS}"
    if args.no_speech_threshold is not None:
        kw["no_speech_threshold"] = args.no_speech_threshold
    if args.logprob_threshold is not None:
        kw["logprob_threshold"] = args.logprob_threshold
    if args.compression_ratio_threshold is not None:
        kw["compression_ratio_threshold"] = args.compression_ratio_threshold
    if args.temperature is not None:
        kw["temperature"] = args.temperature
    if args.best_of is not None:
        kw["best_of"] = args.best_of
    if args.hallucination_silence_threshold is not None:
        kw["hallucination_silence_threshold"] = args.hallucination_silence_threshold
    segments, _ = asr._load().transcribe(audio, **kw)
    return " ".join(seg.text.strip() for seg in segments).strip()


def load_asr(model_dir: str | None):
    if model_dir:
        from faster_whisper import WhisperModel
        return type("_W", (), {"_load": lambda self: WhisperModel(
            model_dir, device="cpu", compute_type="int8")})()
    from stts_core.backends import WhisperASR
    return WhisperASR(ModelConfig(backend="whisper",
                                  offlinePath=os.path.join(ROOT, "models")))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dirs", required=True, help="comma-separated clip dirs")
    ap.add_argument("--model-dir", default=None,
                    help="faster-whisper model dir (default: product turbo)")
    ap.add_argument("--beam", type=int, default=5)
    ap.add_argument("--prompt", action="store_true", help="use per-lang medical prompt")
    ap.add_argument("--prompt-text", default=None,
                    help="explicit initial_prompt for every language "
                         "(overrides --prompt)")
    ap.add_argument("--hotwords", action="store_true", help="add hotwords")
    ap.add_argument("--sim", type=float, default=0.1,
                    help="no-overlap dice threshold (default 0.1)")
    ap.add_argument("--no-speech-threshold", type=float, default=None)
    ap.add_argument("--logprob-threshold", type=float, default=None)
    ap.add_argument("--compression-ratio-threshold", type=float, default=None)
    ap.add_argument("--temperature", type=float, default=None)
    ap.add_argument("--best-of", type=int, default=None)
    ap.add_argument("--hallucination-silence-threshold", type=float, default=None)
    args = ap.parse_args()

    asr = load_asr(args.model_dir)

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
        print(f"{d}  ({lang})  {len(wavs)} clips  [beam={args.beam} "
              f"prompt={args.prompt} nst={args.no_speech_threshold} "
              f"lpt={args.logprob_threshold} crt={args.compression_ratio_threshold} "
              f"temp={args.temperature} best_of={args.best_of}]")
        for wav in wavs:
            cid = os.path.splitext(os.path.basename(wav))[0]
            gold = open(os.path.join(d, cid + ".txt"), encoding="utf-8").read() \
                .strip().replace("\n", " ")
            hyp = transcribe(asr, wav, lang, args)
            hall, why = classify(gold, hyp, args.sim)
            wers.append(eval_asr.wer(gold, hyp))
            cers.append(chain.cer(gold, hyp))
            if hall:
                n_hall += 1
                print(f"  HALL {cid}: {why}")
                print(f"    GOLD : {gold}")
                print(f"    STT  : {hyp}")
        print(f"  -> hallucinated {n_hall}/{len(wavs)} "
              f"({n_hall / len(wavs):.0%})  WER {np.mean(wers):.3f}  "
              f"CER {np.mean(cers):.3f}")


if __name__ == "__main__":
    main()
