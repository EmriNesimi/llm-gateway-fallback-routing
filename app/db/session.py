import logging
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.models import Base

logger = logging.getLogger("gateway.db")

engine = create_async_engine(settings.database_url)
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
