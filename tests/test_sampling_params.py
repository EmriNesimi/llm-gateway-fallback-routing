"""Sampling controls, from the request body down to each provider's wire format.

The interesting part isn't that a temperature gets passed along — it's that
the three providers disagree about almost everything: what the controls are
called, whether they're nested, whether the output cap is required, and
whether the newest models accept them at all. Each disagreement is a way for
a value to be silently lost or a request to be silently rejected.
"""

import httpx
import pytest

from app.providers.anthropic_provider import (
    DEFAULT_MAX_TOKENS,
    _accepts_sampling_controls,
)
from app.providers.anthropic_provider import _sampling_kwargs as anthropic_kwargs
from app.providers.base import ChatMessage, SamplingParams
from app.providers.ollama_provider import _sampling_payload
from app.providers.openai_provider import _sampling_kwargs as openai_kwargs
from app.schemas import ChatCompletionRequest

MESSAGES = [ChatMessage(role="user", content="hi")]


# --------------------------------------------------------------------------
# The neutral shape
# --------------------------------------------------------------------------


def test_unset_controls_are_none_not_defaults():
    """None has to mean "don't send it". Substituting our own default would
    change behavior for every caller who never asked to change it."""
    params = SamplingParams()

    assert params.is_empty()
    assert params.set_names() == []
    assert (params.temperature, params.top_p, params.max_tokens, params.stop) == (
        None,
        None,
        None,
        None,
    )


def test_set_names_reports_only_what_the_caller_set():
    assert SamplingParams(temperature=0.5, max_tokens=10).set_names() == [
        "temperature",
        "max_tokens",
    ]


# --------------------------------------------------------------------------
# Parsing the request body
# --------------------------------------------------------------------------


def _request(**kwargs):
    return ChatCompletionRequest(
        model="default", messages=[{"role": "user", "content": "hi"}], **kwargs
    )


def test_request_without_controls_produces_an_empty_params():
    assert _request().sampling_params().is_empty()


def test_request_controls_are_carried_across():
    params = _request(temperature=0.3, top_p=0.8, max_tokens=64).sampling_params()

    assert (params.temperature, params.top_p, params.max_tokens) == (0.3, 0.8, 64)


def test_a_bare_string_stop_is_normalised_to_a_list():
    """OpenAI accepts either; every provider downstream wants a list. Passing
    the raw string through would give Ollama a stop sequence per character."""
    assert _request(stop="END").sampling_params().stop == ["END"]
    assert _request(stop=["A", "B"]).sampling_params().stop == ["A", "B"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"temperature": -0.1},
        {"temperature": 2.1},
        {"top_p": 0},
        {"top_p": 1.5},
        {"max_tokens": 0},
        {"max_tokens": -5},
    ],
)
def test_out_of_range_values_are_rejected_at_the_boundary(kwargs):
    """Better a 422 naming the field than a provider 400, which the router
    reads as a dead provider — the caller would get an answer from the next
    provider in the chain instead of being told their value was invalid."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _request(**kwargs)


# --------------------------------------------------------------------------
# OpenAI's dialect
# --------------------------------------------------------------------------


def test_openai_sends_nothing_when_nothing_was_set():
    assert openai_kwargs(None) == {}
    assert openai_kwargs(SamplingParams()) == {}


def test_openai_uses_its_own_names():
    kwargs = openai_kwargs(
        SamplingParams(temperature=0.2, top_p=0.9, max_tokens=32, stop=["X"])
    )

    assert kwargs == {"temperature": 0.2, "top_p": 0.9, "max_tokens": 32, "stop": ["X"]}


def test_openai_omits_unset_controls_rather_than_sending_null():
    assert openai_kwargs(SamplingParams(temperature=0.5)) == {"temperature": 0.5}


def test_openai_forwards_a_zero_temperature():
    """0.0 is falsy, so a truthiness check would drop it — while being exactly
    the value someone sets when they want deterministic output."""
    assert openai_kwargs(SamplingParams(temperature=0.0))["temperature"] == 0.0


# --------------------------------------------------------------------------
# Ollama's dialect
# --------------------------------------------------------------------------


def test_ollama_nests_controls_and_renames_the_output_cap():
    payload = _sampling_payload(SamplingParams(temperature=0.4, max_tokens=16))

    assert payload == {"options": {"temperature": 0.4, "num_predict": 16}}


def test_ollama_omits_the_options_block_entirely_when_empty():
    """An explicit empty options block isn't the same as no block at all."""
    assert _sampling_payload(None) == {}
    assert _sampling_payload(SamplingParams()) == {}


# --------------------------------------------------------------------------
# Anthropic's dialect, and its model-specific restrictions
# --------------------------------------------------------------------------


def test_anthropic_always_sends_a_max_tokens():
    """Anthropic requires it, unlike the other two, so an unset one still
    needs a number."""
    assert anthropic_kwargs("claude-haiku-4-5", None) == {"max_tokens": DEFAULT_MAX_TOKENS}


def test_anthropic_lets_the_caller_raise_the_output_cap():
    """This was hardcoded to 1024, so every Anthropic response was silently
    truncated there no matter what the caller asked for."""
    kwargs = anthropic_kwargs("claude-haiku-4-5", SamplingParams(max_tokens=4096))

    assert kwargs["max_tokens"] == 4096


def test_anthropic_renames_stop_to_stop_sequences():
    kwargs = anthropic_kwargs("claude-haiku-4-5", SamplingParams(stop=["END"]))

    assert kwargs["stop_sequences"] == ["END"]
    assert "stop" not in kwargs


def test_anthropic_forwards_sampling_controls_to_models_that_accept_them():
    kwargs = anthropic_kwargs("claude-haiku-4-5", SamplingParams(temperature=0.6, top_p=0.7))

    assert kwargs["temperature"] == 0.6
    assert kwargs["top_p"] == 0.7


@pytest.mark.parametrize(
    "model,accepts",
    [
        ("claude-haiku-4-5", True),
        ("claude-sonnet-4-6", True),
        ("claude-opus-5", False),
        ("claude-sonnet-5", False),
        ("claude-opus-4-8", False),
        ("claude-fable-5", False),
    ],
)
def test_which_models_accept_sampling_controls(model, accepts):
    assert _accepts_sampling_controls(model) is accepts


def test_sampling_controls_are_dropped_for_models_that_reject_them(caplog):
    """The failure this prevents is indirect and nasty: sending temperature to
    claude-opus-5 is a 400, a 4xx is classified non-retryable, so the router
    would fall straight through to the next provider. Every "smart" request
    carrying a temperature would quietly be served by the OpenAI fallback."""
    with caplog.at_level("WARNING", logger="gateway.provider.anthropic"):
        kwargs = anthropic_kwargs("claude-opus-5", SamplingParams(temperature=0.6))

    assert "temperature" not in kwargs
    assert any("does not accept" in rec.message for rec in caplog.records)


def test_dropping_sampling_controls_still_honours_max_tokens_and_stop():
    """Only temperature and top_p were removed on those models; the other two
    controls still work and shouldn't be collateral damage."""
    kwargs = anthropic_kwargs(
        "claude-opus-5", SamplingParams(temperature=0.6, max_tokens=99, stop=["Z"])
    )

    assert kwargs["max_tokens"] == 99
    assert kwargs["stop_sequences"] == ["Z"]
    assert "temperature" not in kwargs


# --------------------------------------------------------------------------
# End to end through a provider
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_openai_provider_puts_the_controls_on_the_wire(monkeypatch):
    from types import SimpleNamespace

    from app.providers.openai_provider import OpenAIProvider

    provider = OpenAIProvider(api_key="sk-test", timeout_seconds=5)
    seen: dict = {}

    async def create(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
            model="gpt-4o-mini",
        )

    monkeypatch.setattr(provider._client.chat.completions, "create", create)
    await provider.chat("gpt-4o-mini", MESSAGES, SamplingParams(temperature=0.25))

    assert seen["temperature"] == 0.25


@pytest.mark.asyncio
async def test_ollama_provider_puts_the_controls_on_the_wire(monkeypatch):
    import json as _json

    from app.providers.ollama_provider import OllamaProvider

    provider = OllamaProvider(base_url="http://ollama.test", timeout_seconds=5)
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(_json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "message": {"content": "ok"},
                "model": "llama3",
                "prompt_eval_count": 1,
                "eval_count": 1,
            },
        )

    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    def patched(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", patched)
    await provider.chat("llama3", MESSAGES, SamplingParams(temperature=0.9, max_tokens=8))

    assert seen["options"] == {"temperature": 0.9, "num_predict": 8}


@pytest.mark.asyncio
async def test_router_carries_controls_down_the_fallback_chain():
    """A value that reaches the primary but not its fallback would mean a
    request behaves differently depending on which provider happened to be up
    — the exact class of surprise this gateway exists to remove."""
    from app.providers.base import ChatResponse, ProviderError
    from app.routing.circuit_breaker import CircuitBreaker
    from app.routing.fallback import FallbackRouter

    seen = []

    class Recording:
        def __init__(self, name, fail):
            self.name = name
            self._fail = fail

        async def chat(self, model, messages, params=None):
            seen.append((self.name, params))
            if self._fail:
                raise ProviderError("down")
            return ChatResponse(
                content="ok", provider=self.name, model=model, input_tokens=1, output_tokens=1
            )

    router = FallbackRouter(
        [
            (Recording("primary", True), "m", CircuitBreaker(9, 1)),
            (Recording("backup", False), "m", CircuitBreaker(9, 1)),
        ],
        retry_attempts=0,
    )

    params = SamplingParams(temperature=0.11)
    await router.chat(MESSAGES, params=params)

    assert [name for name, _ in seen] == ["primary", "backup"]
    assert all(p is params for _, p in seen)
