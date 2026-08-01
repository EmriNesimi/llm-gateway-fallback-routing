import pytest

from app.providers.base import BaseProvider, ProviderError, StreamChunk
from app.routing.circuit_breaker import CircuitBreaker
from app.routing.fallback import AllProvidersFailedError, FallbackRouter


class FakeStreamProvider(BaseProvider):
    """Streaming-only fake provider: `chat` is unused by these tests."""

    def __init__(self, name: str, fail_before_first_chunk: int = 0, fail_after_chunks=None):
        self.name = name
        self._fail_before_first_chunk = fail_before_first_chunk
        self._fail_after_chunks = fail_after_chunks
        self._content = ["hello", " world"]
        self.calls = 0

    async def chat(self, model, messages):
        raise NotImplementedError

    async def chat_stream(self, model, messages):
        self.calls += 1
        if self.calls <= self._fail_before_first_chunk:
            raise ProviderError(f"{self.name} failed before first chunk (call {self.calls})")

        emitted = 0
        for piece in self._content:
            if self._fail_after_chunks is not None and emitted >= self._fail_after_chunks:
                raise ProviderError(f"{self.name} failed mid-stream")
            yield StreamChunk(content=piece)
            emitted += 1
        yield StreamChunk(content="", done=True, input_tokens=5, output_tokens=7)


def _breaker():
    return CircuitBreaker(failure_threshold=99, cooldown_seconds=60)


async def _collect(router, messages):
    return [chunk async for chunk in router.chat_stream(messages)]


@pytest.mark.asyncio
async def test_stream_retries_before_first_chunk_then_succeeds():
    flaky = FakeStreamProvider("flaky", fail_before_first_chunk=1)
    router = FallbackRouter(
        chain=[(flaky, "model-a", _breaker())], retry_attempts=1, retry_backoff_seconds=0
    )

    chunks = await _collect(router, [])

    assert [c.content for c in chunks] == ["hello", " world", ""]
    assert chunks[-1].done is True
    assert chunks[-1].provider == "flaky"
    assert flaky.calls == 2


@pytest.mark.asyncio
async def test_stream_falls_back_before_committing():
    primary = FakeStreamProvider("primary", fail_before_first_chunk=99)
    backup = FakeStreamProvider("backup")
    router = FallbackRouter(
        chain=[(primary, "model-a", _breaker()), (backup, "model-b", _breaker())],
        retry_attempts=0,
        retry_backoff_seconds=0,
    )

    chunks = await _collect(router, [])

    assert chunks[0].provider == "backup"
    assert chunks[0].content == "hello"


@pytest.mark.asyncio
async def test_stream_mid_failure_after_commit_raises_but_keeps_partial_chunks():
    provider = FakeStreamProvider("provider", fail_after_chunks=1)
    router = FallbackRouter(
        chain=[(provider, "model-a", _breaker())], retry_attempts=0, retry_backoff_seconds=0
    )

    received = []
    with pytest.raises(AllProvidersFailedError):
        async for chunk in router.chat_stream([]):
            received.append(chunk)

    assert [c.content for c in received] == ["hello"]


@pytest.mark.asyncio
async def test_stream_raises_when_all_providers_fail_before_first_chunk():
    always_fails = FakeStreamProvider("always_fails", fail_before_first_chunk=99)
    router = FallbackRouter(
        chain=[(always_fails, "model-a", _breaker())],
        retry_attempts=0,
        retry_backoff_seconds=0,
    )

    with pytest.raises(AllProvidersFailedError):
        async for _ in router.chat_stream([]):
            pass
