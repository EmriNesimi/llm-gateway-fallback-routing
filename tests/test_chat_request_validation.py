import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.budget.dependency import enforce_budget
from app.main import app
from app.routing.fallback import AllProvidersFailedError
from app.schemas import MAX_CONTENT_CHARS, MAX_TOTAL_CONTENT_CHARS


class _AlwaysFailsRouter:
    async def chat(self, messages, request_id="", params=None, skip_providers=None):
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


# --------------------------------------------------------------------------
# The total-content bound
# --------------------------------------------------------------------------
#
# MAX_CONTENT_CHARS caps a single message; MAX_TOTAL_CONTENT_CHARS caps the
# request. Only the second one actually bounds cost — the per-message limit
# multiplied by MAX_MESSAGES is 2.5M characters, which is far more than the
# entire provider budget would pay for. Until now nothing exercised the raise,
# so the bound that does the work was the untested one.


def _two_messages_over_the_total():
    """Each message is legal on its own; together they are not."""
    half = "x" * 30_000
    return [
        {"role": "user", "content": half},
        {"role": "user", "content": half},
    ]


@pytest.mark.parametrize("endpoint", ["/v1/chat", "/v1/chat/completions"])
def test_messages_that_are_individually_legal_can_still_be_too_much(_client, endpoint):
    messages = _two_messages_over_the_total()
    assert all(len(m["content"]) < MAX_CONTENT_CHARS for m in messages)
    assert sum(len(m["content"]) for m in messages) > MAX_TOTAL_CONTENT_CHARS

    r = _client.post(endpoint, json={"model": "default", "messages": messages})

    assert r.status_code == 422
    assert "total message content" in r.text


@pytest.mark.parametrize("endpoint", ["/v1/chat", "/v1/chat/completions"])
def test_a_request_exactly_at_the_total_limit_is_accepted(_client, endpoint):
    """The boundary itself must pass, or the limit is off by one and the
    error message lies about where the line is."""
    messages = [{"role": "user", "content": "x" * MAX_TOTAL_CONTENT_CHARS}]

    r = _client.post(endpoint, json={"model": "default", "messages": messages})

    # Past validation: the stub router refuses, which is a 502 rather than 422.
    assert r.status_code != 422
