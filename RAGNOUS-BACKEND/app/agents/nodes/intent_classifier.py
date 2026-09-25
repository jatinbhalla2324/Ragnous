"""Turns the student's turn + class into a lesson plan.

One Groq call, JSON-only output. The response tells the graph what teaching
aid to build, whether the topic is on syllabus, and hands back a semantic
rewrite of the query for the retriever (formal NCERT terms wired in).

This is a graph *node*: it reads the mutable `AgentState` and returns a partial
dict of updates. Everything expensive that also depends on `intent` (video
gating, PYQ lookup, retrieval) sits downstream of this node in the graph.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict

from langchain_core.messages import HumanMessage

from app.agents.prompts import build_intent_prompt
from app.agents.state import AgentState, Intent, TeachingAid
from app.services.llm_fallback import (
    ainvoke_with_fallback,
    build_gemini_chain,
    build_groq_chain,
)
from app.services.search_service import _parse_student_class


# Chain built once and cached — building it walks env variables + constructs
# LangChain clients, which does not want to happen per-turn.
_INTENT_CHAIN = None


def _intent_chain() -> list:
    global _INTENT_CHAIN
    if _INTENT_CHAIN is None:
        _INTENT_CHAIN = build_groq_chain("intent") or build_gemini_chain("answer")
    return _INTENT_CHAIN


# The planner occasionally wraps its JSON in ``` fences — strip anything before
# the first `{` and after the last `}` before we json.loads.
def _strip_fences(text: str) -> str:
    text = text.strip()
    if not text:
        return text
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


# ── Confusion / video signals — regexes lifted from chat.py ──────────────
# Kept here so the classifier is where all "what does the student mean" heuristics
# live, rather than scattered across nodes.
_VIDEO_REQUEST_RE = re.compile(
    r"\b(?:video|videos|youtube|yt\b|lecture|animation|animated (?:video|explanation)|"
    r"visual(?:ly)? (?:explain|show)|kisi ?video|koi ?video|video ?dikh|video ?bhej)\b",
    re.IGNORECASE,
)
_CONFUSION_RE = re.compile(
    r"(?:\b(?:i|we)\s+(?:still\s+|really\s+|just\s+)?"
    r"(?:can(?:no|')?t|cannot|don'?t|do not|couldn'?t)\s+"
    r"(?:seem to\s+)?(?:understand|get|follow|grasp|catch)\b"
    r"|\b(?:not|didn'?t|dont|don'?t)\s+(?:able to\s+)?(?:understand|get) (?:it|this|that|anything)\b"
    r"|\bi'?m\s+(?:so\s+|really\s+|totally\s+)?(?:confused|lost|stuck)\b"
    r"|\b(?:this|it|that)\s+(?:is|was|seems)\s+(?:too\s+)?"
    r"(?:hard|difficult|confusing|complicated)\b"
    r"|\bstill\s+(?:not clear|unclear|confusing|confused)\b"
    r"|\b(?:samajh|samjh)\s*(?:nahi|nhi|ni)\b"
    r"|\bexplain\s+(?:it\s+)?(?:again|once more|differently|in another way)\b"
    r"|\bmakes? no sense\b)",
    re.IGNORECASE,
)


def video_trigger(query: str) -> str | None:
    """"requested" | "confused" | None — parallel to the check in chat.py."""
    text = query or ""
    if _VIDEO_REQUEST_RE.search(text):
        return "requested"
    if _CONFUSION_RE.search(text):
        return "confused"
    return None


_NON_ACADEMIC = {
    "movie", "film", "trailer", "song", "songs", "music", "lyrics", "album",
    "cricket", "ipl", "football", "match", "highlights", "goals", "wwe",
    "game", "gaming", "gameplay", "minecraft", "freefire", "bgmi", "pubg",
    "vlog", "prank", "comedy", "meme", "memes", "cartoon", "anime",
    "actor", "actress", "celebrity", "influencer", "tiktok", "reels",
    "recipe", "cooking", "makeup", "fashion", "workout", "gym",
}


def _looks_non_academic(topic: str) -> bool:
    words = re.findall(r"[a-z0-9]+", (topic or "").lower())
    return bool(set(words) & _NON_ACADEMIC)


# ── Node ─────────────────────────────────────────────────────────────────
async def intent_classifier(state: AgentState) -> Dict[str, Any]:
    """Populate: intent, teaching_aid, aid_topic, subject_area, needs_notes,
    in_syllabus, semantic_query, student_class_no."""
    query = (state.get("query") or "").strip()
    student_class = state.get("student_class", "8th")
    student_class_no = _parse_student_class(student_class)

    if not query:
        # Nothing to plan against; downstream will bail cleanly.
        return {
            "intent": Intent.META,
            "teaching_aid": TeachingAid.NONE,
            "aid_topic": "",
            "subject_area": "",
            "needs_notes": False,
            "in_syllabus": None,
            "semantic_query": "",
            "student_class_no": student_class_no,
        }

    prompt = build_intent_prompt(student_class, query)

    intent_payload: Dict[str, Any] = {}
    try:
        res = await ainvoke_with_fallback(
            _intent_chain(), [HumanMessage(content=prompt)], label="INTENT"
        )
        intent_payload = json.loads(_strip_fences(res.content))
    except Exception as exc:  # noqa: BLE001 — planner is best-effort
        print(f"[GRAPH INTENT] planner failed: {exc}")

    teaching_aid_raw = str(intent_payload.get("teaching_aid") or "none").lower()
    if teaching_aid_raw not in {"none", "flowchart", "image", "3d", "video"}:
        teaching_aid_raw = "none"
    aid = TeachingAid(teaching_aid_raw)

    aid_topic = (intent_payload.get("aid_topic") or "").strip() or query
    subject_area = (intent_payload.get("subject_area") or "").strip()
    needs_notes = bool(intent_payload.get("needs_notes"))
    raw_syllabus = intent_payload.get("in_syllabus")
    in_syllabus = None if raw_syllabus is None else bool(raw_syllabus)
    semantic_query = (intent_payload.get("expanded_query") or query).strip()

    # Deterministic overrides — the small planner reads confusion as a plain
    # re-explain and emits "none"; force a video and keep the previous topic.
    trigger = video_trigger(query)
    if trigger and not needs_notes:
        aid = TeachingAid.VIDEO

    if _looks_non_academic(aid_topic):
        intent_kind = Intent.OUT_OF_SYLLABUS
    elif len(query.split()) <= 2 and re.match(
        r"^(?:hi|hey|hello|yo|thanks|thank you|ok(?:ay)?|cool)\W*$",
        query,
        re.IGNORECASE,
    ):
        intent_kind = Intent.GREETING
    elif needs_notes:
        intent_kind = Intent.META
    elif in_syllabus is False:
        intent_kind = Intent.OUT_OF_SYLLABUS
    else:
        intent_kind = Intent.ON_SYLLABUS

    print(
        f"[GRAPH INTENT] aid={aid.value} topic={aid_topic!r} "
        f"subject={subject_area!r} intent={intent_kind.value} "
        f"in_syllabus={in_syllabus} video_trigger={trigger}"
    )

    return {
        "intent": intent_kind,
        "teaching_aid": aid,
        "aid_topic": aid_topic,
        "subject_area": subject_area,
        "needs_notes": needs_notes,
        "in_syllabus": in_syllabus,
        "semantic_query": semantic_query,
        "student_class_no": student_class_no,
    }
