import pytest

from app.providers.base import BaseProvider, ChatMessage, ChatResponse, ProviderError
from app.routing.circuit_breaker import CircuitBreaker
from app.routing.fallback import AllProvidersFailedError, FallbackRouter


class FakeProvider(BaseProvider):
    def __init__(self, name: str, fail_times: int = 0):
        self.name = name
        self._fail_times = fail_times
        self.calls = 0

    async def chat(self, model, messages):
        self.calls += 1
        if self.calls <= self._fail_times:
            raise ProviderError(f"{self.name} transient failure #{self.calls}")
        return ChatResponse(
            content="ok", provider=self.name, model=model, input_tokens=1, output_tokens=1
        )

    async def chat_stream(self, model, messages):
        raise NotImplementedError("not exercised by these tests")
        yield  # pragma: no cover - makes this an async generator


class DatedSnapshotProvider(BaseProvider):
    """Simulates a provider that echoes back a dated snapshot instead of the
    requested model (e.g. OpenAI returning "gpt-4o-mini-2024-07-18" for a
    "gpt-4o-mini" request) — this broke cost lookups until the router was
    fixed to bill against the requested model, not the echoed one."""

    name = "dated"

    async def chat(self, model, messages):
        return ChatResponse(
            content="ok",
            provider=self.name,
            model=f"{model}-2024-07-18",
            input_tokens=1,
            output_tokens=1,
        )

    async def chat_stream(self, model, messages):
        raise NotImplementedError("not exercised by these tests")
        yield  # pragma: no cover - makes this an async generator


def _breaker():
    return CircuitBreaker(failure_threshold=99, cooldown_seconds=60)


@pytest.mark.asyncio
async def test_retries_before_falling_back():
    flaky = FakeProvider("flaky", fail_times=1)
    router = FallbackRouter(
        chain=[(flaky, "model-a", _breaker())],
        retry_attempts=1,
        retry_backoff_seconds=0,
    )

    result = await router.chat([ChatMessage(role="user", content="hi")])

    assert result.provider == "flaky"
    assert flaky.calls == 2  # first call failed, retry succeeded


@pytest.mark.asyncio
async def test_falls_back_after_exhausting_retries():
    always_fails = FakeProvider("always_fails", fail_times=99)
    backup = FakeProvider("backup", fail_times=0)
    router = FallbackRouter(
        chain=[(always_fails, "model-a", _breaker()), (backup, "model-b", _breaker())],
        retry_attempts=1,
        retry_backoff_seconds=0,
    )

    result = await router.chat([ChatMessage(role="user", content="hi")])

    assert result.provider == "backup"
    assert always_fails.calls == 2  # initial attempt + 1 retry, then gave up


@pytest.mark.asyncio
async def test_raises_when_all_providers_exhausted():
    always_fails = FakeProvider("always_fails", fail_times=99)
    router = FallbackRouter(
        chain=[(always_fails, "model-a", _breaker())],
        retry_attempts=0,
        retry_backoff_seconds=0,
    )

    with pytest.raises(AllProvidersFailedError):
        await router.chat([ChatMessage(role="user", content="hi")])


class NonRetryableProvider(BaseProvider):
    """Simulates a provider rejecting the request outright (e.g. a 400 for
    an invalid model) — every call fails identically, so retrying is pure
    waste. Tracks call count to prove the router doesn't burn retries here."""

    name = "non_retryable"

    def __init__(self):
        self.calls = 0

    async def chat(self, model, messages):
        self.calls += 1
        raise ProviderError("bad request", retryable=False)

    async def chat_stream(self, model, messages):
        raise NotImplementedError("not exercised by these tests")
        yield  # pragma: no cover - makes this an async generator


@pytest.mark.asyncio
async def test_non_retryable_error_skips_remaining_retries():
    bad = NonRetryableProvider()
    backup = FakeProvider("backup", fail_times=0)
    router = FallbackRouter(
        chain=[(bad, "model-a", _breaker()), (backup, "model-b", _breaker())],
        retry_attempts=3,
        retry_backoff_seconds=0,
    )

    result = await router.chat([ChatMessage(role="user", content="hi")])

    assert result.provider == "backup"
    # Exactly one call — none of the 3 configured retries were burned
    # against a provider whose failure was never going to change.
    assert bad.calls == 1


@pytest.mark.asyncio
async def test_retryable_error_still_retries_before_falling_back():
    flaky = FakeProvider("flaky", fail_times=1)  # default ProviderError is retryable
    router = FallbackRouter(
        chain=[(flaky, "model-a", _breaker())],
        retry_attempts=1,
        retry_backoff_seconds=0,
    )

    result = await router.chat([ChatMessage(role="user", content="hi")])

    assert result.provider == "flaky"
    assert flaky.calls == 2


@pytest.mark.asyncio
async def test_result_model_is_requested_model_not_providers_echo():
    provider = DatedSnapshotProvider()
    router = FallbackRouter(
        chain=[(provider, "gpt-4o-mini", _breaker())],
        retry_attempts=0,
        retry_backoff_seconds=0,
    )

    result = await router.chat([ChatMessage(role="user", content="hi")])

    assert result.model == "gpt-4o-mini"
