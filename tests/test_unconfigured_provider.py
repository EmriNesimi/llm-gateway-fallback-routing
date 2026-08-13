import pytest

from app.providers.base import ProviderError, UnconfiguredProvider


@pytest.mark.asyncio
async def test_chat_raises_non_retryable_provider_error_immediately():
    provider = UnconfiguredProvider("openai")

    with pytest.raises(ProviderError) as exc_info:
        await provider.chat("gpt-4o-mini", [])

    assert exc_info.value.retryable is False
    assert "openai" in str(exc_info.value)
    assert "not configured" in str(exc_info.value)


@pytest.mark.asyncio
async def test_chat_stream_raises_non_retryable_provider_error_immediately():
    provider = UnconfiguredProvider("anthropic")

    with pytest.raises(ProviderError) as exc_info:
        async for _ in provider.chat_stream("claude-3-5-haiku-20241022", []):
            pass

    assert exc_info.value.retryable is False
    assert "anthropic" in str(exc_info.value)
