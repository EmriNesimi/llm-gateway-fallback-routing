# 009 — An unknown model is served, loudly, unless the operator opts into rejecting it

## Context

`app/routing/model_map.py` originally held exactly one chain, named `default`,
and `get_chain()` fell back to it for anything it didn't recognize:

```python
return FALLBACK_CHAINS.get(virtual_model, FALLBACK_CHAINS["default"])
```

So `model` was validated at the boundary — non-empty, capped at 255 characters
to match its audit column — written to the audit log as the *requested* model,
and then ignored by the router. A caller asking for `gpt-4o` got `gpt-4o-mini`,
and nothing in the response, the logs, or the audit row said so. The audit row
was actively misleading: it recorded a request for a model that never ran.

Splitting `default` into real tiers (`fast`, `smart`, `local`) makes that worse
rather than better. Once the names genuinely differ in cost and capability,
silently serving the wrong one stops being a cosmetic wart and becomes a
billing and quality problem — `smart` costs substantially more per token than
`fast`, and the substitution is unwanted in both directions.

## The constraint

Rejecting unknown models is the obviously correct end state, but
`docs/api-versioning.md` gets a veto here. It lists

> Changing the meaning of an existing HTTP status code for a given situation.

as a breaking change, and says breaking changes get a `/v2/` rather than
mutating `/v1/` in place. A request that returns `200` today returning `404`
tomorrow is exactly that. The policy exists precisely so this call isn't made
ad hoc in a PR description, so it applies to itself.

## Decision

Unknown models keep resolving to the `default` chain, but the substitution is
now reported three ways:

- **`X-Gateway-Chain`** on every `/v1/chat` and `/v1/chat/stream` response,
  naming the chain that actually served the request. Adding a response header
  is explicitly non-breaking under the versioning policy.
- **A warning log** carrying the request ID, the requested model, and the
  chain it fell back to.
- **`GET /v1/models`**, so a client can discover the routable names up front
  instead of finding out by not being told.

Operators who want the error can set `STRICT_MODEL_ROUTING=true`, which turns
an unroutable model into a `404` listing what *would* have worked. It defaults
to `false`, so a stock deployment's behavior is unchanged and `/v1` stays
intact.

## Alternatives considered

**Reject unknown models outright, in `/v1`.** The cleanest semantics, and what
the routing gap really deserves. Rejected because it breaks the versioning
policy on its first real test. A policy that gets suspended the first time it's
inconvenient isn't a policy, and the cost of honoring it here is one config
flag.

**Introduce `/v2/` with strict routing.** Policy-correct and permanent. Rejected
as disproportionate: it doubles the route tree, the schema set, and the test
surface to change one branch of one function, on a project where `/v1` has no
external consumers to protect yet. The flag makes the same behavior available
today and leaves `/v2/` on the table for when there's more than one reason to
cut it.

**Report the substitution and never offer strictness.** Non-breaking and
simple, but it permanently accepts that a caller asking for a model this
gateway can't route gets a different one. Fine as a default, not as the only
option.

## Consequences

- Default behavior is unchanged, so no version bump and no migration.
- The audit log can still record a `requested_model` that never ran — but
  `X-Gateway-Chain` and the warning now make that recoverable, and strict mode
  removes it entirely.
- A future `/v1/chat/completions` endpoint would have no existing callers and
  therefore no back-compat debt, so it could be strict from birth regardless of
  what `/v1/chat` does. Worth doing when it lands: the OpenAI-compatible
  surface is the one real clients point at, and it should never substitute
  silently.
- Every model added to a chain needs a price. `tests/test_pricing.py` fails the
  build otherwise, because an unpriced model costs $0.00 and escapes budget
  enforcement.
