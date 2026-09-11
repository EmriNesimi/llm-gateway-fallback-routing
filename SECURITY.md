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
   money. Controls: a per-key monthly budget and a hard per-provider lifetime
   ceiling, both reserved atomically before each call, plus request size bounds
   and a token-bucket rate limiter — all enforced before a provider is reached,
   all failing closed if Redis is unreachable. See
   [decision 011](docs/decisions/011-hard-provider-spend-ceiling.md) and
   [decision 015](docs/decisions/015-both-spend-ledgers-reserve.md).

   The ceiling is only as good as the number it reserves, so a request whose
   cost cannot be computed is **refused rather than run**. A model with no
   entry in `app/budget/pricing.py` used to produce a `$0` worst case, which
   reserved nothing and let the request run outside the ceiling entirely — a
   bypass in the control that is supposed not to have one. That provider is
   now dropped from the chain; an entirely unpriced chain returns `503`. See
   [decision 012](docs/decisions/012-uncostable-requests-are-refused.md).

   Every refusal is counted in `gateway_requests_refused_total`, labelled by
   reason. Refusals raise before the request counter is touched, so without it
   a gateway turning all traffic away is indistinguishable from an idle one.
2. **The credentials themselves.** Client keys are stored only as HMAC
   hashes, in the database and in Redis key names alike. Provider keys live in
   a git-ignored `.env` and are never logged, echoed in an error body, or
   written to the audit log.

## What it is not designed for

- **Multi-tenant isolation.** Client keys are separated by budget and rate
  limit, not by data. Any key that can reach `/admin` can mint or revoke any
  other.
- **Throttled credential guessing on the client API.** The rate limiter is
  keyed on the caller's API key, so it only runs once a key has been accepted.
  An invalid key is rejected before the bucket is touched, and each guess costs
  one indexed database lookup. The admin API is deliberately the other way
  round — rate limited ahead of the key check — because that key mints client
  keys.

  Not mirrored on the client surface because throttling before authentication
  means a single shared bucket for all unauthenticated traffic, which lets one
  bad client starve every good one. Client keys are high-entropy; the trade
  favours availability. Worth revisiting behind a reverse proxy that can supply
  a trustworthy per-client identity. See
  [decision 016](docs/decisions/016-no-pre-auth-rate-limit-on-the-client-api.md).

- **Bounded storage.** `audit_log` and `admin_audit_log` grow by one row per
  request and per key operation, and nothing prunes them. On the default
  SQLite file that is a file that only gets larger; on Postgres it is a table
  that does. Neither is a problem at this project's volume, but a deployment
  keeping this running would need a retention policy — and deleting rows is a
  deliberate decision, since the audit log is the record of what was spent and
  by whom.

- **Public exposure without a reverse proxy.** There is no TLS termination and
  no WAF. `docker-compose.yml` binds every port to `127.0.0.1` deliberately.
- **A tamper-proof spend ledger.** It lives in Redis, which now persists to
  disk (`appendonly`) and survives a restart — until this batch it did not,
  and every `docker compose down` silently reset the lifetime ceiling to zero.
  Anyone who can reach Redis can still flush it, or delete the volume, and the
  ceiling starts over. The password and loopback bind are what stand in the
  way; there is no second copy of the number.

## Notes for anyone reading the code

- `/metrics` publishes exactly how much has been spent and on what. It is
  unauthenticated unless `METRICS_TOKEN` is set.
- Provider errors are logged in full but never returned to callers — upstream
  bodies carry key prefixes and organisation IDs.
- A placeholder API key (the `.env.example` value left in place) is detected at
  startup and treated as unset, because a fake key is worse than a missing one:
  it builds a real client that 401s on every call while the router silently
  falls past it.
