import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.core.config import settings
from app.main import app
from app.providers.base import ChatResponse, StreamChunk
from app.ratelimit import dependency as ratelimit_dependency


class FakeRouter:
    async def chat(self, messages, request_id=""):
        return ChatResponse(
            content="hi", provider="openai", model="gpt-4o-mini", input_tokens=5, output_tokens=5
        )

    async def chat_stream(self, messages, request_id=""):
        yield StreamChunk(content="hi")
        yield StreamChunk(content="", done=True, input_tokens=5, output_tokens=5)


@pytest.fixture(autouse=True)
def _client_key(monkeypatch):
    monkeypatch.setattr(settings, "gateway_api_keys", "test-client-key")
    monkeypatch.setattr(main_module, "build_router", lambda model: FakeRouter())


def test_chat_response_includes_rate_limit_and_budget_headers(isolated_db, isolated_redis):
    with TestClient(app) as client:
        r = client.post(
            "/v1/chat",
            headers={"X-API-Key": "test-client-key"},
            json={"model": "default", "messages": [{"role": "user", "content": "hi"}]},
        )

    assert r.status_code == 200
    assert "X-RateLimit-Limit" in r.headers
    assert "X-RateLimit-Remaining" in r.headers
    assert "X-Budget-Remaining-USD" in r.headers


def test_stream_response_includes_rate_limit_and_budget_headers(isolated_db, isolated_redis):
    with TestClient(app) as client:
        r = client.post(
            "/v1/chat/stream",
            headers={"X-API-Key": "test-client-key"},
            json={"model": "default", "messages": [{"role": "user", "content": "hi"}]},
        )

    assert r.status_code == 200
    assert "X-RateLimit-Limit" in r.headers
    assert "X-RateLimit-Remaining" in r.headers
    assert "X-Budget-Remaining-USD" in r.headers


def test_rate_limit_headers_decrement_and_429_includes_retry_after(
    isolated_db, isolated_redis, monkeypatch
):
    # _limiter is a module-level singleton already constructed from settings at
    # import time, so its instance attributes must be patched directly.
    monkeypatch.setattr(ratelimit_dependency._limiter, "_capacity", 2)
    monkeypatch.setattr(ratelimit_dependency._limiter, "_refill_rate", 0.01)

    with TestClient(app) as client:
        body = {"model": "default", "messages": [{"role": "user", "content": "hi"}]}
        headers = {"X-API-Key": "test-client-key"}

        first = client.post("/v1/chat", headers=headers, json=body)
        second = client.post("/v1/chat", headers=headers, json=body)
        third = client.post("/v1/chat", headers=headers, json=body)

    assert first.headers["X-RateLimit-Remaining"] == "1"
    assert second.headers["X-RateLimit-Remaining"] == "0"
    assert third.status_code == 429
    assert third.headers["X-RateLimit-Remaining"] == "0"
    assert "Retry-After" in third.headers


def test_budget_exceeded_returns_402_with_zero_remaining_header(
    isolated_db, isolated_redis, monkeypatch
):
    # enforce_budget reads settings.monthly_budget_usd_per_key directly (not
    # tracker._monthly_cap_usd), so patching settings here is what actually matters.
    monkeypatch.setattr(settings, "monthly_budget_usd_per_key", 0.0)

    with TestClient(app) as client:
        r = client.post(
            "/v1/chat",
            headers={"X-API-Key": "test-client-key"},
            json={"model": "default", "messages": [{"role": "user", "content": "hi"}]},
        )

    assert r.status_code == 402
    assert r.headers["X-Budget-Remaining-USD"] == "0.0000"
