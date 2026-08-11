# API versioning policy

Every route lives under `/v1/` (`/v1/chat`, `/v1/chat/stream`). Everything
outside that prefix — `/healthz`, `/readyz`, `/metrics`, `/admin/*` — is
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

## What doesn't require a version bump

- Adding a new optional request field with a backwards-compatible default.
- Adding a new field to a response body (clients that don't know about it
  should ignore it, per normal JSON client behavior).
- Adding new virtual model names to the fallback map.
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
