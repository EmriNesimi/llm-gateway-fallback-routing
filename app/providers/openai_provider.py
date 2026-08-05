from collections.abc import AsyncIterator

from openai import APIError, APIStatusError, AsyncOpenAI

from app.providers.base import BaseProvider, ChatMessage, ChatResponse, ProviderError, StreamChunk


class OpenAIProvider(BaseProvider):
    name = "openai"

    def __init__(self, api_key: str, timeout_seconds: float = 30.0):
        self._client = AsyncOpenAI(api_key=api_key, timeout=timeout_seconds)

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

    async def chat_stream(
        self, model: str, messages: list[ChatMessage]
    ) -> AsyncIterator[StreamChunk]:
        try:
            stream = await self._client.chat.completions.create(
                model=model,
                messages=[{"role": m.role, "content": m.content} for m in messages],
                stream=True,
                stream_options={"include_usage": True},
            )

            async for event in stream:
                if event.usage:
                    yield StreamChunk(
                        content="",
                        done=True,
                        input_tokens=event.usage.prompt_tokens,
                        output_tokens=event.usage.completion_tokens,
                    )
                elif event.choices and event.choices[0].delta.content:
                    yield StreamChunk(content=event.choices[0].delta.content)
        except (APIError, APIStatusError) as exc:
            raise ProviderError(f"openai stream failed: {exc}") from exc
