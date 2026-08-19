from app.core.config import settings
from app.providers.anthropic_provider import AnthropicProvider
from app.providers.base import BaseProvider, UnconfiguredProvider
from app.providers.ollama_provider import OllamaProvider
from app.providers.openai_provider import OpenAIProvider
from app.routing.circuit_breaker import CircuitBreaker
from app.routing.fallback import FallbackRouter
from app.routing.model_map import resolve_chain

# Per-process, unlike the Redis-backed rate limiter and budget tracker. The
# asymmetry is deliberate: a shared breaker would let one sick replica trip
# the circuit for the whole fleet, turning a local fault into a global
# outage. See docs/decisions/010-per-process-circuit-breakers.md.
_PROVIDER_INSTANCES: dict[str, BaseProvider] = {}
_BREAKERS: dict[str, CircuitBreaker] = {}


def _get_provider(name: str) -> BaseProvider:
    if name in _PROVIDER_INSTANCES:
        return _PROVIDER_INSTANCES[name]

    if name == "openai":
        if not settings.openai_api_key:
            provider: BaseProvider = UnconfiguredProvider(name)
        else:
            provider = OpenAIProvider(
                api_key=settings.openai_api_key,
                timeout_seconds=settings.provider_request_timeout_seconds,
            )
    elif name == "anthropic":
        if not settings.anthropic_api_key:
            provider = UnconfiguredProvider(name)
        else:
            provider = AnthropicProvider(
                api_key=settings.anthropic_api_key,
                timeout_seconds=settings.provider_request_timeout_seconds,
            )
    elif name == "ollama":
        provider = OllamaProvider(
            base_url=settings.ollama_base_url,
            timeout_seconds=settings.ollama_request_timeout_seconds,
        )
    else:
        raise ValueError(f"unknown provider: {name}")

    _PROVIDER_INSTANCES[name] = provider
    return provider


def _get_breaker(name: str) -> CircuitBreaker:
    if name not in _BREAKERS:
        _BREAKERS[name] = CircuitBreaker(
            failure_threshold=settings.circuit_breaker_failure_threshold,
            cooldown_seconds=settings.circuit_breaker_cooldown_seconds,
        )
    return _BREAKERS[name]


def build_router(virtual_model: str) -> tuple[str, FallbackRouter]:
    """Build the router for a requested model, and report which chain it
    resolved to. The caller needs the name to tell the client what actually
    served the request when the requested model wasn't routable."""
    chain_name, chain_spec = resolve_chain(virtual_model)
    chain = [
        (_get_provider(name), model, _get_breaker(name)) for name, model in chain_spec
    ]
    return chain_name, FallbackRouter(chain)
