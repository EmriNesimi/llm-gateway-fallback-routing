import logging

from app.observability.metrics import FALLBACK_TRIGGERED, PROVIDER_ATTEMPTS
from app.providers.base import BaseProvider, ChatMessage, ChatResponse, ProviderError

logger = logging.getLogger("gateway.router")


class AllProvidersFailedError(Exception):
    """Raised when every provider in the fallback chain has failed."""


class FallbackRouter:
    """Tries providers in order, falling back to the next on ProviderError."""

    def __init__(self, chain: list[tuple[BaseProvider, str]]):
        # chain is a list of (provider, model) pairs, tried in order
        self._chain = chain

    async def chat(self, messages: list[ChatMessage]) -> ChatResponse:
        errors: list[str] = []

        for attempt, (provider, model) in enumerate(self._chain):
            try:
                result = await provider.chat(model=model, messages=messages)
                PROVIDER_ATTEMPTS.labels(provider=provider.name, outcome="success").inc()
                if attempt > 0:
                    FALLBACK_TRIGGERED.inc()
                return result
            except ProviderError as exc:
                PROVIDER_ATTEMPTS.labels(provider=provider.name, outcome="error").inc()
                logger.warning("provider %s failed, falling back: %s", provider.name, exc)
                errors.append(f"{provider.name}: {exc}")

        raise AllProvidersFailedError(
            f"all providers in fallback chain failed: {'; '.join(errors)}"
        )
