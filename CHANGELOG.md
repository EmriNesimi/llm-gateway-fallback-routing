# Changelog

## Unreleased

**Every module says what it is for**
- A docstring on every module and package under `app/`, written from the
  code rather than templated — each names the decision it rests on where
  there is one. D100/D104 pinned for `app/`.
- Every public class, method and function under `app/` has a docstring too,
  and every multi-line docstring opens with a one-line summary that stands
  alone — tools that show only the first line were showing half a sentence.
  D101–D103, D204 and D205 pinned. D107, D401 and D212/D213 are off with the
  reason stated, so they read as decided rather than overlooked.
- ARG pinned for `app/`: a parameter accepted and never read is how a config
  value gets silently ignored. The admin rate limiter's deliberately-unread
  header now says so beside the parameter.
- The demo's exit trap reports a failed key revoke instead of swallowing it,
  and preserves the script's own exit code.
- GitHub surface: PR and bug-report templates, `CONTRIBUTING.md`,
  `CODEOWNERS` for the money path, `.editorconfig`. Dependabot no longer
  proposes a Python major for the image alone. CI cancels superseded runs
  on pull requests only — on `main` every commit gets its own verdict, so a
  failure can no longer hide behind the next push's cancellation.

**Lint tightened, with the rules that caught something pinned**
- Composite assertions split; three in the ledger-durability tests were
  wrong — `"always"` anywhere in the redis block passed a check meant to
  prove `--appendfsync` was set to it. Now checked by adjacency.
- A lazy import in `model_map.py` was dodging a circular import that does
  not exist. Hoisted, with the graph written beside it.
- `BudgetTracker._apply` split so the swallowed path returns nothing instead
  of a `0.0` a caller could read as "the ledger holds nothing".
- `record_spend` removed: nothing called it, and its name is the shape
  decision 015 exists to retire.
- Mechanical: `logger.exception()`, tuple parametrize names, class instead
  of `lambda: Cls()`, spelled-out regex flags, underscore fixtures renamed.

**The demo no longer costs twenty requests or leaks a key**
- The rate-limit burst goes to the free `local` chain. The limiter runs
  before routing, so the 429 is identical — but every request under the cap
  went on to a provider, and on `default` that was up to twenty billed calls
  to show one refusal.
- `POST /admin/keys` returns the new key's `id` (additive), and the demo
  revokes its key on exit. Every earlier run left one behind, each a full
  monthly budget of exposure.
- `make shellcheck` runs CI's exact shellcheck image, so the lint step can
  be checked before pushing.
- `.env.example` describes the Redis auth failure as it actually appears:
  `/readyz` 503 and `AuthenticationError`, not the `NOAUTH` it told you to
  expect.

**Small things**
- `make help` is the default target; a `##` on each target line is its help.
- `scripts/__init__.py` says why the scripts run as modules (run as files,
  `app` is not on the path) and what belongs in that directory.
- `audit-purges/` is dockerignored as well as gitignored, so a build after a
  purge does not copy exported audit rows into the image.
- Runbook says reconcile runs from a checkout, since `scripts/` is not in the
  image and `docker exec` would find nothing.

**Both spend records agree with each other and with the bill**
- `make purge-audit` removes audit rows by exact `request_id`, dry run unless
  `APPLY=1`, with a JSON copy of every removed row written and read back
  before the `DELETE`. No date range on purpose — that is how a real request
  gets swept up with fake ones. Exact matching earned its keep on first use:
  the 150 rows of stub traffic carried two different hardcoded IDs, and a
  substring match would have taken all 150 without saying there were two.
- With those gone, `make reconcile` exposed what they had hidden: the
  anthropic ledger was `$0.00665` high with stranded reservations. Corrected
  to the audit-log figure, which the provider dashboard confirms. First time
  both stores have agreed with each other and with the bill.
- The budget gauge now publishes on every write. It refreshed only on reads,
  and the request path only reads when refusing — so after each *served*
  request the dashboard still showed the pre-request headroom, and after an
  operator correction it showed the old figure until the next refusal.
  `ProviderBudgetLow` and `ProviderBudgetExhausted` read that gauge.
- CI runs both operator scripts against its empty stores to prove they still
  import. Explicitly not a reconciliation: two empty stores agree trivially,
  and a check that cannot fail is worse than none. The runbook says where the
  real one runs, and gives the correction procedure for each direction of gap.
- README's spend-ceilings section no longer claims the per-key cap is checked
  rather than reserved, and now says how to check the ledger.

**The two spend records, and a way to compare them**
- `make reconcile` compares the ledger to the audit log per provider and says
  which way any gap runs. Ledger high is stranded reservations; ledger low is
  a ceiling bigger than the spend says, the direction this control must never
  fail in. Exits 1 on a gap, so it can gate a deploy. Written after the check
  it automates found $3.97 of phantom OpenAI spend and $5.92 of fake anthropic
  spend, by hand, once.
- [Decision 017](docs/decisions/017-two-spend-records-neither-is-the-truth.md):
  neither store is the truth. Agreement is the evidence; the provider's bill
  arbitrates because it is the only record the gateway cannot have written.
- [Decision 004](docs/decisions/004-best-effort-bookkeeping-vs-fail-closed-enforcement.md)
  updated with what "best-effort" did in practice once reservations existed.
- The runbook routes every budget page through `make reconcile` before any
  other action.

**Test isolation, closed properly**
- A guard now derives every `from app.db.session import async_session` from
  the source and checks `isolated_db` reaches it. It found one it did not:
  `app/main.py`, so any test hitting `/readyz` queried the real `gateway.db`.
  Same hole that put 146 fake rows in the audit log, one module over.
- The guard imports at collection time. Imported inside the fixture it binds
  the already-patched name and passes vacuously — which it did, on first run.

**Verified against real infrastructure**
- The `v0.5.0` release ran green on six freshly-bumped action majors; the
  published image was pulled, ran native arm64, passed `/readyz`, and served a
  real request that moved the ledger by $0.0000024. The ledger survived a
  Docker Desktop restart in between — decision 013 holding in practice.
- Real-Redis integration tests were silently skipping; they run now.
  Migrations applied clean to a real Postgres 18.
- The test suite was deleting the production spend ledger and writing fake
  spend into the production audit log on every run. Both fixed in 0.5.0;
  noted here because the numbers in any earlier reconciliation were affected.

## 0.5.0

**The per-key budget now holds under concurrency**
- `MONTHLY_BUDGET_USD_PER_KEY` was checked before a request and recorded after,
  with nothing claimed in between, so concurrent requests from one key all read
  the same pre-call total and were all admitted. The overshoot was bounded by
  the rate-limit burst rather than the cap. `BudgetTracker` now reserves and
  settles the way `ProviderBudget` already did, both ledgers claiming in
  `_reserve_chain` and releasing in `_settle_chain`
  ([decision 015](docs/decisions/015-both-spend-ledgers-reserve.md)).
- The per-key reservation is the *sum* of the chain's provider reservations,
  not the largest: fallback can bill more than one hop.
- A failed over-cap refund propagated its Redis error instead of the budget
  exception, so a caller who was merely out of budget was told the gateway was
  broken. Both ledgers now refuse either way and log the stranded claim.
- A Redis failure partway along a chain escaped with the earlier hop's
  reservation still claimed and nothing downstream to refund it — lifetime
  headroom gone for a request that was never served. One unwind now covers the
  whole of `_reserve_chain`, and it catches `CancelledError`, which is not an
  `Exception` and so slipped past the old handler on every client disconnect.
- Bookkeeping finishes before a cancellation propagates. The refund loop used
  to stop dead on one, stranding every provider after it, and the key ledger
  was skipped entirely — leaving one ledger refunded and the other still
  holding a claim for a request that was over.
- `gateway_budget_reservation_leaked_usd_total` and `BudgetReservationLeaked`.
  A reservation that cannot be handed back makes the ceiling permanently
  smaller, and it fails in the safe direction — spend looks normal while
  headroom erodes — which is exactly what would stop anyone noticing.

**Guards**
- `tests/test_ledger_conservation.py` asserts the property the money-path bugs
  have all violated: whatever a request claims, it either spends or hands back.
  Both ledgers, every outcome, all three endpoints.
- The client API's rate-limit ordering is a decision rather than a habit
  ([016](docs/decisions/016-no-pre-auth-rate-limit-on-the-client-api.md)), and
  both surfaces' orderings are now pinned by tests.
- The circuit breaker takes an injectable clock, and `/readyz`'s concurrency
  test asserts its checks overlap instead of timing them. Both were racing the
  wall clock with margins a loaded machine defeats — one of them reproducibly
  (#17).

**Dependencies**
- Runtime floors raised: `redis>=8.1.0`, `uvicorn>=0.52.4`,
  `opentelemetry-instrumentation-fastapi>=0.65b0`. Dev floors: `ruff>=0.16.5`,
  `pytest-cov>=7.1.0`. Workflow actions bumped to current majors.
- The Docker base stays on Python 3.12. Moving it alone would have left CI
  testing one interpreter while the image shipped another; the version is
  written down in seven places and they move together.

**Cost control — from a security review**
- OpenAI is now sent the output cap the budget reserved against. It was called
  with no `max_tokens` at all while the reservation assumed 2048, so the
  reserved figure bounded a limit the request never carried. Native `/v1/chat`
  and `/v1/chat/stream` callers could not set one even deliberately.
- The reservation covers every retry attempt, not one call. A provider that
  times out client-side after generating a completion bills for it and bills
  again for the retry; only the last attempt was ever settled.
- Both are one rule, now written down as
  [decision 014](docs/decisions/014-a-reservation-must-be-an-upper-bound.md).
- Settling is best-effort, matching the rule `record_spend` already followed:
  a Redis blip after a provider has answered no longer turns a paid-for
  response into a 500, and one failure no longer strands the rest of the chain.
- An OpenAI response arriving without a `usage` block is charged an estimate
  instead of settling as free.
- The circuit breaker's half-open state admits exactly one trial request. It
  admitted every concurrent request in the cooldown window — the thundering
  herd [decision 010](docs/decisions/010-per-process-circuit-breakers.md)
  assumes it prevents, aimed at a provider that bills for each attempt.

**Observability — the failures nothing was watching**
- `gateway_unhandled_exceptions_total`. A 500 reached no counter at all:
  `gateway_requests_total` is incremented inside the handlers, so anything
  raising before or around them left the request rate flat — which on a
  dashboard is indistinguishable from nobody calling. Graphed and alerted.
- `ProviderFallbackRateHigh`. The breaker only opens on outright failures, so
  a provider that times out and succeeds on retry never trips it. Fallback
  absorbs the problem silently while every affected request pays for a failed
  attempt first.
- `RequestLatencyDegraded`. Every other rule asks whether requests work; none
  asked how long they take, which is the only thing a caller experiences.
- Alert severities are pinned to the set Alertmanager routes on, since an
  unroutable value looks perfectly reasonable in the file and matches nothing.

**Guards and documentation**
- The two hand-maintained lists of refusal statuses — the OpenAPI `responses=`
  and the table in `docs/api-versioning.md` — are checked against each other.
  The guard found drift immediately: the policy was missing `401` and `404`.
- Every refusal reason label now maps to an action in the runbook, and a guard
  keeps it that way. A label on a dashboard that leads nowhere is not
  observability.
- The runbook gained the commonest real question, which is not an alert: a
  caller reporting refusals, and which of the five outcomes it is.
- Prometheus's evaluation interval is held finer than the shortest `for:`
  clause, because `for:` is counted in evaluations rather than wall time.
- Two limitations are now stated with their reasoning rather than left to be
  found: the per-key monthly budget races under concurrency (the operator's
  ceiling does not), and invalid client keys are not rate limited (the admin
  key is, deliberately).

**API**
- `/v1/chat`, `/v1/chat/stream` and `/v1/chat/completions` declare their
  refusal statuses in the OpenAPI document. Clients had no way to discover
  401/402/404/429/502/503 from the schema.

**Hardening**
- The gateway container drops every Linux capability and sets
  `no-new-privileges`. It already ran as an unprivileged uid; these close the
  routes back out of that.

**Documentation**
- A runbook section for the failure that looks healthy: process up, `/healthz`
  green, every request refused. Usually the Redis password, and it presents
  that way because the rate limiter and budget checks fail closed.
- The VS Code guide no longer claims `/v1/chat` works with no external
  services. It reads Redis for three separate checks, all of which fail
  closed.

## 0.4.0

Nothing here changes how a request is routed or what it costs. It is all
hardening around the edges — the image, CI, and guards against documentation
quietly going stale.

**Runtime image**
- Test and lint tooling moved to `requirements-dev.txt`. `pytest`, `ruff`,
  `mypy`, `pip-audit` and `fakeredis` were being installed into the production
  image; it is now 295MB rather than 433MB.

**Alerting**
- `GatewayTargetDown` — every other rule evaluates metrics the gateway
  publishes, so none of them fire when the gateway is the thing that's down.
- `GatewayRequestsFailingAcrossWholeChain` — fires when requests are failing
  every provider, which is the outcome fallback exists to prevent.

**CI**
- Job timeouts, so a wedged step can't burn six hours of runner time.
- `shellcheck` over the tracked shell scripts.
- `promtool`, `docker compose config`, and an image build that starts the
  container and asserts it isn't running as root.
- Dependabot enabled for pip, GitHub Actions and Docker.
- Warnings from our own code fail the build; `--strict-markers` and
  `--strict-config`; ruff's bugbear rules.
- `make check` runs the same set locally.

**Cost control**
- A model with no pricing entry is now **refused** rather than run. A missing
  price meant a `$0` worst case, which reserved nothing and let the request
  run outside the lifetime ceiling entirely — a bypass in the one control
  designed not to have one. The provider is dropped from the chain like an
  exhausted one so fallback still serves the request; only an entirely
  unpriced chain fails, with `503 no pricing configured`. See
  [decision 012](docs/decisions/012-uncostable-requests-are-refused.md).
- Spend with no matching reservation is now actually charged.
  `record_unreserved` existed for the client-hangup case, was documented as
  the escape hatch and unit-tested, and was never called from anywhere — so
  `_settle_chain` silently dropped any charge against a provider that had no
  reservation.

**Cost control (continued)**
- The spend ledger is now **persisted**. Redis ran with no volume and no
  append-only file, so it started empty after every restart — which meant a
  `$4` *lifetime* ceiling was really `$4` *per uptime window*, and
  `docker compose down` refilled it. Nothing in the application code was
  wrong; the storage under it was a scratch pad. See
  [decision 013](docs/decisions/013-the-spend-ledger-is-persisted.md).

**Operations**
- Redis is now told never to evict (`maxmemory-policy noeviction`) and to
  fsync every write (`appendfsync always`). The ledger holds money; the
  defaults are tuned for a cache that can afford to lose a second of writes.
- `make ledger` prints spend and remaining headroom per provider, so reading
  the number no longer means guessing at a Redis key name.
- `.env.example` warns that the bundled Redis needs a password in `REDIS_URL`
  — without it the gateway starts fine and then fails closed on every request.
- Guards for two properties that were only comments: every compose port binds
  loopback, and every route is either under `/v1/` or named as operational in
  the versioning policy.
- Every image in the compose stack names an exact version. Redis and Postgres
  were on `7-alpine` and `16-alpine`, which float across a whole major — and
  Redis is the one holding the spend ledger.
- The unbounded growth of `audit_log` is now stated as a known limitation
  rather than left to be discovered as a full disk.
- [`docs/runbook.md`](docs/runbook.md) — one section per alert: what it means,
  what to check, and for three of them what *not* to do. Every rule carries a
  `runbook_url`, so the instructions arrive with the page.
- The base image is pinned by digest as well as tag, so two builds of the same
  commit produce the same userland.

**Observability**
- `gateway_requests_refused_total{reason}` — a refused request previously
  reached no metric at all, since it raises before the handler records
  anything. A gateway turning every request away looked identical to an idle
  one. Five reasons, kept separate because each has a different fix.
- Dashboard panel for refusals by reason, and an alert for the one that means
  misconfiguration rather than spend (`GatewayRefusingUnpricedRequests`).
- `gateway_provider_budget_spent_usd` is now graphed. It had been exported on
  every ledger read and displayed nowhere.

**Test coverage**
- Coverage now traces SQLAlchemy's greenlets. Without it, every line after the
  first `await session.…` in a handler was reported as never executed —
  including branches the tests were provably exercising.
- Closed the real gaps that were left once the measurement was honest: the
  request-size bound that limits cost, the spend ceiling's free-provider
  exemptions, both skip paths in the router, Anthropic's system-prompt hoist
  on the streaming path, and the startup guards for tracing and schema
  creation. 98% line-and-branch, floor raised from 92% to 97%.
- `worst_case_cost_usd` now warns when a model has no price, because a $0
  worst case means no budget is reserved and the request escapes the ceiling
  entirely. Only the billing side warned before.

**Guards against drift**
Each of these exists because the thing it checks had already gone stale at
least once, silently:
- The seven places the Python version is pinned must agree.
- The version the app serves must match the newest CHANGELOG entry.
- `.env.example` must document the defaults the code actually uses.
- Alert rules must reference metrics that exist.
- The README's test count and coverage floor must match reality.

## 0.3.0

The spend ceiling, and making it actually work.

**Cost control**
- Hard per-provider **lifetime** ceiling (`PROVIDER_LIFETIME_BUDGET_USD`),
  reserved atomically before each call. Never resets, isn't keyed by caller,
  and can't be raised by issuing more client keys.
- Request size bounded — a single request could previously cost more than the
  entire budget.
- Streamed spend now recorded when a client disconnects mid-stream. It
  previously recorded `$0.00`, which made every cap unreachable.
- Fixed a double-refund that drove the ledger negative, so the ceiling could
  never be reached at all. It shipped tested and documented, and was inert.

**Security**
- Client API keys hashed before use as Redis key names.
- Every compose port bound to loopback; Redis given a password.
- Upstream provider error bodies no longer returned to callers.
- Admin API rate limited — ahead of the key check, so guesses are limited too.
- Key issuance and revocation audited, readable at `/admin/key-events`.
- Placeholder API keys detected at startup instead of 401ing every request.
- Container runs as a non-root user.

**Observability**
- Budget headroom exported as a gauge and charted; alert rules for breaker
  state and remaining budget.
- Anthropic system prompts sent as a system prompt rather than a message,
  which was a 400 and silently skipped the provider.
- `/metrics` gated on an optional `METRICS_TOKEN`.

## 0.2.0

Usable by an existing application.

- OpenAI-compatible `POST /v1/chat/completions`, streaming included, tested
  against the real `openai` SDK.
- Named fallback chains (`default` / `fast` / `smart` / `local`) replacing a
  single chain every model name collapsed onto, plus `GET /v1/models`.
- `temperature` / `top_p` / `max_tokens` / `stop` forwarded to each provider
  in its own dialect.
- Circuit breaker state, spend, tokens and per-provider latency on Prometheus;
  Jaeger in the compose stack.
- Multi-arch container image published to GHCR on tag.

## 0.1.0

Fallback routing, rate limiting, per-key budgets, circuit breakers, streaming
with fallback-before-first-chunk, admin API and audit log, OpenTelemetry
tracing and Prometheus metrics.
