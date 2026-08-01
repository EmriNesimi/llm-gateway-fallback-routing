import json
from collections.abc import AsyncIterator

import httpx

from app.providers.base import BaseProvider, ChatMessage, ChatResponse, ProviderError, StreamChunk


class OllamaProvider(BaseProvider):
    name = "ollama"

    def __init__(self, base_url: str):
        self._base_url = base_url.rstrip("/")

    async def chat(self, model: str, messages: list[ChatMessage]) -> ChatResponse:
        payload = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": False,
        }

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(f"{self._base_url}/api/chat", json=payload)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderError(f"ollama request failed: {exc}") from exc

        data = response.json()

        return ChatResponse(
            content=data["message"]["content"],
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
            async with httpx.AsyncClient(timeout=60.0) as client:
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
                            yield StreamChunk(content=data.get("message", {}).get("content", ""))
        except httpx.HTTPError as exc:
            raise ProviderError(f"ollama stream failed: {exc}") from exc
