"""USD price per token, by provider/model. Rates as of the provider's public pricing page.

Ollama is local/free, so it has no entry and costs $0.
"""

# (input_price_per_token, output_price_per_token)
_PRICING: dict[str, tuple[float, float]] = {
    "openai:gpt-4o-mini": (0.15 / 1_000_000, 0.60 / 1_000_000),
    "anthropic:claude-3-5-haiku-20241022": (0.80 / 1_000_000, 4.00 / 1_000_000),
}


def estimate_cost_usd(
    provider: str, model: str, input_tokens: int, output_tokens: int
) -> float:
    input_price, output_price = _PRICING.get(f"{provider}:{model}", (0.0, 0.0))
    return input_tokens * input_price + output_tokens * output_price
