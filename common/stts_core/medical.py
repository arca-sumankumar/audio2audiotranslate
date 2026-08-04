"""Medical-domain context for the Whisper ASR backend.

faster-whisper 1.2.1 exposes two prompt mechanisms that bias decoding:

- ``initial_prompt``: the domain instruction placed at the front of whisper's
  conditioning context (the "what kind of text is this" cue). The audio in
  this project's corpus code-switches between an Indian language and English
  medical terms, so the prompts embed the English terms inside the native
  script to anchor whisper to the right language while covering the terms.

- ``hotwords``: a comma-separated vocabulary, encoded directly after
  ``<|sot_prev|>`` so the model strongly attends to those tokens. This is the
  lever for drug names, symptoms and units, which whisper otherwise mangles or
  drops inside Indian-language audio.

Both share whisper's 224-token context window (the library truncates each to
half), so keep the lists modest.

Anti-hallucination tuning is deliberately conservative: only
``hallucination_silence_threshold`` is tightened (it drops decoded segments
that fall in VAD-detected silence); the confidence thresholds stay at
faster-whisper defaults so legitimate low-confidence Indian-language words are
not dropped.

Every value can be overridden at runtime via ``STTS_ASR_*`` env vars (see
``WhisperASR`` in ``backends.py``).
"""
from __future__ import annotations

import os

# Per-language initial_prompt. The Indian-language prompts are written in the
# native script with the spoken English medical terms embedded, mirroring the
# code-switched audio.
MEDICAL_PROMPT: dict[str, str] = {
    "en": (
        "This is a doctor-patient conversation in a clinic. The patient "
        "describes symptoms, medications, dosages, and test results. "
        "Transcribe all numbers, temperatures, blood-pressure readings, "
        "dosages, and medicine names exactly as spoken."
    ),
    "ml": (
        "ഇത് ഒരു ക്ലിനിക്കിലെ ഡോക്ടർ-രോഗി സംഭാഷണമാണ്. രോഗി ലക്ഷണങ്ങൾ, "
        "മരുന്നുകൾ, ഡോസേജ്, ടെസ്റ്റ് ഫലങ്ങൾ എന്നിവ വിവരിക്കുന്നു. "
        "temperature, fever, headache, blood pressure, painkiller, tablet, "
        "prescription, medicine, X-ray എന്നീ ഇംഗ്ലീഷ് വാക്കുകളും എല്ലാ "
        "സംഖ്യകളും കൃത്യമായി ട്രാൻസ്ക്രൈബ് ചെയ്യുക."
    ),
    "hi": (
        "यह एक क्लिनिक में डॉक्टर-मरीज़ की बातचीत है। मरीज़ लक्षण, दवाइयाँ, "
        "डोज़ और जाँच के नतीजे बताता है। temperature, fever, headache, blood "
        "pressure, painkiller, tablet, prescription, medicine, X-ray जैसे "
        "अंग्रेज़ी शब्दों और सभी संख्याओं को सही-सही ट्रांसक्राइब करें।"
    ),
    "DEFAULT": (
        "This is a doctor-patient conversation in a clinic. The patient "
        "describes symptoms, medications, dosages, and test results. "
        "Transcribe all numbers, temperatures, blood-pressure readings, "
        "dosages, and medicine names exactly as spoken."
    ),
}

# Per-language hotwords: corpus terms plus drugs patients commonly name.
# English terms are used for all languages because the medical vocabulary is
# spoken in English inside the Indian-language audio. Literal numbers are not
# listed (ineffective as hotwords); numbers are steered by the prompt and the
# unit words below.
MEDICAL_HOTWORDS: dict[str, str] = {
    "en": (
        "fever, temperature, headache, blood pressure, BP, migraine, "
        "painkiller, tablet, prescription, medicine, X-ray, muscle strain, "
        "malaria, cough, cold, phlegm, asthma, allergy, chest infection, "
        "cough syrup, gastritis, antacid, antibiotic, stomach, joint pain, "
        "back pain, numbness, weakness, degrees, twice a day, paracetamol, "
        "ibuprofen, amoxicillin, cetirizine, aspirin, metformin, "
        "pantoprazole, amlodipine"
    ),
    "ml": (
        "fever, temperature, headache, blood pressure, BP, migraine, "
        "painkiller, tablet, prescription, medicine, X-ray, muscle strain, "
        "malaria, cough, cold, phlegm, asthma, allergy, chest infection, "
        "cough syrup, gastritis, antacid, antibiotic, stomach, joint pain, "
        "back pain, numbness, weakness, degrees, twice a day, paracetamol, "
        "ibuprofen, amoxicillin, cetirizine, aspirin"
    ),
    "DEFAULT": (
        "fever, temperature, headache, blood pressure, BP, migraine, "
        "painkiller, tablet, prescription, medicine, X-ray, muscle strain, "
        "malaria, cough, cold, phlegm, asthma, allergy, chest infection, "
        "cough syrup, gastritis, antacid, antibiotic, stomach, joint pain, "
        "back pain, numbness, weakness, degrees, twice a day, paracetamol, "
        "ibuprofen, amoxicillin, cetirizine, aspirin, metformin, "
        "pantoprazole, amlodipine"
    ),
}

# Anti-hallucination tuning (conservative). Only silence-rescoring is enabled;
# the confidence thresholds stay at faster-whisper defaults.
HALLUCINATION_SILENCE_THRESHOLD: float = 0.7

# Words (units / quantities) the numbers refer to; kept as hotwords because
# numbers themselves are not useful as hotword tokens.
NUMERIC_HOTWORDS: str = "degrees, milligram, tablet, twice a day, once a day"

# Languages for which the hotword injection is DISABLED. Measured on real
# native audio (docs/REAL_NATIVE_EVAL.md §4.3), the `sot_prev` hotword prompt
# biases Whisper toward English terms and hurts WER for Hindi
# (0.638 -> 0.792 on clinical audio), so it is turned off there while the
# native-script initial_prompt stays on.
HOTWORDS_DISABLED: frozenset[str] = frozenset({"hi"})


def _env(name: str, default: str | None) -> str | None:
    value = os.environ.get(name)
    return value if value else default


def prompt_for(source_lang: str | None) -> str | None:
    """Return the medical-domain initial_prompt for a language (None to skip).

    Prompts are only applied when the source language is known: injecting one
    while auto-detecting would bias language selection.
    """
    if source_lang is None:
        return None
    override = _env("STTS_ASR_INITIAL_PROMPT", None)
    if override:
        return override
    return MEDICAL_PROMPT.get(source_lang) or MEDICAL_PROMPT["DEFAULT"]


def hotwords_for(source_lang: str | None) -> str | None:
    """Return the comma-separated hotword list for a language (None to skip)."""
    if source_lang is None:
        return None
    override = _env("STTS_ASR_HOTWORDS", None)
    if override:
        return override
    if source_lang in HOTWORDS_DISABLED:
        return None
    base = MEDICAL_HOTWORDS.get(source_lang) or MEDICAL_HOTWORDS["DEFAULT"]
    return f"{base}, {NUMERIC_HOTWORDS}"


def hallucination_silence_threshold() -> float | None:
    """Return the tuned hallucination_silence_threshold (None = disabled)."""
    value = os.environ.get("STTS_ASR_HALLUCINATION_SILENCE_THRESHOLD")
    if value is not None:
        try:
            return float(value)
        except ValueError:
            return HALLUCINATION_SILENCE_THRESHOLD
    return HALLUCINATION_SILENCE_THRESHOLD
