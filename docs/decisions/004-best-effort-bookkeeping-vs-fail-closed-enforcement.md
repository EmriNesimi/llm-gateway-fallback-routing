# 4. Post-hoc bookkeeping is best-effort; pre-flight enforcement fails closed

## Context

Two different things touch Redis/the DB around a single `/v1/chat` request,
and they need opposite failure behavior:

- **Before** calling a provider: checking the rate limit and remaining
  budget (`enforce_rate_limit`, `enforce_budget` in `app/ratelimit/` and
  `app/budget/`).
- **After** a provider has already returned a response: writing the audit
  log row (`record_audit_log`) and recording the spend
  (`BudgetTracker.record_spend`).

Both touch the same Redis instance and the same DB. A live bug surfaced the
difference: with Redis unreachable, `record_spend` raising after a
successful provider call turned an already-obtained, already-billed chat
response into a `500` for the caller — discarding output that cost real
money to produce, over a failure in something that's purely bookkeeping.

## Decision

- **Pre-flight checks fail closed.** If Redis is unreachable when checking
  the rate limit or budget, the request is rejected (surfaces as a `500` via
  the global exception handler, not silently allowed through). Better to
  block traffic than let budget/rate-limit enforcement pass through
  unverified.
- **Post-hoc bookkeeping is best-effort.** `record_audit_log` and
  `BudgetTracker.record_spend` each catch their own failures internally, log
  loudly (with the request ID), and return normally. The caller already has
  their response; nothing about the bookkeeping path should be able to take
  it away from them.

## Why

The asymmetry isn't an inconsistency — it reflects that these two things
protect different failure modes. Pre-flight enforcement is the actual
safety mechanism (it's the reason `RATE_LIMIT_CAPACITY` and
`MONTHLY_BUDGET_USD_PER_KEY` mean anything); failing open there would mean
an outage silently disables cost controls at exactly the moment the
system is already degraded. Post-hoc bookkeeping is a record of what
already happened; failing loudly-but-non-fatally there means a
transient outage costs you one row in the audit log or one increment of
spend tracking, not a response your caller (and your OpenAI/Anthropic
bill) already paid for.

## Consequences

- A Redis or DB outage causes `/v1/chat` and `/v1/chat/stream` to reject
  new requests outright (fail closed on the pre-flight side) — which is
  the intended, conservative behavior for a system with budget
  enforcement as a design goal.
- A request that got past the pre-flight check and reached a provider will
  always return that provider's response to the caller, even if the
  audit log or spend recording fails immediately afterward.
- Spend tracking and the audit log can therefore under-count during an
  outage window — this is a known, accepted gap, not an oversight; the
  alternative (discarding successful, paid-for responses) is strictly
  worse.
