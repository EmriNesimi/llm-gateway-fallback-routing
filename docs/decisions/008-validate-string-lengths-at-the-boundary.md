# 8. Client-controlled strings are length-capped at the API boundary

## Context

Three client-controlled values — `X-Request-ID`, `team` (admin API), and
`model` (`/v1/chat`) — flow into fixed-width DB columns
(`AuditLogEntry.request_id`/`team`/`requested_model`, `ApiKeyRecord.team`,
all `String(64)` or `String(255)`) with no length validation at the API
boundary. None of the three was caught by local testing, because **SQLite
does not enforce `VARCHAR` length at all** — a 1000-character value inserts
into a `String(255)` column without complaint. The same insert against
Postgres raises `asyncpg.exceptions.StringDataRightTruncationError`.
Verified live: all three reproduced cleanly against real SQLite (silent
success) and real Postgres (hard failure) in the same test run.

This is a dev/prod parity gap in the literal sense — the failure is
invisible in the environment used for local development and CI's default
job, and only appears against the production-shaped database, which is
exactly why the project already runs a dedicated `postgres-migrations` CI
job (see the CI workflow) rather than trusting SQLite-only testing.

## Decision

Each of the three fields is capped at its DB column's width, at the
earliest point that value is accepted:

- `X-Request-ID` — capped in `RequestIDMiddleware` itself (not a pydantic
  model; a header value is checked directly and replaced with a fresh UUID
  if it's too long, rather than rejecting the request outright).
- `team` (`CreateKeyRequest`) — `Field(max_length=255)`.
- `model` (`ChatRequest`) — `Field(min_length=1, max_length=255)`.

## Why

Two options existed for handling an oversized value: reject it, or truncate
it. Rejection was chosen for `team`/`model` because both are meaningful
identifiers a caller supplies on purpose — silently truncating
`"my-really-long-team-name-that-got-cut-off-at-255-chars"` would corrupt
attribution without telling anyone. `X-Request-ID` is different: it's
diagnostic metadata a caller may not even be deliberately setting (a proxy
or library might inject one), so silently generating a fresh ID and moving
on is more useful than failing the whole request over a header value
nobody's likely to notice or care about the exact content of.

For `team`/`model`, this closes the same class of gap decision 004
(best-effort bookkeeping) already handles for the *consequence* of a DB
failure — but rejecting the bad input up front is strictly better than
catching the failure after the fact, since it gives the caller an
actionable `422` instead of a `200`/`502` that silently drops their audit
row.

## Consequences

- All three fields behave identically against SQLite (dev) and Postgres
  (prod) — the class of bug that motivated this can't reoccur for these
  specific fields.
- Any *future* client-controlled field added to a request schema that maps
  to a fixed-width DB column needs the same treatment — this isn't
  automatically enforced (no test scans for it), so it's a pattern to
  apply deliberately, not a guarantee.
- This is exactly the kind of gap the `postgres-migrations` CI job and
  decision 007's "verify against real Postgres, not just SQLite" discipline
  exist to catch — all three were found by doing that, not by reading the
  code.
- **The test suite itself still only ever runs against SQLite.** Making
  `tests/conftest.py`'s `isolated_db` fixture run the same test suite
  against real Postgres in CI was attempted and reverted: sharing one
  asyncpg connection across a test (for savepoint-based transactional
  rollback, the standard isolation pattern) breaks under FastAPI
  `TestClient` + `BaseHTTPMiddleware`, which spawns the request into a
  separate task — asyncpg connections aren't safe for concurrent use
  across tasks, producing `RuntimeError`/`InterfaceError` failures unrelated
  to the code under test. A correct version would need per-request
  connection pooling still scoped to one outer transaction (e.g. a
  contextvar-scoped connection), which is a bigger, riskier change than
  fit this pass. Until that exists, this exact class of bug (SQLite
  accepts, Postgres rejects) can still only be caught by deliberate manual
  verification against real Postgres — not by CI automatically.
