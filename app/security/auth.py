"""Client API key auth for gateway endpoints.

Two sources of valid keys, checked in order:
1. The GATEWAY_API_KEYS env var (legacy/simple — good for a single operator).
2. Keys issued through the admin API and stored (hashed) in the DB, which
   support per-team attribution and revocation.
Either source being satisfied is enough; this lets an operator start with env
keys and migrate to admin-issued keys without a breaking change.
"""
import hmac

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import ApiKeyRecord
from app.db.session import get_session
from app.security.api_keys import hash_key


def _extract_key(authorization: str | None, x_api_key: str | None) -> str | None:
    if x_api_key:
        return x_api_key.strip()
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[len("bearer ") :].strip()
    return None


async def _is_valid_db_key(presented: str, session: AsyncSession) -> bool:
    result = await session.execute(
        select(ApiKeyRecord).where(
            ApiKeyRecord.key_hash == hash_key(presented),
            ApiKeyRecord.revoked.is_(False),
        )
    )
    return result.scalar_one_or_none() is not None


async def require_api_key(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    session: AsyncSession = Depends(get_session),
) -> str:
    allowed = settings.allowed_api_keys()
    presented = _extract_key(authorization, x_api_key)

    if not presented:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing API key",
        )

    for key in allowed:
        if hmac.compare_digest(presented, key):
            return presented

    if await _is_valid_db_key(presented, session):
        return presented

    if not allowed:
        # No env keys configured and no matching DB key: fail closed rather
        # than leaving the endpoint open if the admin API hasn't been used yet.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="gateway API key auth is not configured",
        )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="invalid API key",
    )
