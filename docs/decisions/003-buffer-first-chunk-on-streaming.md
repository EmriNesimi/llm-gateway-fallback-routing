# 3. Streaming buffers the first chunk before committing to a provider

## Context

`/v1/chat/stream` has to make the same fallback decision as `/v1/chat` — if
the primary provider is down, fall back to the next one in the chain. But
streaming responses start sending bytes to the client immediately, and HTTP
doesn't let you un-send a response once headers and body have started
flowing.

## Decision

The router doesn't forward a provider's stream to the client chunk-by-chunk
from the first byte. It waits for the *first* chunk from a provider before
committing to it — if that first call fails outright (connection error,
non-2xx, timeout), the router falls back to the next provider and tries
again, all before the client has seen anything. Only once a provider has
proven it's actually going to respond does its stream get forwarded live.

## Why

Without this, a failure partway through a provider's stream — or worse, a
failure on the very first chunk — would either have to be silently
swallowed (client gets a truncated response with no explanation) or turned
into a mid-stream SSE error event with no fallback (client gets a partial
answer and then a failure, instead of a complete answer from a different
provider). Buffering just the first chunk is the cheapest point to still
catch "this provider isn't going to work" before the client has committed to
reading from it, while not buffering the *entire* response (which would
throw away streaming's whole latency benefit).

The tradeoff is a small added latency: the client waits for that first chunk
to arrive before seeing anything, rather than seeing bytes the instant the
TCP connection opens. In practice this is negligible compared to LLM
time-to-first-token, and it's the same latency the non-streaming endpoint
pays for its entire response.

## Consequences

- A provider failing before its first token still results in a clean
  fallback to the next provider — streaming and non-streaming have the same
  failure-recovery guarantee for anything up to "first token received."
- A provider failing *after* its first token has already been forwarded to
  the client cannot be silently retried — the client has already committed
  to that provider's output. This case surfaces as an `event: error` SSE
  event instead (see `_event_stream` in `app/main.py`), which is a strictly
  narrower failure window than treating any mid-stream failure as fatal.
- `app/routing/fallback.py`'s `chat_stream()` is necessarily more complex
  than `chat()` because of this — it has to hold back one chunk to inspect,
  where the non-streaming path just awaits the whole response.
