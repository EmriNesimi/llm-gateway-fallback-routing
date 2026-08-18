<div align="center">

<img src="https://capsule-render.vercel.app/api?type=waving&color=0:0a0e27,50:0f3443,100:00d9a3&height=200&section=header&text=LLM%20GATEWAY&fontSize=54&fontColor=00ff9d&fontAlignY=35&desc=%3E%20route%20/%20fallback%20/%20throttle%20/%20audit%20/%20observe&descAlignY=55&descSize=18&descColor=7dffcf&animation=fadeIn" width="100%"/>

<img src="https://readme-typing-svg.demolab.com?font=Fira+Code&weight=500&size=16&pause=900&color=00E5A0&background=0A0E27&center=true&vCenter=true&width=820&height=130&lines=%24+curl+-X+POST+%2Fv1%2Fchat+-H+%22Authorization%3A+Bearer+...%22;%5Bgateway%5D+routing+request+%E2%86%92+openai%3Agpt-4o-mini;%5Bopenai%5D+429+rate_limited+%E2%86%92+falling+back...;%5Banthropic%5D+200+OK+%E2%86%90+response+served+in+412ms;%5Bbreaker%5D+openai+circuit+OPEN+%E2%80%94+cooling+down+30s;%5Badmin%5D+POST+%2Fadmin%2Fkeys+%E2%86%92+team%3Ademo-team+key+issued;%5Baudit%5D+row+written%3A+team%3Ddemo-team+cost%3D%240.0031+412ms;%5Bmetrics%5D+fallback_triggered_total%2B%2B+%7C+scraped+by+prometheus" alt="terminal typing animation" />

<br/><br/>

[![CI](https://github.com/EmriNesimi/llm-gateway-fallback-routing/actions/workflows/ci.yml/badge.svg)](https://github.com/EmriNesimi/llm-gateway-fallback-routing/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-0a0e27?style=for-the-badge&labelColor=0a0e27&color=00ff9d)](LICENSE)
[![Release](https://img.shields.io/github/v/tag/EmriNesimi/llm-gateway-fallback-routing?style=for-the-badge&label=release&labelColor=0a0e27&color=00ff9d)](https://github.com/EmriNesimi/llm-gateway-fallback-routing/tags)
[![Python](https://img.shields.io/badge/Python-3.12-0a0e27?style=for-the-badge&logo=python&logoColor=00ff9d&labelColor=0a0e27)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0a0e27?style=for-the-badge&logo=fastapi&logoColor=00ff9d&labelColor=0a0e27)](https://fastapi.tiangolo.com/)
[![Redis](https://img.shields.io/badge/Redis-0a0e27?style=for-the-badge&logo=redis&logoColor=00ff9d&labelColor=0a0e27)](https://redis.io/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-0a0e27?style=for-the-badge&logo=postgresql&logoColor=00ff9d&labelColor=0a0e27)](https://www.postgresql.org/)
[![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-0a0e27?style=for-the-badge&logo=sqlalchemy&logoColor=00ff9d&labelColor=0a0e27)](https://www.sqlalchemy.org/)
[![OpenTelemetry](https://img.shields.io/badge/OpenTelemetry-0a0e27?style=for-the-badge&logo=opentelemetry&logoColor=00ff9d&labelColor=0a0e27)](https://opentelemetry.io/)
[![Prometheus](https://img.shields.io/badge/Prometheus-0a0e27?style=for-the-badge&logo=prometheus&logoColor=00ff9d&labelColor=0a0e27)](https://prometheus.io/)
[![Grafana](https://img.shields.io/badge/Grafana-0a0e27?style=for-the-badge&logo=grafana&logoColor=00ff9d&labelColor=0a0e27)](https://grafana.com/)

<img src="https://capsule-render.vercel.app/api?type=rect&color=0:00d9a3,100:0a0e27&height=3&width=100%25" width="100%"/>

</div>

## 📡 what this is

Every request into this gateway is a **packet looking for a route**. It hits auth, clears the rate limiter, checks its budget, then gets handed to the router — which tries providers in priority order until one answers. If OpenAI is down, Anthropic picks it up. If both are unavailable, Ollama (local, free) is the last hop. Every hop is measured.

> Most AI demos show a single model doing a clever trick. In production, the hard part is **reliability** — providers rate-limit you, go down, or get expensive, and someone has to own the routing, the budget enforcement, and the "why did this fail at 2am" answer. This gateway is that someone.

<img src="https://capsule-render.vercel.app/api?type=rect&color=0:0a0e27,100:00d9a3&height=3&width=100%25" width="100%"/>

## 🧭 the routing table

| Signal | Meaning |
|:---:|---|
| 🟢 | Primary provider answered — fast path |
| 🟡 | Primary failed, fallback provider served the request |
| 🔴 | Every provider in the chain failed — `502` returned, nothing silently swallowed |
| 🚫 | Rate limit or budget cap hit before a provider was ever called — `429` / `402` |
| ⚫ | Provider's circuit is open (too many recent failures) — skipped without a network call |

## ⚙️ what it does

- 🔀 **Unified API** — one OpenAI-compatible endpoint in front of multiple providers; clients never know who actually answered.
- 🛟 **Automatic failover** — primary errors or times out → the router advances to the next provider in the chain, no client-side retry logic needed.
- ⚡ **Circuit breaker per provider** — after repeated failures a provider is skipped entirely for a cooldown period instead of being retried and timing out every request.
- 🚦 **Per-key rate limiting & budgets** — Redis-backed token bucket + monthly spend cap, enforced *before* a provider is ever called.
- 📊 **Full observability** — every hop is traced (OpenTelemetry) and measured (Prometheus), visualized in Grafana.
- 🔒 **Security-first** — fail-closed auth, constant-time key comparison, zero secrets in git history.

## 🗺️ the request path

```mermaid
%%{init: {'theme':'dark', 'themeVariables': {'primaryColor':'#0f3443','primaryTextColor':'#00ff9d','primaryBorderColor':'#00d9a3','lineColor':'#00d9a3','secondaryColor':'#0a0e27','tertiaryColor':'#0a0e27'}}}%%
flowchart LR
    Client(["📨 Client"]) -->|API key| Auth{{"🔑 Auth"}}
    Auth -->|429 if over| RateLimit["🚦 Rate Limiter\nRedis token bucket"]
    RateLimit -->|402 if over| Budget["💰 Budget Check\nRedis spend tracker"]
    Budget --> Router["🔀 Fallback Router"]
    Router -->|"1️⃣ try"| OpenAI["OpenAI"]
    Router -->|"2️⃣ fallback"| Anthropic["Anthropic"]
    Router -->|"3️⃣ fallback"| Ollama["Ollama (local)"]
    Router -.trace + metrics.-> Otel["📡 OpenTelemetry\n+ Prometheus"]
    Otel --> Grafana["📊 Grafana"]
```

## 🧱 stack

| Layer | Choice |
|---|---|
| Backend | Python, FastAPI |
| Rate limiting / budgets | Redis (token bucket) |
| API keys / audit log | SQLAlchemy async — SQLite by default, Postgres in production |
| Schema migrations | Alembic |
| Observability | OpenTelemetry, Prometheus |
| Dashboards | Grafana |
| Providers | OpenAI, Anthropic, Ollama |

<img src="https://capsule-render.vercel.app/api?type=rect&color=0:00d9a3,100:0a0e27&height=3&width=100%25" width="100%"/>

## 🚧 build log

Phase 4 (resilience) complete ✅ — production-hardening pass in progress: timeouts, readiness checks, rate-limit/budget headers, and request-ID tracing are done.

- [x] Project scaffold, config, security foundations
- [x] Provider adapters (OpenAI / Anthropic / Ollama) + fallback chain
- [x] Redis-backed rate limiting & per-key monthly budgets
- [x] OpenTelemetry tracing + Prometheus metrics
- [x] Grafana dashboards (via docker compose)
- [x] Circuit breaker per provider
- [x] Retry with backoff before falling back
- [x] Streaming support with fallback-before-first-chunk
- [x] Admin API for teams/keys + audit log
- [x] Configurable per-provider request timeouts
- [x] `/readyz` readiness checks (Redis + DB) + Docker `HEALTHCHECK`
- [x] Rate limit / budget response headers
- [x] Request ID correlation across headers, audit log, traces, and logs
- [x] Tests for rate limiter, budget tracker, metrics, circuit breaker, fallback/retry, streaming, admin API, audit log, readiness, response headers, request ID, health endpoint
- [x] CI (GitHub Actions: lint + tests + coverage on every push/PR)

## 🔌 calling the api

`POST /v1/chat` requires a client API key (one of the values in `GATEWAY_API_KEYS`), passed as either:

```
Authorization: Bearer <key>
```
or
```
X-API-Key: <key>
```

Each key is independently rate-limited (token bucket: `RATE_LIMIT_CAPACITY` burst, `RATE_LIMIT_REFILL_PER_SEC` sustained) and budget-capped (`MONTHLY_BUDGET_USD_PER_KEY`, resets monthly). Exceeding the rate limit returns `429`; exceeding the budget returns `402`.

`POST /v1/chat/stream` takes the same request body and auth, but streams the response back as Server-Sent Events (`text/event-stream`) — each `data:` line is a JSON chunk, ending with `data: [DONE]`.

Every response (success, `429`, or `402`) on both endpoints carries status headers so clients don't have to guess:

| Header | Meaning |
|---|---|
| `X-RateLimit-Limit` | This key's token bucket capacity |
| `X-RateLimit-Remaining` | Tokens left *after* this request |
| `Retry-After` | Seconds to wait before retrying (`429` responses only) |
| `X-Budget-Remaining-USD` | Monthly budget left as of this request's admission |
| `X-Request-ID` | Correlation ID for this request — see below |
| `X-Gateway-Chain` | Which fallback chain actually served this request — see below |

## 🔁 drop-in openai compatibility

`POST /v1/chat/completions` speaks OpenAI's Chat Completions format, so an application already built against the `openai` SDK gets the fallback chain, rate limiting, budgets, and audit trail by changing one line — its `base_url`:

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8000/v1", api_key="<your gateway key>")

completion = client.chat.completions.create(
    model="fast",                                    # a chain name, see below
    messages=[{"role": "user", "content": "hello"}],
)
print(completion.choices[0].message.content)
```

`stream=True` works the same way, emitting `chat.completion.chunk` deltas. `GET /v1/models` is compatible too, so `client.models.list()` returns the routable chains. The test suite drives all three with the real SDK rather than hand-built JSON — see `tests/test_openai_compat.py`.

Two deliberate differences from OpenAI:

- **Unknown models are rejected**, not substituted. This endpoint is new, so it has no callers to break and doesn't inherit the compatibility constraint `/v1/chat` is stuck with ([decision 009](docs/decisions/009-unknown-model-handling.md)).
- **Sampling parameters aren't forwarded.** `temperature`, `top_p` and friends are accepted so clients don't break, but the provider adapters don't pass them through yet — so rather than dropping them silently, the response carries `X-Gateway-Ignored-Params` and the gateway logs a warning naming each one.

## 🎚️ picking a chain

The `model` field selects a **fallback chain**, not a specific model. Chains are capability tiers, so a chain can be re-pointed at a newer model without any client changing:

| `model` | Chain | Notes |
|---|---|---|
| `default` | OpenAI `gpt-4o-mini` → Anthropic `claude-haiku-4-5` → Ollama `llama3` | What you get if you don't pick |
| `fast` | same as `default` | Named explicitly, so the intent is in the request rather than implied |
| `smart` | Anthropic `claude-opus-5` → OpenAI `gpt-4o` | No local hop on purpose — a caller who asked for the strongest model deserves a `502` rather than an 8B model quietly answering in its place |
| `local` | Ollama `llama3` | Nothing leaves the host, nothing is billed |

`GET /v1/models` returns this list at runtime (same `object`/`data` envelope as OpenAI's, plus the providers behind each name), so a client can discover the routable names instead of guessing.

**A model this gateway can't route is served by `default`** — the behavior it's always had — but no longer silently: the response carries `X-Gateway-Chain: default`, and the substitution is logged with the request ID. Set `STRICT_MODEL_ROUTING=true` to get a `404` listing the routable names instead. It defaults to `false` because turning a `200` into a `404` is a breaking change under [`docs/api-versioning.md`](docs/api-versioning.md), and that policy doesn't get suspended just because it's inconvenient — the reasoning is in [decision 009](docs/decisions/009-unknown-model-handling.md).

Every model in a chain needs a matching entry in `app/budget/pricing.py`. An unpriced model costs `$0.00`, so spend never accumulates and the monthly budget cap silently stops applying to it — `tests/test_pricing.py` fails the build rather than letting that ship.

## 📡 streaming fallback

Fallback and streaming don't naturally get along: once a client has started receiving tokens, silently switching providers mid-answer would produce garbled output. This gateway resolves it by **buffering only the first chunk** of a provider's response before committing:

- If a provider fails (or its circuit is open) before it produces any output, the router retries it, then falls back to the next provider — the client never sees a failed attempt.
- Once a provider's first chunk has been sent to the client, the gateway is committed to it. A failure *after* that point ends the stream with an `event: error` SSE message rather than switching providers mid-answer.

## ⚡ circuit breaker

Each provider gets its own breaker: `CIRCUIT_BREAKER_FAILURE_THRESHOLD` consecutive failures trips it open, and it stays open (skipped, no network call) for `CIRCUIT_BREAKER_COOLDOWN_SECONDS` before a single trial request is allowed through (half-open). That trial succeeding closes the circuit; failing re-opens it. This keeps a dead provider from adding latency to every single request while it's down.

## 🔁 retry before fallback

A single transient error (a dropped connection, a momentary 5xx) doesn't need a full provider swap. Before the router gives up on a provider and moves to the next one in the chain, it retries the *same* provider `PROVIDER_RETRY_ATTEMPTS` times with a `PROVIDER_RETRY_BACKOFF_SECONDS` pause between tries. Only after retries are exhausted does the circuit breaker record a failure and the router falls back.

## 🔑 admin api & audit log

Client API keys can be issued and revoked without editing `.env`, via an admin API gated by a separate `ADMIN_API_KEY` secret (higher trust level than client keys — a leaked client key can't be used to mint more keys):

```
POST   /admin/keys        {"team": "acme"}   → {"api_key": "...", "team": "acme"}  (shown once)
GET    /admin/keys                             → [{"id", "team", "created_at", "revoked"}, ...] (paginated: limit/offset)
GET    /admin/keys/{id}                        → a single key's record
DELETE /admin/keys/{id}                        → revokes the key immediately
```

Pass the admin secret via `X-Admin-Key`. Like the client-facing auth, this fails closed (`503`) if `ADMIN_API_KEY` isn't set, rather than being left open.

Every `/v1/chat` and `/v1/chat/stream` request — success or failure — is written to an **audit log** (SQLite by default, Postgres via `DATABASE_URL` in production): timestamp, hashed API key, team, model, provider, outcome, tokens, cost, and latency. That's the "why did this fail at 2am" record — keys issued through the admin API get their team attributed automatically; keys from the legacy `GATEWAY_API_KEYS` env var are logged under `team: "unlinked"`.

`GET /admin/audit-log` takes `team`, `request_id`, `limit` (max 1000), and `offset` — page through a wider window with `limit`/`offset` once a team has more rows than fit in one response.

### schema migrations

Schema changes are managed with **Alembic**, not ad-hoc `create_all` calls, so a real (Postgres) database can be upgraded in place without losing data:

```bash
alembic upgrade head        # apply all pending migrations
alembic revision --autogenerate -m "describe the change"   # after editing app/db/models.py
alembic check                # fails if models.py drifted from committed migrations — this runs in CI
```

Against the default SQLite dev database, `create_all` still runs automatically at startup for zero-config convenience. For Postgres, that's skipped — `docker-entrypoint.sh` runs `alembic upgrade head` once before the app starts, rather than racing every replica into migrating on boot.

CI runs these same migrations against a real Postgres service (not just SQLite) on every push, so the Postgres path is actually exercised, not just assumed to work.

## 🔍 tracing a single request

Every request gets a correlation ID — either generated, or honored if the caller sends one via `X-Request-ID` — and it's threaded through everything that request touches:

1. **Response header**: `X-Request-ID` on every response, success or error, so a caller always has something to hand to support.
2. **Audit log**: `GET /admin/audit-log?request_id=<id>` jumps straight to that request's row — team, provider, outcome, cost, latency.
3. **Traces**: the same ID is a `gateway.request_id` attribute on every OpenTelemetry span for that request, linking the audit row to the exact provider attempts (including retries and fallbacks) behind it.
4. **Logs**: router log lines are prefixed `[request_id=<id>]`, so `grep`ing a support-reported ID surfaces the full story even without a tracing backend configured.

## 📊 observability

- **`GET /healthz`** — liveness: is the process up? No dependency checks.
- **`GET /readyz`** — readiness: can this instance actually reach Redis and the DB? Returns `503` with per-dependency detail if not — what a load balancer or orchestrator should actually probe.
- **`GET /metrics`** — Prometheus-format metrics: request count/latency, per-provider attempt outcomes, fallback rate.
- **Tracing** — set `OTEL_EXPORTER_OTLP_ENDPOINT` to send traces to any OTLP collector. Each provider attempt gets its own span with provider/model/attempt/request_id attributes. If unset, or nothing is listening there, the app still runs fine — you'll just see a harmless retry warning in the logs.
- **Dashboards** — `docker compose up` brings up Prometheus (`:9090`) scraping the gateway and Grafana (`:3000`, default login `admin`/`admin`), auto-provisioned with a "LLM Gateway Overview" dashboard (request rate by status, p50/p95/p99 latency, provider attempts by outcome, fallback rate) — no manual setup, it's already there on first load.
- **Logging** — plain text by default; set `LOG_FORMAT=json` for structured single-line logs a log aggregator (Loki, CloudWatch, etc.) can parse.
- **Load testing** — `scripts/load_test.js` is a [k6](https://k6.io) script that drives sustained concurrent traffic at `/v1/chat` (ramping past the default rate limit on purpose) so you can watch the fallback chain, circuit breaker, and rate limiter behave under pressure live in the Grafana dashboard above: `GATEWAY_URL=http://localhost:8000 CLIENT_KEY=<your key> k6 run scripts/load_test.js`. Real results from a run against a live OpenAI key: [`docs/load-test-results.md`](docs/load-test-results.md) — 3067 requests, 0 failures, p95 ~1.1s on served requests.

## 🚀 getting started

```bash
git clone https://github.com/EmriNesimi/llm-gateway-fallback-routing.git
cd llm-gateway-fallback-routing

cp .env.example .env   # fill in your real API keys — .env is git-ignored
# you don't need all of OpenAI/Anthropic/Ollama — an unset provider is
# skipped cleanly and the fallback chain still works with whatever you have
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

uvicorn app.main:app --reload
```

Visit `http://localhost:8000/docs` for the interactive API docs, or `http://localhost:8000/healthz` for a health check. `.python-version` pins this to Python 3.12, matching CI and the Dockerfile.

Testing this in VS Code specifically (Test Explorer setup, a debug config with breakpoints, running the Docker stack)? See [`docs/testing-in-vscode.md`](docs/testing-in-vscode.md).

### running lint & tests locally

```bash
make lint
make typecheck
make audit
make test
```

(equivalent to `ruff check .`, `mypy`, `pip-audit -r requirements.txt`, and `pytest -q --cov=app --cov-report=term-missing` — see the `Makefile` for the rest: `make run`, `make migrate`, `make up`/`down`, `make demo`)

Same commands CI runs on every push/PR to `main` — CI also runs a real Redis service container, so the rate limiter's Lua script is tested against genuine Redis, not just `fakeredis`'s emulation of it.

### with docker

```bash
make up
# or: docker compose up --build
```

Spins up the gateway, Redis, Prometheus, and Grafana together. Add `postgres` to `DATABASE_URL` in `.env` (`postgresql+asyncpg://gateway:changeme@postgres:5432/gateway`) to back the admin API/audit log with the bundled Postgres service instead of SQLite. The gateway container has a `HEALTHCHECK` against `/readyz`, waits for Postgres to be healthy before running migrations (with its own retry loop in `docker-entrypoint.sh` as a second line of defense — orchestrators other than Compose don't all offer that dependency ordering), and `.dockerignore` keeps secrets/caches/tests out of the build context.

### 🎬 run the guided demo

With the gateway up and `ADMIN_API_KEY` set, `scripts/demo.sh` walks the whole system end-to-end — issuing a key, routing a real request, tripping the rate limiter, and pulling the audit trail back out:

```bash
DEMO_ADMIN_KEY=<your ADMIN_API_KEY> make demo
# or: DEMO_ADMIN_KEY=<your ADMIN_API_KEY> ./scripts/demo.sh
```

```
=== 2. Issue a fresh client key via the admin API ===
{ "api_key": "5992baf...", "team": "demo-team" }

=== 4. Trip the rate limiter (bursting past RATE_LIMIT_CAPACITY) ===
request 01 -> 200
request 02 -> 200
request 03 -> 429
-> Rate limit engaged (429) after 3 requests.

=== 6. Review the audit trail for this demo ===
[ { "team": "demo-team", "provider": "openai", "outcome": "success", "cost_usd": 6.6e-06, ... } ]
```

<img src="https://capsule-render.vercel.app/api?type=rect&color=0:0a0e27,100:00d9a3&height=3&width=100%25" width="100%"/>

## 🧭 architecture decisions

The non-obvious tradeoffs — why the circuit breaker gates retry rather than the other way around, why SQLite is the default and Postgres is opt-in, why streaming buffers just the first chunk before committing to a provider, why post-hoc bookkeeping (audit log, spend recording) is best-effort while pre-flight enforcement (rate limit, budget) fails closed — are written up in [`docs/decisions/`](docs/decisions/), one file per decision, with the alternative considered and why it lost.

## ⚠️ error handling

- **Config is validated at startup, not discovered at runtime.** Nonsensical values — a negative rate limit capacity, a zero timeout, an unsupported `DATABASE_URL` scheme, an invalid `LOG_FORMAT` — fail the process immediately with a clear message, instead of the gateway starting up and misbehaving in a way that's harder to trace back to its cause.
- **Unhandled exceptions return a structured `500`.** Anything not already handled by a specific `except` block (a bug, a backend outage that wasn't caught closer to its source) still gets logged with the request's correlation ID and returns `{"error": "internal server error", "request_id": "..."}` — not FastAPI's bare default body — so a report of "it 500'd" can always be traced to the matching server-side log line.
- **Pre-flight checks fail closed; post-hoc bookkeeping doesn't fail the request.** If Redis is unreachable when checking the rate limit or budget, the request is rejected. But once a provider has already returned a response, a Redis or DB blip while recording the audit log entry or spend can't discard that response — it's logged and swallowed instead. See [decision 004](docs/decisions/004-best-effort-bookkeeping-vs-fail-closed-enforcement.md) for the reasoning.
- **Requests are validated before they reach a provider.** An empty `messages` array or an invalid `role` fails fast with a `422` at the API boundary, instead of burning a real call against every provider in the fallback chain (each rejecting it identically) before surfacing as an opaque `502`.
- **A malformed 2xx is treated as a failure, not a crash.** An unexpected response shape from a provider — Ollama returning JSON without a `message` key, OpenAI returning an empty `choices` array — raises the same `ProviderError` a network failure would, so `FallbackRouter` falls back to the next provider instead of an unhandled `KeyError`/`IndexError` skipping the fallback chain entirely and surfacing as a raw `500`.
- **Retries are skipped on errors that won't change on retry.** A 4xx from a provider (other than 429) fails identically every time; the router falls back to the next provider immediately instead of burning `PROVIDER_RETRY_ATTEMPTS` retries and their backoff delay for no chance of a different outcome. See [decision 005](docs/decisions/005-retryable-vs-non-retryable-provider-errors.md).
- **Running with only some providers configured just works.** You don't need all three of `OPENAI_API_KEY`/`ANTHROPIC_API_KEY`/a running Ollama — an unset key skips that provider cleanly and falls back to the next one, rather than crashing the whole chain. (This one was a real bug, not a hypothetical: the OpenAI SDK raises at client-construction time on a missing key, which briefly meant an unset `OPENAI_API_KEY` took down every provider in the chain, not just OpenAI. See [decision 006](docs/decisions/006-unconfigured-providers-fail-fast-not-crash.md).)
- **A hung backend fails as fast as a down one.** Redis and Postgres connections have explicit, bounded connect/command timeouts (`REDIS_CONNECT_TIMEOUT_SECONDS`, `DATABASE_COMMAND_TIMEOUT_SECONDS`, etc.) rather than relying on library defaults that are either unbounded or too long for a request path — verified live against a non-routable address and a real `pg_sleep()` query. See [decision 007](docs/decisions/007-bounded-backend-timeouts.md).
- **String fields are capped to match their DB columns.** `team`, `model`, and `X-Request-ID` all flow into fixed-width Postgres columns; oversized values are rejected (or, for the diagnostic `X-Request-ID`, silently replaced) at the API boundary. This one's a real dev/prod parity trap: SQLite doesn't enforce `VARCHAR` length at all, so the bug was invisible against the default local/CI database and only reproduced against real Postgres. See [decision 008](docs/decisions/008-validate-string-lengths-at-the-boundary.md).

## 🔢 api versioning

Every client-facing route lives under `/v1/`; operational routes (`/healthz`, `/metrics`, `/admin/*`) aren't versioned since they're not part of the client contract. What counts as a breaking change, and what doesn't, is written down in [`docs/api-versioning.md`](docs/api-versioning.md).

## 🔒 security

- **No secrets in the repo.** Real credentials live only in a local `.env` (git-ignored). `.env.example` documents every variable with placeholder values.
- **`/v1/chat` fails closed.** If no client keys are configured, the endpoint refuses all requests (503) rather than serving openly.
- **Constant-time key comparison** (`hmac.compare_digest`) prevents timing attacks on API key checks.
- **Structured logging** is scrubbed of tokens, keys, and Authorization headers.
- **Security headers** (`X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`) are set on every response.
- **CORS is closed by default.** No `Access-Control-Allow-Origin` header at all unless `CORS_ALLOWED_ORIGINS` is explicitly set — this API is meant to be called server-to-server, not from arbitrary browser origins.
- **`pip-audit` runs in CI** (`make audit` locally) on every push, checking every pinned dependency against known CVE databases — a vulnerable transitive dependency fails the build instead of going unnoticed.
- Secret scanning is enabled on this repository (auto-enabled for public GitHub repos).
- Licensed under [MIT](LICENSE).

<div align="center">
<br/>
<img src="https://capsule-render.vercel.app/api?type=soft&color=0:00d9a3,100:0a0e27&height=120&section=footer&animation=twinkling" width="100%"/>
</div>
