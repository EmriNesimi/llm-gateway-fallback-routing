"""A script that writes must redirect the stores it writes to.

The suite has autouse fixtures for this — `isolated_db` and `isolated_redis`
— because a test that forgets is not a test that fails, it is a test that
writes to whatever the environment points at. That has happened twice: 150
rows of stub traffic in the production audit log, and the spend ledger
deleted on every run.

A script gets no fixture. `scripts/bench_overhead.py` drives the real
request path at volume, and its first version redirected Redis but not the
database — 338 rows into the real `gateway.db` on the first run.

So the redirect is asserted here. Read statically rather than by running the
thing, because running it is what would do the damage.
"""

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Scripts that drive the request path, so they write to both stores. A
# read-only one (reconcile) and a deliberate writer aimed at production
# (purge_audit_rows) are not in here — they are supposed to use the real
# database, which is the whole point of them.
_WRITES_AT_VOLUME = ["bench_overhead.py"]


@pytest.mark.parametrize("script_name", _WRITES_AT_VOLUME)
def test_the_script_redirects_both_stores(script_name):
    source = (ROOT / "scripts" / script_name).read_text()

    for var in ("REDIS_URL", "DATABASE_URL"):
        assert re.search(rf'os\.environ\["{var}"\]\s*=', source), (
            f"scripts/{script_name} drives the request path at volume but never"
            f" redirects {var}. Every request it makes writes to whichever store"
            " that names — the ledger for one, an audit row for the other."
        )


@pytest.mark.parametrize("script_name", _WRITES_AT_VOLUME)
def test_the_redirect_happens_before_the_app_is_imported(script_name):
    """Ordering is the whole thing.

    `app/core/config.py` builds its Settings singleton at import time and
    `app/budget/dependency.py` builds the ledger clients from it, so a
    redirect after the first `app.` import changes nothing — the real URLs
    are already captured. This is why conftest.py does its environment work
    above its own imports and carries an E402 waiver for it.
    """
    lines = (ROOT / "scripts" / script_name).read_text().splitlines()

    first_app_import = next(
        (i for i, line in enumerate(lines) if re.match(r"\s*(from|import) app[. ]", line)),
        None,
    )
    assert first_app_import is not None, f"scripts/{script_name} imports nothing from app/"

    for var in ("REDIS_URL", "DATABASE_URL"):
        redirect = next(
            (i for i, line in enumerate(lines) if f'os.environ["{var}"]' in line and "=" in line),
            None,
        )
        assert redirect is not None, (
            f"scripts/{script_name} never sets {var}"
        )
        assert redirect < first_app_import, (
            f"scripts/{script_name} sets {var} at line {redirect} but first imports"
            f" from app/ at line {first_app_import}. Settings is built at import"
            " time, so a redirect after that point is read by nothing."
        )


# Globals a script may set for its own run that outlive it in a test
# process. Each pairs what the script does with the evidence that the test
# file puts it back — not merely that it mentions it, which the first
# version of this checked and which the snapshot line satisfies on its own.
_PROCESS_WIDE = [
    (
        "app.dependency_overrides[",
        "app.dependency_overrides.update(",
        "authentication stays overridden for every later test",
    ),
    (
        "_limiter._capacity =",
        'monkeypatch.setattr(\n        ratelimit_dependency._limiter, "_capacity"',
        "the rate limiter stops refusing anything",
    ),
    (
        "tracker._monthly_cap_usd =",
        'budget_dependency.tracker, "_monthly_cap_usd"',
        "the per-key budget stops refusing anything",
    ),
    (
        "provider_budget._cap_usd =",
        'budget_dependency.provider_budget, "_cap_usd"',
        "the provider ceiling stops refusing anything",
    ),
]


@pytest.mark.parametrize(("mutation", "restore", "consequence"), _PROCESS_WIDE)
def test_a_test_calling_the_script_restores_what_it_changed(mutation, restore, consequence):
    """Whatever `bench_overhead` mutates, its test file must put back.

    The script is an entry point and owns the process it runs in, so raising
    the caps and overriding auth is right for it. A test calling that same
    function inherits the mutation and hands it to every test after — which
    fails in combination and passes in isolation, the worst way to find out.
    Two of these did exactly that.

    Matched on the restore, not on the name being mentioned: the fixture
    snapshots each of these by name on the way in, so a check for the name
    alone passes even with the restore deleted. It did.

    Read statically, because the failure this prevents is one test breaking
    a different one, which a test cannot observe about itself.
    """
    script = (ROOT / "scripts" / "bench_overhead.py").read_text()
    if mutation not in script:
        pytest.skip(f"bench_overhead no longer sets {mutation}")

    guard = (ROOT / "tests" / "test_bench_overhead.py").read_text()
    assert restore in guard, (
        f"scripts/bench_overhead.py sets {mutation} and"
        f" tests/test_bench_overhead.py does not restore it, so after those"
        f" tests run, {consequence}."
    )
