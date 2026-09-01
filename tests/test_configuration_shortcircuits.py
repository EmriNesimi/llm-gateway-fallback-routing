"""Two "don't do the thing" guards that had never been exercised.

Both decide, at startup, whether a subsystem gets set up at all. A broken
guard doesn't raise — it either silently skips work that was wanted, or does
work that was deliberately avoided. Neither shows up as a failure.
"""

import logging

import pytest

import app.db.session as session_module
import app.observability.tracing as tracing_module

# --------------------------------------------------------------------------
# Tracing
# --------------------------------------------------------------------------


@pytest.fixture
def _tracing_sandbox(monkeypatch):
    """Keeps the global tracer provider untouched. Really calling
    set_tracer_provider would install this test's provider for the rest of the
    session, and OpenTelemetry only honours the first one."""
    installed = []
    exporters = []

    monkeypatch.setattr(tracing_module, "_configured", False)
    monkeypatch.setattr(tracing_module.trace, "set_tracer_provider", installed.append)
    monkeypatch.setattr(
        tracing_module, "OTLPSpanExporter", lambda **kw: exporters.append(kw) or object()
    )
    # The real TracerProvider keeps the processor and calls shutdown() on it
    # at interpreter exit, so the stub has to satisfy that much of the
    # interface or the whole session ends in an AttributeError.
    class _NullSpanProcessor:
        def __init__(self, exporter):
            self.exporter = exporter

        def on_start(self, span, parent_context=None):
            pass

        def on_end(self, span):
            pass

        def shutdown(self):
            pass

        def force_flush(self, timeout_millis=30_000):
            return True

    monkeypatch.setattr(tracing_module, "BatchSpanProcessor", _NullSpanProcessor)
    return installed, exporters


def test_tracing_is_configured_when_an_endpoint_is_set(_tracing_sandbox, monkeypatch):
    installed, exporters = _tracing_sandbox
    monkeypatch.setattr(
        tracing_module.settings, "otel_exporter_otlp_endpoint", "http://collector:4317"
    )

    tracing_module.configure_tracing()

    assert len(installed) == 1
    assert exporters[0]["endpoint"] == "http://collector:4317"
    assert tracing_module._configured is True


def test_configuring_tracing_twice_is_a_no_op(_tracing_sandbox, monkeypatch):
    """docker-compose and the app both call this, and OpenTelemetry silently
    ignores a second provider — so a double call would leave spans going to
    the first one while the code believes otherwise."""
    installed, _ = _tracing_sandbox
    monkeypatch.setattr(
        tracing_module.settings, "otel_exporter_otlp_endpoint", "http://collector:4317"
    )

    tracing_module.configure_tracing()
    tracing_module.configure_tracing()

    assert len(installed) == 1


def test_no_endpoint_means_tracing_is_simply_off(_tracing_sandbox, monkeypatch):
    installed, _ = _tracing_sandbox
    monkeypatch.setattr(tracing_module.settings, "otel_exporter_otlp_endpoint", None)

    tracing_module.configure_tracing()

    assert installed == []
    assert tracing_module._configured is False


# --------------------------------------------------------------------------
# Schema creation
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_init_db_does_not_create_tables_on_postgres(monkeypatch, caplog):
    """create_all raced by every replica on boot is the thing this avoids;
    on Postgres the schema belongs to `alembic upgrade head`, run once as a
    deploy step. If this guard broke, replicas would race DDL on startup.
    """

    class _ExplodingEngine:
        def begin(self):
            raise AssertionError("create_all must not run against a non-SQLite database")

    monkeypatch.setattr(
        session_module.settings, "database_url", "postgresql+asyncpg://u:p@db:5432/gateway"
    )
    monkeypatch.setattr(session_module, "engine", _ExplodingEngine())

    with caplog.at_level(logging.INFO):
        await session_module.init_db()

    assert "skipping create_all" in caplog.text
