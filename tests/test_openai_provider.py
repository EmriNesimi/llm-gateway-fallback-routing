from unittest.mock import patch

import pytest

from app.providers.base import ProviderError
from app.providers.openai_provider import OpenAIProvider


class _FakeUsage:
    prompt_tokens = 3
    completion_tokens = 5


class _FakeMessage:
    content = "hi there"


class _FakeChoice:
    message = _FakeMessage()


class _FakeResponse:
    def __init__(self, choices, usage=_FakeUsage(), model="gpt-4o-mini"):
        self.choices = choices
        self.usage = usage
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
