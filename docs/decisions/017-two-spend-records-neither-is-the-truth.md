# 017 — Two spend records, and neither one is the truth

## Context

Spend is written down twice, independently, by design. The ledger in Redis
is what the ceiling enforces against (decisions 011, 015). The audit log in
the database is the per-request record — who, what, how much, with a
`request_id`. Neither reads the other.

Decisions 004 and 013 each made one of them best-effort in a different way:
bookkeeping failures after a paid-for response are swallowed rather than
turned into a 500, and the ledger lives only in Redis with no second copy.
Both were the right call, and together they mean the two records *can*
disagree — and the code had no opinion about what disagreement meant.

Then, in one week, real data showed every way they can go wrong:

- **The ledger read $3.97 for OpenAI. The audit log read $0.0003.** Stranded
  reservations from bugs since fixed. The ceiling was $0.027 from locking a
  provider out over money that was never spent.
- **The ledger read $0.007 for anthropic. The audit log read $5.92.** The test
  suite had been deleting the production ledger key on every run — so a
  lifetime ceiling was quietly resetting to zero, which reads as "nothing spent
  yet".
- **The audit log's $5.92 was itself fake.** 146 rows from a stub router
  streaming into the production database, because the DB isolation fixture
  was not autouse while the Redis one was. The provider's dashboard showed
  the balance untouched.

Three failures, three different stores implicated, and in each case the
number that looked authoritative was the wrong one. Nothing in the code or the
docs said which to believe, so each was resolved by a person comparing all
three by hand and reasoning about which had been corrupted.

## Decision

> **Neither store is the truth. Agreement between them is the evidence, and
> disagreement is a diagnostic to be run, not a number to be picked.**

Concretely:

- `make reconcile` compares the two per provider and classifies the gap by
  direction. It exits non-zero on any gap, so it can gate a deploy or run on a
  schedule. The runbook routes every budget page through it before any other
  action.
- The direction of the gap names the fault. Ledger high is stranded
  reservations (the `BudgetReservationLeaked` shape) and the fix is a
  correction down. Ledger low means the ceiling has become larger than the
  spend says — the one direction this control may never fail in — and it means
  the ledger was reset or spend bypassed it.
- The provider's own billing page remains the arbiter when the two disagree.
  It is the only record the gateway cannot have written to.
- Test isolation for *both* stores is autouse. A test that forgets to ask for
  isolation does not fail; it writes to production. That asymmetry is what put
  fake rows in the audit log.

## Alternatives considered

**Make one store authoritative and derive the other.** Recomputing the ledger
from the audit log on startup would close the "ledger reset" case. Rejected
for now: it makes the ceiling depend on a database write that decision 004
deliberately made best-effort, and a request whose audit row was lost would
then also escape the ceiling. It would also have hidden the OpenAI case —
the audit log was right there, but the point was that nobody checked.

**A single store.** Move the ledger into Postgres, one synchronous write per
request. Decision 013 already weighed this and kept Redis for latency; this
week's findings do not change that trade, they add a check on top of it.

**Trust the ledger, since it is what is enforced.** This is the status quo,
and it is how a $4 ceiling nearly shut off a provider that had spent thirty
cents of a dollar.

## Consequences

- A budget alert is now a prompt to reconcile, not a number to act on.
- The tolerance is $0.00001, from observation: 46 real requests left the
  stores $0.000002 apart through Redis re-parsing a decimal string on every
  `INCRBYFLOAT`. Below the cheapest priced request by an order of magnitude.
- The known-bad history stays where it is. 146 fake audit rows are still in
  the database, identifiable and documented, and `make reconcile` will keep
  flagging anthropic until they are purged. Deleting audit rows is a deliberate
  act by this project's own rules and is left to the operator.
- The question to ask of any future spend-path change is no longer "does the
  ledger move correctly" but "do both records still agree afterwards".
