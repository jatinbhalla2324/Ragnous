"""Turns a numeric relevance score into a routing decision.

Runs as an ordinary node (populating `confidence_tier` and `citations`) *and*
exposes a `route_from_tier` function that the graph passes to
`add_conditional_edges` — so the decision is a single source of truth, whether
the graph is inspecting state or LangGraph is choosing the next node.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List

from app.agents.state import AgentState, Citation, ConfidenceTier


# Same knobs the endpoint reads — env override kept so a live deployment
# doesn't require a code change to retune.
RERANK_HIGH = float(os.getenv("RAG_RERANK_HIGH", "0.45"))
RERANK_MEDIUM = float(os.getenv("RAG_RERANK_MEDIUM", "0.12"))
COSINE_HIGH = float(os.getenv("RAG_COSINE_HIGH", "0.55"))
COSINE_MEDIUM = float(os.getenv("RAG_COSINE_MEDIUM", "0.35"))


def tier_for(score: float | None, kind: str) -> ConfidenceTier:
    """Map a raw score on its own scale to a tier."""
    if score is None:
        return ConfidenceTier.LOW
    high, medium = (
        (RERANK_HIGH, RERANK_MEDIUM) if kind == "rerank" else (COSINE_HIGH, COSINE_MEDIUM)
    )
    if score >= high:
        return ConfidenceTier.HIGH
    if score >= medium:
        return ConfidenceTier.MEDIUM
    return ConfidenceTier.LOW


def percent_for(score: float | None, kind: str, tier: ConfidenceTier) -> float | None:
    """Map a raw score to a badge percentage that agrees with the tier band.

    Bands don't overlap, so the number and the badge can never disagree — the
    old formula could hit 99% on a weak hit and undersell a strong one.
    """
    if score is None:
        return None
    high, medium = (
        (RERANK_HIGH, RERANK_MEDIUM) if kind == "rerank" else (COSINE_HIGH, COSINE_MEDIUM)
    )

    if tier == ConfidenceTier.HIGH:
        span = max(1e-6, 1.0 - high)
        pct = 82.0 + 15.0 * min(1.0, (score - high) / span)
    elif tier == ConfidenceTier.MEDIUM:
        span = max(1e-6, high - medium)
        pct = 58.0 + 22.0 * ((score - medium) / span)
    else:
        span = max(1e-6, medium)
        pct = 30.0 + 25.0 * max(0.0, min(1.0, score / span))
    return round(max(30.0, min(97.0, pct)), 1)


def confidence_router(state: AgentState) -> Dict[str, Any]:
    """Populate: confidence_tier, citations (may downgrade tier to LOW).

    A pure function — no I/O — so it costs nothing to run on every turn.
    """
    score_kind = state.get("score_kind", "cosine")
    best_score = state.get("best_score", 0.0)

    if not state.get("raw_rows"):
        return {"confidence_tier": ConfidenceTier.LOW, "citations": []}

    tier = tier_for(best_score, score_kind)

    # Cosine alone cannot certify grounding on this corpus — a topic entirely
    # outside the books can score higher than a real hit — so any non-LOW tier
    # from cosine is refused.
    if score_kind != "rerank" and tier != ConfidenceTier.LOW:
        print(f"[GRAPH ROUTE] cosine-only tier {tier.value} -> low (cannot certify)")
        tier = ConfidenceTier.LOW

    citation_floor = RERANK_MEDIUM if score_kind == "rerank" else COSINE_MEDIUM
    citations: List[Citation] = []
    if tier != ConfidenceTier.LOW:
        seen: set[tuple[str, int]] = set()
        for scored in state.get("reranked", []) or []:
            if scored["score"] < citation_floor:
                continue
            chunk = scored["chunk"]
            ref = (chunk.get("chapter", ""), chunk.get("page_number") or 0)
            if ref in seen:
                continue
            seen.add(ref)
            citations.append(
                Citation(
                    chapter=chunk.get("chapter", ""),
                    page=int(chunk.get("page_number") or 0),
                    source=chunk.get("subject", ""),
                )
            )
            if len(citations) >= 3:
                break

    # No chunk cleared the floor -> the answer is not textbook-grounded.
    if not citations and tier != ConfidenceTier.LOW:
        print("[GRAPH ROUTE] no chunk cleared citation floor; downgrading to low")
        tier = ConfidenceTier.LOW

    print(f"[GRAPH ROUTE] tier={tier.value} citations={len(citations)}")
    return {"confidence_tier": tier, "citations": citations}


def route_from_tier(state: AgentState) -> str:
    """LangGraph conditional-edge selector.

    Names are string literals (not node references) because `add_conditional_edges`
    takes a mapping from these names to node names.
    """
    return state.get("confidence_tier", ConfidenceTier.LOW).value


# Mapping the tier back to the badge label the frontend renders.
TIER_TO_MODE = {
    ConfidenceTier.HIGH: "ncert_verified",
    ConfidenceTier.MEDIUM: "extended_reference",
    ConfidenceTier.LOW: "ai_knowledge",
}
