import fakeredis.aioredis
import pytest

from app.ratelimit.token_bucket import TokenBucketLimiter


@pytest.mark.asyncio
async def test_allows_up_to_capacity_then_blocks():
    redis = fakeredis.aioredis.FakeRedis()
    limiter = TokenBucketLimiter(redis=redis, capacity=3, refill_rate=0.0)

    assert await limiter.allow("key1") is True
    assert await limiter.allow("key1") is True
    assert await limiter.allow("key1") is True
    assert await limiter.allow("key1") is False


@pytest.mark.asyncio
async def test_separate_keys_have_separate_buckets():
    redis = fakeredis.aioredis.FakeRedis()
    limiter = TokenBucketLimiter(redis=redis, capacity=1, refill_rate=0.0)

    assert await limiter.allow("a") is True
    assert await limiter.allow("a") is False
    assert await limiter.allow("b") is True
