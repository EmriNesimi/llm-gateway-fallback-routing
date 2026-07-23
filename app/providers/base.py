from abc import ABC, abstractmethod
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


class ProviderError(Exception):
    """Raised when a provider call fails in a way that should trigger fallback."""


class BaseProvider(ABC):
    name: str

    @abstractmethod
    async def chat(self, model: str, messages: list[ChatMessage]) -> ChatResponse:
        ...
