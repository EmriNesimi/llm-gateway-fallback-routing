"""Maps a virtual model name to an ordered fallback chain of (provider, real_model)."""

FALLBACK_CHAINS: dict[str, list[tuple[str, str]]] = {
    "default": [
        ("openai", "gpt-4o-mini"),
        ("anthropic", "claude-3-5-haiku-20241022"),
        ("ollama", "llama3"),
    ],
}


def get_chain(virtual_model: str) -> list[tuple[str, str]]:
    return FALLBACK_CHAINS.get(virtual_model, FALLBACK_CHAINS["default"])
