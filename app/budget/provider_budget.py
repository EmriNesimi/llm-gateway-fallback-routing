"""A hard, lifetime spend ceiling per upstream provider.

Distinct from `BudgetTracker`, and deliberately so. That one caps spend *per
client key, per month* — it answers "is this caller taking more than their
share this month". This answers a different question: "have we spent more of
the owner's actual money with this provider than we ever intended to". It
never resets, it isn't attached to a caller, and no number of client API keys
can raise it.

Three properties make it a real ceiling rather than a soft cap:

1. **Lifetime, not monthly.** A monthly cap of $1 permits $12 a year. When the
   limit is a prepaid balance rather than a spending rate, the period is the
   wrong axis entirely.

2. **Reserve before the call, reconcile after.** Checking `spent < cap` and
   then calling is a time-of-check/time-of-use race: with a burst allowance of
   20, twenty concurrent requests all observe `spent = $0` and all proceed.
   Instead the request's worst-case cost is added atomically up front and the
   difference refunded once the real cost is known, so concurrent requests see
   each other's reservations immediately.

3. **Fails closed.** If Redis is unreachable the request is refused. Being
   unable to prove there is budget left is not the same as having budget left,
   and this is the last line between a bug and a real bill.
"""

import logging

from redis.asyncio import Redis

from app.observability.metrics import (
    PROVIDER_BUDGET_REMAINING,
    PROVIDER_BUDGET_SPENT,
)

logger = logging.getLogger("gateway.provider_budget")

# Ollama runs locally and bills nothing, so it has no ceiling — the same
# exemption app/budget/pricing.py makes.
FREE_PROVIDERS = frozenset({"ollama"})


class ProviderBudgetExhausted(Exception):
    """Raised when a provider has spent its lifetime allowance."""

    def __init__(self, provider: str, spent: float, cap: float):
        super().__init__(
            f"{provider} has spent ${spent:.4f} of its ${cap:.2f} lifetime budget"
        )
        self.provider = provider
        self.spent = spent
        self.cap = cap


class ProviderBudget:
    """Tracks lifetime USD spend per provider in Redis."""

    def __init__(self, redis: Redis, cap_usd: float):
        self._redis = redis
        self._cap_usd = cap_usd

    @property
    def cap_usd(self) -> float:
        return self._cap_usd

    def _key(self, provider: str) -> str:
        # No period component: this ledger covers the life of the deployment.
        return f"provider_budget:{provider}"

    async def spent(self, provider: str) -> float:
        raw = await self._redis.get(self._key(provider))
        total = float(raw) if raw else 0.0
        self._publish(provider, total)
        return total

    def _publish(self, provider: str, total: float) -> None:
        """Mirror the ledger onto its gauges.

        Done on read rather than on write because the ledger lives in Redis
        and can move without this process touching it — another replica
        settling a request, or an operator resetting it. Publishing what was
        just read keeps the gauge honest about the shared value rather than
        this instance's guess at it.
        """
        if provider in FREE_PROVIDERS:
            return
        PROVIDER_BUDGET_SPENT.labels(provider=provider).set(total)
        PROVIDER_BUDGET_REMAINING.labels(provider=provider).set(
            max(0.0, self._cap_usd - total)
        )

    async def remaining(self, provider: str) -> float:
        if provider in FREE_PROVIDERS:
            return float("inf")
        return max(0.0, self._cap_usd - await self.spent(provider))

    async def is_exhausted(self, provider: str) -> bool:
        if provider in FREE_PROVIDERS:
            return False
        return await self.spent(provider) >= self._cap_usd

    async def exhausted_providers(self, providers: list[str]) -> set[str]:
        """Which of these can no longer be called. Used to drop providers from
        a chain before the router tries them, so an exhausted provider costs
        nothing instead of being called and refused upstream."""
        out = set()
        for provider in providers:
            if await self.is_exhausted(provider):
                out.add(provider)
        return out

    async def reserve(self, provider: str, worst_case_usd: float) -> None:
        """Claim headroom for a request that hasn't happened yet.

        INCRBYFLOAT is atomic and returns the post-increment total, so two
        concurrent callers cannot both see room for the last dollar. Over the
        cap, the reservation is handed straight back and the request refused —
        the provider is never called, so nothing is spent.
        """
        if provider in FREE_PROVIDERS or worst_case_usd <= 0:
            return

        key = self._key(provider)
        total = float(await self._redis.incrbyfloat(key, worst_case_usd))
        if total > self._cap_usd:
            await self._redis.incrbyfloat(key, -worst_case_usd)
            spent = await self.spent(provider)
            logger.warning(
                "refusing request: %s would exceed its $%.2f lifetime budget"
                " (spent $%.4f, this request could cost up to $%.4f)",
                provider,
                self._cap_usd,
                spent,
                worst_case_usd,
            )
            raise ProviderBudgetExhausted(provider, spent, self._cap_usd)

    async def settle(self, provider: str, reserved_usd: float, actual_usd: float) -> None:
        """Replace a reservation with what the request really cost.

        Always called after a reserve, including when the request failed — in
        which case `actual_usd` is 0 and the whole reservation comes back. A
        reservation that is never settled is a permanent leak out of the
        budget, so every caller must pair the two.
        """
        if provider in FREE_PROVIDERS:
            return
        delta = actual_usd - reserved_usd
        if delta:
            await self._redis.incrbyfloat(self._key(provider), delta)

    async def record_unreserved(self, provider: str, actual_usd: float) -> None:
        """Charge spend that never had a reservation.

        The escape hatch for a streamed response already partly generated when
        something went wrong. Under-charging here is how a ceiling quietly
        stops being one, so this errs toward charging.
        """
        if provider in FREE_PROVIDERS or actual_usd <= 0:
            return
        await self._redis.incrbyfloat(self._key(provider), actual_usd)

    async def snapshot(self, providers: tuple[str, ...]) -> dict:
        """Current spend per provider, for operators and for tests.

        Takes the list rather than defaulting to one. This module has no way
        to know which providers exist — that lives in the routing chains — and
        the default it used to carry was a literal pair that would have gone
        stale the moment a third paid provider was added.
        """
        return {p: await self.spent(p) for p in providers}
