"""Infrastructure probes used by the readiness endpoint."""

from collections.abc import Awaitable, Callable

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from revenueguard_api.config import Settings

DependencyProbe = Callable[[Settings], Awaitable[dict[str, bool]]]


async def probe_dependencies(settings: Settings) -> dict[str, bool]:
    """Check PostgreSQL and Redis without retaining process-global connections."""

    postgres_ready = False
    redis_ready = False

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        postgres_ready = True
    except Exception:  # Dependency failure is represented in the response.
        postgres_ready = False
    finally:
        await engine.dispose()

    redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        redis_ready = bool(await redis_client.ping())
    except Exception:  # Dependency failure is represented in the response.
        redis_ready = False
    finally:
        await redis_client.aclose()

    return {"postgres": postgres_ready, "redis": redis_ready}
