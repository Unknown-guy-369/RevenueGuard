"""Async SQLAlchemy engine and explicit transaction helpers."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

type AsyncSessionFactory = async_sessionmaker[AsyncSession]


def create_database_engine(
    database_url: str,
    *,
    echo: bool = False,
    use_null_pool: bool = False,
) -> AsyncEngine:
    """Create the application database engine without opening a connection eagerly."""

    if use_null_pool:
        return create_async_engine(database_url, echo=echo, poolclass=NullPool)
    return create_async_engine(database_url, echo=echo, pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> AsyncSessionFactory:
    """Create sessions that preserve objects after transaction commits."""

    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


@asynccontextmanager
async def session_scope(factory: AsyncSessionFactory) -> AsyncIterator[AsyncSession]:
    """Commit one visible application transaction or roll it back on failure."""

    async with factory() as session:
        async with session.begin():
            yield session
