"""Auth for the admin API (key issuance/revocation) — a separate, higher-trust
secret from the client-facing GATEWAY_API_KEYS, so a leaked client key can't
be used to mint more keys."""
import hmac

from fastapi import Header, HTTPException, status

from app.core.config import settings


async def require_admin_key(
    x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
) -> None:
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
