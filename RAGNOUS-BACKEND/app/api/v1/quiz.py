"""Quiz mode.

Once a chat has enough context, the student can tap "Quiz me on this topic"
(or type "give me quiz"). This endpoint:

  1. Reads the recent chat turns and extracts the TOPICS the conversation has
     actually been about — usually 1-3 (mitochondria, Non-Cooperation Movement,
     etc.). A single-topic chat still returns one; a chat that hopped from
     motion to chemistry returns both.
  2. Optionally pulls previous-year board-exam questions per topic from the
     public web (via Tavily), so at least one item on each topic is grounded
     in a real board question.
  3. Asks the LLM to write a small multiple-choice quiz — 5-6 items, 4 options
     each, one correct — DISTRIBUTED across every topic. Every question is
     tagged with the topic it belongs to so the client can record accuracy
     per topic in Progress and so no unrelated topic slips into the set.

The endpoint returns strict JSON the frontend renders in a quiz panel.
"""

import json
import os
import re
from typing import List, Optional

from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq
from pydantic import BaseModel, Field
from tavily import AsyncTavilyClient

from app.services.llm_fallback import (
    ainvoke_with_fallback,
    build_gemini_chain,
    build_groq_chain,
)
from app.services.pyq_service import fetch_pyq

load_dotenv(override=True)

router = APIRouter()

# Ordered fallback: Groq quiz models first (gpt-oss → qwen → kimi → gpt-oss-20b
# by default), then Gemini flashes (3.6 → 3.7 → 3.8). One decommissioned Groq
# model no longer takes quiz mode down, and a Groq rate-limit on a hot key
# rolls to Gemini instead of failing the request.
quiz_chain = build_groq_chain("quiz") + build_gemini_chain("quiz")
if not quiz_chain:
    raise RuntimeError(
        "No quiz model configured. Set GROQ_API_KEY and/or GEMINI_API_KEY in .env."
    )

# Kept for any external reference / debug print.
quiz_llm = quiz_chain[0]
GROQ_QUIZ_MODEL = getattr(quiz_llm, "model", None) or "?"

_tavily_key = os.getenv("TAVILY_API_KEY")
tavily_client = AsyncTavilyClient(api_key=_tavily_key) if _tavily_key else None

# Cap so a chat that jumped across five subjects still fits inside one prompt
# and still fits inside a friendly quiz length.
MAX_TOPICS = 3
QUESTIONS_PER_TOPIC = 2   # 2-3 topics × 2 = 4-6 questions; the LLM adds one
                          # "mixed" item on top so a two-topic quiz still hits 5.


# ── Request / response schemas ────────────────────────────────────────────

class Turn(BaseModel):
    role: str
    content: str


class QuizRequest(BaseModel):
    history: List[Turn] = Field(default_factory=list)
    student_class: str = "8th"
    language: str = "English"
    # If the caller already knows the topic (e.g. from a heading), pass it.
    # Otherwise we extract every topic in the chat.
    topic_hint: Optional[str] = None


class QuizQuestion(BaseModel):
    question: str
    options: List[str]
    answer_index: int
    explanation: str
    source: str = "generated"   # "generated" | "pyq"
    # Which of the discussed topics this question belongs to. Populated so the
    # Progress tab can record per-topic accuracy even when a quiz mixes topics
    # like "motion" and "acids and bases" in the same set.
    topic: str = ""


class QuizResponse(BaseModel):
    # Human-friendly combined label ("Motion · Acids and bases") for the header.
    topic: str
    # Individual topics discovered in the chat.
    topics: List[str] = Field(default_factory=list)
    subject_area: str
    questions: List[QuizQuestion]
    points_per_correct: int = 10
    pyq_sources: List[dict] = Field(default_factory=list)


# ── Helpers ───────────────────────────────────────────────────────────────

def _parse_class_no(student_class: str) -> Optional[int]:
    m = re.search(r"\d+", student_class or "")
    return int(m.group(0)) if m else None


def _summarize_history(history: List[Turn], max_chars: int = 4500) -> str:
    """Trim the chat to the last ~4.5k chars, alternating roles. Slightly
    larger than the single-topic version — multi-topic detection needs to see
    every subject the student touched, not just the most recent."""
    buf: List[str] = []
    total = 0
    for turn in reversed(history[-16:]):
        line = f"{turn.role.upper()}: {turn.content.strip()}"
        if not turn.content.strip():
            continue
        total += len(line) + 2
        if total > max_chars:
            break
        buf.append(line)
    return "\n".join(reversed(buf))


def _first_json_block(raw: str) -> Optional[str]:
    """Return the outermost JSON object in the model's reply, ignoring the
    fences and prose it sometimes wraps around it."""
    if not raw:
        return None
    start = raw.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(raw)):
        ch = raw[i]
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
                return raw[start:i + 1]
    return None


def _valid_mcq(item: dict) -> bool:
    if not isinstance(item, dict):
        return False
    q = str(item.get("question") or "").strip()
    opts = item.get("options") or []
    idx = item.get("answer_index")
    if len(q) < 8 or not isinstance(opts, list) or len(opts) != 4:
        return False
    if not all(isinstance(o, str) and o.strip() for o in opts):
        return False
    if not isinstance(idx, int) or idx < 0 or idx > 3:
        return False
    return True


async def _extract_topics(history_text: str, student_class: str, hint: Optional[str]) -> dict:
    """Ask the LLM for {topics: [...], subject_area}. Multi-topic on purpose —
    a chat that hopped from motion to chemistry should quiz on BOTH."""
    if hint and hint.strip():
        prompt = (
            f"The student (Class {student_class}) is being quizzed on: \"{hint.strip()}\".\n"
            "Return ONLY a JSON object: "
            '{"topics": ["<topic 1>", "<topic 2>"], "subject_area": '
            '"Science|English|Social Science"}\n'
            "Split multiple topics into separate entries if the hint names more than one; "
            "otherwise return a single-element list."
        )
    else:
        prompt = (
            f"You are helping quiz an Indian NCERT student (Class {student_class}).\n"
            "Read the chat below and list EVERY distinct topic the student has been\n"
            "LEARNING about. Between 1 and 3 topics. Pick the most specific real topic\n"
            "each time (e.g. 'Mitochondria', 'Non-Cooperation Movement', 'Reflection of\n"
            "light by curved mirrors'). Do NOT return whole chapter names, and do NOT\n"
            "invent topics that were not actually discussed.\n"
            "If the chat is small talk with no learnable topic, return topics = [].\n\n"
            "CHAT:\n"
            f"{history_text}\n\n"
            "Reply with ONLY a JSON object, nothing else:\n"
            '{"topics": ["<topic>", ...], "subject_area": '
            '"Science|English|Social Science"}'
        )

    try:
        resp = await ainvoke_with_fallback(
            quiz_chain, [HumanMessage(content=prompt)], label="QUIZ-TOPICS"
        )
        raw = getattr(resp, "content", "") or ""
        block = _first_json_block(raw) or "{}"
        data = json.loads(block)
    except Exception as e:
        print(f"[QUIZ] topic extraction failed: {e}")
        data = {}

    raw_topics = data.get("topics") or []
    if not isinstance(raw_topics, list):
        raw_topics = []

    topics: List[str] = []
    seen = set()
    for t in raw_topics:
        s = str(t or "").strip()
        if not s:
            continue
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        topics.append(s)
        if len(topics) >= MAX_TOPICS:
            break

    if not topics and hint and hint.strip():
        topics = [hint.strip()]

    return {
        "topics": topics,
        "subject_area": str(data.get("subject_area") or "").strip(),
    }


def _pyq_context_block(topic: str, pyq: Optional[dict]) -> str:
    """Ground a single topic's questions in REAL board-exam material."""
    if not pyq:
        return ""
    qs = pyq.get("questions") or []
    if not qs:
        return ""
    lines = [f"REAL PREVIOUS-YEAR BOARD QUESTIONS ON \"{topic}\":"]
    for q in qs[:4]:
        year = q.get("year")
        lines.append(f"  - ({year or 'year unknown'}) {q.get('text', '').strip()}")
    return "\n".join(lines)


async def _generate_multi_topic_quiz(
    topics: List[str],
    subject_area: str,
    student_class: str,
    language: str,
    pyq_by_topic: dict,
) -> List[dict]:
    """Ask the LLM for a strict-JSON quiz distributed across every topic."""
    class_no = _parse_class_no(student_class) or 10

    # Per-topic PYQ blocks so the prompt keeps each topic's ground truth
    # attached to that topic — otherwise a Class 10 physics PYQ ends up
    # informing the chemistry item.
    pyq_blocks = "\n\n".join(
        _pyq_context_block(t, pyq_by_topic.get(t))
        for t in topics
        if pyq_by_topic.get(t)
    )
    pyq_intro = (
        "Use these REAL previous-year questions to shape at least ONE item PER TOPIC "
        "that has them, and tag that item with source=\"pyq\":\n\n" + pyq_blocks + "\n\n"
        if pyq_blocks
        else ""
    )

    quota_line = ", ".join(f"{QUESTIONS_PER_TOPIC} on \"{t}\"" for t in topics)
    total_items = QUESTIONS_PER_TOPIC * len(topics)
    if len(topics) >= 2:
        quota_line += ", and 1 that DELIBERATELY connects two of the topics"
        total_items += 1

    topics_list = ", ".join(f"\"{t}\"" for t in topics)

    prompt = (
        f"You are an NCERT quiz-master for a Class {student_class} student. Language: {language}.\n\n"
        f"TOPICS in this chat (strict — do not add any others): {topics_list}\n"
        f"SUBJECT AREA: {subject_area or 'general'}\n\n"
        f"WRITE A MIXED MULTIPLE-CHOICE QUIZ — {total_items} items total, 4 options each, "
        "exactly one correct.\n"
        f"DISTRIBUTION: {quota_line}.\n\n"
        "HARDEST RULE — every question must be about one of the topics above and NOTHING\n"
        "ELSE. If a topic is \"Mitochondria\", questions on it must be about mitochondria\n"
        "(structure, function, cristae, ATP, why 'powerhouse of the cell', link with\n"
        "respiration). If a topic is \"Motion\", questions on it must be about motion\n"
        "(distance vs displacement, speed vs velocity, acceleration, graphs of motion,\n"
        "equations of motion). NEVER stray to other chapters, other years, or unrelated\n"
        "topics — an off-topic item is invalid.\n\n"
        "TAG EACH QUESTION with its source topic. Use exactly one of the strings from\n"
        f"the TOPICS list above ({topics_list}) in the \"topic\" field.\n\n"
        f"CLASS-APPROPRIATENESS — write at Class {class_no} NCERT level. Use the exact\n"
        "NCERT terminology the student sees in the textbook. Explanations must be one\n"
        "crisp line naming WHY the correct answer is correct.\n\n"
        "ITEM MIX per topic:\n"
        "  * 1 conceptual MCQ (definition, function, mechanism).\n"
        "  * 1 application/reasoning MCQ, or a PYQ-style one if real PYQs are given.\n\n"
        f"{pyq_intro}"
        "OUTPUT — reply with ONLY a JSON object (no prose, no fences):\n"
        "{\n"
        '  "questions": [\n'
        '    {"question": "...", "options": ["A","B","C","D"], "answer_index": 0,\n'
        '     "explanation": "one line reason", "source": "generated",\n'
        '     "topic": "<one of the topics above>" }\n'
        "  ]\n"
        "}\n\n"
        "answer_index is 0-based (0..3). All four options MUST be distinct, plausible, and\n"
        "brief (under 90 chars). Never write 'All of the above' or 'None of the above'."
    )

    system = SystemMessage(
        content=(
            "You write focused NCERT-aligned MCQ quizzes for Indian school students. You "
            "obey the topic list absolutely, tag every question with its topic, and reply "
            "with valid JSON only."
        )
    )

    try:
        resp = await ainvoke_with_fallback(
            quiz_chain, [system, HumanMessage(content=prompt)], label="QUIZ"
        )
        raw = getattr(resp, "content", "") or ""
    except Exception as e:
        print(f"[QUIZ] LLM call failed: {e}")
        raise HTTPException(status_code=502, detail="Quiz generator is unavailable right now.")

    block = _first_json_block(raw)
    if not block:
        print(f"[QUIZ] no JSON in reply. Raw head: {raw[:200]!r}")
        raise HTTPException(status_code=502, detail="Quiz generator returned no JSON.")

    try:
        data = json.loads(block)
    except json.JSONDecodeError as e:
        print(f"[QUIZ] JSON decode failed: {e}. Block: {block[:200]!r}")
        raise HTTPException(status_code=502, detail="Quiz generator returned bad JSON.")

    items = [q for q in (data.get("questions") or []) if _valid_mcq(q)]
    if not items:
        raise HTTPException(status_code=502, detail="Quiz generator produced no valid questions.")

    # Snap each question's topic tag back to one of the requested topics —
    # a small model will sometimes lowercase, pluralise, or reword the tag.
    lookup = {t.lower(): t for t in topics}
    for it in items:
        t = str(it.get("topic") or "").strip().lower()
        if t in lookup:
            it["topic"] = lookup[t]
        else:
            # Fuzzy: any topic that shares a distinctive word wins.
            match = next(
                (name for lc, name in lookup.items()
                 if lc and (lc in t or any(w in t for w in lc.split() if len(w) > 3))),
                topics[0],
            )
            it["topic"] = match

    return items[: max(total_items, 5)]


# ── Endpoint ──────────────────────────────────────────────────────────────

@router.post("/generate", response_model=QuizResponse)
async def generate_quiz(req: QuizRequest):
    if not req.history and not (req.topic_hint or "").strip():
        raise HTTPException(
            status_code=400,
            detail="Need at least some chat context or a topic hint to build a quiz.",
        )

    history_text = _summarize_history(req.history)
    extracted = await _extract_topics(history_text, req.student_class, req.topic_hint)
    topics: List[str] = extracted["topics"]
    subject_area: str = extracted["subject_area"]

    if not topics:
        raise HTTPException(
            status_code=422,
            detail="I couldn't spot a clear topic in this chat yet. Talk about one concept "
                   "for a bit and then tap Quiz.",
        )

    # Pull real PYQs per topic. Each failure is non-fatal — the quiz still
    # generates without ground-truth items, just less richly.
    pyq_by_topic: dict = {}
    pyq_sources: List[dict] = []
    for topic in topics:
        try:
            pyq = await fetch_pyq(
                tavily_client,
                topic=topic,
                subject_area=subject_area,
                class_no=_parse_class_no(req.student_class),
            )
        except Exception as e:
            print(f"[QUIZ] PYQ fetch failed for {topic!r} (non-fatal): {e}")
            pyq = None
        if pyq:
            pyq_by_topic[topic] = pyq
            pyq_sources.extend(pyq.get("sources") or [])

    items = await _generate_multi_topic_quiz(
        topics=topics,
        subject_area=subject_area,
        student_class=req.student_class,
        language=req.language,
        pyq_by_topic=pyq_by_topic,
    )

    display_topic = " · ".join(topics)
    return QuizResponse(
        topic=display_topic,
        topics=topics,
        subject_area=subject_area,
        questions=[QuizQuestion(**q) for q in items],
        points_per_correct=10,
        pyq_sources=pyq_sources[:6],
    )
