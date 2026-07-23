from openai import APIError, APIStatusError, AsyncOpenAI

from app.providers.base import BaseProvider, ChatMessage, ChatResponse, ProviderError


class OpenAIProvider(BaseProvider):
    name = "openai"

    def __init__(self, api_key: str):
        self._client = AsyncOpenAI(api_key=api_key)

    async def chat(self, model: str, messages: list[ChatMessage]) -> ChatResponse:
        try:
            response = await self._client.chat.completions.create(
                model=model,
                messages=[{"role": m.role, "content": m.content} for m in messages],
            )
        except (APIError, APIStatusError) as exc:
            raise ProviderError(f"openai request failed: {exc}") from exc

        choice = response.choices[0]
        usage = response.usage

        return ChatResponse(
            content=choice.message.content or "",
            provider=self.name,
            model=response.model,
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
        )
