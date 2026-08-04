"""Offline model adapters.

The default backend is ``mock``: deterministic, dependency-free and fully
offline, used to exercise the whole pipeline end to end.

Real offline engines live in :mod:`stts_core.backends` and are selected via
``model.backend``:

- ``whisper`` — faster-whisper (CTranslate2 int8) ASR
- ``nllb`` — CTranslate2 NLLB-200 distilled 600M (int8) MT
- ``piper`` — Piper (ONNX) neural TTS (voices available for en/hi)
"""
from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from stts_core.audio import AudioChunk, synth_tone_wav
from stts_core.config import ModelConfig


@dataclass
class TranscriptResult:
    text: str
    start_ms: int
    end_ms: int
    is_final: bool
    gap: bool = False
    session_end: bool = False  # True on the final segment of a streaming session


@dataclass
class TranslationResult:
    text: str
    start_ms: int
    end_ms: int
    is_final: bool


@dataclass
class TtsResult:
    data: bytes        # wav bytes
    format: str = "wav"
    start_ms: int = 0
    end_ms: int = 0


class ASRBackend(ABC):
    def __init__(self, model_cfg: ModelConfig):
        self.model_cfg = model_cfg

    @abstractmethod
    def transcribe(self, session_id: str, chunk: AudioChunk,
                   batch: bool = False,
                   source_lang: str | None = None) -> list[TranscriptResult]:
        """Transcribe a chunk, returning the pipeline events to emit.

        ``batch=True`` means the gateway aggregates the job transcript from
        the emitted text, so backends should emit text only on the final
        segment to avoid duplication.

        ``source_lang`` is the caller-known source language (ISO-639-1) when
        available; backends may use it to guide ASR instead of auto-detect.

        Returns a *list* because a single streaming chunk can produce both a
        newly-confirmed ``final`` segment and the live ``partial`` tail.
        """
        ...


class MTBackend(ABC):
    def __init__(self, model_cfg: ModelConfig):
        self.model_cfg = model_cfg

    @abstractmethod
    def translate(self, source_lang: str, target_lang: str,
                  text: str, start_ms: int, end_ms: int,
                  is_final: bool) -> TranslationResult:
        ...


class TTSBackend(ABC):
    def __init__(self, model_cfg: ModelConfig):
        self.model_cfg = model_cfg

    @abstractmethod
    def synthesize(self, lang: str, text: str,
                   start_ms: int, end_ms: int) -> TtsResult:
        ...


# ---------------------------------------------------------------- mock impls

class MockASR(ASRBackend):
    """Deterministic pseudo-transcription so the pipeline runs offline."""

    def __init__(self, model_cfg: ModelConfig):
        super().__init__(model_cfg)
        self._counters: dict[str, int] = {}

    def transcribe(self, session_id: str, chunk: AudioChunk,
                   batch: bool = False,
                   source_lang: str | None = None) -> list[TranscriptResult]:
        n = self._counters.get(session_id, 0) + 1
        self._counters[session_id] = n
        hash_tail = hashlib.sha1(f"{session_id}:{chunk.seq_no}".encode()).hexdigest()[:4]
        text = f"mock-asr [{self.model_cfg.languages[0]}] segment {n} #{hash_tail}"
        return [TranscriptResult(
            text=text,
            start_ms=(n - 1) * chunk.duration_ms,
            end_ms=n * chunk.duration_ms,
            is_final=chunk.is_final or (n % 4 == 0),
        )]


class MockMT(MTBackend):
    """Prefixes text with target language tag."""

    def translate(self, source_lang: str, target_lang: str, text: str,
                  start_ms: int, end_ms: int, is_final: bool) -> TranslationResult:
        return TranslationResult(
            text=f"[{target_lang}] {text}",
            start_ms=start_ms,
            end_ms=end_ms,
            is_final=is_final,
        )


class MockTTS(TTSBackend):
    """Produces a short sine-wave WAV whose length scales with the text."""

    def synthesize(self, lang: str, text: str,
                   start_ms: int, end_ms: int) -> TtsResult:
        duration_ms = max(100, min(500, len(text) * 20))
        return TtsResult(
            data=synth_tone_wav(duration_ms),
            format="wav",
            start_ms=start_ms,
            end_ms=end_ms,
        )


# ------------------------------------------------------ MT model catalog
# Candidate machine-translation models exposed to the demo UI. Batch and
# streaming evaluation across these models is the project's main goal, so a
# model is only admitted if it covers Gujarati AND Tamil (plus the other Indic
# languages) and is open source. See README "Model evaluation".


@dataclass(frozen=True)
class MTModelInfo:
    id: str                # value used over WS query param / REST body / payload
    label: str             # shown in the demo dropdown
    backend: str           # resolves to a MTBackend class in make_mt()
    languages: tuple[str, ...]
    license: str
    note: str = ""


MT_MODELS: list[MTModelInfo] = [
    MTModelInfo(
        id="nllb",
        label="NLLB-200 distilled 600M (CTranslate2 int8)",
        backend="nllb",
        languages=("en", "bn", "gu", "hi", "kn", "ml", "mr", "pa", "ta", "te", "ur"),
        license="CC-BY-NC-4.0",
        note="baseline; any language pair among the Indic set; ~1.1 GB",
    ),
    MTModelInfo(
        id="bergamot",
        label="Mozilla Firefox Translations (tiny intgemm)",
        backend="bergamot",
        languages=("en", "gu", "hi", "kn", "ml", "ta"),
        license="MPL-2.0",
        note="English-centric pairs only (en<->gu/hi/kn/ml/ta); ~17 MB/pair, fastest on CPU",
    ),
    MTModelInfo(
        id="indictrans2",
        label="AI4Bharat IndicTrans2 1.1B (transformers)",
        backend="indictrans2",
        languages=("en", "bn", "gu", "hi", "kn", "ml", "mr", "pa", "ta", "te", "ur"),
        license="MIT",
        note="English<->Indic only; two ~1 GB checkpoints (en-indic + indic-en); CPU-slow",
    ),
]

MT_MODEL_IDS: set[str] = {m.id for m in MT_MODELS}


def make_mt(model_cfg: ModelConfig, model_id: str | None = None) -> MTBackend:
    """Build an MT backend.

    ``model_id`` selects a catalog model by id (e.g. from the demo dropdown);
    when empty/``"default"`` the backend configured in ``model_cfg.backend``
    is used (mock | nllb | bergamot | indictrans2).
    """
    if model_id and model_id != "default":
        return _make_mt_by_id(model_id, model_cfg)
    if model_cfg.backend == "mock":
        return MockMT(model_cfg)
    if model_cfg.backend == "nllb":
        from stts_core.backends import NLLBMT
        return NLLBMT(model_cfg)
    if model_cfg.backend == "bergamot":
        from stts_core.backends import BergamotMT
        return BergamotMT(model_cfg)
    if model_cfg.backend == "indictrans2":
        from stts_core.backends import IndicTrans2MT
        return IndicTrans2MT(model_cfg)
    raise NotImplementedError(
        f"MT backend '{model_cfg.backend}' not wired yet; "
        "extend MTBackend and register it in make_mt()")


def _make_mt_by_id(model_id: str, model_cfg: ModelConfig) -> MTBackend:
    for info in MT_MODELS:
        if info.id != model_id:
            continue
        if info.backend == "nllb":
            from stts_core.backends import NLLBMT
            return NLLBMT(model_cfg)
        if info.backend == "bergamot":
            from stts_core.backends import BergamotMT
            return BergamotMT(model_cfg)
        if info.backend == "indictrans2":
            from stts_core.backends import IndicTrans2MT
            return IndicTrans2MT(model_cfg)
        raise NotImplementedError(
            f"MT model '{model_id}' maps to backend '{info.backend}' "
            "which is not wired yet")
    raise ValueError(
        f"unknown MT model id '{model_id}'; known: {sorted(MT_MODEL_IDS)}")


def make_asr(model_cfg: ModelConfig) -> ASRBackend:
    if model_cfg.backend == "mock":
        return MockASR(model_cfg)
    if model_cfg.backend == "whisper":
        from stts_core.backends import RoutingASR
        return RoutingASR(model_cfg)
    raise NotImplementedError(
        f"ASR backend '{model_cfg.backend}' not wired yet; "
        "extend ASRBackend and register it in make_asr()")


def make_tts(model_cfg: ModelConfig) -> TTSBackend:
    if model_cfg.backend == "mock":
        return MockTTS(model_cfg)
    if model_cfg.backend == "piper":
        from stts_core.backends import PiperTTS
        return PiperTTS(model_cfg)
    raise NotImplementedError(
        f"TTS backend '{model_cfg.backend}' not wired yet; "
        "extend TTSBackend and register it in make_tts()")
