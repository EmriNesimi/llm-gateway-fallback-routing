import time

from fastapi import Depends, FastAPI, HTTPException, Response
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.budget.dependency import enforce_budget, tracker as budget_tracker
from app.budget.pricing import estimate_cost_usd
from app.observability.metrics import REQUEST_COUNT, REQUEST_LATENCY
from app.observability.tracing import configure_tracing
from app.providers.base import ChatMessage
from app.routing.dependencies import build_router
from app.routing.fallback import AllProvidersFailedError
from app.schemas import ChatRequest, ChatResponseOut

configure_tracing()

app = FastAPI(
    title="LLM Gateway",
    description="Resilient LLM gateway with provider fallback, rate limiting, and observability.",
    version="0.1.0",
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

    return ChatResponseOut(
        content=result.content,
        provider=result.provider,
        model=result.model,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
    )
