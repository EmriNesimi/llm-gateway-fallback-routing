from app.providers.anthropic_provider import AnthropicProvider
from app.providers.ollama_provider import OllamaProvider
from app.providers.openai_provider import OpenAIProvider


def test_openai_provider_applies_configured_timeout():
    provider = OpenAIProvider(api_key="test-key", timeout_seconds=12.5)
    assert provider._client.timeout == 12.5


def test_anthropic_provider_applies_configured_timeout():
    provider = AnthropicProvider(api_key="test-key", timeout_seconds=12.5)
    assert provider._client.timeout == 12.5


def test_ollama_provider_applies_configured_timeout():
    provider = OllamaProvider(base_url="http://localhost:11434", timeout_seconds=99.0)
    assert provider._timeout_seconds == 99.0
