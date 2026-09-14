# Load test results

A real run of `scripts/load_test.js` (k6, 10 VUs ramped over 50s) against a
local gateway instance, real Redis, and live provider keys — not a mock.

**Measured at `f88f658` (2026-09-14).** The previous run, at `db8af9e`
(2026-08-10), is kept below for comparison — it is what the request path cost
before the spend ceiling and everything built on it.

## 2026-09-14 — `f88f658`

```
checks_total.......: 3400    100.00% passed
http_req_failed....: 0.00%   0 out of 1700   (200s and 429s both count as "expected")
iterations.........: 1700    34.0/s

gateway_request_latency_ms (successful /v1/chat calls only):
  avg=2241ms  min=634ms  med=1331ms  p90=5551ms  p95=7795ms  max=10135ms

http_req_duration (every response, including the instant 429s):
  avg=137ms   min=5ms    med=44ms    p90=187ms   p95=432ms   max=10.13s
```

`/metrics` at the end of the run:

```
gateway_requests_total{status="success"} 44
gateway_requests_refused_total{reason="rate_limit"} 1657
gateway_provider_attempts_total{outcome="success",provider="openai"} 43
gateway_fallback_triggered_total 0
gateway_cost_usd_total{model="gpt-4o-mini",provider="openai"} 0.00026625
```

**Total cost of this run: $0.00027.** 43 of 1700 requests reached a provider;
the rate limiter refused the other 1657 before any outbound call.

### What changed since the previous run

**Served latency is roughly 3x worse** — median 684ms then, 1331ms now; p95
1092ms then, 7795ms now. That is the number this document existed to
re-measure, and it moved in the direction the staleness table predicted.

It is **not** safe to attribute all of it to the gateway. Confounders, stated
rather than buried:

- Provider-side variance. 43 samples is a small number, and `max` of 10.1s
  against a `med` of 1.3s says the tail is dominated by a handful of slow
  upstream responses rather than by anything systematic.
- The host was running Docker, Postgres and a full test suite during the run.
- `gateway_fallback_triggered_total` stayed at 0 and attempts equal successes,
  so no retries or fallbacks inflated these figures. Whatever the cause, it is
  not the router doing extra work.

What the gateway unambiguously does add per request is four Redis round-trips
(reserve and settle, on both ledgers) against an instance running
`appendfsync always` — a disk write per ledger update. `http_req_duration`'s
44ms median, which is dominated by the 1657 instant 429s, is the closest thing
here to a measurement of gateway overhead alone, and it is unchanged in
character from the previous run's 83ms p95.

### What this run did prove

- **The ceiling drops an exhausted provider before calling it.** OpenAI began
  the run with $0.027 of headroom and the reservation logic admitted exactly
  what fit, refusing nothing incorrectly and never overspending.
- **Both ledgers agree with each other and with the bill.** The per-key ledger
  moved $0.00026625; the provider ledger moved $0.00026625; the audit log
  records $0.00026775 across 45 requests. The conservation property that
  `tests/test_ledger_conservation.py` asserts against stubs holds against real
  Redis and a real provider.
- **Zero failures under sustained load**, as before: every one of 1700
  requests got a 200 or a 429, never a 5xx or a connection error.

## The previous run — `db8af9e` (2026-08-10)

## Staleness

> **These numbers predate the spend ceiling and everything built on it.**
> Superseded by the run above; kept as the before-picture.
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
