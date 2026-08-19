"""Admin API edge cases: pagination clamping, 404s, and audit-log filters.

The happy paths were already covered in test_admin_api.py and
test_audit_log_endpoint.py. What wasn't: the not-found branches, the bounds
that stop a caller asking for a million rows, and the request_id filter — the
one you reach for when someone hands you an ID and asks what happened.
"""

import pytest
from fastapi.testclient import TestClient

from app.admin import auth as admin_auth
from app.db.models import ApiKeyRecord, AuditLogEntry
from app.main import app
from app.security.api_keys import hash_key

HEADERS = {"X-Admin-Key": "test-admin-secret"}


@pytest.fixture
def client(monkeypatch, isolated_db):
    monkeypatch.setattr(admin_auth.settings, "admin_api_key", "test-admin-secret")
    with TestClient(app) as c:
        yield c


# --------------------------------------------------------------------------
# Not found
# --------------------------------------------------------------------------


def test_fetching_a_missing_key_is_a_404(client):
    r = client.get("/admin/keys/99999", headers=HEADERS)

    assert r.status_code == 404
    assert r.json()["detail"] == "key not found"


def test_revoking_a_missing_key_is_a_404(client):
    """A revoke that silently succeeded against a nonexistent id would let an
    operator believe they'd cut off access when they hadn't."""
    r = client.delete("/admin/keys/99999", headers=HEADERS)

    assert r.status_code == 404


# --------------------------------------------------------------------------
# Pagination bounds
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_key_listing_is_paginated_and_clamped(client, isolated_db):
    async with isolated_db() as session:
        for i in range(5):
            session.add(ApiKeyRecord(key_hash=hash_key(f"k{i}"), team=f"team-{i}"))
        await session.commit()

    assert len(client.get("/admin/keys?limit=2", headers=HEADERS).json()) == 2
    # A limit of zero would return nothing and look like an empty database.
    assert len(client.get("/admin/keys?limit=0", headers=HEADERS).json()) == 1
    # Negative offsets are clamped rather than handed to the database.
    assert len(client.get("/admin/keys?offset=-5", headers=HEADERS).json()) == 5
    # And a caller can't ask for an unbounded page.
    assert len(client.get("/admin/keys?limit=99999", headers=HEADERS).json()) == 5


@pytest.mark.asyncio
async def test_offset_walks_past_the_first_page(client, isolated_db):
    async with isolated_db() as session:
        for i in range(4):
            session.add(ApiKeyRecord(key_hash=hash_key(f"p{i}"), team=f"team-{i}"))
        await session.commit()

    assert len(client.get("/admin/keys?limit=2&offset=2", headers=HEADERS).json()) == 2
    assert client.get("/admin/keys?offset=99", headers=HEADERS).json() == []


# --------------------------------------------------------------------------
# Audit log filters
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_log_filters_by_request_id(client, isolated_db):
    """The support path: someone reports a failure and quotes the X-Request-ID
    off their response, and this is what turns that into a row."""
    async with isolated_db() as session:
        for rid, team in [("req-a", "acme"), ("req-b", "globex")]:
            session.add(
                AuditLogEntry(
                    request_id=rid,
                    api_key_hash=hash_key("k"),
                    team=team,
                    requested_model="default",
                    provider="openai",
                    outcome="success",
                )
            )
        await session.commit()

    rows = client.get("/admin/audit-log?request_id=req-a", headers=HEADERS).json()

    assert len(rows) == 1
    assert rows[0]["team"] == "acme"


@pytest.mark.asyncio
async def test_audit_log_team_and_request_id_filters_combine(client, isolated_db):
    async with isolated_db() as session:
        session.add(
            AuditLogEntry(
                request_id="req-c",
                api_key_hash=hash_key("k"),
                team="acme",
                requested_model="default",
                provider="openai",
                outcome="success",
            )
        )
        await session.commit()

    match = client.get("/admin/audit-log?team=acme&request_id=req-c", headers=HEADERS)
    mismatch = client.get("/admin/audit-log?team=globex&request_id=req-c", headers=HEADERS)

    assert len(match.json()) == 1
    assert mismatch.json() == []
