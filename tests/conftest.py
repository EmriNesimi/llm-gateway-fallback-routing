"""Shared fixtures, plus the environment isolation the whole suite rests on.

The os.environ work below deliberately runs *before* any `app.*` import.
app/core/config.py builds its Settings singleton at module import time, so a
fixture can't do this job — by the time the earliest fixture runs, the
configuration has already been read.
"""

import os

# Read no env file at all (see app/core/config.py). Without this, pytest picks
# up the developer's real .env — API keys, budget caps, a live OTLP endpoint
# whose failed trace exports flood stderr — so the suite exercises a different
# configuration here than in CI, where no .env exists.
os.environ["GATEWAY_ENV_FILE"] = ""

# An exported shell variable leaks in exactly the same way a .env does, so drop
# those too and let each test set only what it needs. REDIS_URL and
# DATABASE_URL are the deliberate exceptions: CI addresses its service
# containers through them, and tests/test_redis_integration.py reads REDIS_URL
# directly. tests/test_env_isolation.py keeps both sets honest against
# Settings' actual fields.
PRESERVED_ENV_VARS = frozenset({"REDIS_URL", "DATABASE_URL"})

CLEARED_ENV_VARS = frozenset(
    {
        "ADMIN_API_KEY",
        "ANTHROPIC_API_KEY",
        "CIRCUIT_BREAKER_COOLDOWN_SECONDS",
        "CIRCUIT_BREAKER_FAILURE_THRESHOLD",
        "CORS_ALLOWED_ORIGINS",
        "DATABASE_COMMAND_TIMEOUT_SECONDS",
        "DATABASE_CONNECT_TIMEOUT_SECONDS",
        "GATEWAY_API_KEYS",
        "GATEWAY_SECRET_KEY",
        "LOG_FORMAT",
        "MONTHLY_BUDGET_USD_PER_KEY",
        "OLLAMA_BASE_URL",
        "OLLAMA_REQUEST_TIMEOUT_SECONDS",
        "OPENAI_API_KEY",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "PROVIDER_REQUEST_TIMEOUT_SECONDS",
        "PROVIDER_RETRY_ATTEMPTS",
        "PROVIDER_RETRY_BACKOFF_SECONDS",
        "RATE_LIMIT_CAPACITY",
        "RATE_LIMIT_REFILL_PER_SEC",
        "REDIS_CONNECT_TIMEOUT_SECONDS",
        "REDIS_SOCKET_TIMEOUT_SECONDS",
        "PROVIDER_LIFETIME_BUDGET_USD",
        "STRICT_MODEL_ROUTING",
    }
)

for _name in CLEARED_ENV_VARS:
    os.environ.pop(_name, None)

# Everything below imports app code, which reads the environment set up above.
import fakeredis.aioredis  # noqa: E402
import pytest_asyncio  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.budget import dependency as budget_dependency  # noqa: E402
from app.db import audit as db_audit  # noqa: E402
from app.db import session as db_session  # noqa: E402
from app.db.models import Base  # noqa: E402
from app.ratelimit import dependency as ratelimit_dependency  # noqa: E402
from app.ratelimit.token_bucket import _LUA_TOKEN_BUCKET  # noqa: E402


@pytest_asyncio.fixture
async def isolated_db(monkeypatch):
    """A fresh in-memory DB per test, swapped in for the real engine/session
    factory wherever the app looks them up."""
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)

    monkeypatch.setattr(db_session, "engine", engine)
    monkeypatch.setattr(db_session, "async_session", factory)
    # app/db/audit.py imported `async_session` by name, so it needs its own patch.
    monkeypatch.setattr(db_audit, "async_session", factory)

    yield factory

    await engine.dispose()


@pytest_asyncio.fixture
async def isolated_redis(monkeypatch):
    """Swap the real rate-limiter/budget-tracker Redis client for a fake one,
    so tests don't need a live Redis and don't share state between runs."""
    fake = fakeredis.aioredis.FakeRedis()

    monkeypatch.setattr(ratelimit_dependency._limiter, "_redis", fake)
    # The limiter's Lua script is bound to whichever client it was registered
    # against, so it must be re-registered on the fake client too.
    monkeypatch.setattr(
        ratelimit_dependency._limiter, "_script", fake.register_script(_LUA_TOKEN_BUCKET)
    )
    monkeypatch.setattr(budget_dependency.tracker, "_redis", fake)
    # The lifetime provider ledger is a separate client; without this it
    # reaches for a real Redis and every chat test fails on connect.
    monkeypatch.setattr(budget_dependency.provider_budget, "_redis", fake)

    yield fake
