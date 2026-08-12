# 5. Provider errors carry a retryable flag; 4xx skips remaining retries

## Context

`FallbackRouter` retries a failing provider up to `provider_retry_attempts`
times before moving to the next provider in the chain. That's the right
behavior for a timeout or a dropped connection — the same request might
succeed a moment later. It's the wrong behavior for a 400 (invalid model,
malformed request) or a 401 (bad API key): that request will fail exactly
the same way on every retry, so burning the configured retries against it
just adds `provider_retry_attempts * provider_retry_backoff_seconds` of
pure latency before falling back to the next provider — for zero chance of
a different outcome.

This wasn't hypothetical: a request with an empty `messages` array reached
OpenAI before schema validation caught that case (see the `ChatRequest`
validation added alongside this), got a clean 400 back, and the router
retried it against OpenAI anyway before eventually falling through
Anthropic and Ollama too.

## Decision

`ProviderError` carries a `retryable: bool` (default `True`). Each provider
adapter (OpenAI, Anthropic, Ollama) inspects the underlying SDK/HTTP
exception's status code — via the shared `is_retryable_status_code()` in
`app/providers/base.py` — and sets `retryable=False` for any 4xx other than
429 (rate limited, which *is* worth retrying). `FallbackRouter` checks this
flag: on a non-retryable error it logs and moves straight to the next
provider, skipping the remaining retry attempts and their backoff delay
entirely.

## Why

429 and 5xx are the provider saying "try again, this might work" — a
capacity or transient-availability signal. Every other 4xx is the provider
saying "this exact request is wrong" — a signal about the *request*, not
about whether the provider is currently working. Retrying only makes sense
for the first kind. Conflating them (retrying everything, as before) meant
a config mistake or a validation gap upstream was punished with the full
retry latency on top of the actual error, on every single provider in the
chain.

## Consequences

- A non-retryable failure against one provider still falls back to the
  next provider in the chain — the fix is scoped to *retry count*, not to
  whether fallback happens at all. A model that's simply unknown to OpenAI
  might still be perfectly valid on Anthropic.
- The circuit breaker's `record_failure()` still fires once per provider
  attempt regardless of `retryable` — a provider that's misconfigured
  (wrong model name, bad key) is, from the breaker's perspective, exactly
  as unusable right now as one that's actually down, and should be skipped
  the same way once it's failed enough times.
- Provider adapters that can't cheaply classify a failure (a raw
  `httpx.ConnectError`, an SDK error with no status code) default to
  `retryable=True`, preserving today's behavior rather than guessing.
