# 016 — The client API is rate limited after authentication, not before

## Context

The two authenticated surfaces order the rate limiter and the key check
differently, and the difference is deliberate.

On the admin API, `enforce_admin_rate_limit` is a **sibling** dependency listed
ahead of `require_admin_key`, keyed on a fixed label. A wrong admin key
consumes a token before it is rejected, so guessing is throttled.

On the client API, `enforce_rate_limit` takes the key as a **sub-dependency**:

```python
async def enforce_rate_limit(
    request: Request,
    response: Response,
    api_key: str = Depends(require_api_key),
) -> str:
```

FastAPI resolves sub-dependencies first, so `require_api_key` runs before the
bucket is touched. An invalid client key is therefore rejected with a 401
having consumed no rate limit, and each guess costs one indexed `SELECT`
against `ApiKeyRecord` — a key absent from `GATEWAY_API_KEYS` falls through to
a database lookup. Guessing client keys is unthrottled, and every attempt does
a little work on the server.

Read on its own that looks like the admin fix simply not being applied twice.
It is not.

## Decision

> **The client API stays rate limited after authentication. The asymmetry with
> the admin API is the correct answer to a different question, not an
> oversight.**

## Why not mirror the admin ordering

Throttling before authentication means throttling something other than the
caller's key, because at that point there is no trustworthy per-client
identity. This deployment sits behind no reverse proxy, so there is no
`X-Forwarded-For` worth believing and the remote address is whatever connected
to the loopback bind. The only available bucket is a single shared one for all
unauthenticated traffic.

A shared pre-auth bucket lets one misbehaving or malicious client exhaust the
allowance for every legitimate one — turning a guessing attempt into an outage.
That is a worse failure than the one it prevents:

- Client keys are high-entropy, so guessing is not a realistic path in.
- The cost per guess is one indexed lookup, not a provider call. Nothing
  billable is reached, because every spend control sits behind authentication.
- The admin key is the one worth protecting from volume guessing, and it *is*
  protected — it mints and revokes client keys, so the trust levels are not
  comparable.

## Alternatives considered

**A per-IP pre-auth bucket.** The right answer, and not available: without a
proxy supplying a trustworthy client identity, the "IP" is untrusted input and
keying a limiter on untrusted input gives an attacker a fresh bucket per
request, which is no limit at all.

**Cache negative key lookups.** Removes the per-guess database query without
the shared-bucket risk. Worth doing if the lookup ever shows up in a profile;
it does not today, and it adds a cache whose invalidation has to be right when
a key is revoked. The query is not the part of this that matters.

**A separate, larger pre-auth bucket.** Softens the starvation risk without
removing it, and adds a second limiter to reason about for a threat that is
already not realistic.

## Consequences

- Invalid client keys are not throttled. This is a known, accepted property,
  stated in `SECURITY.md` under "what it is not designed for" and in a comment
  at the ordering itself in `app/ratelimit/dependency.py`.
- **What would change the answer:** putting this behind a reverse proxy that
  supplies a trustworthy client identity. At that point a per-IP pre-auth
  bucket becomes both possible and clearly correct, and this decision should be
  revisited rather than inherited.
- The ordering is load-bearing in both files and reads as an accident in both.
  `tests/test_operational_surfaces.py` pins each direction, so flipping
  either one to match the other fails the build rather than silently changing
  the trade.
