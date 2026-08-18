from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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
FORWARDED_COMPLETION_FIELDS = frozenset({"model", "messages", "stream"})


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str = Field(min_length=1, max_length=255)
    messages: list[ChatMessageIn] = Field(min_length=1)
    # OpenAI drives streaming with a body field rather than a separate path,
    # and its SDK relies on that, so this endpoint handles both modes.
    stream: bool = False

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
