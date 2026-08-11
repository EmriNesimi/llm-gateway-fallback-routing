import logging
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("gateway.config")

_INSECURE_SECRET_DEFAULT = "dev-only-insecure-key"
# Schemes app/db/session.py and migrations/env.py actually know how to drive —
# anything else would fail deep inside SQLAlchemy with a much less legible
# error, well after startup, instead of here.
_SUPPORTED_DATABASE_SCHEMES = ("sqlite+aiosqlite", "postgresql+asyncpg")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    ollama_base_url: str = "http://localhost:11434"

    redis_url: str = "redis://localhost:6379/0"
    # SQLite by default so the gateway runs with zero external setup; point at
    # Postgres in production via DATABASE_URL (e.g. postgresql+asyncpg://...).
    database_url: str = "sqlite+aiosqlite:///./gateway.db"

    # Shared secret for the admin API (key issuance/revocation). Separate from
    # GATEWAY_API_KEYS since admin access is a different trust level.
    admin_api_key: str | None = None

    gateway_secret_key: str = _INSECURE_SECRET_DEFAULT
    # Comma-separated client API keys accepted by this gateway. When empty,
    # authenticated endpoints fail closed (they refuse to serve) rather than
    # exposing provider credits to anyone who can reach the port.
    gateway_api_keys: str = ""
    otel_exporter_otlp_endpoint: str | None = None

    # Comma-separated allowed browser origins for CORS. Empty by default —
    # this gateway is meant to be called server-to-server or via curl, so
    # opening it to arbitrary browser origins is opt-in, not assumed.
    cors_allowed_origins: str = ""

    # "text" for human-readable local dev logs, "json" for structured
    # single-line logs a log aggregator can parse in production.
    log_format: Literal["text", "json"] = "text"

    # Token bucket: capacity = max burst size, refill_rate = tokens/sec sustained.
    # capacity must be positive — zero would reject every request; negative
    # is nonsensical. refill_rate of 0 is valid (a fixed, non-refilling
    # allowance) so it's only bounded below at zero.
    rate_limit_capacity: int = Field(default=20, gt=0)
    rate_limit_refill_per_sec: float = Field(default=0.5, ge=0)  # 30 requests/min sustained

    # Per-API-key spend cap in USD, reset monthly.
    monthly_budget_usd_per_key: float = Field(default=5.0, ge=0)

    # Circuit breaker: consecutive failures before a provider is skipped, and
    # how long to wait before trying it again.
    circuit_breaker_failure_threshold: int = Field(default=3, gt=0)
    circuit_breaker_cooldown_seconds: float = Field(default=30.0, ge=0)

    # Retries against the SAME provider before moving on to the next one in
    # the fallback chain. 0 = no retry, fail over immediately.
    provider_retry_attempts: int = Field(default=1, ge=0)
    provider_retry_backoff_seconds: float = Field(default=0.5, ge=0)

    # Per-request timeout to each provider's HTTP client. Without this, a
    # hung upstream connection could block a request (and its retries/
    # fallback) far longer than any caller would reasonably wait. Ollama gets
    # a separate, longer default since local generation is often slower than
    # a hosted API. Zero or negative would mean "no timeout" or an instantly
    # expired request depending on the HTTP client, neither of which is ever
    # what's intended here, so both are rejected outright.
    provider_request_timeout_seconds: float = Field(default=30.0, gt=0)
    ollama_request_timeout_seconds: float = Field(default=60.0, gt=0)

    @field_validator("database_url")
    @classmethod
    def _validate_database_url_scheme(cls, value: str) -> str:
        if not value.startswith(_SUPPORTED_DATABASE_SCHEMES):
            raise ValueError(
                f"DATABASE_URL must start with one of {_SUPPORTED_DATABASE_SCHEMES} "
                f"(the async drivers this app is actually wired for), got: {value!r}"
            )
        return value

    def allowed_api_keys(self) -> list[str]:
        return [k.strip() for k in self.gateway_api_keys.split(",") if k.strip()]

    def allowed_cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]

    def model_post_init(self, __context: object) -> None:
        if self.gateway_secret_key == _INSECURE_SECRET_DEFAULT:
            logger.warning(
                "GATEWAY_SECRET_KEY is the insecure built-in default; set a long "
                "random value in .env before running anywhere but local dev."
            )
        if not self.allowed_api_keys():
            logger.warning(
                "GATEWAY_API_KEYS is empty; authenticated endpoints will refuse "
                "requests. Set at least one client key in .env to enable /v1/chat."
            )
        if not self.admin_api_key:
            logger.warning(
                "ADMIN_API_KEY is not set; the admin API (key issuance/revocation) "
                "will refuse all requests rather than being left open."
            )


settings = Settings()
