# 2. SQLite by default, Postgres by opt-in

## Context

The gateway needs a database for two things that aren't on the hot path of
serving a chat request: the admin API's key records, and the audit log.
Something has to store them, and that something needs a `DATABASE_URL`.

## Decision

`DATABASE_URL` defaults to a local SQLite file (`sqlite+aiosqlite:///./gateway.db`).
Postgres is fully supported (`asyncpg` driver, real Alembic migrations run via
`docker-entrypoint.sh`) but is opt-in — you set `DATABASE_URL` to a Postgres
DSN yourself, and `docker-compose.yml` ships a Postgres service you can point
at, but nothing requires it.

## Why

Neither the admin API nor the audit log is in the request path that actually
serves `/v1/chat` — a client key check against Redis (via
`GATEWAY_API_KEYS`/rate limiting) is what gates traffic, not the DB. That
means the DB's job here is bookkeeping, not correctness-critical concurrent
writes from day one. SQLite is completely adequate for that at the scale a
single-instance demo or small deployment runs at, and it means `git clone &&
uvicorn app.main:app` works with zero external services — no `docker compose
up`, no waiting on a Postgres container, no connection string to get right
before you can even see the thing run.

The moment that stops being true — multiple gateway replicas writing audit
rows concurrently, or admin API traffic heavy enough that SQLite's
single-writer model becomes a bottleneck — Postgres is a `DATABASE_URL`
change away, with zero code changes, because the app talks to SQLAlchemy's
async engine, not to either database's specifics directly.

## Consequences

- `app/db/session.py`'s `init_db()` only runs `create_all` for SQLite; a
  Postgres `DATABASE_URL` skips it and expects `alembic upgrade head` to have
  already run (see `docker-entrypoint.sh`) — this avoids every replica racing
  to create tables concurrently, which SQLite's single-file nature makes a
  non-issue but Postgres would not.
- Local dev and the guided demo (`scripts/demo.sh`) need nothing beyond
  `pip install` to produce a working audit log.
- Anyone deploying multiple replicas needs to switch to Postgres explicitly —
  this is documented in the README, not silently assumed.
