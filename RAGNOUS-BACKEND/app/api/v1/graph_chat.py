"""POST /api/v1/chat/graph — the LangGraph-backed tutor endpoint.

Runs alongside the legacy `/api/v1/chat` endpoint. Both accept the same
request shape (see `chat.ChatRequest`) and emit the same `ChatResponse` — so
the frontend can flip between them by changing the URL, no client changes
required.

What runs where:

    /api/v1/chat        legacy monolith (chat.py) — attachments, PYQ, image
                        legend, video gating still live inline here.
    /api/v1/chat/graph  new pipeline: memory read → planner → retrieve →
                        rerank → confidence route → synth → artifact →
                        verify → memory write.

The graph endpoint currently covers the *common on-syllabus text turn*. Turns
with attachments, explicit PYQ requests, or the deterministic video-refusal
path still route through the legacy endpoint — those side flows are on the
migration TODO and each will collapse into its own graph node in a follow-up.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.agents.graph import tutor_graph
from app.agents.nodes.confidence_router import percent_for
from app.agents.nodes.verification_agent import display_mode
from app.agents.state import ConfidenceTier
from app.api.v1.chat import Attachment, ChatRequest, ChatResponse, Citation
from app.memory.working_memory import thread_config


router = APIRouter()


class GraphChatResponse(ChatResponse):
    """Same wire shape, but adds a `verified` flag so debug UIs can see when
    the verifier flipped a badge down."""
    verified: bool = False
    verification_note: str = ""


@router.post("", response_model=GraphChatResponse)
async def graph_chat_endpoint(req: ChatRequest):
    """Run the compiled tutor graph against the request."""
    if req.attachments:
        # Attachments still ride the legacy endpoint until the vision path
        # is factored into a node. Return a clear 501 so the frontend knows
        # to switch back rather than silently dropping the file.
        raise HTTPException(
            status_code=501,
            detail=(
                "The graph endpoint does not yet accept file attachments — "
                "use /api/v1/chat for turns that carry files."
            ),
        )

    try:
        graph = tutor_graph()
    except Exception as exc:  # noqa: BLE001
        print(f"[GRAPH ENDPOINT] graph unavailable: {exc}")
        raise HTTPException(status_code=500, detail="Tutor graph unavailable")

    # Session id doubles as the LangGraph thread id, so working memory keeps
    # state across turns. When the client doesn't send one, generate one and
    # echo it in the response header (see `ChatResponse.id` — the browser
    # already persists it as `session_id`).
    session_id = getattr(req, "session_id", None) or f"anon-{uuid.uuid4().hex[:12]}"
    user_id = getattr(req, "user_id", None)

    initial_state = {
        "query": req.query,
        "history": req.history,
        "student_class": req.student_class,
        "language": req.language,
        "model_preference": req.model,
        "user_id": user_id,
        "session_id": session_id,
        "pyq_topics_seen": req.pyq_topics_seen,
    }

    try:
        final_state = await graph.ainvoke(initial_state, config=thread_config(session_id))
    except Exception as exc:  # noqa: BLE001
        print(f"[GRAPH ENDPOINT] invoke failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))

    tier = final_state.get("confidence_tier", ConfidenceTier.LOW)
    citations_raw = final_state.get("citations") or []

    citations = [
        Citation(
            chapter=c["chapter"],
            page=int(c["page"] or 0),
            source=c["source"],
        )
        for c in citations_raw
    ]

    confidence_score = (
        percent_for(final_state.get("best_score", 0.0),
                    final_state.get("score_kind", "cosine"),
                    tier)
        if citations
        else None
    )

    return GraphChatResponse(
        id=os.urandom(4).hex(),
        role="assistant",
        content=final_state.get("answer_md", "") or "",
        confidenceMode=display_mode(final_state),
        confidenceScore=confidence_score,
        citations=citations,
        artifact=final_state.get("artifact"),
        pyq=final_state.get("pyq"),
        verified=bool(final_state.get("verified")),
        verification_note=final_state.get("verification_note", ""),
        createdAt=datetime.utcnow().isoformat() + "Z",
    )
