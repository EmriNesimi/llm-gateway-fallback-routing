"""The hard per-provider spend ceiling.

This is the control standing between a bug and a real bill, so the tests here
are about the ways a cap stops being a cap: resetting, being multiplied by the
number of API keys, being checked before a request whose cost isn't bounded,
and being raced by concurrent requests that all read the same pre-call total.
"""

import asyncio

import fakeredis.aioredis
import pytest

from app.budget.provider_budget import (
    FREE_PROVIDERS,
    ProviderBudget,
    ProviderBudgetExhausted,
)


@pytest.fixture
def budget():
    return ProviderBudget(redis=fakeredis.aioredis.FakeRedis(), cap_usd=4.0)


# --------------------------------------------------------------------------
# The ceiling holds
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spend_stops_at_the_cap(budget):
    for _ in range(8):
        await budget.reserve("anthropic", 0.50)
        await budget.settle("anthropic", 0.50, 0.50)

    assert await budget.spent("anthropic") == pytest.approx(4.0)
    with pytest.raises(ProviderBudgetExhausted):
        await budget.reserve("anthropic", 0.50)


@pytest.mark.asyncio
async def test_a_refused_request_does_not_consume_budget(budget):
    """The reservation has to come back on refusal, or repeatedly hitting an
    exhausted provider would inflate recorded spend past the cap."""
    await budget.reserve("openai", 4.0)
    await budget.settle("openai", 4.0, 4.0)

    for _ in range(5):
        with pytest.raises(ProviderBudgetExhausted):
            await budget.reserve("openai", 1.0)

    assert await budget.spent("openai") == pytest.approx(4.0)


@pytest.mark.asyncio
async def test_concurrent_requests_cannot_all_pass_the_same_check(budget):
    """The reason for reserving rather than checking. Twenty simultaneous
    requests against a $4 cap at $1 each must admit four, not twenty — every
    one of them observes `spent == 0` if the check happens before the call."""

    async def attempt():
        try:
            await budget.reserve("openai", 1.0)
            return True
        except ProviderBudgetExhausted:
            return False

    admitted = sum(await asyncio.gather(*[attempt() for _ in range(20)]))

    assert admitted == 4
    assert await budget.spent("openai") == pytest.approx(4.0)


@pytest.mark.asyncio
async def test_one_oversized_request_cannot_exceed_the_cap(budget):
    """Reserving the worst case is what stops a single request costing more
    than the headroom left. A plain `spent < cap` check would admit this."""
    await budget.reserve("anthropic", 3.9)
    await budget.settle("anthropic", 3.9, 3.9)

    with pytest.raises(ProviderBudgetExhausted):
        await budget.reserve("anthropic", 0.5)


# --------------------------------------------------------------------------
# Reserve / settle bookkeeping
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_settle_refunds_the_difference(budget):
    await budget.reserve("openai", 1.0)
    await budget.settle("openai", 1.0, 0.02)

    assert await budget.spent("openai") == pytest.approx(0.02)


@pytest.mark.asyncio
async def test_settling_a_failed_request_refunds_everything(budget):
    await budget.reserve("openai", 1.0)
    await budget.settle("openai", 1.0, 0.0)

    assert await budget.spent("openai") == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_unreserved_spend_can_still_be_charged(budget):
    """The escape hatch for a stream already partly generated when the client
    hung up — real spend with no live reservation."""
    await budget.record_unreserved("anthropic", 0.25)

    assert await budget.spent("anthropic") == pytest.approx(0.25)


# --------------------------------------------------------------------------
# Scope
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_ledger_is_not_keyed_by_caller(budget):
    """The whole point of this existing alongside the per-key monthly cap:
    minting more client keys must not raise the operator's ceiling."""
    for _ in range(4):
        await budget.reserve("openai", 1.0)
        await budget.settle("openai", 1.0, 1.0)

    # A brand-new client key changes nothing.
    with pytest.raises(ProviderBudgetExhausted):
        await budget.reserve("openai", 0.01)


@pytest.mark.asyncio
async def test_providers_have_independent_ceilings(budget):
    await budget.reserve("openai", 4.0)
    await budget.settle("openai", 4.0, 4.0)

    assert await budget.is_exhausted("openai")
    assert not await budget.is_exhausted("anthropic")
    await budget.reserve("anthropic", 1.0)  # must not raise


@pytest.mark.asyncio
async def test_the_ledger_never_expires(budget):
    """A monthly cap of $1 permits $12 a year. When the limit is a prepaid
    balance rather than a spending rate, resetting defeats the purpose."""
    await budget.reserve("openai", 1.0)
    await budget.settle("openai", 1.0, 1.0)

    ttl = await budget._redis.ttl(budget._key("openai"))
    assert ttl == -1, f"key expires in {ttl}s — the ceiling would reset"


@pytest.mark.asyncio
async def test_local_providers_are_exempt(budget):
    """Ollama bills nothing, so capping it would only break the free fallback
    that exists for when the paid ones are gone."""
    assert "ollama" in FREE_PROVIDERS

    await budget.reserve("ollama", 999.0)

    assert not await budget.is_exhausted("ollama")
    assert await budget.remaining("ollama") == float("inf")


@pytest.mark.asyncio
async def test_exhausted_providers_reports_only_the_spent_ones(budget):
    await budget.reserve("openai", 4.0)
    await budget.settle("openai", 4.0, 4.0)

    assert await budget.exhausted_providers(["openai", "anthropic", "ollama"]) == {"openai"}


# --------------------------------------------------------------------------
# Failure behaviour
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_unreachable_redis_refuses_rather_than_allows():
    """Fails closed. Being unable to prove there's budget left is not the same
    as having budget left, and this is the last line before a real bill."""

    class DeadRedis:
        async def incrbyfloat(self, *a, **k):
            raise ConnectionError("redis is down")

        async def get(self, *a, **k):
            raise ConnectionError("redis is down")

    budget = ProviderBudget(redis=DeadRedis(), cap_usd=4.0)  # type: ignore[arg-type]

    with pytest.raises(ConnectionError):
        await budget.reserve("openai", 0.01)


# --------------------------------------------------------------------------
# Visibility
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_ledger_is_visible_as_metrics(budget):
    """gateway_cost_usd_total is a counter — it says what has gone, never what
    is left, and "left" is the number worth alerting on. Without a gauge the
    only warning is a 402 after the money is already spent."""
    from prometheus_client import REGISTRY

    await budget.reserve("openai", 1.5)
    await budget.settle("openai", 1.5, 1.5)
    await budget.spent("openai")

    spent = REGISTRY.get_sample_value(
        "gateway_provider_budget_spent_usd", {"provider": "openai"}
    )
    remaining = REGISTRY.get_sample_value(
        "gateway_provider_budget_remaining_usd", {"provider": "openai"}
    )

    assert spent == pytest.approx(1.5)
    assert remaining == pytest.approx(2.5)  # $4 cap


@pytest.mark.asyncio
async def test_remaining_never_reports_negative(budget):
    """An overshoot shouldn't render as a negative bar on a dashboard; zero
    headroom is the honest reading."""
    await budget.record_unreserved("anthropic", 99.0)
    await budget.spent("anthropic")

    from prometheus_client import REGISTRY

    assert REGISTRY.get_sample_value(
        "gateway_provider_budget_remaining_usd", {"provider": "anthropic"}
    ) == 0.0
