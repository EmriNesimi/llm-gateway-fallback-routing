import time

from redis.asyncio import Redis

from app.security.api_keys import hash_key

# Atomic refill + consume so concurrent requests can't race past the limit.
_LUA_TOKEN_BUCKET = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local requested = tonumber(ARGV[4])

local bucket = redis.call('HMGET', key, 'tokens', 'updated_at')
local tokens = tonumber(bucket[1])
local updated_at = tonumber(bucket[2])

if tokens == nil then
  tokens = capacity
  updated_at = now
end

local elapsed = math.max(0, now - updated_at)
tokens = math.min(capacity, tokens + elapsed * refill_rate)

local allowed = 0
if tokens >= requested then
  tokens = tokens - requested
  allowed = 1
end

redis.call('HMSET', key, 'tokens', tokens, 'updated_at', now)

local ttl_seconds = 3600
if refill_rate > 0 then
  ttl_seconds = math.ceil(capacity / refill_rate) + 60
end
redis.call('EXPIRE', key, ttl_seconds)

return {allowed, tokens}
"""  # noqa: S105 — flagged only because the name contains "TOKEN"; this is a Lua script, not a credential


class TokenBucketLimiter:
    """Redis-backed token bucket: `capacity` tokens, refilled at `refill_rate` tokens/sec."""

    def __init__(self, redis: Redis, capacity: int, refill_rate: float):
        self._redis = redis
        self._capacity = capacity
        self._refill_rate = refill_rate
        self._script = redis.register_script(_LUA_TOKEN_BUCKET)

    async def check(self, key: str, cost: int = 1) -> tuple[bool, float]:
        """Like `allow`, but also returns the tokens remaining after this
        check — used to surface X-RateLimit-Remaining on responses."""
        allowed, remaining = await self._script(
            keys=[f"ratelimit:{hash_key(key)}"],
            args=[self._capacity, self._refill_rate, time.time(), cost],
        )
        return bool(allowed), float(remaining)

    async def allow(self, key: str, cost: int = 1) -> bool:
        allowed, _remaining = await self.check(key, cost)
        return allowed

    @property
    def capacity(self) -> int:
        return self._capacity
