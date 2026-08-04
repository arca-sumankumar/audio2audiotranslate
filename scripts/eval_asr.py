#!/usr/bin/env python3
"""Measure Whisper ASR quality on the doctor-patient test corpus, with and
without the medical-domain context (initial_prompt + hotwords + silence
guard).

For every ``data/test_audio/*.wav`` it transcribes the full file once with
the medical context and once as a plain baseline, then reports per-file and
per-session:

- glossary hit-rate: of the English medical terms present in the source
  transcript, how many survive into the hypothesis (the demo audio code-
  switches to English for these terms, so they should appear verbatim);
- numeric accuracy: of the digit-sequences in the source, how many appear in
  the hypothesis (numbers are spoken as digits in the Malayalam clips);
- WER (English files only): word error rate vs the source transcript.

Usage:
    scripts/eval_asr.py                 # all files
    scripts/eval_asr.py ml              # only files whose name matches 'ml'
    scripts/eval_asr.py --beam 1        # greedy decode (faster)
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from stts_core.audio import decode_wav  # noqa: E402
from stts_core import medical  # noqa: E402
from stts_core.backends import PIPELINE_RATE  # noqa: E402
from stts_core.config import ModelConfig  # noqa: E402

AUDIO_DIR = os.path.join(ROOT, "data", "test_audio")
LANG_RE = re.compile(r"^\d+_([a-z]{2})_")

GLOSSARY = [
    "fever", "temperature", "headache", "blood pressure", "bp", "migraine",
    "painkiller", "tablet", "prescription", "medicine", "x-ray", "x ray",
    "muscle strain", "malaria", "cough", "cold", "phlegm", "asthma",
    "allergy", "chest infection", "cough syrup", "gastritis", "antacid",
    "antibiotic", "stomach", "joint pain", "back pain", "numbness",
    "weakness", "degrees",
]


def lang_of(name: str) -> str:
    m = LANG_RE.match(name)
    return m.group(1) if m else "en"


def wer(ref: str, hyp: str) -> float:
    a, b = ref.split(), hyp.split()
    if not a:
        return 0.0 if not b else 1.0
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, y in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y))
        prev = cur
    return prev[-1] / len(a)


def numbers_in(text: str) -> list[str]:
    return re.findall(r"\d+", text)


def glossary_stats(source: str, hyp: str) -> tuple[int, int]:
    s, h = source.lower(), hyp.lower()
    hit = total = 0
    for term in GLOSSARY:
        if term in s:
            total += 1
            if term in h:
                hit += 1
    return hit, total


def transcribe(asr, wav_path: str, src: str, beam: int, use_context: bool):
    pcm, _, _ = decode_wav(open(wav_path, "rb").read())
    audio = np.asarray([x / 32768.0 for x in np.frombuffer(pcm, dtype=np.int16)],
                       dtype=np.float32)
    kw = dict(
        language=src,
        beam_size=beam,
        vad_filter=True,
        no_repeat_ngram_size=3,
        condition_on_previous_text=False,
    )
    if use_context:
        kw["initial_prompt"] = medical.prompt_for(src)
        kw["hotwords"] = medical.hotwords_for(src)
        kw["hallucination_silence_threshold"] = (
            medical.hallucination_silence_threshold())
    segments, _ = asr._load().transcribe(audio, **kw)
    return " ".join(seg.text.strip() for seg in segments).strip()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("filter", nargs="?", default="",
                    help="only files whose name contains this (e.g. 'ml')")
    ap.add_argument("--beam", type=int, default=5, help="beam size (default 5)")
    args = ap.parse_args()

    wavs = sorted(glob.glob(os.path.join(AUDIO_DIR, "*.wav")))
    if args.filter:
        wavs = [w for w in wavs
                if args.filter in lang_of(os.path.splitext(os.path.basename(w))[0])]
    if not wavs:
        sys.exit(f"no .wav under {AUDIO_DIR} with language '{args.filter}'")

    from stts_core.backends import WhisperASR  # noqa: PLC0415
    asr = WhisperASR(ModelConfig(backend="whisper", offlinePath=os.path.join(ROOT, "models")))

    tot = {"gloss": [0, 0, 0], "num": [0, 0, 0], "wer": [0.0, 0]}
    t0 = time.time()
    for wav in wavs:
        name = os.path.splitext(os.path.basename(wav))[0]
        src = lang_of(name)
        txt = os.path.join(AUDIO_DIR, name + ".txt")
        source = open(txt, encoding="utf-8").read().strip().replace("\n", " ") \
            if os.path.isfile(txt) else "(no .txt)"

        base = transcribe(asr, wav, src, args.beam, use_context=False)
        ctx = transcribe(asr, wav, src, args.beam, use_context=True)

        b_hit, b_tot = glossary_stats(source, base)
        c_hit, c_tot = glossary_stats(source, ctx)
        tot["gloss"][0] += c_hit - b_hit
        tot["gloss"][1] += c_tot
        tot["gloss"][2] += b_hit

        src_nums = numbers_in(source)
        b_missing = [n for n in src_nums if n not in numbers_in(base)]
        c_missing = [n for n in src_nums if n not in numbers_in(ctx)]
        tot["num"][0] += len(c_missing)
        tot["num"][1] += len(src_nums)
        tot["num"][2] += len(b_missing)

        print("=" * 80)
        print(f"{name}.wav  ({src})")
        print("-" * 80)
        print("SOURCE     :", source)
        print(f"BASELINE   : {base}   [gloss {b_hit}/{b_tot} missing {b_missing}]")
        print(f"+CONTEXT   : {ctx}   [gloss {c_hit}/{c_tot} missing {c_missing}]")
        if src == "en":
            wb = wer(source, base)
            wc = wer(source, ctx)
            tot["wer"][0] += wc - wb
            tot["wer"][1] += 1
            print(f"WER        : baseline {wb:.3f}  +context {wc:.3f}  "
                  f"(delta {wc - wb:+.3f})")

    print("=" * 80)
    gh, gt, gb = tot["gloss"]   # [delta_kept, total, baseline_kept]
    mh, mt, mb = tot["num"]     # [ctx_missing, total, base_missing]
    wd, wn = tot["wer"]
    print(f"glossary terms kept (baseline -> +context): {gb} -> {gb + gh}"
          f"  of {gt}  (delta {gh:+d})")
    print(f"numbers kept (baseline -> +context): {mt - mb} -> {mt - mh}"
          f"  of {mt}  (delta {mb - mh:+d})")
    if wn:
        print(f"mean WER delta (en): {wd / wn:+.3f}")
    print(f"evaluated {len(wavs)} files in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
