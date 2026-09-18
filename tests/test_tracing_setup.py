"""configure_tracing is called at import and again by anything that re-imports
app.main — every test file that does, for a start. It has to be safe to call
twice, and it has to be a no-op with no endpoint configured.

Neither was tested. The once-guard is a module-level flag, and a broken one
would not raise: OpenTelemetry warns and ignores a second set_tracer_provider,
so the visible effect is one more BatchSpanProcessor export thread per call,
each holding a gRPC channel to an endpoint that may not exist.
"""

import pytest

from app.observability import tracing


@pytest.fixture
def fresh_guard(monkeypatch):
    monkeypatch.setattr(tracing, "_configured", False)


def test_no_endpoint_means_no_provider(fresh_guard, monkeypatch):
    monkeypatch.setattr(tracing.settings, "otel_exporter_otlp_endpoint", None)
    built = []
    monkeypatch.setattr(tracing, "TracerProvider", lambda **kw: built.append(kw))

    tracing.configure_tracing()

    assert built == [], "a provider was built with no endpoint to export to"
    assert tracing._configured is False, "marked configured without configuring"


def test_a_second_call_is_a_no_op(fresh_guard, monkeypatch):
    monkeypatch.setattr(tracing.settings, "otel_exporter_otlp_endpoint", "localhost:4317")
    built = []

    class _Provider:
        def __init__(self, **kw):
            built.append(self)

        def add_span_processor(self, processor):
            pass

    monkeypatch.setattr(tracing, "TracerProvider", _Provider)
    monkeypatch.setattr(tracing, "OTLPSpanExporter", lambda **kw: object())
    monkeypatch.setattr(tracing, "BatchSpanProcessor", lambda exporter: object())
    monkeypatch.setattr(tracing.trace, "set_tracer_provider", lambda p: None)

    tracing.configure_tracing()
    tracing.configure_tracing()
    tracing.configure_tracing()

    assert len(built) == 1, f"configure_tracing built {len(built)} providers across 3 calls"
