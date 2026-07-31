import logging

from app.observability.metrics import FALLBACK_TRIGGERED, PROVIDER_ATTEMPTS
from app.observability.tracing import tracer
from app.providers.base import BaseProvider, ChatMessage, ChatResponse, ProviderError
from app.routing.circuit_breaker import CircuitBreaker

logger = logging.getLogger("gateway.router")


class AllProvidersFailedError(Exception):
    """Raised when every provider in the fallback chain has failed or is unavailable."""


class FallbackRouter:
    """Tries providers in order, falling back to the next on ProviderError.

    Each provider has its own circuit breaker: after repeated failures it's
    skipped entirely (no network call) until its cooldown elapses.
    """

    def __init__(self, chain: list[tuple[BaseProvider, str, CircuitBreaker]]):
        # chain is a list of (provider, model, breaker) triples, tried in order
        self._chain = chain

    async def chat(self, messages: list[ChatMessage]) -> ChatResponse:
        errors: list[str] = []
        tried_any = False

        for attempt, (provider, model, breaker) in enumerate(self._chain):
            if not breaker.allow_request():
                logger.warning("provider %s circuit open, skipping", provider.name)
                errors.append(f"{provider.name}: circuit open")
                continue

            tried_any = True
            with tracer.start_as_current_span(f"provider.{provider.name}.chat") as span:
                span.set_attribute("gateway.provider", provider.name)
                span.set_attribute("gateway.model", model)
                span.set_attribute("gateway.attempt", attempt)
                try:
                    result = await provider.chat(model=model, messages=messages)
                    breaker.record_success()
                    PROVIDER_ATTEMPTS.labels(provider=provider.name, outcome="success").inc()
                    if attempt > 0:
                        FALLBACK_TRIGGERED.inc()
                    return result
                except ProviderError as exc:
                    breaker.record_failure()
                    span.set_attribute("gateway.error", str(exc))
                    PROVIDER_ATTEMPTS.labels(provider=provider.name, outcome="error").inc()
                    logger.warning("provider %s failed, falling back: %s", provider.name, exc)
                    errors.append(f"{provider.name}: {exc}")

        if not tried_any:
            logger.error("all providers had open circuits, nothing was attempted")

        raise AllProvidersFailedError(
            f"all providers in fallback chain failed: {'; '.join(errors)}"
        )
