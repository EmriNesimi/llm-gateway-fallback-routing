"""`make reconcile` compares the ledger to the audit log. It has to see a gap.

The reconciliation that found $3.97 of phantom OpenAI spend and $5.92 of
fake anthropic spend was done by hand with ad-hoc queries. This is that
procedure as a script, so these pin that it reports each direction correctly
and exits non-zero on a gap — the property that lets it gate anything.
"""

import pytest

from app.budget import dependency as budget_dependency
from app.db.audit import record_audit_log
from scripts.reconcile import reconcile

PROVIDERS = ("anthropic", "openai")


async def _audit(provider: str, cost: float) -> None:
    await record_audit_log(
        "key", requested_model="smart", outcome="success", provider=provider,
        input_tokens=1, output_tokens=1, cost_usd=cost, latency_ms=1.0,
        request_id="r",
    )


@pytest.mark.asyncio
async def test_agreeing_stores_exit_zero(monkeypatch, capsys):
    monkeypatch.setattr("scripts.reconcile.billable_providers", lambda: PROVIDERS)
    await budget_dependency.provider_budget.record_unreserved("anthropic", 0.5)
    await _audit("anthropic", 0.5)

    assert await reconcile(tolerance_usd=1e-5) == 0
    assert "agree" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_stranded_reservation_reads_as_ledger_high(monkeypatch, capsys):
    """The BudgetReservationLeaked shape: claimed, never released."""
    monkeypatch.setattr("scripts.reconcile.billable_providers", lambda: PROVIDERS)
    await budget_dependency.provider_budget.record_unreserved("openai", 1.0)
    await _audit("openai", 0.25)

    assert await reconcile(tolerance_usd=1e-5) == 1
    out = capsys.readouterr().out
    assert "LEDGER HIGH" in out
    assert "+0.750000" in out


@pytest.mark.asyncio
async def test_reset_ledger_reads_as_ledger_low(monkeypatch, capsys):
    """The dangerous direction: the ceiling is now bigger than the spend."""
    monkeypatch.setattr("scripts.reconcile.billable_providers", lambda: PROVIDERS)
    await _audit("anthropic", 2.0)  # ledger holds nothing for it

    assert await reconcile(tolerance_usd=1e-5) == 1
    assert "LEDGER LOW" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_float_drift_is_not_a_gap(monkeypatch):
    """46 real requests left the stores $0.000002 apart. That is rounding on
    the Redis side, not a lost request, and must not page anyone.
    """
    monkeypatch.setattr("scripts.reconcile.billable_providers", lambda: PROVIDERS)
    await budget_dependency.provider_budget.record_unreserved("openai", 0.000273)
    await _audit("openai", 0.000270)

    assert await reconcile(tolerance_usd=1e-5) == 0


@pytest.mark.asyncio
async def test_an_unreachable_ledger_exits_two_not_one(monkeypatch, capsys):
    """The runbook crons this. "Could not check" and "found a gap" need
    different responses, so they get different exit codes — and a page is
    worth more than a traceback.
    """
    monkeypatch.setattr("scripts.reconcile.billable_providers", lambda: PROVIDERS)

    async def _unreachable(*a, **k):
        raise ConnectionError("connection refused")

    monkeypatch.setattr(budget_dependency.provider_budget, "snapshot", _unreachable)

    assert await reconcile(tolerance_usd=1e-5) == 2
    assert "cannot read the ledger" in capsys.readouterr().err


def test_the_printed_url_has_no_password():
    """It is printed on failure, which is exactly when someone pastes the
    output into an issue.
    """
    from scripts.reconcile import _redacted

    assert _redacted("redis://:hunter2@localhost:6379/0") == "redis://:***@localhost:6379/0"
    assert "hunter2" not in _redacted("redis://:hunter2@localhost:6379/0")
    # No credentials, nothing to redact.
    assert _redacted("redis://localhost:6379/0") == "redis://localhost:6379/0"
