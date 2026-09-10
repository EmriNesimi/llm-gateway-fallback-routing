"""What happens when a client hangs up mid-stream.

Starlette closes the generator, which raises GeneratorExit at the yield.
Everything after the streaming loop — the spend recording — is skipped, so
before this was handled a disconnect recorded $0.00 against tokens the
provider had already generated and billed. The ledger never moved, and the
lifetime ceiling was unreachable by construction.

Both endpoints have their own generator with its own copy of the handler.
The HTTP tests elsewhere read every response to completion, so neither
handler is reached by any of them — abandoning the generator is the only way
to exercise what an aborted client actually does.
"""

import time

import fakeredis.aioredis
import pytest

import app.main as main_module
from app.budget.provider_budget import ProviderBudget
from app.providers.base import ChatMessage, StreamChunk


class _EndlessRouter:
    """Streams forever, so the test decides when to hang up rather than
    racing the end of a fixed list."""

    def __init__(self):
        self.name = "anthropic"

    async def chat_stream(self, messages, request_id="", params=None, skip_providers=None):
        while True:
            yield StreamChunk(
                content="x" * 300, provider="anthropic", model="claude-opus-5"
            )


@pytest.fixture
def budget(monkeypatch):
    b = ProviderBudget(redis=fakeredis.aioredis.FakeRedis(), cap_usd=4.0)
    monkeypatch.setattr(main_module, "provider_budget", b)
    return b


async def _consume_then_hang_up(generator, chunks=3):
    """Read a few chunks, then abandon the stream exactly as Starlette does."""
    taken = 0
    async for _ in generator:
        taken += 1
        if taken >= chunks:
            break
    await generator.aclose()


@pytest.mark.asyncio
async def test_hanging_up_on_v1_chat_stream_still_charges(budget, isolated_db):
    await budget.reserve("anthropic", 0.80)

    stream = main_module._event_stream(
        _EndlessRouter(),
        [ChatMessage(role="user", content="hi")],
        "test-client-key",
        "smart",
        "req-hangup-native",
        reservations={"anthropic": 0.80},
    )
    await _consume_then_hang_up(stream)

    spent = await budget.spent("anthropic")
    assert spent > 0, "a disconnect recorded nothing — the ceiling cannot advance"
    assert spent != pytest.approx(0.80), "the reservation was never settled"


@pytest.mark.asyncio
async def test_hanging_up_on_the_openai_endpoint_still_charges(budget, isolated_db):
    """The OpenAI-compatible endpoint has its own generator and its own copy
    of this handler. Duplicated accounting logic tested once is how the two
    halves drift apart."""
    await budget.reserve("anthropic", 0.80)

    stream = main_module._openai_event_stream(
        _EndlessRouter(),
        [ChatMessage(role="user", content="hi")],
        "test-client-key",
        "smart",
        "req-hangup-openai",
        completion_id="chatcmpl-test",
        created=int(time.time()),
        reservations={"anthropic": 0.80},
    )
    await _consume_then_hang_up(stream)

    spent = await budget.spent("anthropic")
    assert spent > 0, "a disconnect recorded nothing — the ceiling cannot advance"
    assert spent != pytest.approx(0.80), "the reservation was never settled"


def _aborted_count() -> float:
    from prometheus_client import REGISTRY

    return REGISTRY.get_sample_value(
        "gateway_requests_total", {"status": "aborted"}
    ) or 0.0


@pytest.mark.asyncio
async def test_a_disconnected_stream_is_counted(budget, isolated_db):
    """The disconnect branch incremented nothing, so a hung-up stream left
    gateway_requests_total untouched — while gateway_fallback_triggered_total
    had already counted it if a fallback served it.

    ProviderFallbackRateHigh divides one by the other, so those requests
    inflated the ratio, and in a disconnect-heavy window it was not bounded by
    1 at all. Both halves now count the same population.
    """
    before = _aborted_count()

    stream = main_module._event_stream(
        _EndlessRouter(),
        [ChatMessage(role="user", content="hi")],
        "test-client-key",
        "smart",
        "req-counted-abort",
        reservations={"anthropic": 0.80},
    )
    await _consume_then_hang_up(stream)

    assert _aborted_count() == before + 1


@pytest.mark.asyncio
async def test_an_aborted_stream_is_not_counted_as_an_error(budget, isolated_db):
    """A third status rather than reusing "error". Nothing failed — the client
    left — and folding the two together would make
    GatewayRequestsFailingAcrossWholeChain fire on people closing tabs."""
    from prometheus_client import REGISTRY

    def errors() -> float:
        return REGISTRY.get_sample_value(
            "gateway_requests_total", {"status": "error"}
        ) or 0.0

    before = errors()

    stream = main_module._openai_event_stream(
        _EndlessRouter(),
        [ChatMessage(role="user", content="hi")],
        "test-client-key",
        "smart",
        "req-abort-not-error",
        completion_id="chatcmpl-x",
        created=int(time.time()),
        reservations={"anthropic": 0.80},
    )
    await _consume_then_hang_up(stream)

    assert errors() == before, "an abandoned stream was recorded as a failure"
