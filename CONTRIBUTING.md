# Contributing

Short, because most of what a contributor needs is already written down
somewhere with a guard on it. This says where.

## Before you start

```
make install     # runtime + dev deps into the active venv
make help        # every target, one line each
make check       # what CI runs without Docker: lint, types, audit, migrations, tests
```

`make check` has to pass before a push. It deliberately excludes everything
needing Docker, so it works with nothing but the venv. Those steps have their
own targets, running the same images CI does:

```
make shellcheck      # the two shell scripts
make promtool        # the Prometheus config and alert rules
make compose-check   # docker-compose.yml parses
```

Also useful, and free: `make bench` measures what the gateway itself costs
per request with the provider stubbed, and the difference against a chain
that reserves nothing — which is what the reservation costs. Worth a
before-and-after if you have changed anything on the request path.

Run it more than once before believing a change moved it, and run it on an
idle machine. Five runs here spanned 3.8ms to 5.3ms on the same code, so
anything under about a millisecond is noise — and at load average 18 it
produced a negative reservation cost, which is impossible. It exits 3 rather
than reporting that, so a non-zero exit from `make bench` usually means
"close your other work and try again" rather than "something is broken".

Worth running if you touched what they cover. All three fail in ways that
otherwise only surface when someone brings the stack up.

If `make check` fails at `migrate-check` with "Target database is not up to
date", that is almost certainly not model drift: running the gateway locally
creates its SQLite tables directly, without going through Alembic, so the
file has no revision recorded. `alembic stamp head` fixes it.

## Where the rules live

- **Why the code is shaped the way it is:** [`docs/decisions/`](docs/decisions/).
  One file per non-obvious call, each with the alternative that lost. Read
  the index before changing anything in the money path; several of those
  decisions exist because the obvious fix was tried and was wrong.
- **What a change must not break:** the tests, and specifically the guards —
  tests that check the prose against the code rather than the code against
  itself. If one fails on your change, the guard is usually right. Update the
  thing it is checking, not the guard.

  | Guard | Fails when |
  |---|---|
  | `test_docs_accuracy.py` | a doc quotes a count, a `make` target or an endpoint that no longer matches |
  | `test_docs_reference_real_code.py` | prose names a module, function, constant or test file that was renamed, or links to a file or heading that does not exist |
  | `test_decision_records.py` | a decision is cited by a number that does not exist, or is missing from the index |
  | `test_toolchain_consistency.py` | the Python version disagrees across the seven places it is written |
  | `test_env_example_matches_defaults.py` | `.env.example` documents a default the code no longer has |
  | `test_stub_chains_match_reality.py` | a test's hardcoded copy of a chain or a price drifts from the real one |
  | `test_metrics_observability.py` | a metric is exported but never graphed or alerted on |
  | `test_scripts_do_not_touch_production.py` | a script that drives the request path writes to the real ledger or audit log |
  | `test_bench_overhead.py` | the chain `bench --compare` subtracts stops being free, which would silently invalidate the figure |

  They exist because each of those has gone stale at least once, and none of
  them fails loudly on its own — a wrong number in a README is invisible
  until someone acts on it.
- **The spend ceiling:** [`SECURITY.md`](SECURITY.md) says what it protects
  and what it does not. Anything under `app/budget/`, or in `_reserve_chain`
  / `_settle_chain`, is the part with the worst track record for subtle
  breakage — three bugs there shipped with passing tests. Run `make reconcile`
  after, and say what it reported in the PR.
- **`/v1/` is a contract:** [`docs/api-versioning.md`](docs/api-versioning.md).
  `/admin/*` and the operational endpoints are not.

## Commits

Write the message with `git commit -F -` and a quoted heredoc, not `-m
"..."`. These messages name commands and settings in backticks, and inside
double quotes the shell runs them as substitutions — a message mentioning
`make purge-audit ID=` ran it, and the commit landed with the output pasted
in where the backticks had been. Nothing warns you; the commit succeeds.

Run the checks, **read the output**, then push as a separate command. Not
`make check && git push` in one line: the failure scrolls past, the chain
carries on, and the red lands on `main` where the next person finds it. That
has happened here more than once.

After pushing, look at the run — `main` has no `cancel-in-progress`, so every
commit gets its own verdict and a failure cannot hide behind the next push.

One change per commit, pushed as it lands. The message says why — what was
wrong, what the alternative was, what was verified — not what the diff
already shows. Look at `git log` for the register.

## Money

Tests use fakeredis and an in-memory database, both autouse, and spend
nothing. Anything that hits a real provider is a deliberate act by the
person running it: `scripts/demo.sh` costs two requests, the k6 load test
more. The lifetime ceiling is the last line, not the first.
