import logging

import pytest

from app.budget.pricing import (
    _PRICING,
    UnpricedModelError,
    estimate_cost_usd,
    worst_case_cost_usd,
)
from app.routing.model_map import FALLBACK_CHAINS

# Ollama runs locally at no cost and deliberately has no pricing entry — see
# the module docstring in app/budget/pricing.py.
_FREE_PROVIDERS = {"ollama"}


def test_every_routable_model_has_a_pricing_entry():
    """A model the router can reach but the price table doesn't know costs
    $0.00 per request, with nothing but a log warning to say so. Spend never
    accumulates for it, so the monthly budget cap — a safety feature, and the
    thing standing between a misconfiguration and a real provider bill —
    silently stops applying while every request keeps succeeding.

    Adding a fallback chain is the easy way to introduce that gap, which is
    why this is checked against the routing table rather than a fixed list.
    """
    missing = sorted(
        {
            f"{provider}:{model}"
            for chain in FALLBACK_CHAINS.values()
            for provider, model in chain
            if provider not in _FREE_PROVIDERS and f"{provider}:{model}" not in _PRICING
        }
    )

    assert not missing, (
        f"{missing} can be routed to but has no entry in app/budget/pricing.py's"
        " _PRICING, so requests to it would cost $0.00 and escape budget"
        " enforcement entirely. Add real per-token rates, or add the provider to"
        " _FREE_PROVIDERS if it genuinely costs nothing."
    )


def test_known_model_computes_nonzero_cost():
    cost = estimate_cost_usd(
        provider="openai", model="gpt-4o-mini", input_tokens=1000, output_tokens=1000
    )
    assert cost > 0


def test_ollama_is_silently_free_with_no_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="gateway.pricing"):
        cost = estimate_cost_usd(
            provider="ollama", model="llama3", input_tokens=1000, output_tokens=1000
        )
    assert cost == 0.0
    assert caplog.records == []


def test_unknown_non_ollama_model_costs_zero_but_warns_loudly(caplog):
    with caplog.at_level(logging.WARNING, logger="gateway.pricing"):
        cost = estimate_cost_usd(
            provider="openai", model="some-future-model", input_tokens=1000, output_tokens=1000
        )
    assert cost == 0.0
    assert len(caplog.records) == 1
    assert "openai:some-future-model" in caplog.records[0].message


def test_an_unpriced_model_cannot_be_reserved_for(caplog):
    """Fail-closed. A $0 worst case used to mean reserve() claimed nothing, so
    the request ran outside the lifetime ceiling entirely — the one control
    meant to be un-bypassable. The honest answer to "how much could this cost"
    is "unknown", and unknown has to mean no.

    test_every_routable_model_has_a_pricing_entry is what stops this happening
    for a real model; this is the behaviour if that guard is ever wrong.
    """
    with caplog.at_level(logging.ERROR):
        with pytest.raises(UnpricedModelError) as exc_info:
            worst_case_cost_usd("openai", "not-a-real-model", 10_000, 2048)

    assert exc_info.value.provider == "openai"
    assert exc_info.value.model == "not-a-real-model"
    assert "openai:not-a-real-model" in caplog.text


def test_ollama_costs_nothing_without_complaint(caplog):
    """Ollama bills nothing, so "no price" is correct rather than missing. It
    must not raise, or the free fallback that exists for when the paid
    providers are gone would be the first thing to break."""
    with caplog.at_level(logging.WARNING):
        cost = worst_case_cost_usd("ollama", "llama3", 10_000, 2048)

    assert cost == 0.0
    assert caplog.text == ""


@pytest.mark.asyncio
async def test_free_providers_are_exempt_in_both_modules():
    """Pricing and the ledger have to agree on which providers are free.

    They briefly did not: pricing.py grew its own copy of the set. The failure
    that creates is silent and one-sided — pricing would refuse a free
    provider as un-costable while the ledger happily exempts it, so the free
    fallback that exists for when the paid providers are gone would be the
    thing that breaks.

    Asserted behaviourally rather than by comparing the two names, so it still
    catches a divergence introduced some other way.
    """
    import fakeredis.aioredis

    from app.budget.provider_budget import FREE_PROVIDERS, ProviderBudget

    budget = ProviderBudget(redis=fakeredis.aioredis.FakeRedis(), cap_usd=4.0)

    assert FREE_PROVIDERS, "no free providers — the guard would pass vacuously"
    for provider in FREE_PROVIDERS:
        # Pricing: costs nothing and does not refuse.
        assert worst_case_cost_usd(provider, "any-model", 10_000, 2048) == 0.0

        # Ledger: reserving is a no-op, so nothing to settle and no ceiling.
        await budget.reserve(provider, 99.0)
        assert await budget.spent(provider) == 0.0
        assert not await budget.is_exhausted(provider)
