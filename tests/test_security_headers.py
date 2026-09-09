import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.core.config import settings
from app.main import app
from app.providers.base import StreamChunk


class _FakeRouter:
    async def chat_stream(self, messages, request_id="", params=None, skip_providers=None):
        yield StreamChunk(content="hi")
        yield StreamChunk(content="", done=True, input_tokens=5, output_tokens=5)


@pytest.fixture
def _streaming_client(monkeypatch):
    """A configured key and a stub router. Without the key the endpoint fails
    closed with a 503 and the test would be asserting headers on a refusal."""
    monkeypatch.setattr(settings, "gateway_api_keys", "test-client-key")
    monkeypatch.setattr(main_module, "build_router", lambda model: ("default", _FakeRouter()))
    with TestClient(app) as client:
        yield client


def test_security_headers_present_on_response():
    with TestClient(app) as client:
        r = client.get("/healthz")

    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["Referrer-Policy"] == "no-referrer"


def test_security_headers_present_on_a_streaming_response(
    _streaming_client, isolated_db, isolated_redis
):
    """Streaming responses are built by the endpoint rather than by FastAPI,
    and travel a different path through BaseHTTPMiddleware than a plain JSON
    response does. The rate-limit and budget headers are set by hand for
    exactly that reason (see the comment in chat_stream), so it is worth
    confirming the middleware-applied ones survive the same trip.
    """
    r = _streaming_client.post(
            "/v1/chat/stream",
            headers={"X-API-Key": "test-client-key"},
            json={"model": "default", "messages": [{"role": "user", "content": "hi"}]},
        )

    assert r.status_code == 200
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["Referrer-Policy"] == "no-referrer"
