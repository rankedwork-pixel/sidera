"""Async database session factory for Sidera.

Uses SQLAlchemy 2.0 async engine with asyncpg driver.
Falls back gracefully when database_url is not configured (dev/test).
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.config import settings
from src.models.schema import Base

logger = structlog.get_logger(__name__)

# --- Engine & Session Factory ---

_engine = None
_session_factory = None


def _get_engine():
    """Lazily create the async engine from settings."""
    global _engine
    if _engine is not None:
        return _engine

    if not settings.database_url:
        logger.warning(
            "database_url is not configured — database operations will fail. "
            "Set DATABASE_URL in your .env file to enable persistence."
        )
        return None

    _engine = create_async_engine(
        settings.database_url,
        echo=(settings.app_env == "development"),
        pool_size=20,
        max_overflow=30,
        pool_pre_ping=True,
        pool_recycle=1800,  # Recycle connections after 30min (cloud DB proxy safety)
    )
    logger.info("async_engine_created", url=settings.database_url[:30] + "...")
    return _engine


def _get_session_factory():
    """Lazily create the async session factory."""
    global _session_factory
    if _session_factory is not None:
        return _session_factory

    engine = _get_engine()
    if engine is None:
        return None

    _session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    return _session_factory


@asynccontextmanager
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async database session with automatic commit/rollback.

    Usage:
        async with get_db_session() as session:
            result = await session.execute(select(Account))
            ...

    Raises:
        RuntimeError: If database_url is not configured.
    """
    factory = _get_session_factory()
    if factory is None:
        raise RuntimeError("Database is not configured. Set DATABASE_URL in your environment.")

    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """Create all database tables defined in Base.metadata.

    Intended for initial setup and development. In production, use Alembic migrations.
    """
    engine = _get_engine()
    if engine is None:
        logger.warning("init_db skipped — database_url is not configured.")
        return

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    logger.info("database_tables_created")


async def close_db() -> None:
    """Dispose of the engine connection pool.

    Call this during application shutdown.
    """
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
        logger.info("database_engine_disposed")
