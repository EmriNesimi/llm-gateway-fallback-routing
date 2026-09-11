"""An unexpected failure mid-stream must not look like a finished answer.

Once the first SSE chunk is out, the 200 status line cannot be taken back. The
gateway already handles the *expected* mid-stream failure well —
AllProvidersFailedError is reported in-band as an `event: error` — but an
unexpected one used to propagate out of the generator and simply stop the
stream: no `[DONE]`, no error, nothing. A caller cannot distinguish that from a
complete response without tracking `[DONE]` themselves, and a partial answer
that looks finished is worse than an error because it gets acted on.
"""

import json

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.core.config import settings
from app.main import app
from app.providers.base import StreamChunk

BODY = {"model": "default", "messages": [{"role": "user", "content": "hi"}]}
HEADERS = {"X-API-Key": "test-client-key"}


class _Router:
    async def chat_stream(self, messages, request_id="", params=None, skip_providers=None):
        yield StreamChunk(content="partial answer", provider="openai", model="gpt-4o-mini")
        yield StreamChunk(
            content="", done=True, provider="openai", model="gpt-4o-mini",
            input_tokens=5, output_tokens=5,
        )


class _ExplodingRouter:
    """Streams one chunk, then fails with something that is not a
    ProviderError — a bug rather than a provider problem, so fallback does not
    apply and nothing below catches it."""

    async def chat_stream(self, messages, request_id="", params=None, skip_providers=None):
        yield StreamChunk(content="partial answer", provider="openai", model="gpt-4o-mini")
        raise RuntimeError("router blew up mid-stream")


@pytest.fixture
def exploding_client(monkeypatch, isolated_db, isolated_redis):
    monkeypatch.setattr(settings, "gateway_api_keys", "test-client-key")
    monkeypatch.setattr(
        main_module, "build_router", lambda m: ("default", _ExplodingRouter())
    )
    with TestClient(app) as c:
        yield c


@pytest.fixture
def client(monkeypatch, isolated_db, isolated_redis):
    monkeypatch.setattr(settings, "gateway_api_keys", "test-client-key")
    monkeypatch.setattr(main_module, "build_router", lambda m: ("default", _Router()))
    with TestClient(app) as c:
        yield c


def _frames(text: str) -> list[str]:
    return [b for b in text.split("\n\n") if b.strip()]


def test_a_failure_after_the_last_chunk_is_reported_in_band(client, monkeypatch):
    """The bookkeeping runs after the final chunk, on a response already
    committed to 200. If it raises, the caller must be told."""
    def boom(**kwargs):
        raise RuntimeError("pricing table exploded")

    monkeypatch.setattr(main_module, "estimate_cost_usd", boom)

    r = client.post("/v1/chat/stream", json=BODY, headers=HEADERS)

    assert r.status_code == 200  # cannot be anything else; headers are long gone
    frames = _frames(r.text)
    assert any(f.startswith("event: error") for f in frames), (
        f"the stream just stopped; caller cannot tell it failed: {frames}"
    )
    assert "data: [DONE]" not in r.text, "a failed stream claimed to be complete"

    payload = json.loads(
        next(f for f in frames if f.startswith("event: error")).split("data: ", 1)[1]
    )
    assert payload["error"] == "internal server error"
    assert payload["request_id"], "no request id to correlate with the logs"


def test_the_error_frame_carries_no_internal_detail(client, monkeypatch):
    """Same rule as the non-streaming 500: the exception text may carry
    provider error detail, key prefixes or organisation ids. It is logged, not
    returned."""
    def boom(**kwargs):
        raise RuntimeError("sk-secret-leaked-value org-12345")

    monkeypatch.setattr(main_module, "estimate_cost_usd", boom)

    r = client.post("/v1/chat/stream", json=BODY, headers=HEADERS)

    assert "sk-secret-leaked-value" not in r.text
    assert "org-12345" not in r.text


def test_a_healthy_stream_still_ends_with_done(client):
    """The guard against over-correcting: a normal stream must be unchanged."""
    r = client.post("/v1/chat/stream", json=BODY, headers=HEADERS)

    assert r.status_code == 200
    assert r.text.rstrip().endswith("data: [DONE]")
    assert "event: error" not in r.text


def test_the_openai_endpoint_reports_mid_stream_failures_too(client, monkeypatch):
    """The OpenAI-compatible endpoint has its own generator and its own copy
    of this logic. It is also the one an existing application is pointed at,
    so a truncated stream there is the version that actually reaches users."""
    def boom(**kwargs):
        raise RuntimeError("pricing table exploded")

    monkeypatch.setattr(main_module, "estimate_cost_usd", boom)

    r = client.post(
        "/v1/chat/completions",
        json={**BODY, "stream": True},
        headers=HEADERS,
    )

    assert r.status_code == 200
    assert "event: error" in r.text, "the stream stopped without telling the caller"
    assert "data: [DONE]" not in r.text, "a failed stream claimed to be complete"
    assert "pricing table exploded" not in r.text


@pytest.mark.parametrize(
    "path,body",
    [
        ("/v1/chat/stream", BODY),
        ("/v1/chat/completions", {**BODY, "stream": True}),
    ],
)
def test_a_failure_inside_the_loop_is_reported_in_band(exploding_client, path, body):
    """The other half: a failure part-way through streaming, not after it.

    The caller has already received a partial answer. Without the in-band
    report they keep it and never learn it is incomplete — which is the
    specific harm, since a truncated answer that looks finished gets used.
    """
    r = exploding_client.post(path, json=body, headers=HEADERS)

    assert r.status_code == 200
    assert "partial answer" in r.text, "the test did not get far enough to matter"
    assert "event: error" in r.text, "the stream stopped without telling the caller"
    assert "data: [DONE]" not in r.text
    assert "router blew up" not in r.text, "internal detail leaked to the caller"


def test_a_failed_stream_is_not_counted_as_a_success(client, monkeypatch):
    """Raised in review: `success` was incremented before the tail
    bookkeeping, so a tail failure recorded the same request as both a success
    and an unhandled exception — while the caller was told it failed.

    The status a request is counted under has to match what the caller got,
    otherwise the ratio alerts that divide by this counter are measuring
    something other than reality.
    """
    from prometheus_client import REGISTRY

    def count(status: str) -> float:
        return REGISTRY.get_sample_value(
            "gateway_requests_total", {"status": status}
        ) or 0.0

    def boom(**kwargs):
        raise RuntimeError("pricing table exploded")

    monkeypatch.setattr(main_module, "estimate_cost_usd", boom)

    before_success, before_error = count("success"), count("error")

    r = client.post("/v1/chat/stream", json=BODY, headers=HEADERS)

    assert "event: error" in r.text
    assert count("success") == before_success, "a failed stream counted as a success"
    assert count("error") == before_error + 1
