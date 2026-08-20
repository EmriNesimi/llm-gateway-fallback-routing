import asyncio
import logging
import time
from collections.abc import AsyncIterator

from app.core.config import settings
from app.observability.metrics import (
    CIRCUIT_STATE,
    FALLBACK_TRIGGERED,
    PROVIDER_ATTEMPTS,
    PROVIDER_LATENCY,
)
from app.observability.tracing import tracer
from app.providers.base import (
    BaseProvider,
    ChatMessage,
    ChatResponse,
    ProviderError,
    SamplingParams,
    StreamChunk,
)
from app.routing.circuit_breaker import CircuitBreaker, CircuitState

logger = logging.getLogger("gateway.router")

# A gauge carries a number, so the state enum needs an ordering. Ascending
# severity means a Grafana threshold or an alert rule can just say "> 0".
_CIRCUIT_STATE_VALUES = {
    CircuitState.CLOSED: 0,
    CircuitState.HALF_OPEN: 1,
    CircuitState.OPEN: 2,
}


def publish_circuit_state(provider_name: str, breaker: CircuitBreaker) -> None:
    """Mirror a breaker's state onto its gauge.

    Called wherever the state could have changed, which includes reads: the
    OPEN -> HALF_OPEN transition happens lazily on inspection rather than on a
    timer, so without publishing on the read path a cooled-down breaker would
    keep reporting OPEN until its next recorded success or failure."""
    CIRCUIT_STATE.labels(provider=provider_name).set(_CIRCUIT_STATE_VALUES[breaker.state])


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

    async def chat(
        self,
        messages: list[ChatMessage],
        request_id: str = "",
        params: SamplingParams | None = None,
    ) -> ChatResponse:
        errors: list[str] = []
        tried_any = False

        for attempt, (provider, model, breaker) in enumerate(self._chain):
            allowed = breaker.allow_request()
            publish_circuit_state(provider.name, breaker)
            if not allowed:
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
                    attempt_started = time.perf_counter()
                    try:
                        result = await provider.chat(
                            model=model, messages=messages, params=params
                        )
                        PROVIDER_LATENCY.labels(provider=provider.name).observe(
                            time.perf_counter() - attempt_started
                        )
                        # Bill/log against the model we actually requested (e.g.
                        # "gpt-4o-mini"), not whatever dated snapshot the provider
                        # echoes back (e.g. "gpt-4o-mini-2024-07-18") — otherwise
                        # cost lookups in app/budget/pricing.py silently miss.
                        result.model = model
                        breaker.record_success()
                        publish_circuit_state(provider.name, breaker)
                        PROVIDER_ATTEMPTS.labels(provider=provider.name, outcome="success").inc()
                        if attempt > 0:
                            FALLBACK_TRIGGERED.inc()
                        return result
                    except ProviderError as exc:
                        # Observed here rather than in a finally: the retry
                        # backoff below is inside this block, and folding a
                        # deliberate sleep into a provider's latency would
                        # make a healthy-but-retried provider look slow.
                        # Failures are timed as well as successes on purpose —
                        # a provider that fails slowly is a different problem
                        # from one that fails fast.
                        PROVIDER_LATENCY.labels(provider=provider.name).observe(
                            time.perf_counter() - attempt_started
                        )
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
            publish_circuit_state(provider.name, breaker)
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
        self,
        messages: list[ChatMessage],
        request_id: str = "",
        params: SamplingParams | None = None,
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
            allowed = breaker.allow_request()
            publish_circuit_state(provider.name, breaker)
            if not allowed:
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

                    generator = provider.chat_stream(
                        model=model, messages=messages, params=params
                    )
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
                    publish_circuit_state(provider.name, breaker)
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
                        publish_circuit_state(provider.name, breaker)
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
                publish_circuit_state(provider.name, breaker)
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
