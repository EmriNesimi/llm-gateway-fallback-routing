import logging

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

        for provider, model in self._chain:
            try:
                return await provider.chat(model=model, messages=messages)
            except ProviderError as exc:
                logger.warning("provider %s failed, falling back: %s", provider.name, exc)
                errors.append(f"{provider.name}: {exc}")

        raise AllProvidersFailedError(
            f"all providers in fallback chain failed: {'; '.join(errors)}"
        )
