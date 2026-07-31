<div align="center">

<img src="https://capsule-render.vercel.app/api?type=soft&color=0:0a0e27,50:0f3443,100:00d9a3&height=200&section=header&text=LLM%20GATEWAY&fontSize=54&fontColor=00ff9d&fontAlignY=35&desc=%3E%20route%20/%20fallback%20/%20throttle%20/%20observe&descAlignY=55&descSize=18&descColor=7dffcf&animation=twinkling" width="100%"/>

<img src="https://readme-typing-svg.demolab.com?font=Fira+Code&weight=500&size=16&pause=900&color=00E5A0&background=0A0E27&center=true&vCenter=true&width=780&height=110&lines=%24+curl+-X+POST+%2Fv1%2Fchat+-H+%22Authorization%3A+Bearer+...%22;%5Bgateway%5D+routing+request+%E2%86%92+openai%3Agpt-4o-mini;%5Bopenai%5D+429+rate_limited+%E2%86%92+falling+back...;%5Banthropic%5D+200+OK+%E2%86%90+response+served+in+412ms;%5Bbudget%5D+key+sk_live_%2A%2A%2A8f2c+%E2%80%94+%240.0031+recorded;%5Bmetrics%5D+fallback_triggered_total%2B%2B+%7C+scraped+by+prometheus" alt="terminal typing animation" />

<br/><br/>

[![Python](https://img.shields.io/badge/Python-3.12-0a0e27?style=for-the-badge&logo=python&logoColor=00ff9d&labelColor=0a0e27)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0a0e27?style=for-the-badge&logo=fastapi&logoColor=00ff9d&labelColor=0a0e27)](https://fastapi.tiangolo.com/)
[![Redis](https://img.shields.io/badge/Redis-0a0e27?style=for-the-badge&logo=redis&logoColor=00ff9d&labelColor=0a0e27)](https://redis.io/)
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
| Observability | OpenTelemetry, Prometheus |
| Dashboards | Grafana |
| Providers | OpenAI, Anthropic, Ollama |

<img src="https://capsule-render.vercel.app/api?type=rect&color=0:00d9a3,100:0a0e27&height=3&width=100%25" width="100%"/>

## 🚧 build log

Actively in development. Current milestone: resilience (Phase 4) 🟡

- [x] Project scaffold, config, security foundations
- [x] Provider adapters (OpenAI / Anthropic / Ollama) + fallback chain
- [x] Redis-backed rate limiting & per-key monthly budgets
- [x] OpenTelemetry tracing + Prometheus metrics
- [x] Grafana dashboards (via docker compose)
- [x] Circuit breaker per provider
- [x] Retry with backoff before falling back
- [ ] Streaming support with mid-stream fallback
- [ ] Admin API for teams/keys + audit log
- [x] Tests for rate limiter, budget tracker, metrics, circuit breaker, fallback/retry, health endpoint
- [ ] CI

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

## ⚡ circuit breaker

Each provider gets its own breaker: `CIRCUIT_BREAKER_FAILURE_THRESHOLD` consecutive failures trips it open, and it stays open (skipped, no network call) for `CIRCUIT_BREAKER_COOLDOWN_SECONDS` before a single trial request is allowed through (half-open). That trial succeeding closes the circuit; failing re-opens it. This keeps a dead provider from adding latency to every single request while it's down.

## 🔁 retry before fallback

A single transient error (a dropped connection, a momentary 5xx) doesn't need a full provider swap. Before the router gives up on a provider and moves to the next one in the chain, it retries the *same* provider `PROVIDER_RETRY_ATTEMPTS` times with a `PROVIDER_RETRY_BACKOFF_SECONDS` pause between tries. Only after retries are exhausted does the circuit breaker record a failure and the router falls back.

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

### with docker

```bash
docker compose up --build
```

Spins up the gateway, Redis, Prometheus, and Grafana together.

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
