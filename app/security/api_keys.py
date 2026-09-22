"""Client API key hashing so raw keys are never persisted or logged."""
import hashlib
import hmac

from app.core.config import settings


def hash_key(raw_key: str) -> str:
    """HMAC-SHA256 of a client key under GATEWAY_SECRET_KEY. This is stored; the key never is."""
    return hmac.new(
        settings.gateway_secret_key.encode(), raw_key.encode(), hashlib.sha256,
    ).hexdigest()

