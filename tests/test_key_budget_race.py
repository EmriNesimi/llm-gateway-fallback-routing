"""The per-key monthly cap, exercised through real HTTP requests.

tests/test_budget_tracker.py proves the ledger arithmetic. This proves the
wiring — that `MONTHLY_BUDGET_USD_PER_KEY` is reserved before a provider is
called rather than merely checked, and so holds when requests from one key
overlap.

Issue #15: `enforce_budget` read the running total and compared it to the cap,
and the real cost only landed after the provider had answered. Concurrent
requests from one key all read the same pre-call total, all passed the
comparison, and all proceeded — so the overshoot was bounded by the rate-limit
burst times the per-request cost, not by the cap.

The provider-ceiling equivalents live in tests/test_spend_ceiling_e2e.py. The
two ledgers are deliberately independent, so each needs its own proof: here the
*provider* cap is set out of reach, so anything refused below is refused by the
per-key control and not by the other one.
"""

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.budget import dependency as budget_dependency
from app.core.config import settings
from app.main import app
from app.providers.base import ChatMessage, ChatResponse
from app.routing.fallback import AllProvidersFailedError
from app.security.auth import require_api_key

BODY = {"model": "smart", "messages": [{"role": "user", "content": "hi"}]}
HEADERS = {"X-API-Key": "test-client-key"}

SMART_CHAIN = [("anthropic", "claude-opus-5"), ("openai", "gpt-4o")]


class Router:
    """Serves from the chain, honouring skip_providers like the real router."""

    def __init__(self, out_tokens=40):
        self.served = []
        self._out = out_tokens

    async def chat(self, messages, request_id="", params=None, skip_providers=None):
        for provider, model in SMART_CHAIN:
            if not skip_providers or provider not in skip_providers:
                self.served.append(provider)
                return ChatResponse(
                    content="ok",
                    provider=provider,
                    model=model,
                    input_tokens=1000,
                    output_tokens=self._out,
                )
        raise AllProvidersFailedError("every provider is out of budget")


@pytest.fixture
def client(monkeypatch, isolated_db, isolated_redis):
    monkeypatch.setattr(settings, "gateway_api_keys", "test-client-key")
    # The provider ceiling set far out of reach, so it cannot be the thing
    # refusing anything here — the mirror image of the fixture in
    # tests/test_spend_ceiling_e2e.py.
    monkeypatch.setattr(budget_dependency.provider_budget, "_cap_usd", 10_000.0)
    monkeypatch.setattr(settings, "monthly_budget_usd_per_key", 0.5)
    monkeypatch.setattr(budget_dependency.tracker, "_monthly_cap_usd", 0.5)
    app.dependency_overrides[require_api_key] = lambda: "test-client-key"
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


async def _key_spent(api_key="test-client-key"):
    return await budget_dependency.tracker.spent_usd(api_key)


# --------------------------------------------------------------------------


def test_a_served_request_advances_the_key_ledger(client, monkeypatch):
    monkeypatch.setattr(main_module, "build_router", lambda m: ("smart", Router()))

    assert client.post("/v1/chat", json=BODY, headers=HEADERS).status_code == 200
    assert client.portal.call(_key_spent) > 0


def test_the_key_ledger_settles_to_exactly_the_cost_of_one_request(client, monkeypatch):
    """Not the reservation, and not double. The reservation is the whole
    chain's worst case; what must remain afterwards is the real cost of the
    one hop that answered."""
    monkeypatch.setattr(main_module, "build_router", lambda m: ("smart", Router(out_tokens=40)))

    assert client.post("/v1/chat", json=BODY, headers=HEADERS).status_code == 200

    # 1000 in @ $5/1M + 40 out @ $25/1M on claude-opus-5
    expected = 1000 * 5 / 1_000_000 + 40 * 25 / 1_000_000
    assert client.portal.call(_key_spent) == pytest.approx(expected, rel=1e-6)


def test_repeated_requests_accumulate_on_the_key_ledger(client, monkeypatch):
    """Each request costing less than its reservation is the normal case. If
    the refund outran the charge the ledger would erode and the cap would
    never be reached."""
    monkeypatch.setattr(main_module, "build_router", lambda m: ("smart", Router(out_tokens=10)))

    seen = []
    for _ in range(4):
        client.post("/v1/chat", json=BODY, headers=HEADERS)
        seen.append(client.portal.call(_key_spent))

    assert seen == sorted(seen), f"key ledger went backwards: {seen}"
    assert seen[0] > 0


def test_the_key_cap_refuses_with_402_once_reached(client, monkeypatch):
    monkeypatch.setattr(
        main_module, "build_router", lambda m: ("smart", Router(out_tokens=100_000))
    )

    statuses = [
        client.post("/v1/chat", json=BODY, headers=HEADERS).status_code
        for _ in range(6)
    ]

    assert 402 in statuses, f"the per-key cap never refused: {statuses}"
    assert statuses[-1] == 402
    # Refused by this cap, not the other one. Both return 402, so the status
    # alone does not distinguish them.
    assert client.portal.call(budget_dependency.provider_budget.spent, "anthropic") < 10_000.0


def test_a_refused_request_never_reaches_a_provider(client, monkeypatch):
    """A 402 that still called the provider would have spent the money it was
    refusing to spend."""
    router = Router(out_tokens=100_000)
    monkeypatch.setattr(main_module, "build_router", lambda m: ("smart", router))

    while client.post("/v1/chat", json=BODY, headers=HEADERS).status_code != 402:
        pass

    calls_before = len(router.served)
    assert client.post("/v1/chat", json=BODY, headers=HEADERS).status_code == 402
    assert len(router.served) == calls_before, "a refused request still called a provider"


def test_a_refusal_does_not_consume_the_headroom_it_was_denied(client, monkeypatch):
    """The reservation of a refused request has to be handed straight back.
    Otherwise repeated refusals drain a budget no request ever spent — and the
    caller could never recover even after the month rolled over."""
    monkeypatch.setattr(
        main_module, "build_router", lambda m: ("smart", Router(out_tokens=100_000))
    )

    while client.post("/v1/chat", json=BODY, headers=HEADERS).status_code != 402:
        pass

    after_first_refusal = client.portal.call(_key_spent)
    for _ in range(5):
        assert client.post("/v1/chat", json=BODY, headers=HEADERS).status_code == 402

    assert client.portal.call(_key_spent) == pytest.approx(after_first_refusal)


def test_concurrent_requests_from_one_key_cannot_all_be_admitted(client, monkeypatch):
    """Issue #15 itself.

    Twenty simultaneous requests from one key used to read the same pre-call
    total of $0 and all proceed. Driven at the reservation rather than through
    twenty real HTTP requests because TestClient serialises them, which is
    exactly the interleaving the bug needs to not happen.
    """
    import asyncio

    from app.budget.tracker import KeyBudgetExhausted

    monkeypatch.setattr(main_module, "build_router", lambda m: ("smart", Router()))
    messages = [ChatMessage(role="user", content="hi")]

    async def attempt():
        try:
            reservations, _ = await main_module._reserve_chain(
                "smart", messages, None, "req-concurrent", "test-client-key"
            )
        except Exception as exc:  # HTTPException(402) or KeyBudgetExhausted
            if isinstance(exc, KeyBudgetExhausted) or getattr(exc, "status_code", None) == 402:
                return 0.0
            raise
        return sum(reservations.values())

    async def run():
        return await asyncio.gather(*(attempt() for _ in range(20)))

    admitted = client.portal.call(run)

    # Every admitted request holds a reservation, and they are all live at
    # once. Their total is what the caller could actually spend.
    assert sum(admitted) <= 0.5 + 1e-9, (
        f"20 concurrent requests claimed ${sum(admitted):.4f} against a $0.50 cap"
    )
    assert any(a > 0 for a in admitted), "the cap refused everything, including the first"


def test_a_redis_failure_at_key_reserve_releases_the_provider_reservations(
    client, monkeypatch
):
    """The provider reservations are claimed before the per-key one. If the
    key reserve then dies, they have to go back — otherwise a request that was
    never served permanently shrinks the operator's ceiling.

    Not a 402: this is Redis being unreachable, not a caller out of budget.
    """
    monkeypatch.setattr(main_module, "build_router", lambda m: ("smart", Router()))

    async def _boom(*a, **k):
        raise ConnectionError("redis went away mid-reserve")

    monkeypatch.setattr(budget_dependency.tracker, "reserve", _boom)

    before = client.portal.call(budget_dependency.provider_budget.spent, "anthropic")

    with pytest.raises(ConnectionError):
        client.post("/v1/chat", json=BODY, headers=HEADERS)

    after = client.portal.call(budget_dependency.provider_budget.spent, "anthropic")
    assert after == pytest.approx(before), (
        f"a failed reserve stranded ${after - before:.4f} of provider headroom"
    )
