import logging
import time

from redis.asyncio import Redis

logger = logging.getLogger("gateway.budget")

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
        # Deliberately NOT swallowed like record_spend below: this backs the
        # pre-flight budget check in app/budget/dependency.py, which must
        # fail closed on a Redis outage (block the request) rather than
        # silently let spend enforcement pass through unverified.
        raw = await self._redis.get(self._key(api_key))
        return float(raw) if raw else 0.0

    async def has_budget(self, api_key: str) -> bool:
        return await self.spent_usd(api_key) < self._monthly_cap_usd

    async def record_spend(self, api_key: str, amount_usd: float) -> None:
        """Best-effort: this runs *after* a provider has already returned a
        (paid-for) response. A Redis blip here must not turn that successful
        response into a 500 for the caller — it would discard output that's
        already been billed by the provider. Losing one request's worth of
        spend tracking to a transient outage is an acceptable tradeoff;
        discarding a successful response the user already paid for is not."""
        try:
            key = self._key(api_key)
            pipe = self._redis.pipeline()
            pipe.incrbyfloat(key, amount_usd)
            pipe.expire(key, _SECONDS_PER_MONTH)
            await pipe.execute()
        except Exception:  # noqa: BLE001 - any Redis failure here must not break the caller
            logger.error(
                "failed to record spend of $%.6f for a request that already succeeded",
                amount_usd,
                exc_info=True,
            )
