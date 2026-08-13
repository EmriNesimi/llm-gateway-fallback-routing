# 6. An unconfigured provider is a clean fallback skip, not a crash

## Context

Providers are only instantiated once, lazily, the first time `build_router()`
needs them (`app/routing/dependencies.py`'s `_get_provider`). If
`OPENAI_API_KEY` is unset, the code previously still constructed
`OpenAIProvider(api_key="")`. The OpenAI SDK's client raises
`OpenAIError("Missing credentials...")` *at construction time* for an empty
key — not when a request is actually sent. That exception happened while
building the fallback chain itself, before `FallbackRouter` even existed,
so it wasn't a `ProviderError` and nothing in the retry/fallback machinery
ever saw it. The result: every request to a virtual model whose chain
includes OpenAI returned a raw `500`, and Anthropic/Ollama — regardless of
whether they were configured and healthy — never got a chance to serve the
request at all. Running with only some providers configured (a very normal
setup — e.g. "I only have an Anthropic key" or "I'm just running Ollama
locally") silently broke the gateway's one job.

## Decision

`_get_provider` checks for a configured API key *before* constructing the
real SDK-backed provider. If it's missing, it returns an
`UnconfiguredProvider` instead — a `BaseProvider` implementation that raises
a normal, non-retryable `ProviderError` the instant `chat()`/`chat_stream()`
is called, with zero network calls and zero risk of an SDK-specific
construction-time surprise.

## Why

"Not configured" and "failing" should look identical to everything
downstream of provider selection — `FallbackRouter`, the circuit breaker,
the metrics — none of which should need special-case handling for a
provider that simply isn't set up. Making `UnconfiguredProvider` speak the
same `ProviderError` protocol as a real failure means the *existing*
fallback and non-retryable-error logic (decision 005) handles it for free:
skip immediately, try the next provider, no wasted retries.

The alternative considered — passing a placeholder non-empty string as the
API key so the SDK constructs without raising, then letting the eventual
API call fail with a real 401 — was rejected because it's fragile in
exactly the way that caused this bug: it relies on a specific SDK's
current behavior (raises now, might not always) instead of an explicit,
inspectable check for what we actually know: the key isn't set.

## Consequences

- A gateway configured with only some providers behaves correctly: unset
  providers are skipped, configured ones still work — no configuration
  combination should ever produce a hard crash instead of a clean fallback
  or a `502` with a per-provider reason.
- An unconfigured provider still participates in circuit breaker tracking.
  After `CIRCUIT_BREAKER_FAILURE_THRESHOLD` requests, its breaker opens and
  it's skipped entirely rather than even reaching `UnconfiguredProvider`'s
  raise — a nice side effect, not something relied on for correctness.
- This class of bug (SDK client construction failing on bad/missing
  credentials) is specific to the OpenAI SDK today; Anthropic's client
  doesn't raise on an empty key. `UnconfiguredProvider` is used for both
  regardless, since skipping a wasted network call that's guaranteed to
  401 is a win either way, not just a correctness fix for OpenAI.
