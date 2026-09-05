# Changelog

## Unreleased

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
