# 🛡️ LLM Gateway with Fallback Routing

A production-style proxy that sits in front of OpenAI, Anthropic, and local Ollama models — giving every request automatic provider failover, per-team rate limits and budgets, and full observability out of the box.

> The infrastructure layer every team running LLMs in production eventually needs to build. This project builds it once, properly.

---

## Why this exists

Most AI demos show off a single model doing a clever trick. In production, the harder problem is **reliability**: providers rate-limit you, go down, or get expensive — and someone has to own the routing, the budget enforcement, and the "why did this request fail at 2am" answer. This gateway is that someone.

## What it does

- **Unified API** — one OpenAI-compatible endpoint in front of multiple providers, so clients don't need to know or care who's actually serving the request.
- **Automatic failover** — if the primary provider errors out, times out, or rate-limits you, the gateway retries against the next provider in the chain — including mid-stream.
- **Per-team rate limiting & budgets** — token-bucket limits and spend caps enforced in Redis, so one team can't blow the budget or the shared rate limit for everyone else.
- **Full observability** — every request is traced (OpenTelemetry) and measured (Prometheus), with Grafana dashboards for traffic, latency, error/fallback rate, and cost by team.
- **Security-first** — client API keys are hashed at rest, secrets never touch logs or the repo, and every credential is environment-based.

## Architecture

```
                     ┌─────────────────────┐
Client ─────────────▶│   FastAPI Gateway    │
                     │  ┌───────────────┐   │
                     │  │ Rate Limiter  │◀──┼── Redis (token bucket, budgets)
                     │  └───────┬───────┘   │
                     │  ┌───────▼───────┐   │
                     │  │ Router +      │   │
                     │  │ Fallback Chain│   │
                     │  └───────┬───────┘   │
                     └──────────┼───────────┘
                                │
              ┌─────────────────┼─────────────────┐
              ▼                 ▼                 ▼
          OpenAI           Anthropic           Ollama
                                │
                     ┌──────────▼───────────┐
                     │ OpenTelemetry traces │
                     │ Prometheus metrics   │──▶ Grafana dashboards
                     └──────────────────────┘
```

## Stack

| Layer | Choice |
|---|---|
| Backend | Python, FastAPI |
| Rate limiting / budgets | Redis (token bucket) |
| Observability | OpenTelemetry, Prometheus |
| Dashboards | Grafana |
| Providers | OpenAI, Anthropic, Ollama |

## Status

🚧 Actively in development. Current milestone: Redis-backed rate limiting & budgets (Phase 2).

- [x] Project scaffold, config, security foundations
- [x] Provider adapters (OpenAI / Anthropic / Ollama) + fallback chain
- [x] Redis-backed rate limiting & per-key monthly budgets
- [ ] OpenTelemetry tracing + Prometheus metrics
- [ ] Grafana dashboards
- [ ] Streaming support with mid-stream fallback
- [ ] Circuit breaker + retry/backoff
- [ ] Admin API for teams/keys + audit log
- [x] Tests for rate limiter, budget tracker, health endpoint
- [ ] CI

## Calling the API

`POST /v1/chat` requires a client API key (one of the values in `GATEWAY_API_KEYS`), passed as either:

```
Authorization: Bearer <key>
```
or
```
X-API-Key: <key>
```

Each key is independently rate-limited (token bucket: `RATE_LIMIT_CAPACITY` burst, `RATE_LIMIT_REFILL_PER_SEC` sustained) and budget-capped (`MONTHLY_BUDGET_USD_PER_KEY`, resets monthly). Exceeding the rate limit returns `429`; exceeding the budget returns `402`.

## Getting started

```bash
git clone https://github.com/EmriNesimi/llm-gateway-fallback-routing.git
cd llm-gateway-fallback-routing

cp .env.example .env   # fill in your real API keys — .env is git-ignored
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

uvicorn app.main:app --reload
```

Visit `http://localhost:8000/docs` for the interactive API docs, or `http://localhost:8000/healthz` for a health check.

### With Docker

```bash
docker compose up --build
```

## Security

- **No secrets in the repo.** Real credentials live only in a local `.env` (git-ignored). `.env.example` documents every variable with placeholder values.
- **`/v1/chat` fails closed.** If no client keys are configured, the endpoint refuses all requests (503) rather than serving openly.
- **Constant-time key comparison** (`hmac.compare_digest`) prevents timing attacks on API key checks.
- **Structured logging** is scrubbed of tokens, keys, and Authorization headers.
- Dependency and secret scanning are enabled on this repository.
