from typing import Literal

from pydantic import BaseModel, Field


class ChatMessageIn(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1)


class ChatRequest(BaseModel):
    model: str
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
