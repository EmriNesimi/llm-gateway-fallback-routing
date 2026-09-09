from fastapi import Depends, HTTPException, Request, Response, status

from app.budget.provider_budget import ProviderBudget
from app.budget.tracker import BudgetTracker
from app.core.config import settings
from app.core.redis_client import get_redis
from app.observability.metrics import REQUESTS_REFUSED
from app.ratelimit.dependency import enforce_rate_limit

tracker = BudgetTracker(
    redis=get_redis(),
    monthly_cap_usd=settings.monthly_budget_usd_per_key,
)

# The ceiling on the operator's own money, as opposed to `tracker`'s ceiling
# on any one caller's monthly share. Issuing more client keys multiplies the
# latter and cannot touch this one.
provider_budget = ProviderBudget(
    redis=get_redis(),
    cap_usd=settings.provider_lifetime_budget_usd,
)


async def enforce_budget(
    request: Request,
    response: Response,
    api_key: str = Depends(enforce_rate_limit),
) -> str:
    # Checked, not reserved — unlike the provider ceiling, which reserves
    # atomically for exactly this reason (decision 011). Concurrent requests
    # from one key all read the same pre-call total and are all admitted, so a
    # caller can overshoot their monthly cap by up to a rate-limit burst's
    # worth of requests.
    #
    # Left as a check deliberately. The cap this races is the per-caller
    # share, not the operator's money: the lifetime provider ceiling reserves
    # before every call and is unaffected, so overshooting here cannot spend
    # more than the operator has allowed in total. Closing it properly means
    # reserving against the worst case, which is only known in _reserve_chain
    # — a restructure that would buy a fairness guarantee this project (one
    # key, one user) does not need. Stated in SECURITY.md rather than left to
    # be discovered.
    spent = await tracker.spent_usd(api_key)
    remaining = max(0.0, settings.monthly_budget_usd_per_key - spent)

    # Stashed for endpoints (like streaming) that build their own Response
    # object and can't rely on FastAPI merging headers set here automatically.
    # Reflects budget remaining as of admission — a streaming response can't
    # know this request's own cost until after it's fully sent.
    request.state.budget_remaining_usd = remaining

    if spent >= settings.monthly_budget_usd_per_key:
        # Distinct from provider_budget_exhausted: this caller is over their
        # own monthly share while the operator's balance is untouched. Same
        # 402, completely different fix.
        REQUESTS_REFUSED.labels(reason="key_budget_exhausted").inc()
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="monthly budget exceeded for this API key",
            headers={"X-Budget-Remaining-USD": "0.0000"},
        )

    response.headers["X-Budget-Remaining-USD"] = f"{remaining:.4f}"
    return api_key
