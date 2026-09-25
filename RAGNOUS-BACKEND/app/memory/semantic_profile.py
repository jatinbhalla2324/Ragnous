"""CRUD over `semantic_profiles`.

The schema already defines the table (see `app/db/schema.sql`). The old code
just never read or wrote it. Three functions here:

- `fetch_profile_snapshot(user_id)` — the read the graph's memory_read node
  calls before the planner runs. Returns a plain dict so the node's return
  type stays JSON-serialisable (LangGraph checkpoints go through pickle *and*
  JSON depending on backend; dicts survive both).
- `upsert_profile(user_id, updates)` — the write called by the reflection
  worker when a new weak topic is inferred.
- `record_weak_subject(user_id, subject)` — a nudge helper used by the quiz
  endpoint whenever a subject's mastery falls below the practice threshold.

None of these raise on a missing pool. The tutor works without memory; only
the *smart* behaviours (personalised tone, recall of past sessions) go dark.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Iterable, Optional


async def _pool():
    """Lazy import to avoid a circular with `app/api/v1/chat.py`."""
    from app.api.v1.chat import get_db_pool
    return await get_db_pool()


async def fetch_profile_snapshot(user_id: str) -> Optional[Dict[str, Any]]:
    """Return `{learning_style, language_preference, weak_subjects}` or None."""
    pool = await _pool()
    if pool is None or not user_id:
        return None
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT learning_style, language_preference, weak_subjects
                FROM semantic_profiles WHERE user_id = $1::uuid
                """,
                user_id,
            )
    except Exception as exc:  # noqa: BLE001
        print(f"[MEM PROFILE] fetch failed for {user_id!r}: {exc}")
        return None

    if not row:
        return None

    weak = row["weak_subjects"]
    if isinstance(weak, (str, bytes)):
        try:
            weak = json.loads(weak)
        except (ValueError, TypeError):
            weak = []
    return {
        "learning_style": row["learning_style"],
        "language_preference": row["language_preference"] or "hinglish",
        "weak_subjects": list(weak or []),
    }


async def upsert_profile(user_id: str, updates: Dict[str, Any]) -> None:
    """Merge `updates` into the semantic profile, creating the row if needed.

    `weak_subjects` is treated additively — pass a list and it is unioned with
    what's already stored, not overwritten. Every other field is a plain
    replace.
    """
    pool = await _pool()
    if pool is None or not user_id:
        return
    try:
        async with pool.acquire() as conn:
            existing = await conn.fetchrow(
                "SELECT weak_subjects FROM semantic_profiles WHERE user_id = $1::uuid",
                user_id,
            )
            current_weak: list[str] = []
            if existing:
                raw = existing["weak_subjects"]
                if isinstance(raw, (str, bytes)):
                    try:
                        current_weak = list(json.loads(raw))
                    except (ValueError, TypeError):
                        current_weak = []
                else:
                    current_weak = list(raw or [])

            new_weak = updates.get("weak_subjects")
            if isinstance(new_weak, Iterable):
                merged = list({*current_weak, *(str(s) for s in new_weak)})
            else:
                merged = current_weak

            await conn.execute(
                """
                INSERT INTO semantic_profiles
                    (user_id, learning_style, language_preference, weak_subjects, updated_at)
                VALUES ($1::uuid, $2, $3, $4::jsonb, now())
                ON CONFLICT (user_id) DO UPDATE
                SET learning_style      = COALESCE(EXCLUDED.learning_style,      semantic_profiles.learning_style),
                    language_preference = COALESCE(EXCLUDED.language_preference, semantic_profiles.language_preference),
                    weak_subjects       = EXCLUDED.weak_subjects,
                    updated_at          = now()
                """,
                user_id,
                updates.get("learning_style"),
                updates.get("language_preference"),
                json.dumps(merged),
            )
    except Exception as exc:  # noqa: BLE001
        print(f"[MEM PROFILE] upsert failed for {user_id!r}: {exc}")


async def record_weak_subject(user_id: str, subject: str) -> None:
    """Convenience wrapper called when the quiz endpoint spots a slip."""
    if not subject:
        return
    await upsert_profile(user_id, {"weak_subjects": [subject]})
