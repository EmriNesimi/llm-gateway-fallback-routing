from app.core.config import settings
from app.providers.anthropic_provider import AnthropicProvider
from app.providers.base import BaseProvider
from app.providers.ollama_provider import OllamaProvider
from app.providers.openai_provider import OpenAIProvider
from app.routing.fallback import FallbackRouter
from app.routing.model_map import get_chain

_PROVIDER_INSTANCES: dict[str, BaseProvider] = {}


def _get_provider(name: str) -> BaseProvider:
    if name in _PROVIDER_INSTANCES:
        return _PROVIDER_INSTANCES[name]

    if name == "openai":
        provider: BaseProvider = OpenAIProvider(api_key=settings.openai_api_key or "")
    elif name == "anthropic":
        provider = AnthropicProvider(api_key=settings.anthropic_api_key or "")
    elif name == "ollama":
        provider = OllamaProvider(base_url=settings.ollama_base_url)
    else:
        raise ValueError(f"unknown provider: {name}")

    _PROVIDER_INSTANCES[name] = provider
    return provider


def build_router(virtual_model: str) -> FallbackRouter:
    chain = [(_get_provider(name), model) for name, model in get_chain(virtual_model)]
    return FallbackRouter(chain)
