#!/usr/bin/env python3
"""Evaluate the real product chain on REAL native-speech clips:
    native audio (ml/gu/hi/mr) -> STT in native script -> MT to English

For every ``<id>.wav`` in a directory (with a matching ``<id>.txt`` gold
transcript in the native script) it:
- transcribes the full file with Whisper (language forced, no English prompt),
- reports WER/CER vs the native-script gold,
- optionally translates the STT output to English with IndicTrans2 (indic->en)
  and prints it next to the translation of the gold transcript, for review.

Usage:
    scripts/eval_indic_chain.py --dir data/eval/real_native/eqourse_gu
    scripts/eval_indic_chain.py --dir data/eval/real_native/ekacare_hi --no-mt
    scripts/eval_indic_chain.py --dir ... --lang hi --beam 1
The language defaults to the dir suffix (eqourse_gu -> gu, ekacare_hi -> hi).
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
import eval_asr  # noqa: E402  (wer()/cer() are here)

LANG_RE = re.compile(r"(?:eqourse_|ekacare_)([a-z]{2})$")


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


def decode_to_float32(path: str) -> np.ndarray:
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", path,
         "-f", "s16le", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
         "pipe:1"],
        capture_output=True, check=True).stdout
    pcm = np.frombuffer(out, dtype=np.int16)
    return np.asarray(pcm / 32768.0, dtype=np.float32)


def transcribe(asr, wav_path: str, lang: str, beam: int, prompt: str | None = None,
               hotwords: str | None = None, temperature: float | None = None) -> str:
    audio = decode_to_float32(wav_path)
    kw = dict(language=lang, beam_size=beam, vad_filter=True,
              no_repeat_ngram_size=3, condition_on_previous_text=False)
    if prompt:
        kw["initial_prompt"] = prompt
    if hotwords:
        kw["hotwords"] = hotwords
    if temperature is not None:
        kw["temperature"] = temperature
    segments, _ = asr._load().transcribe(audio, **kw)
    return " ".join(seg.text.strip() for seg in segments).strip()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", required=True, help="case dir with *.wav + *.txt")
    ap.add_argument("--lang", default=None, help="ISO code (default from dir name)")
    ap.add_argument("--beam", type=int, default=5, help="beam size (default 5)")
    ap.add_argument("--initial-prompt", default=None,
                    help="Whisper initial_prompt (e.g. native-script Hindi medical cue)")
    ap.add_argument("--hotwords", default=None,
                    help="comma-separated hotwords for decoding")
    ap.add_argument("--temperature", type=float, default=None,
                    help="decoding temperature (default: faster-whisper default)")
    ap.add_argument("--max", type=int, default=0, help="limit to first N files (0=all)")
    ap.add_argument("--no-mt", action="store_true", help="skip MT to English")
    args = ap.parse_args()

    lang = args.lang or LANG_RE.match(os.path.basename(args.dir))
    if not lang:
        sys.exit("could not infer language from dir name; pass --lang")
    if isinstance(lang, re.Match):
        lang = lang.group(1)

    wavs = sorted(glob.glob(os.path.join(args.dir, "*.wav")))
    if args.max:
        wavs = wavs[:args.max]
    if not wavs:
        sys.exit(f"no *.wav under {args.dir}")

    from stts_core.backends import WhisperASR, IndicTrans2MT  # noqa: PLC0415
    asr = WhisperASR(ModelConfig(backend="whisper",
                                 offlinePath=os.path.join(ROOT, "models")))
    mt = None
    if not args.no_mt:
        mt = IndicTrans2MT(ModelConfig(backend="indictrans2",
                                       offlinePath=os.path.join(ROOT, "models")))

    def to_en(text: str) -> str:
        if not text:
            return "(empty)"
        return mt.translate(lang, "en", text, 0, 0, True).text.strip()

    wers, cers = [], []
    t0 = time.time()
    for wav in wavs:
        cid = os.path.splitext(os.path.basename(wav))[0]
        txt = os.path.join(args.dir, cid + ".txt")
        gold = open(txt, encoding="utf-8").read().strip().replace("\n", " ") \
            if os.path.isfile(txt) else "(no .txt)"
        hyp = transcribe(asr, wav, lang, args.beam, args.initial_prompt,
                         args.hotwords, args.temperature)
        w, c = eval_asr.wer(gold, hyp), cer(gold, hyp)
        wers.append(w)
        cers.append(c)
        print("=" * 80)
        print(f"{cid}  ({lang})  WER {w:.3f}  CER {c:.3f}")
        print("-" * 80)
        print("GOLD  :", gold)
        print("STT   :", hyp)
        if mt:
            print("EN(gold):", to_en(gold))
            print("EN(STT) :", to_en(hyp))

    print("=" * 80)
    print(f"{lang}: {len(wers)} clips, mean WER {np.mean(wers):.3f}, "
          f"mean CER {np.mean(cers):.3f}, {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
