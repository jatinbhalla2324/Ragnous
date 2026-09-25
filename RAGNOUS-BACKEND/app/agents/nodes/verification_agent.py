"""Cheap post-hoc check that the answer is grounded in retrieved chunks.

The old pipeline had no verification pass at all — a synthesiser mixup between
tier LOW and tier HIGH would just ship. This node runs a very small check:

- LOW tier or no citations → nothing to verify, `verified=False` silently.
- HIGH / MEDIUM tier → sample the top 3 chunks and ask a tiny model whether
  each factual sentence in the answer is *supported*. On disagreement, the
  answer is flagged and the badge is downgraded, but the answer itself is left
  intact — the alternative is regenerating (expensive) or blanking (bad UX).

This is intentionally not gating: it produces a signal the endpoint stores
alongside the confidence tier, which future turns can use to *back off* on the
badge without ever hiding a real answer from the student.
"""

from __future__ import annotations

import re
from typing import Any, Dict

from langchain_core.messages import HumanMessage

from app.agents.nodes.confidence_router import TIER_TO_MODE
from app.agents.state import AgentState, ConfidenceTier
from app.services.llm_fallback import ainvoke_with_fallback, build_groq_chain


_VERIFY_CHAIN = None


def _verify_chain() -> list:
    global _VERIFY_CHAIN
    if _VERIFY_CHAIN is None:
        # Uses the same intent-role chain — small, fast, JSON-friendly models.
        _VERIFY_CHAIN = build_groq_chain("intent")
    return _VERIFY_CHAIN


_VERIFY_PROMPT = """You are checking whether an NCERT tutor's answer is grounded in the textbook chunks below.

Do NOT rewrite the answer. Reply with ONLY one JSON object:
{"supported": true|false, "note": "<short reason, max 20 words>"}

- "supported": true if every factual claim in the answer is either backed by the chunks OR is common knowledge safe to teach at the student's class. false if the answer states specific numbers, dates, formulas or names not in the chunks and not general knowledge.
- The tutor's five-part teaching structure (analogies, worked examples) is fine even if not in the chunks — flag only *factual* deviations.

TEXTBOOK CHUNKS:
{context}

ANSWER TO CHECK:
{answer}
"""


def _first_json_object(text: str) -> Dict[str, Any] | None:
    """Pull the first {...} out of a possibly-fenced reply."""
    match = re.search(r"\{.*?\}", text, re.DOTALL)
    if not match:
        return None
    try:
        import json
        return json.loads(match.group(0))
    except (ValueError, TypeError):
        return None


async def verification_agent(state: AgentState) -> Dict[str, Any]:
    """Populate: verified, verification_note (and possibly downgrade citations)."""
    tier = state.get("confidence_tier", ConfidenceTier.LOW)
    answer = state.get("answer_md", "") or ""
    reranked = state.get("reranked") or []

    if tier == ConfidenceTier.LOW or not reranked or not answer.strip():
        return {"verified": False, "verification_note": ""}

    context = "\n\n---\n\n".join(
        s["chunk"]["content"][:600] for s in reranked[:3]
    )
    prompt = _VERIFY_PROMPT.format(context=context, answer=answer[:3000])

    try:
        res = await ainvoke_with_fallback(
            _verify_chain(),
            [HumanMessage(content=prompt)],
            label="VERIFY",
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[GRAPH VERIFY] check failed: {exc}")
        return {"verified": False, "verification_note": ""}

    payload = _first_json_object(res.content) or {}
    supported = bool(payload.get("supported"))
    note = str(payload.get("note") or "")[:160]

    print(f"[GRAPH VERIFY] supported={supported} note={note!r}")

    updates: Dict[str, Any] = {"verified": supported, "verification_note": note}

    # Downgrade the badge one step on failure — don't drop straight to LOW
    # because that would hide the sources, which is worse than a softer label.
    if not supported and tier == ConfidenceTier.HIGH:
        updates["confidence_tier"] = ConfidenceTier.MEDIUM
        print(f"[GRAPH VERIFY] downgrading tier HIGH -> MEDIUM (unsupported claims)")

    return updates


def display_mode(state: AgentState) -> str:
    """Badge label the endpoint should emit — used by the endpoint after the
    graph returns."""
    return TIER_TO_MODE[state.get("confidence_tier", ConfidenceTier.LOW)]
