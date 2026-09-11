# Load test results

A real run of `scripts/load_test.js` (k6, 10 VUs ramped over 50s) against a
local gateway instance, real Redis, and a live OpenAI key — not a mock.

**Measured at `db8af9e` (2026-08-10). Not re-run since.** Read the staleness
note below before quoting any number here.

## Staleness

> **These numbers predate the spend ceiling and everything built on it.**
> Treat them as a floor for the routing path, not a current benchmark. They
> are kept rather than deleted because the routing and fallback behaviour they
> measured has not changed — only the accounting around it, which has only
> ever been added to.

Everything on the request path that did not exist when this was measured, in
the order it landed:

| Change | Cost per request | Where |
|---|---|---|
| Per-provider budget reservation | two Redis round-trips per billable provider (reserve, then settle) | [decision 011](decisions/011-hard-provider-spend-ceiling.md) |
| Request-size bounds | validation over the message list | `app/schemas.py` |
| Un-costable refusal | a pricing lookup that can reject before any provider is called | [decision 012](decisions/012-uncostable-requests-are-refused.md) |
| `/v1/chat/completions` | a second endpoint, and half the traffic the script now generates | `app/main.py` |
| Durable ledger | `appendfsync always` — a disk write per ledger update | [decision 013](decisions/013-the-spend-ledger-is-persisted.md) |
| Reservations cover every retry | larger reservations, so stricter admission under load | [decision 014](decisions/014-a-reservation-must-be-an-upper-bound.md) |
| Per-key budget reservation | two more Redis round-trips per request, on the same ledger | [decision 015](decisions/015-both-spend-ledgers-reserve.md) |

The last row is the newest and the one this document has never reflected at
all: the per-key cap used to be a single `GET` before the call and an
`INCRBYFLOAT` after, and is now a reserve/settle pair like the provider
ceiling.

### Why it has not been re-run

`scripts/load_test.js` drives real traffic to real providers. A re-run spends
actual money against `PROVIDER_LIFETIME_BUDGET_USD`, which is a lifetime
ceiling that does not reset — so it is a deliberate act for the repository
owner, not routine maintenance. The number in this document is worth less than
the budget a re-run would consume.

### What a re-run should capture

- Both endpoints, since the script already generates both.
- The reservation overhead specifically. It is the largest addition to the
  request path and the one these numbers most obviously lack — now four Redis
  round-trips per request on a two-provider chain, where there were none.
- The commit it was measured at, replacing the pin at the top of this file, so
  the next staleness assessment starts from a fact rather than a guess.
- Ideally a paired run with the providers stubbed, which isolates the
  gateway's own overhead from the upstream round trip and costs nothing. The
  absolute latencies would not be comparable to the run below, but the
  delta that this table is all about would be.

```
checks_total.......: 3067    61.0/s
checks_succeeded...: 100.00% 3067 out of 3067
http_req_failed....: 0.00%   0 out of 3067   (200s and 429s both count as "expected")

gateway_request_latency_ms (successful /v1/chat calls only):
  avg=718ms  min=433ms  med=684ms  p90=919ms  p95=1092ms  max=1559ms
```

`/metrics` at the end of the run:

```
gateway_requests_total{status="success"} 85
gateway_provider_attempts_total{outcome="success",provider="openai"} 85
gateway_fallback_triggered_total 0
```

## What this shows

- **Zero failures under sustained load.** Every one of 3067 requests across
  10 concurrent VUs got either a `200` (served) or a `429` (rate limited) —
  never a `5xx` or connection error. The gateway degrades to "no" under
  pressure, not "broken."
- **The rate limiter is doing almost all of the work.** Only 85 of 3067
  requests actually reached a provider — the rest were correctly rejected at
  the token bucket before ever making an outbound call, per-key, exactly as
  `RATE_LIMIT_CAPACITY`/`RATE_LIMIT_REFILL_PER_SEC` are configured to do.
  That's the intended shape of this test: a single API key bursting past its
  budget should get throttled, not queued or dropped.
- **OpenAI stayed healthy for the whole run** (`gateway_fallback_triggered_total`
  stayed at `0`), so this run didn't exercise the fallback-to-Anthropic path —
  that needs a run where the primary provider is deliberately failing
  (wrong API key, or a network block) to see `gateway_fallback_triggered_total`
  actually increment.
- **Latency on served requests is dominated by the OpenAI round trip**
  (~700ms average), not by gateway overhead — `http_req_duration` (the raw
  HTTP timing k6 measures, including 429s which return near-instantly) has a
  p95 of 83ms, two orders of magnitude below the p95 for actual chat
  completions.

## Reproducing this

```bash
brew install k6   # or see https://k6.io/docs/get-started/installation/
make up           # or: uvicorn app.main:app --reload, with Redis running separately
# issue a client key via POST /admin/keys, then:
GATEWAY_URL=http://localhost:8000 CLIENT_KEY=<key> k6 run scripts/load_test.js
```

Watch the Grafana dashboard (`:3000`) live while it runs — this is exactly
the traffic pattern `deploy/grafana/dashboards/gateway-overview.json` is
built to visualize.
