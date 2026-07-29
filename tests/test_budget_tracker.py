import fakeredis.aioredis
import pytest

from app.budget.tracker import BudgetTracker


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
