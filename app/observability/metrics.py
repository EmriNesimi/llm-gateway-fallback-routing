from prometheus_client import Counter, Gauge, Histogram

REQUEST_COUNT = Counter(
    "gateway_requests_total",
    "Total chat requests handled by the gateway",
    ["status"],
)

# A refused request never reaches REQUEST_COUNT: it raises out of a
# dependency or out of _reserve_chain, before the handler records anything.
# So the two outcomes an operator most wants to see — "we are turning traffic
# away" and why — were visible only in logs, and the dashboards showed a
# perfectly healthy, quietly idle gateway.
REQUESTS_REFUSED = Counter(
    "gateway_requests_refused_total",
    "Requests turned away before any provider was called, by reason",
    ["reason"],
)

# A 500 reaches no other counter. REQUEST_COUNT is incremented inside the
# handlers, so anything that raises before or around them — a Redis outage
# making the rate limiter throw, a bug in a dependency — is recorded nowhere:
# gateway_requests_total simply stops rising, which on a dashboard is
# indistinguishable from nobody calling.
#
# Deliberately unlabelled. The obvious label is the request path, and that is
# attacker-controlled on a 404, so it would be an open invitation to blow up
# cardinality.
UNHANDLED_EXCEPTIONS = Counter(
    "gateway_unhandled_exceptions_total",
    "Requests that failed with an unhandled exception (HTTP 500)",
)

# Explicit buckets, because the prometheus_client defaults stop at 10s and
# this gateway's own PROVIDER_REQUEST_TIMEOUT_SECONDS is 30 (Ollama's is 60).
# With the defaults, everything slower than 10s fell into +Inf: p50, p95 and
# p99 all read the same, and no alert threshold above 10s was expressible.
#
# Chosen to be dense where a fast path lives and sparse where a slow one does,
# and to bracket both timeouts so a request that dies at its limit is
# distinguishable from one that merely took a while.
_LATENCY_BUCKETS = (0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 15.0, 20.0, 30.0, 45.0, 60.0, 90.0)

REQUEST_LATENCY = Histogram(
    "gateway_request_latency_seconds",
    "End-to-end latency of chat requests",
    buckets=_LATENCY_BUCKETS,
)

PROVIDER_ATTEMPTS = Counter(
    "gateway_provider_attempts_total",
    "Attempts against each provider, labeled by outcome",
    ["provider", "outcome"],  # outcome: success | error
)

FALLBACK_TRIGGERED = Counter(
    "gateway_fallback_triggered_total",
    "Number of times a request fell back past the primary provider",
)

# A breaker opening is the single most operationally interesting thing this
# gateway does, and until now it existed only in process memory and a log line
# — nothing to alert on, nothing to put on a dashboard.
CIRCUIT_STATE = Gauge(
    "gateway_circuit_state",
    "Circuit breaker state per provider (0 = closed, 1 = half-open, 2 = open)",
    ["provider"],
)

# REQUEST_LATENCY measures the whole request, including retries and every
# fallback hop, so it can't answer "is Anthropic slower than OpenAI right now"
# — the question you actually have when deciding a chain's order.
PROVIDER_LATENCY = Histogram(
    "gateway_provider_latency_seconds",
    "Latency of a single provider attempt, excluding retries and fallback",
    ["provider"],
    buckets=_LATENCY_BUCKETS,
)

# Cost only reached the audit database before, which means no alert could fire
# on spend and no dashboard could show it. Labeled by provider and model
# rather than by team: those are a small fixed set drawn from the routing
# table, whereas API keys are unbounded and would eventually blow up
# cardinality. Per-team spend stays a query against the audit log, which is
# the right tool for an unbounded dimension.
COST_USD = Counter(
    "gateway_cost_usd_total",
    "Estimated spend in USD, as priced by app/budget/pricing.py",
    ["provider", "model"],
)

TOKENS = Counter(
    "gateway_tokens_total",
    "Tokens processed, by direction",
    ["provider", "model", "direction"],  # direction: input | output
)


# The ceiling itself, not just what has been spent against it.
# gateway_cost_usd_total is a counter — it says how much has gone, never how
# much is left, and "left" is the number worth alerting on. Without this the
# only warning you get is a 402 after the budget is already gone.
PROVIDER_BUDGET_SPENT = Gauge(
    "gateway_provider_budget_spent_usd",
    "Lifetime USD spent against a provider's hard ceiling",
    ["provider"],
)

PROVIDER_BUDGET_REMAINING = Gauge(
    "gateway_provider_budget_remaining_usd",
    "Lifetime USD still available before a provider is refused",
    ["provider"],
)

# A reservation that was claimed and could not be handed back, because Redis
# failed between the two. The ledger stays permanently short by that much with
# no request behind it — the ceiling quietly gets smaller.
#
# It needs a metric rather than only a log line for the same reason the two
# gauges above exist: the failure is in the safe direction (it refuses more,
# never less), which is exactly what makes it easy to never notice. Spend looks
# normal, headroom just erodes, and Redis holds the only copy of the number.
# `ledger` is which one it leaked out of, because the fix differs: the provider
# ceiling needs a deliberate correction, a per-key leak rights itself when the
# period rolls over.
BUDGET_RESERVATION_LEAKED = Counter(
    "gateway_budget_reservation_leaked_usd_total",
    "USD claimed as a reservation that could not be refunded",
    ["ledger"],  # provider | key
)
