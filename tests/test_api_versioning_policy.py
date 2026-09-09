"""Every route has to be covered by the versioning policy.

docs/api-versioning.md draws one line: `/v1/*` is the client contract and
changing it breaks people; everything else is operational and may change
freely. That line is only meaningful if every route sits on one side of it.

A new endpoint added without that decision defaults to neither — and the
default in practice is that it quietly becomes a contract, because someone
starts calling it.
"""

import pathlib

from app.main import app
from tests.conftest import all_route_paths

POLICY = (
    pathlib.Path(__file__).resolve().parent.parent / "docs" / "api-versioning.md"
).read_text()

# FastAPI registers these itself from the OpenAPI settings. They are not part
# of anything this project decides about, and naming them in the policy
# document would suggest otherwise.
_FRAMEWORK_ROUTES = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}


def _app_paths() -> set[str]:
    # Via the helper, not app.routes directly. FastAPI keeps an included
    # router as one opaque entry with path=None and its children behind
    # `original_router`, so a direct scan sees no /admin route at all — this
    # guard passed over two thirds of the routes it claimed to check.
    return {p for p in all_route_paths(app) if p not in _FRAMEWORK_ROUTES}


def test_every_route_is_versioned_or_named_as_operational():
    paths = _app_paths()
    assert paths, "no routes found — the guard would pass vacuously"

    unaccounted = []
    for path in sorted(paths):
        if path.startswith("/v1/"):
            continue
        # Parameterised routes are documented by their collection path,
        # e.g. /admin/keys covers /admin/keys/{key_id}.
        stem = path.split("/{", 1)[0] or "/"
        if stem in POLICY or path in POLICY:
            continue
        unaccounted.append(path)

    assert not unaccounted, (
        f"{unaccounted} are neither under /v1/ nor named in"
        " docs/api-versioning.md. Decide whether each is part of the client"
        " contract before something starts depending on it."
    )


def test_the_documented_statuses_match_the_openapi_ones():
    """The refusal statuses are written down twice: as a table in
    docs/api-versioning.md, and as `responses=` on the routes so they reach
    /openapi.json. Two hand-maintained lists of the same fact drift, and the
    failure is quiet — the prose promises a status the schema never mentions,
    or the schema advertises one the policy never explains.
    """
    import re

    from app.main import _CHAT_RESPONSES

    declared = {int(code) for code in _CHAT_RESPONSES}
    assert declared, "no refusal statuses declared — the guard would pass vacuously"

    # The policy table's first column, e.g. "| `402` | budget exhausted ..."
    documented = {int(c) for c in re.findall(r"^\|\s*`(\d{3})`\s*\|", POLICY, re.M)}
    assert documented, "docs/api-versioning.md no longer lists statuses in a table"

    assert declared == documented, (
        f"OpenAPI declares {sorted(declared)} and the policy documents"
        f" {sorted(documented)} — one of them is lying to a client"
    )


def test_every_chat_route_advertises_the_refusal_statuses():
    """`responses=_CHAT_RESPONSES` is one keyword on each of three decorators.
    Dropping it from one route changes nothing observable — the route still
    behaves identically — and the schema quietly stops telling clients that
    route can 402 or 503, while the other two still do. An inconsistent
    contract is worse than a uniformly silent one, because it looks
    deliberate.
    """
    from app.main import _CHAT_RESPONSES, app

    expected = {str(code) for code in _CHAT_RESPONSES}
    document = app.openapi()

    for path in ("/v1/chat", "/v1/chat/stream", "/v1/chat/completions"):
        declared = set(document["paths"][path]["post"]["responses"])
        missing = sorted(expected - declared)
        assert not missing, f"{path} does not advertise {missing}"
