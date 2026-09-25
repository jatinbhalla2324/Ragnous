"""The generation step: compose the system prompt, call the LLM fallback chain,
return the model's answer.

The prompt itself is built by `prompts/system_blocks.build_system_prompt`,
which is where the dynamic blocks (Mermaid / 3D / Notes / Image legend) get
attached based on `teaching_aid`. This node concentrates on turning `AgentState`
into messages, walking the ordered fallback chain, and normalising the output.
"""

from __future__ import annotations

from typing import Any, Dict, List

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.agents.nodes.intent_classifier import video_trigger
from app.agents.prompts import build_system_prompt
from app.agents.state import AgentState, ConfidenceTier, TeachingAid
from app.services.llm_fallback import (
    ainvoke_with_fallback,
    build_full_chain,
    build_gemini_chain,
    build_groq_chain,
)


# Cached chains — building them per turn is measurable overhead.
_GROQ = None
_GEMINI = None
_FULL = None


def _groq_chain() -> list:
    global _GROQ
    if _GROQ is None:
        _GROQ = build_groq_chain("answer")
    return _GROQ


def _gemini_chain() -> list:
    global _GEMINI
    if _GEMINI is None:
        _GEMINI = build_gemini_chain("answer")
    return _GEMINI


def _full_chain() -> list:
    global _FULL
    if _FULL is None:
        _FULL = build_full_chain("answer")
    return _FULL


def _context_from_reranked(state: AgentState) -> str:
    scored = state.get("reranked") or []
    if not scored:
        return "No relevant context found in the textbook vector database."
    return "\n\n---\n\n".join(s["chunk"]["content"] for s in scored)


def _memory_hints(state: AgentState) -> tuple[str, str]:
    profile = state.get("profile") or {}
    prior = state.get("prior_summaries") or []

    profile_hint = ""
    weak = profile.get("weak_subjects") or []
    if weak:
        profile_hint = (
            "Weak areas the student has struggled with: "
            + ", ".join(weak[:6])
        )
    style = profile.get("learning_style")
    if style:
        profile_hint = (profile_hint + " · " if profile_hint else "") + f"Learns best via {style}."

    prior_hint = "\n".join(f"- {s}" for s in prior[:3])
    return profile_hint, prior_hint


async def response_synthesizer(state: AgentState) -> Dict[str, Any]:
    """Populate: answer_md."""
    context = _context_from_reranked(state)
    aid = state.get("teaching_aid", TeachingAid.NONE)
    tier = state.get("confidence_tier", ConfidenceTier.LOW)
    trigger = video_trigger(state.get("query", ""))

    profile_hint, prior_hint = _memory_hints(state)

    system_prompt = build_system_prompt(
        student_class=state.get("student_class", "8th"),
        language=state.get("language", "English"),
        aid=aid,
        tier=tier,
        needs_notes=bool(state.get("needs_notes")),
        has_attachments=False,   # graph endpoint keeps attachments to the legacy path for now
        in_syllabus=state.get("in_syllabus"),
        context=context,
        video_follow_up=(trigger == "confused" and tier == ConfidenceTier.LOW),
        profile_hint=profile_hint,
        prior_hint=prior_hint,
    )

    history: List[dict] = state.get("history") or []
    history_messages = []
    for turn in history[:-1]:                # last item is the current query
        role = turn.get("role")
        content = turn.get("content") or ""
        if role == "user":
            history_messages.append(HumanMessage(content=content))
        elif role == "assistant":
            history_messages.append(AIMessage(content=content))

    messages = [
        SystemMessage(content=system_prompt),
        *history_messages,
        HumanMessage(content=state.get("query", "")),
    ]

    # Model preference. The current graph path is text-only — attachments still
    # go through the legacy endpoint because their marshalling into vision
    # parts is nontrivial. When we lift attachments in, this is where the
    # gemini-first chain will kick in on any turn with images.
    if state.get("model_preference") == "ragnous_pro_x1" and _gemini_chain():
        chain = _gemini_chain() + _groq_chain()
    else:
        chain = _full_chain()

    response = await ainvoke_with_fallback(chain, messages, label="GRAPH SYNTH")

    content = response.content
    if isinstance(content, list):
        content = "".join(
            c.get("text", "") if isinstance(c, dict) else str(c) for c in content
        )
    elif not isinstance(content, str):
        content = str(content)

    return {"answer_md": content}
