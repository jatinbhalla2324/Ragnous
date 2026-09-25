"""Long-term "what did we work on together" memory.

Stored one row per session, in `episodic_memories`:

    (user_id, summary text, embedding vector, session_start, session_end)

Writing:  `summarise_session` — one LLM call over the tail of the transcript,
          then embed the result with the same 384-d MiniLM model retrieval
          uses (no separate embedding lane) and INSERT.

Reading:  `recall_similar_summaries` — cosine-nearest top-k for a given user,
          used by the graph's `memory_read` node so the current turn's prompt
          can carry a short reminder of past sessions on the same topic.

The write side is intentionally offline — it's called from the reflection
worker on session end, not from the request path. So a turn never has to wait
for summarisation to finish before responding.
"""

from __future__ import annotations

import asyncio
from typing import Dict, List, Optional

from langchain_core.messages import HumanMessage

from app.services.embedding_service import (
    embed_query,
    get_embedding_model,
    to_pgvector,
)
from app.services.llm_fallback import ainvoke_with_fallback, build_groq_chain


_SUMMARY_CHAIN = None


def _summary_chain() -> list:
    global _SUMMARY_CHAIN
    if _SUMMARY_CHAIN is None:
        _SUMMARY_CHAIN = build_groq_chain("intent")   # tiny, fast, JSON-friendly
    return _SUMMARY_CHAIN


async def _pool():
    from app.api.v1.chat import get_db_pool
    return await get_db_pool()


_SUMMARY_PROMPT = """Summarise this student-tutor conversation in ONE short paragraph (max 60 words) that captures:
- the topic(s) covered,
- what the student clearly grasped, and
- what they still struggled with (if anything).

Write it as if you were reminding a future tutor "here's where we left off".
No preamble, no headings, just the paragraph.

TRANSCRIPT:
{transcript}
"""


def _transcript_of(messages: List[Dict[str, str]]) -> str:
    """Compact string form of the tail of the chat. Last 12 turns is enough
    for a coherent summary and keeps the prompt cheap."""
    return "\n".join(
        f"{m.get('role', '?').upper()}: {m.get('content', '')}"
        for m in messages[-12:]
    )


async def summarise_session(
    user_id: str,
    messages: List[Dict[str, str]],
) -> Optional[str]:
    """Write one episodic memory row for this session. Returns the summary
    text on success, None on failure.

    Idempotency isn't enforced here; the reflection worker is the only caller
    and only fires once per session on close.
    """
    if not user_id or not messages:
        return None

    prompt = _SUMMARY_PROMPT.format(transcript=_transcript_of(messages))

    try:
        res = await ainvoke_with_fallback(
            _summary_chain(),
            [HumanMessage(content=prompt)],
            label="EPISODIC",
        )
        summary = (res.content or "").strip()
    except Exception as exc:  # noqa: BLE001
        print(f"[MEM EPISODIC] summarise failed: {exc}")
        return None

    if not summary:
        return None

    try:
        embedding = await asyncio.to_thread(get_embedding_model().encode, summary)
        vector_str = to_pgvector(embedding.tolist())
    except Exception as exc:  # noqa: BLE001
        print(f"[MEM EPISODIC] embed failed: {exc}")
        return None

    pool = await _pool()
    if pool is None:
        print("[MEM EPISODIC] no DB pool; summary not persisted")
        return summary

    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO episodic_memories
                    (user_id, summary, embedding, session_start, session_end)
                VALUES ($1::uuid, $2, $3::vector, now(), now())
                """,
                user_id, summary, vector_str,
            )
    except Exception as exc:  # noqa: BLE001
        print(f"[MEM EPISODIC] insert failed: {exc}")

    return summary


async def recall_similar_summaries(
    user_id: str,
    query: str,
    k: int = 3,
) -> List[str]:
    """Top-k episodic summaries most similar to `query`, for this user only.

    Read side of the memory loop. Runs on the request path via
    `graph.memory_read`, so it is capped at k=3 and stops on the first error.
    """
    if not user_id or not query.strip():
        return []
    pool = await _pool()
    if pool is None:
        return []

    try:
        query_vec = await asyncio.to_thread(embed_query, query)
        vector_str = to_pgvector(query_vec)
    except Exception as exc:  # noqa: BLE001
        print(f"[MEM EPISODIC] embed query failed: {exc}")
        return []

    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT summary
                FROM episodic_memories
                WHERE user_id = $1::uuid
                ORDER BY embedding <=> $2::vector
                LIMIT $3
                """,
                user_id, vector_str, k,
            )
    except Exception as exc:  # noqa: BLE001
        print(f"[MEM EPISODIC] recall failed: {exc}")
        return []

    return [r["summary"] for r in rows]
