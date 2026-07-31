import asyncio
import logging

from app.core.config import settings
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
    skipped entirely (no network call) until its cooldown elapses. Within a
    single provider, transient failures are retried a few times with a short
    backoff before giving up and falling back to the next provider.
    """

    def __init__(
        self,
        chain: list[tuple[BaseProvider, str, CircuitBreaker]],
        retry_attempts: int | None = None,
        retry_backoff_seconds: float | None = None,
    ):
        # chain is a list of (provider, model, breaker) triples, tried in order
        self._chain = chain
        self._retry_attempts = (
            retry_attempts if retry_attempts is not None else settings.provider_retry_attempts
        )
        self._retry_backoff_seconds = (
            retry_backoff_seconds
            if retry_backoff_seconds is not None
            else settings.provider_retry_backoff_seconds
        )

    async def chat(self, messages: list[ChatMessage]) -> ChatResponse:
        errors: list[str] = []
        tried_any = False

        for attempt, (provider, model, breaker) in enumerate(self._chain):
            if not breaker.allow_request():
                logger.warning("provider %s circuit open, skipping", provider.name)
                errors.append(f"{provider.name}: circuit open")
                continue

            tried_any = True
            last_error: ProviderError | None = None

            for retry in range(self._retry_attempts + 1):
                with tracer.start_as_current_span(f"provider.{provider.name}.chat") as span:
                    span.set_attribute("gateway.provider", provider.name)
                    span.set_attribute("gateway.model", model)
                    span.set_attribute("gateway.attempt", attempt)
                    span.set_attribute("gateway.retry", retry)
                    try:
                        result = await provider.chat(model=model, messages=messages)
                        breaker.record_success()
                        PROVIDER_ATTEMPTS.labels(provider=provider.name, outcome="success").inc()
                        if attempt > 0:
                            FALLBACK_TRIGGERED.inc()
                        return result
                    except ProviderError as exc:
                        last_error = exc
                        span.set_attribute("gateway.error", str(exc))
                        PROVIDER_ATTEMPTS.labels(provider=provider.name, outcome="error").inc()
                        if retry < self._retry_attempts:
                            logger.warning(
                                "provider %s failed (retry %d/%d): %s",
                                provider.name,
                                retry + 1,
                                self._retry_attempts,
                                exc,
                            )
                            await asyncio.sleep(self._retry_backoff_seconds)

            breaker.record_failure()
            logger.warning("provider %s exhausted retries, falling back: %s", provider.name, last_error)
            errors.append(f"{provider.name}: {last_error}")

        if not tried_any:
            logger.error("all providers had open circuits, nothing was attempted")

        raise AllProvidersFailedError(
            f"all providers in fallback chain failed: {'; '.join(errors)}"
        )
