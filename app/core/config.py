import logging
import os
import re
from typing import Literal

from pydantic import Field, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("gateway.config")

# A provider key that is present but obviously not real — the placeholder
# copied out of .env.example, most often — is worse than one that's missing.
# An unset key is caught by `if not settings.<provider>_api_key` and the
# provider is replaced with UnconfiguredProvider, which fails instantly and
# non-retryably. A placeholder passes that check: a real SDK client gets
# built, every call 401s, and because a 4xx is classified non-retryable (see
# is_retryable_status_code) the router falls straight past to the next
# provider. The chain still answers, so nothing looks broken, and the
# provider you thought you configured is quietly never used.
#
# Blanking these to None routes them back into the missing-key path, which
# already does the right thing and already warns.
_PLACEHOLDER_RUN = re.compile(r"x{8,}", re.IGNORECASE)

# Both providers issue keys far longer than this; the shipped placeholders are
# 24 and 28 characters. Set well below any real key rather than just above the
# placeholders, so a provider changing its key length can't produce a false
# positive that skips a working provider.
_MIN_PLAUSIBLE_KEY_LENGTH = 40

# Which env file to read, if any. Overridable so a caller can opt out of file
# loading entirely by setting GATEWAY_ENV_FILE="" — which the test suite does,
# because Settings is constructed at import time and would otherwise pick up
# whichever .env the developer happens to have (real API keys, real budget
# caps, a live OTLP endpoint). That made the suite exercise one configuration
# locally and a different one in CI, where no .env exists.
_ENV_FILE = os.environ.get("GATEWAY_ENV_FILE", ".env") or None

_INSECURE_SECRET_DEFAULT = "dev-only-insecure-key"
# Schemes app/db/session.py and migrations/env.py actually know how to drive —
# anything else would fail deep inside SQLAlchemy with a much less legible
# error, well after startup, instead of here.
_SUPPORTED_DATABASE_SCHEMES = ("sqlite+aiosqlite", "postgresql+asyncpg")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, extra="ignore")

    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    ollama_base_url: str = "http://localhost:11434"

    redis_url: str = "redis://localhost:6379/0"
    # Without these, redis-py blocks on the OS-level TCP timeout to connect
    # (platform-dependent, often 30s+) and indefinitely on socket_timeout for
    # a connection that accepted but never responds — meaning a Redis that's
    # merely unresponsive (not cleanly refusing connections) could hang
    # /readyz and every rate-limit/budget check instead of failing fast.
    redis_connect_timeout_seconds: float = Field(default=5.0, gt=0)
    redis_socket_timeout_seconds: float = Field(default=5.0, gt=0)
    # SQLite by default so the gateway runs with zero external setup; point at
    # Postgres in production via DATABASE_URL (e.g. postgresql+asyncpg://...).
    database_url: str = "sqlite+aiosqlite:///./gateway.db"
    # Only applied for Postgres (see app/db/session.py) — SQLite is a local
    # file with no network involved. asyncpg's own default connect timeout is
    # 60s (too long for a request path) and its command_timeout is unset
    # entirely by default, meaning a hung Postgres backend (a stuck lock, a
    # network partition after connect) could block a query indefinitely.
    database_connect_timeout_seconds: float = Field(default=5.0, gt=0)
    database_command_timeout_seconds: float = Field(default=10.0, gt=0)

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

    # How to treat a `model` the routing table doesn't know. False (the
    # default) preserves this gateway's long-standing behavior: serve it on
    # the default chain, but say so via the X-Gateway-Chain response header,
    # a warning log, and the audit row. True rejects it with a 404 instead.
    #
    # Defaulting to False keeps /v1 non-breaking — turning a request that used
    # to return 200 into a 404 is a breaking change under
    # docs/api-versioning.md, and would otherwise demand a whole /v2.
    # See docs/decisions/009-unknown-model-handling.md.
    strict_model_routing: bool = False

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

    @field_validator("openai_api_key", "anthropic_api_key")
    @classmethod
    def _blank_out_placeholder_keys(cls, value: str | None, info: ValidationInfo) -> str | None:
        """Treat an obviously-fake key as no key at all.

        Warn rather than raise: this gateway is explicitly designed to run
        with only some providers configured (decision 006), so one bad key
        shouldn't stop the process booting — it should just take that
        provider out of the chain, loudly.
        """
        if value is None or not value.strip():
            return None

        if _PLACEHOLDER_RUN.search(value):
            reason = "looks like the placeholder from .env.example"
        elif len(value) < _MIN_PLAUSIBLE_KEY_LENGTH:
            reason = f"is only {len(value)} characters, far shorter than a real key"
        else:
            return value

        logger.warning(
            "%s %s. Treating it as unset, so that provider is skipped cleanly instead"
            " of being called and failing every request with a 401.",
            (info.field_name or "provider key").upper(),
            reason,
        )
        return None

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
        if not self.openai_api_key and not self.anthropic_api_key:
            # Not necessarily broken (Ollama alone is a valid setup) but
            # easy to hit by accident — an empty .env still starts cleanly
            # and /readyz still reports "ok" (it checks Redis/DB, not
            # providers), so without this warning every /v1/chat request
            # failing would be the first sign anything's wrong.
            logger.warning(
                "Neither OPENAI_API_KEY nor ANTHROPIC_API_KEY is set; only Ollama "
                "(if reachable at OLLAMA_BASE_URL) will be able to serve requests."
            )


settings = Settings()
