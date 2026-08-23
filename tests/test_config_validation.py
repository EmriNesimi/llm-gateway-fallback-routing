import logging

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
        ("redis_connect_timeout_seconds", 0),
        ("redis_connect_timeout_seconds", -1),
        ("redis_socket_timeout_seconds", 0),
        ("database_connect_timeout_seconds", 0),
        ("database_command_timeout_seconds", 0),
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


def test_warns_when_no_hosted_provider_is_configured(caplog):
    with caplog.at_level(logging.WARNING, logger="gateway.config"):
        _settings(openai_api_key=None, anthropic_api_key=None)
    assert any("OPENAI_API_KEY" in r.message for r in caplog.records)


# Long enough to be plausible keys. These used to be "sk-test"/"sk-ant-test",
# which the placeholder check in config.py now correctly rejects as far too
# short to be real — so under the current rules those fixtures described an
# *unconfigured* provider, the opposite of what this test is about.
_PLAUSIBLE_OPENAI = "sk-proj-" + "aB3dEf9_hJ2lMn5pQr8tUv1wXy4z" * 3
_PLAUSIBLE_ANTHROPIC = "sk-ant-api03-" + "Ab3dEf9_hJ2lMn5pQr8tUv1wXy4z" * 3


@pytest.mark.parametrize(
    "kwargs",
    [
        {"openai_api_key": _PLAUSIBLE_OPENAI, "anthropic_api_key": None},
        {"openai_api_key": None, "anthropic_api_key": _PLAUSIBLE_ANTHROPIC},
        {"openai_api_key": _PLAUSIBLE_OPENAI, "anthropic_api_key": _PLAUSIBLE_ANTHROPIC},
    ],
)
def test_no_warning_when_at_least_one_hosted_provider_is_configured(caplog, kwargs):
    with caplog.at_level(logging.WARNING, logger="gateway.config"):
        _settings(**kwargs)
    assert not any("OPENAI_API_KEY" in r.message for r in caplog.records)
