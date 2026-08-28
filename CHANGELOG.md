# Changelog

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
