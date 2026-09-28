"""Prose that names a file or a function has to name one that exists.

The docs here lean hard on pointing at code: decision records cite the
module that implements them, SECURITY.md names the functions that enforce
each control, the runbook tells an operator which file to open. That is what
makes them worth reading instead of a summary that drifts.

It is also what makes a rename break them silently. Nothing about renaming
`_settle_chain` tells you that four decision records mention it, and a
dangling reference is worse than no reference: it sends a reader looking for
something that is not there and costs them the trust they had in the rest.

So the references are checked. Anything in backticks that looks like a path
under `app/` must exist, and anything that looks like a private function call
must be defined somewhere in `app/`.
"""

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

# The Makefile and .env.example are in here too: both explain themselves in
# comments that name modules, and a comment pointing at a moved file rots the
# same way prose does. They just write the path bare rather than in backticks.
_PROSE = sorted(
    [*ROOT.joinpath("docs").rglob("*.md"), ROOT / "README.md",
     ROOT / "SECURITY.md", ROOT / "CONTRIBUTING.md", ROOT / "CHANGELOG.md",
     ROOT / "Makefile", ROOT / ".env.example"],
)

# `app/budget/tracker.py`, or the same path bare. Backticks optional because
# half of these files are not markdown.
_FILE_REF = re.compile(r"`?\b(app/[A-Za-z0-9_/]+\.py)`?")
# `_settle_chain` or `_settle_chain()` — a private function in backticks.
# Both spellings appear; the parens are optional and the docs mostly omit
# them, which is how the first version of this guard matched nothing at all
# and passed while a renamed function sat undetected.
_FUNC_REF = re.compile(r"`(_[a-z][a-z0-9_]*)(?:\(\))?`")


# Backticked ALL-CAPS names that are not this project's: protocol keywords,
# SQL types, Docker directives, Redis commands and errors. Each is here
# because it appears in prose and is not ours to rename.
_NOT_OURS = {
    "HEALTHCHECK",   # a Dockerfile directive
    "VARCHAR",       # a SQL type
    "INCRBYFLOAT",   # a Redis command
    "NOAUTH",        # a Redis error code
    "DONE",          # the [DONE] sentinel in the SSE protocol
    "GATEWAY_ENV_FILE",  # read by conftest before Settings exists, so not a field
    "DEMO_ADMIN_KEY",    # scripts/demo.sh only
    "GATEWAY_URL", "CLIENT_KEY",  # scripts/load_test.js only
    "APPLY", "ID",   # make purge-audit arguments
    "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB",  # compose only
    "REDIS_PASSWORD", "GRAFANA_PASSWORD",  # compose only, per .env.example
    "PYTHONUNBUFFERED",  # set in the Dockerfile
    "KEYS", "FLUSHALL",  # more Redis commands
    "SELECT", "DELETE",  # SQL verbs
    "CODEOWNERS",        # a GitHub filename
    # Ruff rule codes, named in the changelog where each was pinned or
    # deliberately left off.
    "ANN204", "D400", "N818", "PIE790", "RUF100", "TRY401",
}


def _app_source() -> str:
    return "\n".join(p.read_text() for p in sorted(ROOT.joinpath("app").rglob("*.py")))


@pytest.mark.parametrize("doc", _PROSE, ids=lambda p: str(p.relative_to(ROOT)))
def test_referenced_files_exist(doc):
    missing = sorted(
        {m.group(1) for m in _FILE_REF.finditer(doc.read_text())
         if not (ROOT / m.group(1)).exists()},
    )
    assert not missing, (
        f"{doc.relative_to(ROOT)} points at files that no longer exist: {missing}."
        " A moved module leaves the prose pointing at nothing."
    )


@pytest.mark.parametrize("doc", _PROSE, ids=lambda p: str(p.relative_to(ROOT)))
def test_referenced_functions_exist(doc):
    source = _app_source()
    gone = sorted(
        {m.group(1) for m in _FUNC_REF.finditer(doc.read_text())
         if f"def {m.group(1)}(" not in source},
    )
    assert not gone, (
        f"{doc.relative_to(ROOT)} names functions that are not defined in app/:"
        f" {gone}. Renaming one is what usually does this."
    )


def test_the_prose_list_is_not_empty():
    """A glob that stopped matching would make every test above pass."""
    assert len(_PROSE) > 10, f"only found {len(_PROSE)} prose files to check"
    joined = "".join(p.read_text() for p in _PROSE)
    assert _FILE_REF.search(joined), (
        "no file references found at all — the guard would pass vacuously"
    )
    # This one caught itself: the first version required `name()` with parens,
    # which the docs almost never write, so it checked nothing.
    assert len(set(_FUNC_REF.findall(joined))) >= 4, (
        "almost no function references matched; check the pattern still fits"
        " how the docs actually write them"
    )


# `MAX_MESSAGES`, `PROVIDER_LIFETIME_BUDGET_USD` — a constant or setting in
# backticks. Four or more characters, so `USD` and `SSE` do not qualify.
_CONST_REF = re.compile(r"`([A-Z][A-Z0-9_]{3,})`")


@pytest.mark.parametrize("doc", _PROSE, ids=lambda p: str(p.relative_to(ROOT)))
def test_referenced_constants_exist(doc):
    """A backticked capital name is either ours or on the list saying whose.

    The README documents the request-size caps by name, SECURITY.md names the
    settings behind each control, and `.env.example` is read as the list of
    what can be configured. Renaming one of those leaves prose confidently
    naming something that is not there — and unlike a wrong number, nothing
    else fails to make anyone look.
    """
    from app.core.config import Settings

    source = _app_source()
    ours = {f.upper() for f in Settings.model_fields}

    unknown = sorted(
        {
            name
            for name in _CONST_REF.findall(doc.read_text())
            if name not in _NOT_OURS
            and name not in ours
            and f"\n{name} " not in source
            and f"\n{name}:" not in source
        },
    )
    assert not unknown, (
        f"{doc.relative_to(ROOT)} names {unknown}, which are neither settings,"
        " constants defined in app/, nor listed in _NOT_OURS. Either it was"
        " renamed, or it belongs on that list with a note saying whose it is."
    )
