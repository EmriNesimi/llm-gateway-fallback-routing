import httpx
import pytest
from anthropic import APIConnectionError, BadRequestError, RateLimitError

from app.providers.anthropic_provider import AnthropicProvider
from app.providers.base import ProviderError


def _status_error(error_cls, status_code):
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(status_code, request=request)
    return error_cls(str(error_cls), response=response, body=None)


@pytest.mark.asyncio
async def test_bad_request_error_is_not_retryable(monkeypatch):
    # Regression test for the classification our fallback-skip logic (see
    # docs/decisions/005) depends on: a 400 must map to retryable=False so
    # the router doesn't burn retries against a request that will fail the
    # same way every time.
    provider = AnthropicProvider(api_key="test")

    async def fake_create(*a, **kw):
        raise _status_error(BadRequestError, 400)

    monkeypatch.setattr(provider._client.messages, "create", fake_create)

    with pytest.raises(ProviderError) as exc_info:
        await provider.chat("claude-3-5-haiku-20241022", [])

    assert exc_info.value.retryable is False


@pytest.mark.asyncio
async def test_rate_limit_error_is_retryable(monkeypatch):
    provider = AnthropicProvider(api_key="test")

    async def fake_create(*a, **kw):
        raise _status_error(RateLimitError, 429)

    monkeypatch.setattr(provider._client.messages, "create", fake_create)

    with pytest.raises(ProviderError) as exc_info:
        await provider.chat("claude-3-5-haiku-20241022", [])

    assert exc_info.value.retryable is True


@pytest.mark.asyncio
async def test_connection_error_is_retryable(monkeypatch):
    provider = AnthropicProvider(api_key="test")

    async def fake_create(*a, **kw):
        request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        raise APIConnectionError(request=request)

    monkeypatch.setattr(provider._client.messages, "create", fake_create)

    with pytest.raises(ProviderError) as exc_info:
        await provider.chat("claude-3-5-haiku-20241022", [])

    assert exc_info.value.retryable is True
