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
