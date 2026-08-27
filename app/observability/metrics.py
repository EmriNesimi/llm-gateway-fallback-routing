from prometheus_client import Counter, Gauge, Histogram

REQUEST_COUNT = Counter(
    "gateway_requests_total",
    "Total chat requests handled by the gateway",
    ["status"],
)

REQUEST_LATENCY = Histogram(
    "gateway_request_latency_seconds",
    "End-to-end latency of chat requests",
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
