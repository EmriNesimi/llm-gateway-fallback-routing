"""Maps a virtual model name to an ordered fallback chain of (provider, real_model).

A caller picks a chain by name via the request's `model` field. The names are
capability tiers rather than real model IDs, because the whole point of the
gateway is that the caller shouldn't have to care which provider answers — a
chain can be re-pointed at a different model without the client changing.

Every model named here needs a matching entry in app/budget/pricing.py, or it
costs $0.00 and escapes budget enforcement entirely. tests/test_pricing.py
fails the build if that ever drifts.
"""

FALLBACK_CHAINS: dict[str, list[tuple[str, str]]] = {
    # The entry point for callers that don't pick a tier. Same shape it's
    # always had: cheap hosted primary, cheap hosted backup, local last resort.
    "default": [
        ("openai", "gpt-4o-mini"),
        ("anthropic", "claude-haiku-4-5"),
        ("ollama", "llama3"),
    ],
    # An explicit alias for "default" — worth naming separately so the intent
    # is in the request rather than implied, and so "default" stays free to be
    # re-pointed later without silently changing what a caller asked for.
    "fast": [
        ("openai", "gpt-4o-mini"),
        ("anthropic", "claude-haiku-4-5"),
        ("ollama", "llama3"),
    ],
    # Deliberately has no Ollama hop: a caller who asked for the strongest
    # available model is better served by a clear 502 than by a local 8B model
    # quietly answering in its place.
    "smart": [
        ("anthropic", "claude-opus-5"),
        ("openai", "gpt-4o"),
    ],
    # No hosted providers, so no spend and nothing leaves the host.
    "local": [
        ("ollama", "llama3"),
    ],
}

DEFAULT_CHAIN_NAME = "default"


def routable_models() -> list[str]:
    """Chain names a caller may pass as `model`, for discovery and errors."""
    return sorted(FALLBACK_CHAINS)


def is_routable(virtual_model: str) -> bool:
    return virtual_model in FALLBACK_CHAINS


def resolve_chain(virtual_model: str) -> tuple[str, list[tuple[str, str]]]:
    """Resolve a requested model to (chain_name, chain).

    An unrecognized name resolves to the default chain — the behavior this
    gateway has always had. The caller gets the name back so the substitution
    can be reported rather than hidden; see docs/decisions/009.
    """
    if virtual_model in FALLBACK_CHAINS:
        return virtual_model, FALLBACK_CHAINS[virtual_model]
    return DEFAULT_CHAIN_NAME, FALLBACK_CHAINS[DEFAULT_CHAIN_NAME]
