import logging
from collections.abc import AsyncIterator
from typing import cast

from anthropic import AnthropicError, APIStatusError, AsyncAnthropic
from anthropic.types import MessageParam

from app.providers.base import (
    BaseProvider,
    ChatMessage,
    ChatResponse,
    ProviderError,
    SamplingParams,
    StreamChunk,
    is_retryable_status_code,
)

logger = logging.getLogger("gateway.provider.anthropic")

# Anthropic requires max_tokens on every request, unlike the other two
# providers, so an unset one still needs a number.
DEFAULT_MAX_TOKENS = 1024

# Sampling controls were removed from the Claude 4.7+ and 5-family models:
# sending temperature or top_p to one is a 400, not a warning. That matters
# beyond the failed call — is_retryable_status_code treats 4xx as
# non-retryable, so the router falls straight through to the next provider.
# The "smart" chain leads with claude-opus-5, so forwarding a temperature
# naively would silently demote every such request to its OpenAI fallback, at
# several times the cost, with only a log line to say why.
_MODEL_PREFIXES_WITHOUT_SAMPLING_CONTROLS = (
    "claude-opus-5",
    "claude-opus-4-7",
    "claude-opus-4-8",
    "claude-sonnet-5",
    "claude-fable-5",
    "claude-mythos-5",
)


def _accepts_sampling_controls(model: str) -> bool:
    return not model.startswith(_MODEL_PREFIXES_WITHOUT_SAMPLING_CONTROLS)


def _sampling_kwargs(model: str, params: SamplingParams | None) -> dict:
    kwargs: dict = {"max_tokens": DEFAULT_MAX_TOKENS}
    if params is None:
        return kwargs

    if params.max_tokens is not None:
        kwargs["max_tokens"] = params.max_tokens
    if params.stop is not None:
        kwargs["stop_sequences"] = params.stop

    wanted = [n for n in ("temperature", "top_p") if getattr(params, n) is not None]
    if not wanted:
        return kwargs

    if _accepts_sampling_controls(model):
        for name in wanted:
            kwargs[name] = getattr(params, name)
    else:
        # Dropped rather than forwarded: forwarding is a hard 400, and a
        # silently-skipped provider is worse than a silently-ignored knob.
        logger.warning(
            "%s does not accept %s; dropping them rather than sending a request"
            " the API would reject",
            model,
            ", ".join(wanted),
        )
    return kwargs


def _to_anthropic_messages(messages: list[ChatMessage]) -> list[MessageParam]:
    # ChatMessage.role is a plain str (validated at the API boundary in
    # app/schemas.py); the SDK wants its narrower Literal["user", "assistant"],
    # so this cast documents "already validated" rather than re-checking here.
    return cast(
        "list[MessageParam]", [{"role": m.role, "content": m.content} for m in messages]
    )


def _provider_error(prefix: str, exc: AnthropicError) -> ProviderError:
    retryable = True
    if isinstance(exc, APIStatusError):
        retryable = is_retryable_status_code(exc.status_code)
    return ProviderError(f"{prefix}: {exc}", retryable=retryable)


class AnthropicProvider(BaseProvider):
    name = "anthropic"

    def __init__(self, api_key: str, timeout_seconds: float = 30.0):
        self._client = AsyncAnthropic(api_key=api_key, timeout=timeout_seconds)

    async def chat(
        self,
        model: str,
        messages: list[ChatMessage],
        params: SamplingParams | None = None,
    ) -> ChatResponse:
        try:
            response = await self._client.messages.create(
                model=model,
                messages=_to_anthropic_messages(messages),
                **_sampling_kwargs(model, params),
            )
        except AnthropicError as exc:
            raise _provider_error("anthropic request failed", exc) from exc

        text = "".join(block.text for block in response.content if block.type == "text")

        return ChatResponse(
            content=text,
            provider=self.name,
            model=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )

    async def chat_stream(
        self,
        model: str,
        messages: list[ChatMessage],
        params: SamplingParams | None = None,
    ) -> AsyncIterator[StreamChunk]:
        try:
            async with self._client.messages.stream(
                model=model,
                messages=_to_anthropic_messages(messages),
                **_sampling_kwargs(model, params),
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
            raise _provider_error("anthropic stream failed", exc) from exc
