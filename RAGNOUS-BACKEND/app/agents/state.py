"""The one dict every node in the tutor graph reads and writes.

A LangGraph node returns a *partial* state — LangGraph merges the returned keys
into the running state — so each node here declares what it *produces*, not
what the whole pipeline holds. The full shape lives on `AgentState` so type
checkers, IDE autocomplete and the graph builder all see the same contract.

Field naming follows what already flows through `chat.py`: `semantic_query`,
`aid_topic`, `subject_area` etc. — so lifting the endpoint onto this state
does not renaming half the pipeline.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional, TypedDict


class Intent(str, Enum):
    """What the lesson planner decided this turn is."""
    ON_SYLLABUS = "on_syllabus"
    OUT_OF_SYLLABUS = "out_of_syllabus"
    GREETING = "greeting"
    META = "meta"                       # "give me a PDF", "read that aloud"


class TeachingAid(str, Enum):
    """The single visual aid the planner picked (or NONE)."""
    NONE = "none"
    FLOWCHART = "flowchart"
    IMAGE = "image"
    WIDGET_3D = "3d"
    VIDEO = "video"


class ConfidenceTier(str, Enum):
    """Retrieval grounding strength — drives both the badge and the prompt."""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Message(TypedDict, total=False):
    role: str            # "user" | "assistant"
    content: str


class Chunk(TypedDict, total=False):
    """A row from `ncert_chunks` after retrieval."""
    id: str
    subject: str
    chapter: str
    page_number: Optional[int]
    content: str
    vector_score: float
    fused_score: float


class ScoredChunk(TypedDict):
    score: float         # cross-encoder relevance (0-1)
    chunk: Chunk


class Citation(TypedDict):
    chapter: str
    page: int
    source: str


class Artifact(TypedDict, total=False):
    type: str            # "mermaid" | "widget_3d" | "youtube" | "notes" | "image"
    data: Any


# ── The state itself ──────────────────────────────────────────────────────
class AgentState(TypedDict, total=False):
    # Inputs (populated by the endpoint before invoke).
    query: str
    history: List[Message]
    student_class: str
    student_class_no: Optional[int]
    language: str
    model_preference: str                # "ragnous_x1" | "ragnous_pro_x1"
    user_id: Optional[str]               # for memory lookup; None = anonymous
    session_id: Optional[str]            # LangGraph thread id
    pyq_topics_seen: List[str]

    # Planner output.
    intent: Intent
    teaching_aid: TeachingAid
    aid_topic: str
    subject_area: str
    needs_notes: bool
    semantic_query: str                  # query rewritten with formal NCERT terms
    in_syllabus: Optional[bool]

    # Retrieval + rerank.
    ts_query: str
    vector_str: str
    class_subjects: Optional[List[str]]
    raw_rows: List[Chunk]
    reranked: List[ScoredChunk]
    rerank_backend: str                  # "cohere" | "local" | "none"
    score_kind: str                      # "rerank" | "cosine"
    best_score: float
    confidence_tier: ConfidenceTier

    # Synthesis + downstream.
    answer_md: str
    citations: List[Citation]
    artifact: Optional[Artifact]
    pyq: Optional[Dict[str, Any]]
    verified: bool
    verification_note: str

    # Memory (populated by the memory nodes before the planner runs).
    profile: Dict[str, Any]              # learning_style, language_preference, weak_subjects
    prior_summaries: List[str]           # top-k episodic summaries retrieved for this turn
