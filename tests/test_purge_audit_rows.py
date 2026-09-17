"""The purge tool has to be safe by default and leave a copy of what it took.

Deleting audit rows is a deliberate act here. These pin the two properties
that make the tool acceptable at all: nothing changes without --apply, and
nothing is removed without an export that round-trips first.
"""

import json

import pytest

from app.db.audit import record_audit_log
from scripts.purge_audit_rows import purge


async def _row(request_id: str, cost: float = 0.04) -> None:
    await record_audit_log(
        "key", requested_model="smart", outcome="aborted", provider="anthropic",
        input_tokens=1, output_tokens=3000, cost_usd=cost, latency_ms=1.0,
        request_id=request_id,
    )


async def _count(isolated_db, request_id: str) -> int:
    from sqlalchemy import func, select

    from app.db.models import AuditLogEntry

    async with isolated_db() as s:
        return (await s.execute(
            select(func.count(AuditLogEntry.id)).where(AuditLogEntry.request_id == request_id)
        )).scalar_one()


@pytest.mark.asyncio
async def test_dry_run_changes_nothing(isolated_db, tmp_path, capsys):
    await _row("fake-id")
    await _row("fake-id")

    assert await purge("fake-id", apply=False, export_dir=tmp_path) == 0

    assert await _count(isolated_db, "fake-id") == 2
    assert not list(tmp_path.iterdir()), "a dry run wrote an export"
    assert "dry run" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_apply_exports_then_removes_only_the_matching_rows(isolated_db, tmp_path):
    await _row("fake-id", cost=0.04)
    await _row("fake-id", cost=0.04)
    await _row("7c94276dc21a4e43b12fedf30022e2d0", cost=0.000565)  # a real one

    assert await purge("fake-id", apply=True, export_dir=tmp_path) == 0

    assert await _count(isolated_db, "fake-id") == 0
    assert await _count(isolated_db, "7c94276dc21a4e43b12fedf30022e2d0") == 1, (
        "a real request's row was swept up with the fake ones"
    )

    exports = list(tmp_path.glob("audit-purge-*-fake-id.json"))
    assert len(exports) == 1
    saved = json.loads(exports[0].read_text())
    assert len(saved) == 2
    assert {r["request_id"] for r in saved} == {"fake-id"}
    assert sum(r["cost_usd"] for r in saved) == pytest.approx(0.08)


@pytest.mark.asyncio
async def test_exact_match_only(isolated_db, tmp_path):
    """A prefix or substring match is how a real request gets deleted."""
    await _row("client-hung-up")
    await _row("client-hung-up-2")

    await purge("client-hung-up", apply=True, export_dir=tmp_path)

    assert await _count(isolated_db, "client-hung-up") == 0
    assert await _count(isolated_db, "client-hung-up-2") == 1


@pytest.mark.asyncio
async def test_nothing_to_do_is_not_an_error(isolated_db, tmp_path, capsys):
    assert await purge("absent", apply=True, export_dir=tmp_path) == 0
    assert "nothing to do" in capsys.readouterr().out
    assert not list(tmp_path.iterdir())
