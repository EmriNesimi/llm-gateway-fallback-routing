# 1. Circuit breaker gates retry, not the other way around

## Context

When a provider call fails, the gateway can do two things before giving up on
that provider: retry the same provider a few times, and/or skip it entirely
via the circuit breaker if it's been failing consistently. The order matters.

## Decision

The circuit breaker is checked *before* a provider is attempted at all. Retry
only happens *within* a provider that the breaker still considers healthy —
a provider that's already tripped the breaker is skipped straight to the next
one in the fallback chain, with no retries burned against it.

## Why

Retrying against a provider that's already down wastes exactly the thing the
gateway exists to protect: response latency. If OpenAI is returning 500s
consistently, `provider_retry_attempts` retries against it before falling
back to Anthropic just adds `provider_retry_backoff_seconds * attempts` of
pure waiting to every request, for a provider that was never going to
succeed. The breaker's whole job is to short-circuit exactly that waste once
it's seen enough consecutive failures to be confident the provider is
actually down, not just having a bad millisecond.

The tradeoff: a provider that's flapping (one bad request, then fine) will
occasionally get retried when the breaker hasn't tripped yet — that's
intentional. Retry handles transient noise; the breaker handles sustained
outages. Conflating them (e.g. having the breaker trip on the *first*
failure) would turn every blip into an unnecessary fallback, which defeats
the point of having a primary provider at all.

## Consequences

- A single flaky request against a healthy provider still gets retried in
  place, not immediately foisted onto a fallback provider.
- A provider that's genuinely down gets skipped with zero retry latency once
  the breaker trips, so the fallback chain moves fast when it matters most.
- The breaker's `failure_threshold` and `cooldown_seconds` are the real
  tuning knobs for "how much do we trust this provider right now" — retry
  count is purely about tolerating single-request noise.
