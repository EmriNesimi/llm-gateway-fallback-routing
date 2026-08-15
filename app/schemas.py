from typing import Literal

from pydantic import BaseModel, Field


class ChatMessageIn(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1)


class ChatRequest(BaseModel):
    # max_length matches AuditLogEntry.requested_model's String(255) column.
    # An oversized value wouldn't break /v1/chat itself (record_audit_log
    # catches DB failures — see decision 004) but would silently and
    # permanently lose audit logging for that caller's traffic on Postgres,
    # same class of gap as the X-Request-ID length cap.
    model: str = Field(min_length=1, max_length=255)
    # Must be non-empty: an empty list is a client mistake, not something
    # any provider can usefully answer. Without this, it was passing our
    # validation, burning a real call against every provider in the fallback
    # chain (each rejecting it for the same reason), and only then surfacing
    # as an opaque 502 — instead of failing fast here with a clear 422.
    messages: list[ChatMessageIn] = Field(min_length=1)


class ChatResponseOut(BaseModel):
    content: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
