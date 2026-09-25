"""Between-session pass that updates weak_topics + semantic profile.

Called from `workers/tasks_reflection.py` on a cadence (nightly, or after a
session closes) — never on the request path, so the LLM budget spent here
doesn't slow down a live turn.

Steps:
1. Pull each user's recent quiz attempts and episodic summaries.
2. Ask a small LLM to name the topics the student is slipping on.
3. Update `weak_topics.confidence_score` and `next_review` (spaced repetition)
   and push the subject list into `semantic_profiles.weak_subjects`.

Kept intentionally thin — the workers file schedules it and this file owns
the logic. So tuning cadence is a Celery beat change, not code.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List

from langchain_core.messages import HumanMessage

from app.memory.semantic_profile import upsert_profile
from app.services.llm_fallback import ainvoke_with_fallback, build_groq_chain


_REFLECT_CHAIN = None


def _chain() -> list:
    global _REFLECT_CHAIN
    if _REFLECT_CHAIN is None:
        _REFLECT_CHAIN = build_groq_chain("intent")
    return _REFLECT_CHAIN


_REFLECT_PROMPT = """You are the reflection layer of an NCERT tutor. Given a student's recent activity below, name the topics they are struggling with.

Reply with ONLY a JSON object:
{"weak_subjects": ["<subject 1>", "<subject 2>", ...], "focus_topics": ["<topic 1>", ...]}

- weak_subjects: at most 4 NCERT subject names (Physics, Chemistry, Biology, Mathematics, Geography, History, Civics, Economics). Include only subjects with clear evidence of struggle.
- focus_topics: at most 5 specific concept names inside those subjects. Empty list if nothing stands out.

STUDENT ACTIVITY:
{activity}
"""


def _activity_summary(
    attempts: List[Dict],
    summaries: List[str],
) -> str:
    """Compact prose the reflector reads."""
    lines: List[str] = []
    if attempts:
        lines.append("Recent quiz attempts:")
        for a in attempts[-30:]:
            correct = "correct" if a.get("correct") else "WRONG"
            lines.append(
                f"- [{a.get('subject_area', '?')}/{a.get('topic', '?')}] {correct}: {a.get('question', '')[:120]}"
            )
    if summaries:
        lines.append("\nRecent session summaries:")
        for s in summaries[-6:]:
            lines.append(f"- {s}")
    return "\n".join(lines) or "(no activity)"


async def reflect_on_user(
    user_id: str,
    attempts: List[Dict],
    summaries: List[str],
) -> Dict[str, List[str]]:
    """Run the reflection prompt and persist the verdict. Returns the parsed
    JSON so a caller (Celery, the admin obs page) can log what happened."""
    if not user_id:
        return {"weak_subjects": [], "focus_topics": []}

    prompt = _REFLECT_PROMPT.format(activity=_activity_summary(attempts, summaries))
    try:
        res = await ainvoke_with_fallback(
            _chain(), [HumanMessage(content=prompt)], label="REFLECT"
        )
        text = (res.content or "").strip()
        # Strip fences if the tiny model wraps its JSON.
        if text.startswith("```"):
            text = text.strip("`")
            text = text.split("\n", 1)[1] if "\n" in text else text
        payload = json.loads(text[text.find("{"): text.rfind("}") + 1])
    except Exception as exc:  # noqa: BLE001
        print(f"[REFLECT] parse failed for {user_id!r}: {exc}")
        payload = {}

    weak = [str(x) for x in (payload.get("weak_subjects") or [])][:4]
    focus = [str(x) for x in (payload.get("focus_topics") or [])][:5]

    if weak:
        await upsert_profile(user_id, {"weak_subjects": weak})

    if focus:
        await _upsert_weak_topics(user_id, focus)

    return {"weak_subjects": weak, "focus_topics": focus}


async def _upsert_weak_topics(user_id: str, topic_names: Iterable[str]) -> None:
    """Insert or refresh spaced-repetition rows for the named topics.

    `concepts` needs an entry per topic first (created on demand), then
    `weak_topics` gets one row per (user, concept). The next review is scheduled
    a small delta into the future — SM-2 lite: fresh flags start at +1 day,
    revisits stretch out based on the current confidence.
    """
    from app.api.v1.chat import get_db_pool
    pool = await get_db_pool()
    if pool is None:
        return
    try:
        async with pool.acquire() as conn:
            for name in topic_names:
                concept_id = await conn.fetchval(
                    """
                    INSERT INTO concepts (name, subject)
                    VALUES ($1, 'auto')
                    ON CONFLICT DO NOTHING
                    RETURNING id
                    """,
                    name,
                ) or await conn.fetchval(
                    "SELECT id FROM concepts WHERE name = $1 LIMIT 1", name
                )
                if concept_id is None:
                    continue
                next_review = datetime.now(timezone.utc) + timedelta(days=1)
                await conn.execute(
                    """
                    INSERT INTO weak_topics (user_id, concept_id, confidence_score, last_reviewed, next_review)
                    VALUES ($1::uuid, $2, 0.2, now(), $3)
                    ON CONFLICT (user_id, concept_id) DO UPDATE
                    SET confidence_score = LEAST(weak_topics.confidence_score + 0.05, 1.0),
                        last_reviewed    = now(),
                        next_review      = $3
                    """,
                    user_id, concept_id, next_review,
                )
    except Exception as exc:  # noqa: BLE001
        print(f"[REFLECT] weak_topics update failed for {user_id!r}: {exc}")
