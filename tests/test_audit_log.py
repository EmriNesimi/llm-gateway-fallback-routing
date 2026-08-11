import pytest
from sqlalchemy import select

import app.db.audit as audit_module
from app.db.audit import record_audit_log
from app.db.models import ApiKeyRecord, AuditLogEntry
from app.security.api_keys import hash_key


@pytest.mark.asyncio
async def test_record_audit_log_creates_row(isolated_db):
    await record_audit_log(
        "raw-key-123",
        requested_model="default",
        outcome="success",
        provider="openai",
        input_tokens=5,
        output_tokens=10,
        cost_usd=0.002,
        latency_ms=42.0,
    )

    async with isolated_db() as session:
        rows = (await session.execute(select(AuditLogEntry))).scalars().all()

    assert len(rows) == 1
    assert rows[0].provider == "openai"
    assert rows[0].outcome == "success"
    assert rows[0].team == "unlinked"  # no matching ApiKeyRecord


@pytest.mark.asyncio
async def test_record_audit_log_resolves_team_from_db_key(isolated_db):
    async with isolated_db() as session:
        session.add(ApiKeyRecord(key_hash=hash_key("raw-key-456"), team="acme"))
        await session.commit()

    await record_audit_log("raw-key-456", requested_model="default", outcome="success")

    async with isolated_db() as session:
        row = (await session.execute(select(AuditLogEntry))).scalar_one()

    assert row.team == "acme"


@pytest.mark.asyncio
async def test_record_audit_log_swallows_db_failures(isolated_db, monkeypatch):
    def broken_session():
        raise RuntimeError("db is down")

    monkeypatch.setattr(audit_module, "async_session", broken_session)

    # Must not raise — a DB outage here shouldn't take down whatever request
    # already succeeded and is just trying to log itself.
    await record_audit_log("raw-key-789", requested_model="default", outcome="success")
