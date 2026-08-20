from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, fields


@dataclass
class ChatMessage:
    role: str
    content: str


@dataclass
class SamplingParams:
    """Generation controls a caller can set, in provider-neutral form.

    Every field is optional, and `None` means "don't send it" rather than
    "send our default". That distinction matters: each provider has its own
    default for these, and substituting one of ours would silently change the
    behavior of every caller who never asked to change it. Only the four
    controls that map cleanly onto all three providers live here — anything
    more OpenAI-specific is reported back to the client as unsupported rather
    than quietly approximated.
    """

    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    stop: list[str] | None = None

    def is_empty(self) -> bool:
        return all(getattr(self, f.name) is None for f in fields(self))

    def set_names(self) -> list[str]:
        """Which controls the caller actually set, for logging and headers."""
        return [f.name for f in fields(self) if getattr(self, f.name) is not None]


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
    carries the usage totals (providers report token counts once, at the end).

    `provider`/`model` are left blank by the provider adapters themselves —
    the router fills them in once a provider is committed to, since that's
    the only place that knows which provider ended up serving the request.
    """

    content: str
    done: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    provider: str = ""
    model: str = ""


class ProviderError(Exception):
    """Raised when a provider call fails in a way that should trigger fallback.

    `retryable` distinguishes two very different failure shapes: a timeout or
    connection drop is worth retrying against the SAME provider (it might
    just be transient noise), but a 4xx client error (bad request, unknown
    model, invalid API key) will fail identically on every retry — burning
    `provider_retry_attempts` retries against it before falling back wastes
    real latency for zero chance of a different outcome. Defaults to True so
    provider code that doesn't reason about this explicitly keeps today's
    retry-then-fallback behavior; provider adapters set it to False when they
    can positively identify a non-transient failure.
    """

    def __init__(self, message: str, *, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


def is_retryable_status_code(status_code: int) -> bool:
    """429 (rate limited) and 5xx are worth retrying — the same request might
    succeed a moment later. Any other 4xx (bad request, invalid model,
    auth failure) will fail identically every time; retrying it is pure
    wasted latency. Shared by every provider adapter so the rule is defined
    once, not reimplemented slightly differently per provider."""
    return status_code == 429 or status_code >= 500


class BaseProvider(ABC):
    name: str

    @abstractmethod
    async def chat(
        self,
        model: str,
        messages: list[ChatMessage],
        params: SamplingParams | None = None,
    ) -> ChatResponse:
        ...

    @abstractmethod
    def chat_stream(
        self,
        model: str,
        messages: list[ChatMessage],
        params: SamplingParams | None = None,
    ) -> AsyncIterator[StreamChunk]:
        ...


class UnconfiguredProvider(BaseProvider):
    """Stand-in for a provider with no API key set (e.g. OPENAI_API_KEY
    unset). Fails instantly and non-retryably, with zero network calls —
    the alternative, constructing the real SDK client with an empty key, is
    actively dangerous: the OpenAI SDK raises at *construction* time on a
    missing key, which previously crashed build_router() outright and took
    the whole fallback chain down with it (Anthropic/Ollama never even got a
    chance), rather than the intended "skip this one, fall back" behavior.
    """

    def __init__(self, name: str):
        self.name = name

    async def chat(
        self,
        model: str,
        messages: list[ChatMessage],
        params: SamplingParams | None = None,
    ) -> ChatResponse:
        raise ProviderError(f"{self.name} is not configured (no API key set)", retryable=False)

    async def chat_stream(
        self,
        model: str,
        messages: list[ChatMessage],
        params: SamplingParams | None = None,
    ) -> AsyncIterator[StreamChunk]:
        raise ProviderError(f"{self.name} is not configured (no API key set)", retryable=False)
        yield  # pragma: no cover - unreachable, makes this an async generator
