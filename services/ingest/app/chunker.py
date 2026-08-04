"""Audio chunker: accumulates PCM until the configured chunk duration.

Emits AudioChunk objects with sequential seq numbers. Used by both the
streaming WS path and the batch file path.
"""
from __future__ import annotations

import logging
from typing import Optional

from stts_core.audio import AudioChunk, decode_wav, encode_wav

log = logging.getLogger("stts.ingest.chunker")


class Chunker:
    def __init__(self, sample_rate: int, chunk_duration_ms: int):
        self.sample_rate = sample_rate
        self.chunk_duration_ms = chunk_duration_ms
        self._buf = bytearray()
        self._target_bytes = int(sample_rate * 2 * chunk_duration_ms / 1000)

    def add(self, pcm: bytes, is_final: bool) -> list[AudioChunk]:
        """Feed decoded mono 16-bit PCM; returns full chunks (final flushes).

        When ``is_final`` is set and the last full chunk empties the buffer,
        that chunk is marked final (keeps the final marker on exact-size
        chunks).
        """
        self._buf.extend(pcm)
        out: list[AudioChunk] = []
        while len(self._buf) >= self._target_bytes:
            data = bytes(self._buf[: self._target_bytes])
            del self._buf[: self._target_bytes]
            is_last = is_final and len(self._buf) == 0
            out.append(self._make(data, is_final=is_last))
        if is_final and self._buf:
            out.append(self._make(bytes(self._buf), is_final=True))
            self._buf.clear()
        return out

    def _make(self, data: bytes, is_final: bool) -> AudioChunk:
        pcm = data
        return AudioChunk(
            seq_no=-1,
            data=encode_wav(pcm, self.sample_rate),  # payload is a self-describing WAV
            format="wav",
            sample_rate=self.sample_rate,
            duration_ms=int(len(pcm) * 1000 / (self.sample_rate * 2)),
            is_final=is_final,
        )

    def chunks_from_wav(self, wav_data: bytes, seq_start: int = 1) -> list[AudioChunk]:
        """Split a full WAV file into chunks with sequential seq numbers."""
        pcm, rate, _ = decode_wav(wav_data)
        self.sample_rate = rate
        self._target_bytes = int(rate * 2 * self.chunk_duration_ms / 1000)
        self._buf.clear()
        self._buf.extend(pcm)
        chunks: list[AudioChunk] = []
        seq = seq_start
        while True:
            if len(self._buf) >= self._target_bytes:
                data = bytes(self._buf[: self._target_bytes])
                del self._buf[: self._target_bytes]
                chunks.append(self._make(data, is_final=False))
                seq += 1
                continue
            if self._buf:
                chunks.append(self._make(bytes(self._buf), is_final=True))
                self._buf.clear()
                seq += 1
            break
        for i, c in enumerate(chunks, start=seq_start):
            c.seq_no = i
        return chunks
