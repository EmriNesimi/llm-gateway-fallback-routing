from unittest.mock import patch

import httpx
import pytest
from openai import APIConnectionError, BadRequestError, RateLimitError

from app.providers.base import ProviderError
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
