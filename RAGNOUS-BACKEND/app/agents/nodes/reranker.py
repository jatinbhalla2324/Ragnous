"""Cross-encoder rerank pass, packaged as a graph node.

Wraps `rerank_service.rerank`, which is synchronous and CPU-bound — pushed off
the event loop with `asyncio.to_thread`. If both cross-encoder backends are
unavailable, this node falls back to sorting by raw cosine and *tags the score
kind* so the confidence router downstream refuses to certify the answer.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

from app.agents.state import AgentState, ScoredChunk
from app.services import rerank_service


async def reranker(state: AgentState) -> Dict[str, Any]:
    """Populate: reranked, rerank_backend, score_kind, best_score."""
    rows: List[dict] = state.get("raw_rows", []) or []

    if not rows:
        return {
            "reranked": [],
            "rerank_backend": "none",
            "score_kind": "cosine",
            "best_score": 0.0,
        }

    docs = [row["content"] for row in rows]
    semantic_query = state.get("semantic_query") or state.get("query", "")

    reranked, backend = await asyncio.to_thread(
        rerank_service.rerank, semantic_query, docs, 3
    )

    if reranked:
        score_kind = "rerank"
        best_score = reranked[0][1]
        scored: List[ScoredChunk] = [
            {"score": float(score), "chunk": rows[idx]}
            for idx, score in reranked
        ]
    else:
        # No cross-encoder available. Fall back to raw cosine, then let the
        # router downgrade this to LOW (see confidence_router).
        score_kind = "cosine"
        by_cosine = sorted(
            rows,
            key=lambda r: r.get("vector_score") or 0.0,
            reverse=True,
        )[:3]
        scored = [
            {"score": float(r.get("vector_score") or 0.0), "chunk": r}
            for r in by_cosine
        ]
        best_score = scored[0]["score"] if scored else 0.0

    print(
        f"[GRAPH RERANK] backend={backend} kind={score_kind} "
        f"best={best_score:.4f} kept={len(scored)}"
    )

    return {
        "reranked": scored,
        "rerank_backend": backend,
        "score_kind": score_kind,
        "best_score": float(best_score),
    }
