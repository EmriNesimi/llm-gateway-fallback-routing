import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from app.admin.routes import router as admin_router
from app.budget.dependency import enforce_budget
from app.budget.dependency import tracker as budget_tracker
from app.budget.pricing import estimate_cost_usd
from app.core.redis_client import get_redis
from app.db.audit import record_audit_log
from app.db.session import async_session, init_db
from app.observability.metrics import REQUEST_COUNT, REQUEST_LATENCY
from app.observability.request_id import RequestIDMiddleware
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
app.add_middleware(RequestIDMiddleware)
app.include_router(admin_router)


@app.get("/healthz")
async def healthz():
    """Liveness: is the process up? No dependency checks — used by orchestrators
    to decide whether to restart the container."""
    return {"status": "ok"}


@app.get("/readyz")
async def readyz():
    """Readiness: can this instance actually serve traffic? Checks the
    dependencies /v1/chat needs (Redis for rate limits/budgets, the DB for
    the admin API/audit log) so a load balancer can route around an instance
    that's up but can't reach them."""
    checks: dict[str, str] = {}

    try:
        await get_redis().ping()
        checks["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001 - report any failure, not just known types
        checks["redis"] = f"error: {exc}"

    try:
        async with async_session() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["database"] = f"error: {exc}"

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


@app.post("/v1/chat", response_model=ChatResponseOut)
async def chat(
    request: ChatRequest, http_request: Request, api_key: str = Depends(enforce_budget)
):
    router = build_router(request.model)
    messages = [ChatMessage(role=m.role, content=m.content) for m in request.messages]
    request_id = http_request.state.request_id

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
        request_id=request_id,
    )

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
    router = build_router(request.model)
    messages = [ChatMessage(role=m.role, content=m.content) for m in request.messages]
    response = StreamingResponse(
        _event_stream(router, messages, api_key, request.model, http_request.state.request_id),
        media_type="text/event-stream",
    )
    # enforce_rate_limit/enforce_budget stash these on request.state since this
    # endpoint builds its own Response, bypassing FastAPI's usual header merge.
    response.headers["X-RateLimit-Limit"] = str(http_request.state.rate_limit_limit)
    response.headers["X-RateLimit-Remaining"] = str(http_request.state.rate_limit_remaining)
    response.headers["X-Budget-Remaining-USD"] = f"{http_request.state.budget_remaining_usd:.4f}"
    return response
