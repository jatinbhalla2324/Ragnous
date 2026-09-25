"""FastAPI dependency helpers.

`Depends(get_db)` is the async-conn shortcut — everything else routes through
here so a future auth dependency has one place to attach itself.
"""

from __future__ import annotations

from typing import Optional

from app.db.session import get_pool


async def get_db() -> Optional[object]:
    """Yield an asyncpg pool. Returns None when the pool cannot be acquired,
    so route code has to handle both — this is deliberate: retrieval degrades
    gracefully when the DB is down."""
    return await get_pool()
