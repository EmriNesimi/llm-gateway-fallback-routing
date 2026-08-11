import pytest
from pydantic import ValidationError

from app.core.config import Settings


def _settings(**overrides):
    # env_file=".env" would pull in real local values otherwise; tests only
    # care about the fields they're overriding.
    base = {
        "gateway_secret_key": "test-secret",
        "gateway_api_keys": "test-key",
        "admin_api_key": "test-admin",
    }
    return Settings(_env_file=None, **{**base, **overrides})  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "field,value",
    [
        ("rate_limit_capacity", 0),
        ("rate_limit_capacity", -1),
        ("rate_limit_refill_per_sec", -0.1),
        ("monthly_budget_usd_per_key", -1.0),
        ("circuit_breaker_failure_threshold", 0),
        ("circuit_breaker_cooldown_seconds", -1.0),
        ("provider_retry_attempts", -1),
        ("provider_retry_backoff_seconds", -0.1),
        ("provider_request_timeout_seconds", 0),
        ("provider_request_timeout_seconds", -5),
        ("ollama_request_timeout_seconds", 0),
    ],
)
def test_out_of_range_numeric_settings_are_rejected(field, value):
    with pytest.raises(ValidationError):
        _settings(**{field: value})


def test_valid_numeric_settings_are_accepted():
    settings = _settings(rate_limit_capacity=1, provider_retry_attempts=0)
    assert settings.rate_limit_capacity == 1
    assert settings.provider_retry_attempts == 0


def test_invalid_log_format_is_rejected():
    with pytest.raises(ValidationError):
        _settings(log_format="xml")


@pytest.mark.parametrize("log_format", ["text", "json"])
def test_valid_log_formats_are_accepted(log_format):
    assert _settings(log_format=log_format).log_format == log_format


def test_unsupported_database_scheme_is_rejected():
    with pytest.raises(ValidationError):
        _settings(database_url="mysql://user:pass@localhost/db")


@pytest.mark.parametrize(
    "database_url",
    [
        "sqlite+aiosqlite:///./gateway.db",
        "postgresql+asyncpg://gateway:changeme@localhost:5432/gateway",
    ],
)
def test_supported_database_schemes_are_accepted(database_url):
    assert _settings(database_url=database_url).database_url == database_url
