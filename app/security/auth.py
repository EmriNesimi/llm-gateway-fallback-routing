"""Lightweight client API key auth for gateway endpoints.

Phase 1 keeps this deliberately simple: accepted client keys are configured via
the GATEWAY_API_KEYS env var and compared in constant time. Full per-team key
issuance and hashed-at-rest storage (see app/security/api_keys.py) lands later.
The important property now is that /v1/chat is not open to the world, so nobody
who can reach the port can burn the operator's OpenAI/Anthropic credits.
"""
import hmac

from fastapi import Header, HTTPException, status

from app.core.config import settings


def _extract_key(authorization: str | None, x_api_key: str | None) -> str | None:
    if x_api_key:
        return x_api_key.strip()
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[len("bearer ") :].strip()
    return None


async def require_api_key(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> None:
    allowed = settings.allowed_api_keys()
    if not allowed:
        # Fail closed: without configured keys we refuse rather than serve openly.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="gateway API key auth is not configured",
        )

    presented = _extract_key(authorization, x_api_key)
    if not presented:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing API key",
        )

    for key in allowed:
        if hmac.compare_digest(presented, key):
            return

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="invalid API key",
    )
