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

_PROSE = sorted(
    [*ROOT.joinpath("docs").rglob("*.md"), ROOT / "README.md",
     ROOT / "SECURITY.md", ROOT / "CONTRIBUTING.md", ROOT / "CHANGELOG.md"],
)

# `app/budget/tracker.py` — a path in backticks.
_FILE_REF = re.compile(r"`(app/[A-Za-z0-9_/]+\.py)`")
# `_settle_chain` or `_settle_chain()` — a private function in backticks.
# Both spellings appear; the parens are optional and the docs mostly omit
# them, which is how the first version of this guard matched nothing at all
# and passed while a renamed function sat undetected.
_FUNC_REF = re.compile(r"`(_[a-z][a-z0-9_]*)(?:\(\))?`")


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
