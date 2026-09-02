# Architecture decisions

One file per decision, each with the alternative that was considered and why
it lost. They exist so the non-obvious calls aren't re-litigated from scratch,
and so a reader can tell a deliberate trade from an accident.

| # | Decision | |
|---:|---|---|
| 001 | Circuit breaker gates retry, not the other way around | [file](001-circuit-breaker-before-retry.md) |
| 002 | SQLite by default, Postgres by opt-in | [file](002-sqlite-default-postgres-optional.md) |
| 003 | Streaming buffers the first chunk before committing to a provider | [file](003-buffer-first-chunk-on-streaming.md) |
| 004 | Post-hoc bookkeeping is best-effort; pre-flight enforcement fails closed | [file](004-best-effort-bookkeeping-vs-fail-closed-enforcement.md) |
| 005 | Provider errors carry a retryable flag; 4xx skips remaining retries | [file](005-retryable-vs-non-retryable-provider-errors.md) |
| 006 | An unconfigured provider is a clean fallback skip, not a crash | [file](006-unconfigured-providers-fail-fast-not-crash.md) |
| 007 | Redis and Postgres connections have explicit, bounded timeouts | [file](007-bounded-backend-timeouts.md) |
| 008 | Client-controlled strings are length-capped at the API boundary | [file](008-validate-string-lengths-at-the-boundary.md) |
| 009 | An unknown model is served, loudly, unless the operator opts into rejecting it | [file](009-unknown-model-handling.md) |
| 010 | Circuit breakers stay per-process, not shared through Redis | [file](010-per-process-circuit-breakers.md) |
| 011 | A hard per-provider spend ceiling, separate from the per-key budget | [file](011-hard-provider-spend-ceiling.md) |
| 012 | A request that cannot be costed is refused, not run | [file](012-uncostable-requests-are-refused.md) |
