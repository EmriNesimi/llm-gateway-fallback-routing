"""The decision records and their index have to agree.

docs/decisions/README.md is a hand-maintained table pointing at the files
beside it. Nothing regenerated it, so the two drift in both directions: a new
record that never gets a row is invisible to anyone browsing the index, and a
row whose file was renamed is a dead link in the first document a reader opens
to understand why this codebase is shaped the way it is.
"""

import pathlib
import re

DECISIONS = pathlib.Path(__file__).resolve().parent.parent / "docs" / "decisions"
INDEX = DECISIONS / "README.md"


def _record_files() -> dict[str, pathlib.Path]:
    """Every NNN-*.md record on disk, keyed by its number."""
    found = {}
    for path in DECISIONS.glob("*.md"):
        match = re.match(r"^(\d{3})-", path.name)
        if match:
            found[match.group(1)] = path
    return found


def _indexed() -> dict[str, str]:
    """Every row of the index table, mapping number -> linked filename."""
    rows = {}
    for number, target in re.findall(
        r"^\|\s*(\d{3})\s*\|[^|]*\|\s*\[[^\]]*\]\(([^)]+)\)", INDEX.read_text(), re.M
    ):
        rows[number] = target
    return rows


def test_every_record_appears_in_the_index():
    files, indexed = _record_files(), _indexed()
    assert files, "no decision records found — the guard would pass vacuously"

    missing = sorted(set(files) - set(indexed))
    assert not missing, (
        f"decision record(s) {missing} exist but are not listed in"
        " docs/decisions/README.md"
    )


def test_every_index_row_points_at_a_real_file():
    files, indexed = _record_files(), _indexed()
    assert indexed, "the index table has no rows — the guard would pass vacuously"

    broken = sorted(
        f"{number} -> {target}"
        for number, target in indexed.items()
        if not (DECISIONS / target).exists()
    )
    assert not broken, f"index links to files that do not exist: {broken}"

    renamed = sorted(
        f"{number}: index says {target}, file is {files[number].name}"
        for number, target in indexed.items()
        if number in files and files[number].name != target
    )
    assert not renamed, f"index links do not match the files on disk: {renamed}"


def test_decision_numbers_are_contiguous():
    """A gap means a record was deleted rather than superseded. Decisions are
    a history — the reasoning that was later overturned is the part worth
    keeping, so the fix is a new record saying so, not a removed one."""
    numbers = sorted(int(n) for n in _record_files())

    assert numbers == list(range(1, len(numbers) + 1)), (
        f"decision numbers are not contiguous from 001: {numbers}"
    )


def _citing_files() -> list[pathlib.Path]:
    root = DECISIONS.parent.parent
    files = list((root / "app").rglob("*.py"))
    files += [f for f in (root / "docs").rglob("*.md") if f.parent != DECISIONS]
    files += [root / "README.md", root / "SECURITY.md", root / "CHANGELOG.md"]
    return [f for f in files if f.exists()]


def test_every_cited_decision_exists():
    """Code comments and prose cite decisions by number — "see decision 010",
    "(decision 004)". Those citations are the main way anyone finds the
    reasoning behind a piece of code, and a wrong number sends the reader to a
    document about something else, which is worse than no citation at all.
    """
    numbers = set(_record_files())
    assert numbers, "no decision records found — the guard would pass vacuously"

    bad = []
    for path in _citing_files():
        text = path.read_text()
        for cited in re.findall(r"decisions?[/ -](\d{3})", text):
            if cited not in numbers:
                bad.append(f"{path.name} cites {cited}")

    assert not bad, f"citations pointing at decisions that do not exist: {sorted(set(bad))}"


def test_every_decision_link_resolves_to_a_file():
    """The markdown links are separate from the bare citations above and can
    rot on their own — a renamed record leaves a link that looks live and
    404s."""
    numbers = _record_files()
    broken = []
    for path in _citing_files():
        for target in re.findall(r"\(([^)]*decisions/[^)]+\.md)\)", path.read_text()):
            name = target.rsplit("/", 1)[-1]
            if not (DECISIONS / name).exists():
                broken.append(f"{path.name} -> {name}")

    assert not broken, (
        f"links to decision files that do not exist: {sorted(set(broken))}."
        f" Records present: {sorted(f.name for f in numbers.values())}"
    )
