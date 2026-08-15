from functools import lru_cache

from redis.asyncio import Redis

from app.core.config import settings


@lru_cache
def get_redis() -> Redis:
    return Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=settings.redis_connect_timeout_seconds,
        socket_timeout=settings.redis_socket_timeout_seconds,
    )
