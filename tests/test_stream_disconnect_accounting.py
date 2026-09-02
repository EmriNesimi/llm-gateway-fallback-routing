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
