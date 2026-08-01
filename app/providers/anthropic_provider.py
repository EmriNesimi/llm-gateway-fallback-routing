from collections.abc import AsyncIterator

from anthropic import AnthropicError, AsyncAnthropic

from app.providers.base import BaseProvider, ChatMessage, ChatResponse, ProviderError, StreamChunk


class AnthropicProvider(BaseProvider):
    name = "anthropic"

    def __init__(self, api_key: str):
        self._client = AsyncAnthropic(api_key=api_key)

    async def chat(self, model: str, messages: list[ChatMessage]) -> ChatResponse:
        try:
            response = await self._client.messages.create(
                model=model,
                max_tokens=1024,
                messages=[{"role": m.role, "content": m.content} for m in messages],
            )
        except AnthropicError as exc:
            raise ProviderError(f"anthropic request failed: {exc}") from exc

        text = "".join(block.text for block in response.content if block.type == "text")

        return ChatResponse(
            content=text,
            provider=self.name,
            model=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )

    async def chat_stream(
        self, model: str, messages: list[ChatMessage]
    ) -> AsyncIterator[StreamChunk]:
        try:
            async with self._client.messages.stream(
                model=model,
                max_tokens=1024,
                messages=[{"role": m.role, "content": m.content} for m in messages],
            ) as stream:
                async for text in stream.text_stream:
                    yield StreamChunk(content=text)

                final = await stream.get_final_message()
                yield StreamChunk(
                    content="",
                    done=True,
                    input_tokens=final.usage.input_tokens,
                    output_tokens=final.usage.output_tokens,
                )
        except AnthropicError as exc:
            raise ProviderError(f"anthropic stream failed: {exc}") from exc
