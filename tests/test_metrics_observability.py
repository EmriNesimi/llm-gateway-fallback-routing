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
from app.routing.fallback import (
    AllProvidersFailedError,
    FallbackRouter,
    publish_circuit_state,
)


class StubProvider:
    def __init__(self, name, fail=False):
        self.name = name
        self._fail = fail

    async def chat(self, model, messages, params=None):
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

    with pytest.raises(AllProvidersFailedError):
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

    with pytest.raises(AllProvidersFailedError):
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

    with pytest.raises(AllProvidersFailedError):
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


def test_alert_rules_only_reference_metrics_that_exist():
    """Same failure mode as the dashboard, one step worse.

    A Grafana panel querying a renamed metric renders empty. An alert rule
    querying one simply never fires — so the alert that exists to tell you the
    budget is gone stays silent, and silence is indistinguishable from healthy.
    """
    import pathlib
    import re

    rules = pathlib.Path("deploy/prometheus/alerts.yml").read_text()
    exposed = {m.name for m in REGISTRY.collect()}

    referenced = set()
    for name in re.findall(r"\bgateway_[a-z_]+", rules):
        for suffix in ("_bucket", "_count", "_sum", "_total"):
            if name.endswith(suffix):
                name = name[: -len(suffix)]
                break
        referenced.add(name)

    missing = sorted(referenced - exposed)
    assert not missing, (
        f"alerts.yml references {missing}, which the app doesn't expose —"
        " those alerts would never fire, and never firing looks like healthy"
    )


def test_metric_objects_carry_the_labels_the_dashboard_queries():
    assert CIRCUIT_STATE._labelnames == ("provider",)
    assert PROVIDER_LATENCY._labelnames == ("provider",)
    assert COST_USD._labelnames == ("provider", "model")
    assert TOKENS._labelnames == ("provider", "model", "direction")


def test_target_down_alert_matches_the_scrape_job_name():
    """GatewayTargetDown selects on job="llm-gateway".

    That label isn't published by the app — Prometheus attaches it from the
    scrape config's job_name. Rename the job there and the alert keeps
    evaluating against a series that no longer exists, so it never fires. The
    metric-name guard above can't catch this: `up` isn't a gateway_ metric.
    """
    import pathlib
    import re

    rules = pathlib.Path("deploy/prometheus/alerts.yml").read_text()
    scrape = pathlib.Path("deploy/prometheus/prometheus.yml").read_text()

    alerted_on = set(re.findall(r'up\{job="([^"]+)"\}', rules))
    configured = set(re.findall(r"job_name:\s*(\S+)", scrape))

    assert alerted_on, "GatewayTargetDown no longer selects on a job label"
    assert alerted_on <= configured, (
        f"alerts.yml watches job(s) {sorted(alerted_on - configured)} that"
        f" prometheus.yml does not scrape (it defines {sorted(configured)}) —"
        " the alert would never fire"
    )


def test_every_refusal_reason_is_a_distinct_label():
    """The reasons must stay separable, because each one has a different fix:
    wait (rate_limit), raise the caller's cap (key_budget_exhausted), top up
    the provider balance (provider_budget_exhausted), add a pricing entry
    (no_pricing_configured), or investigate whoever is hammering key issuance
    (admin_rate_limit).

    Collapsing any two would mean paging on a graph that cannot say which.
    """
    import pathlib
    import re

    sources = [
        pathlib.Path("app/main.py"),
        pathlib.Path("app/ratelimit/dependency.py"),
        pathlib.Path("app/budget/dependency.py"),
    ]
    used = set()
    for path in sources:
        for line in path.read_text().splitlines():
            if "reason=" in line:
                # Both literals of a conditional live on the same line, e.g.
                # reason="a" if cond else "b" — so scan the line, not just the
                # text immediately after `reason=`.
                used.update(re.findall(r'"([a-z_]+)"', line))

    assert used == {
        "rate_limit",
        "admin_rate_limit",
        "key_budget_exhausted",
        "provider_budget_exhausted",
        "no_pricing_configured",
    }, f"refusal reasons changed: {sorted(used)}"


def test_alert_rules_select_on_refusal_reasons_that_exist():
    """GatewayRefusingUnpricedRequests filters on reason="no_pricing_configured".

    That label is a string in the app, not something the metric declares — so
    renaming the reason leaves the alert evaluating a series that is never
    produced. It would sit green forever while the condition it exists for
    happens, which is the failure mode this whole family of guards is for.
    """
    import pathlib
    import re

    rules = pathlib.Path("deploy/prometheus/alerts.yml").read_text()
    selected = set(re.findall(r'gateway_requests_refused_total\{reason="([a-z_]+)"', rules))
    assert selected, "no alert selects a refusal reason — the guard would pass vacuously"

    emitted = set()
    for path in (
        pathlib.Path("app/main.py"),
        pathlib.Path("app/ratelimit/dependency.py"),
        pathlib.Path("app/budget/dependency.py"),
    ):
        for line in path.read_text().splitlines():
            if "reason=" in line:
                emitted.update(re.findall(r'"([a-z_]+)"', line))

    unknown = sorted(selected - emitted)
    assert not unknown, (
        f"alerts.yml selects refusal reason(s) {unknown} that no code emits"
        f" (emitted: {sorted(emitted)}) — those alerts would never fire"
    )


def test_every_metric_is_graphed_or_alerted():
    """The reverse of the two guards above.

    Those catch a dashboard or alert pointing at a metric that does not exist.
    This catches a metric that exists and nothing looks at — which is not
    harmless: it is cardinality and code carried on the belief that someone is
    watching. gateway_provider_budget_spent_usd was exactly that, published
    on every ledger read and shown nowhere, while being the more authoritative
    spend figure (the shared Redis ledger, not one process's counter).
    """
    import pathlib
    import re

    declared = set(
        re.findall(r'"(gateway_[a-z_]+)"', pathlib.Path("app/observability/metrics.py").read_text())
    )
    assert declared, "no metrics declared — the guard would pass vacuously"

    watched_text = (
        pathlib.Path("deploy/grafana/dashboards/gateway-overview.json").read_text()
        + pathlib.Path("deploy/prometheus/alerts.yml").read_text()
    )

    def base(name: str) -> str:
        for suffix in ("_bucket", "_count", "_sum", "_total"):
            if name.endswith(suffix):
                return name[: -len(suffix)]
        return name

    watched = {base(n) for n in re.findall(r"gateway_[a-z_]+", watched_text)}

    orphans = sorted(m for m in declared if base(m) not in watched)
    assert not orphans, (
        f"{orphans} are exported but appear in no dashboard panel and no alert"
        " rule — nobody would ever see them"
    )


def _alert_names_and_runbooks() -> list[tuple[str, str | None]]:
    import pathlib
    import re

    text = pathlib.Path("deploy/prometheus/alerts.yml").read_text()
    out, current = [], None
    seen: dict[str, str | None] = {}
    for line in text.splitlines():
        m = re.match(r"\s*- alert: (\w+)", line)
        if m:
            current = m.group(1)
            seen[current] = None
        m = re.search(r"runbook_url:\s*\"([^\"]+)\"", line)
        if m and current:
            seen[current] = m.group(1)
    out = list(seen.items())
    assert out, "no alerts found — the guard would pass vacuously"
    return out


def test_every_alert_links_to_a_runbook_section():
    """Alertmanager puts runbook_url in the notification, so the instructions
    arrive with the page. An alert without one sends someone to go and find
    them while the thing is on fire."""
    missing = sorted(name for name, url in _alert_names_and_runbooks() if not url)
    assert not missing, f"alert(s) {missing} have no runbook_url annotation"


def test_no_runbook_link_points_at_a_missing_section():
    """The dead-link case, which is worse than no link: it reads as help and
    lands on a page with nothing about this alert."""
    import pathlib
    import re

    runbook = pathlib.Path("docs/runbook.md").read_text()
    # GitHub anchors a `## Heading` as #heading, lowercased.
    anchors = {h.strip().lower() for h in re.findall(r"^## (.+)$", runbook, re.M)}
    assert anchors, "runbook has no sections — the guard would pass vacuously"

    broken = []
    for name, url in _alert_names_and_runbooks():
        fragment = url.split("#", 1)[1] if url and "#" in url else ""
        if fragment not in anchors:
            broken.append(f"{name} -> #{fragment}")

    assert not broken, (
        f"runbook links with no matching section: {broken}."
        f" Sections present: {sorted(anchors)}"
    )


def test_the_runbook_has_no_sections_for_alerts_that_no_longer_exist():
    """A section for a deleted alert is stale advice that reads as current."""
    import pathlib
    import re

    runbook = pathlib.Path("docs/runbook.md").read_text()
    # Only headings shaped like an alert name. The runbook also carries
    # operational sections ("Working with the ledger") that are not alerts and
    # must not be mistaken for stale ones.
    documented = {
        h.strip()
        for h in re.findall(r"^## (.+)$", runbook, re.M)
        if re.fullmatch(r"[A-Z][A-Za-z]+", h.strip())
    }
    alerts = {name for name, _ in _alert_names_and_runbooks()}

    orphaned = sorted(documented - alerts)
    assert not orphaned, f"runbook documents alert(s) that no longer exist: {orphaned}"
