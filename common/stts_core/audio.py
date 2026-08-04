"""Minimal offline audio helpers (stdlib only).

Provides WAV decode/normalize + encode without external deps so the mock
pipeline runs fully offline. Decoding normalizes any WAV (stereo/mono, any
sample width, any rate) to 16 kHz mono 16-bit PCM. Real codecs (mp3,
resampling) can plug in behind the same functions.
"""
from __future__ import annotations

import array
import io
import math
import struct
import wave
from dataclasses import dataclass
from typing import Optional

PIPELINE_RATE = 16000  # Hz; the pipeline's canonical sample rate


@dataclass
class AudioChunk:
    seq_no: int
    data: bytes
    format: str = "wav"          # wav | mp3
    sample_rate: int = 16000     # Hz
    duration_ms: int = 300
    is_final: bool = False


def encode_wav(pcm: bytes, sample_rate: int = PIPELINE_RATE,
               channels: int = 1, sample_width: int = 2) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(sample_width)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


def decode_wav(data: bytes) -> tuple[bytes, int, int]:
    """Decode a WAV to normalized ``(mono 16-bit PCM @ 16 kHz, 16000, 2)``.

    Downmixes multi-channel, converts 8/16/24/32-bit samples to 16-bit and
    resamples to the pipeline rate, so callers always receive uniform PCM
    regardless of the source file. Raises ``ValueError`` on undecodable or
    unsupported WAV payloads.
    """
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            nch = w.getnchannels()
            width = w.getsampwidth()
            rate = w.getframerate()
            raw = w.readframes(w.getnframes())
    except (wave.Error, EOFError) as exc:
        raise ValueError(f"not a decodable WAV: {exc}") from exc

    if nch not in (1, 2):
        raise ValueError(f"unsupported channel count: {nch} (mono/stereo only)")
    if width not in (1, 2, 3, 4):
        raise ValueError(f"unsupported sample width: {width} bytes")

    pcm = _normalize_pcm(raw, nch, width)
    if rate != PIPELINE_RATE:
        pcm = _resample(pcm, rate, PIPELINE_RATE)
    return pcm.tobytes(), PIPELINE_RATE, 2


def _clamp16(v: int) -> int:
    return max(-32768, min(32767, v))


def _normalize_pcm(raw: bytes, nch: int, width: int) -> "array.array":
    """Return mono 16-bit samples from raw frames."""
    if width == 1:
        samples = array.array("B")
        samples.frombytes(raw)
        mono = [(v - 128) << 8 for v in samples]
    elif width == 2:
        samples = array.array("h")
        samples.frombytes(raw)
        mono = samples
    elif width == 4:
        samples = array.array("i")
        samples.frombytes(raw)
        mono = [_clamp16(v >> 16) for v in samples]
    else:  # width == 3
        mono = [int.from_bytes(raw[i:i + 3], "little", signed=True) >> 8
                for i in range(0, len(raw), 3)]

    if nch == 1:
        return array.array("h", (_clamp16(s) for s in mono))
    # downmix stereo: average each pair
    n = len(mono) - (len(mono) % 2)
    return array.array("h", (_clamp16((mono[i] + mono[i + 1]) // 2)
                             for i in range(0, n, 2)))


def _resample(src: "array.array", src_rate: int, dst_rate: int) -> "array.array":
    """Linear-interpolation resampler (mono int16)."""
    n = len(src)
    m = max(1, int(n * dst_rate / src_rate))
    out = array.array("h", [0]) * m
    ratio = src_rate / dst_rate
    for i in range(m):
        t = i * ratio
        i0 = int(t)
        i1 = min(i0 + 1, n - 1)
        frac = t - i0
        out[i] = int(src[i0] + (src[i1] - src[i0]) * frac)
    return out


def synth_tone_wav(duration_ms: int, sample_rate: int = PIPELINE_RATE,
                   freq_hz: float = 440.0) -> bytes:
    """Generate a mono 16-bit PCM sine wave as a WAV blob (mock TTS)."""
    n = max(1, int(sample_rate * duration_ms / 1000))
    pcm = bytearray()
    for i in range(n):
        sample = int(12000 * math.sin(2 * math.pi * freq_hz * i / sample_rate))
        pcm += struct.pack("<h", sample)
    return encode_wav(bytes(pcm), sample_rate)
