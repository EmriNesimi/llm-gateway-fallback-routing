"""Upstream error detail stays server-side.

Each provider adapter builds `ProviderError(f"{prefix}: {exc}")`, where `exc`
is the SDK exception whose string carries the upstream response body. Those
bodies contain API key prefixes and suffixes, organisation IDs, upstream
request IDs and rate-limit internals. `AllProvidersFailedError` concatenates
them, and the chat endpoints used to hand the whole thing back to the caller.

What a caller genuinely needs is the request ID, which is already correlated
to the full detail in the logs.
"""

import pytest
from fastapi.testclient import TestClient
from redis.asyncio import Redis

import app.main as main_module
from app.core.config import settings
from app.main import app
from app.routing.fallback import AllProvidersFailedError
from app.security.auth import require_api_key

# Shaped like a real upstream 401 body, which is where the leak came from.
# Entirely synthetic — no real key material.
LEAKY = (
    "openai request failed: Error code: 401 - {'error': {'message': "
    "'Incorrect API key provided: sk-proj-AbC************xyzA. "
    "You can find your API key at https://platform.openai.com/account/api-keys', "
    "'type': 'invalid_request_error', 'param': None, 'code': 'invalid_api_key'}}"
)
SECRETS = ("sk-proj-AbC", "xyzA", "invalid_api_key", "platform.openai.com")


class ExplodingRouter:
    async def chat(self, messages, request_id="", params=None, skip_providers=None):
        raise AllProvidersFailedError(LEAKY)

    async def chat_stream(self, messages, request_id="", params=None, skip_providers=None):
        raise AllProvidersFailedError(LEAKY)
        yield  # pragma: no cover - makes this an async generator


@pytest.fixture
def client(monkeypatch, isolated_db, isolated_redis):
    monkeypatch.setattr(settings, "gateway_api_keys", "test-client-key")
    monkeypatch.setattr(main_module, "build_router", lambda m: ("default", ExplodingRouter()))
    app.dependency_overrides[require_api_key] = lambda: "test-client-key"
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


BODY = {"model": "default", "messages": [{"role": "user", "content": "hi"}]}
HEADERS = {"X-API-Key": "test-client-key"}


def test_502_body_carries_no_upstream_detail(client):
    r = client.post("/v1/chat", json=BODY, headers=HEADERS)

    assert r.status_code == 502
    for secret in SECRETS:
        assert secret not in r.text, f"{secret!r} leaked into the 502 body"


def test_502_still_gives_the_caller_a_request_id(client):
    """Redaction mustn't make failures unsupportable — the ID is what turns
    an "it broke" report into the exact server-side log line."""
    r = client.post("/v1/chat", json=BODY, headers=HEADERS)

    assert r.json()["detail"]["request_id"]
    assert r.headers["X-Request-ID"]


def test_stream_error_event_carries_no_upstream_detail(client):
    r = client.post("/v1/chat/stream", json=BODY, headers=HEADERS)

    assert "event: error" in r.text
    for secret in SECRETS:
        assert secret not in r.text, f"{secret!r} leaked into the SSE error event"


def test_openai_stream_error_carries_no_upstream_detail(client):
    r = client.post(
        "/v1/chat/completions", json={**BODY, "stream": True}, headers=HEADERS
    )

    for secret in SECRETS:
        assert secret not in r.text, f"{secret!r} leaked into the completions stream"


def test_the_detail_is_still_logged(client, caplog):
    """Redacted from the response, not thrown away — an operator still needs
    to know which provider failed and why."""
    with caplog.at_level("ERROR", logger="gateway.main"):
        client.post("/v1/chat", json=BODY, headers=HEADERS)

    assert any("sk-proj-AbC" in r.getMessage() for r in caplog.records)


def test_readyz_reports_which_dependency_failed_but_not_why(isolated_db, monkeypatch):
    """Unauthenticated, so the exception text was free reconnaissance: hosts,
    ports, driver versions and failure modes for the backends that hold the
    spend counters."""
    monkeypatch.setattr(
        main_module, "get_redis", lambda: Redis.from_url("redis://localhost:1")
    )

    with TestClient(app) as c:
        r = c.get("/readyz")

    assert r.status_code == 503
    assert r.json()["checks"]["redis"] == "error"
    for leaky in ("localhost:1", "Errno", "ConnectionError", "Connect call failed"):
        assert leaky not in r.text, f"{leaky!r} leaked from /readyz"
