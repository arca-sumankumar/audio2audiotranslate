#!/usr/bin/env python3
"""Synthesize demo audio from the doctor-patient test transcripts in
``data/test_audio/*.txt`` using the offline Piper voices, so the files can
be used as batch translation input.

The source language is taken from the filename when it matches
``NN_<lang>_<symptom>`` (e.g. ``06_ml_fever`` -> Malayalam); otherwise it
defaults to ``en``. Files whose language has no Piper voice installed
(e.g. Gujarati) are skipped with a warning. Writes a 16 kHz mono WAV next
to each .txt. Requires ``make models``.
"""
from __future__ import annotations

import glob
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from stts_core.backends import PiperTTS  # noqa: E402
from stts_core.config import ModelConfig  # noqa: E402

AUDIO_DIR = os.path.join(ROOT, "data", "test_audio")
MODELS_DIR = os.path.join(ROOT, "models")

LANG_RE = re.compile(r"^\d+_([a-z]{2})_")


def lang_of(name: str) -> str:
    m = LANG_RE.match(name)
    return m.group(1) if m else "en"


def main() -> None:
    texts = sorted(glob.glob(os.path.join(AUDIO_DIR, "*.txt")))
    if not texts:
        sys.exit(f"no *.txt transcripts found under {AUDIO_DIR}")
    tts = PiperTTS(ModelConfig(backend="piper", offlinePath=MODELS_DIR))
    made, skipped = 0, []
    for txt in texts:
        name = os.path.splitext(os.path.basename(txt))[0]
        lang = lang_of(name)
        if tts._voice_for(lang) is None:
            skipped.append(f"{name} ({lang})")
            continue
        text = open(txt, encoding="utf-8").read().strip()
        result = tts.synthesize(lang, text, 0, 0)
        out = os.path.join(AUDIO_DIR, name + ".wav")
        with open(out, "wb") as f:
            f.write(result.data)
        made += 1
        print(f"{name}.wav: {lang} {len(text)} chars -> {len(result.data)} bytes WAV")
    if skipped:
        print(f"skipped (no piper voice, record audio instead): {', '.join(skipped)}")
    print(f"{made} wav files written to {AUDIO_DIR}")


if __name__ == "__main__":
    main()
