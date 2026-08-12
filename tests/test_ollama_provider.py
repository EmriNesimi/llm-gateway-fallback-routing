import pytest

from app.providers.base import ProviderError
from app.providers.ollama_provider import OllamaProvider


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


@pytest.mark.asyncio
async def test_chat_raises_provider_error_on_malformed_json(monkeypatch):
    provider = OllamaProvider(base_url="http://localhost:11434")

    async def fake_post(self, url, json):
        return _FakeResponse({"unexpected": "shape"})  # no "message" key

    monkeypatch.setattr("httpx.AsyncClient.post", fake_post)

    with pytest.raises(ProviderError):
        await provider.chat("llama3", [])


@pytest.mark.asyncio
async def test_chat_succeeds_on_well_formed_response(monkeypatch):
    provider = OllamaProvider(base_url="http://localhost:11434")

    async def fake_post(self, url, json):
        return _FakeResponse(
            {
                "message": {"content": "hi there"},
                "model": "llama3",
                "prompt_eval_count": 3,
                "eval_count": 5,
            }
        )

    monkeypatch.setattr("httpx.AsyncClient.post", fake_post)

    result = await provider.chat("llama3", [])
    assert result.content == "hi there"
    assert result.input_tokens == 3
    assert result.output_tokens == 5


class _FakeStreamResponse:
    def __init__(self, lines):
        self._lines = lines

    def raise_for_status(self):
        pass

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@pytest.mark.asyncio
async def test_chat_stream_raises_provider_error_on_malformed_chunk(monkeypatch):
    provider = OllamaProvider(base_url="http://localhost:11434")

    def fake_stream(self, method, url, json):
        # A non-done chunk missing the expected "message" key.
        return _FakeStreamResponse(['{"done": false}'])

    monkeypatch.setattr("httpx.AsyncClient.stream", fake_stream)

    with pytest.raises(ProviderError):
        async for _ in provider.chat_stream("llama3", []):
            pass


@pytest.mark.asyncio
async def test_chat_stream_yields_chunks_from_well_formed_response(monkeypatch):
    provider = OllamaProvider(base_url="http://localhost:11434")

    def fake_stream(self, method, url, json):
        return _FakeStreamResponse(
            [
                '{"message": {"content": "hi"}, "done": false}',
                '{"done": true, "prompt_eval_count": 2, "eval_count": 4}',
            ]
        )

    monkeypatch.setattr("httpx.AsyncClient.stream", fake_stream)

    chunks = [chunk async for chunk in provider.chat_stream("llama3", [])]
    assert chunks[0].content == "hi"
    assert chunks[0].done is False
    assert chunks[1].done is True
    assert chunks[1].input_tokens == 2
    assert chunks[1].output_tokens == 4
