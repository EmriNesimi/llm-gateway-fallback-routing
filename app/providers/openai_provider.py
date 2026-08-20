from collections.abc import AsyncIterator
from typing import cast

from openai import APIError, APIStatusError, AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from app.providers.base import (
    BaseProvider,
    ChatMessage,
    ChatResponse,
    ProviderError,
    SamplingParams,
    StreamChunk,
    is_retryable_status_code,
)


def _sampling_kwargs(params: SamplingParams | None) -> dict:
    """Only the controls the caller actually set. Passing None values through
    would override the provider's own defaults with nulls."""
    if params is None:
        return {}
    kwargs: dict = {}
    if params.temperature is not None:
        kwargs["temperature"] = params.temperature
    if params.top_p is not None:
        kwargs["top_p"] = params.top_p
    if params.max_tokens is not None:
        kwargs["max_tokens"] = params.max_tokens
    if params.stop is not None:
        kwargs["stop"] = params.stop
    return kwargs


def _provider_error(prefix: str, exc: APIError) -> ProviderError:
    retryable = True
    if isinstance(exc, APIStatusError):
        retryable = is_retryable_status_code(exc.status_code)
    return ProviderError(f"{prefix}: {exc}", retryable=retryable)


def _to_openai_messages(messages: list[ChatMessage]) -> list[ChatCompletionMessageParam]:
    # ChatMessage.role is a plain str (validated at the API boundary in
    # app/schemas.py); the SDK wants one of its narrower per-role TypedDicts,
    # so this cast documents "already validated" rather than re-checking here.
    return cast(
        "list[ChatCompletionMessageParam]",
        [{"role": m.role, "content": m.content} for m in messages],
    )


class OpenAIProvider(BaseProvider):
    name = "openai"

    def __init__(self, api_key: str, timeout_seconds: float = 30.0):
        self._client = AsyncOpenAI(api_key=api_key, timeout=timeout_seconds)

    async def chat(
        self,
        model: str,
        messages: list[ChatMessage],
        params: SamplingParams | None = None,
    ) -> ChatResponse:
        try:
            response = await self._client.chat.completions.create(
                model=model,
                messages=_to_openai_messages(messages),
                **_sampling_kwargs(params),
            )
        except (APIError, APIStatusError) as exc:
            raise _provider_error("openai request failed", exc) from exc

        if not response.choices:
            # A 2xx with an empty choices array (seen from some proxies/
            # gateways sitting in front of the real API) is as unusable as an
            # HTTP error — it must raise ProviderError so FallbackRouter
            # falls back, rather than an IndexError skipping the fallback
            # chain entirely.
            raise ProviderError("openai response contained no choices")

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
        self,
        model: str,
        messages: list[ChatMessage],
        params: SamplingParams | None = None,
    ) -> AsyncIterator[StreamChunk]:
        try:
            stream = await self._client.chat.completions.create(
                model=model,
                messages=_to_openai_messages(messages),
                stream=True,
                stream_options={"include_usage": True},
                **_sampling_kwargs(params),
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
            raise _provider_error("openai stream failed", exc) from exc
