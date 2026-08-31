"""Keep the README's numbers honest.

The test count in the build log has gone stale four times, each caught by
someone reading it rather than by anything failing. A README that overstates
its own coverage is a small lie, but it's the first thing anyone reads about
this project — and it's exactly the class of drift the pricing-coverage and
dashboard-metrics guards already exist to prevent elsewhere.
"""

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
README = ROOT / "README.md"

# Below this, the run is a subset (a single file, a -k filter) rather than the
# whole suite, and the collected count means nothing. Enforce only on full
# runs — which is CI and `make test`, the two that matter.
_FULL_RUN_THRESHOLD = 50


def test_readme_test_count_is_current(request):
    """Compares against *collected* tests, not passed.

    Deliberate: tests/test_redis_integration.py skips without a live Redis, so
    locally it's 316 passed / 2 skipped while CI — which runs a real Redis
    service — passes all 318. Pinning the passed count would fail in one
    environment or the other. Collected is the same number everywhere.
    """
    collected = request.session.testscollected
    if collected < _FULL_RUN_THRESHOLD:
        pytest.skip(f"partial run ({collected} tests) — nothing to compare against")

    match = re.search(r"(\d+) tests,", README.read_text())
    assert match, "README no longer states a test count in the form 'N tests,'"

    claimed = int(match.group(1))
    assert claimed == collected, (
        f"README claims {claimed} tests, the suite collects {collected}."
        f" Update the build-log line in README.md to {collected}."
    )


def test_readme_coverage_floor_matches_what_ci_enforces():
    """The floor, not the achieved percentage.

    The percentage legitimately moves with which tests run — the Redis
    integration tests cover code locally that they don't when skipped — so
    pinning it would produce exactly the flapping this file exists to stop.
    The floor is a fixed promise and worth holding to.
    """
    match = re.search(r"(\d+)% floor", README.read_text())
    assert match, "README no longer states a coverage floor in the form 'N% floor'"
    claimed = int(match.group(1))

    enforced = set()
    for path in (ROOT / ".github/workflows/ci.yml", ROOT / "Makefile"):
        enforced.update(int(m) for m in re.findall(r"cov-fail-under=(\d+)", path.read_text()))

    assert enforced, "no --cov-fail-under found in CI or the Makefile"
    assert enforced == {claimed}, (
        f"README claims a {claimed}% floor; CI and the Makefile enforce {sorted(enforced)}"
    )


def test_readme_panel_count_matches_the_dashboard():
    """The README said "Nine panels" and then listed ten.

    Same class as the test count above: a number written by hand next to a
    list that grows. It had already drifted by one, which nobody noticed
    because prose doesn't fail.
    """
    import json

    dashboard = json.loads(
        (ROOT / "deploy" / "grafana" / "dashboards" / "gateway-overview.json").read_text()
    )

    match = re.search(r"(\d+) panels:", README.read_text())
    assert match, "README no longer states a panel count in the form 'N panels:'"

    claimed = int(match.group(1))
    actual = len(dashboard["panels"])
    assert claimed == actual, (
        f"README claims {claimed} dashboard panels, gateway-overview.json"
        f" defines {actual}"
    )


def test_readme_alert_count_matches_the_rules_file():
    """Third instance of the same shape: a hand-written number beside a list
    that grows. The panel count had already drifted; this one starts correct
    and is pinned so it stays that way.

    Counted from the text rather than by parsing YAML, so this needs no
    dependency the project doesn't otherwise have. promtool already validates
    the file's structure in CI, so a count of `- alert:` lines can't be
    reading something malformed.
    """
    rules = (ROOT / "deploy" / "prometheus" / "alerts.yml").read_text()
    actual = len(re.findall(r"^\s*- alert:", rules, re.M))
    assert actual, "no alert rules found — the guard would pass vacuously"

    match = re.search(r"(\d+) rules in `deploy/prometheus/alerts.yml`", README.read_text())
    assert match, "README no longer states an alert count"

    claimed = int(match.group(1))
    assert claimed == actual, (
        f"README claims {claimed} alert rules, alerts.yml defines {actual}"
    )


def test_readme_only_references_make_targets_that_exist():
    """The README tells a reader to run ten different `make` commands. A
    renamed or dropped target turns one of those into `make: *** No rule to
    make target` — landing on whoever is following the README for the first
    time, which is the worst possible audience for it.

    Checked against the rules the Makefile actually defines rather than
    .PHONY, since a target can work without being listed there.
    """
    makefile = (ROOT / "Makefile").read_text()
    defined = set(re.findall(r"^([a-zA-Z][\w-]*):", makefile, re.M))
    assert defined, "no targets found in the Makefile — the guard would pass vacuously"

    referenced = set(re.findall(r"\bmake ([a-z][a-z-]*)", README.read_text()))
    assert referenced, "README no longer mentions any make targets"

    missing = sorted(referenced - defined)
    assert not missing, (
        f"README tells the reader to run {missing}, which the Makefile does not"
        f" define (it has {sorted(defined)})"
    )
