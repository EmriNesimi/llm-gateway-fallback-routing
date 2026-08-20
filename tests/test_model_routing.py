"""Model routing: chain selection, unknown-model handling, and discovery.

Before this existed, app/routing/model_map.py had a single chain and every
`model` value collapsed onto it — a caller asking for one model was served
another with nothing in the response, the logs, or the audit row to say so.
These tests pin down both halves of the fix: the chain a name resolves to,
and what happens when it resolves to nothing.
"""

import pytest
from fastapi.testclient import TestClient

from app.budget import pricing
from app.core.config import settings
from app.main import app
from app.providers.base import ChatResponse
from app.routing import dependencies, model_map
from app.security.auth import require_api_key


class FakeRouter:
    async def chat(self, messages, request_id="", params=None):
        return ChatResponse(
            content="ok",
            provider="openai",
            model="gpt-4o-mini",
            input_tokens=1,
            output_tokens=1,
        )


@pytest.fixture
def client(monkeypatch, isolated_db, isolated_redis):
    monkeypatch.setattr(settings, "gateway_api_keys", "test-client-key")
    app.dependency_overrides[require_api_key] = lambda: "test-client-key"
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# --------------------------------------------------------------------------
# Chain resolution
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["default", "fast", "smart", "local"])
def test_each_named_chain_resolves_to_itself(name):
    chain_name, chain = model_map.resolve_chain(name)

    assert chain_name == name
    assert chain, f"chain {name!r} is empty, so every request to it would 502"


def test_unknown_model_resolves_to_the_default_chain():
    chain_name, chain = model_map.resolve_chain("gpt-4o")

    assert chain_name == "default"
    assert chain == model_map.FALLBACK_CHAINS["default"]


def test_chains_are_distinguishable():
    """A tier that resolves to the same providers as another tier is a name
    with no routing consequence — the bug this whole step exists to fix."""
    assert model_map.FALLBACK_CHAINS["smart"] != model_map.FALLBACK_CHAINS["default"]
    assert model_map.FALLBACK_CHAINS["local"] != model_map.FALLBACK_CHAINS["default"]


def test_local_chain_never_reaches_a_hosted_provider():
    providers = {provider for provider, _ in model_map.FALLBACK_CHAINS["local"]}

    assert providers == {"ollama"}, (
        "the 'local' chain exists so a caller can guarantee nothing leaves the"
        f" host and nothing is billed; it currently routes to {providers}"
    )


def test_smart_chain_does_not_silently_fall_back_to_a_local_model():
    providers = [provider for provider, _ in model_map.FALLBACK_CHAINS["smart"]]

    assert "ollama" not in providers, (
        "a caller asking for the strongest model should get a 502 rather than a"
        " local model quietly answering in its place"
    )


def test_is_routable_and_routable_models_agree():
    for name in model_map.routable_models():
        assert model_map.is_routable(name)

    assert not model_map.is_routable("definitely-not-a-chain")
    assert model_map.routable_models() == sorted(model_map.FALLBACK_CHAINS)


def test_build_router_reports_the_chain_it_used():
    chain_name, router = dependencies.build_router("local")

    assert chain_name == "local"
    assert len(router._chain) == len(model_map.FALLBACK_CHAINS["local"])


def test_build_router_reports_default_for_an_unknown_model():
    chain_name, _ = dependencies.build_router("gpt-4o")

    assert chain_name == "default"


# --------------------------------------------------------------------------
# Pricing keeps up with the chains
# --------------------------------------------------------------------------


def test_every_chain_model_is_priced_or_free():
    """Overlaps tests/test_pricing.py deliberately — that one guards the
    invariant in general, this one states it per chain so a failure names the
    tier an operator would have to stop trusting the budget for."""
    for name, chain in model_map.FALLBACK_CHAINS.items():
        for provider, model in chain:
            if provider == "ollama":
                continue
            assert f"{provider}:{model}" in pricing._PRICING, (
                f"chain {name!r} routes to {provider}:{model}, which has no price"
            )


# --------------------------------------------------------------------------
# Unknown models over HTTP — lenient (default) and strict
# --------------------------------------------------------------------------


def test_unknown_model_is_served_but_the_substitution_is_reported(
    client, monkeypatch, caplog
):
    """The old behavior, minus the silence."""
    import app.main as main_module

    monkeypatch.setattr(settings, "strict_model_routing", False)
    monkeypatch.setattr(main_module, "build_router", lambda m: ("default", FakeRouter()))

    with caplog.at_level("WARNING", logger="gateway.main"):
        r = client.post(
            "/v1/chat",
            json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
            headers={"X-API-Key": "test-client-key"},
        )

    assert r.status_code == 200
    assert r.headers["X-Gateway-Chain"] == "default"
    assert any("not routable" in rec.message for rec in caplog.records)


def test_known_model_is_not_warned_about(client, monkeypatch, caplog):
    import app.main as main_module

    monkeypatch.setattr(settings, "strict_model_routing", False)
    monkeypatch.setattr(main_module, "build_router", lambda m: ("local", FakeRouter()))

    with caplog.at_level("WARNING", logger="gateway.main"):
        r = client.post(
            "/v1/chat",
            json={"model": "local", "messages": [{"role": "user", "content": "hi"}]},
            headers={"X-API-Key": "test-client-key"},
        )

    assert r.status_code == 200
    assert r.headers["X-Gateway-Chain"] == "local"
    assert not any("not routable" in rec.message for rec in caplog.records)


def test_strict_mode_rejects_an_unknown_model(client, monkeypatch):
    monkeypatch.setattr(settings, "strict_model_routing", True)

    r = client.post(
        "/v1/chat",
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
        headers={"X-API-Key": "test-client-key"},
    )

    assert r.status_code == 404
    detail = r.json()["detail"]
    assert "gpt-4o" in detail["error"]
    # The error has to say what *would* work, or the caller is left guessing
    # at the exact names this gateway happens to use.
    assert detail["routable"] == model_map.routable_models()
    assert detail["request_id"]


def test_strict_mode_still_serves_a_known_model(client, monkeypatch):
    import app.main as main_module

    monkeypatch.setattr(settings, "strict_model_routing", True)
    monkeypatch.setattr(main_module, "build_router", lambda m: ("smart", FakeRouter()))

    r = client.post(
        "/v1/chat",
        json={"model": "smart", "messages": [{"role": "user", "content": "hi"}]},
        headers={"X-API-Key": "test-client-key"},
    )

    assert r.status_code == 200
    assert r.headers["X-Gateway-Chain"] == "smart"


def test_strict_mode_rejects_an_unknown_model_on_the_streaming_endpoint(
    client, monkeypatch
):
    monkeypatch.setattr(settings, "strict_model_routing", True)

    r = client.post(
        "/v1/chat/stream",
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
        headers={"X-API-Key": "test-client-key"},
    )

    assert r.status_code == 404


def test_lenient_mode_is_the_default():
    """The whole reason /v1 doesn't need a version bump: a request that used
    to return 200 still does, on a stock configuration."""
    from app.core.config import Settings

    assert Settings(_env_file=None).strict_model_routing is False  # type: ignore[call-arg]


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------


def test_models_endpoint_lists_every_routable_chain(client):
    r = client.get("/v1/models", headers={"X-API-Key": "test-client-key"})

    assert r.status_code == 200
    body = r.json()
    assert body["object"] == "list"
    assert [entry["id"] for entry in body["data"]] == model_map.routable_models()


def test_models_endpoint_reports_the_providers_behind_each_name(client):
    r = client.get("/v1/models", headers={"X-API-Key": "test-client-key"})

    by_id = {entry["id"]: entry for entry in r.json()["data"]}
    assert by_id["local"]["providers"] == ["ollama"]
    assert by_id["smart"]["providers"] == ["anthropic", "openai"]
