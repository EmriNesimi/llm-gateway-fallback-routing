# 7. Redis and Postgres connections have explicit, bounded timeouts

## Context

Neither the Redis client nor the Postgres connection had an explicit
timeout configured. Both libraries' defaults are either unbounded or too
long for a request path: `redis-py`'s `socket_connect_timeout` and
`socket_timeout` default to `None` (block on the OS-level TCP timeout to
connect — often 30s+ — and indefinitely once connected, for a command
whose response never arrives), and `asyncpg`'s connect timeout defaults to
60s with `command_timeout` unset entirely. A Redis or Postgres instance
that's merely *unresponsive* — not cleanly refusing connections, just
hanging (a network partition after connect, a stuck lock, an overloaded
host) — would block `/readyz` and every request touching that backend for
however long the OS/library default happens to be, rather than failing in
a bounded, predictable time.

## Decision

`REDIS_CONNECT_TIMEOUT_SECONDS`/`REDIS_SOCKET_TIMEOUT_SECONDS` (both 5s
default) are passed to the Redis client. `DATABASE_CONNECT_TIMEOUT_SECONDS`
(5s)/`DATABASE_COMMAND_TIMEOUT_SECONDS` (10s) are passed to asyncpg via
SQLAlchemy's `connect_args`, only when `DATABASE_URL` is Postgres — SQLite
is a local file with no network involved, and aiosqlite doesn't accept
these kwargs at all.

## Why

A hung backend and a down backend should look the same to everything that
depends on them: a bounded failure, fast enough that a caller isn't left
waiting, and fast enough that `/readyz` still functions as an actual
*readiness* signal rather than itself becoming unresponsive. Verified live
against a non-routable test address (Redis) and a real `pg_sleep()` query
(Postgres) — both now fail at exactly the configured timeout instead of
hanging.

The 5s/10s defaults are a starting point, not a carefully load-tested
number — they're short enough to keep a hung backend from meaningfully
degrading the gateway's own responsiveness, and long enough not to spuriously
trip under normal latency. Both are configurable per-deployment via env
vars if a specific environment needs different values.

## Consequences

- `/readyz`, the rate limiter, the budget tracker, and the admin/audit-log
  DB queries all now fail within a bounded time against a hung backend,
  instead of potentially hanging as long as the OS/library default.
- Postgres's `command_timeout` is per-command, not per-transaction — a
  transaction doing multiple queries could still take longer than
  `DATABASE_COMMAND_TIMEOUT_SECONDS` in total. This project's queries are
  all single-statement, so that distinction doesn't currently matter, but
  it would for anything doing multi-statement transactions.
- `migrations/env.py` builds its own engine via `async_engine_from_config`
  and does not currently pick up these timeouts — acceptable for now since
  migrations are a one-time startup step (with its own retry loop in
  `docker-entrypoint.sh`), not a per-request path, but worth revisiting if
  that ever changes.
