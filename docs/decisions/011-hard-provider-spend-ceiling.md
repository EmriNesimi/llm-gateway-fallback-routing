# 011 — A hard per-provider spend ceiling, separate from the per-key budget

## Context

`MONTHLY_BUDGET_USD_PER_KEY` looked like a spend control. It is one, but not
the one an operator running this against prepaid provider credit actually
needs, and the gap between the two isn't obvious from the name.

It caps spend **per client API key, per month**. That answers "is this caller
taking more than their share this month". It cannot answer "have we spent more
of my own money than I ever intended to", for three separate reasons:

- **It multiplies.** Six client keys at $1/month is $6/month, not $1. Issuing
  another key raises the ceiling, so `POST /admin/keys` silently mints new
  allowance.
- **It resets.** $1/month is $12/year. When the limit is a prepaid balance
  rather than a spending rate, a period is the wrong axis entirely.
- **It was checked, not reserved.** `enforce_budget` admitted any request
  where `spent < cap`, then called the provider. Nothing bounded what the
  admitted request could cost, and with a burst allowance of 20 every
  concurrent request observed the same pre-call total. Twenty requests could
  each pass a check against a budget with room for one.

## Decision

Add `PROVIDER_LIFETIME_BUDGET_USD`, enforced by
`app/budget/provider_budget.py`, as a **separate control with a different
job**. Both now exist; neither replaces the other.

| | `MONTHLY_BUDGET_USD_PER_KEY` | `PROVIDER_LIFETIME_BUDGET_USD` |
|---|---|---|
| Keyed by | client API key | upstream provider |
| Resets | monthly | never |
| Raised by more keys | yes | no |
| Protects | fair share between callers | the operator's actual balance |

Three properties make the new one a ceiling rather than a cap:

**Lifetime.** No period component in the Redis key, so it cannot reset. A test
asserts the key has no TTL, because an expiry added later would silently turn
the ceiling back into a cap.

**Reserve, then reconcile.** The request's worst-case cost is added atomically
with `INCRBYFLOAT` *before* the provider is called, and the surplus refunded
once the real cost is known. `INCRBYFLOAT` returns the post-increment total,
so two concurrent callers cannot both see room for the last dollar. Over the
ceiling, the reservation is handed straight back and the request refused — the
provider is never called, so nothing is spent. Verified: 20 concurrent
requests at $1 against a $4 ceiling admit exactly 4.

**Fails closed.** An unreachable Redis refuses the request. Being unable to
prove there is budget left is not the same as having budget left, and this is
the last thing standing between a bug and a real bill. Consistent with
decision 004's treatment of pre-flight checks.

Out-of-budget providers are dropped from the chain *before* the circuit
breaker is consulted, because a half-open trial request is still a billable
call.

## What this depended on

The ceiling was worthless until two other things were fixed, and both are
worth recording because neither was visible from the budget code:

**Request size was unbounded.** `content`, `messages` and `max_tokens` had no
upper limit, so one admitted request could cost more than the entire ceiling —
$16.77 against claude-opus-5 at the caps that existed. Decision 008 had capped
`model` and `team` because they hit fixed-width database columns; `content`
fell outside that reasoning precisely because it never touches the database,
while being the field that actually costs money. The *total* across messages
is the bound that matters, since a per-message cap is multiplied by the
message count. Worst case is now $0.13.

**Streamed spend wasn't recorded if the client disconnected.** `record_spend`
sat after the `async for` loop. Starlette closes the generator on disconnect,
raising `GeneratorExit` at the `yield`, so every line after the loop was
skipped — cost estimation, spend recording and the audit row alike. The
provider had already generated and been billed for those tokens; Redis
recorded `$0.00` forever. Any cap, of any kind, was unreachable: open a
stream, read one chunk, hang up, repeat. Both stream paths now catch
`GeneratorExit` and `CancelledError` and charge an estimate derived from the
characters actually streamed, since the usage totals only ever arrive in a
final chunk that a disconnect guarantees never comes.

## Alternatives considered

**Just lower `MONTHLY_BUDGET_USD_PER_KEY`.** Cheapest, and wrong: it still
multiplies per key, still resets, and still admits concurrent requests that
each pass the same check.

**A single global ceiling across all providers.** Simpler, one number.
Rejected because the balances are genuinely separate — exhausting one
provider's credit should fail over to the other, not halt the gateway.
Per-provider ceilings preserve the fallback chain's whole purpose.

**Track spend in Postgres instead of Redis.** Durable across a Redis flush,
which is a real weakness of the chosen design. Rejected for the request path:
it adds a synchronous write before every provider call, and the audit log
already provides the durable record. A ceiling that resets when Redis is
flushed is a documented limitation, not an accident.

## Consequences

- Two budget controls now exist with similar names. The table above is the
  distinction; `app/budget/provider_budget.py`'s docstring restates it.
- Every reservation **must** be settled, on success and failure alike. An
  unsettled reservation is a permanent hole in the ceiling. Each exit path in
  `app/main.py` settles explicitly rather than relying on a `finally`, because
  the success path's bookkeeping happens after the `try` block and a blanket
  refund there would undo it.
- Spend on an aborted stream is an *estimate*, biased high. Under-charging is
  how a ceiling quietly stops being one.
- Flushing Redis resets the ledger. For a deployment where that matters, back
  it with Postgres.
