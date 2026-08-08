"""Integration tests against a REAL Redis (not fakeredis).

fakeredis's Lua support (via lupa) has previously diverged from real Redis
behavior in subtle ways — these tests exist specifically to catch that class
of bug. They're skipped automatically if no Redis is reachable, since local
`pytest` runs shouldn't require one; CI always has a real Redis service.
"""

import os
import uuid

import pytest
import pytest_asyncio
from redis.asyncio import Redis

from app.budget.tracker import BudgetTracker
from app.ratelimit.token_bucket import TokenBucketLimiter

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")


@pytest_asyncio.fixture
async def real_redis():
    client = Redis.from_url(REDIS_URL, decode_responses=True)
    try:
        await client.ping()
    except Exception:
        pytest.skip(f"no Redis reachable at {REDIS_URL}")

    yield client

    await client.aclose()


@pytest.mark.asyncio
async def test_token_bucket_against_real_redis(real_redis):
    key = f"test:{uuid.uuid4().hex}"
    limiter = TokenBucketLimiter(redis=real_redis, capacity=3, refill_rate=0.0)

    try:
        assert await limiter.allow(key) is True
        assert await limiter.allow(key) is True
        assert await limiter.allow(key) is True
        assert await limiter.allow(key) is False
    finally:
        await real_redis.delete(f"ratelimit:{key}")


@pytest.mark.asyncio
async def test_budget_tracker_against_real_redis(real_redis):
    api_key = f"test-{uuid.uuid4().hex}"
    tracker = BudgetTracker(redis=real_redis, monthly_cap_usd=1.0)

    try:
        assert await tracker.has_budget(api_key) is True
        await tracker.record_spend(api_key, 0.6)
        assert await tracker.spent_usd(api_key) == pytest.approx(0.6)
        await tracker.record_spend(api_key, 0.5)
        assert await tracker.has_budget(api_key) is False
    finally:
        await real_redis.delete(tracker._key(api_key))
