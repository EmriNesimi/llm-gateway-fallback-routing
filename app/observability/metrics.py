from prometheus_client import Counter, Histogram

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
