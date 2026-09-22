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
