"""Compare the spend ledger against the audit log, per provider.

The two are written independently — the ledger by reserve/settle in Redis, the
audit log by record_audit_log in the database — so they can disagree, and the
disagreement is the diagnostic. The runbook already tells the reader to
cross-check one against the other; this is the cross-check.

What a gap means:

    ledger > audit   Reservations were claimed and never released. This is
                     the BudgetReservationLeaked shape. Headroom is gone for
                     requests that never ran, and the ceiling will refuse
                     earlier than it should.

    ledger < audit   Spend reached the audit log and not the ledger, or the
                     ledger was reset. The ceiling is now LARGER than the
                     money spent says it should be — which is the direction
                     this control must never fail in.

    equal            Both records agree. This is the only state in which the
                     ceiling can be trusted to mean what it says.

Neither store is the provider's invoice. When they disagree, the provider's
own billing page is the arbiter; this tells you *that* you need to look, and
which direction the error runs.

Exit codes, because the runbook has this on a cron and the two cases call for
different responses:

    0   the two records agree
    1   they disagree — investigate, per the runbook
    2   one of them could not be read at all

Read-only: it changes nothing in either store.
"""

import argparse
import asyncio
import re
import sys

from sqlalchemy import func, select

from app.budget.dependency import provider_budget
from app.core.config import settings
from app.db import session as db_session
from app.db.models import AuditLogEntry
from app.routing.model_map import billable_providers

# Float arithmetic on many small INCRBYFLOATs drifts in the last few digits,
# and Redis stores the running total as a decimal string it re-parses on every
# increment. Observed: 46 real requests left the two stores $0.000002 apart.
# Anything this small is rounding, not a lost request — the cheapest priced
# request in app/budget/pricing.py costs an order of magnitude more.
_DEFAULT_TOLERANCE_USD = 1e-5


async def _audit_totals() -> dict[str, tuple[int, float]]:
    # Looked up through the module rather than imported by name, so the test
    # suite's isolated_db fixture can swap it. Importing the name binds it at
    # import time and quietly reads the real database instead — conftest
    # documents the same trap for app/db/audit.py.
    async with db_session.async_session() as session:
        rows = await session.execute(
            select(
                AuditLogEntry.provider,
                func.count(AuditLogEntry.id),
                func.coalesce(func.sum(AuditLogEntry.cost_usd), 0.0),
            )
            .where(AuditLogEntry.provider != "")
            .group_by(AuditLogEntry.provider),
        )
        return {provider: (count, float(total)) for provider, count, total in rows}


_UNREACHABLE = 2


def _redacted(url: str) -> str:
    """The URL with any password blanked, so this can be printed."""
    return re.sub(r"(://[^:/@]*:)[^@]*(@)", r"\1***\2", url)


async def reconcile(tolerance_usd: float) -> int:
    # The gateway creates its SQLite tables at startup; a script has no
    # startup. Without this the first run against a fresh database — which is
    # every CI run — dies on `no such table: audit_log`. It did, on four
    # pushes, while passing locally against a database that already had them.
    # For Postgres init_db is a no-op and migrations are expected, as in the app.
    await db_session.init_db()
    providers = billable_providers()
    # A store that cannot be read is not a store that agrees. Reported as its
    # own exit code rather than a traceback: the runbook puts this on a cron,
    # and "could not check" needs a different response from "found a gap".
    try:
        ledger = await provider_budget.snapshot(providers)
    except Exception as exc:  # noqa: BLE001 - reported, not handled
        print(
            f"cannot read the ledger at {_redacted(settings.redis_url)}: {exc}",
            file=sys.stderr,
        )
        return _UNREACHABLE
    try:
        audit = await _audit_totals()
    except Exception as exc:  # noqa: BLE001 - reported, not handled
        print(f"cannot read the audit log: {exc}", file=sys.stderr)
        return _UNREACHABLE

    print(
        f"{'provider':<12} {'ledger':>12} {'audit log':>12} {'requests':>9}"
        f"  {'gap':>12}  verdict",
    )
    worst = 0.0
    for provider in providers:
        led = ledger.get(provider, 0.0)
        count, aud = audit.get(provider, (0, 0.0))
        gap = led - aud
        worst = max(worst, abs(gap))
        if abs(gap) <= tolerance_usd:
            verdict = "ok"
        elif gap > 0:
            verdict = "LEDGER HIGH — stranded reservations, ceiling refuses early"
        else:
            verdict = "LEDGER LOW — ceiling larger than spend says; check the bill"
        print(f"{provider:<12} ${led:>11.6f} ${aud:>11.6f} {count:>9}  ${gap:>+11.6f}  {verdict}")

    if worst > tolerance_usd:
        print(f"\ngap of ${worst:.6f} exceeds tolerance ${tolerance_usd:.6f}", file=sys.stderr)
        return 1
    print("\nledger and audit log agree")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--tolerance",
        type=float,
        default=_DEFAULT_TOLERANCE_USD,
        help=f"largest gap in USD still reported as ok (default {_DEFAULT_TOLERANCE_USD})",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(reconcile(args.tolerance)))


if __name__ == "__main__":
    main()
