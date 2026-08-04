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
