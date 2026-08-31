"""`.env.example` is read as documentation of what happens if you set nothing.

Every tuning value in it — timeouts, budgets, breaker thresholds, rate limit
— is also a default in app/core/config.py, and the two are written down
independently. Change a default in the code and the example file keeps
advertising the old one, which is worse than saying nothing: someone copies it
to .env and pins the old value while believing they took the default.

Only values that are *meant* to equal the default are compared. The secrets
and placeholders deliberately differ, and each exclusion below says why.
"""

import pathlib

import pytest

from app.core.config import Settings

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Deliberately not the default. Every entry needs a reason.
_INTENTIONALLY_DIFFERENT = {
    "OPENAI_API_KEY": "obvious placeholder, demonstrates startup placeholder detection",
    "ANTHROPIC_API_KEY": "obvious placeholder, demonstrates startup placeholder detection",
    "GATEWAY_SECRET_KEY": "must be replaced; the code default is deliberately insecure",
    "GATEWAY_API_KEYS": "must be replaced; empty default fails closed on purpose",
    "ADMIN_API_KEY": "must be replaced; unset default fails closed on purpose",
    "OTEL_EXPORTER_OTLP_ENDPOINT": (
        "documents the host-side Jaeger address; the code default is unset so"
        " tracing is simply off unless asked for"
    ),
}

# Consumed by docker-compose.yml, not by Settings, so there is no default to
# compare them against. Kept honest by
# test_compose_variables_are_documented below, which derives the same set from
# the compose file rather than trusting this literal.
_COMPOSE_ONLY = {"REDIS_PASSWORD", "GRAFANA_PASSWORD"}


def _example_values() -> dict[str, str]:
    values = {}
    for line in (ROOT / ".env.example").read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def _matches(value: str, default, annotation) -> bool:
    # An empty value means "unset", which is how both "" and None read.
    if value == "":
        return default in ("", None)
    if annotation is bool or isinstance(default, bool):
        return value.lower() == str(default).lower()
    if isinstance(default, (int, float)):
        return float(value) == float(default)
    return value == (default if default is not None else "")


def test_every_documented_key_is_a_real_setting():
    """Guards the guard: a key renamed in the code but not here would
    otherwise be silently skipped rather than reported."""
    fields = set(Settings.model_fields)
    unknown = sorted(
        key
        for key in _example_values()
        if key not in _COMPOSE_ONLY and key.lower() not in fields
    )
    assert not unknown, (
        f".env.example documents {unknown}, which Settings does not define —"
        " setting them would do nothing"
    )


@pytest.mark.parametrize("key,value", sorted(_example_values().items()))
def test_documented_default_matches_the_code(key, value):
    if key in _COMPOSE_ONLY or key in _INTENTIONALLY_DIFFERENT:
        pytest.skip(_INTENTIONALLY_DIFFERENT.get(key, "consumed by docker-compose, not Settings"))

    field = Settings.model_fields[key.lower()]
    assert _matches(value, field.default, field.annotation), (
        f".env.example says {key}={value!r} but the code default is"
        f" {field.default!r} — the file advertises a value the gateway"
        " would not actually use"
    )


def test_every_setting_is_documented():
    """The other direction from the check above.

    That one catches a key in .env.example that no longer exists. This one
    catches a setting added to config.py that never made it into the file —
    which is worse, because the reader has no way to discover it exists. The
    only inventory of what this gateway can be configured with is this file.
    """
    documented = set(_example_values())
    undocumented = sorted(
        name.upper() for name in Settings.model_fields if name.upper() not in documented
    )
    assert not undocumented, (
        f"{undocumented} exist as settings but appear nowhere in .env.example —"
        " nobody reading the file would know they can be set"
    )


def _compose_variables() -> set[str]:
    """Names docker-compose.yml substitutes, e.g. ${REDIS_PASSWORD:-default}."""
    import re

    compose = (ROOT / "docker-compose.yml").read_text()
    return set(re.findall(r"\$\{([A-Z_][A-Z0-9_]*)", compose))


def test_compose_variables_are_documented():
    """Every ${VAR} in the compose file has a default baked in, so an
    undocumented one doesn't fail — the stack quietly comes up using the
    fallback, and nobody knows the knob exists. That is how the Redis password
    would end up left at its default on something exposed.
    """
    referenced = _compose_variables()
    assert referenced, "no ${VAR} substitutions found — the guard would pass vacuously"

    missing = sorted(referenced - set(_example_values()))
    assert not missing, (
        f"docker-compose.yml substitutes {missing}, which .env.example never"
        " mentions — the stack would silently use the built-in default"
    )


def test_the_compose_only_exclusion_list_is_accurate():
    """_COMPOSE_ONLY above skips the default comparison for compose variables.
    If it drifts from what the compose file actually uses, it either skips a
    real setting (hiding drift) or names one that no longer exists."""
    assert _COMPOSE_ONLY == _compose_variables() - set(Settings.model_fields), (
        f"_COMPOSE_ONLY is {sorted(_COMPOSE_ONLY)} but the compose file"
        f" substitutes {sorted(_compose_variables())}"
    )
