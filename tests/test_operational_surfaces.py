"""The endpoints that aren't the client contract, and what protects them.

`/metrics` and `/admin/*` sit outside `/v1`, so they're easy to forget when
thinking about access control — which is how one ended up publishing spend
figures to anyone who asked and the other ended up as the only surface with no
rate limit at all.
"""

import pytest
from fastapi.testclient import TestClient

from app.admin import auth as admin_auth
from app.core.config import settings
from app.main import app
from app.providers.anthropic_provider import _split_system
from app.providers.base import ChatMessage

ADMIN = {"X-Admin-Key": "test-admin-secret"}


@pytest.fixture
def client(monkeypatch, isolated_db):
    monkeypatch.setattr(admin_auth.settings, "admin_api_key", "test-admin-secret")
    with TestClient(app) as c:
        yield c


# --------------------------------------------------------------------------
# /metrics
# --------------------------------------------------------------------------


def test_metrics_is_open_when_no_token_is_configured(client, monkeypatch):
    """The default suits a loopback-bound dev stack; docker-compose.yml binds
    every port to 127.0.0.1 precisely so this default is safe there."""
    monkeypatch.setattr(settings, "metrics_token", None)

    assert client.get("/metrics").status_code == 200


def test_metrics_requires_the_token_once_one_is_set(client, monkeypatch):
    monkeypatch.setattr(settings, "metrics_token", "scrape-me")

    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers={"X-Metrics-Token": "wrong"}).status_code == 401
    assert (
        client.get("/metrics", headers={"X-Metrics-Token": "scrape-me"}).status_code == 200
    )


def test_prometheus_can_authenticate_with_a_bearer_token(client, monkeypatch):
    """The scraper this gate exists for. Prometheus scrape configs can send
    Authorization: Bearer natively and have no generic custom-header field, so
    a gate that only read X-Metrics-Token would be one the intended client
    physically could not satisfy — it would just show the target as down."""
    monkeypatch.setattr(settings, "metrics_token", "scrape-me")

    ok = client.get("/metrics", headers={"Authorization": "Bearer scrape-me"})
    bad = client.get("/metrics", headers={"Authorization": "Bearer wrong"})

    assert ok.status_code == 200
    assert bad.status_code == 401


def test_a_rejected_scrape_leaks_no_spend_figures(client, monkeypatch):
    """The reason to gate it at all: these metrics say exactly how much has
    been spent and on what."""
    monkeypatch.setattr(settings, "metrics_token", "scrape-me")

    r = client.get("/metrics")

    assert "gateway_cost_usd" not in r.text


def test_the_scrape_token_is_not_a_gateway_key(client, monkeypatch):
    """A scraper shouldn't hold a credential that can spend money, so a valid
    client key must not open /metrics."""
    monkeypatch.setattr(settings, "metrics_token", "scrape-me")
    monkeypatch.setattr(settings, "gateway_api_keys", "a-real-client-key")

    r = client.get("/metrics", headers={"X-Metrics-Token": "a-real-client-key"})

    assert r.status_code == 401


# --------------------------------------------------------------------------
# /admin
# --------------------------------------------------------------------------


def test_admin_api_is_rate_limited(client, monkeypatch):
    """It mints and revokes client keys, and was the one surface with no limit
    — X-Admin-Key was guessable at unlimited rate."""
    monkeypatch.setattr(settings, "rate_limit_capacity", 3)

    statuses = [client.get("/admin/keys", headers=ADMIN).status_code for _ in range(25)]

    assert 429 in statuses, f"admin API never rate limited: {set(statuses)}"


def test_a_wrong_admin_key_is_also_rate_limited(client, monkeypatch):
    """Guesses have to consume the bucket too. A limiter that only counts
    successfully authenticated calls is no defence against brute force."""
    monkeypatch.setattr(settings, "rate_limit_capacity", 3)

    statuses = [
        client.get("/admin/keys", headers={"X-Admin-Key": f"guess-{i}"}).status_code
        for i in range(25)
    ]

    assert 429 in statuses, f"guesses were never limited: {set(statuses)}"


# --------------------------------------------------------------------------
# System prompts reaching Anthropic
# --------------------------------------------------------------------------


def test_system_messages_are_hoisted_out_of_the_conversation():
    """Anthropic takes a system prompt as a top-level parameter and rejects it
    inside messages[] with a 400 — which, being non-retryable, made the router
    fall past Anthropic entirely. Any request with a system prompt silently
    never used it."""
    system, conversation = _split_system(
        [
            ChatMessage(role="system", content="be terse"),
            ChatMessage(role="user", content="hello"),
        ]
    )

    assert system == "be terse"
    assert conversation == [{"role": "user", "content": "hello"}]


def test_multiple_system_messages_are_joined():
    system, conversation = _split_system(
        [
            ChatMessage(role="system", content="be terse"),
            ChatMessage(role="system", content="be kind"),
            ChatMessage(role="user", content="hi"),
        ]
    )

    assert system == "be terse\n\nbe kind"
    assert len(conversation) == 1


def test_no_system_message_yields_an_empty_string():
    """Which the provider then omits entirely — sending system="" is not the
    same as not sending it."""
    system, conversation = _split_system([ChatMessage(role="user", content="hi")])

    assert system == ""
    assert conversation == [{"role": "user", "content": "hi"}]


@pytest.mark.asyncio
async def test_the_provider_sends_system_at_the_top_level(monkeypatch):
    from app.providers.anthropic_provider import AnthropicProvider

    provider = AnthropicProvider(api_key="sk-ant-test", timeout_seconds=5)
    seen: dict = {}

    async def create(**kwargs):
        seen.update(kwargs)
        raise RuntimeError("stop here — the call shape is what matters")

    monkeypatch.setattr(provider._client.messages, "create", create)

    with pytest.raises(RuntimeError):
        await provider.chat(
            "claude-opus-5",
            [
                ChatMessage(role="system", content="be terse"),
                ChatMessage(role="user", content="hi"),
            ],
        )

    assert seen["system"] == "be terse"
    assert all(m["role"] != "system" for m in seen["messages"])
