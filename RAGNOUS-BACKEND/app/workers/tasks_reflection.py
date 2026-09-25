"""Celery tasks that update long-term memory outside the request path.

Two tasks:
- `reflect_all_users` (beat-scheduled, nightly) — sweep every user with recent
  activity, call the reflection agent, persist weak_subjects + weak_topics.
- `reflect_user` (fire-and-forget from the endpoint on session close) —
  summarise + reflect for a single user, immediately.

Celery is sync; we drive the async memory helpers through `asyncio.run`
inside each task body. Not the fastest thing on earth, but tasks live for
seconds and this dodges every "shared event loop under Celery" trap.
"""

from __future__ import annotations

import asyncio
from typing import Dict, List

from app.memory.episodic_memory import summarise_session
from app.memory.reflection_agent import reflect_on_user
from app.workers.celery_app import celery_app


async def _load_user_activity(user_id: str) -> tuple[list, list]:
    """Fetch the tail of quiz attempts + episodic summaries for a user.

    Assumes attempts are stored elsewhere (frontend today, but a table is on
    the roadmap). Returns empty lists when the store isn't wired up.
    """
    # TODO(server-side attempt log): once /api/v1/quiz writes attempts to
    # Postgres, read them here. For now the reflector gets episodic-only
    # signal — still useful, since summaries name the struggle.
    from app.api.v1.chat import get_db_pool
    pool = await get_db_pool()
    if pool is None:
        return [], []
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT summary FROM episodic_memories
                WHERE user_id = $1::uuid
                ORDER BY session_end DESC
                LIMIT 10
                """,
                user_id,
            )
    except Exception as exc:  # noqa: BLE001
        print(f"[REFLECT TASK] load activity failed for {user_id!r}: {exc}")
        return [], []
    return [], [r["summary"] for r in rows]


@celery_app.task(name="app.workers.tasks_reflection.reflect_user")
def reflect_user(user_id: str, transcript: List[Dict[str, str]] | None = None) -> Dict:
    """End-of-session reflection.

    `transcript` is optional: pass it and we also write a fresh episodic
    summary before running the reflector. Omitting it means "just reflect on
    whatever memory already holds".
    """
    async def _run() -> Dict:
        if transcript:
            await summarise_session(user_id, transcript)
        attempts, summaries = await _load_user_activity(user_id)
        return await reflect_on_user(user_id, attempts, summaries)

    return asyncio.run(_run())


@celery_app.task(name="app.workers.tasks_reflection.reflect_all_users")
def reflect_all_users() -> Dict[str, int]:
    """Beat-scheduled sweep. Runs the reflector for every user with at least
    one episodic memory in the last 7 days."""
    async def _run() -> Dict[str, int]:
        from app.api.v1.chat import get_db_pool
        pool = await get_db_pool()
        if pool is None:
            return {"users": 0}
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT DISTINCT user_id::text AS user_id
                    FROM episodic_memories
                    WHERE session_end > now() - interval '7 days'
                    """
                )
        except Exception as exc:  # noqa: BLE001
            print(f"[REFLECT TASK] sweep query failed: {exc}")
            return {"users": 0}

        touched = 0
        for row in rows:
            user_id = row["user_id"]
            attempts, summaries = await _load_user_activity(user_id)
            try:
                await reflect_on_user(user_id, attempts, summaries)
                touched += 1
            except Exception as exc:  # noqa: BLE001
                print(f"[REFLECT TASK] {user_id!r} failed: {exc}")
        return {"users": touched}

    return asyncio.run(_run())
