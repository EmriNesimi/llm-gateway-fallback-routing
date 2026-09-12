import asyncio

import fakeredis.aioredis
import pytest

from app.budget.tracker import BudgetTracker, KeyBudgetExhausted


@pytest.mark.asyncio
async def test_has_budget_until_cap_reached():
    redis = fakeredis.aioredis.FakeRedis()
    tracker = BudgetTracker(redis=redis, monthly_cap_usd=1.0)

    assert await tracker.has_budget("key1") is True

    await tracker.record_spend("key1", 0.6)
    assert await tracker.spent_usd("key1") == pytest.approx(0.6)
    assert await tracker.has_budget("key1") is True

    await tracker.record_spend("key1", 0.5)
    assert await tracker.has_budget("key1") is False


@pytest.mark.asyncio
async def test_keys_are_tracked_independently():
    redis = fakeredis.aioredis.FakeRedis()
    tracker = BudgetTracker(redis=redis, monthly_cap_usd=1.0)

    await tracker.record_spend("key1", 5.0)

    assert await tracker.has_budget("key1") is False
    assert await tracker.has_budget("key2") is True


class _BrokenPipeline:
    def incrbyfloat(self, *a, **kw):
        return self

    def expire(self, *a, **kw):
        return self

    async def execute(self):
        raise ConnectionError("redis is down")


@pytest.mark.asyncio
async def test_record_spend_swallows_redis_failures(monkeypatch):
    redis = fakeredis.aioredis.FakeRedis()
    tracker = BudgetTracker(redis=redis, monthly_cap_usd=1.0)
    monkeypatch.setattr(redis, "pipeline", lambda: _BrokenPipeline())

    # Must not raise — a Redis outage here shouldn't discard a response the
    # provider has already returned (and already been paid for).
    await tracker.record_spend("key1", 0.5)


@pytest.mark.asyncio
async def test_reserve_admits_only_up_to_the_cap_under_concurrency():
    """The race in issue #15. Twenty simultaneous requests from one key used
    to all read the same pre-call total of $0 and all be admitted."""
    redis = fakeredis.aioredis.FakeRedis()
    tracker = BudgetTracker(redis=redis, monthly_cap_usd=1.0)

    async def attempt() -> bool:
        try:
            await tracker.reserve("key1", 0.25)
            return True
        except KeyBudgetExhausted:
            return False

    admitted = await asyncio.gather(*(attempt() for _ in range(20)))

    # $1.00 cap, $0.25 worst case each: exactly four fit, not twenty.
    assert sum(admitted) == 4
    assert await tracker.spent_usd("key1") == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_refused_reservation_is_handed_straight_back():
    """A refusal must not consume the headroom it was denied, or repeated
    refusals would drain a budget no request ever spent."""
    redis = fakeredis.aioredis.FakeRedis()
    tracker = BudgetTracker(redis=redis, monthly_cap_usd=1.0)

    await tracker.reserve("key1", 0.9)

    for _ in range(5):
        with pytest.raises(KeyBudgetExhausted):
            await tracker.reserve("key1", 0.5)

    assert await tracker.spent_usd("key1") == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_settle_replaces_the_reservation_with_the_real_cost():
    redis = fakeredis.aioredis.FakeRedis()
    tracker = BudgetTracker(redis=redis, monthly_cap_usd=1.0)

    await tracker.reserve("key1", 0.40)
    assert await tracker.spent_usd("key1") == pytest.approx(0.40)

    await tracker.settle("key1", reserved_usd=0.40, actual_usd=0.03)
    assert await tracker.spent_usd("key1") == pytest.approx(0.03)


@pytest.mark.asyncio
async def test_settling_a_failed_request_refunds_the_whole_reservation():
    redis = fakeredis.aioredis.FakeRedis()
    tracker = BudgetTracker(redis=redis, monthly_cap_usd=1.0)

    await tracker.reserve("key1", 0.40)
    await tracker.settle("key1", reserved_usd=0.40, actual_usd=0.0)

    assert await tracker.spent_usd("key1") == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_settle_without_a_reservation_charges_the_full_cost():
    """Free chains and post-abort estimates reach settle having reserved
    nothing. The cost still has to land on the ledger."""
    redis = fakeredis.aioredis.FakeRedis()
    tracker = BudgetTracker(redis=redis, monthly_cap_usd=1.0)

    await tracker.settle("key1", reserved_usd=0.0, actual_usd=0.07)

    assert await tracker.spent_usd("key1") == pytest.approx(0.07)


@pytest.mark.asyncio
async def test_reserve_keeps_the_monthly_expiry():
    """incrbyfloat on a missing key creates it without a TTL. Losing the
    expiry would turn the monthly budget into a lifetime one."""
    redis = fakeredis.aioredis.FakeRedis()
    tracker = BudgetTracker(redis=redis, monthly_cap_usd=1.0)

    await tracker.reserve("key1", 0.1)

    key = tracker._key("key1")
    assert await redis.ttl(key) > 0


@pytest.mark.asyncio
async def test_settle_swallows_redis_failures(monkeypatch):
    """Same reasoning as record_spend: settle runs after a provider has
    already answered and billed."""
    redis = fakeredis.aioredis.FakeRedis()
    tracker = BudgetTracker(redis=redis, monthly_cap_usd=1.0)
    monkeypatch.setattr(redis, "pipeline", lambda: _BrokenPipeline())

    await tracker.settle("key1", reserved_usd=0.4, actual_usd=0.1)


@pytest.mark.asyncio
async def test_reserve_does_not_swallow_redis_failures(monkeypatch):
    """Unlike settle. Reserve runs *before* the provider call, so being
    unable to prove there is budget left must refuse, not admit."""
    redis = fakeredis.aioredis.FakeRedis()
    tracker = BudgetTracker(redis=redis, monthly_cap_usd=1.0)
    monkeypatch.setattr(redis, "pipeline", lambda: _BrokenPipeline())

    with pytest.raises(ConnectionError):
        await tracker.reserve("key1", 0.1)


@pytest.mark.asyncio
async def test_reserving_nothing_touches_nothing():
    """A chain of only free providers reserves $0. That has to be a no-op
    rather than a round-trip that creates the key."""
    redis = fakeredis.aioredis.FakeRedis()
    tracker = BudgetTracker(redis=redis, monthly_cap_usd=1.0)

    await tracker.reserve("key1", 0.0)

    assert await redis.exists(tracker._key("key1")) == 0


@pytest.mark.asyncio
async def test_settling_at_exactly_the_reserved_cost_is_a_no_op():
    redis = fakeredis.aioredis.FakeRedis()
    tracker = BudgetTracker(redis=redis, monthly_cap_usd=1.0)

    await tracker.reserve("key1", 0.25)
    await tracker.settle("key1", reserved_usd=0.25, actual_usd=0.25)

    assert await tracker.spent_usd("key1") == pytest.approx(0.25)


class _FailAfterN:
    """Wraps the real pipeline factory, working N times and then failing.

    Takes the factory as an argument rather than reaching through the Redis
    instance: monkeypatching `redis.pipeline` and then calling
    `redis.pipeline()` from inside here would recurse into this stub instead
    of the real one, quietly making it a no-op that never fails at all.
    """

    def __init__(self, factory, n):
        self._factory, self._n = factory, n

    def __call__(self):
        self._inner = self._factory()
        return self

    def incrbyfloat(self, key, amount):
        self._inner.incrbyfloat(key, amount)
        return self

    def expire(self, *a, **kw):
        self._inner.expire(*a, **kw)
        return self

    async def execute(self):
        if self._n <= 0:
            raise ConnectionError("redis went away")
        self._n -= 1
        return await self._inner.execute()


@pytest.mark.asyncio
async def test_a_failed_refund_still_refuses_rather_than_erroring(monkeypatch, caplog):
    """The over-cap increment lands, then the hand-back fails.

    Being over the cap is true whether or not the refund landed, so the caller
    must still get KeyBudgetExhausted. Letting the refund's ConnectionError
    through instead turns a clean 402 into a 500 — the caller is told the
    gateway is broken when it is actually working and saying no.
    """
    import logging

    redis = fakeredis.aioredis.FakeRedis()
    tracker = BudgetTracker(redis=redis, monthly_cap_usd=1.0)

    # First execute succeeds (the over-cap increment); the second — the refund
    # — raises.
    monkeypatch.setattr(redis, "pipeline", _FailAfterN(redis.pipeline, 1))

    with caplog.at_level(logging.ERROR):
        with pytest.raises(KeyBudgetExhausted):
            await tracker.reserve("key1", 5.0)

    assert "stays claimed" in caplog.text, (
        "a stranded reservation has to be logged — it permanently reduces this"
        " caller's month with no request behind it"
    )
