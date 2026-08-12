import asyncio
import logging
from collections.abc import AsyncIterator

from app.core.config import settings
from app.observability.metrics import FALLBACK_TRIGGERED, PROVIDER_ATTEMPTS
from app.observability.tracing import tracer
from app.providers.base import BaseProvider, ChatMessage, ChatResponse, ProviderError, StreamChunk
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

    async def chat(self, messages: list[ChatMessage], request_id: str = "") -> ChatResponse:
        errors: list[str] = []
        tried_any = False

        for attempt, (provider, model, breaker) in enumerate(self._chain):
            if not breaker.allow_request():
                logger.warning(
                    "[request_id=%s] provider %s circuit open, skipping", request_id, provider.name
                )
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
                    span.set_attribute("gateway.request_id", request_id)
                    try:
                        result = await provider.chat(model=model, messages=messages)
                        # Bill/log against the model we actually requested (e.g.
                        # "gpt-4o-mini"), not whatever dated snapshot the provider
                        # echoes back (e.g. "gpt-4o-mini-2024-07-18") — otherwise
                        # cost lookups in app/budget/pricing.py silently miss.
                        result.model = model
                        breaker.record_success()
                        PROVIDER_ATTEMPTS.labels(provider=provider.name, outcome="success").inc()
                        if attempt > 0:
                            FALLBACK_TRIGGERED.inc()
                        return result
                    except ProviderError as exc:
                        last_error = exc
                        span.set_attribute("gateway.error", str(exc))
                        span.set_attribute("gateway.retryable", exc.retryable)
                        PROVIDER_ATTEMPTS.labels(provider=provider.name, outcome="error").inc()
                        if not exc.retryable:
                            # A 4xx-class client error fails identically on
                            # every retry — skip straight to the next
                            # provider instead of burning the remaining
                            # retries and their backoff for no chance of a
                            # different outcome.
                            logger.warning(
                                "[request_id=%s] provider %s failed with a non-retryable"
                                " error, falling back immediately: %s",
                                request_id,
                                provider.name,
                                exc,
                            )
                            break
                        if retry < self._retry_attempts:
                            logger.warning(
                                "[request_id=%s] provider %s failed (retry %d/%d): %s",
                                request_id,
                                provider.name,
                                retry + 1,
                                self._retry_attempts,
                                exc,
                            )
                            await asyncio.sleep(self._retry_backoff_seconds)

            breaker.record_failure()
            logger.warning(
                "[request_id=%s] provider %s exhausted retries, falling back: %s",
                request_id,
                provider.name,
                last_error,
            )
            errors.append(f"{provider.name}: {last_error}")

        if not tried_any:
            logger.error(
                "[request_id=%s] all providers had open circuits, nothing was attempted",
                request_id,
            )

        raise AllProvidersFailedError(
            f"all providers in fallback chain failed: {'; '.join(errors)}"
        )

    async def chat_stream(
        self, messages: list[ChatMessage], request_id: str = ""
    ) -> AsyncIterator[StreamChunk]:
        """Like `chat`, but streams chunks as they arrive.

        Fallback only works up to the FIRST chunk of a provider's response: we
        buffer it before yielding anything downstream, so if a provider fails
        before producing any output we can still try the next one. Once a
        provider has started streaming to the client, we're committed to it —
        a mid-stream failure at that point ends the response rather than
        silently switching providers partway through an answer.
        """
        errors: list[str] = []
        tried_any = False

        for attempt, (provider, model, breaker) in enumerate(self._chain):
            if not breaker.allow_request():
                logger.warning(
                    "[request_id=%s] provider %s circuit open, skipping", request_id, provider.name
                )
                errors.append(f"{provider.name}: circuit open")
                continue

            tried_any = True
            last_error: ProviderError | None = None
            committed = False

            for retry in range(self._retry_attempts + 1):
                failure: ProviderError | None = None

                with tracer.start_as_current_span(f"provider.{provider.name}.chat_stream") as span:
                    span.set_attribute("gateway.provider", provider.name)
                    span.set_attribute("gateway.model", model)
                    span.set_attribute("gateway.attempt", attempt)
                    span.set_attribute("gateway.retry", retry)
                    span.set_attribute("gateway.request_id", request_id)

                    generator = provider.chat_stream(model=model, messages=messages)
                    try:
                        first_chunk = await generator.__anext__()
                    except StopAsyncIteration:
                        failure = ProviderError("stream produced no chunks")
                    except ProviderError as exc:
                        failure = exc

                    if failure is not None:
                        last_error = failure
                        span.set_attribute("gateway.error", str(failure))
                        span.set_attribute("gateway.retryable", failure.retryable)
                        PROVIDER_ATTEMPTS.labels(provider=provider.name, outcome="error").inc()
                        if not failure.retryable:
                            logger.warning(
                                "[request_id=%s] provider %s failed before first chunk with a"
                                " non-retryable error, falling back immediately: %s",
                                request_id,
                                provider.name,
                                failure,
                            )
                            break
                        if retry < self._retry_attempts:
                            logger.warning(
                                "[request_id=%s] provider %s failed before first chunk"
                                " (retry %d/%d): %s",
                                request_id,
                                provider.name,
                                retry + 1,
                                self._retry_attempts,
                                failure,
                            )
                            await asyncio.sleep(self._retry_backoff_seconds)
                        continue

                    # Committed: first chunk arrived, hand it and everything after to the caller.
                    committed = True
                    breaker.record_success()
                    PROVIDER_ATTEMPTS.labels(provider=provider.name, outcome="success").inc()
                    if attempt > 0:
                        FALLBACK_TRIGGERED.inc()

                    first_chunk.provider = provider.name
                    first_chunk.model = model
                    yield first_chunk
                    try:
                        async for chunk in generator:
                            chunk.provider = provider.name
                            chunk.model = model
                            yield chunk
                    except ProviderError as exc:
                        breaker.record_failure()
                        logger.error(
                            "[request_id=%s] provider %s failed mid-stream after committing: %s",
                            request_id,
                            provider.name,
                            exc,
                        )
                        raise AllProvidersFailedError(
                            f"{provider.name} failed mid-stream: {exc}"
                        ) from exc
                    return

            if not committed:
                breaker.record_failure()
                logger.warning(
                    "[request_id=%s] provider %s exhausted retries before first chunk,"
                    " falling back: %s",
                    request_id,
                    provider.name,
                    last_error,
                )
                errors.append(f"{provider.name}: {last_error}")

        if not tried_any:
            logger.error(
                "[request_id=%s] all providers had open circuits, nothing was attempted",
                request_id,
            )

        raise AllProvidersFailedError(
            f"all providers in fallback chain failed: {'; '.join(errors)}"
        )
