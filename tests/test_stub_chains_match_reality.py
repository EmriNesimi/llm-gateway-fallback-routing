"""The chains the money-path tests stub must be the chains the gateway routes.

Three end-to-end test files hardcode a copy of the `smart` chain, because
their stub routers have to answer as a specific provider and model, and
because their expected costs are written out as literals rather than computed
(see tests/test_ledger_conservation.py for why: a test that computes the
figure from the same helper the app uses agrees with itself even when that
helper is wrong).

Both of those are deliberate. What neither survives is the chain moving.
Re-pointing `smart` at a newer model is the advertised point of having chains
at all — and if it happened, these three files would keep serving the old
models, keep asserting the old prices, and keep passing while testing a
configuration the gateway no longer has.

So the copies are checked against the real thing here. A chain change fails
this one test, with a message saying which files also need their cost
literals revisited, instead of silently rotting three suites.
"""

import importlib

import pytest

from app.budget.pricing import _PRICING
from app.routing.model_map import FALLBACK_CHAINS

# Module, and the attribute holding its copy of the chain.
_STUBS = [
    ("tests.test_key_budget_race", "SMART_CHAIN"),
    ("tests.test_ledger_conservation", "SMART_CHAIN"),
    ("tests.test_spend_ceiling_e2e", "SMART_CHAIN"),
]


@pytest.mark.parametrize(("module_name", "attr"), _STUBS)
def test_stubbed_smart_chain_matches_the_real_one(module_name, attr):
    stub = getattr(importlib.import_module(module_name), attr)

    assert [tuple(hop) for hop in stub] == [tuple(h) for h in FALLBACK_CHAINS["smart"]], (
        f"{module_name}.{attr} no longer matches FALLBACK_CHAINS['smart'].\n"
        "The stub routers in that file answer as these providers and models, and"
        " its expected costs are written as literals priced against them — so"
        " both need updating together, not just this list."
    )


def test_every_stub_module_still_declares_one():
    """Renaming the constant away would make the guard above pass vacuously."""
    for module_name, attr in _STUBS:
        module = importlib.import_module(module_name)
        assert hasattr(module, attr), (
            f"{module_name} no longer has {attr}; this guard is no longer"
            " checking it. Point it at the new name or drop the entry."
        )


# What tests/test_key_budget_race.py and tests/test_spend_ceiling_e2e.py
# multiply out by hand, as `1000 * 5 / 1_000_000 + 40 * 25 / 1_000_000`.
_ASSUMED_RATES_PER_1M = {"anthropic:claude-opus-5": (5.00, 25.00)}


@pytest.mark.parametrize(("priced", "rates"), sorted(_ASSUMED_RATES_PER_1M.items()))
def test_hardcoded_rates_match_the_pricing_table(priced, rates):
    """Prices move. When one does, say so rather than failing on arithmetic.

    Two files spell their expected cost out as a sum of per-million rates
    instead of calling estimate_cost_usd, deliberately — a test that computes
    the figure from the same helper the app uses agrees with itself even when
    that helper is wrong. The cost of that is a literal that goes stale
    silently, and the failure it produces is an approx() mismatch six decimal
    places down, which reads like a bookkeeping bug rather than a price
    change.
    """
    assert priced in _PRICING, (
        f"{priced} is no longer priced at all, but tests still assume a rate for it"
    )
    assert _PRICING[priced] == pytest.approx(tuple(r / 1_000_000 for r in rates)), (
        f"{priced} is priced differently now. tests/test_key_budget_race.py and"
        " tests/test_spend_ceiling_e2e.py multiply the old rates out by hand and"
        " will fail on arithmetic until they are updated to match."
    )
