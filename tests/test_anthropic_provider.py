import httpx
import pytest
from anthropic import APIConnectionError, BadRequestError, RateLimitError

from app.providers.anthropic_provider import AnthropicProvider
from app.providers.base import ChatMessage, ProviderError


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


class _FakeStream:
    """Stands in for the async context manager anthropic's .stream() returns."""

    def __init__(self):
        self.text_stream = self._texts()

    async def _texts(self):
        yield "hello"

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get_final_message(self):
        class _Usage:
            input_tokens = 3
            output_tokens = 5

        class _Final:
            model = "claude-opus-5"
            usage = _Usage()

        return _Final()


@pytest.mark.asyncio
async def test_streaming_hoists_the_system_prompt_out_of_messages(monkeypatch):
    """Anthropic takes the system prompt as a top-level `system=` parameter and
    rejects role:"system" inside messages[] with a 400 — which is classified
    non-retryable, so the router falls straight past Anthropic to its OpenAI
    fallback. The non-streaming path was fixed and tested for this; the
    streaming path does the same hoisting in its own code and was not.

    The failure is invisible from outside: streamed requests carrying a system
    prompt just quietly get served by a different provider.
    """
    provider = AnthropicProvider(api_key="test")
    captured = {}

    def fake_stream(**kwargs):
        captured.update(kwargs)
        return _FakeStream()

    monkeypatch.setattr(provider._client.messages, "stream", fake_stream)

    messages = [
        ChatMessage(role="system", content="be terse"),
        ChatMessage(role="user", content="hi"),
    ]
    chunks = [c async for c in provider.chat_stream("claude-opus-5", messages)]

    assert captured["system"] == "be terse"
    assert [m["role"] for m in captured["messages"]] == ["user"]
    assert chunks[0].content == "hello"


@pytest.mark.asyncio
async def test_streaming_without_a_system_prompt_sends_no_system_key(monkeypatch):
    """Absent rather than empty: an explicit empty system prompt is not the
    same thing as none, and sending one would override the model default for
    every caller who never asked."""
    provider = AnthropicProvider(api_key="test")
    captured = {}

    def fake_stream(**kwargs):
        captured.update(kwargs)
        return _FakeStream()

    monkeypatch.setattr(provider._client.messages, "stream", fake_stream)

    async for _ in provider.chat_stream("claude-opus-5", [ChatMessage(role="user", content="hi")]):
        pass

    assert "system" not in captured


class _Block:
    def __init__(self, type_, text=""):
        self.type = type_
        self.text = text


class _Usage:
    input_tokens = 11
    output_tokens = 22


class _Response:
    model = "claude-opus-5-20260101"
    usage = _Usage()

    def __init__(self, content):
        self.content = content


@pytest.mark.asyncio
async def test_only_text_blocks_are_concatenated_into_the_reply(monkeypatch):
    """Anthropic returns `content` as a list of typed blocks, not a string.
    Non-text blocks have no `.text`, so including them would raise — and
    joining them blindly would splice tool-use metadata into what the caller
    sees as the model's answer.

    Only the error paths of this method were tested; the response parsing that
    produces every successful Anthropic reply was not.
    """
    provider = AnthropicProvider(api_key="test")

    async def fake_create(**kwargs):
        return _Response([_Block("text", "hello "), _Block("tool_use"), _Block("text", "world")])

    monkeypatch.setattr(provider._client.messages, "create", fake_create)

    result = await provider.chat("claude-opus-5", [ChatMessage(role="user", content="hi")])

    assert result.content == "hello world"
    assert result.provider == "anthropic"
    assert result.input_tokens == 11
    assert result.output_tokens == 22


@pytest.mark.asyncio
async def test_the_model_reported_is_the_one_anthropic_echoed(monkeypatch):
    """Anthropic answers with a dated snapshot id. Reporting the requested
    name instead would make the audit log claim a model that never ran."""
    provider = AnthropicProvider(api_key="test")

    async def fake_create(**kwargs):
        return _Response([_Block("text", "ok")])

    monkeypatch.setattr(provider._client.messages, "create", fake_create)

    result = await provider.chat("claude-opus-5", [ChatMessage(role="user", content="hi")])

    assert result.model == "claude-opus-5-20260101"
