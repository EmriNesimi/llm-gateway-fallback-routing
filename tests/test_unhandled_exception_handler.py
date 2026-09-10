import pytest
from fastapi.testclient import TestClient

from app.admin import auth as admin_auth
from app.db import session as db_session
from app.main import app


@pytest.fixture(autouse=True)
def _admin_key(monkeypatch):
    monkeypatch.setattr(admin_auth.settings, "admin_api_key", "test-admin-secret")


def test_unhandled_exception_returns_structured_500_with_request_id():
    async def broken_session():
        raise RuntimeError("boom")
        yield  # pragma: no cover - unreachable, satisfies the generator shape

    # dependency_overrides, not monkeypatch — routes.py already bound its own
    # `get_session` name at import time, so patching the db_session module
    # attribute afterwards wouldn't affect what the route actually calls.
    app.dependency_overrides[db_session.get_session] = broken_session

    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.get(
                "/admin/keys",
                headers={"X-Admin-Key": "test-admin-secret", "X-Request-ID": "test-req-id"},
            )
    finally:
        app.dependency_overrides.pop(db_session.get_session, None)

    assert r.status_code == 500
    body = r.json()
    assert body["error"] == "internal server error"
    assert body["request_id"] == "test-req-id"
    assert r.headers["X-Request-ID"] == "test-req-id"


def test_a_500_is_counted():
    """A 500 reaches no other counter. REQUEST_COUNT is incremented inside the
    handlers, so anything raising before or around them — a Redis outage
    making the rate limiter throw, a bug in a dependency — was recorded
    nowhere at all. gateway_requests_total simply stops rising, which on a
    dashboard is indistinguishable from nobody calling.
    """
    from prometheus_client import REGISTRY

    def _count() -> float:
        return REGISTRY.get_sample_value("gateway_unhandled_exceptions_total") or 0.0

    async def broken_session():
        raise RuntimeError("boom")
        yield  # pragma: no cover - unreachable, satisfies the generator shape

    before = _count()
    app.dependency_overrides[db_session.get_session] = broken_session
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.get("/admin/keys", headers={"X-Admin-Key": "test-admin-secret"})
    finally:
        app.dependency_overrides.pop(db_session.get_session, None)

    assert r.status_code == 500
    assert _count() == before + 1, "the 500 was not counted"


def test_a_404_is_not_counted_as_a_failure():
    """Only unhandled exceptions. A missing route is a client mistake, and
    counting it would make the metric fire on ordinary scanning traffic."""
    from prometheus_client import REGISTRY

    def _count() -> float:
        return REGISTRY.get_sample_value("gateway_unhandled_exceptions_total") or 0.0

    before = _count()
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/no-such-route").status_code == 404

    assert _count() == before
