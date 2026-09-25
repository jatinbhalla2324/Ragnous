"""Post-generation: pull the widget/mermaid/video artifact out of the answer.

The response schema carries a *single* artifact, so this node runs LAST and
picks the first one that materialises: 3D widget → mermaid fence → notes token
→ YouTube (only if the planner asked for a video). Everything else gets
stripped from the visible text no matter what happens next, so raw tokens
never leak into the chat.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Dict, Optional

from app.agents.state import AgentState, Artifact, TeachingAid
from app.services import model3d_service
from app.services.youtube_service import find_lesson_videos


_MERMAID_RE = re.compile(
    r"```\s*mermaid[^\n]*\n(.*?)(?:\n\s*```|$)",
    re.IGNORECASE | re.DOTALL,
)


def _extract_widget_intent(content: str) -> tuple[Optional[dict], int, int]:
    """Pull `[WIDGET_3D_INTENT: {...}]` out of the reply.

    A regex cannot do this safely — the token carries nested JSON with braces
    inside string values, so we balance braces explicitly.
    """
    marker = "[WIDGET_3D_INTENT:"
    start = content.find(marker)
    if start == -1:
        return None, -1, -1
    open_idx = content.find("{", start)
    if open_idx == -1:
        return None, -1, -1

    depth = 0
    in_string = False
    escaped = False
    for i in range(open_idx, len(content)):
        ch = content[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                payload_str = content[open_idx: i + 1]
                end = i + 1
                while end < len(content) and content[end].isspace():
                    end += 1
                if end < len(content) and content[end] == "]":
                    end += 1
                try:
                    return json.loads(payload_str), start, end
                except json.JSONDecodeError:
                    return None, start, end
    return None, -1, -1


_NON_MOLECULE_TERMS = {
    "brain", "heart", "lung", "lungs", "kidney", "liver", "stomach", "eye",
    "ear", "nose", "tongue", "skin", "bone", "bones", "skeleton", "skull",
    "muscle", "neuron", "nerve", "spine", "tooth", "teeth", "intestine",
    "body", "hand", "leg", "arm", "organ", "cell", "tissue",
    "plant", "leaf", "flower", "root", "tree", "animal", "human", "insect",
    "fish", "bird", "frog", "butterfly",
    "atom", "planet", "earth", "moon", "sun", "star", "galaxy", "solar",
    "rover", "satellite", "rocket", "telescope", "microscope", "engine",
    "motor", "machine", "pump", "lever", "pulley", "circuit", "magnet",
    "volcano", "mountain", "river", "building", "monument", "fort", "temple",
}
_MOLECULE_HINTS = {
    "molecule", "compound", "chemical", "formula", "structure", "bond",
    "protein", "acid", "base", "salt", "oxide", "polymer", "isomer",
}


def _validate_widget_type(wtype: str, query: str) -> str:
    tokens = set(re.findall(r"[a-z]+", (query or "").lower()))
    if wtype == "molecule":
        if tokens & _NON_MOLECULE_TERMS and not (tokens & _MOLECULE_HINTS):
            print(f"[GRAPH ARTIFACT] corrected molecule -> search for {query!r}")
            return "search"
    return wtype


async def artifact_selector(state: AgentState) -> Dict[str, Any]:
    """Populate: artifact, answer_md (with widget tokens stripped)."""
    content = state.get("answer_md", "") or ""
    aid = state.get("teaching_aid", TeachingAid.NONE)
    aid_topic = state.get("aid_topic") or state.get("query", "")

    artifact: Optional[Artifact] = None

    # 3D widget token — always stripped, resolved only when parseable.
    intent_json, w_start, w_end = _extract_widget_intent(content)
    if w_start != -1:
        content = content[:w_start] + content[w_end:]
    if intent_json:
        try:
            wtype = intent_json.get("type")
            query = intent_json.get("query", "") or aid_topic
            wtype = _validate_widget_type(wtype, query)
            widget_data = await model3d_service.resolve_widget(wtype, query)
            if widget_data:
                labels = model3d_service.normalize_labels(
                    intent_json.get("labels"), widget_data.get("topic", "")
                )
                if labels:
                    widget_data["labels"] = labels
                artifact = {"type": "widget_3d", "data": widget_data}
        except Exception as exc:  # noqa: BLE001
            print(f"[GRAPH ARTIFACT] widget resolve failed: {exc}")

    # Notes token.
    if artifact is None and "[WIDGET_NOTES]" in content:
        content = content.replace("[WIDGET_NOTES]", "").strip()
        artifact = {"type": "notes", "data": {}}
        if not content:
            content = "I've synthesized our conversation. How would you like to receive your study notes?"

    # Mermaid fence.
    if artifact is None:
        match = _MERMAID_RE.search(content)
        if match:
            artifact = {"type": "mermaid", "data": match.group(1).strip()}
            content = _MERMAID_RE.sub("", content, count=1)

    # Video — the planner routed us here; go look one up.
    if artifact is None and aid == TeachingAid.VIDEO and state.get("in_syllabus") is not False:
        try:
            videos = await asyncio.to_thread(
                find_lesson_videos,
                aid_topic,
                state.get("subject_area") or "",
                state.get("student_class_no"),
                3,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[GRAPH ARTIFACT] youtube lookup failed: {exc}")
            videos = []
        if videos:
            artifact = {
                "type": "youtube",
                "data": {
                    "topic": aid_topic,
                    "reason": "planner",
                    "videos": videos,
                },
            }

    # Kill any stray YouTube markdown links now that the artifact owns the video.
    if artifact and artifact.get("type") == "youtube":
        content = re.sub(
            r"\[([^\]]*)\]\((?:https?://)?(?:www\.)?(?:youtube\.com|youtu\.be)/[^)]*\)",
            r"\1",
            content,
        )
        content = re.sub(
            r"(?:https?://)?(?:www\.)?(?:youtube\.com/watch\?\S+|youtu\.be/\S+)",
            "",
            content,
        )

    content = re.sub(r"\n{3,}", "\n\n", content).strip()

    return {"answer_md": content, "artifact": artifact}
