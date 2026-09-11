# 015 — Both spend ledgers reserve; neither only checks

## Context

There are two spend ledgers, and they answer different questions. The lifetime
per-provider ceiling (`PROVIDER_LIFETIME_BUDGET_USD`) bounds the operator's
actual money. The monthly per-key budget (`MONTHLY_BUDGET_USD_PER_KEY`) bounds
one caller's share of it.

Decision 011 built the first one on reserve-then-settle, and said plainly why:
checking `spent < cap` and then calling is a time-of-check/time-of-use race,
because concurrent requests all read the same pre-call total and all pass.

That fix was applied to one ledger and not the other. `enforce_budget` read
the per-key total in a FastAPI dependency and the real cost was recorded only
after the provider had answered, with nothing claimed in between — the exact
pattern decision 011 exists to describe. A caller's overshoot was bounded by
`RATE_LIMIT_CAPACITY` (default 20) times the per-request cost, not by the cap.

The asymmetry was known and documented as accepted risk, on the grounds that
it could not spend more of the operator's money — the provider ceiling reserves
and is unaffected. That reasoning is correct and it is also not the point: a
cap that does not hold is a cap that cannot be relied on for the one thing it
is named after, and "it is documented" is how an accepted risk becomes an
assumption.

## Why it was not simply fixed before

Reserving requires the worst-case cost, and that depends on the resolved chain
and model. A FastAPI dependency runs too early to know either. So the fix is
not "add a reserve call to `enforce_budget`" — it is moving per-key enforcement
to the one place in the request that has the information.

## Decision

> **Every spend control reserves atomically before the call and settles after.
> A control that only checks is not enforcement.**

Concretely:

- `BudgetTracker` gains `reserve()` / `settle()` mirroring `ProviderBudget`,
  built on the same `INCRBYFLOAT`-and-hand-back pattern. `record_spend()`
  survives as `settle(reserved=0)`.
- Both reservations are taken in `_reserve_chain`, the one point that knows the
  resolved chain, and both are released in `_settle_chain`, the one exit that
  every success and every failure path reaches.
- The per-key reservation is the **sum** of the chain's provider reservations,
  not the largest. Fallback can bill more than one hop: a provider that
  generated a completion and then timed out is charged for it, and the next
  provider is charged again. Decision 014's rule — a reservation is an upper
  bound for every input the code permits, not for the case the author had in
  mind — makes the sum the only defensible figure.
- `enforce_budget` keeps its check. It is now a fast refusal in front of the
  reservation rather than the enforcement itself, and it is worth keeping: it
  turns an exhausted key away before routing, and the total it reads now
  includes live reservations, so it reads the number the reservation will
  actually be measured against.

## Alternatives considered

**Leave it, per the original reasoning.** Genuinely defensible while this
deployment has one key and one user. Rejected because the argument for it is
entirely about the *current* deployment, and nothing in the code enforces that
this stays true — the second key issued silently invalidates the premise. The
race is also cheap to close now and expensive to reason about later.

**Reserve the largest hop rather than the sum.** Tighter, and wrong under
exactly the condition that makes reservations necessary: more than one hop can
bill for a single request.

**Give `enforce_budget` a fixed pessimistic reservation.** Avoids restructuring
but has to assume the most expensive chain for every request, so cheap requests
on cheap chains would be refused against budget they would never have used.

## Consequences

- A caller can no longer exceed its monthly cap under concurrency. The
  remaining overshoot is bounded by rounding, not by the burst allowance.
- Admission is stricter while requests are in flight, because a reservation is
  the worst case and the surplus only comes back at settle. Nothing is
  over-charged; the cap simply refuses sooner under load, which is the correct
  direction for a control whose job is not overspending.
- `X-Budget-Remaining-USD` now nets off live reservations, so a caller with
  requests in flight sees less headroom than their settled spend implies. That
  is the honest number: the headroom is genuinely claimed.
- The two ledgers are now the same shape, which is the property that made this
  bug hard to see. One reserved and one checked, with no rule saying which was
  correct.
