"""One Redis client for the process, built on first use.

The rate limiter and both spend ledgers share it. Tests swap it out per
test through the autouse isolated_redis fixture; anything that captured the
client at import time is patched there by hand.
"""

from functools import lru_cache

from redis.asyncio import Redis

from app.core.config import settings


@lru_cache
def get_redis() -> Redis:
    """The process-wide async Redis client, created on first call."""
    return Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=settings.redis_connect_timeout_seconds,
        socket_timeout=settings.redis_socket_timeout_seconds,
    )
