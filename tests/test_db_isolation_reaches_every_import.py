"""Every place that holds `async_session` by name has to be patched by
isolated_db, or tests that go through it write to the real database.

This is the trap that put 146 rows of fake spend into the production audit
log. isolated_db patches `app.db.session.async_session`, but a module that did
`from app.db.session import async_session` bound the *original* at import
time and keeps using it — conftest patches app/db/audit.py by hand for exactly
that reason, and scripts/reconcile.py hit the same wall the day it was written.

Rather than remember to add a patch line each time, this derives the list of
by-name importers from the source and checks each one against what
isolated_db actually swaps. A new by-name import fails here with the fix
spelled out, instead of quietly reaching production the first time a test
forgets to ask.
"""

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

_BY_NAME = re.compile(
    r"^from app\.db\.session import (?P<names>[^\n#]+)", re.MULTILINE
)


def _by_name_importers() -> dict[str, set[str]]:
    """{module path: names it imported from app.db.session by name}."""
    found = {}
    for path in list((ROOT / "app").rglob("*.py")) + list((ROOT / "scripts").rglob("*.py")):
        for m in _BY_NAME.finditer(path.read_text()):
            names = {n.strip().split(" as ")[0] for n in m.group("names").split(",")}
            # Only the session factory reaches a database for reads and
            # writes. get_session and init_db call through the module at run
            # time, so a patch on the module reaches them already. `engine` is
            # deliberately not on this list: app.main holds it purely to
            # dispose() at shutdown, and patching that in tears down the
            # fixture's in-memory database (see conftest for the full story).
            risky = names & {"async_session"}
            if risky:
                found[str(path.relative_to(ROOT))] = risky
    return found


# Imported at collection time, before any fixture runs. A module first
# imported *inside* the fixture binds the already-patched name and passes
# vacuously; in a full run app.main is always imported earlier, so the
# guard has to see the same thing a full run sees.
import app.db.audit  # noqa: E402, F401
import app.main  # noqa: E402, F401


@pytest.mark.asyncio
async def test_isolated_db_patches_every_by_name_importer(isolated_db):
    """`isolated_db` is autouse, so this test is already inside one. Walk
    each importer and check the name it holds is the fixture's fake, not the
    real factory."""
    import importlib

    from app.db import session as real

    importers = _by_name_importers()
    assert importers, "no by-name importers found — the guard would pass vacuously"

    unpatched = []
    for module_path, names in importers.items():
        module = importlib.import_module(
            module_path[:-3].replace("/", ".")
        )
        for name in names:
            held = getattr(module, name, None)
            # After isolated_db, the module attribute must be the same object
            # the fixture installed on app.db.session. If it is the original
            # factory instead, nothing patched it.
            if held is not getattr(real, name):
                unpatched.append(f"{module_path}: {name}")

    assert not unpatched, (
        "these hold a real database handle by name and isolated_db does not"
        f" reach them: {unpatched}. Either look it up through the module"
        " (`from app.db import session as db_session` then"
        " `db_session.async_session()`), or add a monkeypatch line for it"
        " in tests/conftest.py::isolated_db beside the one for app/db/audit.py."
    )
