from fastapi import Depends, FastAPI, HTTPException

from app.budget.dependency import enforce_budget, tracker as budget_tracker
from app.budget.pricing import estimate_cost_usd
from app.providers.base import ChatMessage
from app.routing.dependencies import build_router
from app.routing.fallback import AllProvidersFailedError
from app.schemas import ChatRequest, ChatResponseOut

app = FastAPI(
    title="LLM Gateway",
    description="Resilient LLM gateway with provider fallback, rate limiting, and observability.",
    version="0.1.0",
)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.get("/")
async def root():
    return {"service": "llm-gateway", "docs": "/docs"}


@app.post("/v1/chat", response_model=ChatResponseOut)
async def chat(request: ChatRequest, api_key: str = Depends(enforce_budget)):
    router = build_router(request.model)
    messages = [ChatMessage(role=m.role, content=m.content) for m in request.messages]

    try:
        result = await router.chat(messages)
    except AllProvidersFailedError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

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
