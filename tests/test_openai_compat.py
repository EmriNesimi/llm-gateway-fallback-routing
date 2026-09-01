"""The OpenAI-compatible surface at /v1/chat/completions.

The point of this endpoint is that an existing application can repoint its
base_url at the gateway and get fallback, rate limiting, budgets, and an audit
trail without changing a line. So the tests that matter most here are the ones
at the bottom, which drive the endpoint with the actual `openai` SDK rather
than with hand-built JSON — asserting on a payload we constructed ourselves
would only prove we're self-consistent, not that a real client works.
"""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.core.config import settings
from app.main import app
from app.providers.base import ChatResponse, StreamChunk
from app.security.auth import require_api_key


class FakeRouter:
    async def chat(self, messages, request_id="", params=None, skip_providers=None):
        return ChatResponse(
            content="hello from the gateway",
            provider="openai",
            model="gpt-4o-mini",
            input_tokens=11,
            output_tokens=7,
        )

    async def chat_stream(self, messages, request_id="", params=None, skip_providers=None):
        for piece in ("hel", "lo"):
            yield StreamChunk(
                content=piece, provider="openai", model="gpt-4o-mini", done=False
            )
        yield StreamChunk(
            content="",
            provider="openai",
            model="gpt-4o-mini",
            done=True,
            input_tokens=11,
            output_tokens=7,
        )


@pytest.fixture
def client(monkeypatch, isolated_db, isolated_redis):
    monkeypatch.setattr(settings, "gateway_api_keys", "test-client-key")
    monkeypatch.setattr(main_module, "build_router", lambda m: ("default", FakeRouter()))
    app.dependency_overrides[require_api_key] = lambda: "test-client-key"
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _body(**overrides):
    return {
        "model": "default",
        "messages": [{"role": "user", "content": "hi"}],
        **overrides,
    }


# --------------------------------------------------------------------------
# Response envelope
# --------------------------------------------------------------------------


def test_response_uses_the_openai_envelope(client):
    r = client.post("/v1/chat/completions", json=_body())

    assert r.status_code == 200
    body = r.json()
    assert body["object"] == "chat.completion"
    assert body["id"].startswith("chatcmpl-")
    assert isinstance(body["created"], int)
    # The field every OpenAI-shaped client actually reads.
    assert body["choices"][0]["message"]["content"] == "hello from the gateway"
    assert body["choices"][0]["message"]["role"] == "assistant"
    assert body["choices"][0]["index"] == 0
    assert body["choices"][0]["finish_reason"] == "stop"


def test_usage_uses_openai_token_names(client):
    body = client.post("/v1/chat/completions", json=_body()).json()

    assert body["usage"] == {
        "prompt_tokens": 11,
        "completion_tokens": 7,
        "total_tokens": 18,
    }


def test_model_reports_what_answered_not_the_chain_asked_for(client):
    body = client.post("/v1/chat/completions", json=_body(model="default")).json()

    assert body["model"] == "gpt-4o-mini"


def test_gateway_chain_header_is_present(client):
    r = client.post("/v1/chat/completions", json=_body())

    assert r.headers["X-Gateway-Chain"] == "default"


def test_rate_limit_and_budget_headers_are_present(client):
    r = client.post("/v1/chat/completions", json=_body())

    assert "X-RateLimit-Limit" in r.headers
    assert "X-RateLimit-Remaining" in r.headers
    assert "X-Budget-Remaining-USD" in r.headers


# --------------------------------------------------------------------------
# Streaming
# --------------------------------------------------------------------------


def _sse_payloads(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        if line.startswith("data: ") and line != "data: [DONE]":
            out.append(json.loads(line[len("data: ") :]))
    return out


def test_stream_emits_chat_completion_chunks(client):
    r = client.post("/v1/chat/completions", json=_body(stream=True))

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    chunks = _sse_payloads(r.text)
    assert all(c["object"] == "chat.completion.chunk" for c in chunks)
    assert r.text.rstrip().endswith("data: [DONE]")


def test_stream_sends_role_first_then_content_then_finish_reason(client):
    chunks = _sse_payloads(
        client.post("/v1/chat/completions", json=_body(stream=True)).text
    )

    # The exact sequence the OpenAI SDK's stream parser expects.
    assert chunks[0]["choices"][0]["delta"] == {"role": "assistant"}
    content = "".join(c["choices"][0]["delta"].get("content", "") for c in chunks)
    assert content == "hello"
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"


def test_stream_keeps_one_id_across_every_chunk(client):
    chunks = _sse_payloads(
        client.post("/v1/chat/completions", json=_body(stream=True)).text
    )

    assert len({c["id"] for c in chunks}) == 1


def test_stream_carries_the_gateway_chain_header(client):
    r = client.post("/v1/chat/completions", json=_body(stream=True))

    assert r.headers["X-Gateway-Chain"] == "default"


# --------------------------------------------------------------------------
# Routing and unsupported parameters
# --------------------------------------------------------------------------


def test_unknown_model_is_rejected_even_in_lenient_mode(client, monkeypatch):
    """/v1/chat substitutes because /v1 can't break. This endpoint has no
    existing callers, so it doesn't inherit that debt — see decision 009."""
    monkeypatch.setattr(settings, "strict_model_routing", False)

    r = client.post("/v1/chat/completions", json=_body(model="gpt-4o"))

    assert r.status_code == 404
    assert "gpt-4o" in r.json()["detail"]["error"]


def test_unsupported_parameters_are_accepted_but_reported(client, caplog):
    """temperature and top_p used to be listed here. They're forwarded now, so
    what's left is the genuinely OpenAI-specific set the provider adapters
    have no equivalent for."""
    with caplog.at_level("WARNING", logger="gateway.main"):
        r = client.post("/v1/chat/completions", json=_body(seed=42, presence_penalty=0.5))

    assert r.status_code == 200
    assert r.headers["X-Gateway-Ignored-Params"] == "presence_penalty,seed"
    assert any("seed" in rec.message for rec in caplog.records)


def test_forwarded_parameters_are_not_reported_as_ignored(client):
    r = client.post("/v1/chat/completions", json=_body(temperature=0.7, top_p=0.9))

    assert r.status_code == 200
    assert "X-Gateway-Ignored-Params" not in r.headers


def test_no_ignored_header_when_everything_was_understood(client):
    r = client.post("/v1/chat/completions", json=_body(stream=False))

    assert "X-Gateway-Ignored-Params" not in r.headers


def test_empty_messages_is_rejected(client):
    r = client.post("/v1/chat/completions", json=_body(messages=[]))

    assert r.status_code == 422


# --------------------------------------------------------------------------
# The real `openai` SDK, unmodified, against this app
# --------------------------------------------------------------------------


@pytest.fixture
def openai_client(monkeypatch, isolated_db, isolated_redis):
    """An actual openai.AsyncOpenAI wired to this ASGI app in-process.

    No network and no running server, but the SDK's own request building and
    response parsing are fully exercised — which is the part that would break
    if the envelope were subtly wrong.
    """
    from openai import AsyncOpenAI

    monkeypatch.setattr(settings, "gateway_api_keys", "test-client-key")
    monkeypatch.setattr(main_module, "build_router", lambda m: ("default", FakeRouter()))
    app.dependency_overrides[require_api_key] = lambda: "test-client-key"

    http_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gateway"
    )
    yield AsyncOpenAI(
        api_key="test-client-key",
        base_url="http://gateway/v1",
        http_client=http_client,
    )

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_openai_sdk_can_complete_against_the_gateway(openai_client):
    completion = await openai_client.chat.completions.create(
        model="default",
        messages=[{"role": "user", "content": "hi"}],
    )

    assert completion.choices[0].message.content == "hello from the gateway"
    assert completion.usage.prompt_tokens == 11
    assert completion.usage.completion_tokens == 7
    assert completion.object == "chat.completion"


@pytest.mark.asyncio
async def test_openai_sdk_can_stream_against_the_gateway(openai_client):
    stream = await openai_client.chat.completions.create(
        model="default",
        messages=[{"role": "user", "content": "hi"}],
        stream=True,
    )

    received = ""
    finish_reasons = []
    async for chunk in stream:
        received += chunk.choices[0].delta.content or ""
        if chunk.choices[0].finish_reason:
            finish_reasons.append(chunk.choices[0].finish_reason)

    assert received == "hello"
    assert finish_reasons == ["stop"]


@pytest.mark.asyncio
async def test_openai_sdk_lists_models(openai_client):
    models = await openai_client.models.list()

    assert [m.id for m in models.data] == ["default", "fast", "local", "smart"]


def test_streamed_requests_also_report_ignored_parameters(client):
    """The streaming path sets this header on its own StreamingResponse rather
    than on the shared `response` object, so it is a second implementation of
    the same contract — and it was the untested one. A client that sends
    `seed` and streams would otherwise get no hint it was dropped."""
    r = client.post("/v1/chat/completions", json=_body(stream=True, seed=42, presence_penalty=0.5))

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.headers["X-Gateway-Ignored-Params"] == "presence_penalty,seed"
