// Soak/load test for the gateway's /v1/chat endpoint under sustained
// concurrent traffic — the same request path scripts/demo.sh exercises once,
// here run at volume to see what the fallback chain, rate limiter, and
// circuit breaker actually do under pressure.
//
// Requires: k6 (https://k6.io), the gateway running, and a valid client key.
//
// Usage:
//   GATEWAY_URL=http://localhost:8000 CLIENT_KEY=<your key> k6 run scripts/load_test.js
//
// Watch /metrics (or the Grafana dashboard at :3000) while this runs —
// gateway_provider_attempts_total and gateway_fallback_triggered_total are
// the interesting series here.
import http from "k6/http";
import { check, sleep } from "k6";
import { Rate, Trend } from "k6/metrics";

const GATEWAY_URL = __ENV.GATEWAY_URL || "http://localhost:8000";
const CLIENT_KEY = __ENV.CLIENT_KEY;

if (!CLIENT_KEY) {
  throw new Error("Set CLIENT_KEY to a valid gateway client API key before running this.");
}

const fallbackRate = new Rate("gateway_fallback_rate");
const requestLatency = new Trend("gateway_request_latency_ms");

// Without this, k6's built-in http_req_failed metric treats every non-2xx
// response as a failure — including the 429s this test deliberately
// provokes by bursting past the rate limit. Telling k6 that 200 and 429 are
// both "expected" keeps that metric meaningful: it only trips on genuine
// failures (5xx, connection errors), not on the rate limiter doing its job.
http.setResponseCallback(http.expectedStatuses(200, 429));

export const options = {
  // Ramp up, hold, ramp down — enough to push past the default
  // RATE_LIMIT_CAPACITY (20) per key and see 429s show up, then see the
  // token bucket recover once load drops off.
  stages: [
    { duration: "10s", target: 10 },
    { duration: "30s", target: 10 },
    { duration: "10s", target: 0 },
  ],
  thresholds: {
    // Not "everything must succeed" — 429s under intentional overload are
    // expected and correct. This just guards against outright 5xx storms.
    http_req_failed: ["rate<0.5"],
  },
};

// Half the traffic through each endpoint. /v1/chat/completions is what an
// existing application actually points at, and it carries more per-request
// work than /v1/chat — schema with extra optional fields, sampling params,
// the OpenAI response envelope — so load-testing only the native shape was
// measuring the path fewer people use.
const ENDPOINTS = ["/v1/chat", "/v1/chat/completions"];

export default function () {
  const endpoint = ENDPOINTS[__ITER % ENDPOINTS.length];
  const res = http.post(
    `${GATEWAY_URL}${endpoint}`,
    JSON.stringify({
      model: "default",
      messages: [{ role: "user", content: "Say hi in five words." }],
    }),
    {
      headers: {
        "X-API-Key": CLIENT_KEY,
        "Content-Type": "application/json",
      },
    }
  );

  check(res, {
    "status is 200 or 429": (r) => r.status === 200 || r.status === 429,
    // 402 would mean a provider hit its lifetime ceiling mid-run — valid
    // behaviour, but it means the rest of the run is measuring refusals
    // rather than routing, so it's worth seeing rather than passing quietly.
    "not budget-exhausted": (r) => r.status !== 402,
  });

  if (res.status === 200) {
    requestLatency.add(res.timings.duration);
    const body = JSON.parse(res.body);
    fallbackRate.add(body.provider !== "openai");
  }

  sleep(0.1);
}
