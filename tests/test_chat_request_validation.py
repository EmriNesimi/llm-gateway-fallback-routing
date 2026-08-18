import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.budget.dependency import enforce_budget
from app.main import app
from app.routing.fallback import AllProvidersFailedError


class _AlwaysFailsRouter:
    async def chat(self, messages, request_id=""):
        raise AllProvidersFailedError("no real provider needed for this test")


@pytest.fixture
def _client(monkeypatch):
    app.dependency_overrides[enforce_budget] = lambda: "test-key"
    # A request that passes schema validation must not actually reach a real
    # provider in this test — only that it gets past validation to the
    # router at all (a 502 from there, not the network).
    monkeypatch.setattr(
        main_module, "build_router", lambda model: ("default", _AlwaysFailsRouter())
    )
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client
    finally:
        # dependency_overrides is a plain dict on the shared `app` object,
        # not scoped by monkeypatch — leaving this set leaked into later
        # tests in the same session and broke their real rate-limit/budget
        # header assertions.
        app.dependency_overrides.pop(enforce_budget, None)


def test_empty_messages_list_is_rejected(_client):
    r = _client.post("/v1/chat", json={"model": "default", "messages": []})
    assert r.status_code == 422


def test_invalid_role_is_rejected(_client):
    r = _client.post(
        "/v1/chat",
        json={"model": "default", "messages": [{"role": "bogus", "content": "hi"}]},
    )
    assert r.status_code == 422


def test_empty_message_content_is_rejected(_client):
    r = _client.post(
        "/v1/chat",
        json={"model": "default", "messages": [{"role": "user", "content": ""}]},
    )
    assert r.status_code == 422


def test_empty_model_is_rejected(_client):
    r = _client.post(
        "/v1/chat",
        json={"model": "", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 422


def test_oversized_model_is_rejected(_client):
    # Regression test: AuditLogEntry.requested_model is a String(255)
    # column. SQLite doesn't enforce VARCHAR length, so this would silently
    # succeed without the max_length validation, only failing on Postgres.
    r = _client.post(
        "/v1/chat",
        json={"model": "x" * 256, "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 422


def test_valid_roles_are_accepted_by_validation(_client):
    # Not asserting a 200 here — no real provider is reachable in this test
    # environment — just that these roles pass schema validation and the
    # request gets as far as the router (a 502/503 from there, not a 422).
    for role in ("system", "user", "assistant"):
        r = _client.post(
            "/v1/chat",
            json={"model": "default", "messages": [{"role": role, "content": "hi"}]},
        )
        assert r.status_code != 422
