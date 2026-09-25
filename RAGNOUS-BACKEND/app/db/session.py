"""Central asyncpg pool for the whole backend.

The legacy `app/api/v1/chat.py` owns its own pool via `get_db_pool()`. This
module wraps that same function and exposes a FastAPI-friendly dependency,
`get_conn`, so new endpoints don't have to reach into `chat.py`.

Why not a fresh pool? Two competing pools race on the same DB, waste
connections, and hide bugs (a query that only misbehaves under contention).
Better: one pool, wrapped twice.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator


async def get_pool():
    """The one process-wide asyncpg pool. Returns None when DATABASE_URL is
    unset or the DB is unreachable — callers must handle both."""
    from app.api.v1.chat import get_db_pool
    return await get_db_pool()


@asynccontextmanager
async def get_conn() -> AsyncIterator:
    """`async with get_conn() as conn: await conn.fetch(...)`.

    Yields None when the pool is unavailable, rather than raising, so downstream
    code can degrade gracefully.
    """
    pool = await get_pool()
    if pool is None:
        yield None
        return
    async with pool.acquire() as conn:
        yield conn
