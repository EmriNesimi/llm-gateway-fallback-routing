# 013 — The spend ledger is persisted, and lives only in Redis

## Context

Decision 011 made the lifetime provider ceiling a number in Redis, incremented
before each call and reconciled after. Decision 012 made a request that cannot
be costed a refusal rather than an unmetered call.

Both are about arithmetic. Neither said anything about where the number is
kept, and the answer was: in the memory of a container started with no volume
and no persistence.

`docker compose down`, a host reboot, an OOM kill, or `docker compose up
--build` after editing anything all discarded it. Spend went back to `$0.00`
and the full ceiling became available again.

Nothing in the application was wrong, which is why this survived several
rounds of review of the budget code itself. A `$4` **lifetime** cap was in
practice a `$4` cap **per uptime window**, and the number of uptime windows
during development is large.

## Decision

Run Redis with `--appendonly yes` and mount a named volume at `/data`.

**Append-only rather than RDB snapshots.** RDB writes a point-in-time dump
every N seconds and loses everything after the last one. The window that would
be lost is precisely the window that matters: a runaway loop spends its money
in the minutes before someone notices and stops the stack.

**Both halves are required.** `appendonly yes` without a volume writes the file
into the container's own layer, which is discarded with the container. It looks
configured and behaves exactly like having nothing, so
`tests/test_ledger_durability.py` asserts the flag *and* the mount.

## Consequences

- The ceiling now means what its name says. Spend accumulates across restarts.
- There is still exactly one copy of the number. Anyone who can reach Redis can
  `FLUSHALL` it, and the volume can be deleted with `docker compose down -v`.
  The password and the loopback bind are the whole defence; this decision makes
  the ledger durable, not tamper-proof.
- Local development now carries spend forward between runs, which is the
  intended behaviour and will eventually exhaust a development ceiling. Raising
  `PROVIDER_LIFETIME_BUDGET_USD` or clearing the key is a deliberate act, which
  is the point — it used to happen by accident on every restart.
- The append-only file grows. At this project's request volume that is
  irrelevant; a deployment with real traffic would want Redis's own AOF
  rewrite settings considered.
