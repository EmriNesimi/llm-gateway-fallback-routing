import pytest

from app.core.config import settings
from app.providers.base import UnconfiguredProvider
from app.providers.openai_provider import OpenAIProvider
from app.routing import dependencies


@pytest.fixture(autouse=True)
def _reset_provider_cache(monkeypatch):
    # _PROVIDER_INSTANCES is a module-level cache keyed by provider name —
    # without resetting it, whichever test runs first "wins" and every
    # later test sees its cached instance instead of exercising the actual
    # settings-dependent construction logic being tested here.
    monkeypatch.setattr(dependencies, "_PROVIDER_INSTANCES", {})


def test_get_provider_returns_unconfigured_stand_in_when_api_key_missing(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", None)

    provider = dependencies._get_provider("openai")

    assert isinstance(provider, UnconfiguredProvider)
    assert provider.name == "openai"


def test_get_provider_builds_real_client_when_api_key_present(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "sk-test-key")

    provider = dependencies._get_provider("openai")

    assert isinstance(provider, OpenAIProvider)


def test_get_provider_caches_instances_across_calls(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "sk-test-key")

    first = dependencies._get_provider("openai")
    second = dependencies._get_provider("openai")

    assert first is second
