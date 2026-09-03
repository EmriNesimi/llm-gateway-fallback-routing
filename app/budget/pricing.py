"""USD price per token, by provider/model. Rates as of the provider's public pricing page.

Ollama is local/free, so it has no entry and costs $0.
"""

import logging

from app.budget.provider_budget import FREE_PROVIDERS


class UnpricedModelError(Exception):
    """Raised when a billable model has no entry in _PRICING.

    Only ever raised from the reservation path. Returning $0 there meant
    reserving nothing, so the request ran entirely outside the lifetime
    ceiling — the one control that is supposed to be un-bypassable. Refusing
    to price it is the fail-closed answer: an un-costable request is one we
    cannot promise to stop.
    """

    def __init__(self, provider: str, model: str):
        super().__init__(
            f"no pricing entry for {provider}:{model} — cannot bound the cost of"
            " this request, so it cannot be allowed to run"
        )
        self.provider = provider
        self.model = model


logger = logging.getLogger("gateway.pricing")

# (input_price_per_token, output_price_per_token), quoted per 1M tokens below
# to match how both providers publish their rates.
#
# Anthropic rates are first-party API list prices. Bedrock and Vertex are
# partner-operated and priced separately, so these would be wrong for a
# deployment routed through either.
_PRICING: dict[str, tuple[float, float]] = {
    "openai:gpt-4o-mini": (0.15 / 1_000_000, 0.60 / 1_000_000),
    # Verified 2026-08-20 against developers.openai.com/api/docs/pricing.
    "openai:gpt-4o": (2.50 / 1_000_000, 10.00 / 1_000_000),
    "anthropic:claude-haiku-4-5": (1.00 / 1_000_000, 5.00 / 1_000_000),
    "anthropic:claude-opus-5": (5.00 / 1_000_000, 25.00 / 1_000_000),
    # No longer in any chain, kept so audit rows written against the previous
    # default chain still price correctly if they're ever recomputed.
    "anthropic:claude-3-5-haiku-20241022": (0.80 / 1_000_000, 4.00 / 1_000_000),
}


# Rough characters-per-token. Deliberately low: ~4 is the usual English
# approximation, and 3 over-counts — the safe direction when the number is
# reserving budget rather than billing.
_CHARS_PER_TOKEN = 3


def worst_case_cost_usd(
    provider: str, model: str, input_chars: int, max_output_tokens: int
) -> float:
    """The most a single request to this model could possibly cost.

    Used to reserve budget *before* the call. Checking `spent < cap` and then
    calling leaves two holes — one request costing more than the remaining
    headroom, and concurrent requests all observing the same pre-call total.
    Reserving the upper bound closes both.

    A billable model with no price raises UnpricedModelError rather than
    returning 0.0. A $0 worst case reserves nothing, which lets the request run
    outside the lifetime ceiling entirely — so the honest answer to "how much
    could this cost" is "unknown", and unknown has to mean no.

    tests/test_pricing.py fails the build if any model reachable from a chain
    has no price, so this should be unreachable in practice. It is the
    behaviour if that guard is ever wrong.
    """
    key = f"{provider}:{model}"
    pricing = _PRICING.get(key)
    if pricing is None:
        if provider in FREE_PROVIDERS:
            return 0.0
        logger.error(
            "no pricing entry for %s — refusing to reserve budget for an"
            " un-costable request. Add an entry to app/budget/pricing.py's"
            " _PRICING.",
            key,
        )
        raise UnpricedModelError(provider, model)
    input_price, output_price = pricing
    return (input_chars / _CHARS_PER_TOKEN) * input_price + max_output_tokens * output_price


def estimate_cost_usd(
    provider: str, model: str, input_tokens: int, output_tokens: int
) -> float:
    key = f"{provider}:{model}"
    pricing = _PRICING.get(key)
    if pricing is None:
        if provider != "ollama":
            # Ollama has no entry by design (local/free) — anything else
            # missing means a model was added to app/routing/model_map.py
            # without a matching pricing entry here. Silently returning $0
            # would make that invisible: budget enforcement (a core safety
            # feature) would stop working for this model with no signal
            # anything's wrong, since spend would never accumulate.
            logger.warning(
                "no pricing entry for %s — cost for this request will be recorded as"
                " $0, and budget enforcement will not see it. Add an entry to"
                " app/budget/pricing.py's _PRICING.",
                key,
            )
        pricing = (0.0, 0.0)
    input_price, output_price = pricing
    return input_tokens * input_price + output_tokens * output_price
