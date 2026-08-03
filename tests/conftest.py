import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db import audit as db_audit
from app.db import session as db_session
from app.db.models import Base


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
