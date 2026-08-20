"""USD price per token, by provider/model. Rates as of the provider's public pricing page.

Ollama is local/free, so it has no entry and costs $0.
"""

import logging

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
