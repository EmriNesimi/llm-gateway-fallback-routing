import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.admin import auth as admin_auth
from app.core.config import settings
from app.main import app
from app.providers.base import ChatResponse, StreamChunk
from app.routing.fallback import AllProvidersFailedError


class FakeRouter:
    async def chat(self, messages, request_id=""):
        return ChatResponse(
            content="hi", provider="openai", model="gpt-4o-mini", input_tokens=5, output_tokens=5
        )

    async def chat_stream(self, messages, request_id=""):
        yield StreamChunk(content="hi")
        yield StreamChunk(content="", done=True, input_tokens=5, output_tokens=5)


class FailingRouter:
    async def chat(self, messages, request_id=""):
        raise AllProvidersFailedError("all providers down")

    async def chat_stream(self, messages, request_id=""):
        raise AllProvidersFailedError("all providers down")
        yield  # pragma: no cover - makes this an async generator


@pytest.fixture(autouse=True)
def _client_key(monkeypatch):
    monkeypatch.setattr(settings, "gateway_api_keys", "test-client-key")
    monkeypatch.setattr(admin_auth.settings, "admin_api_key", "test-admin-secret")


def test_oversized_request_id_is_replaced_not_honored():
    # Regression test: AuditLogEntry.request_id is a String(64) column — on
    # Postgres, inserting anything longer raises StringDataRightTruncationError
    # at the DB layer. record_audit_log catches that (decision 004) so the
    # request itself never fails, but a caller sending an oversized ID on
    # every request would silently lose audit logging for all of it. Capped
    # at the source instead of relying on that safety net.
    oversized = "x" * 500
    with TestClient(app) as client:
        r = client.get("/healthz", headers={"X-Request-ID": oversized})

    assert r.headers["X-Request-ID"] != oversized
    assert len(r.headers["X-Request-ID"]) <= 64


def test_max_length_request_id_is_still_honored():
    exactly_64 = "a" * 64
    with TestClient(app) as client:
        r = client.get("/healthz", headers={"X-Request-ID": exactly_64})

    assert r.headers["X-Request-ID"] == exactly_64


def test_request_id_is_generated_when_not_supplied(isolated_db, isolated_redis, monkeypatch):
    monkeypatch.setattr(main_module, "build_router", lambda model: FakeRouter())

    with TestClient(app) as client:
        r = client.post(
            "/v1/chat",
            headers={"X-API-Key": "test-client-key"},
            json={"model": "default", "messages": [{"role": "user", "content": "hi"}]},
        )

    assert r.status_code == 200
    assert len(r.headers["X-Request-ID"]) > 0


def test_client_supplied_request_id_is_honored(isolated_db, isolated_redis, monkeypatch):
    monkeypatch.setattr(main_module, "build_router", lambda model: FakeRouter())

    with TestClient(app) as client:
        r = client.post(
            "/v1/chat",
            headers={"X-API-Key": "test-client-key", "X-Request-ID": "my-custom-id"},
            json={"model": "default", "messages": [{"role": "user", "content": "hi"}]},
        )

    assert r.headers["X-Request-ID"] == "my-custom-id"


def test_request_id_links_response_to_audit_log_row(isolated_db, isolated_redis, monkeypatch):
    monkeypatch.setattr(main_module, "build_router", lambda model: FakeRouter())

    with TestClient(app) as client:
        r = client.post(
            "/v1/chat",
            headers={"X-API-Key": "test-client-key", "X-Request-ID": "trace-me-123"},
            json={"model": "default", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert r.headers["X-Request-ID"] == "trace-me-123"

        audit = client.get(
            "/admin/audit-log",
            params={"request_id": "trace-me-123"},
            headers={"X-Admin-Key": "test-admin-secret"},
        )

    entries = audit.json()
    assert len(entries) == 1
    assert entries[0]["request_id"] == "trace-me-123"
    assert entries[0]["outcome"] == "success"


def test_request_id_included_in_error_response_and_audit_log(
    isolated_db, isolated_redis, monkeypatch
):
    monkeypatch.setattr(main_module, "build_router", lambda model: FailingRouter())

    with TestClient(app) as client:
        r = client.post(
            "/v1/chat",
            headers={"X-API-Key": "test-client-key", "X-Request-ID": "failed-request-1"},
            json={"model": "default", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert r.status_code == 502
        assert r.json()["detail"]["request_id"] == "failed-request-1"

        audit = client.get(
            "/admin/audit-log",
            params={"request_id": "failed-request-1"},
            headers={"X-Admin-Key": "test-admin-secret"},
        )

    entries = audit.json()
    assert len(entries) == 1
    assert entries[0]["outcome"] == "error"


def test_stream_request_id_header_and_error_event(isolated_db, isolated_redis, monkeypatch):
    monkeypatch.setattr(main_module, "build_router", lambda model: FailingRouter())

    with TestClient(app) as client:
        r = client.post(
            "/v1/chat/stream",
            headers={"X-API-Key": "test-client-key", "X-Request-ID": "stream-fail-1"},
            json={"model": "default", "messages": [{"role": "user", "content": "hi"}]},
        )

    assert r.headers["X-Request-ID"] == "stream-fail-1"
    assert "stream-fail-1" in r.text
