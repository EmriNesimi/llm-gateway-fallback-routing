"""The Python version is pinned in seven files that nothing keeps in step.

`.python-version`, the Dockerfile, ruff's target-version, mypy's
python_version, both CI jobs and the README badge all name the interpreter
independently. Nothing reads one from another, so a bump can land in one place
and leave six behind — and the failure is quiet: CI keeps testing the old
version while the image ships the new one, or ruff keeps applying the old
version's upgrade rules.

This is not hypothetical. There is an open Dependabot PR right now proposing
to change exactly one of these seven (the Dockerfile, 3.12 -> 3.14). Merging
it as-is would mean the image runs an interpreter the suite has never been
executed against.
"""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _read(name: str) -> str:
    return (ROOT / name).read_text()


def _declared_versions() -> dict[str, str]:
    """Every place the Python version is written down, normalised to 'X.Y'."""
    sites: dict[str, str] = {}

    sites[".python-version"] = _read(".python-version").strip()

    m = re.search(r"^FROM python:(\d+\.\d+)-", _read("Dockerfile"), re.M)
    assert m, "Dockerfile no longer has a recognisable `FROM python:X.Y-...`"
    sites["Dockerfile"] = m.group(1)

    pyproject = _read("pyproject.toml")

    # ruff writes it without the dot: py312
    m = re.search(r'target-version\s*=\s*"py(\d)(\d+)"', pyproject)
    assert m, "pyproject.toml no longer sets [tool.ruff] target-version"
    sites["ruff target-version"] = f"{m.group(1)}.{m.group(2)}"

    m = re.search(r'python_version\s*=\s*"(\d+\.\d+)"', pyproject)
    assert m, "pyproject.toml no longer sets [tool.mypy] python_version"
    sites["mypy python_version"] = m.group(1)

    ci = re.findall(r'python-version:\s*"(\d+\.\d+)"', _read(".github/workflows/ci.yml"))
    assert len(ci) == 2, f"expected 2 setup-python pins in ci.yml, found {len(ci)}"
    for i, v in enumerate(ci, start=1):
        sites[f"ci.yml setup-python #{i}"] = v

    m = re.search(r"badge/Python-(\d+\.\d+)-", _read("README.md"))
    assert m, "README no longer carries a Python-X.Y badge"
    sites["README badge"] = m.group(1)

    return sites


def test_python_version_is_pinned_consistently():
    sites = _declared_versions()

    # If a rename or a reformat makes a site unfindable, the guard must fail
    # rather than quietly check fewer places than it thinks it does.
    assert len(sites) == 7, f"expected 7 declaration sites, found {len(sites)}: {sites}"

    distinct = set(sites.values())
    assert len(distinct) == 1, (
        "Python version declarations disagree — bump them together:\n"
        + "\n".join(f"  {site}: {version}" for site, version in sorted(sites.items()))
    )
