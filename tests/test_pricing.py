import logging

from app.budget.pricing import _PRICING, estimate_cost_usd
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
