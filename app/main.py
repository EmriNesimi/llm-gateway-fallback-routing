import asyncio
import hmac
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from app.admin.routes import router as admin_router
from app.budget.dependency import enforce_budget, provider_budget
from app.budget.dependency import tracker as budget_tracker
from app.budget.pricing import (
    UnpricedModelError,
    estimate_cost_usd,
    worst_case_cost_usd,
)
from app.budget.provider_budget import FREE_PROVIDERS, ProviderBudgetExhausted
from app.core.config import settings
from app.core.redis_client import get_redis
from app.db.audit import record_audit_log
from app.db.session import async_session, engine, init_db
from app.observability.logging_config import configure_logging
from app.observability.metrics import COST_USD, REQUEST_COUNT, REQUEST_LATENCY, TOKENS
from app.observability.request_id import RequestIDMiddleware
from app.observability.security_headers import SecurityHeadersMiddleware
from app.observability.tracing import configure_tracing
from app.providers.base import ChatMessage, ChatResponse, SamplingParams
from app.routing.dependencies import build_router
from app.routing.fallback import AllProvidersFailedError, FallbackRouter
from app.routing.model_map import (
    DEFAULT_CHAIN_NAME,
    FALLBACK_CHAINS,
    is_routable,
    routable_models,
)
from app.schemas import (
    MAX_OUTPUT_TOKENS,
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
    version="0.3.0",
    lifespan=lifespan,
)

FastAPIInstrumentor.instrument_app(app)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(SecurityHeadersMiddleware)

def _configure_cors(application: FastAPI) -> bool:
    """Add the CORS middleware only when origins are configured.

    A function rather than a bare `if` at import time so both outcomes are
    reachable from a test. As module-level code the enabled branch could only
    be exercised by reimporting this module, which re-runs the instrumentation
    and the Prometheus metric definitions — so it was simply never covered,
    on the one middleware whose job is deciding which sites may call this API.

    Returns whether it was added, which is the only observable difference.
    """
    origins = settings.allowed_cors_origins()
    if not origins:
        return False
    application.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    return True


_configure_cors(app)
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
async def healthz() -> dict[str, str]:
    """Liveness: is the process up? No dependency checks — used by orchestrators
    to decide whether to restart the container."""
    return {"status": "ok"}


async def _check_redis() -> str:
    try:
        await get_redis().ping()
        return "ok"
    except Exception as exc:  # noqa: BLE001 - report any failure, not just known types
        # Logged in full, reported as one word. /readyz is unauthenticated, so
        # the exception text handed hostnames, ports, driver versions and
        # failure modes to anyone who asked — reconnaissance for attacking the
        # very backends that hold the spend counters.
        logger.error("readiness check failed for redis: %s", exc, exc_info=exc)
        return "error"


async def _check_database() -> str:
    try:
        async with async_session() as session:
            await session.execute(text("SELECT 1"))
        return "ok"
    except Exception as exc:  # noqa: BLE001
        logger.error("readiness check failed for database: %s", exc, exc_info=exc)
        return "error"


@app.get("/readyz")
async def readyz() -> Response:
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
async def root() -> dict[str, str]:
    return {"service": "llm-gateway", "docs": "/docs"}


@app.get("/metrics")
async def metrics(
    x_metrics_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> Response:
    """Prometheus scrape target.

    Gated on its own token rather than a client key, because a scraper isn't a
    client — it shouldn't hold a key that can spend money, and it shouldn't
    consume rate-limit budget. What's behind here is worth protecting:
    gateway_cost_usd_total publishes exactly how much has been spent and on
    what, alongside process-level detail from prometheus_client's default
    collectors.

    An unset METRICS_TOKEN leaves the endpoint open, which is the right
    default for a loopback-bound dev stack and is why docker-compose.yml binds
    everything to 127.0.0.1. Set it for anything reachable from elsewhere.
    """
    expected = settings.metrics_token
    # Accept the standard Authorization: Bearer as well as the custom header.
    # Prometheus scrape configs can send the former natively and have no
    # generic custom-header field, so a token-only-on-X-Metrics-Token gate
    # would be one the intended scraper physically cannot satisfy.
    presented = x_metrics_token
    if not presented and authorization and authorization.lower().startswith("bearer "):
        presented = authorization[len("bearer ") :].strip()

    if expected and not (presented and hmac.compare_digest(presented, expected)):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid metrics token"
        )
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/v1/models")
async def list_models(_: str = Depends(require_api_key)) -> dict[str, Any]:
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


async def _reserve_chain(
    chain_name: str, messages: list[ChatMessage], params: SamplingParams | None, request_id: str
) -> tuple[dict[str, float], set[str]]:
    """Claim worst-case budget on every billable provider the chain may reach.

    Reserving up front rather than checking-then-calling is what makes the
    ceiling hold under concurrency: with a burst allowance of 20, twenty
    simultaneous requests would otherwise all observe the same pre-call total
    and all proceed.

    Every provider in the chain is reserved against, not just the first,
    because fallback decides at call time which one actually answers. The
    surplus is refunded in _settle_chain — which every caller must reach, on
    success and on failure alike, or the reservation leaks out of the budget
    permanently.

    Returns (reservations, skip) — `skip` being providers already at their
    ceiling, which the router drops from the chain without calling them.
    """
    chars = sum(len(m.content) for m in messages)
    max_out = (params.max_tokens if params and params.max_tokens else MAX_OUTPUT_TOKENS)

    reservations: dict[str, float] = {}
    skip: set[str] = set()
    unpriced: set[str] = set()
    billable = 0

    for provider, model in FALLBACK_CHAINS[chain_name]:
        if provider in FREE_PROVIDERS:
            continue
        billable += 1
        try:
            cost = worst_case_cost_usd(provider, model, chars, max_out)
        except UnpricedModelError:
            # Un-costable, so the ceiling cannot apply to it. Dropped from the
            # chain exactly like an exhausted provider rather than failing the
            # whole request — a priced provider further down can still serve
            # it, and refusing outright would turn one missing table entry
            # into a total outage.
            skip.add(provider)
            unpriced.add(provider)
            continue
        try:
            await provider_budget.reserve(provider, cost)
            reservations[provider] = cost
        except ProviderBudgetExhausted:
            skip.add(provider)

    has_free_hop = any(p in FREE_PROVIDERS for p, _ in FALLBACK_CHAINS[chain_name])
    if billable and len(skip) == billable and not has_free_hop:
        exhausted = skip - unpriced
        logger.error(
            "[request_id=%s] refusing: no usable provider in chain %r"
            " (out of budget: %s; unpriced: %s)",
            request_id,
            chain_name,
            sorted(exhausted) or "none",
            sorted(unpriced) or "none",
        )
        # 402 when money is why, 503 when it is a missing pricing entry —
        # the first is the caller's problem to wait out, the second is an
        # operator misconfiguration and retrying will never fix it.
        raise HTTPException(
            status_code=402 if exhausted else 503,
            detail={
                "error": (
                    "provider budget exhausted" if exhausted else "no pricing configured"
                ),
                "spent": await provider_budget.snapshot(),
                "cap_usd": provider_budget.cap_usd,
                "unpriced": sorted(unpriced),
                "request_id": request_id,
            },
        )
    return reservations, skip


async def _settle_stream_on_abort(
    api_key: str,
    requested_model: str,
    provider: str,
    model: str,
    input_tokens: int,
    streamed_chars: int,
    reservations: dict[str, float] | None,
    request_id: str,
    start: float,
) -> None:
    """Charge for a stream that ended without its usage totals.

    Two ways to get here: the client hung up, or the provider died after
    committing. Either way tokens were generated and billed upstream, and the
    `done` chunk carrying the real counts never arrived — so previously
    nothing was recorded at all and the budget cap simply never advanced.

    Output is estimated from the characters actually streamed. Deliberately
    approximate and biased high (see _CHARS_PER_TOKEN): under-charging here is
    exactly how a ceiling quietly stops being one.
    """
    if not provider:
        await _settle_chain(reservations or {}, "", 0.0)
        return

    estimated_output = max(1, streamed_chars // 3)
    cost = estimate_cost_usd(
        provider=provider,
        model=model,
        input_tokens=input_tokens,
        output_tokens=estimated_output,
    )
    logger.warning(
        "[request_id=%s] stream ended without usage totals; charging an estimate"
        " of $%.6f against %s from %d streamed characters",
        request_id,
        cost,
        provider,
        streamed_chars,
    )
    await _settle_chain(reservations or {}, provider, cost)
    _record_usage(provider, model, input_tokens, estimated_output, cost)
    await budget_tracker.record_spend(api_key, cost)
    await record_audit_log(
        api_key,
        requested_model=requested_model,
        outcome="aborted",
        provider=provider,
        input_tokens=input_tokens,
        output_tokens=estimated_output,
        cost_usd=cost,
        latency_ms=(time.perf_counter() - start) * 1000,
        request_id=request_id,
    )


async def _settle_chain(
    reservations: dict[str, float], served_provider: str, actual_usd: float
) -> None:
    """Swap reservations for the real cost. Unused providers get a full refund."""
    for provider, reserved in reservations.items():
        await provider_budget.settle(
            provider, reserved, actual_usd if provider == served_provider else 0.0
        )

    if served_provider and served_provider not in reservations:
        # Real spend with no reservation to swap. settle() would do nothing
        # here — it only walks the reservations — so the charge would simply
        # be dropped, and dropped spend is how a ceiling stops being one.
        # ProviderBudget.record_unreserved exists for exactly this and was
        # never actually called from anywhere.
        await provider_budget.record_unreserved(served_provider, actual_usd)


async def _serve_chat(
    router: FallbackRouter,
    messages: list[ChatMessage],
    api_key: str,
    requested_model: str,
    request_id: str,
    params: SamplingParams | None = None,
    reservations: dict[str, float] | None = None,
    skip_providers: set[str] | None = None,
) -> ChatResponse:
    """Run a non-streaming request through the router and do the bookkeeping.

    Shared by /v1/chat and /v1/chat/completions so there's exactly one
    implementation of spend recording, audit logging, and the metrics around
    them. Two copies would drift, and the direction they'd drift in is a
    request that gets served but never billed."""
    start = time.perf_counter()
    try:
        result = await router.chat(
            messages, request_id=request_id, params=params, skip_providers=skip_providers
        )
    except AllProvidersFailedError as exc:
        # Nothing was served, so every reservation comes straight back.
        await _settle_chain(reservations or {}, "", 0.0)
        REQUEST_COUNT.labels(status="error").inc()
        await record_audit_log(
            api_key,
            requested_model=requested_model,
            outcome="error",
            latency_ms=(time.perf_counter() - start) * 1000,
            request_id=request_id,
        )
        # The provider error text is logged, not returned. It embeds upstream
        # response bodies, which carry API key prefixes/suffixes, org IDs and
        # rate-limit internals — the request_id is what a caller actually
        # needs to get help, and it's already correlated to this log line.
        logger.error("[request_id=%s] all providers failed: %s", request_id, exc)
        raise HTTPException(
            status_code=502,
            detail={"error": "all providers failed", "request_id": request_id},
        ) from exc
    finally:
        # Deliberately no refund here. `finally` runs before the success-path
        # bookkeeping below, so refunding at this point would hand the money
        # back and then settle a second time — subtracting the reservation
        # twice and driving the ledger negative, at which point the ceiling
        # can never be reached. Each exit settles explicitly instead: the
        # except branch above, and the success path below.
        REQUEST_LATENCY.observe(time.perf_counter() - start)

    REQUEST_COUNT.labels(status="success").inc()

    cost = estimate_cost_usd(
        provider=result.provider,
        model=result.model,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
    )
    await _settle_chain(reservations or {}, result.provider, cost)
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
) -> ChatResponseOut:
    request_id = http_request.state.request_id
    chain_name, router = _route(request.model, request_id)
    response.headers["X-Gateway-Chain"] = chain_name
    messages = [ChatMessage(role=m.role, content=m.content) for m in request.messages]
    reservations, skip = await _reserve_chain(chain_name, messages, None, request_id)

    result = await _serve_chat(
        router, messages, api_key, request.model, request_id,
        reservations=reservations, skip_providers=skip,
    )

    return ChatResponseOut(
        content=result.content,
        provider=result.provider,
        model=result.model,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
    )


async def _event_stream(
    router: FallbackRouter,
    messages: list[ChatMessage],
    api_key: str,
    requested_model: str,
    request_id: str,
    reservations: dict[str, float] | None = None,
    skip_providers: set[str] | None = None,
) -> AsyncIterator[str]:
    start = time.perf_counter()
    final_provider = ""
    final_model = ""
    input_tokens = 0
    output_tokens = 0

    streamed_chars = 0

    try:
        async for chunk in router.chat_stream(
            messages, request_id=request_id, skip_providers=skip_providers
        ):
            if chunk.done:
                final_provider = chunk.provider
                final_model = chunk.model
                input_tokens = chunk.input_tokens
                output_tokens = chunk.output_tokens
            else:
                # Counted as it goes, because the usage totals only arrive in
                # the final chunk — which never comes if the client hangs up.
                streamed_chars += len(chunk.content)
                final_provider = final_provider or chunk.provider
                final_model = final_model or chunk.model
            yield f"data: {json.dumps(asdict(chunk))}\n\n"
    except (GeneratorExit, asyncio.CancelledError):
        # The client disconnected mid-stream. Starlette closes the generator,
        # so every line after the loop is skipped — which used to mean the
        # tokens the provider had already generated and billed were recorded
        # as $0.00, leaving the budget cap permanently unreachable. Charge an
        # estimate from what was actually streamed instead.
        await _settle_stream_on_abort(
            api_key, requested_model, final_provider, final_model,
            input_tokens, streamed_chars, reservations, request_id, start,
        )
        raise
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
        # A provider that died after committing has still generated and been
        # billed for whatever it sent, so charge that rather than nothing.
        await _settle_stream_on_abort(
            api_key, requested_model, final_provider, final_model,
            input_tokens, streamed_chars, reservations, request_id, start,
        )
        logger.error("[request_id=%s] stream failed: %s", request_id, exc)
        yield (
            "event: error\ndata: "
            + json.dumps({"error": "all providers failed", "request_id": request_id})
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

    await _settle_chain(reservations or {}, final_provider, cost)

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
) -> StreamingResponse:
    request_id = http_request.state.request_id
    chain_name, router = _route(request.model, request_id)
    messages = [ChatMessage(role=m.role, content=m.content) for m in request.messages]
    reservations, skip = await _reserve_chain(chain_name, messages, None, request_id)
    response = StreamingResponse(
        _event_stream(
            router, messages, api_key, request.model, request_id,
            reservations=reservations, skip_providers=skip,
        ),
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
    router: FallbackRouter,
    messages: list[ChatMessage],
    api_key: str,
    requested_model: str,
    request_id: str,
    completion_id: str,
    created: int,
    params: SamplingParams | None = None,
    reservations: dict[str, float] | None = None,
    skip_providers: set[str] | None = None,
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
    streamed_chars = 0

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
            messages, request_id=request_id, params=params,
            skip_providers=skip_providers,
        ):
            if chunk.done:
                final_provider = chunk.provider
                final_model = chunk.model
                input_tokens = chunk.input_tokens
                output_tokens = chunk.output_tokens
                continue
            streamed_chars += len(chunk.content)
            final_provider = final_provider or chunk.provider
            if first:
                yield envelope({"role": "assistant"}, None, chunk.model or requested_model)
                first = False
            yield envelope({"content": chunk.content}, None, chunk.model or requested_model)
    except (GeneratorExit, asyncio.CancelledError):
        # See _settle_stream_on_abort: a client disconnect used to skip every
        # line below, recording $0.00 for tokens the provider had already
        # generated and billed.
        await _settle_stream_on_abort(
            api_key, requested_model, final_provider, final_model,
            input_tokens, streamed_chars, reservations, request_id, start,
        )
        raise
    except AllProvidersFailedError:
        # Status is already committed to 200 once streaming has begun, so this
        # is reported in-band. OpenAI's SDK surfaces an `error` field on a
        # chunk as an exception, which is the closest thing to the 502 a
        # non-streaming caller would have received.
        REQUEST_COUNT.labels(status="error").inc()
        await _settle_stream_on_abort(
            api_key, requested_model, final_provider, final_model,
            input_tokens, streamed_chars, reservations, request_id, start,
        )
        yield (
            "data: "
            + json.dumps(
                {"error": {"message": "all providers failed", "request_id": request_id}}
            )
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

    await _settle_chain(reservations or {}, final_provider, cost)

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


# response_model=None because the return annotation is a union with
# StreamingResponse (stream=True returns one), which FastAPI cannot turn
# into a response schema. It never generated one for this route anyway —
# this says so explicitly rather than by omitting the annotation.
@app.post("/v1/chat/completions", response_model=None)
async def chat_completions(
    request: ChatCompletionRequest,
    http_request: Request,
    response: Response,
    api_key: str = Depends(enforce_budget),
) -> ChatCompletionResponse | StreamingResponse:
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

    reservations, skip = await _reserve_chain(chain_name, messages, params, request_id)

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
                reservations=reservations,
                skip_providers=skip,
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
        router, messages, api_key, request.model, request_id, params=params,
        reservations=reservations, skip_providers=skip,
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
