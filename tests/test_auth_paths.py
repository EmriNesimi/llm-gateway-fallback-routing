"""Every branch of client API key auth.

This was the least-covered module in the codebase at 63%, which is a bad place
to have gaps: the untested lines were the database-key lookup, the fail-closed
503, and the rejection paths — that is, most of the reasons this function
exists. A regression in any of them either locks out legitimate callers or,
worse, doesn't.
"""

import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.db.models import ApiKeyRecord
from app.security.api_keys import hash_key
from app.security.auth import _extract_key, require_api_key

# --------------------------------------------------------------------------
# Pulling the key off the request
# --------------------------------------------------------------------------


def test_x_api_key_header_is_used_when_present():
    assert _extract_key(None, "from-header") == "from-header"


def test_x_api_key_wins_over_authorization():
    """Not arbitrary — checking the more specific header first means a client
    that sets both gets deterministic behavior rather than order-of-evaluation
    luck."""
    assert _extract_key("Bearer from-bearer", "from-header") == "from-header"


@pytest.mark.parametrize(
    "header,expected",
    [
        ("Bearer abc123", "abc123"),
        ("bearer abc123", "abc123"),  # the scheme is case-insensitive per RFC 7235
        ("BEARER abc123", "abc123"),
        ("Bearer   abc123  ", "abc123"),  # surrounding whitespace is stripped
    ],
)
def test_bearer_tokens_are_parsed(header, expected):
    assert _extract_key(header, None) == expected


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "abc123",  # no scheme at all
        "Basic abc123",  # wrong scheme
        "Bearertoken",  # no separating space
    ],
)
def test_non_bearer_authorization_yields_nothing(header):
    assert _extract_key(header, None) is None


def test_whitespace_is_stripped_from_the_header_form():
    assert _extract_key(None, "  padded-key  ") == "padded-key"


# --------------------------------------------------------------------------
# require_api_key
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_key_is_rejected_with_401(monkeypatch, isolated_db):
    monkeypatch.setattr(settings, "gateway_api_keys", "env-key")

    async with isolated_db() as session:
        with pytest.raises(HTTPException) as exc_info:
            await require_api_key(authorization=None, x_api_key=None, session=session)

    assert exc_info.value.status_code == 401
    assert "missing" in str(exc_info.value.detail).lower()


@pytest.mark.asyncio
async def test_env_configured_key_is_accepted(monkeypatch, isolated_db):
    monkeypatch.setattr(settings, "gateway_api_keys", "env-key,other-key")

    async with isolated_db() as session:
        assert (
            await require_api_key(authorization=None, x_api_key="other-key", session=session)
            == "other-key"
        )


@pytest.mark.asyncio
async def test_wrong_key_is_rejected_with_401_when_keys_are_configured(
    monkeypatch, isolated_db
):
    monkeypatch.setattr(settings, "gateway_api_keys", "env-key")

    async with isolated_db() as session:
        with pytest.raises(HTTPException) as exc_info:
            await require_api_key(authorization=None, x_api_key="nope", session=session)

    assert exc_info.value.status_code == 401
    assert "invalid" in str(exc_info.value.detail).lower()


@pytest.mark.asyncio
async def test_admin_issued_key_in_the_database_is_accepted(monkeypatch, isolated_db):
    """The migration path: an operator starts with GATEWAY_API_KEYS and moves
    to admin-issued keys without a flag day, so both sources must work."""
    monkeypatch.setattr(settings, "gateway_api_keys", "env-key")

    async with isolated_db() as session:
        session.add(ApiKeyRecord(key_hash=hash_key("db-issued"), team="acme", revoked=False))
        await session.commit()

        assert (
            await require_api_key(authorization=None, x_api_key="db-issued", session=session)
            == "db-issued"
        )


@pytest.mark.asyncio
async def test_revoked_database_key_is_rejected(monkeypatch, isolated_db):
    """Revocation has to take effect immediately — a revoked key that still
    authenticates is the whole feature failing silently."""
    monkeypatch.setattr(settings, "gateway_api_keys", "env-key")

    async with isolated_db() as session:
        session.add(ApiKeyRecord(key_hash=hash_key("revoked"), team="acme", revoked=True))
        await session.commit()

        with pytest.raises(HTTPException) as exc_info:
            await require_api_key(authorization=None, x_api_key="revoked", session=session)

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_fails_closed_with_503_when_nothing_is_configured(monkeypatch, isolated_db):
    """No env keys and no matching database key means auth isn't set up. The
    endpoint refuses rather than serving openly, and a 503 says
    "misconfigured", which is the truth, where a 401 would say "your key is
    wrong"."""
    monkeypatch.setattr(settings, "gateway_api_keys", "")

    async with isolated_db() as session:
        with pytest.raises(HTTPException) as exc_info:
            await require_api_key(authorization=None, x_api_key="anything", session=session)

    assert exc_info.value.status_code == 503
    assert "not configured" in str(exc_info.value.detail).lower()


@pytest.mark.asyncio
async def test_database_key_works_even_with_no_env_keys_configured(monkeypatch, isolated_db):
    """The fail-closed 503 must not fire when the admin API is the only key
    source — otherwise migrating off GATEWAY_API_KEYS would break the
    gateway."""
    monkeypatch.setattr(settings, "gateway_api_keys", "")

    async with isolated_db() as session:
        session.add(ApiKeyRecord(key_hash=hash_key("only-db"), team="acme", revoked=False))
        await session.commit()

        assert (
            await require_api_key(authorization=None, x_api_key="only-db", session=session)
            == "only-db"
        )


@pytest.mark.asyncio
async def test_bearer_form_authenticates_the_same_as_the_header_form(monkeypatch, isolated_db):
    monkeypatch.setattr(settings, "gateway_api_keys", "env-key")

    async with isolated_db() as session:
        assert (
            await require_api_key(
                authorization="Bearer env-key", x_api_key=None, session=session
            )
            == "env-key"
        )
