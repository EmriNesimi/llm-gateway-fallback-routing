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
