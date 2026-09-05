# API versioning policy

Every route lives under `/v1/` (`/v1/chat`, `/v1/chat/stream`,
`/v1/chat/completions`, `/v1/models`). Everything
outside that prefix — `/healthz`, `/readyz`, `/metrics`, and every `/admin/*`
route including `/admin/keys`, `/admin/audit-log` and `/admin/key-events` — is
operational or administrative, not part of the versioned client contract, and
can change without a version bump.

## What counts as a breaking change to `/v1/`

- Removing or renaming a field in a request or response body.
- Changing a field's type or meaning (e.g. `cost_usd` switching from dollars
  to cents).
- Changing the meaning of an existing HTTP status code for a given
  situation.
- Removing a virtual model name from `app/routing/model_map.py` that clients
  may already be passing as `"model"`.

## Refusal statuses are part of the contract

`/v1/` routes turn requests away in four ways, and the status is the only
thing a client can branch on:

| Status | Situation | Client should |
|---|---|---|
| `429` | rate limit | back off; `Retry-After` says how long |
| `402` | budget exhausted — the caller's monthly cap, or the operator's lifetime provider ceiling | stop; retrying cannot help until a cap moves |
| `502` | every provider in the chain failed | retry later; this is transient |
| `503` | no pricing configured, so the cost cannot be bounded | stop; only an operator deploy fixes it |

Adding a **new** refusal status for a **new** situation is additive and does
not require a version bump — a client that doesn't know it treats it as an
unexpected error, which is the correct behaviour anyway. Changing which status
an **existing** situation returns is breaking, because clients branch on it:
moving the un-costable refusal from `503` to `402` would make callers wait for
a balance that was never the problem.

## What doesn't require a version bump

- Adding a new optional request field with a backwards-compatible default.
- Adding a new field to a response body (clients that don't know about it
  should ignore it, per normal JSON client behavior).
- Adding new virtual model names to the fallback map.
- Adding a whole new route under `/v1/`. `/v1/chat/completions` shipped this
  way. A route with no callers has no compatibility obligations, which is why
  it can reject unroutable models outright while `/v1/chat` can't — see
  [decision 009](decisions/009-unknown-model-handling.md).
- Adding new response headers (`X-RateLimit-*`, `X-Budget-*`, `X-Request-ID`
  all shipped this way).
- Internal routing/fallback/retry behavior changes that don't alter the
  request/response contract — e.g. changing `provider_retry_attempts`'
  default, or adding a provider to the `default` chain.

## When a breaking change is unavoidable

Introduce `/v2/` alongside `/v1/` rather than mutating `/v1/` in place, and
keep `/v1/` serving until it's explicitly deprecated. This repo hasn't needed
a `/v2/` yet — the policy exists so that if it does, the decision of "is this
breaking" isn't made ad hoc in a PR description.
