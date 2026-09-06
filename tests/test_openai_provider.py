from unittest.mock import patch

import httpx
import pytest
from openai import APIConnectionError, BadRequestError, RateLimitError

from app.providers.base import ChatMessage, ProviderError
from app.providers.openai_provider import OpenAIProvider


def _status_error(error_cls, status_code):
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    response = httpx.Response(status_code, request=request)
    return error_cls(str(error_cls), response=response, body=None)


class _FakeUsage:
    prompt_tokens = 3
    completion_tokens = 5


class _FakeMessage:
    content = "hi there"


class _FakeChoice:
    message = _FakeMessage()


class _FakeResponse:
    def __init__(self, choices, usage=None, model="gpt-4o-mini"):
        self.choices = choices
        # Built per instance rather than shared as a default: one mutation in
        # any test would otherwise leak into every later one.
        self.usage = _FakeUsage() if usage is None else usage
        self.model = model


@pytest.mark.asyncio
async def test_chat_raises_provider_error_on_empty_choices():
    provider = OpenAIProvider(api_key="test")

    async def fake_create(*a, **kw):
        return _FakeResponse(choices=[])

    with patch.object(provider._client.chat.completions, "create", fake_create):
        with pytest.raises(ProviderError):
            await provider.chat("gpt-4o-mini", [])


@pytest.mark.asyncio
async def test_chat_succeeds_on_well_formed_response():
    provider = OpenAIProvider(api_key="test")

    async def fake_create(*a, **kw):
        return _FakeResponse(choices=[_FakeChoice()])

    with patch.object(provider._client.chat.completions, "create", fake_create):
        result = await provider.chat("gpt-4o-mini", [])

    assert result.content == "hi there"
    assert result.input_tokens == 3
    assert result.output_tokens == 5


@pytest.mark.asyncio
async def test_bad_request_error_is_not_retryable():
    # Regression test for the classification our fallback-skip logic (see
    # docs/decisions/005) depends on: a 400 must map to retryable=False so
    # the router doesn't burn retries against a request that will fail the
    # same way every time.
    provider = OpenAIProvider(api_key="test")

    async def fake_create(*a, **kw):
        raise _status_error(BadRequestError, 400)

    with patch.object(provider._client.chat.completions, "create", fake_create):
        with pytest.raises(ProviderError) as exc_info:
            await provider.chat("gpt-4o-mini", [])

    assert exc_info.value.retryable is False


@pytest.mark.asyncio
async def test_rate_limit_error_is_retryable():
    provider = OpenAIProvider(api_key="test")

    async def fake_create(*a, **kw):
        raise _status_error(RateLimitError, 429)

    with patch.object(provider._client.chat.completions, "create", fake_create):
        with pytest.raises(ProviderError) as exc_info:
            await provider.chat("gpt-4o-mini", [])

    assert exc_info.value.retryable is True


@pytest.mark.asyncio
async def test_connection_error_is_retryable():
    provider = OpenAIProvider(api_key="test")

    async def fake_create(*a, **kw):
        request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
        raise APIConnectionError(request=request)

    with patch.object(provider._client.chat.completions, "create", fake_create):
        with pytest.raises(ProviderError) as exc_info:
            await provider.chat("gpt-4o-mini", [])

    assert exc_info.value.retryable is True


@pytest.mark.asyncio
async def test_a_response_with_no_usage_is_still_charged(monkeypatch, caplog):
    """A missing `usage` block used to report 0 tokens, which costs $0, which
    makes _settle_chain refund the whole reservation for a call the provider
    already billed. Real spend, erased.

    The same proxies that drop `choices` drop this — the code eight lines up
    already defends against that case, so it is not hypothetical.
    """
    import logging

    provider = OpenAIProvider(api_key="test")

    class _NoUsageResponse:
        model = "gpt-4o-mini"
        usage = None

        class _Choice:
            class message:
                content = "x" * 300

        choices = [_Choice()]

    async def fake_create(**kwargs):
        return _NoUsageResponse()

    monkeypatch.setattr(provider._client.chat.completions, "create", fake_create)

    with caplog.at_level(logging.WARNING):
        result = await provider.chat(
            "gpt-4o-mini", [ChatMessage(role="user", content="y" * 60)]
        )

    assert result.output_tokens > 0, "a billed response was recorded as free"
    assert result.input_tokens > 0
    assert "no usage" in caplog.text
