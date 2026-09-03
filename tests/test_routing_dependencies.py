import pytest

from app.core.config import settings
from app.providers.anthropic_provider import AnthropicProvider
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


def test_anthropic_also_gets_a_real_client_when_configured(monkeypatch):
    """The openai branch above was covered and this one was not, on a
    construction path that is written out per provider rather than shared.
    A copy-paste slip here — the wrong key, the wrong timeout setting — builds
    a client that fails on every call, and the router treats that as the
    provider being down and quietly falls through to the next one."""
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-test-key")

    provider = dependencies._get_provider("anthropic")

    assert isinstance(provider, AnthropicProvider)


def test_anthropic_without_a_key_is_a_stand_in(monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", None)

    provider = dependencies._get_provider("anthropic")

    assert isinstance(provider, UnconfiguredProvider)
    assert provider.name == "anthropic"


def test_an_unknown_provider_name_fails_loudly():
    """Reached only if app/routing/model_map.py names a provider this factory
    doesn't build. Raising beats returning None, which would surface much
    later as an AttributeError inside the router with nothing pointing back
    to the typo in the chain definition."""
    with pytest.raises(ValueError, match="unknown provider: gemini"):
        dependencies._get_provider("gemini")


def test_every_provider_named_in_a_chain_can_be_built():
    """model_map.py and this factory name providers independently.

    Add "gemini" to a chain without teaching _get_provider about it and
    nothing complains until the first request routed there — which raises
    ValueError from inside the router and surfaces as a 500, with the typo in
    the chain definition several layers away from the traceback.

    The pricing guard already checks every chained *model* has a price. This
    is the same check for the *provider*.
    """
    from app.routing.model_map import FALLBACK_CHAINS

    named = {provider for chain in FALLBACK_CHAINS.values() for provider, _ in chain}
    assert named, "no providers found in any chain — the guard would pass vacuously"

    unbuildable = []
    for provider in sorted(named):
        try:
            dependencies._get_provider(provider)
        except ValueError:
            unbuildable.append(provider)

    assert not unbuildable, (
        f"chains route to {unbuildable}, which app/routing/dependencies.py"
        " cannot build — every request to those chains would 500"
    )
