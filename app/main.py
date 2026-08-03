import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict

from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.responses import StreamingResponse
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.budget.dependency import enforce_budget
from app.budget.dependency import tracker as budget_tracker
from app.budget.pricing import estimate_cost_usd
from app.db.audit import record_audit_log
from app.db.session import init_db
from app.observability.metrics import REQUEST_COUNT, REQUEST_LATENCY
from app.observability.tracing import configure_tracing
from app.providers.base import ChatMessage
from app.routing.dependencies import build_router
from app.routing.fallback import AllProvidersFailedError
from app.schemas import ChatRequest, ChatResponseOut

configure_tracing()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await init_db()
    yield


app = FastAPI(
    title="LLM Gateway",
    description="Resilient LLM gateway with provider fallback, rate limiting, and observability.",
    version="0.1.0",
    lifespan=lifespan,
)

FastAPIInstrumentor.instrument_app(app)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.get("/")
async def root():
    return {"service": "llm-gateway", "docs": "/docs"}


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/v1/chat", response_model=ChatResponseOut)
async def chat(request: ChatRequest, api_key: str = Depends(enforce_budget)):
    router = build_router(request.model)
    messages = [ChatMessage(role=m.role, content=m.content) for m in request.messages]

    start = time.perf_counter()
    try:
        result = await router.chat(messages)
    except AllProvidersFailedError as exc:
        REQUEST_COUNT.labels(status="error").inc()
        await record_audit_log(
            api_key,
            requested_model=request.model,
            outcome="error",
            latency_ms=(time.perf_counter() - start) * 1000,
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        REQUEST_LATENCY.observe(time.perf_counter() - start)

    REQUEST_COUNT.labels(status="success").inc()

    cost = estimate_cost_usd(
        provider=result.provider,
        model=result.model,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
    )
    await budget_tracker.record_spend(api_key, cost)
    await record_audit_log(
        api_key,
        requested_model=request.model,
        outcome="success",
        provider=result.provider,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cost_usd=cost,
        latency_ms=(time.perf_counter() - start) * 1000,
    )

    return ChatResponseOut(
        content=result.content,
        provider=result.provider,
        model=result.model,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
    )


async def _event_stream(
    router, messages: list[ChatMessage], api_key: str, requested_model: str
) -> AsyncIterator[str]:
    start = time.perf_counter()
    final_provider = ""
    final_model = ""
    input_tokens = 0
    output_tokens = 0

    try:
        async for chunk in router.chat_stream(messages):
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
        )
        yield f"event: error\ndata: {json.dumps({'error': str(exc)})}\n\n"
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
    )

    yield "data: [DONE]\n\n"


@app.post("/v1/chat/stream")
async def chat_stream(request: ChatRequest, api_key: str = Depends(enforce_budget)):
    router = build_router(request.model)
    messages = [ChatMessage(role=m.role, content=m.content) for m in request.messages]
    return StreamingResponse(
        _event_stream(router, messages, api_key, request.model),
        media_type="text/event-stream",
    )
