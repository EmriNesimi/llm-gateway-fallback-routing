import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from app.admin.routes import router as admin_router
from app.budget.dependency import enforce_budget
from app.budget.dependency import tracker as budget_tracker
from app.budget.pricing import estimate_cost_usd
from app.core.config import settings
from app.core.redis_client import get_redis
from app.db.audit import record_audit_log
from app.db.session import async_session, engine, init_db
from app.observability.logging_config import configure_logging
from app.observability.metrics import COST_USD, REQUEST_COUNT, REQUEST_LATENCY, TOKENS
from app.observability.request_id import RequestIDMiddleware
from app.observability.security_headers import SecurityHeadersMiddleware
from app.observability.tracing import configure_tracing
from app.providers.base import ChatMessage, SamplingParams
from app.routing.dependencies import build_router
from app.routing.fallback import AllProvidersFailedError, FallbackRouter
from app.routing.model_map import (
    DEFAULT_CHAIN_NAME,
    FALLBACK_CHAINS,
    is_routable,
    routable_models,
)
from app.schemas import (
    ChatCompletionChoice,
    ChatCompletionMessage,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatCompletionUsage,
    ChatRequest,
    ChatResponseOut,
)
from app.security.auth import require_api_key

configure_logging()
configure_tracing()

logger = logging.getLogger("gateway.main")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await init_db()
    yield
    # Close pooled connections explicitly on shutdown rather than letting the
    # process exit drop them — avoids noisy "connection reset" warnings from
    # Redis/Postgres when the container is stopped.
    await get_redis().aclose()
    await engine.dispose()


app = FastAPI(
    title="LLM Gateway",
    description="Resilient LLM gateway with provider fallback, rate limiting, and observability.",
    # Keep in step with the git tag — this is what /openapi.json advertises,
    # and a client pinning against it deserves the two to agree.
    version="0.2.0",
    lifespan=lifespan,
)

FastAPIInstrumentor.instrument_app(app)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(SecurityHeadersMiddleware)

if settings.allowed_cors_origins():
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_cors_origins(),
        allow_methods=["*"],
        allow_headers=["*"],
    )
app.include_router(admin_router)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> Response:
    """Catches anything that isn't an HTTPException (which FastAPI already
    handles) or AllProvidersFailedError (which the chat endpoints handle
    themselves) — an unexpected bug, a DB or Redis outage that wasn't caught
    closer to its source, etc. Without this, such a failure would still
    return 500, but as FastAPI's bare default body with no request_id, making
    it much harder to correlate a user's bug report with server-side logs.

    A handler registered for the bare `Exception` type is dispatched by
    Starlette's ServerErrorMiddleware, which wraps *outside* every
    add_middleware()'d layer (RequestIDMiddleware included) — so this
    response never passes back through that middleware and must set its own
    X-Request-ID header rather than relying on it."""
    request_id = getattr(request.state, "request_id", "")
    logger.error(
        "[request_id=%s] unhandled exception on %s %s: %s",
        request_id,
        request.method,
        request.url.path,
        exc,
        exc_info=exc,
    )
    return Response(
        content=json.dumps(
            {"error": "internal server error", "request_id": request_id}
        ),
        status_code=500,
        media_type="application/json",
        headers={"X-Request-ID": request_id},
    )


@app.get("/healthz")
async def healthz():
    """Liveness: is the process up? No dependency checks — used by orchestrators
    to decide whether to restart the container."""
    return {"status": "ok"}


async def _check_redis() -> str:
    try:
        await get_redis().ping()
        return "ok"
    except Exception as exc:  # noqa: BLE001 - report any failure, not just known types
        return f"error: {exc}"


async def _check_database() -> str:
    try:
        async with async_session() as session:
            await session.execute(text("SELECT 1"))
        return "ok"
    except Exception as exc:  # noqa: BLE001
        return f"error: {exc}"


@app.get("/readyz")
async def readyz():
    """Readiness: can this instance actually serve traffic? Checks the
    dependencies /v1/chat needs (Redis for rate limits/budgets, the DB for
    the admin API/audit log) so a load balancer can route around an instance
    that's up but can't reach them.

    Run concurrently, not sequentially — with both backends now carrying
    their own bounded timeouts (see decision 007), a naive sequential await
    would mean this endpoint's own worst-case latency is the SUM of both
    timeouts instead of the max of the two, undermining the point of a fast
    readiness probe."""
    redis_status, database_status = await asyncio.gather(_check_redis(), _check_database())
    checks = {"redis": redis_status, "database": database_status}

    healthy = all(v == "ok" for v in checks.values())
    status_code = 200 if healthy else 503
    return Response(
        content=json.dumps({"status": "ok" if healthy else "unavailable", "checks": checks}),
        status_code=status_code,
        media_type="application/json",
    )


@app.get("/")
async def root():
    return {"service": "llm-gateway", "docs": "/docs"}


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/v1/models")
async def list_models(_: str = Depends(require_api_key)):
    """The model names this gateway will route, so a client can discover them
    instead of guessing and silently landing on the default chain.

    Shaped like OpenAI's /v1/models (an object/data envelope) so the same
    client code works against either, with the provider chain added since
    knowing what a name actually resolves to is the useful part here."""
    return {
        "object": "list",
        "data": [
            {
                "id": name,
                "object": "model",
                "owned_by": "llm-gateway",
                "providers": [provider for provider, _ in FALLBACK_CHAINS[name]],
            }
            for name in routable_models()
        ],
    }


def _route(
    requested_model: str, request_id: str, *, strict: bool | None = None
) -> tuple[str, FallbackRouter]:
    """Pick the chain for a requested model, and decide what to do when there
    isn't one.

    Unrecognized names resolve to the default chain by default — that's what
    this gateway has always done, and turning it into an error would break
    /v1 under docs/api-versioning.md. What changes is that the substitution is
    now reported rather than silent. Operators who'd rather have the error can
    set STRICT_MODEL_ROUTING=true. See docs/decisions/009.

    `strict` overrides that setting for callers with no back-compat debt —
    /v1/chat/completions is new, so nothing depends on it substituting."""
    if not is_routable(requested_model):
        reject = settings.strict_model_routing if strict is None else strict
        if reject:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": f"unknown model {requested_model!r}",
                    "routable": routable_models(),
                    "request_id": request_id,
                },
            )
        logger.warning(
            "[request_id=%s] requested model %r is not routable; serving it on the"
            " %r chain. Set STRICT_MODEL_ROUTING=true to reject instead.",
            request_id,
            requested_model,
            DEFAULT_CHAIN_NAME,
        )
    return build_router(requested_model)


def _record_usage(
    provider: str, model: str, input_tokens: int, output_tokens: int, cost_usd: float
) -> None:
    """Publish spend and token counts to Prometheus.

    These already reach the audit table, but a table can't fire an alert. A
    provider is only absent when every one of them failed, in which case there
    was no usage to record."""
    if not provider:
        return
    COST_USD.labels(provider=provider, model=model).inc(cost_usd)
    TOKENS.labels(provider=provider, model=model, direction="input").inc(input_tokens)
    TOKENS.labels(provider=provider, model=model, direction="output").inc(output_tokens)


async def _serve_chat(
    router,
    messages: list[ChatMessage],
    api_key: str,
    requested_model: str,
    request_id: str,
    params: SamplingParams | None = None,
):
    """Run a non-streaming request through the router and do the bookkeeping.

    Shared by /v1/chat and /v1/chat/completions so there's exactly one
    implementation of spend recording, audit logging, and the metrics around
    them. Two copies would drift, and the direction they'd drift in is a
    request that gets served but never billed."""
    start = time.perf_counter()
    try:
        result = await router.chat(messages, request_id=request_id, params=params)
    except AllProvidersFailedError as exc:
        REQUEST_COUNT.labels(status="error").inc()
        await record_audit_log(
            api_key,
            requested_model=requested_model,
            outcome="error",
            latency_ms=(time.perf_counter() - start) * 1000,
            request_id=request_id,
        )
        raise HTTPException(
            status_code=502, detail={"error": str(exc), "request_id": request_id}
        ) from exc
    finally:
        REQUEST_LATENCY.observe(time.perf_counter() - start)

    REQUEST_COUNT.labels(status="success").inc()

    cost = estimate_cost_usd(
        provider=result.provider,
        model=result.model,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
    )
    _record_usage(
        result.provider, result.model, result.input_tokens, result.output_tokens, cost
    )
    await budget_tracker.record_spend(api_key, cost)
    await record_audit_log(
        api_key,
        requested_model=requested_model,
        outcome="success",
        provider=result.provider,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cost_usd=cost,
        latency_ms=(time.perf_counter() - start) * 1000,
        request_id=request_id,
    )
    return result


@app.post("/v1/chat", response_model=ChatResponseOut)
async def chat(
    request: ChatRequest,
    http_request: Request,
    response: Response,
    api_key: str = Depends(enforce_budget),
):
    request_id = http_request.state.request_id
    chain_name, router = _route(request.model, request_id)
    response.headers["X-Gateway-Chain"] = chain_name
    messages = [ChatMessage(role=m.role, content=m.content) for m in request.messages]

    result = await _serve_chat(router, messages, api_key, request.model, request_id)

    return ChatResponseOut(
        content=result.content,
        provider=result.provider,
        model=result.model,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
    )


async def _event_stream(
    router, messages: list[ChatMessage], api_key: str, requested_model: str, request_id: str
) -> AsyncIterator[str]:
    start = time.perf_counter()
    final_provider = ""
    final_model = ""
    input_tokens = 0
    output_tokens = 0

    try:
        async for chunk in router.chat_stream(messages, request_id=request_id):
            if chunk.done:
                final_provider = chunk.provider
                final_model = chunk.model
                input_tokens = chunk.input_tokens
                output_tokens = chunk.output_tokens
            yield f"data: {json.dumps(asdict(chunk))}\n\n"
    except AllProvidersFailedError as exc:
        # Headers/status are already committed to 200 once streaming has begun,
        # so a failure is reported as an SSE error event rather than an HTTP error.
        REQUEST_COUNT.labels(status="error").inc()
        await record_audit_log(
            api_key,
            requested_model=requested_model,
            outcome="error",
            latency_ms=(time.perf_counter() - start) * 1000,
            request_id=request_id,
        )
        yield f"event: error\ndata: {json.dumps({'error': str(exc), 'request_id': request_id})}\n\n"
        return
    finally:
        REQUEST_LATENCY.observe(time.perf_counter() - start)

    REQUEST_COUNT.labels(status="success").inc()

    cost = 0.0
    if final_provider:
        cost = estimate_cost_usd(
            provider=final_provider,
            model=final_model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        _record_usage(final_provider, final_model, input_tokens, output_tokens, cost)
        await budget_tracker.record_spend(api_key, cost)

    await record_audit_log(
        api_key,
        requested_model=requested_model,
        outcome="success",
        provider=final_provider,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost,
        latency_ms=(time.perf_counter() - start) * 1000,
        request_id=request_id,
    )

    yield "data: [DONE]\n\n"


@app.post("/v1/chat/stream")
async def chat_stream(
    request: ChatRequest, http_request: Request, api_key: str = Depends(enforce_budget)
):
    request_id = http_request.state.request_id
    chain_name, router = _route(request.model, request_id)
    messages = [ChatMessage(role=m.role, content=m.content) for m in request.messages]
    response = StreamingResponse(
        _event_stream(router, messages, api_key, request.model, request_id),
        media_type="text/event-stream",
    )
    response.headers["X-Gateway-Chain"] = chain_name
    # enforce_rate_limit/enforce_budget stash these on request.state since this
    # endpoint builds its own Response, bypassing FastAPI's usual header merge.
    response.headers["X-RateLimit-Limit"] = str(http_request.state.rate_limit_limit)
    response.headers["X-RateLimit-Remaining"] = str(http_request.state.rate_limit_remaining)
    response.headers["X-Budget-Remaining-USD"] = f"{http_request.state.budget_remaining_usd:.4f}"
    return response


# ---------------------------------------------------------------------------
# OpenAI-compatible surface
# ---------------------------------------------------------------------------


async def _openai_event_stream(
    router,
    messages: list[ChatMessage],
    api_key: str,
    requested_model: str,
    request_id: str,
    completion_id: str,
    created: int,
    params: SamplingParams | None = None,
) -> AsyncIterator[str]:
    """SSE in OpenAI's `chat.completion.chunk` shape.

    Differs from _event_stream in wire format only — same router, same
    first-chunk-buffered fallback, same bookkeeping. The role arrives in the
    first delta and the last delta carries finish_reason, which is the
    sequence the OpenAI SDK's stream parser expects."""
    start = time.perf_counter()
    final_provider = ""
    final_model = requested_model
    input_tokens = 0
    output_tokens = 0
    first = True

    def envelope(delta: dict, finish_reason: str | None, model: str) -> str:
        payload = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }
        return f"data: {json.dumps(payload)}\n\n"

    try:
        async for chunk in router.chat_stream(
            messages, request_id=request_id, params=params
        ):
            if chunk.done:
                final_provider = chunk.provider
                final_model = chunk.model
                input_tokens = chunk.input_tokens
                output_tokens = chunk.output_tokens
                continue
            if first:
                yield envelope({"role": "assistant"}, None, chunk.model or requested_model)
                first = False
            yield envelope({"content": chunk.content}, None, chunk.model or requested_model)
    except AllProvidersFailedError as exc:
        # Status is already committed to 200 once streaming has begun, so this
        # is reported in-band. OpenAI's SDK surfaces an `error` field on a
        # chunk as an exception, which is the closest thing to the 502 a
        # non-streaming caller would have received.
        REQUEST_COUNT.labels(status="error").inc()
        await record_audit_log(
            api_key,
            requested_model=requested_model,
            outcome="error",
            latency_ms=(time.perf_counter() - start) * 1000,
            request_id=request_id,
        )
        yield (
            "data: "
            + json.dumps({"error": {"message": str(exc), "request_id": request_id}})
            + "\n\n"
        )
        return
    finally:
        REQUEST_LATENCY.observe(time.perf_counter() - start)

    REQUEST_COUNT.labels(status="success").inc()

    cost = 0.0
    if final_provider:
        cost = estimate_cost_usd(
            provider=final_provider,
            model=final_model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        _record_usage(final_provider, final_model, input_tokens, output_tokens, cost)
        await budget_tracker.record_spend(api_key, cost)

    await record_audit_log(
        api_key,
        requested_model=requested_model,
        outcome="success",
        provider=final_provider,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost,
        latency_ms=(time.perf_counter() - start) * 1000,
        request_id=request_id,
    )

    yield envelope({}, "stop", final_model)
    yield "data: [DONE]\n\n"


@app.post("/v1/chat/completions")
async def chat_completions(
    request: ChatCompletionRequest,
    http_request: Request,
    response: Response,
    api_key: str = Depends(enforce_budget),
):
    """OpenAI Chat Completions, so an unmodified `openai` client can point its
    base_url here and get the fallback chain, rate limiting, budgets, and audit
    trail without changing a line of application code.

    Routes strictly: this endpoint has no existing callers, so unlike
    /v1/chat it can reject an unroutable model from day one rather than
    inheriting the silent substitution /v1 is stuck with. See decision 009."""
    request_id = http_request.state.request_id
    chain_name, router = _route(request.model, request_id, strict=True)
    messages = [ChatMessage(role=m.role, content=m.content) for m in request.messages]

    params = request.sampling_params()
    ignored = request.ignored_params()
    if ignored:
        # Whatever's left after the forwarded ones: n, seed, presence_penalty
        # and the like. Accepted for compatibility, but never silently.
        logger.warning(
            "[request_id=%s] ignoring unsupported completion parameters: %s",
            request_id,
            ", ".join(ignored),
        )

    if request.stream:
        stream_response = StreamingResponse(
            _openai_event_stream(
                router,
                messages,
                api_key,
                request.model,
                request_id,
                completion_id=f"chatcmpl-{uuid.uuid4().hex}",
                created=int(time.time()),
                params=params,
            ),
            media_type="text/event-stream",
        )
        stream_response.headers["X-Gateway-Chain"] = chain_name
        stream_response.headers["X-RateLimit-Limit"] = str(http_request.state.rate_limit_limit)
        stream_response.headers["X-RateLimit-Remaining"] = str(
            http_request.state.rate_limit_remaining
        )
        stream_response.headers["X-Budget-Remaining-USD"] = (
            f"{http_request.state.budget_remaining_usd:.4f}"
        )
        if ignored:
            stream_response.headers["X-Gateway-Ignored-Params"] = ",".join(ignored)
        return stream_response

    response.headers["X-Gateway-Chain"] = chain_name
    if ignored:
        response.headers["X-Gateway-Ignored-Params"] = ",".join(ignored)

    result = await _serve_chat(
        router, messages, api_key, request.model, request_id, params=params
    )

    return ChatCompletionResponse(
        id=f"chatcmpl-{uuid.uuid4().hex}",
        created=int(time.time()),
        model=result.model,
        choices=[
            ChatCompletionChoice(
                message=ChatCompletionMessage(content=result.content),
            )
        ],
        usage=ChatCompletionUsage(
            prompt_tokens=result.input_tokens,
            completion_tokens=result.output_tokens,
            total_tokens=result.input_tokens + result.output_tokens,
        ),
    )
