"""Auth for the admin API.

Key issuance and revocation use a separate, higher-trust secret from the client-facing
GATEWAY_API_KEYS, so a leaked client key can't
be used to mint more keys.
"""
import hmac

from fastapi import Header, HTTPException, status

from app.core.config import settings


async def require_admin_key(
    x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
) -> str:
    """Check X-Admin-Key and return it, so handlers can record which credential acted.

    Returns the presented key rather than a bare pass/fail. FastAPI caches a dependency per request,
    so depending on
    this again inside a handler doesn't re-run the check.
    """
    if not settings.admin_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="admin API is not configured",
        )
    if not x_admin_key or not hmac.compare_digest(x_admin_key, settings.admin_api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid admin key",
        )
    return x_admin_key
