"""Configuration loading with defaults, ranges and env overrides.

Every parameter has a default and is documented with its valid range in the
YAML config files shipped with each service (see PRD section 2.4).

Env overrides use the prefix ``STTS_`` and follow the nested path, e.g.::

    STTS_AUDIO_SAMPLE_RATE=48000
    STTS_NATS_URL=nats://localhost:4222
"""
from __future__ import annotations

import os
from typing import Any, Optional, Type, TypeVar

import yaml
from pydantic import BaseModel, field_validator

T = TypeVar("T", bound="BaseConfig")

ENV_PREFIX = "STTS_"


class AudioConfig(BaseModel):
    """Audio parameters. Ranges in comments below (from PRD 2.4)."""
    sampleRate: int = 16000          # 8000-48000 Hz
    chunkDurationMs: int = 300       # 200-500 ms per streaming chunk
    allowedFormats: list[str] = ["wav", "mp3"]
    outputFormat: str = "wav"        # wav | mp3
    maxFileSizeMb: int = 100         # 1-1000 MB for batch uploads
    outputDir: str = "/data/output"  # directory for batch result audio files

    @field_validator("sampleRate")
    @classmethod
    def _check_sample_rate(cls, v: int) -> int:
        if not 8000 <= v <= 48000:
            raise ValueError(f"sampleRate {v} out of range [8000, 48000]")
        return v

    @field_validator("chunkDurationMs")
    @classmethod
    def _check_chunk(cls, v: int) -> int:
        if not 200 <= v <= 500:
            raise ValueError(f"chunkDurationMs {v} out of range [200, 500]")
        return v


class ModelConfig(BaseModel):
    """Offline model settings. No internet access at runtime."""
    backend: str = "mock"            # mock | whisper | nllb | piper | bergamot | indictrans2
    offlinePath: str = "/models"     # directory with vendored model artifacts
    languages: list[str] = ["en", "bn", "gu", "hi", "kn", "ml", "mr", "pa", "ta", "te", "ur"]  # supported language codes


class SocketConfig(BaseModel):
    """Outbound downstream WebSocket forwarding."""
    enabled: bool = False            # true | false
    url: str = ""                    # ws://host:port (empty = log-only mode)
    reconnectDelayMs: int = 1000     # >= 0 ms
    maxReconnectDelayMs: int = 30000 # >= reconnectDelayMs
    maxRetries: int = -1             # -1 = infinite, else >= 0


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 50010                # IANA dynamic range 49152-65535

    @field_validator("port")
    @classmethod
    def _check_port(cls, v: int) -> int:
        if not 49152 <= v <= 65535:
            raise ValueError(
                f"port {v} outside safe dynamic range [49152, 65535]")
        return v


class BaseConfig(BaseModel):
    logLevel: str = "INFO"           # DEBUG|INFO|WARNING|ERROR
    natsUrl: str = "nats://broker:4222"
    natsUser: Optional[str] = None
    natsPass: Optional[str] = None
    server: ServerConfig = ServerConfig()
    audio: AudioConfig = AudioConfig()
    model: ModelConfig = ModelConfig()
    socket: SocketConfig = SocketConfig()

    @classmethod
    def load(cls: Type[T], path: Optional[str] = None, env_prefix: str = ENV_PREFIX) -> T:
        # Seed with current defaults so env overrides resolve even when no
        # YAML file exists (e.g. running services directly on the laptop).
        data = cls._defaults()
        if path and os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as fh:
                loaded = yaml.safe_load(fh) or {}
                data = _deep_merge(data, loaded)
        _apply_env_overrides(data, env_prefix)
        return cls(**data)

    @classmethod
    def _defaults(cls) -> dict[str, Any]:
        """Build a dict of the model's defaults using the exact field names,
        so ``_set_path`` can resolve STTS_* env vars against them."""
        out: dict[str, Any] = {}
        for name, field in cls.model_fields.items():
            if field.is_required():
                continue
            value = field.get_default(call_default_factory=True)
            if isinstance(value, BaseModel):
                value = value.model_dump()
            out[name] = value
        return out


def _deep_merge(base: dict, extra: dict) -> dict:
    out = dict(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _apply_env_overrides(data: dict, prefix: str) -> None:
    """Apply STTS_* env vars onto nested config dict.

    Env segments map to YAML keys case-insensitively and underscore-less, so
    ``STTS_AUDIO_OUTPUT_DIR`` and ``STTS_AUDIO_OUTPUTDIR`` both reach
    ``audio.outputDir``.
    """
    for env_name, value in os.environ.items():
        if not env_name.startswith(prefix):
            continue
        parts = env_name[len(prefix):].lower().split("_")
        _set_path(data, parts, _coerce(value))


def _set_path(data: dict, parts: list[str], value: Any) -> bool:
    """Set ``value`` at the nested path described by ``parts``.

    Parts are matched greedily against existing keys, ignoring case and
    underscores (so ``nats_url`` matches ``natsUrl``). Returns False when no
    key matches, in which case the override is ignored (prevents typos).
    """
    if not parts:
        return False
    node = data
    i = 0
    while i < len(parts):
        match: Optional[str] = None
        consumed = 0
        for j in range(len(parts), i, -1):
            key = _find_key(node, "_".join(parts[i:j]))
            if key is not None:
                match, consumed = key, j
                break
        if match is None:
            return False
        if consumed == len(parts):
            current = node[match]
            if isinstance(current, list) and isinstance(value, str):
                value = [item.strip() for item in value.split(",") if item.strip()]
            node[match] = value
            return True
        node = node[match]
        i = consumed
    return False


def _find_key(node: dict, target: str) -> Optional[str]:
    norm = target.lower().replace("_", "")
    for key in node:
        if key.lower().replace("_", "") == norm:
            return key
    return None


def _coerce(value: str) -> Any:
    lowered = value.strip().lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value
