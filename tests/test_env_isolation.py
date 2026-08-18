"""Guards the environment isolation set up in conftest.py.

The isolation there rests on a hand-written list of variable names, which is
exactly the kind of thing that rots: add a field to Settings, forget to add it
here, and that one setting quietly starts being read from the developer's
shell again — reintroducing the local-vs-CI divergence the list exists to
prevent, with no failing test to say so.
"""

import os

from conftest import CLEARED_ENV_VARS, PRESERVED_ENV_VARS

from app.core import config


def _settings_env_var_names() -> set[str]:
    """The env var each Settings field reads. No env_prefix is configured, so
    pydantic-settings maps a field to its own name, upper-cased."""
    return {name.upper() for name in config.Settings.model_fields}


def test_every_setting_is_either_cleared_or_deliberately_preserved():
    unaccounted = _settings_env_var_names() - CLEARED_ENV_VARS - PRESERVED_ENV_VARS
    assert not unaccounted, (
        f"Settings fields {sorted(unaccounted)} are neither cleared nor preserved in"
        " conftest.py, so they'd still be read from the developer's shell. Add each"
        " to CLEARED_ENV_VARS, or to PRESERVED_ENV_VARS with a reason."
    )


def test_no_stale_names_in_the_conftest_lists():
    known = _settings_env_var_names()
    stale = (CLEARED_ENV_VARS | PRESERVED_ENV_VARS) - known
    assert not stale, (
        f"conftest.py references {sorted(stale)}, which are no longer Settings fields."
        " Drop them so the lists keep describing the real configuration surface."
    )


def test_env_file_loading_is_disabled_for_the_suite():
    # Resolved once at config import time, which conftest.py runs ahead of.
    assert config._ENV_FILE is None
    assert config.Settings.model_config["env_file"] is None


def test_cleared_variables_are_actually_absent_during_a_test_run():
    still_set = sorted(name for name in CLEARED_ENV_VARS if name in os.environ)
    assert not still_set, (
        f"{still_set} survived conftest's cleanup — something re-set them after import."
    )


def test_settings_construct_without_an_env_file_present():
    # The suite must not depend on a .env existing, since CI has none. Nothing
    # is passed here: this is the same construction path app code takes.
    settings = config.Settings()

    assert settings.openai_api_key is None
    assert settings.anthropic_api_key is None
    assert settings.otel_exporter_otlp_endpoint is None
    assert settings.gateway_api_keys == ""
