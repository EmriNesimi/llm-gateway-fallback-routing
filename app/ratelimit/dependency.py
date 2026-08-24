import math

from fastapi import Depends, Header, HTTPException, Request, Response, status

from app.core.config import settings
from app.core.redis_client import get_redis
from app.ratelimit.token_bucket import TokenBucketLimiter
from app.security.auth import require_api_key

_limiter = TokenBucketLimiter(
    redis=get_redis(),
    capacity=settings.rate_limit_capacity,
    refill_rate=settings.rate_limit_refill_per_sec,
)

# How long to tell a rate-limited client to wait before retrying: roughly the
# time for one more token to refill.
_RETRY_AFTER_SECONDS = (
    max(1, math.ceil(1 / settings.rate_limit_refill_per_sec))
    if settings.rate_limit_refill_per_sec > 0
    else 60
)


async def enforce_rate_limit(
    request: Request,
    response: Response,
    api_key: str = Depends(require_api_key),
) -> str:
    allowed, remaining = await _limiter.check(key=api_key)

    # Stashed for endpoints (like streaming) that build their own Response
    # object and can't rely on FastAPI merging headers set here automatically.
    request.state.rate_limit_limit = _limiter.capacity
    request.state.rate_limit_remaining = max(0, int(remaining))

    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="rate limit exceeded, slow down",
            headers={
                "X-RateLimit-Limit": str(_limiter.capacity),
                "X-RateLimit-Remaining": "0",
                "Retry-After": str(_RETRY_AFTER_SECONDS),
            },
        )

    response.headers["X-RateLimit-Limit"] = str(_limiter.capacity)
    response.headers["X-RateLimit-Remaining"] = str(request.state.rate_limit_remaining)
    return api_key


async def enforce_admin_rate_limit(
    x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
) -> None:
    """Rate limit the admin API.

    The admin key mints and revokes client keys, so it's a higher trust level
    than any client key — but it was the only surface with no limit at all,
    leaving X-Admin-Key guessable at unlimited rate. That's theoretical while
    the configured secret is long, but that's a property of this deployment's
    secret rather than of the endpoint, and the endpoint is where the control
    belongs.

    Keyed on a fixed label rather than the presented value: keying on the
    guess would give an attacker a fresh bucket per attempt, which is no limit
    at all. One shared bucket for admin traffic is the point.
    """
    allowed, _ = await _limiter.check(key="admin-api")
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="admin API rate limit exceeded",
            headers={"Retry-After": str(_RETRY_AFTER_SECONDS)},
        )
