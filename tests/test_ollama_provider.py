import pytest

import httpx

from app.providers.base import ProviderError, SamplingParams
from app.providers.ollama_provider import (
    OllamaProvider,
    _provider_error,
    _sampling_payload,
)


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


@pytest.mark.asyncio
async def test_blank_lines_in_the_stream_are_skipped(monkeypatch):
    """Ollama's NDJSON stream carries empty lines as keep-alives and around
    chunk boundaries. json.loads("") raises, so without the skip a healthy
    stream would surface as a ProviderError and fall back to a paid provider —
    turning a free local response into a billed one."""
    provider = OllamaProvider(base_url="http://localhost:11434")

    def fake_stream(self, method, url, json):
        return _FakeStreamResponse(
            [
                "",
                '{"message": {"content": "hi"}, "done": false}',
                "",
                '{"done": true, "prompt_eval_count": 2, "eval_count": 4}',
            ]
        )

    monkeypatch.setattr("httpx.AsyncClient.stream", fake_stream)

    chunks = [chunk async for chunk in provider.chat_stream("llama3", [])]

    assert [c.content for c in chunks] == ["hi", ""]
    assert chunks[-1].done is True


# --------------------------------------------------------------------------
# Sampling controls
# --------------------------------------------------------------------------
#
# Ollama names two of these differently from every other provider
# (max_tokens -> num_predict), and the mapping is hand-written per field. A
# field silently dropped here doesn't fail — the model just quietly ignores
# what the caller asked for, which is the hardest kind of bug to notice.


def test_every_sampling_field_reaches_ollama():
    payload = _sampling_payload(
        SamplingParams(temperature=0.2, top_p=0.9, max_tokens=64, stop=["END"])
    )

    assert payload == {
        "options": {
            "temperature": 0.2,
            "top_p": 0.9,
            "num_predict": 64,  # Ollama's name for max_tokens
            "stop": ["END"],
        }
    }


def test_unset_sampling_fields_are_omitted_entirely():
    """None means "don't send it", not "send our default" — sending an
    explicit value would override the model's own default for every caller
    who never asked."""
    assert _sampling_payload(SamplingParams(temperature=0.5)) == {
        "options": {"temperature": 0.5}
    }
    assert _sampling_payload(None) == {}
    assert _sampling_payload(SamplingParams()) == {}


# --------------------------------------------------------------------------
# Error classification
# --------------------------------------------------------------------------


def test_http_status_errors_are_classified_by_status_code():
    """A 400 fails identically on every retry; a 503 might not. Getting this
    wrong either burns retries for nothing or gives up on a blip."""
    request = httpx.Request("POST", "http://localhost:11434/api/chat")

    bad_request = httpx.HTTPStatusError(
        "bad request", request=request, response=httpx.Response(400, request=request)
    )
    unavailable = httpx.HTTPStatusError(
        "unavailable", request=request, response=httpx.Response(503, request=request)
    )

    assert _provider_error("ollama", bad_request).retryable is False
    assert _provider_error("ollama", unavailable).retryable is True


def test_transport_errors_are_retryable():
    """No response means no status to judge by. A connection refused or a
    timeout is the transient case retrying exists for, so it defaults to
    retryable rather than being treated as a hard failure."""
    exc = httpx.ConnectError("connection refused")

    assert _provider_error("ollama", exc).retryable is True
