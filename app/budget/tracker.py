import time

from redis.asyncio import Redis

_SECONDS_PER_MONTH = 30 * 24 * 60 * 60


class BudgetTracker:
    """Tracks running USD spend per API key in Redis, resetting monthly."""

    def __init__(self, redis: Redis, monthly_cap_usd: float):
        self._redis = redis
        self._monthly_cap_usd = monthly_cap_usd

    def _key(self, api_key: str) -> str:
        period = int(time.time() // _SECONDS_PER_MONTH)
        return f"budget:{api_key}:{period}"

    async def spent_usd(self, api_key: str) -> float:
        raw = await self._redis.get(self._key(api_key))
        return float(raw) if raw else 0.0

    async def has_budget(self, api_key: str) -> bool:
        return await self.spent_usd(api_key) < self._monthly_cap_usd

    async def record_spend(self, api_key: str, amount_usd: float) -> None:
        key = self._key(api_key)
        pipe = self._redis.pipeline()
        pipe.incrbyfloat(key, amount_usd)
        pipe.expire(key, _SECONDS_PER_MONTH)
        await pipe.execute()
