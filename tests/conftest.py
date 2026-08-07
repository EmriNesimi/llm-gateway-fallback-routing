import fakeredis.aioredis
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.budget import dependency as budget_dependency
from app.db import audit as db_audit
from app.db import session as db_session
from app.db.models import Base
from app.ratelimit import dependency as ratelimit_dependency
from app.ratelimit.token_bucket import _LUA_TOKEN_BUCKET


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

    yield fake
