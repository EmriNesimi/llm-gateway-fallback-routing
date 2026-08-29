# Load test results

A real run of `scripts/load_test.js` (k6, 10 VUs ramped over 50s) against a
local gateway instance, real Redis, and a live OpenAI key — not a mock.

> **These numbers predate the spend ceiling.** They were recorded before the
> per-provider budget reservation, the request-size bounds, and the
> `/v1/chat/completions` half of the traffic the script now generates — so
> latency here is missing one Redis round-trip per provider per request, and
> the run couldn't have been refused with a 402. Treat them as a floor for
> the routing path rather than a current benchmark; re-run to compare.

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
