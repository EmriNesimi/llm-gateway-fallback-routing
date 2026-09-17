"""Remove audit rows that match a request_id, keeping a copy of every one.

Deleting audit rows is a deliberate act by this project's own rules — the
audit log is the record of what was spent and by whom — so this is built to
be hard to do carelessly and impossible to do invisibly:

- **Dry run by default.** It prints what would go and exits. `--apply` is
  required to change anything.
- **Every removed row is exported first**, as JSON, to a file whose path is
  printed. The deletion does not proceed unless the export succeeded. Nothing
  is ever removed without a copy existing somewhere first.
- **Matches on request_id exactly.** Real requests carry a 32-character hex
  correlation ID from the middleware; a test that hardcodes a readable one
  leaves rows that are unmistakable. This is not a general-purpose retention
  tool and takes no date range on purpose — a range is how a real request
  gets swept up with the fake ones.

Why it exists at all: the suite was writing to the production audit log for
weeks (fixed in 0.5.0), and left 150 rows of stub-router traffic carrying the
literal request_id "client-hung-up". Those rows make `make reconcile` report a
$6 gap that is not real, and reconcile is the check that decides whether the
spend ceiling can be trusted. A diagnostic that always fires is one nobody
runs.
"""

import argparse
import asyncio
import datetime
import json
import pathlib
import sys

from sqlalchemy import delete, select

from app.db import session as db_session
from app.db.models import AuditLogEntry


def _serialise(row: AuditLogEntry) -> dict:
    return {
        c.name: (v.isoformat() if isinstance(v := getattr(row, c.name), datetime.datetime) else v)
        for c in AuditLogEntry.__table__.columns
    }


async def purge(request_id: str, apply: bool, export_dir: pathlib.Path) -> int:
    async with db_session.async_session() as session:
        rows = list(
            (await session.execute(
                select(AuditLogEntry).where(AuditLogEntry.request_id == request_id)
            )).scalars()
        )
        if not rows:
            print(f"no audit rows carry request_id={request_id!r}; nothing to do")
            return 0

        total = sum(r.cost_usd for r in rows)
        by_provider: dict[str, int] = {}
        for r in rows:
            by_provider[r.provider] = by_provider.get(r.provider, 0) + 1
        print(
            f"{len(rows)} rows carry request_id={request_id!r}, totalling ${total:.6f}"
            f" across {by_provider}"
        )

        if not apply:
            print("dry run — pass --apply to remove them (a JSON copy is written first)")
            return 0

        export_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
        export = export_dir / f"audit-purge-{stamp}-{request_id}.json"
        export.write_text(json.dumps([_serialise(r) for r in rows], indent=2))
        # Read it back before deleting anything. A write that raised would
        # have stopped us already; this guards against the quieter case of a
        # file that exists and is not the rows. A real raise, not an assert —
        # `python -O` strips asserts, and this is the one check that must run.
        if len(json.loads(export.read_text())) != len(rows):
            raise RuntimeError(f"export at {export} did not round-trip; nothing deleted")
        print(f"exported to {export}")

        await session.execute(delete(AuditLogEntry).where(AuditLogEntry.request_id == request_id))
        await session.commit()
        print(f"removed {len(rows)} rows")
        return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("request_id", help="exact request_id the rows to remove carry")
    parser.add_argument("--apply", action="store_true", help="actually delete (default: dry run)")
    parser.add_argument(
        "--export-dir",
        type=pathlib.Path,
        default=pathlib.Path("audit-purges"),
        help="where the JSON copy of removed rows is written (default: ./audit-purges)",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(purge(args.request_id, args.apply, args.export_dir)))


if __name__ == "__main__":
    main()
