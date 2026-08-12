import json
from collections.abc import AsyncIterator

import httpx

from app.providers.base import (
    BaseProvider,
    ChatMessage,
    ChatResponse,
    ProviderError,
    StreamChunk,
    is_retryable_status_code,
)


def _provider_error(prefix: str, exc: httpx.HTTPError) -> ProviderError:
    retryable = True
    if isinstance(exc, httpx.HTTPStatusError):
        retryable = is_retryable_status_code(exc.response.status_code)
    return ProviderError(f"{prefix}: {exc}", retryable=retryable)


class OllamaProvider(BaseProvider):
    name = "ollama"

    def __init__(self, base_url: str, timeout_seconds: float = 60.0):
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    async def chat(self, model: str, messages: list[ChatMessage]) -> ChatResponse:
        payload = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": False,
        }

        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.post(f"{self._base_url}/api/chat", json=payload)
                response.raise_for_status()
            data = response.json()
            content = data["message"]["content"]
        except httpx.HTTPError as exc:
            raise _provider_error("ollama request failed", exc) from exc
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            # A 2xx response with an unexpected shape (wrong Ollama version,
            # a proxy mangling the body, etc.) is just as much "this provider
            # isn't usable right now" as an HTTP error — it must also raise
            # ProviderError so FallbackRouter falls back instead of letting
            # a raw KeyError/JSONDecodeError skip the fallback chain entirely.
            raise ProviderError(f"ollama returned an unexpected response shape: {exc}") from exc

        return ChatResponse(
            content=content,
            provider=self.name,
            model=data.get("model", model),
            input_tokens=data.get("prompt_eval_count", 0),
            output_tokens=data.get("eval_count", 0),
        )

    async def chat_stream(
        self, model: str, messages: list[ChatMessage]
    ) -> AsyncIterator[StreamChunk]:
        payload = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": True,
        }

        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                async with client.stream(
                    "POST", f"{self._base_url}/api/chat", json=payload
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        data = json.loads(line)
                        if data.get("done"):
                            yield StreamChunk(
                                content="",
                                done=True,
                                input_tokens=data.get("prompt_eval_count", 0),
                                output_tokens=data.get("eval_count", 0),
                            )
                        else:
                            yield StreamChunk(content=data["message"]["content"])
        except httpx.HTTPError as exc:
            raise _provider_error("ollama stream failed", exc) from exc
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            # Same reasoning as chat(): a malformed line mid-stream must still
            # surface as ProviderError so FallbackRouter's fallback-before-
            # first-chunk logic (see docs/decisions/003) can act on it, rather
            # than an unhandled exception skipping the fallback chain.
            raise ProviderError(f"ollama returned an unexpected stream chunk shape: {exc}") from exc
