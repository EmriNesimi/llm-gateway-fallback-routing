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

3. **What is left is the real cost, not the reservation.** The first two are
   not enough on their own, and it took a review to notice: _reserve_chain
   claims the same total on both ledgers, so if settling stopped happening
   entirely, both would sit frozen at that worst case — still balanced, still
   never negative, and invariant 2 would hold trivially while every request
   permanently leaked the gap between its reservation and its cost. So the
   exact figure is pinned per outcome.
"""

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.budget import dependency as budget_dependency
from app.core.config import settings
from app.main import app
from app.providers.base import ChatResponse, StreamChunk
from app.ratelimit import dependency as ratelimit_dependency
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
    # And the rate limiter out of the way with them. Its capacity is read once
    # at import, so patching the setting does nothing — the live limiter has
    # to be moved. Without this the thirty-request test below quietly became a
    # twenty-request one: the rest came back 429 having never reached the
    # money path, and the ledger assertions held because a refused request
    # reserves nothing. Exactly the kind of test that passes while measuring
    # a third less than it says it does.
    monkeypatch.setattr(ratelimit_dependency._limiter, "_capacity", 10_000)
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
    # No try/except: the mid-stream death staged by Router(mode="abort") is
    # absorbed by _event_stream's own handler and reported in-band, so it
    # never reaches the client. An `except` here would be dead code implying
    # a failure mode that does not exist.
    client.post(endpoint, json=BODY, headers=HEADERS).read()


def _deltas(client, before):
    after = client.portal.call(_ledgers)
    return tuple(after[i] - before[i] for i in range(3))


# What each outcome really costs on claude-opus-5 ($5/1M in, $25/1M out).
#
#   ok     1000 in + 40 out                         -> 0.005 + 0.001
#   abort  3 chunks x 100 chars, no usage totals, so
#          the estimate is 300 // 3 = 100 output     -> 0.0025
#   fail   nothing was served                        -> 0
#
# Spelled out rather than computed from the same helper the app uses, so a
# change to that helper has to be acknowledged here rather than silently
# agreeing with itself.
_EXPECTED_COST = {"ok": 0.006, "abort": 0.0025, "fail": 0.0}


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
    # Invariant 3. Without this the two above pass with settling switched off
    # entirely: both ledgers just stay at the worst case _reserve_chain put
    # there, balanced against each other and never negative, while the
    # difference between reservation and cost leaks on every request.
    expected = _EXPECTED_COST[mode]
    assert key == pytest.approx(expected, abs=1e-9), (
        f"expected ${expected:.6f} to remain on the ledgers, found ${key:.6f}."
        " Equal to the worst-case reservation means the request reserved and"
        " never settled"
    )

    if mode == "fail":
        assert anthropic == pytest.approx(0.0), "a failed request still charged anthropic"
        assert openai == pytest.approx(0.0), "a failed request still charged openai"


def test_mixed_traffic_does_not_drift(client, monkeypatch):
    """The cumulative version. A per-request error small enough to hide inside
    one request's rounding still compounds over thirty of them, and the ledger
    that matters here is lifetime — it never resets to wash the drift out."""
    before = client.portal.call(_ledgers)
    expected = 0.0

    for i in range(30):
        mode = ["ok", "fail", "abort", "ok"][i % 4]
        endpoint = ["/v1/chat", "/v1/chat/stream", "/v1/chat/completions"][i % 3]
        monkeypatch.setattr(
            main_module, "build_router", lambda m, _m=mode: ("smart", Router(_m))
        )
        _drive(client, endpoint)
        # "abort" only means anything to chat_stream; the non-streaming
        # endpoints take the ordinary path and cost the ordinary amount.
        if mode == "abort" and endpoint != "/v1/chat/stream":
            expected += _EXPECTED_COST["ok"]
        else:
            expected += _EXPECTED_COST[mode]

    anthropic, openai, key = _deltas(client, before)

    assert key == pytest.approx(anthropic + openai, abs=1e-6), (
        f"drifted ${abs(key - anthropic - openai):.6f} over 30 requests"
    )
    # Invariant 3 again, and the reason this test needs it as much as the
    # per-outcome one: reserving without ever settling also accumulates a
    # nonzero, perfectly balanced total. Only the exact figure separates
    # "settled thirty times" from "leaked thirty reservations".
    assert key == pytest.approx(expected, abs=1e-6), (
        f"thirty requests should leave ${expected:.6f}, found ${key:.6f}"
    )


@pytest.mark.asyncio
async def test_a_cancelled_provider_settle_still_settles_the_key_ledger(monkeypatch):
    """The two ledgers must not end a request in different states.

    _settle_providers re-raises a cancellation that interrupted it, so without
    the key settle being unconditional it would be skipped — leaving one
    caller's reservation claimed against a request that is already over, with
    the provider side correctly refunded. The conservation property above
    would then be false, and nothing would say so.
    """
    import asyncio

    import fakeredis.aioredis

    from app.budget.tracker import BudgetTracker

    tracker = BudgetTracker(redis=fakeredis.aioredis.FakeRedis(), monthly_cap_usd=100.0)
    monkeypatch.setattr(main_module, "budget_tracker", tracker)

    class _Cancels:
        cap_usd = 4.0

        async def settle(self, *a, **k):
            raise asyncio.CancelledError()

        async def record_unreserved(self, *a, **k):
            pass

    monkeypatch.setattr(main_module, "provider_budget", _Cancels())

    await tracker.reserve("cancelled-key", 0.5)
    assert await tracker.spent_usd("cancelled-key") == pytest.approx(0.5)

    with pytest.raises(asyncio.CancelledError):
        await main_module._settle_chain(
            "cancelled-key", {"anthropic": 0.5}, "anthropic", 0.2, "req-cancel"
        )

    assert await tracker.spent_usd("cancelled-key") == pytest.approx(0.2), (
        "the key ledger kept its reservation when the provider settle was cancelled"
    )
