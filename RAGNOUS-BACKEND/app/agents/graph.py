"""Compile the tutor `StateGraph` once at import time.

Shape:

    memory_read
        └─► intent_classifier
                ├─► out_of_syllabus/greeting  ── ► synth_refusal ─► END
                └─► on-syllabus / meta
                        └─► hybrid_retriever
                                └─► reranker
                                        └─► confidence_router
                                                ├─► HIGH    ─► response_synthesizer ─► artifact_selector ─► verification_agent ─► memory_write ─► END
                                                ├─► MEDIUM  ─► (same)
                                                └─► LOW     ─► (same, prompt already says "last resort")

Every node returns a partial state; LangGraph merges. Conditional edges use
`route_from_tier` (from `confidence_router`), which reads the tier the router
just wrote — routing is a pure function of state, so the graph is trivially
replayable from any checkpoint.

The compiled graph is exposed as `TUTOR_GRAPH`; `build_graph()` is also
exported so tests can spin up a fresh copy with a different checkpointer.
"""

from __future__ import annotations

from typing import Any, Callable

from app.agents.nodes.artifact_selector import artifact_selector
from app.agents.nodes.confidence_router import confidence_router, route_from_tier
from app.agents.nodes.hybrid_retriever import hybrid_retriever
from app.agents.nodes.intent_classifier import intent_classifier
from app.agents.nodes.reranker import reranker
from app.agents.nodes.response_synthesizer import response_synthesizer
from app.agents.nodes.verification_agent import verification_agent
from app.agents.state import AgentState, ConfidenceTier, Intent
from app.memory.episodic_memory import recall_similar_summaries
from app.memory.semantic_profile import fetch_profile_snapshot
from app.memory.working_memory import get_checkpointer
from app.agents.prompts.system_blocks import REFUSAL_LINE


# ── Auxiliary nodes (memory + refusal + write-back) ─────────────────────
async def memory_read(state: AgentState) -> dict:
    """Best-effort: fetch semantic profile and top-k episodic summaries."""
    user_id = state.get("user_id")
    query = state.get("query", "")
    profile: dict = {}
    prior: list[str] = []
    if user_id:
        try:
            profile = await fetch_profile_snapshot(user_id) or {}
        except Exception as exc:  # noqa: BLE001
            print(f"[GRAPH MEM] profile fetch failed: {exc}")
        try:
            prior = await recall_similar_summaries(user_id, query, k=3) or []
        except Exception as exc:  # noqa: BLE001
            print(f"[GRAPH MEM] episodic fetch failed: {exc}")
    return {"profile": profile, "prior_summaries": prior}


async def memory_write(state: AgentState) -> dict:
    """No-op inside the request. Real writing happens off the request path via
    Celery — see `workers/tasks_reflection.py`. This node is a hook: it exists
    so downstream can enqueue the summary + weak-topic updates without changing
    the graph shape."""
    return {}


async def synth_refusal(state: AgentState) -> dict:
    """Deterministic reply for out-of-syllabus / greeting turns.

    Cheaper than a full LLM call and guarantees the wording matches the same
    line used in the mode instructions — the model can't disagree with itself
    across two paths if only one path emits it.
    """
    intent = state.get("intent")
    if intent == Intent.GREETING:
        answer = (
            "Hi! I'm your NCERT tutor. Ask me anything from your Class "
            f"{state.get('student_class', '8th')} syllabus — Physics, Chemistry, "
            "Biology, Maths, Geography, History — and I'll walk you through it."
        )
    else:
        answer = REFUSAL_LINE
    return {
        "answer_md": answer,
        "citations": [],
        "artifact": None,
        "confidence_tier": ConfidenceTier.LOW,
        "verified": False,
    }


# ── Router functions ─────────────────────────────────────────────────────
def _route_from_intent(state: AgentState) -> str:
    """After the planner: refusal vs. full pipeline."""
    intent = state.get("intent", Intent.ON_SYLLABUS)
    if intent in (Intent.OUT_OF_SYLLABUS, Intent.GREETING):
        return "refuse"
    return "retrieve"


# ── Builder ──────────────────────────────────────────────────────────────
def build_graph(checkpointer: Any | None = None):
    """Return a compiled LangGraph app.

    `checkpointer` is optional so tests can build a memoryless graph. The
    production entry point passes the pooled Postgres checkpointer set up in
    `memory/working_memory.py`.
    """
    from langgraph.graph import END, StateGraph

    workflow = StateGraph(AgentState)

    workflow.add_node("memory_read", memory_read)
    workflow.add_node("intent_classifier", intent_classifier)
    workflow.add_node("synth_refusal", synth_refusal)
    workflow.add_node("hybrid_retriever", hybrid_retriever)
    workflow.add_node("reranker", reranker)
    workflow.add_node("confidence_router", confidence_router)
    workflow.add_node("response_synthesizer", response_synthesizer)
    workflow.add_node("artifact_selector", artifact_selector)
    workflow.add_node("verification_agent", verification_agent)
    workflow.add_node("memory_write", memory_write)

    workflow.set_entry_point("memory_read")
    workflow.add_edge("memory_read", "intent_classifier")

    workflow.add_conditional_edges(
        "intent_classifier",
        _route_from_intent,
        {"refuse": "synth_refusal", "retrieve": "hybrid_retriever"},
    )
    workflow.add_edge("synth_refusal", END)

    workflow.add_edge("hybrid_retriever", "reranker")
    workflow.add_edge("reranker", "confidence_router")

    # All three tiers currently walk the same synthesis path — the mode
    # instructions inside the prompt already branch on tier. The conditional
    # edge is present anyway so a future "route LOW to a web-search node
    # first" is a one-line change.
    workflow.add_conditional_edges(
        "confidence_router",
        route_from_tier,
        {
            ConfidenceTier.HIGH.value: "response_synthesizer",
            ConfidenceTier.MEDIUM.value: "response_synthesizer",
            ConfidenceTier.LOW.value: "response_synthesizer",
        },
    )

    workflow.add_edge("response_synthesizer", "artifact_selector")
    workflow.add_edge("artifact_selector", "verification_agent")
    workflow.add_edge("verification_agent", "memory_write")
    workflow.add_edge("memory_write", END)

    if checkpointer is not None:
        return workflow.compile(checkpointer=checkpointer)
    return workflow.compile()


# The process-wide instance the endpoint uses. Built lazily so importing this
# module does not require the checkpointer's Postgres connection at boot.
_TUTOR_GRAPH: Any | None = None


def tutor_graph():
    global _TUTOR_GRAPH
    if _TUTOR_GRAPH is None:
        _TUTOR_GRAPH = build_graph(checkpointer=get_checkpointer())
    return _TUTOR_GRAPH
