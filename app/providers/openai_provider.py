import logging
from collections.abc import AsyncIterator
from typing import cast

from openai import APIError, APIStatusError, AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from app.providers.base import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    BaseProvider,
    ChatMessage,
    ChatResponse,
    ProviderError,
    SamplingParams,
    StreamChunk,
    is_retryable_status_code,
)

logger = logging.getLogger("gateway.openai")

# Matches app/budget/pricing.py. Deliberately low, so dividing by it
# over-estimates tokens rather than under-estimating them.
_CHARS_PER_TOKEN = 3


def _sampling_kwargs(params: SamplingParams | None) -> dict:
    """Only the controls the caller actually set. Passing None values through
    would override the provider's own defaults with nulls."""
    if params is None:
        return {"max_tokens": DEFAULT_MAX_OUTPUT_TOKENS}
    kwargs: dict = {}
    if params.temperature is not None:
        kwargs["temperature"] = params.temperature
    if params.top_p is not None:
        kwargs["top_p"] = params.top_p
    # Always sent, unlike the others. temperature and top_p are safe to omit —
    # the provider's own default is a sensible value nobody is paying extra
    # for. max_tokens is not: omitting it lets the model generate up to its
    # own ceiling, which for gpt-4o-mini is many times the 2048 tokens the
    # gateway already reserved budget against. The reservation would stop
    # being an upper bound, which is the entire basis of the spend ceiling.
    kwargs["max_tokens"] = (
        params.max_tokens if params.max_tokens is not None else DEFAULT_MAX_OUTPUT_TOKENS
    )
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
        content = choice.message.content or ""
        usage = response.usage

        if usage is None:
            # Same class of oddity as the empty `choices` above — some proxies
            # in front of the real API drop it. Reporting 0 tokens would make
            # the request cost $0, and _settle_chain would hand the entire
            # reservation back for a call the provider has already billed:
            # real spend, refunded as if it never happened.
            #
            # Estimating from characters is wrong, but wrong in the safe
            # direction and loudly. Silence was the actual problem.
            input_tokens = max(1, sum(len(m.content) for m in messages) // _CHARS_PER_TOKEN)
            output_tokens = max(1, len(content) // _CHARS_PER_TOKEN)
            logger.warning(
                "openai returned no usage for model %s — charging an estimate of"
                " %d in / %d out from character counts. Spend for this request"
                " is approximate.",
                response.model,
                input_tokens,
                output_tokens,
            )
        else:
            input_tokens = usage.prompt_tokens
            output_tokens = usage.completion_tokens

        return ChatResponse(
            content=content,
            provider=self.name,
            model=response.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
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
