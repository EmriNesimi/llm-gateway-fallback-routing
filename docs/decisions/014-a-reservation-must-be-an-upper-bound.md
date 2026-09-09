# 014 — A reservation must be an upper bound on what the request can cost

## Context

Decision 011 built the lifetime ceiling on reserve-then-settle: claim the
worst case atomically before calling a provider, refund the surplus after.
Decision 012 closed the case where the worst case cannot be computed at all.

Both assume something neither states: that the number reserved is genuinely
the most the request could cost. A security review found two places where it
was not, and both had been there since the ceiling shipped.

**The output cap was reserved but never sent.** `_reserve_chain` computes the
worst case using `MAX_OUTPUT_TOKENS` (2048) when a caller sets no
`max_tokens`. OpenAI was then called with no `max_tokens` at all, so the model
could generate up to its own ceiling — many times 2048 for `gpt-4o-mini`. The
reservation bounded a limit the request never carried. `/v1/chat` and
`/v1/chat/stream` made this every request: both pass `params=None`, and
`_event_stream` had no `params` argument, so a native caller could not set
`max_tokens` even deliberately. Anthropic was never affected; it requires
`max_tokens` and always sent a default.

**Retries were reserved once and billed twice.** `FallbackRouter` retries the
*same* provider up to `provider_retry_attempts` more times when the error is
retryable, and a client-side read timeout is retryable. A provider that had
already generated a full completion when the timeout fired billed for it, then
billed again for the retry — while only the attempt that finally returned was
settled. The earlier attempts were spend with no reservation, no ledger entry
and no audit row.

## Decision

State the invariant, and hold it in both directions:

> **The amount reserved before a call must be greater than or equal to the
> most that call can cost, for every input the code permits — not for the
> case the author had in mind.**

Concretely:

- Whatever bound the reservation assumes, the provider is **sent** that bound.
  `DEFAULT_MAX_OUTPUT_TOKENS` lives in `app/providers/base.py` and is read by
  both the schema layer and the OpenAI adapter, with a test asserting they are
  the same number rather than two values that happen to agree today.
- The reservation covers **every attempt the code can make**, not one call.
  `_reserve_chain` multiplies by `provider_retry_attempts + 1`.

## Consequences

- OpenAI responses are capped at 2048 output tokens unless a caller asks for
  fewer. That is the limit the gateway already advertised and always charged
  against; it is now also the limit enforced.
- Reservations are larger, so concurrent admission is stricter. The surplus is
  refunded at settle, so nothing is over-charged — the only effect is that the
  ceiling refuses sooner under load, which is the correct direction for a
  control whose job is not overspending.
- The question to ask of any future change to the reservation path is not "is
  this figure about right" but "can any permitted input make the real cost
  exceed it". Both bugs above pass the first test and fail the second.
