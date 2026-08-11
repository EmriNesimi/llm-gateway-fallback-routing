import pytest
from fastapi.testclient import TestClient

from app.admin import auth as admin_auth
from app.db.audit import record_audit_log
from app.main import app


@pytest.fixture(autouse=True)
def _admin_key(monkeypatch):
    monkeypatch.setattr(admin_auth.settings, "admin_api_key", "test-admin-secret")


@pytest.mark.asyncio
async def test_audit_log_endpoint_returns_recorded_requests(isolated_db):
    await record_audit_log("key-a", requested_model="default", outcome="success", provider="openai")
    await record_audit_log("key-b", requested_model="default", outcome="error")

    with TestClient(app) as client:
        r = client.get("/admin/audit-log", headers={"X-Admin-Key": "test-admin-secret"})

    assert r.status_code == 200
    entries = r.json()
    assert len(entries) == 2
    # Newest first.
    assert entries[0]["outcome"] == "error"
    assert entries[1]["outcome"] == "success"


@pytest.mark.asyncio
async def test_audit_log_endpoint_filters_by_team(isolated_db):
    from sqlalchemy import select

    from app.db.models import ApiKeyRecord
    from app.security.api_keys import hash_key

    async with isolated_db() as session:
        session.add(ApiKeyRecord(key_hash=hash_key("team-a-key"), team="team-a"))
        session.add(ApiKeyRecord(key_hash=hash_key("team-b-key"), team="team-b"))
        await session.commit()

    await record_audit_log("team-a-key", requested_model="default", outcome="success")
    await record_audit_log("team-b-key", requested_model="default", outcome="success")

    headers = {"X-Admin-Key": "test-admin-secret"}
    with TestClient(app) as client:
        r = client.get("/admin/audit-log", params={"team": "team-a"}, headers=headers)

    entries = r.json()
    assert len(entries) == 1
    assert entries[0]["team"] == "team-a"

    # Sanity check the fixture actually inserted both teams' records.
    async with isolated_db() as session:
        count = len((await session.execute(select(ApiKeyRecord))).scalars().all())
    assert count == 2


def test_audit_log_endpoint_requires_admin_key(isolated_db):
    with TestClient(app) as client:
        r = client.get("/admin/audit-log")
    assert r.status_code == 401


def test_audit_log_endpoint_empty_when_no_requests(isolated_db):
    with TestClient(app) as client:
        r = client.get("/admin/audit-log", headers={"X-Admin-Key": "test-admin-secret"})
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_audit_log_endpoint_offset_paginates_past_limit(isolated_db):
    for i in range(3):
        await record_audit_log(f"key-{i}", requested_model="default", outcome="success")

    headers = {"X-Admin-Key": "test-admin-secret"}
    with TestClient(app) as client:
        page1 = client.get(
            "/admin/audit-log", params={"limit": 2, "offset": 0}, headers=headers
        ).json()
        page2 = client.get(
            "/admin/audit-log", params={"limit": 2, "offset": 2}, headers=headers
        ).json()

    assert len(page1) == 2
    assert len(page2) == 1
    assert {e["id"] for e in page1}.isdisjoint({e["id"] for e in page2})
