import httpx

from app.providers.base import BaseProvider, ChatMessage, ChatResponse, ProviderError


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
