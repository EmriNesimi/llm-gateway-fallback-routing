import logging
import time

from redis.asyncio import Redis

from app.security.api_keys import hash_key

logger = logging.getLogger("gateway.budget")

_SECONDS_PER_MONTH = 30 * 24 * 60 * 60


class KeyBudgetExhausted(Exception):
    """Raised when one caller has claimed its whole monthly share."""

    def __init__(self, spent: float, cap: float):
        super().__init__(f"key has claimed ${spent:.4f} of its ${cap:.2f} monthly budget")
        self.spent = spent
        self.cap = cap


class BudgetTracker:
    """Tracks running USD spend per API key in Redis, resetting monthly.

    Reserve-then-settle, mirroring `ProviderBudget`. Checking `spent < cap`
    and then calling is a time-of-check/time-of-use race: with a burst
    allowance of 20, twenty concurrent requests from one key all observe the
    same pre-call total and all proceed, so the overshoot is bounded by the
    rate-limit burst rather than by the cap. Adding the worst case atomically
    up front and refunding the surplus once the real cost is known makes
    concurrent requests see each other immediately.

    The two ledgers answer different questions and neither substitutes for the
    other: this one bounds *one caller's* monthly share, `ProviderBudget`
    bounds the operator's actual money. They now close the race the same way.
    """

    def __init__(self, redis: Redis, monthly_cap_usd: float):
        self._redis = redis
        self._monthly_cap_usd = monthly_cap_usd

    def _key(self, api_key: str) -> str:
        period = int(time.time() // _SECONDS_PER_MONTH)
        # Hashed, not raw. The database has only ever stored hashes
        # (app/db/audit.py, app/admin/routes.py) — using the raw key as a
        # Redis key name quietly undid that, because anyone able to run KEYS
        # against Redis could read live client keys straight out of the key
        # space, then DEL them to reset the spend that limits them.
        return f"budget:{hash_key(api_key)}:{period}"

    async def spent_usd(self, api_key: str) -> float:
        # Deliberately NOT swallowed like record_spend below: this backs the
        # pre-flight budget check in app/budget/dependency.py, which must
        # fail closed on a Redis outage (block the request) rather than
        # silently let spend enforcement pass through unverified.
        raw = await self._redis.get(self._key(api_key))
        return float(raw) if raw else 0.0

    async def has_budget(self, api_key: str) -> bool:
        return await self.spent_usd(api_key) < self._monthly_cap_usd

    async def reserve(self, api_key: str, worst_case_usd: float) -> None:
        """Claim this caller's share for a request that hasn't happened yet.

        INCRBYFLOAT is atomic and returns the post-increment total, so two
        concurrent callers cannot both see room for the last cent. Over the
        cap the reservation is handed straight back and the caller refused,
        which matters because a refusal that kept its claim would drain a
        budget no request ever spent.

        Deliberately NOT best-effort, unlike `settle` below. This runs before
        any provider is called, so a Redis outage here must refuse rather than
        admit — being unable to prove there is budget left is not the same as
        having budget left (decision 004).
        """
        if worst_case_usd <= 0:
            return

        total = await self._apply(api_key, worst_case_usd, swallow=False)
        if total <= self._monthly_cap_usd:
            return

        spent = total - worst_case_usd
        try:
            await self._apply(api_key, -worst_case_usd, swallow=False)
        except Exception:  # noqa: BLE001 - the refusal below stands either way
            # The increment landed and the hand-back did not, so the claim is
            # stranded: this caller's month is permanently smaller by a request
            # that was never served. Nothing downstream can undo it, because
            # nothing downstream knows a reservation was ever made.
            #
            # Raise KeyBudgetExhausted anyway. Being over the cap is true
            # whether or not the refund succeeded, and letting this exception
            # through instead would report a broken gateway for what is
            # actually a working one saying no. The leak is in the safe
            # direction — it refuses more, never less — so it is logged loudly
            # rather than allowed to change the answer.
            logger.error(
                "failed to hand back a $%.6f reservation refused at the monthly"
                " cap; it stays claimed against this key until the period rolls"
                " over",
                worst_case_usd,
                exc_info=True,
            )

        logger.warning(
            "refusing request: key would exceed its $%.2f monthly budget"
            " (claimed $%.4f, this request could cost up to $%.4f)",
            self._monthly_cap_usd,
            spent,
            worst_case_usd,
        )
        raise KeyBudgetExhausted(spent, self._monthly_cap_usd)

    async def settle(self, api_key: str, reserved_usd: float, actual_usd: float) -> None:
        """Replace a reservation with what the request really cost.

        Always called after a reserve, including when the request failed — in
        which case `actual_usd` is 0 and the whole reservation comes back. A
        reservation that is never settled is a permanent leak out of the
        caller's month, so every caller must pair the two.

        `reserved_usd` of 0 is the ordinary no-reservation charge: a chain of
        only free providers reserves nothing, and so does the estimate charged
        after a stream aborts.
        """
        delta = actual_usd - reserved_usd
        if delta:
            await self._apply(api_key, delta, swallow=True)

    async def record_spend(self, api_key: str, amount_usd: float) -> None:
        """Charge spend that never had a reservation. See `settle`."""
        await self.settle(api_key, reserved_usd=0.0, actual_usd=amount_usd)

    async def _apply(self, api_key: str, delta_usd: float, *, swallow: bool) -> float:
        """Move the ledger by `delta_usd` and return the new total.

        The expire travels with every increment because INCRBYFLOAT on a
        missing key creates it with no TTL — losing that would quietly turn
        the monthly budget into a lifetime one.

        `swallow` is the decision-004 split. Settling runs *after* a provider
        has already returned a (paid-for) response, so a Redis blip must not
        turn that into a 500 for the caller: it would discard output that has
        already been billed. Losing one request's worth of spend tracking to a
        transient outage is an acceptable trade; discarding a successful
        response the user already paid for is not. Reserving runs before the
        call and has no such response to protect, so it propagates.
        """
        key = self._key(api_key)
        try:
            pipe = self._redis.pipeline()
            pipe.incrbyfloat(key, delta_usd)
            pipe.expire(key, _SECONDS_PER_MONTH)
            result = await pipe.execute()
        except Exception:  # noqa: BLE001 - see docstring
            if not swallow:
                raise
            logger.error(
                "failed to record $%.6f for a request that already succeeded",
                delta_usd,
                exc_info=True,
            )
            return 0.0
        return float(result[0])
