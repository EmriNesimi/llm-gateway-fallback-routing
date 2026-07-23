"""Client API key hashing so raw keys are never persisted or logged."""
import hashlib
import hmac

from app.core.config import settings


def hash_key(raw_key: str) -> str:
    return hmac.new(
        settings.gateway_secret_key.encode(), raw_key.encode(), hashlib.sha256
    ).hexdigest()


def verify_key(raw_key: str, stored_hash: str) -> bool:
    return hmac.compare_digest(hash_key(raw_key), stored_hash)
