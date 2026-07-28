from fastapi import Depends, HTTPException, status

from app.core.config import settings
from app.core.redis_client import get_redis
from app.ratelimit.token_bucket import TokenBucketLimiter
from app.security.auth import require_api_key

_limiter = TokenBucketLimiter(
    redis=get_redis(),
    capacity=settings.rate_limit_capacity,
    refill_rate=settings.rate_limit_refill_per_sec,
)


async def enforce_rate_limit(api_key: str = Depends(require_api_key)) -> str:
    if not await _limiter.allow(key=api_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="rate limit exceeded, slow down",
        )
    return api_key
