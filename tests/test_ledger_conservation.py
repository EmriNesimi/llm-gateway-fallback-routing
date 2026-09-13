"""Both ledgers must end every request holding exactly what it really cost.

The other money-path tests each pin one path. This pins the property that
spans all of them: reserve and settle are paired, so whatever a request
claims it either spends or hands back, on every outcome and every endpoint.

Worth having as its own file because of how the bugs in this path have
actually arrived. Every one of them — the refund in a `finally` that
subtracted twice, spend recorded after a streaming loop a disconnect skips,
retries reserved once and billed twice, a mid-chain failure stranding an
earlier hop — was a *pairing* defect, and each was found only after the fact
by someone reasoning about one specific path. A conservation check does not
need to know which path broke. It notices that the arithmetic stopped adding
up.

Two invariants, checked across the matrix:

1. **Neither ledger moves backwards.** A ledger that erodes can never reach
   its cap, so a cap it can never reach is not a cap.
2. **The per-key ledger moves by exactly what the provider ledgers moved.**
   They count the same dollars from different angles. Any gap between them is
   a reservation that was claimed and not released, or released twice.
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
SMART_CHAIN = [("anthropic", "claude-opus-5"), ("openai", "gpt-4o")]


class Router:
    """Serves, fails outright, or dies mid-stream — the three shapes a request
    can end in, all of which have to settle."""

    def __init__(self, mode="ok", out_tokens=40):
        self.mode, self._out = mode, out_tokens

    def _pick(self, skip):
        for provider, model in SMART_CHAIN:
            if not skip or provider not in skip:
                return provider, model
        raise AllProvidersFailedError("every provider is out of budget")

    async def chat(self, messages, request_id="", params=None, skip_providers=None):
        provider, model = self._pick(skip_providers)
        if self.mode == "fail":
            raise AllProvidersFailedError("everything died")
        return ChatResponse(
            content="ok", provider=provider, model=model,
            input_tokens=1000, output_tokens=self._out,
        )

    async def chat_stream(self, messages, request_id="", params=None, skip_providers=None):
        provider, model = self._pick(skip_providers)
        if self.mode == "fail":
            raise AllProvidersFailedError("everything died")
        for _ in range(3):
            yield StreamChunk(content="x" * 100, provider=provider, model=model)
        if self.mode == "abort":
            # Committed to a 200 and then died, so the usage totals never
            # arrive and the cost has to be estimated from what was streamed.
            raise RuntimeError("provider died mid-stream")
        yield StreamChunk(
            content="", done=True, provider=provider, model=model,
            input_tokens=1000, output_tokens=self._out,
        )


@pytest.fixture
def client(monkeypatch, isolated_db, isolated_redis):
    monkeypatch.setattr(settings, "gateway_api_keys", "test-client-key")
    # Both caps far out of reach: this file is about the arithmetic, and a
    # refusal partway through would stop measuring it.
    monkeypatch.setattr(budget_dependency.provider_budget, "_cap_usd", 10_000.0)
    monkeypatch.setattr(settings, "monthly_budget_usd_per_key", 10_000.0)
    monkeypatch.setattr(budget_dependency.tracker, "_monthly_cap_usd", 10_000.0)
    app.dependency_overrides[require_api_key] = lambda: "test-client-key"
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


async def _ledgers():
    return (
        await budget_dependency.provider_budget.spent("anthropic"),
        await budget_dependency.provider_budget.spent("openai"),
        await budget_dependency.tracker.spent_usd("test-client-key"),
    )


def _drive(client, endpoint):
    """One request, drained, with the failure modes absorbed.

    A provider dying mid-stream reaches the caller as an exception, and a
    stream that is never read never runs the settle at its tail — so both the
    drain and the catch are load-bearing rather than defensive noise. What
    happened to the response is deliberately not asserted here; this file is
    only about what the ledgers hold afterwards.
    """
    try:
        client.post(endpoint, json=BODY, headers=HEADERS).read()
    except RuntimeError:
        # The mid-stream death staged by Router(mode="abort").
        pass


def _deltas(client, before):
    after = client.portal.call(_ledgers)
    return tuple(after[i] - before[i] for i in range(3))


@pytest.mark.parametrize(
    "mode,endpoint",
    [
        ("ok", "/v1/chat"),
        ("fail", "/v1/chat"),
        ("ok", "/v1/chat/stream"),
        ("abort", "/v1/chat/stream"),
        ("fail", "/v1/chat/stream"),
        ("ok", "/v1/chat/completions"),
        ("fail", "/v1/chat/completions"),
    ],
)
def test_every_outcome_leaves_both_ledgers_consistent(client, monkeypatch, mode, endpoint):
    monkeypatch.setattr(main_module, "build_router", lambda m: ("smart", Router(mode)))

    before = client.portal.call(_ledgers)
    _drive(client, endpoint)

    anthropic, openai, key = _deltas(client, before)

    assert anthropic >= 0, f"the anthropic ledger went backwards by {-anthropic:.6f}"
    assert openai >= 0, f"the openai ledger went backwards by {-openai:.6f}"
    assert key >= 0, f"the key ledger went backwards by {-key:.6f}"
    assert key == pytest.approx(anthropic + openai, abs=1e-9), (
        f"the key ledger moved ${key:.6f} while the providers moved"
        f" ${anthropic + openai:.6f} — a reservation was stranded or released twice"
    )
    if mode == "fail":
        assert anthropic == pytest.approx(0.0) and openai == pytest.approx(0.0), (
            "a request that served nothing still charged for something"
        )


def test_mixed_traffic_does_not_drift(client, monkeypatch):
    """The cumulative version. A per-request error small enough to hide inside
    one request's rounding still compounds over thirty of them, and the ledger
    that matters here is lifetime — it never resets to wash the drift out."""
    before = client.portal.call(_ledgers)

    for i in range(30):
        mode = ["ok", "fail", "abort", "ok"][i % 4]
        monkeypatch.setattr(
            main_module, "build_router", lambda m, _m=mode: ("smart", Router(_m))
        )
        _drive(client, ["/v1/chat", "/v1/chat/stream", "/v1/chat/completions"][i % 3])

    anthropic, openai, key = _deltas(client, before)

    assert key > 0, "thirty requests moved nothing; the test proved nothing"
    assert key == pytest.approx(anthropic + openai, abs=1e-6), (
        f"drifted ${abs(key - anthropic - openai):.6f} over 30 requests"
    )
