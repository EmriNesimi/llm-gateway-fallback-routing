<!-- Same shape as this repo's commit messages: the reasoning, not a restatement of the diff. -->

## Why

<!-- What was wrong, or what could not be done. If it is a bug, what the symptom was and why it was not caught. -->

## What changed

<!-- One line per thing. The diff already shows how. -->

## Verified

<!-- What was actually run, against what. `make check` is the floor. If this touches the money path — anything under app/budget/, _reserve_chain, _settle_chain — say what `make reconcile` reported afterwards and that tests/test_ledger_conservation.py passed. -->

## Touches

- [ ] The spend ceiling or either ledger (needs a `## Verified` line about `make reconcile`)
- [ ] A `/v1/` response shape or status code (see `docs/api-versioning.md`)
- [ ] A metric or alert (the "graphed or alerted" guard must still pass; new alerts need a runbook section)
- [ ] `docker-compose.yml` or the Dockerfile (say what `make up` did)
- [ ] None of the above
