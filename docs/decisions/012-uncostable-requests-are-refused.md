# 012 — A request that cannot be costed is refused, not run

## Context

Decision 011 added a hard lifetime ceiling per provider, enforced by reserving
the worst-case cost of a request *before* the provider is called. That
reservation is what makes the ceiling hold: checking `spent < cap` and then
calling leaves room for one request to cost more than the headroom, and for
concurrent requests to all observe the same pre-call total.

The reservation is only as good as the number it reserves. `worst_case_cost_usd`
looked the model up in `_PRICING` and, on a miss, returned `0.0`.

A `$0` worst case means `reserve()` claims nothing. The request then runs with
no reservation at all — outside the ceiling entirely. The one control designed
to be un-bypassable had a bypass, reachable by a single missing table row.

`tests/test_pricing.py::test_every_routable_model_has_a_pricing_entry` fails the
build if any model reachable from a chain has no price, so this was not
reachable in practice. But "a guard elsewhere makes this unreachable" is a
statement about today's code, and the whole point of a spend ceiling is that it
holds when something else has already gone wrong.

An earlier revision made this path *warn* and continue. That was not enough: the
warning is emitted after the decision to proceed has already been made, and
lands in a log nobody is reading at the moment it matters.

## Decision

`worst_case_cost_usd` raises `UnpricedModelError` for a billable model with no
pricing entry. The honest answer to "how much could this cost" is *unknown*,
and for a control whose job is bounding spend, unknown has to mean no.

**The refusal is per provider, not per request.** `_reserve_chain` catches it
and drops that provider from the chain — exactly what it already does for a
provider at its ceiling. Fallback still runs, so a priced provider further down
serves the request normally. One missing table row should degrade the chain, not
take the gateway down.

Only when no billable provider is left, and the chain has no free hop, is the
request refused. That refusal is **`503`, not the `402`** used for exhausted
budget:

| | Out of budget | No pricing entry |
|---|---|---|
| Status | `402` | `503` |
| `error` | `provider budget exhausted` | `no pricing configured` |
| Whose problem | the caller's, resolved by waiting or raising the cap | the operator's |
| Will retrying help | eventually | never |

Ollama is exempt. It bills nothing, so "no price" is correct rather than
missing — raising for it would break the free fallback that exists for exactly
the case where the paid providers are gone.

## Consequences

- The ceiling can no longer be bypassed by a missing pricing entry. Any request
  that runs has had its worst case reserved.
- A pricing gap is now visible as a `503` naming the unpriced providers, instead
  of unmetered spend that shows up on a bill later.
- Adding a model to `app/routing/model_map.py` without a matching `_PRICING`
  entry degrades that provider rather than silently disabling cost control for
  it. The existing rot guard still fails the build first.
- `estimate_cost_usd` is unchanged and still returns `$0` with a warning. It
  runs *after* the response exists, where refusing is not an option — the money
  is already spent, and under-reporting is the only remaining failure mode.
