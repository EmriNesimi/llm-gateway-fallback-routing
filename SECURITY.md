# Security

This is a portfolio project, not a hosted service. There is no deployment to
compromise — but it handles real provider credentials and real money, so the
threat model is written down rather than assumed.

## Reporting

Open a [private security advisory](https://github.com/EmriNesimi/llm-gateway-fallback-routing/security/advisories/new)
rather than a public issue. There is no SLA; this is a personal project.

## What this gateway is trying to protect

Two things, in this order:

1. **The operator's provider balances.** The gateway holds OpenAI and
   Anthropic credentials, so a bug or an unauthorised caller spends real
   money. Controls: a per-key monthly budget, a hard per-provider lifetime
   ceiling reserved atomically before each call, request size bounds, and a
   token-bucket rate limiter — all enforced before a provider is reached, all
   failing closed if Redis is unreachable. See
   [decision 011](docs/decisions/011-hard-provider-spend-ceiling.md).
2. **The credentials themselves.** Client keys are stored only as HMAC
   hashes, in the database and in Redis key names alike. Provider keys live in
   a git-ignored `.env` and are never logged, echoed in an error body, or
   written to the audit log.

## What it is not designed for

- **Multi-tenant isolation.** Client keys are separated by budget and rate
  limit, not by data. Any key that can reach `/admin` can mint or revoke any
  other.
- **Public exposure without a reverse proxy.** There is no TLS termination and
  no WAF. `docker-compose.yml` binds every port to `127.0.0.1` deliberately.
- **A durable spend ledger.** It lives in Redis; flushing Redis resets it.
  A documented limitation, not an oversight.

## Notes for anyone reading the code

- `/metrics` publishes exactly how much has been spent and on what. It is
  unauthenticated unless `METRICS_TOKEN` is set.
- Provider errors are logged in full but never returned to callers — upstream
  bodies carry key prefixes and organisation IDs.
- A placeholder API key (the `.env.example` value left in place) is detected at
  startup and treated as unset, because a fake key is worse than a missing one:
  it builds a real client that 401s on every call while the router silently
  falls past it.
