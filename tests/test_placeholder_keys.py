"""Rejecting provider keys that are present but obviously not real.

This exists because of a specific, silent, weeks-long failure: `.env` still
held `ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxxxxxxxxxxx` straight out of
`.env.example`. Non-empty, so `if not settings.anthropic_api_key` passed, so a
real SDK client was built, so every Anthropic call 401'd — and since a 4xx is
non-retryable, the router fell past Anthropic to the next provider every time.
Requests kept succeeding, nothing looked broken, and the `smart` chain served
every request from its OpenAI fallback instead of the model it was configured
for.

An unset key was always handled correctly. It was the *fake* key that wasn't.
"""

import logging

import pytest

from app.core.config import Settings
from app.providers.base import UnconfiguredProvider
from app.routing import dependencies

# The literal values shipped in .env.example — the ones actually copied.
EXAMPLE_OPENAI_KEY = "sk-xxxxxxxxxxxxxxxxxxxxx"
EXAMPLE_ANTHROPIC_KEY = "sk-ant-xxxxxxxxxxxxxxxxxxxxx"

# Shaped like the real thing: long, mixed case, digits, underscores, hyphens.
REAL_LOOKING_OPENAI = "sk-proj-" + "aB3dEf9_hJ2lMn5pQr8tUv1wXy4z" * 3
REAL_LOOKING_ANTHROPIC = "sk-ant-api03-" + "Ab3dEf9_hJ2lMn5pQr8tUv1wXy4z" * 3


def _settings(**overrides):
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


# --------------------------------------------------------------------------
# The placeholders that caused this
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field,value",
    [
        ("anthropic_api_key", EXAMPLE_ANTHROPIC_KEY),
        ("openai_api_key", EXAMPLE_OPENAI_KEY),
    ],
)
def test_the_env_example_placeholders_are_treated_as_unset(field, value):
    assert getattr(_settings(**{field: value}), field) is None


def test_the_placeholder_is_reported_not_silently_dropped(caplog):
    """Blanking it quietly would swap one silent failure for another."""
    with caplog.at_level(logging.WARNING, logger="gateway.config"):
        _settings(anthropic_api_key=EXAMPLE_ANTHROPIC_KEY)

    messages = [r.getMessage() for r in caplog.records]
    assert any("ANTHROPIC_API_KEY" in m and "placeholder" in m for m in messages)


@pytest.mark.parametrize(
    "value",
    [
        "sk-xxxxxxxx",  # exactly the 8-character run threshold
        "sk-ant-XXXXXXXXXXXX",  # upper case — same placeholder, shouted
        "sk-ant-api03-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
    ],
)
def test_other_placeholder_shapes_are_caught_too(value):
    """Length alone isn't the signal — a long run of x's is a placeholder no
    matter how many characters follow it."""
    assert _settings(anthropic_api_key=value).anthropic_api_key is None


def test_implausibly_short_keys_are_treated_as_unset():
    assert _settings(openai_api_key="sk-abc123").openai_api_key is None


# --------------------------------------------------------------------------
# Real keys must survive — a false positive here silently disables a provider
# --------------------------------------------------------------------------


def test_real_looking_keys_are_kept():
    settings = _settings(
        openai_api_key=REAL_LOOKING_OPENAI, anthropic_api_key=REAL_LOOKING_ANTHROPIC
    )

    assert settings.openai_api_key == REAL_LOOKING_OPENAI
    assert settings.anthropic_api_key == REAL_LOOKING_ANTHROPIC


def test_a_real_key_containing_an_x_is_not_mistaken_for_a_placeholder():
    """Random key material contains x's. Only a *run* of them is the signal —
    checking for a bare "x" would disable working providers at random."""
    key = "sk-ant-api03-" + "xA9x" * 24

    assert _settings(anthropic_api_key=key).anthropic_api_key == key


def test_keeping_a_real_key_produces_no_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="gateway.config"):
        _settings(
            openai_api_key=REAL_LOOKING_OPENAI, anthropic_api_key=REAL_LOOKING_ANTHROPIC
        )

    messages = [r.getMessage() for r in caplog.records]
    assert not any("placeholder" in m or "shorter than a real key" in m for m in messages)


# --------------------------------------------------------------------------
# Unset keys keep working exactly as before
# --------------------------------------------------------------------------


@pytest.mark.parametrize("value", [None, "", "   "])
def test_absent_keys_stay_absent(value):
    assert _settings(anthropic_api_key=value).anthropic_api_key is None


def test_a_bad_key_does_not_stop_the_process_booting():
    """Deliberately a warning, not a raise: this gateway is designed to run
    with only some providers configured (decision 006), so one bad key should
    remove that provider from the chain, not take the whole gateway down."""
    settings = _settings(
        anthropic_api_key=EXAMPLE_ANTHROPIC_KEY, openai_api_key=REAL_LOOKING_OPENAI
    )

    assert settings.anthropic_api_key is None
    assert settings.openai_api_key == REAL_LOOKING_OPENAI


# --------------------------------------------------------------------------
# The end the user actually feels
# --------------------------------------------------------------------------


def test_a_placeholder_key_yields_a_provider_that_fails_fast(monkeypatch):
    """The whole point. A blanked key routes into the missing-key path, so the
    provider is skipped without a network call — instead of being built,
    called, and 401'ing on every single request."""
    from app.core.config import settings as live_settings

    monkeypatch.setattr(dependencies, "_PROVIDER_INSTANCES", {})
    monkeypatch.setattr(live_settings, "anthropic_api_key", None)

    provider = dependencies._get_provider("anthropic")

    assert isinstance(provider, UnconfiguredProvider)


@pytest.mark.asyncio
async def test_that_provider_fails_non_retryably():
    """Non-retryable matters: a retryable failure would burn the retry budget
    and its backoff before falling back, adding latency to every request
    against a provider that can never succeed."""
    from app.providers.base import ProviderError

    provider = UnconfiguredProvider("anthropic")

    with pytest.raises(ProviderError) as exc_info:
        await provider.chat("claude-opus-5", [])

    assert exc_info.value.retryable is False
