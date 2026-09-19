# Contributing

Short, because most of what a contributor needs is already written down
somewhere with a guard on it. This says where.

## Before you start

```
make install     # runtime + dev deps into the active venv
make help        # every target, one line each
make check       # what CI runs without Docker: lint, types, audit, migrations, tests
```

`make check` has to pass before a push. CI runs the same steps plus the
Docker-dependent ones (`make shellcheck` is the local version of one).

## Where the rules live

- **Why the code is shaped the way it is:** [`docs/decisions/`](docs/decisions/).
  One file per non-obvious call, each with the alternative that lost. Read
  the index before changing anything in the money path; several of those
  decisions exist because the obvious fix was tried and was wrong.
- **What a change must not break:** the tests, and specifically the guards —
  tests that check the docs against the code, the dashboard against the
  metrics, the README's numbers against reality. If one fails on your change,
  the guard is usually right. Update the thing it is checking, not the guard.
- **The spend ceiling:** [`SECURITY.md`](SECURITY.md) says what it protects
  and what it does not. Anything under `app/budget/`, or in `_reserve_chain`
  / `_settle_chain`, is the part with the worst track record for subtle
  breakage — three bugs there shipped with passing tests. Run `make reconcile`
  after, and say what it reported in the PR.
- **`/v1/` is a contract:** [`docs/api-versioning.md`](docs/api-versioning.md).
  `/admin/*` and the operational endpoints are not.

## Commits

One change per commit, pushed as it lands. The message says why — what was
wrong, what the alternative was, what was verified — not what the diff
already shows. Look at `git log` for the register.

## Money

Tests use fakeredis and an in-memory database, both autouse, and spend
nothing. Anything that hits a real provider is a deliberate act by the
person running it: `scripts/demo.sh` costs two requests, the k6 load test
more. The lifetime ceiling is the last line, not the first.
