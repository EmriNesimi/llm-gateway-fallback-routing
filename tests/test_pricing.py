import logging

from app.budget.pricing import estimate_cost_usd


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
