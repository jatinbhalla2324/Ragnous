"""Hybrid pgvector + BM25 retrieval, wrapped as a graph node.

All the real work lives in `search_service` — this file is the seam between
the graph's `AgentState` and the retrieval SQL. Keeping the SQL there means
`app/evaluation/eval_runner.py` still measures the exact query production
runs, no matter which caller (this node or the legacy endpoint) reaches it.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Dict, List

from app.agents.state import AgentState
from app.services.embedding_service import (
    get_embedding_model,
    to_pgvector,
)
from app.services.search_service import (
    dedupe_chunks,
    hybrid_search,
    only_medium,
    _subjects_for_class,
)

# Hindi/Punjabi extracts are mangled ligatures — see chat.SOURCE_MEDIUM.
SOURCE_MEDIUM = "en"

# The endpoint-level `get_db_pool` is imported lazily below to avoid a circular
# import with `app/api/v1/chat.py` (chat.py owns the process-wide pool and the
# graph is younger scaffolding — flipping ownership would be its own PR).


_TS_STOPWORDS = {
    "what", "which", "when", "where", "who", "why", "how", "does", "did", "do",
    "is", "are", "was", "were", "the", "a", "an", "of", "for", "and", "or",
    "in", "on", "with", "to", "from", "by", "as", "at", "it", "its", "this",
    "that", "these", "those", "me", "my", "you", "your", "explain", "tell",
    "give", "show", "describe", "define", "please", "can", "will", "would",
    "about", "some", "more", "using", "into", "than", "then", "them",
    "their", "there", "here", "also", "any", "all", "get", "got", "make",
    "made", "want", "need", "know", "much", "many", "such", "very", "only",
}


def build_or_tsquery(text: str, limit: int = 12) -> str:
    """OR-joined tsquery — 'photosynthesis | chlorophyll'.

    A tsquery ANDs unquoted terms by default; the expanded query then requires
    every lexeme in one chunk, and the BM25 arm matches nothing. OR is the
    fix: it recovers the signal on any single keyword hit.
    """
    words = re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}", (text or "").lower())
    seen: set[str] = set()
    terms: List[str] = []
    for w in words:
        w = w.strip("-")
        if w in _TS_STOPWORDS or w in seen or len(w) < 3:
            continue
        seen.add(w)
        terms.append(w)
        if len(terms) >= limit:
            break
    return " | ".join(terms)


async def hybrid_retriever(state: AgentState) -> Dict[str, Any]:
    """Populate: raw_rows, ts_query, vector_str, class_subjects."""
    from app.api.v1.chat import get_db_pool  # deferred to break the cycle

    semantic_query = state.get("semantic_query") or state.get("query", "")
    class_no = state.get("student_class_no")

    query_vec = await asyncio.to_thread(get_embedding_model().encode, semantic_query)
    vector_str = to_pgvector(query_vec.tolist())
    ts_query = build_or_tsquery(semantic_query)

    rows: List[dict] = []
    class_subjects: List[str] | None = None

    try:
        pool = await get_db_pool()
    except Exception as exc:
        print(f"[GRAPH RETRIEVE] DB pool acquisition failed: {exc}")
        pool = None

    if pool is not None:
        try:
            async with pool.acquire() as conn:
                class_subjects = await _subjects_for_class(
                    conn, class_no, SOURCE_MEDIUM
                ) or None
                rows = await hybrid_search(conn, vector_str, ts_query, class_subjects)
                if not rows and class_subjects:
                    # Widen when the class filter turns up nothing.
                    print("[GRAPH RETRIEVE] class filter empty; widening globally")
                    rows = only_medium(
                        await hybrid_search(conn, vector_str, ts_query, None),
                        SOURCE_MEDIUM,
                    )
                    class_subjects = None
        except Exception as exc:
            print(f"[GRAPH RETRIEVE] query failed: {exc}")
            rows = []

    if rows:
        deduped = dedupe_chunks(rows, limit=10)
        if len(deduped) != len(rows):
            print(f"[GRAPH RETRIEVE] deduped {len(rows)} -> {len(deduped)}")
        rows = deduped

    return {
        "raw_rows": [dict(r) for r in rows],
        "ts_query": ts_query,
        "vector_str": vector_str,
        "class_subjects": class_subjects,
    }
