import logging
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.models import Base

logger = logging.getLogger("gateway.db")


def build_connect_args(
    database_url: str, connect_timeout_seconds: float, command_timeout_seconds: float
) -> dict[str, float]:
    """asyncpg-specific connect/command timeouts — meaningless to (and
    rejected by) aiosqlite, so only returned for a Postgres DATABASE_URL. See
    app/core/config.py for why these exist: asyncpg's own defaults either
    aren't set (command_timeout) or are too long for a request path
    (connect). A plain function so this logic is testable without reloading
    the module (`engine` below is built once at import time)."""
    if not database_url.startswith("postgresql"):
        return {}
    return {"timeout": connect_timeout_seconds, "command_timeout": command_timeout_seconds}


engine = create_async_engine(
    settings.database_url,
    connect_args=build_connect_args(
        settings.database_url,
        settings.database_connect_timeout_seconds,
        settings.database_command_timeout_seconds,
    ),
)
async_session = async_sessionmaker(engine, expire_on_commit=False)


async def init_db() -> None:
    """Create tables if they don't exist yet — a zero-config convenience for
    local dev on the default SQLite DB. For Postgres (or any deployment where
    multiple instances might boot concurrently), this is skipped: schema
    changes belong to `alembic upgrade head`, run once as an explicit deploy
    step (see docker-entrypoint.sh), not raced by every replica on startup.
    """
    if not settings.database_url.startswith("sqlite"):
        logger.info("non-SQLite DATABASE_URL: skipping create_all, expecting Alembic migrations")
        return

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with async_session() as session:
        yield session
