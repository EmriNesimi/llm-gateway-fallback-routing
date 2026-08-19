"""Provider streaming implementations.

Both hosted providers had their streaming bodies untested — 75% on OpenAI and
69% on Anthropic, with the gaps sitting exactly on `chat_stream`. That matters
more than the percentages suggest: the router's fallback only works up to the
first chunk, so a bug in how a chunk is built, or in how a stream error is
classified, decides whether a failure falls over cleanly or strands the caller
mid-answer.

The SDK objects are stubbed rather than mocked wholesale, so the shape each
provider actually reads is spelled out here and a change in that shape shows
up as a test failure rather than a runtime AttributeError.
"""

from types import SimpleNamespace

import pytest

from app.providers.anthropic_provider import AnthropicProvider
from app.providers.base import ChatMessage, ProviderError
from app.providers.openai_provider import OpenAIProvider

MESSAGES = [ChatMessage(role="user", content="hi")]


# --------------------------------------------------------------------------
# OpenAI
# --------------------------------------------------------------------------


def _openai_delta(text):
    return SimpleNamespace(
        usage=None, choices=[SimpleNamespace(delta=SimpleNamespace(content=text))]
    )


def _openai_usage(prompt, completion):
    return SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion), choices=[]
    )


class _FakeOpenAIStream:
    def __init__(self, events):
        self._events = events

    def __aiter__(self):
        async def gen():
            for event in self._events:
                yield event

        return gen()


def _openai_provider(monkeypatch, events=None, error=None):
    provider = OpenAIProvider(api_key="sk-test", timeout_seconds=5)

    async def create(**kwargs):
        if error is not None:
            raise error
        create.kwargs = kwargs
        return _FakeOpenAIStream(events or [])

    monkeypatch.setattr(provider._client.chat.completions, "create", create)
    return provider, create


@pytest.mark.asyncio
async def test_openai_stream_yields_content_then_a_final_usage_chunk(monkeypatch):
    provider, _ = _openai_provider(
        monkeypatch, [_openai_delta("Hel"), _openai_delta("lo"), _openai_usage(9, 4)]
    )

    chunks = [c async for c in provider.chat_stream("gpt-4o-mini", MESSAGES)]

    assert [c.content for c in chunks[:-1]] == ["Hel", "lo"]
    assert chunks[-1].done is True
    assert (chunks[-1].input_tokens, chunks[-1].output_tokens) == (9, 4)


@pytest.mark.asyncio
async def test_openai_stream_skips_empty_deltas(monkeypatch):
    """OpenAI sends a role-only opening delta and keep-alive frames carrying no
    content. Forwarding those would make the router treat an empty frame as the
    committing first chunk."""
    provider, _ = _openai_provider(
        monkeypatch,
        [_openai_delta(None), _openai_delta(""), _openai_delta("real"), _openai_usage(1, 1)],
    )

    chunks = [c async for c in provider.chat_stream("gpt-4o-mini", MESSAGES)]

    assert [c.content for c in chunks if not c.done] == ["real"]


@pytest.mark.asyncio
async def test_openai_stream_requests_usage(monkeypatch):
    """Without stream_options include_usage no usage frame ever arrives, cost
    is recorded as $0.00, and the budget silently stops counting streamed
    requests."""
    provider, create = _openai_provider(monkeypatch, [_openai_usage(1, 1)])

    [c async for c in provider.chat_stream("gpt-4o-mini", MESSAGES)]

    assert create.kwargs["stream"] is True
    assert create.kwargs["stream_options"] == {"include_usage": True}


@pytest.mark.asyncio
async def test_openai_stream_failure_becomes_a_provider_error(monkeypatch):
    """It has to be ProviderError specifically — that's what FallbackRouter
    catches. Anything else escapes the chain and surfaces as a raw 500."""
    import httpx
    from openai import APIStatusError

    error = APIStatusError(
        "rate limited",
        response=httpx.Response(429, request=httpx.Request("POST", "https://api.openai.com")),
        body=None,
    )
    provider, _ = _openai_provider(monkeypatch, error=error)

    with pytest.raises(ProviderError):
        [c async for c in provider.chat_stream("gpt-4o-mini", MESSAGES)]


# --------------------------------------------------------------------------
# Anthropic
# --------------------------------------------------------------------------


class _FakeAnthropicStream:
    def __init__(self, texts, usage):
        self._texts = texts
        self._usage = usage

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    @property
    def text_stream(self):
        async def gen():
            for text in self._texts:
                yield text

        return gen()

    async def get_final_message(self):
        return SimpleNamespace(usage=self._usage)


def _anthropic_provider(monkeypatch, texts=None, usage=None, error=None):
    provider = AnthropicProvider(api_key="sk-ant-test", timeout_seconds=5)

    def stream(**kwargs):
        if error is not None:
            raise error
        return _FakeAnthropicStream(
            texts or [], usage or SimpleNamespace(input_tokens=0, output_tokens=0)
        )

    monkeypatch.setattr(provider._client.messages, "stream", stream)
    return provider


@pytest.mark.asyncio
async def test_anthropic_stream_yields_text_then_a_final_usage_chunk(monkeypatch):
    provider = _anthropic_provider(
        monkeypatch,
        texts=["Hel", "lo"],
        usage=SimpleNamespace(input_tokens=12, output_tokens=6),
    )

    chunks = [c async for c in provider.chat_stream("claude-haiku-4-5", MESSAGES)]

    assert [c.content for c in chunks[:-1]] == ["Hel", "lo"]
    assert chunks[-1].done is True
    assert (chunks[-1].input_tokens, chunks[-1].output_tokens) == (12, 6)


@pytest.mark.asyncio
async def test_anthropic_stream_still_reports_usage_for_an_empty_response(monkeypatch):
    """A response with no text still costs input tokens. Skipping the final
    chunk would mean the request is served and never billed."""
    provider = _anthropic_provider(
        monkeypatch, texts=[], usage=SimpleNamespace(input_tokens=7, output_tokens=0)
    )

    chunks = [c async for c in provider.chat_stream("claude-haiku-4-5", MESSAGES)]

    assert len(chunks) == 1
    assert chunks[0].done is True
    assert chunks[0].input_tokens == 7


@pytest.mark.asyncio
async def test_anthropic_stream_failure_becomes_a_provider_error(monkeypatch):
    import httpx
    from anthropic import APIConnectionError

    provider = _anthropic_provider(
        monkeypatch,
        error=APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com")),
    )

    with pytest.raises(ProviderError):
        [c async for c in provider.chat_stream("claude-haiku-4-5", MESSAGES)]
