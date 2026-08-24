"""The spend ceiling, exercised through real HTTP requests.

tests/test_provider_budget.py proves the ledger arithmetic. This proves the
wiring: that a request actually reserves before the provider is called, that
an exhausted provider is dropped from the chain rather than called and
refused, that the refusal reaches the client as a 402, and — the one that
matters most — that hanging up mid-stream still charges for the tokens the
provider already generated.

That last one is why any of the rest works. Spend was recorded after the
streaming loop, and a disconnect skips everything after the loop, so the
ledger never advanced and no ceiling was ever reached.
"""

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.budget import dependency as budget_dependency
from app.core.config import settings
from app.main import app
from app.providers.base import ChatResponse, StreamChunk
from app.routing.fallback import AllProvidersFailedError
from app.security.auth import require_api_key

BODY = {"model": "smart", "messages": [{"role": "user", "content": "hi"}]}
HEADERS = {"X-API-Key": "test-client-key"}


# The real chain for "smart". Honouring the order — and the skip set — is the
# point: an exhausted provider must be stepped over, not halt the gateway,
# because the two balances are independent.
SMART_CHAIN = [("anthropic", "claude-opus-5"), ("openai", "gpt-4o")]


class Router:
    """Stands in for FallbackRouter, reporting enough tokens to cost real money.

    Honours `skip_providers` exactly as the real router does. An earlier
    version of this stub ignored it and always claimed to be anthropic, which
    made the ceiling look broken when it was working — the request was
    correctly falling through to openai.
    """

    def __init__(self, out_tokens=100_000):
        self.served = []
        self._out = out_tokens

    def _pick(self, skip_providers):
        for provider, model in SMART_CHAIN:
            if not skip_providers or provider not in skip_providers:
                return provider, model
        raise AllProvidersFailedError("every provider is out of budget")

    async def chat(self, messages, request_id="", params=None, skip_providers=None):
        provider, model = self._pick(skip_providers)
        self.served.append(provider)
        return ChatResponse(
            content="ok",
            provider=provider,
            model=model,
            input_tokens=1000,
            output_tokens=self._out,
        )

    async def chat_stream(self, messages, request_id="", params=None, skip_providers=None):
        provider, model = self._pick(skip_providers)
        self.served.append(provider)
        for _ in range(5):
            yield StreamChunk(content="x" * 3000, provider=provider, model=model)
        yield StreamChunk(
            content="",
            done=True,
            provider=provider,
            model=model,
            input_tokens=1000,
            output_tokens=self._out,
        )


@pytest.fixture
def client(monkeypatch, isolated_db, isolated_redis):
    monkeypatch.setattr(settings, "gateway_api_keys", "test-client-key")
    # A generous per-key monthly cap, so anything refused below is refused by
    # the lifetime provider ceiling and not by the other control.
    monkeypatch.setattr(settings, "monthly_budget_usd_per_key", 1000.0)
    monkeypatch.setattr(budget_dependency.tracker, "_monthly_cap_usd", 1000.0)
    monkeypatch.setattr(budget_dependency.provider_budget, "_cap_usd", 1.0)
    app.dependency_overrides[require_api_key] = lambda: "test-client-key"
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


async def _spent(provider="anthropic"):
    return await budget_dependency.provider_budget.spent(provider)


# --------------------------------------------------------------------------


def test_a_served_request_advances_the_ledger(client, monkeypatch):
    monkeypatch.setattr(main_module, "build_router", lambda m: ("smart", Router()))

    r = client.post("/v1/chat", json=BODY, headers=HEADERS)

    assert r.status_code == 200
    assert client.portal.call(_spent) > 0


def test_the_ceiling_eventually_refuses_with_402(client, monkeypatch):
    """The whole point: spending has to stop, and stop with a status the
    caller can act on rather than a generic failure."""
    monkeypatch.setattr(main_module, "build_router", lambda m: ("smart", Router()))

    statuses = [
        client.post("/v1/chat", json=BODY, headers=HEADERS).status_code for _ in range(6)
    ]

    assert 402 in statuses, f"never refused: {statuses}"
    first_refusal = statuses.index(402)
    assert all(
        s == 402 for s in statuses[first_refusal:]
    ), f"spending resumed after refusing: {statuses}"


def test_an_exhausted_provider_stops_being_served_and_the_chain_moves_on(
    client, monkeypatch
):
    """Exhausting one provider must fail over, not halt: the two balances are
    independent, which is the whole reason the ceiling is per-provider. And
    the exhausted one must be dropped *before* the call — refusing afterwards
    would already have cost money."""
    router = Router()
    monkeypatch.setattr(main_module, "build_router", lambda m: ("smart", router))

    for _ in range(20):
        if client.post("/v1/chat", json=BODY, headers=HEADERS).status_code == 402:
            break

    assert "anthropic" in router.served, "anthropic was never tried"
    assert "openai" in router.served, "chain never failed over to openai"
    # Once exhausted, anthropic must not appear again.
    last_anthropic = len(router.served) - 1 - router.served[::-1].index("anthropic")
    assert all(p == "openai" for p in router.served[last_anthropic + 1 :])


def test_the_402_says_what_was_spent(client, monkeypatch):
    monkeypatch.setattr(main_module, "build_router", lambda m: ("smart", Router()))

    for _ in range(8):
        r = client.post("/v1/chat", json=BODY, headers=HEADERS)
        if r.status_code == 402:
            break

    detail = r.json()["detail"]
    assert detail["error"] == "provider budget exhausted"
    assert "anthropic" in detail["spent"]
    assert detail["cap_usd"] == 1.0
    assert detail["request_id"]


def test_a_client_that_disconnects_mid_stream_is_still_charged(client, monkeypatch):
    """The bug that made every cap unreachable. record_spend sat after the
    streaming loop; a disconnect closes the generator, so nothing after the
    loop ran and tokens the provider had already billed for were recorded as
    $0.00 — forever, and repeatably."""
    monkeypatch.setattr(main_module, "build_router", lambda m: ("smart", Router()))

    before = client.portal.call(_spent)
    with client.stream("POST", "/v1/chat/stream", json=BODY, headers=HEADERS) as r:
        for _ in r.iter_lines():
            break  # read one line, then hang up

    assert client.portal.call(_spent) > before, "disconnect recorded no spend"


def test_a_completed_stream_is_charged_too(client, monkeypatch):
    monkeypatch.setattr(main_module, "build_router", lambda m: ("smart", Router()))

    before = client.portal.call(_spent)
    client.post("/v1/chat/stream", json=BODY, headers=HEADERS)

    assert client.portal.call(_spent) > before


def test_a_refused_request_does_not_leak_reservation(client, monkeypatch):
    """A reservation that outlives its request permanently shrinks the
    ceiling, so repeated refusals must not inflate recorded spend."""
    monkeypatch.setattr(main_module, "build_router", lambda m: ("smart", Router()))

    for _ in range(8):
        if client.post("/v1/chat", json=BODY, headers=HEADERS).status_code == 402:
            break
    at_refusal = client.portal.call(_spent)

    for _ in range(5):
        client.post("/v1/chat", json=BODY, headers=HEADERS)

    assert client.portal.call(_spent) == pytest.approx(at_refusal)
