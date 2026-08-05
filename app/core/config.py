import logging

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("gateway.config")

_INSECURE_SECRET_DEFAULT = "dev-only-insecure-key"


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

    # Token bucket: capacity = max burst size, refill_rate = tokens/sec sustained.
    rate_limit_capacity: int = 20
    rate_limit_refill_per_sec: float = 0.5  # 30 requests/min sustained

    # Per-API-key spend cap in USD, reset monthly.
    monthly_budget_usd_per_key: float = 5.0

    # Circuit breaker: consecutive failures before a provider is skipped, and
    # how long to wait before trying it again.
    circuit_breaker_failure_threshold: int = 3
    circuit_breaker_cooldown_seconds: float = 30.0

    # Retries against the SAME provider before moving on to the next one in
    # the fallback chain. 0 = no retry, fail over immediately.
    provider_retry_attempts: int = 1
    provider_retry_backoff_seconds: float = 0.5

    # Per-request timeout to each provider's HTTP client. Without this, a
    # hung upstream connection could block a request (and its retries/
    # fallback) far longer than any caller would reasonably wait. Ollama gets
    # a separate, longer default since local generation is often slower than
    # a hosted API.
    provider_request_timeout_seconds: float = 30.0
    ollama_request_timeout_seconds: float = 60.0

    def allowed_api_keys(self) -> list[str]:
        return [k.strip() for k in self.gateway_api_keys.split(",") if k.strip()]

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
