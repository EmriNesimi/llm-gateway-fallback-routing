from fastapi import Depends, HTTPException, status

from app.budget.tracker import BudgetTracker
from app.core.config import settings
from app.core.redis_client import get_redis
from app.ratelimit.dependency import enforce_rate_limit

tracker = BudgetTracker(
    redis=get_redis(),
    monthly_cap_usd=settings.monthly_budget_usd_per_key,
)


async def enforce_budget(api_key: str = Depends(enforce_rate_limit)) -> str:
    if not await tracker.has_budget(api_key):
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="monthly budget exceeded for this API key",
        )
    return api_key
