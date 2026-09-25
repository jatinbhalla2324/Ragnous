"""LangGraph checkpointer — the conversational working memory.

The point of a checkpointer is to persist `AgentState` between graph
invocations, keyed by a `thread_id` (which we equate with the frontend's
`session_id`). Once wired, the endpoint stops having to ship the whole chat
history on every request — LangGraph rehydrates the state from the store.

Storage backends we consider, in order:

1. **Postgres** (async, via `AsyncPostgresSaver`). Preferred: the app already
   depends on Postgres for `ncert_chunks` and it's fine sharing a database
   with the vector store.
2. **In-memory** (`MemorySaver`). Fallback when `DATABASE_URL` is unset —
   useful for local dev without a DB up, useless in production because the
   memory dies with the worker.

Constructed lazily so importing the module has no I/O cost; the graph module
resolves the checkpointer only when it first builds the compiled graph.
"""

from __future__ import annotations

import os
from typing import Any, Optional

_CHECKPOINTER: Optional[Any] = None


def get_checkpointer() -> Optional[Any]:
    """Return the process-wide LangGraph checkpointer, or None on failure.

    Returning None (rather than raising) lets the graph still compile and
    serve turns statelessly — the graph endpoint just loses persistence,
    which is a genuine dev-time trade-off, not a crash-worthy condition.
    """
    global _CHECKPOINTER
    if _CHECKPOINTER is not None:
        return _CHECKPOINTER

    # Postgres first.
    db_url = os.getenv("DATABASE_URL")
    if db_url:
        try:
            # Both langgraph and langgraph-checkpoint-postgres re-export the
            # async saver; try each so this works on either package layout.
            try:
                from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver  # type: ignore
            except ImportError:
                from langgraph_checkpoint_postgres.aio import AsyncPostgresSaver  # type: ignore

            # AsyncPostgresSaver expects a plain postgres:// URL, not the
            # +asyncpg variant SQLAlchemy uses.
            clean_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
            _CHECKPOINTER = AsyncPostgresSaver.from_conn_string(clean_url)
            print("[MEM] using AsyncPostgresSaver for working memory")
            return _CHECKPOINTER
        except Exception as exc:  # noqa: BLE001
            print(f"[MEM] Postgres checkpointer unavailable ({exc}); falling back to in-memory")

    # In-memory fallback.
    try:
        from langgraph.checkpoint.memory import MemorySaver  # type: ignore
        _CHECKPOINTER = MemorySaver()
        print("[MEM] using in-process MemorySaver")
        return _CHECKPOINTER
    except Exception as exc:  # noqa: BLE001
        print(f"[MEM] no checkpointer available ({exc}); graph runs stateless")
        return None


def thread_config(session_id: str) -> dict:
    """The `configurable` block LangGraph reads to key state by thread.

    Passed to `graph.ainvoke(state, config=thread_config(session_id))`.
    """
    return {"configurable": {"thread_id": session_id}}
