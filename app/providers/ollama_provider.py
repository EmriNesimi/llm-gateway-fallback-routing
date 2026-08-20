import json
from collections.abc import AsyncIterator

import httpx

from app.providers.base import (
    BaseProvider,
    ChatMessage,
    ChatResponse,
    ProviderError,
    SamplingParams,
    StreamChunk,
    is_retryable_status_code,
)


def _sampling_payload(params: SamplingParams | None) -> dict:
    """Ollama nests generation controls under `options` and names the output
    cap `num_predict` rather than `max_tokens`. Returns an empty dict when
    nothing was set, so the key is absent rather than present-and-empty —
    Ollama treats an explicit empty options block differently from no block.
    """
    if params is None:
        return {}
    options: dict = {}
    if params.temperature is not None:
        options["temperature"] = params.temperature
    if params.top_p is not None:
        options["top_p"] = params.top_p
    if params.max_tokens is not None:
        options["num_predict"] = params.max_tokens
    if params.stop is not None:
        options["stop"] = params.stop
    return {"options": options} if options else {}


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

    async def chat(
        self,
        model: str,
        messages: list[ChatMessage],
        params: SamplingParams | None = None,
    ) -> ChatResponse:
        payload = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": False,
            **_sampling_payload(params),
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
        self,
        model: str,
        messages: list[ChatMessage],
        params: SamplingParams | None = None,
    ) -> AsyncIterator[StreamChunk]:
        payload = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": True,
            **_sampling_payload(params),
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
