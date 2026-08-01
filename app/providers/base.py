from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass


@dataclass
class ChatMessage:
    role: str
    content: str


@dataclass
class ChatResponse:
    content: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int


@dataclass
class StreamChunk:
    """One piece of a streamed response. `done` marks the final chunk, which
    carries the usage totals (providers report token counts once, at the end)."""

    content: str
    done: bool = False
    input_tokens: int = 0
    output_tokens: int = 0


class ProviderError(Exception):
    """Raised when a provider call fails in a way that should trigger fallback."""


class BaseProvider(ABC):
    name: str

    @abstractmethod
    async def chat(self, model: str, messages: list[ChatMessage]) -> ChatResponse:
        ...

    @abstractmethod
    def chat_stream(
        self, model: str, messages: list[ChatMessage]
    ) -> AsyncIterator[StreamChunk]:
        ...
