from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.settings import get_settings

_settings = get_settings()

engine = create_async_engine(_settings.database_url, pool_pre_ping=True)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as session:
        yield session


@asynccontextmanager
async def task_session() -> AsyncIterator[AsyncSession]:
    """A session for one `asyncio.run()` (Celery tasks, scripts).

    Each Celery task runs in a fresh event loop; asyncpg connections pooled by
    the module-level engine belong to whichever loop opened them, so reusing
    them from a later task fails. This engine never pools and is disposed with
    the session.
    """
    task_engine = create_async_engine(_settings.database_url, poolclass=NullPool)
    try:
        async with async_sessionmaker(task_engine, expire_on_commit=False)() as session:
            yield session
    finally:
        await task_engine.dispose()
