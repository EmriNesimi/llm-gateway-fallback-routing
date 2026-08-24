"""Key issuance and revocation leave a trail.

Until now they didn't: a compromised admin key could mint itself client keys
and revoke yours, and the only evidence was the resulting rows in `api_keys` —
which say a key exists but not how it came to, which credential created it, or
whether a revocation actually happened.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.admin import auth as admin_auth
from app.db.models import AdminAuditEntry, ApiKeyRecord
from app.main import app
from app.security.api_keys import hash_key

ADMIN = {"X-Admin-Key": "test-admin-secret"}


@pytest.fixture
def client(monkeypatch, isolated_db):
    monkeypatch.setattr(admin_auth.settings, "admin_api_key", "test-admin-secret")
    with TestClient(app) as c:
        yield c


async def _events(session_factory, **filters):
    async with session_factory() as s:
        rows = (await s.execute(select(AdminAuditEntry))).scalars().all()
    return [r for r in rows if all(getattr(r, k) == v for k, v in filters.items())]


# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_issuing_a_key_writes_an_audit_row(client, isolated_db):
    r = client.post("/admin/keys", json={"team": "acme"}, headers=ADMIN)
    assert r.status_code == 200

    events = await _events(isolated_db, action="issued")

    assert len(events) == 1
    assert events[0].team == "acme"
    assert events[0].key_id > 0


@pytest.mark.asyncio
async def test_revoking_a_key_writes_an_audit_row(client, isolated_db):
    client.post("/admin/keys", json={"team": "acme"}, headers=ADMIN)
    key_id = client.get("/admin/keys", headers=ADMIN).json()[0]["id"]

    client.delete(f"/admin/keys/{key_id}", headers=ADMIN)

    events = await _events(isolated_db, action="revoked")
    assert len(events) == 1
    assert events[0].key_id == key_id
    assert events[0].team == "acme"


@pytest.mark.asyncio
async def test_the_row_records_which_admin_credential_acted(client, isolated_db):
    """Enough to tell one admin credential from another — and to spot activity
    from one that should have been rotated — without storing the secret."""
    client.post("/admin/keys", json={"team": "acme"}, headers=ADMIN)

    events = await _events(isolated_db, action="issued")

    assert events[0].admin_key_hash == hash_key("test-admin-secret")


@pytest.mark.asyncio
async def test_the_raw_admin_key_is_never_stored(client, isolated_db):
    client.post("/admin/keys", json={"team": "acme"}, headers=ADMIN)

    async with isolated_db() as s:
        rows = (await s.execute(select(AdminAuditEntry))).scalars().all()

    for row in rows:
        for value in vars(row).values():
            assert value != "test-admin-secret"


@pytest.mark.asyncio
async def test_the_row_carries_the_request_id(client, isolated_db):
    """So an admin action lines up on the same timeline as the traffic around
    it, using the same correlation ID as the request audit log."""
    r = client.post("/admin/keys", json={"team": "acme"}, headers=ADMIN)

    events = await _events(isolated_db, action="issued")

    assert events[0].request_id == r.headers["X-Request-ID"]


@pytest.mark.asyncio
async def test_a_failed_revocation_writes_nothing(client, isolated_db):
    """A 404 revoked nothing, so recording one would be a lie in the one place
    you'd go looking for the truth."""
    assert client.delete("/admin/keys/9999", headers=ADMIN).status_code == 404

    assert await _events(isolated_db) == []


@pytest.mark.asyncio
async def test_the_key_and_its_audit_row_land_together(client, isolated_db):
    """One transaction. A key that exists with no audit row is precisely the
    gap this closes, so the two must not be able to diverge."""
    client.post("/admin/keys", json={"team": "acme"}, headers=ADMIN)

    async with isolated_db() as s:
        keys = (await s.execute(select(ApiKeyRecord))).scalars().all()
        events = (await s.execute(select(AdminAuditEntry))).scalars().all()

    assert len(keys) == 1
    assert len(events) == 1
    assert events[0].key_id == keys[0].id


# --------------------------------------------------------------------------
# Reading it back
# --------------------------------------------------------------------------


def test_key_events_endpoint_lists_them(client):
    client.post("/admin/keys", json={"team": "acme"}, headers=ADMIN)
    key_id = client.get("/admin/keys", headers=ADMIN).json()[0]["id"]
    client.delete(f"/admin/keys/{key_id}", headers=ADMIN)

    rows = client.get("/admin/key-events", headers=ADMIN).json()

    assert {r["action"] for r in rows} == {"issued", "revoked"}


def test_key_events_can_be_filtered(client):
    client.post("/admin/keys", json={"team": "acme"}, headers=ADMIN)
    client.post("/admin/keys", json={"team": "globex"}, headers=ADMIN)

    issued = client.get("/admin/key-events?action=issued", headers=ADMIN).json()
    revoked = client.get("/admin/key-events?action=revoked", headers=ADMIN).json()

    assert len(issued) == 2
    assert revoked == []


def test_key_events_can_be_traced_for_one_key(client):
    client.post("/admin/keys", json={"team": "acme"}, headers=ADMIN)
    key_id = client.get("/admin/keys", headers=ADMIN).json()[0]["id"]
    client.delete(f"/admin/keys/{key_id}", headers=ADMIN)

    rows = client.get(f"/admin/key-events?key_id={key_id}", headers=ADMIN).json()

    assert len(rows) == 2
    assert all(r["key_id"] == key_id for r in rows)


def test_key_events_requires_the_admin_key(client):
    assert client.get("/admin/key-events").status_code == 401
    assert client.get("/admin/key-events", headers={"X-Admin-Key": "no"}).status_code == 401
