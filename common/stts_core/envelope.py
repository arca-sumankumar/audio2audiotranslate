"""Typed message envelope shared across all services.

Pydantic v2 model. `payload` is a free-form dict whose schema depends on
`type` (see service docs in LLD2).
"""
from __future__ import annotations

import uuid
from typing import Any, Optional
from datetime import datetime, timezone

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1.0"

EVENT_TYPES = {
    "audio_in", "audio_chunk",
    "partial_transcript", "final_transcript",
    "audio_output",
    "job_started", "job_done", "job_failed",
    "error", "end",
}


def new_id() -> str:
    return str(uuid.uuid4())


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


class Envelope(BaseModel):
    schemaVersion: str = SCHEMA_VERSION
    messageId: str = Field(default_factory=new_id)
    sessionId: str
    jobId: Optional[str] = None
    seqNo: int = 0
    type: str
    sourceLanguage: Optional[str] = None
    targetLanguage: Optional[str] = None
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=now_utc)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Envelope":
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    # --- Dedup id used as the NATS Nats-Msg-Id header value ---
    # messageId is globally unique per event, so producer retries deduplicate
    # without colliding across stages (asr/mt both emit transcripts for a seq).
    @property
    def dedup_id(self) -> str:
        return self.messageId
