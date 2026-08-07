<div align="center">

<img src="https://capsule-render.vercel.app/api?type=waving&color=0:0a0e27,50:0f3443,100:00d9a3&height=200&section=header&text=LLM%20GATEWAY&fontSize=54&fontColor=00ff9d&fontAlignY=35&desc=%3E%20route%20/%20fallback%20/%20throttle%20/%20audit%20/%20observe&descAlignY=55&descSize=18&descColor=7dffcf&animation=fadeIn" width="100%"/>

<img src="https://readme-typing-svg.demolab.com?font=Fira+Code&weight=500&size=16&pause=900&color=00E5A0&background=0A0E27&center=true&vCenter=true&width=820&height=130&lines=%24+curl+-X+POST+%2Fv1%2Fchat+-H+%22Authorization%3A+Bearer+...%22;%5Bgateway%5D+routing+request+%E2%86%92+openai%3Agpt-4o-mini;%5Bopenai%5D+429+rate_limited+%E2%86%92+falling+back...;%5Banthropic%5D+200+OK+%E2%86%90+response+served+in+412ms;%5Bbreaker%5D+openai+circuit+OPEN+%E2%80%94+cooling+down+30s;%5Badmin%5D+POST+%2Fadmin%2Fkeys+%E2%86%92+team%3Ademo-team+key+issued;%5Baudit%5D+row+written%3A+team%3Ddemo-team+cost%3D%240.0031+412ms;%5Bmetrics%5D+fallback_triggered_total%2B%2B+%7C+scraped+by+prometheus" alt="terminal typing animation" />

<br/><br/>

[![CI](https://github.com/EmriNesimi/llm-gateway-fallback-routing/actions/workflows/ci.yml/badge.svg)](https://github.com/EmriNesimi/llm-gateway-fallback-routing/actions/workflows/ci.yml)
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
| Observability | OpenTelemetry, Prometheus |
| Dashboards | Grafana |
| Providers | OpenAI, Anthropic, Ollama |

<img src="https://capsule-render.vercel.app/api?type=rect&color=0:00d9a3,100:0a0e27&height=3&width=100%25" width="100%"/>

## 🚧 build log

Phase 4 (resilience) complete ✅ — next up: production polish (retries on the HTTP client level, alerting, deploy configs).

- [x] Project scaffold, config, security foundations
- [x] Provider adapters (OpenAI / Anthropic / Ollama) + fallback chain
- [x] Redis-backed rate limiting & per-key monthly budgets
- [x] OpenTelemetry tracing + Prometheus metrics
- [x] Grafana dashboards (via docker compose)
- [x] Circuit breaker per provider
- [x] Retry with backoff before falling back
- [x] Streaming support with fallback-before-first-chunk
- [x] Admin API for teams/keys + audit log
- [x] Tests for rate limiter, budget tracker, metrics, circuit breaker, fallback/retry, streaming, admin API, audit log, health endpoint
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
GET    /admin/keys                            → [{"id", "team", "created_at", "revoked"}, ...]
DELETE /admin/keys/{id}                        → revokes the key immediately
```

Pass the admin secret via `X-Admin-Key`. Like the client-facing auth, this fails closed (`503`) if `ADMIN_API_KEY` isn't set, rather than being left open.

Every `/v1/chat` and `/v1/chat/stream` request — success or failure — is written to an **audit log** (SQLite by default, Postgres via `DATABASE_URL` in production): timestamp, hashed API key, team, model, provider, outcome, tokens, cost, and latency. That's the "why did this fail at 2am" record — keys issued through the admin API get their team attributed automatically; keys from the legacy `GATEWAY_API_KEYS` env var are logged under `team: "unlinked"`.

## 📊 observability

- **`GET /metrics`** — Prometheus-format metrics: request count/latency, per-provider attempt outcomes, fallback rate.
- **Tracing** — set `OTEL_EXPORTER_OTLP_ENDPOINT` to send traces to any OTLP collector. Each provider attempt gets its own span with provider/model/attempt attributes. If unset, or nothing is listening there, the app still runs fine — you'll just see a harmless retry warning in the logs.
- **Dashboards** — `docker compose up` brings up Prometheus (`:9090`) scraping the gateway and Grafana (`:3000`, default login `admin`/`admin`) ready to point at it.

## 🚀 getting started

```bash
git clone https://github.com/EmriNesimi/llm-gateway-fallback-routing.git
cd llm-gateway-fallback-routing

cp .env.example .env   # fill in your real API keys — .env is git-ignored
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

uvicorn app.main:app --reload
```

Visit `http://localhost:8000/docs` for the interactive API docs, or `http://localhost:8000/healthz` for a health check.

### running lint & tests locally

```bash
ruff check .
pytest -q --cov=app --cov-report=term-missing
```

Same commands CI runs on every push/PR to `main`.

### with docker

```bash
docker compose up --build
```

Spins up the gateway, Redis, Prometheus, and Grafana together. Add `postgres` to `DATABASE_URL` in `.env` (`postgresql+asyncpg://gateway:changeme@postgres:5432/gateway`) to back the admin API/audit log with the bundled Postgres service instead of SQLite.

### 🎬 run the guided demo

With the gateway up and `ADMIN_API_KEY` set, `scripts/demo.sh` walks the whole system end-to-end — issuing a key, routing a real request, tripping the rate limiter, and pulling the audit trail back out:

```bash
DEMO_ADMIN_KEY=<your ADMIN_API_KEY> ./scripts/demo.sh
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

## 🔒 security

- **No secrets in the repo.** Real credentials live only in a local `.env` (git-ignored). `.env.example` documents every variable with placeholder values.
- **`/v1/chat` fails closed.** If no client keys are configured, the endpoint refuses all requests (503) rather than serving openly.
- **Constant-time key comparison** (`hmac.compare_digest`) prevents timing attacks on API key checks.
- **Structured logging** is scrubbed of tokens, keys, and Authorization headers.
- Dependency and secret scanning are enabled on this repository.

<div align="center">
<br/>
<img src="https://capsule-render.vercel.app/api?type=soft&color=0:00d9a3,100:0a0e27&height=120&section=footer&animation=twinkling" width="100%"/>
</div>
