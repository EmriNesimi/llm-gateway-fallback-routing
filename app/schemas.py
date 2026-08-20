from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.providers.base import SamplingParams


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


# ---------------------------------------------------------------------------
# OpenAI-compatible surface (/v1/chat/completions)
#
# These mirror OpenAI's Chat Completions shapes field for field, so an
# unmodified `openai` SDK — or anything built on it — can point its base_url
# at this gateway and work. The native /v1/chat shapes above stay as they are;
# this is an addition, not a replacement.
# ---------------------------------------------------------------------------

# Fields this gateway actually acts on. Anything else a client sends is
# accepted — rejecting it would break the drop-in compatibility that's the
# whole point — but reported rather than silently dropped, on the same
# reasoning as decision 009: a caller shouldn't have to guess that the
# temperature they set never reached a provider.
FORWARDED_COMPLETION_FIELDS = frozenset(
    {"model", "messages", "stream", "temperature", "top_p", "max_tokens", "stop"}
)


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str = Field(min_length=1, max_length=255)
    messages: list[ChatMessageIn] = Field(min_length=1)
    # OpenAI drives streaming with a body field rather than a separate path,
    # and its SDK relies on that, so this endpoint handles both modes.
    stream: bool = False

    # Bounds match OpenAI's own, so a request this gateway accepts is one the
    # upstream would have accepted too. Better a 422 naming the offending
    # field than a provider 400, which the router reads as a dead provider and
    # falls away from — the caller would get an answer from somewhere else
    # entirely rather than being told their value was out of range.
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, gt=0, le=1)
    max_tokens: int | None = Field(default=None, gt=0)
    stop: list[str] | str | None = None

    def sampling_params(self) -> SamplingParams:
        return SamplingParams(
            temperature=self.temperature,
            top_p=self.top_p,
            max_tokens=self.max_tokens,
            # OpenAI accepts either a bare string or a list here; every
            # provider downstream wants a list.
            stop=[self.stop] if isinstance(self.stop, str) else self.stop,
        )

    def ignored_params(self) -> list[str]:
        """Parameters the client set that this gateway won't act on."""
        declared = self.model_fields_set - FORWARDED_COMPLETION_FIELDS
        return sorted(declared | set(self.model_extra or {}))


class ChatCompletionMessage(BaseModel):
    role: Literal["assistant"] = "assistant"
    content: str


class ChatCompletionChoice(BaseModel):
    index: int = 0
    message: ChatCompletionMessage
    finish_reason: str = "stop"


class ChatCompletionUsage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatCompletionResponse(BaseModel):
    id: str
    object: Literal["chat.completion"] = "chat.completion"
    created: int
    # The real model that answered (e.g. "gpt-4o-mini"), not the chain name the
    # caller asked for — that matches OpenAI's semantics, and the chain is
    # reported separately via X-Gateway-Chain.
    model: str
    choices: list[ChatCompletionChoice]
    usage: ChatCompletionUsage
