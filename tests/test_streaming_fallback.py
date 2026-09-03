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

    async def chat(self, model, messages, params=None):
        raise NotImplementedError

    async def chat_stream(self, model, messages, params=None):
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


# --------------------------------------------------------------------------
# The same refusals, on the streaming path
# --------------------------------------------------------------------------
#
# chat_stream duplicates chat's skip logic rather than sharing it, so the
# budget and breaker checks needed covering twice. Duplicated logic that is
# tested once is exactly how the two halves drift apart.


class _NonRetryableProvider(BaseProvider):
    """Fails before the first chunk with a failure worth no retries — a 4xx
    shape: bad request, unknown model, invalid key."""

    name = "nonretryable"

    def __init__(self):
        self.calls = 0

    async def chat(self, model, messages, params=None):
        raise NotImplementedError

    async def chat_stream(self, model, messages, params=None):
        self.calls += 1
        raise ProviderError(f"{self.name} rejected the request", retryable=False)
        yield  # pragma: no cover - makes this an async generator


@pytest.mark.asyncio
async def test_streaming_skips_a_provider_that_is_out_of_budget():
    broke = FakeStreamProvider("broke")
    spare = FakeStreamProvider("spare")
    router = FallbackRouter(
        chain=[(broke, "model-a", _breaker()), (spare, "model-b", _breaker())],
        retry_attempts=0,
        retry_backoff_seconds=0,
    )

    chunks = [c async for c in router.chat_stream([], skip_providers={"broke"})]

    assert broke.calls == 0
    assert "".join(c.content for c in chunks) == "hello world"


@pytest.mark.asyncio
async def test_streaming_skips_a_provider_with_an_open_breaker():
    tripped = CircuitBreaker(failure_threshold=1, cooldown_seconds=60)
    tripped.record_failure()

    down = FakeStreamProvider("down")
    spare = FakeStreamProvider("spare")
    router = FallbackRouter(
        chain=[(down, "model-a", tripped), (spare, "model-b", _breaker())],
        retry_attempts=0,
        retry_backoff_seconds=0,
    )

    chunks = [c async for c in router.chat_stream([])]

    assert down.calls == 0
    assert "".join(c.content for c in chunks) == "hello world"


@pytest.mark.asyncio
async def test_streaming_every_breaker_open_attempts_nothing(caplog):
    import logging

    a_breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=60)
    a_breaker.record_failure()
    b_breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=60)
    b_breaker.record_failure()

    a, b = FakeStreamProvider("a"), FakeStreamProvider("b")
    router = FallbackRouter(
        chain=[(a, "model-a", a_breaker), (b, "model-b", b_breaker)],
        retry_attempts=0,
        retry_backoff_seconds=0,
    )

    with caplog.at_level(logging.ERROR):
        with pytest.raises(AllProvidersFailedError):
            [c async for c in router.chat_stream([])]

    assert a.calls == 0 and b.calls == 0
    assert "nothing was attempted" in caplog.text


@pytest.mark.asyncio
async def test_a_non_retryable_stream_failure_falls_back_without_retrying():
    """retry_attempts is 2 here. A retryable failure would burn both against
    the same provider first; a non-retryable one must not, because a 4xx fails
    identically every time and the retries are pure added latency."""
    doomed = _NonRetryableProvider()
    spare = FakeStreamProvider("spare")
    router = FallbackRouter(
        chain=[(doomed, "model-a", _breaker()), (spare, "model-b", _breaker())],
        retry_attempts=2,
        retry_backoff_seconds=0,
    )

    chunks = [c async for c in router.chat_stream([])]

    assert doomed.calls == 1, "a non-retryable failure was retried anyway"
    assert "".join(c.content for c in chunks) == "hello world"


class _EmptyStreamProvider(BaseProvider):
    """Ends the stream without yielding anything — no chunks, no error."""

    name = "empty"

    def __init__(self):
        self.calls = 0

    async def chat(self, model, messages, params=None):
        raise NotImplementedError

    async def chat_stream(self, model, messages, params=None):
        self.calls += 1
        return
        yield  # pragma: no cover - makes this an async generator


@pytest.mark.asyncio
async def test_a_provider_that_streams_nothing_falls_back():
    """A stream that ends immediately is a failure, not an empty answer.

    Nothing raises here — the provider just stops — so without turning
    StopAsyncIteration into a ProviderError the router would treat it as a
    successful empty response and hand the caller a blank reply, having
    skipped every remaining provider in the chain. The caller gets nothing and
    no error explaining why.
    """
    empty = _EmptyStreamProvider()
    spare = FakeStreamProvider("spare")
    router = FallbackRouter(
        chain=[(empty, "model-a", _breaker()), (spare, "model-b", _breaker())],
        retry_attempts=0,
        retry_backoff_seconds=0,
    )

    chunks = [c async for c in router.chat_stream([])]

    assert empty.calls == 1
    assert "".join(c.content for c in chunks) == "hello world"


@pytest.mark.asyncio
async def test_every_provider_streaming_nothing_is_an_error_not_an_empty_reply():
    """The end of that logic: if the whole chain produces no chunks, the
    caller must get a failure rather than a successful blank."""
    a, b = _EmptyStreamProvider(), _EmptyStreamProvider()
    router = FallbackRouter(
        chain=[(a, "model-a", _breaker()), (b, "model-b", _breaker())],
        retry_attempts=0,
        retry_backoff_seconds=0,
    )

    with pytest.raises(AllProvidersFailedError):
        [c async for c in router.chat_stream([])]
