from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from app.core.config import settings

_configured = False


def configure_tracing() -> None:
    """Set up the global tracer provider. No-op if no OTLP endpoint is configured
    or if called more than once (keeps re-imports/tests safe)."""
    global _configured
    if _configured or not settings.otel_exporter_otlp_endpoint:
        return

    provider = TracerProvider(resource=Resource.create({SERVICE_NAME: "llm-gateway"}))
    exporter = OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint, insecure=True)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    _configured = True


tracer = trace.get_tracer("gateway")
