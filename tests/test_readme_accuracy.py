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

# A partial run collects a subset, so its count means nothing to compare
# against. Detected from the invocation rather than from a size threshold: a
# threshold has to be guessed, and the guess goes stale as the suite grows —
# this one was 50 while the suite was 631, so running any two files collected
# 93, looked "full", and failed with a count nobody could make sense of.
#
# `pytest` with no paths uses testpaths from pyproject, which is the full run
# CI and `make test` do. Anything else named a file, a directory or a filter.
def _is_full_run(request) -> bool:
    opts = request.config.option
    return not (opts.file_or_dir or opts.keyword or opts.markexpr)


def test_readme_test_count_is_current(request):
    """Compares against *collected* tests, not passed.

    Deliberate: tests/test_redis_integration.py skips without a live Redis, so
    locally it's 316 passed / 2 skipped while CI — which runs a real Redis
    service — passes all 318. Pinning the passed count would fail in one
    environment or the other. Collected is the same number everywhere.
    """
    if not _is_full_run(request):
        pytest.skip("partial run — the collected count means nothing here")
    collected = request.session.testscollected

    match = re.search(r"(\d+) tests,", README.read_text())
    assert match, "README no longer states a test count in the form 'N tests,'"

    claimed = int(match.group(1))
    assert claimed == collected, (
        f"README claims {claimed} tests, the suite collects {collected}."
        f" Update the build-log line in README.md to {collected}."
        " If the difference is larger than the number of tests you wrote, a"
        " parametrised one is multiplying: count what pytest collects rather"
        " than what you added."
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
        (ROOT / "deploy" / "grafana" / "dashboards" / "gateway-overview.json").read_text(),
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
    actual = len(re.findall(r"^\s*- alert:", rules, re.MULTILINE))
    assert actual, "no alert rules found — the guard would pass vacuously"

    match = re.search(r"(\d+) rules in `deploy/prometheus/alerts.yml`", README.read_text())
    assert match, "README no longer states an alert count"

    claimed = int(match.group(1))
    assert claimed == actual, (
        f"README claims {claimed} alert rules, alerts.yml defines {actual}"
    )


@pytest.mark.parametrize(
    "doc_name",
    [
        "README.md",
        "CONTRIBUTING.md",
        "docs/runbook.md",
        # Names six targets, and is the one a contributor reads while already
        # mid-change — the worst moment to be sent at a target that is gone.
        ".github/pull_request_template.md",
    ],
)
def test_docs_only_reference_make_targets_that_exist(doc_name):
    """These tell a reader to run `make` something. A renamed or dropped
    target turns one of those into `make: *** No rule to make target` —
    landing on whoever is following the instructions for the first time,
    which is the worst possible audience for it.

    CONTRIBUTING.md and the runbook matter as much as the README here: one is
    read before a first contribution, the other during an incident. Checked
    against the rules the Makefile actually defines rather than .PHONY, since
    a target can work without being listed there.
    """
    makefile = (ROOT / "Makefile").read_text()
    defined = set(re.findall(r"^([a-zA-Z][\w-]*):", makefile, re.MULTILINE))
    assert defined, "no targets found in the Makefile — the guard would pass vacuously"

    doc = ROOT / doc_name
    # Only what is actually written as code. "make" is also an English verb:
    # the runbook says "do not raise this to make it go away", and a pattern
    # that scans raw prose reads that as a target called `it`. Matching from
    # any backtick has the same problem from the other end, since a closing
    # backtick looks exactly like an opening one — so the code spans are
    # extracted first, in pairs, and only they are searched.
    text = doc.read_text()
    spans = re.findall(r"`([^`\n]+)`", text)
    spans += re.findall(r"^\s*(?:\$ )?(make .+)$", text, re.MULTILINE)
    referenced = {
        m.group(1) for span in spans
        for m in re.finditer(r"\bmake ([a-z][a-z-]*)", span)
    }
    assert referenced, f"{doc_name} no longer mentions any make targets"

    missing = sorted(referenced - defined)
    assert not missing, (
        f"{doc_name} tells the reader to run {missing}, which the Makefile does"
        f" not define (it has {sorted(defined)})"
    )


@pytest.mark.parametrize(
    "doc_name",
    [
        "README.md",
        # Names six of them, and is read while something is already broken —
        # being sent to a 404 then costs more than it does from the README.
        "docs/runbook.md",
        "SECURITY.md",
    ],
)
def test_docs_only_document_endpoints_that_exist(doc_name):
    """These name endpoints in backticks — `GET /readyz`, `/admin/key-events`.
    A renamed or removed route leaves prose promising something that 404s,
    which is discovered by whoever is following it rather than by anyone
    maintaining it.

    Checks only the direction that can mislead. Not every route needs to be
    documented; every path documented needs to be a route.
    """
    from app.main import app
    from tests.conftest import all_route_paths

    # Via the helper: app.routes keeps an included router as a single opaque
    # entry, so a direct scan cannot see any /admin route.
    served = all_route_paths(app)
    # FastAPI's own docs endpoints, which the README legitimately links to.
    served |= {"/docs", "/openapi.json", "/redoc"}
    # Collection paths cover their parameterised children: a README mentioning
    # /admin/keys is satisfied by /admin/keys/{key_id} existing too.
    served |= {p.split("/{", 1)[0] for p in served if "/{" in p}
    # Prefixes the prose names as a group — "any key that can reach /admin",
    # "the /v1 contract". Real as a namespace, never mounted as a route.
    served |= {"/admin", "/v1"}

    documented = {
        p
        for p in re.findall(
            r"`(?:GET |POST |DELETE )?(/[a-z0-9/_-]+)`", (ROOT / doc_name).read_text(),
        )
        # "/v1/" and friends name a prefix, not a route.
        if not p.endswith("/") or p == "/"
    }
    assert documented, f"{doc_name} documents no endpoints — the guard would pass vacuously"

    missing = sorted(p for p in documented if p.rstrip("/") not in served and p not in served)
    assert not missing, (
        f"{doc_name} documents {missing}, which the app does not serve"
    )
