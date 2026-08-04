"""Optional real offline backends, lazy-loaded.

Heavy third-party engines (faster-whisper, CTranslate2, piper) are only
imported when a real backend is actually constructed, so the ``mock``
pipeline keeps running with zero model dependencies.

Expected layout under ``ModelConfig.offlinePath`` (see
``scripts/download_models.py``)::

    <offlinePath>/
      whisper-large-v3-turbo/   # mobiuslabsgmbh/faster-whisper-large-v3-turbo (model.bin, ...)
      nllb-600m/               # mijuanlo/nllb-200-distilled-600M-ct2-int8
      piper/                   # *.onnx + *.onnx.json voices (nested ok)
      bergamot/<src>-<tgt>/    # Mozilla Firefox Translations (fxtranslate), one dir per pair
      indictrans2/             # ai4bharat IndicTrans2 1.1B (en-indic-1b/ + indic-en-1b/)
"""
from __future__ import annotations

import logging
import os
import sys
import threading

import numpy as np

from stts_core.audio import PIPELINE_RATE, AudioChunk, decode_wav
from stts_core import medical
from stts_core.config import ModelConfig
from stts_core.models import (
    ASRBackend,
    MockTTS,
    MTBackend,
    TTSBackend,
    TranscriptResult,
    TranslationResult,
    TtsResult,
)

log = logging.getLogger("stts.backends")

# Whisper already uses lowercase ISO-639-1 codes, identical to our config.
WHISPER_MODEL_DIR = "whisper-large-v3-turbo"
INDICCONFORMER_DIR = "indic-conformer-600m"
NLLB_MODEL_DIR = "nllb-600m"
PIPER_DIR = "piper"
BERGAMOT_DIR = "bergamot"              # per-pair dirs: <offlinePath>/bergamot/<src>-<tgt>/
INDICTRANS2_DIR = "indictrans2"        # en-indic-1b/ + indic-en-1b/ checkpoints

# Languages routed to IndicConformer-600M (CTC) instead of Whisper. Whisper
# structurally hallucinates on these (script-mixing gibberish / repetition
# loops on voiced speech), while IndicConformer produces coherent native-script
# text. Measured on the real-native eval set (docs/REAL_NATIVE_EVAL.md):
#   gu 80% -> 20% (all flagged clips are sub-second segments),
#   ml 47% ->  0%, mr 22% -> 11% (one true residual).
INDICCONFORMER_LANGS = frozenset({"gu", "ml", "mr"})

# ISO-639-1 -> IndicTrans2 language token (ai4bharat/indictrans2-* family,
# tags carry a script suffix, e.g. "eng_Latn").
INDICTRANS2 = {
    "en": "eng_Latn",
    "bn": "ben_Beng",
    "gu": "guj_Gujr",
    "hi": "hin_Deva",
    "kn": "kan_Knda",
    "ml": "mal_Mlym",
    "mr": "mar_Deva",
    "pa": "pan_Guru",
    "ta": "tam_Taml",
    "te": "tel_Telu",
    "ur": "urd_Arab",
}

# ISO-639-1 -> NLLB FLORES-200 code.
FLORES = {
    "en": "eng_Latn",
    "bn": "ben_Beng",
    "gu": "guj_Gujr",
    "hi": "hin_Deva",
    "kn": "kan_Knda",
    "ml": "mal_Mlym",
    "mr": "mar_Deva",
    "pa": "pan_Guru",
    "ta": "tam_Taml",
    "te": "tel_Telu",
    "ur": "urd_Arab",
}

# Streaming partials are re-emitted once ~this much new audio has arrived.
# Streaming partials: re-transcribe when this much NEW audio has accumulated.
# Each transcribe() call has ~4s fixed overhead on this hardware, so decoding
# every 2s made a 28s session cost 15 decodes (~3x realtime). 4s keeps the
# live transcript fresh enough while roughly halving decode count.
PARTIAL_RECHECK_SAMPLES = PIPELINE_RATE * 4

# Streaming: words that finish at least this far before the end of the pending
# buffer are confirmed (immutable) and trimmed away. The trailing audio stays
# pending so the next decode can revise it with more context. This bounds
# per-session memory to roughly CONFIRM_KEEP_BACK_MS + one recheck window.
CONFIRM_KEEP_BACK_MS = 3000
# How much of the confirmed transcript is re-seeded as whisper context.
# Whisper's context window is 224 tokens, so only the recent tail survives
# alongside the medical prompt; faster-whisper truncates the rest anyway.
CONFIRM_PROMPT_WORDS = 80


def _pcm_to_f32(pcm: bytes) -> list[float]:
    return (np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0).tolist()


def _require_dir(path: str) -> None:
    if not os.path.isdir(path):
        raise RuntimeError(
            f"model directory not found: {path} — run `make models` "
            "(scripts/download_models.py) to fetch the offline models")


class WhisperASR(ASRBackend):
    """faster-whisper (CTranslate2, int8) speech recognition."""

    # < 0.1s of new audio: not enough to judge, don't block transcription.
    SILENCE_MIN_SAMPLES = 1600

    def __init__(self, model_cfg: ModelConfig):
        super().__init__(model_cfg)
        self._path = os.path.join(model_cfg.offlinePath, WHISPER_MODEL_DIR)
        _require_dir(self._path)
        self._model = None
        self._lock = threading.Lock()
        self._buffers: dict[str, list[float]] = {}    # batch: full accumulation
        self._pending: dict[str, list[float]] = {}    # streaming: unconfirmed tail
        self._abs_ms: dict[str, int] = {}             # streaming: offset of _pending[0]
        self._confirmed: dict[str, str] = {}          # streaming: immutable transcript
        self._last_len: dict[str, int] = {}
        self._busy: dict[str, bool] = {}

    def _load(self):
        if self._model is None:
            from faster_whisper import WhisperModel
            with self._lock:
                if self._model is None:
                    log.info("loading faster-whisper from %s (int8)", self._path)
                    self._model = WhisperModel(
                        self._path, device="cpu", compute_type="int8")
        return self._model

    def _decode(self, samples: list[float], source_lang: str | None,
                beam_size: int, initial_prompt: str | None,
                word_timestamps: bool = False) -> tuple[str, list]:
        """Decode ``samples`` and return ``(text, segments)``.

        ``initial_prompt`` carries the medical-domain prompt plus, for
        streaming, the confirmed prefix so each decode *continues* the
        transcript instead of re-transcribing the whole buffer from scratch.
        """
        audio = np.asarray(samples, dtype=np.float32)
        segments, _ = self._load().transcribe(
            audio,
            language=source_lang,     # caller-known source, else auto-detect
            beam_size=beam_size,      # beam search: notably better accuracy than greedy
            vad_filter=True,
            no_repeat_ngram_size=3,   # suppress the repetitive "word word word" loops
            condition_on_previous_text=False,
            word_timestamps=word_timestamps,
            initial_prompt=initial_prompt,
            hotwords=medical.hotwords_for(source_lang),
            hallucination_silence_threshold=(
                medical.hallucination_silence_threshold()),
        )
        segs = list(segments)
        text = " ".join(s.text.strip() for s in segs).strip()
        return text, segs

    def _run(self, samples: list[float], source_lang: str | None = None,
             beam_size: int = 5) -> str:
        """Whole-buffer decode (batch / session final) with the medical prompt."""
        text, _ = self._decode(samples, source_lang, beam_size,
                               medical.prompt_for(source_lang))
        return text

    def _stream_prompt(self, source_lang: str | None,
                       confirmed: str) -> str | None:
        """initial_prompt for continuation decoding: the medical-domain prompt
        plus the recent confirmed transcript, so Whisper transcribes the new
        tail in context instead of starting over (and never re-decodes already
        confirmed words)."""
        prompt = medical.prompt_for(source_lang) or ""
        if confirmed:
            tail = confirmed.split()[-CONFIRM_PROMPT_WORDS:]
            if tail:
                prompt = (prompt + "\n" + " ".join(tail)).strip()
        return prompt or None

    @staticmethod
    def _confirm_boundary_ms(segs, duration_ms: int) -> int:
        """Latest word end (ms into the buffer) that is safe to confirm."""
        limit = duration_ms - CONFIRM_KEEP_BACK_MS
        if limit <= 0:
            return 0
        boundary = 0
        for seg in segs:
            for word in seg.words or []:
                end_ms = int(round(word.end * 1000))
                if end_ms <= limit:
                    boundary = max(boundary, end_ms)
        return boundary

    @staticmethod
    def _words_before(segs, boundary_ms: int) -> str:
        return "".join(
            w.word for s in segs for w in (s.words or [])
            if int(round(w.end * 1000)) <= boundary_ms).strip()

    @staticmethod
    def _words_after(segs, boundary_ms: int) -> str:
        return "".join(
            w.word for s in segs for w in (s.words or [])
            if int(round(w.end * 1000)) > boundary_ms).strip()

    @staticmethod
    def _strip_echo(text: str, confirmed: str = "") -> str:
        """Strip seam echoes that Whisper inserts at a decode boundary.

        ``confirmed`` is the already-immutable transcript. Two artifacts are
        removed:

        1. Cross-segment: the trim boundary can land between a speaker label
           and the rest of its turn, so the previous final ends with e.g.
           ``Patient.`` and the next fragment begins ``Patient. I have body
           pain`` - the leading word repeats the tail of ``confirmed``.
        2. Prompt echo: when the decode window starts exactly at the boundary
           and its first word matches the ``initial_prompt`` context, Whisper
           can emit it twice (``Patient. Patient. ...``).

        The duplicate is always the window's leading token(s), matching the
        already-emitted text, so stripping them loses nothing.
        """
        if not text:
            return text
        words = text.split()
        if confirmed:
            c_words = confirmed.split()
            max_k = min(len(c_words), len(words))
            k = max_k
            while k > 0 and c_words[-k:] != words[:k]:
                k -= 1
            if k:
                words = words[k:]
        if len(words) >= 2 and words[0].lower() == words[1].lower():
            words = words[1:]
        return " ".join(words).strip()

    def _drop(self, session_id: str) -> None:
        for d in (self._buffers, self._pending, self._confirmed,
                  self._abs_ms, self._last_len, self._busy):
            d.pop(session_id, None)

    def _evict(self) -> None:
        if len(self._pending) + len(self._buffers) > 64:
            if self._buffers:
                self._drop(next(iter(self._buffers)))
            elif self._pending:
                self._drop(next(iter(self._pending)))

    def _has_speech(self, samples: list[float], start: int) -> bool:
        """True if the audio added since ``start`` contains speech (Silero VAD)."""
        audio = np.asarray(samples[start:], dtype=np.float32)
        if audio.size < self.SILENCE_MIN_SAMPLES:
            return True  # can't judge yet — don't block transcription
        try:
            from faster_whisper.vad import VadOptions, get_speech_timestamps
            ts = get_speech_timestamps(
                audio,
                vad_options=VadOptions(min_speech_duration_ms=150),
                sampling_rate=PIPELINE_RATE,
            )
            return bool(ts)
        except Exception:
            return True  # be permissive if VAD is unavailable

    def transcribe(self, session_id: str, chunk: AudioChunk,
                   batch: bool = False,
                   source_lang: str | None = None) -> list[TranscriptResult]:
        self._evict()
        pcm, _, _ = decode_wav(chunk.data)
        samples = _pcm_to_f32(pcm)

        if batch:
            buf = self._buffers.setdefault(session_id, [])
            buf.extend(samples)
            total_ms = int(len(buf) / PIPELINE_RATE * 1000)
            if not chunk.is_final:
                return [TranscriptResult(
                    text="", start_ms=0, end_ms=0, is_final=False)]
            text = self._run(buf, source_lang)
            self._drop(session_id)
            return [TranscriptResult(
                text=text, start_ms=0, end_ms=total_ms, is_final=True)]

        # Streaming: incremental continuation. We keep only the unconfirmed
        # audio tail (``_pending``), decode it seeded with the confirmed
        # prefix, confirm the words older than CONFIRM_KEEP_BACK_MS (they are
        # immutable once emitted as a final), and trim the audio up to the
        # confirmation boundary. Per-session memory therefore stays bounded and
        # already-heard words can never be dropped by a later decode.
        pending = self._pending.setdefault(session_id, [])
        pending.extend(samples)
        last = self._last_len.get(session_id, 0)
        run = chunk.is_final or (
            (len(pending) - last) >= PARTIAL_RECHECK_SAMPLES
            and not self._busy.get(session_id, False))
        if not run:
            return [TranscriptResult(
                text="", start_ms=0, end_ms=0, is_final=chunk.is_final)]
        # Hold partials while the speaker is silent instead of hallucinating
        # text on room tone / background noise. Only the audio added since the
        # last run is checked, so speech resumes seamlessly.
        if not chunk.is_final and not self._has_speech(pending, last):
            self._last_len[session_id] = len(pending)
            return [TranscriptResult(
                text="", start_ms=0, end_ms=0, is_final=False)]

        self._busy[session_id] = True
        try:
            abs_start = self._abs_ms.get(session_id, 0)
            duration_ms = int(len(pending) / PIPELINE_RATE * 1000)
            confirmed = self._confirmed.get(session_id, "")
            text, segs = self._decode(
                pending, source_lang,
                # Beam 5 everywhere: the confirmed segments are cut straight
                # from partial decodes, so greedy (beam 1) artifacts like
                # truncated/duplicated words would otherwise get committed as
                # immutable. Tails are short, so the fixed ~4s per-decode
                # overhead dominates over the beam-size difference.
                beam_size=5,
                initial_prompt=self._stream_prompt(source_lang, confirmed),
                word_timestamps=not chunk.is_final)

            if chunk.is_final:
                # Session end: confirm the whole tail. `session_end` lets TTS
                # emit the `end` event (earlier per-segment finals must not).
                self._drop(session_id)
                return [TranscriptResult(
                    text=self._strip_echo(text, confirmed), start_ms=abs_start,
                    end_ms=abs_start + duration_ms,
                    is_final=True, session_end=True)]

            boundary = self._confirm_boundary_ms(segs, duration_ms)
            if boundary > 0:
                frag = self._strip_echo(self._words_before(segs, boundary), confirmed)
                if frag:
                    self._confirmed[session_id] = (confirmed + " " + frag).strip()
                cut = int(boundary / 1000 * PIPELINE_RATE)
                del pending[:cut]
                self._abs_ms[session_id] = abs_start + boundary
                results: list[TranscriptResult] = []
                if frag:
                    results.append(TranscriptResult(
                        text=frag, start_ms=abs_start,
                        end_ms=abs_start + boundary, is_final=True))
                tail = self._strip_echo(
                    self._words_after(segs, boundary),
                    (confirmed + " " + frag).strip())
                if tail:
                    results.append(TranscriptResult(
                        text=tail, start_ms=abs_start + boundary,
                        end_ms=abs_start + duration_ms, is_final=False))
                return results

            return [TranscriptResult(
                text=self._strip_echo(text, confirmed), start_ms=abs_start,
                end_ms=abs_start + duration_ms, is_final=False)]
        finally:
            if session_id in self._busy:
                self._busy[session_id] = False
            if session_id in self._pending:
                self._last_len[session_id] = len(self._pending[session_id])


def _vad_has_speech(samples: list[float], start: int) -> bool:
    """True if the audio added since ``start`` contains speech (Silero VAD)."""
    audio = np.asarray(samples[start:], dtype=np.float32)
    if audio.size < WhisperASR.SILENCE_MIN_SAMPLES:
        return True  # can't judge yet — don't block transcription
    try:
        from faster_whisper.vad import VadOptions, get_speech_timestamps
        ts = get_speech_timestamps(
            audio,
            vad_options=VadOptions(min_speech_duration_ms=150),
            sampling_rate=PIPELINE_RATE,
        )
        return bool(ts)
    except Exception:
        return True  # be permissive if VAD is unavailable


class IndicConformerASR(ASRBackend):
    """AI4Bharat IndicConformer-600M (ONNX, hybrid CTC) speech recognition.

    Used for the Indic languages Whisper structurally hallucinates on
    (``INDICCONFORMER_LANGS``). The model decodes a whole utterance with CTC
    into the target native script; English loanwords are rendered
    phonetically in that script. It has no word timestamps, so streaming
    partials re-transcribe the pending tail from scratch instead of the
    incremental-confirm scheme Whisper uses.
    """

    def __init__(self, model_cfg: ModelConfig):
        super().__init__(model_cfg)
        self._path = os.path.join(model_cfg.offlinePath, INDICCONFORMER_DIR)
        _require_dir(self._path)
        self._model = None
        self._lock = threading.Lock()
        self._buffers: dict[str, list[float]] = {}    # batch accumulation
        self._pending: dict[str, list[float]] = {}    # streaming tail
        self._abs_ms: dict[str, int] = {}
        self._last_len: dict[str, int] = {}
        self._busy: dict[str, bool] = {}

    def _load(self):
        if self._model is None:
            with self._lock:
                if self._model is None:
                    if self._path not in sys.path:
                        sys.path.insert(0, self._path)
                    from model_onnx import IndicASRConfig, IndicASRModel
                    log.info("loading IndicConformer-600M from %s (CTC)", self._path)
                    self._model = IndicASRModel(IndicASRConfig(ts_folder=self._path))
        return self._model

    def _decode(self, samples: list[float], source_lang: str) -> str:
        if source_lang not in INDICCONFORMER_LANGS:
            raise ValueError(
                f"IndicConformer only supports {sorted(INDICCONFORMER_LANGS)}, "
                f"got {source_lang!r}")
        import torch
        audio = torch.from_numpy(np.asarray(samples, dtype=np.float32))[None, :]
        text = self._load()(audio, source_lang, "ctc")
        return text.strip() if isinstance(text, str) else str(text).strip()

    def _drop(self, session_id: str) -> None:
        for d in (self._buffers, self._pending, self._abs_ms,
                  self._last_len, self._busy):
            d.pop(session_id, None)

    def _evict(self) -> None:
        if len(self._pending) + len(self._buffers) > 64:
            if self._buffers:
                self._drop(next(iter(self._buffers)))
            elif self._pending:
                self._drop(next(iter(self._pending)))

    def transcribe(self, session_id: str, chunk: AudioChunk,
                   batch: bool = False,
                   source_lang: str | None = None) -> list[TranscriptResult]:
        self._evict()
        lang = source_lang or "hi"
        pcm, _, _ = decode_wav(chunk.data)
        samples = _pcm_to_f32(pcm)

        if batch:
            buf = self._buffers.setdefault(session_id, [])
            buf.extend(samples)
            total_ms = int(len(buf) / PIPELINE_RATE * 1000)
            if not chunk.is_final:
                return [TranscriptResult(
                    text="", start_ms=0, end_ms=0, is_final=False)]
            text = self._decode(buf, lang)
            self._drop(session_id)
            return [TranscriptResult(
                text=text, start_ms=0, end_ms=total_ms, is_final=True)]

        pending = self._pending.setdefault(session_id, [])
        pending.extend(samples)
        last = self._last_len.get(session_id, 0)
        run = chunk.is_final or (
            (len(pending) - last) >= PARTIAL_RECHECK_SAMPLES
            and not self._busy.get(session_id, False))
        if not run:
            return [TranscriptResult(
                text="", start_ms=0, end_ms=0, is_final=chunk.is_final)]
        if not chunk.is_final and not _vad_has_speech(pending, last):
            self._last_len[session_id] = len(pending)
            return [TranscriptResult(
                text="", start_ms=0, end_ms=0, is_final=False)]

        self._busy[session_id] = True
        try:
            abs_start = self._abs_ms.get(session_id, 0)
            duration_ms = int(len(pending) / PIPELINE_RATE * 1000)
            text = self._decode(pending, lang)
            if chunk.is_final:
                self._drop(session_id)
                return [TranscriptResult(
                    text=text, start_ms=abs_start,
                    end_ms=abs_start + duration_ms,
                    is_final=True, session_end=True)]
            return [TranscriptResult(
                text=text, start_ms=abs_start,
                end_ms=abs_start + duration_ms, is_final=False)]
        finally:
            if session_id in self._busy:
                self._busy[session_id] = False
            if session_id in self._pending:
                self._last_len[session_id] = len(self._pending[session_id])


class RoutingASR(ASRBackend):
    """Language-routed ASR facade.

    Routes ``INDICCONFORMER_LANGS`` to IndicConformer-600M (CTC) and every
    other language to Whisper, which keeps Latin English/drug-name fidelity
    for the languages it already handles well (e.g. hi).
    """

    def __init__(self, model_cfg: ModelConfig):
        super().__init__(model_cfg)
        self._whisper = WhisperASR(model_cfg)
        self._ic: IndicConformerASR | None = None
        self._lock = threading.Lock()

    def _indicconformer(self) -> IndicConformerASR:
        if self._ic is None:
            with self._lock:
                if self._ic is None:
                    self._ic = IndicConformerASR(self.model_cfg)
        return self._ic

    def transcribe(self, session_id: str, chunk: AudioChunk,
                   batch: bool = False,
                   source_lang: str | None = None) -> list[TranscriptResult]:
        if source_lang in INDICCONFORMER_LANGS:
            return self._indicconformer().transcribe(
                session_id, chunk, batch, source_lang)
        return self._whisper.transcribe(session_id, chunk, batch, source_lang)


class NLLBMT(MTBackend):
    """CTranslate2 NLLB-200 distilled 600M (int8) machine translation."""

    def __init__(self, model_cfg: ModelConfig):
        super().__init__(model_cfg)
        self._path = os.path.join(model_cfg.offlinePath, NLLB_MODEL_DIR)
        _require_dir(self._path)
        self._loaded = None

    def _load(self):
        if self._loaded is None:
            import ctranslate2
            import sentencepiece as spm
            sp = spm.SentencePieceProcessor(
                model_file=os.path.join(self._path, "sentencepiece.bpe.model"))
            log.info("loading NLLB-600M from %s (int8)", self._path)
            translator = ctranslate2.Translator(
                self._path, device="cpu", compute_type="int8")
            self._loaded = (translator, sp)
        return self._loaded

    def translate(self, source_lang: str, target_lang: str, text: str,
                  start_ms: int, end_ms: int, is_final: bool) -> TranslationResult:
        if not text.strip():
            return TranslationResult(text="", start_ms=start_ms,
                                     end_ms=end_ms, is_final=is_final)
        src, tgt = FLORES.get(source_lang), FLORES.get(target_lang)
        if not src or not tgt:
            raise ValueError(
                f"unsupported language pair {source_lang}->{target_lang} for NLLB")
        translator, sp = self._load()
        source = [src] + sp.encode(text, out_type=str) + ["</s>"]
        results = translator.translate_batch(
            [source], target_prefix=[[tgt]], beam_size=1, max_batch_size=1,
            max_decoding_length=256)
        out = sp.decode(results[0].hypotheses[0][1:])
        return TranslationResult(
            text=out, start_ms=start_ms, end_ms=end_ms, is_final=is_final)


class BergamotMT(MTBackend):
    """Mozilla Firefox Translations / Bergamot model (marian tiny, intgemm).

    Runs through the ``fxtranslate`` package (native Rust port of the bergamot
    engine). Models are English-centric and come one per direction; they are
    downloaded by ``scripts/download_models.py`` into::

        <offlinePath>/bergamot/<src>-<tgt>/
          model.<src><tgt>.intgemm.alphas.bin   # gunzipped models.json file
          vocab.<src><tgt>.spm                  # gunzipped models.json file
    """

    def __init__(self, model_cfg: ModelConfig):
        super().__init__(model_cfg)
        self._root = os.path.join(model_cfg.offlinePath, BERGAMOT_DIR)
        self._loaded: dict[str, object] = {}

    def _load(self, source_lang: str, target_lang: str) -> object:
        key = f"{source_lang}-{target_lang}"
        if key in self._loaded:
            return self._loaded[key]
        d = os.path.join(self._root, key)
        _require_dir(d)
        model_path = vocab_path = None
        for name in sorted(os.listdir(d)):
            if name.startswith("model.") and name.endswith(".bin"):
                model_path = os.path.join(d, name)
            elif name.startswith("vocab.") and name.endswith(".spm"):
                vocab_path = os.path.join(d, name)
        if not model_path or not vocab_path:
            raise RuntimeError(
                f"Bergamot model for {key} incomplete in {d}: expected a "
                "model.<...>.bin and a vocab.<...>.spm — run `make models`")
        try:
            from fxtranslate import Translator
        except ImportError as exc:
            raise RuntimeError(
                "Bergamot backend requires the `fxtranslate` package "
                "(pip install fxtranslate)") from exc
        log.info("loading Bergamot model %s from %s (fxtranslate)", key, d)
        with open(model_path, "rb") as mf, open(vocab_path, "rb") as vf:
            translator = Translator(mf.read(), vf.read(), vf.read())
        self._loaded[key] = translator
        return translator

    def translate(self, source_lang: str, target_lang: str, text: str,
                  start_ms: int, end_ms: int, is_final: bool) -> TranslationResult:
        if not text.strip():
            return TranslationResult(text="", start_ms=start_ms,
                                     end_ms=end_ms, is_final=is_final)
        out = self._load(source_lang, target_lang).translate(text)
        return TranslationResult(
            text=str(out).strip(), start_ms=start_ms,
            end_ms=end_ms, is_final=is_final)


class IndicTrans2MT(MTBackend):
    """AI4Bharat IndicTrans2 (1.1B) via HuggingFace transformers.

    Only English<->Indic directions are supported (an English pivot would be
    needed for indic->indic pairs, so those should use NLLB/Bergamot). The two
    official checkpoints live under::

        <offlinePath>/indictrans2/en-indic-1b/   # ai4bharat/indictrans2-en-indic-1B
        <offlinePath>/indictrans2/indic-en-1b/   # ai4bharat/indictrans2-indic-en-1B
    """

    def __init__(self, model_cfg: ModelConfig):
        super().__init__(model_cfg)
        self._root = os.path.join(model_cfg.offlinePath, INDICTRANS2_DIR)
        self._loaded: dict[str, tuple[object, object]] = {}

    def _checkpoint(self, source_lang: str, target_lang: str) -> str:
        if source_lang == "en" and target_lang != "en":
            return os.path.join(self._root, "en-indic-1b")
        if target_lang == "en" and source_lang != "en":
            return os.path.join(self._root, "indic-en-1b")
        raise ValueError(
            f"IndicTrans2 supports English<->Indic pairs only (got "
            f"{source_lang}->{target_lang}); use NLLB or Bergamot for "
            "indic->indic")

    def _load(self, source_lang: str, target_lang: str) -> tuple[object, object]:
        key = f"{source_lang}-{target_lang}"
        if key in self._loaded:
            return self._loaded[key]
        repo_dir = self._checkpoint(source_lang, target_lang)
        _require_dir(repo_dir)
        try:
            import torch
            from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
        except ImportError as exc:
            raise RuntimeError(
                "IndicTrans2 backend requires torch + transformers "
                "(pip install torch transformers)") from exc
        log.info("loading IndicTrans2 %s from %s (CPU)", key, repo_dir)
        tokenizer = AutoTokenizer.from_pretrained(
            repo_dir,
            src_lang=INDICTRANS2[source_lang],
            tgt_lang=INDICTRANS2[target_lang],
            trust_remote_code=True,
        )
        model = AutoModelForSeq2SeqLM.from_pretrained(
            repo_dir, trust_remote_code=True)
        model.eval()
        self._loaded[key] = (model, tokenizer)
        return self._loaded[key]

    def translate(self, source_lang: str, target_lang: str, text: str,
                  start_ms: int, end_ms: int, is_final: bool) -> TranslationResult:
        if not text.strip():
            return TranslationResult(text="", start_ms=start_ms,
                                     end_ms=end_ms, is_final=is_final)
        if source_lang not in INDICTRANS2 or target_lang not in INDICTRANS2:
            raise ValueError(
                f"unsupported language pair {source_lang}->{target_lang} "
                f"for IndicTrans2 (supported: {sorted(INDICTRANS2)})")
        model, tokenizer = self._load(source_lang, target_lang)
        import torch
        src, tgt = INDICTRANS2[source_lang], INDICTRANS2[target_lang]
        with torch.no_grad():
            inputs = tokenizer([f"{src} {tgt} {text}"], return_tensors="pt",
                               padding=True, truncation=True, max_length=256)
            outputs = model.generate(**inputs, max_new_tokens=256,
                                     use_cache=False)
            out = tokenizer.batch_decode(outputs, skip_special_tokens=True)[0]
        if target_lang != "en":
            from .indic_translit import devanagari_to_script
            out = devanagari_to_script(out, target_lang)
        return TranslationResult(
            text=out.strip(), start_ms=start_ms,
            end_ms=end_ms, is_final=is_final)


class PiperTTS(TTSBackend):
    """Piper (ONNX) neural TTS. Falls back to mock tone when no voice is
    configured for the target language (Piper ships hi_IN + en voices only)."""

    def __init__(self, model_cfg: ModelConfig):
        super().__init__(model_cfg)
        self._dir = os.path.join(model_cfg.offlinePath, PIPER_DIR)
        self._voices: dict[str, tuple[str, str]] = {}
        self._loaded: dict[str, object] = {}
        self._fallback = MockTTS(model_cfg)
        self._scan()

    def _scan(self) -> None:
        if os.path.isdir(self._dir):
            for dirpath, _, files in os.walk(self._dir):
                for name in files:
                    if name.endswith(".onnx"):
                        stem = name[:-5]
                        cfg = os.path.join(dirpath, stem + ".onnx.json")
                        if os.path.isfile(cfg):
                            self._voices[stem] = (os.path.join(dirpath, name), cfg)
        if self._voices:
            log.info("piper voices found: %s", ", ".join(sorted(self._voices)))
        else:
            log.warning(
                "no piper voices under %s — real TTS will fall back to mock tone "
                "(run `make models`)", self._dir)

    def _voice_for(self, lang: str) -> str | None:
        for prefix in (f"{lang}_", lang):
            for stem in self._voices:
                if stem.startswith(prefix):
                    return stem
        return None

    def synthesize(self, lang: str, text: str,
                   start_ms: int, end_ms: int) -> TtsResult:
        if not text.strip():
            return TtsResult(data=b"", format="wav",
                             start_ms=start_ms, end_ms=end_ms)
        stem = self._voice_for(lang)
        if stem is None:
            log.warning("no piper voice for '%s'; falling back to mock tone", lang)
            return self._fallback.synthesize(lang, text, start_ms, end_ms)
        if stem not in self._loaded:
            from piper import PiperVoice
            model_path, cfg_path = self._voices[stem]
            self._loaded[stem] = PiperVoice.load(model_path, config_path=cfg_path)
        voice = self._loaded[stem]

        from stts_core.audio import encode_wav
        chunks = list(voice.synthesize(text))
        if not chunks:
            return TtsResult(data=b"", format="wav",
                             start_ms=start_ms, end_ms=end_ms)
        raw = b"".join(c.audio_int16_bytes for c in chunks)
        rate = chunks[0].sample_rate
        wav = encode_wav(raw, sample_rate=rate, channels=1, sample_width=2)
        pcm, _, _ = decode_wav(wav)  # normalize/resample to 16 kHz mono PCM
        data = encode_wav(pcm)       # wrap back into a 16 kHz WAV file
        return TtsResult(data=data, format="wav",
                         start_ms=start_ms, end_ms=end_ms)
