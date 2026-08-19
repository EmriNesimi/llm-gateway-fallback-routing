"""The metrics added so the dashboard can answer the questions this gateway exists for.

Before these, /metrics could tell you a request happened and roughly how long
it took, but not which provider was slow, what anything cost, or whether a
breaker was open — the last of which the README advertises in its header
animation while nothing exported it.
"""

import pytest
from prometheus_client import REGISTRY

from app.observability.metrics import CIRCUIT_STATE, COST_USD, PROVIDER_LATENCY, TOKENS
from app.providers.base import ChatResponse, ProviderError
from app.routing.circuit_breaker import CircuitBreaker
from app.routing.fallback import FallbackRouter, publish_circuit_state


class StubProvider:
    def __init__(self, name, fail=False):
        self.name = name
        self._fail = fail

    async def chat(self, model, messages):
        if self._fail:
            raise ProviderError(f"{self.name} is down")
        return ChatResponse(
            content="ok", provider=self.name, model=model, input_tokens=3, output_tokens=5
        )


def _sample(name, **labels):
    value = REGISTRY.get_sample_value(name, labels)
    return 0.0 if value is None else value


# --------------------------------------------------------------------------
# Circuit breaker state
# --------------------------------------------------------------------------


def test_gauge_tracks_the_breaker_through_a_full_cycle():
    # A real cooldown, so the open state is actually observable: with a zero
    # cooldown the breaker flips to half-open on the very next state read, and
    # publish_circuit_state performs one.
    breaker = CircuitBreaker(failure_threshold=2, cooldown_seconds=60)

    publish_circuit_state("cycle-provider", breaker)
    assert _sample("gateway_circuit_state", provider="cycle-provider") == 0  # closed

    breaker.record_failure()
    breaker.record_failure()
    publish_circuit_state("cycle-provider", breaker)
    assert _sample("gateway_circuit_state", provider="cycle-provider") == 2  # open

    # Collapse the cooldown rather than sleeping through it.
    breaker._cooldown_seconds = 0
    publish_circuit_state("cycle-provider", breaker)
    assert _sample("gateway_circuit_state", provider="cycle-provider") == 1  # half-open

    breaker.record_success()
    publish_circuit_state("cycle-provider", breaker)
    assert _sample("gateway_circuit_state", provider="cycle-provider") == 0


def test_state_values_ascend_with_severity():
    """A Grafana threshold or alert rule should be able to say "> 0" and mean
    "something is wrong", which only holds if the ordering is deliberate."""
    healthy = CircuitBreaker(failure_threshold=1, cooldown_seconds=60)
    broken = CircuitBreaker(failure_threshold=1, cooldown_seconds=60)
    broken.record_failure()

    publish_circuit_state("ordering-healthy", healthy)
    publish_circuit_state("ordering-broken", broken)

    assert _sample("gateway_circuit_state", provider="ordering-healthy") < _sample(
        "gateway_circuit_state", provider="ordering-broken"
    )


@pytest.mark.asyncio
async def test_router_publishes_breaker_state_without_being_asked():
    breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=60)
    router = FallbackRouter(
        [(StubProvider("router-gauge", fail=True), "m", breaker)], retry_attempts=0
    )

    with pytest.raises(Exception):
        await router.chat([])

    # The breaker tripped during a normal request; nothing had to poll it.
    assert _sample("gateway_circuit_state", provider="router-gauge") == 2


# --------------------------------------------------------------------------
# Per-provider latency
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_successful_attempts_are_timed_per_provider():
    breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=1)
    router = FallbackRouter([(StubProvider("timed-ok"), "m", breaker)], retry_attempts=0)

    await router.chat([])

    assert _sample("gateway_provider_latency_seconds_count", provider="timed-ok") == 1


@pytest.mark.asyncio
async def test_failed_attempts_are_timed_too():
    """A provider that fails slowly is a different problem from one that fails
    fast; timing only successes would hide the difference."""
    breaker = CircuitBreaker(failure_threshold=9, cooldown_seconds=1)
    router = FallbackRouter(
        [(StubProvider("timed-fail", fail=True), "m", breaker)], retry_attempts=0
    )

    with pytest.raises(Exception):
        await router.chat([])

    assert _sample("gateway_provider_latency_seconds_count", provider="timed-fail") == 1


@pytest.mark.asyncio
async def test_each_provider_in_a_chain_is_timed_separately():
    """The whole point: REQUEST_LATENCY covers retries and every fallback hop,
    so it can't compare providers against each other."""
    breaker_a = CircuitBreaker(failure_threshold=9, cooldown_seconds=1)
    breaker_b = CircuitBreaker(failure_threshold=9, cooldown_seconds=1)
    router = FallbackRouter(
        [
            (StubProvider("chain-primary", fail=True), "m", breaker_a),
            (StubProvider("chain-backup"), "m", breaker_b),
        ],
        retry_attempts=0,
    )

    await router.chat([])

    assert _sample("gateway_provider_latency_seconds_count", provider="chain-primary") == 1
    assert _sample("gateway_provider_latency_seconds_count", provider="chain-backup") == 1


@pytest.mark.asyncio
async def test_retry_backoff_is_not_counted_as_provider_latency():
    """The backoff sleep lives inside the except block, so a naive `finally`
    would bill a deliberate delay to the provider and make a healthy-but-
    retried provider look slow."""
    breaker = CircuitBreaker(failure_threshold=9, cooldown_seconds=1)
    router = FallbackRouter(
        [(StubProvider("backoff", fail=True), "m", breaker)],
        retry_attempts=1,
        retry_backoff_seconds=0.25,
    )

    with pytest.raises(Exception):
        await router.chat([])

    total = _sample("gateway_provider_latency_seconds_sum", provider="backoff")
    assert total < 0.25, f"observed {total}s, which means the backoff was counted"


# --------------------------------------------------------------------------
# Cost and tokens
# --------------------------------------------------------------------------


def test_cost_and_tokens_are_recorded_per_provider_and_model():
    from app.main import _record_usage

    before = _sample("gateway_cost_usd_total", provider="billing", model="m1")
    _record_usage("billing", "m1", input_tokens=100, output_tokens=40, cost_usd=0.5)

    assert _sample("gateway_cost_usd_total", provider="billing", model="m1") == before + 0.5
    assert (
        _sample("gateway_tokens_total", provider="billing", model="m1", direction="input") == 100
    )
    assert (
        _sample("gateway_tokens_total", provider="billing", model="m1", direction="output") == 40
    )


def test_usage_is_not_recorded_when_no_provider_answered():
    """Every provider failing means there was no usage — recording a zero-cost
    sample against an empty provider label would just add a junk series."""
    from app.main import _record_usage

    _record_usage("", "m", input_tokens=5, output_tokens=5, cost_usd=1.0)

    assert (
        REGISTRY.get_sample_value("gateway_cost_usd_total", {"provider": "", "model": "m"})
        is None
    )


def test_new_metrics_are_actually_exposed():
    """Defining a metric that never gets registered is a silent no-op, and the
    dashboard would just render an empty panel."""
    exposed = {m.name for m in REGISTRY.collect()}

    assert "gateway_circuit_state" in exposed
    assert "gateway_provider_latency_seconds" in exposed
    assert "gateway_cost_usd" in exposed
    assert "gateway_tokens" in exposed


def test_dashboard_only_queries_metrics_that_exist():
    """A panel pointing at a renamed or deleted metric doesn't error — it just
    renders empty, which looks identical to "no traffic yet". This is the only
    thing that would tell you the difference."""
    import json
    import pathlib
    import re

    dashboard = json.loads(
        pathlib.Path("deploy/grafana/dashboards/gateway-overview.json").read_text()
    )
    exposed = {m.name for m in REGISTRY.collect()}

    referenced = set()
    for panel in dashboard["panels"]:
        for target in panel.get("targets", []):
            for name in re.findall(r"\bgateway_[a-z_]+", target["expr"]):
                # Strip the suffixes Prometheus derives from histograms and
                # counters; the base metric is what's registered.
                for suffix in ("_bucket", "_count", "_sum", "_total"):
                    if name.endswith(suffix):
                        name = name[: -len(suffix)]
                        break
                referenced.add(name)

    missing = sorted(referenced - exposed)
    assert not missing, (
        f"gateway-overview.json queries {missing}, which the app doesn't expose —"
        " those panels would render empty rather than fail"
    )


def test_metric_objects_carry_the_labels_the_dashboard_queries():
    assert CIRCUIT_STATE._labelnames == ("provider",)
    assert PROVIDER_LATENCY._labelnames == ("provider",)
    assert COST_USD._labelnames == ("provider", "model")
    assert TOKENS._labelnames == ("provider", "model", "direction")
