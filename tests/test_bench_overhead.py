"""The benchmark's contract: it refuses clearly, and it never guesses.

Not a performance test — timings from a shared CI runner are worthless and
this asserts nothing about them. What it pins is the behaviour around the
measurement, which is what a person actually hits: a mistyped chain, a Redis
that is down, and the free-chain comparison pointing at a chain that really
does reserve nothing.
"""

import pytest

from app.budget.provider_budget import FREE_PROVIDERS
from app.routing.model_map import FALLBACK_CHAINS
from scripts.bench_overhead import _FREE_CHAIN, _run


def test_the_comparison_chain_really_does_reserve_nothing():
    """`--compare` subtracts this chain's cost to isolate the reservation.

    That subtraction only means anything while every provider in it is free:
    _reserve_chain skips free providers, and with nothing claimed against a
    provider it skips the per-key reservation too. Add one billable hop here
    and the delta quietly starts measuring the difference between two
    reserving chains, which is not a number anyone wants.
    """
    assert _FREE_CHAIN in FALLBACK_CHAINS, f"{_FREE_CHAIN!r} is not a chain"

    billable = [p for p, _ in FALLBACK_CHAINS[_FREE_CHAIN] if p not in FREE_PROVIDERS]
    assert not billable, (
        f"the {_FREE_CHAIN!r} chain now routes to {billable}, which reserve."
        " `bench --compare` subtracts this chain to isolate the reservation"
        " cost, and that subtraction is only valid while it reserves nothing."
    )


@pytest.fixture(autouse=True)
def _contain_the_globals_run_mutates(monkeypatch):
    """`_run` is a script's entry point and owns the process it runs in.

    It raises the rate-limit capacity and both spend caps so a benchmark is
    not refused partway through, and overrides authentication so every
    request is let through — then leaves all of it in place. Correct for a
    script; leakage when a test calls it.

    Both halves were found the hard way. The capacity left at 152 stopped
    the admin rate-limit tests seeing a 429, and the auth override let
    `test_a_wrong_client_key_is_deliberately_not_rate_limited` authenticate
    every bad key it sends. Each failed only in combination and passed in
    isolation.
    """
    from app.budget import dependency as budget_dependency
    from app.core.config import settings
    from app.main import app
    from app.ratelimit import dependency as ratelimit_dependency

    # A dict entry, so monkeypatch cannot snapshot it by attribute.
    overrides = dict(app.dependency_overrides)

    monkeypatch.setattr(
        ratelimit_dependency._limiter, "_capacity", ratelimit_dependency._limiter._capacity,
    )
    monkeypatch.setattr(
        settings, "monthly_budget_usd_per_key", settings.monthly_budget_usd_per_key,
    )
    monkeypatch.setattr(
        budget_dependency.tracker, "_monthly_cap_usd",
        budget_dependency.tracker._monthly_cap_usd,
    )
    monkeypatch.setattr(
        budget_dependency.provider_budget, "_cap_usd",
        budget_dependency.provider_budget._cap_usd,
    )

    yield

    app.dependency_overrides.clear()
    app.dependency_overrides.update(overrides)


@pytest.mark.asyncio
async def test_an_unknown_chain_is_refused_by_name(capsys):
    assert await _run(requests=1, chain="no-such-chain", compare=False) == 1

    err = capsys.readouterr().err
    assert "no chain named 'no-such-chain'" in err
    # The question you have the moment you get it wrong.
    for name in FALLBACK_CHAINS:
        assert name in err, f"{name!r} missing from the list of available chains"


@pytest.mark.asyncio
async def test_an_unreachable_redis_exits_two(monkeypatch, capsys):
    """Distinct from 1, which is "the benchmark ran and something failed".

    The ledgers fail closed, so an unreachable Redis means every request is
    refused — without a clear message that reads as the gateway being slow,
    which is the opposite of what is happening.
    """
    from app.ratelimit import dependency as ratelimit_dependency

    async def _unreachable():
        raise ConnectionError("connection refused")

    monkeypatch.setattr(ratelimit_dependency._limiter._redis, "ping", _unreachable)

    assert await _run(requests=1, chain="smart", compare=False) == 2
    assert "cannot reach Redis" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_an_impossible_reservation_cost_is_refused(monkeypatch, capsys):
    """A negative delta is not a finding, it is a broken measurement.

    Reserving cannot make a request faster. If the subtraction comes out
    negative, the noise on the machine was larger than the thing being
    measured — and a loaded laptop will do that while still printing a
    confident-looking number with two decimal places.

    Its own exit code, so a caller can tell "could not measure" apart from
    "measured, and here is the figure".
    """
    from scripts import bench_overhead

    async def _faster_when_reserving(client, requests, chain):
        # The free chain made to look slower than the billable one, which is
        # what a noisy machine produces by accident.
        slow, fast = [10.0] * requests, [20.0] * requests
        samples = fast if chain == bench_overhead._FREE_CHAIN else slow
        return {"/v1/chat": samples}

    monkeypatch.setattr(bench_overhead, "_measure", _faster_when_reserving)

    assert await _run(requests=3, chain="smart", compare=True) == bench_overhead._TOO_NOISY

    out = capsys.readouterr().out
    assert "impossible" in out
    assert "Re-run on an idle machine" in out
