"""Study-notes synthesis.

"Save as notes" does not summarise the chat. It reads the chat only to work out
*what the student was learning*, and then builds a real study module for those
topics out of the NCERT corpus for their class:

    chat  ->  topics  ->  NCERT retrieval (class-scoped)  ->  per-topic sections
                                    |                                |
                                    +--> figures cropped from the same chapters

Three things make the output textbook-grade rather than chat-grade:

* **The source is the syllabus, not the transcript.** Each topic is embedded and
  run through the same hybrid retrieval the tutor uses, restricted to the books
  belonging to the student's class, so the notes contain the chapter's own
  material — including the parts the conversation never reached.
* **Every section is generated on its own.** One call for the whole document
  produced a page and a half, because the answer model caps at 2048 tokens.
  Sections are written independently and concatenated, which is what makes
  "detailed" achievable at all.
* **Illustrations come out of the student's own textbook.** The figure
  catalogue for the retrieved chapters is handed to the model, so it places
  real NCERT figures by number instead of inventing image search queries.
  See app/services/ncert_figure_service.py.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_groq import ChatGroq

from app.services import ncert_figure_service as figures
from app.services import rerank_service
from app.services.embedding_service import get_embedding_model, to_pgvector
from app.services.llm_fallback import ainvoke_with_fallback, build_gemini_chain, build_groq_chain
from app.services.search_service import (
    _parse_student_class,
    _subjects_for_class,
    dedupe_chunks,
    hybrid_search,
    medium_of,
)

# Reused rather than reimplemented: a second copy of the pool or of the tsquery
# builder would drift away from what the tutor actually runs.
from app.api.v1.chat import _build_or_tsquery, get_db_pool, gemini_llm

MAX_TOPICS = int(os.getenv("NOTES_MAX_TOPICS", "3"))
CHUNKS_PER_TOPIC = 8
FIGURES_PER_TOPIC = 4
FIGURE_BUDGET_S = float(os.getenv("NOTES_FIGURE_BUDGET_S", "25"))
# Notes are written from the English-medium books whatever language they are
# *written in*: Class 8 holds 674 English chunks against 770 Hindi and 952
# Punjabi, and the Devanagari/Gurmukhi PDFs extract as mangled ligatures, so
# retrieving them means writing a chapter's notes from unreadable source text.
NOTES_MEDIUM = os.getenv("NOTES_SOURCE_MEDIUM", "en")
# A chapter that has never been cropped costs a download plus a parse, so a
# single request will not chase more than this many of them.
MAX_COLD_CHAPTERS = int(os.getenv("NOTES_MAX_COLD_CHAPTERS", "2"))
# Extra passages pulled from the pages around the matched ones, so a section
# covers the topic the way the chapter does rather than as isolated fragments.
NEIGHBOUR_CHUNKS = int(os.getenv("NOTES_NEIGHBOUR_CHUNKS", "8"))
# Cross-encoder relevance a passage must reach before the notes are written
# from it. Retrieval always returns *something*: asked for "Volcanoes" in the
# Class 8 books — which have no volcanoes chapter — it returned the history
# chapter on Shivaji, and the section was written from portraits of Sant Ramdas.
# Same scale and same floor the chat path uses for citations (RAG_RERANK_MEDIUM).
RELEVANCE_FLOOR = float(os.getenv("NOTES_RELEVANCE_FLOOR",
                                  os.getenv("RAG_RERANK_MEDIUM", "0.12")))

# Two measured constraints shape how sections are generated, and they pull in
# opposite directions:
#
# * Groq is fast (0.8s to answer "say OK") but this key's on-demand tier allows
#   only 8,000 tokens per minute, so two full sections back to back return a
#   429 and the document loses a topic. Hence SECTION_MAX_TOKENS below, the
#   pause between sections, and the 429-aware retry in _invoke().
# * Gemini has the headroom but is extremely slow on this model — 82 seconds
#   measured for a one-word reply — so it cannot be the primary or a download
#   would take minutes.
#
# Groq writes, Gemini catches what Groq drops. The chat model's own 2048-token
# cap is not reused either way: one section needs more than that.
_GROQ_KEY = os.getenv("GROQ_API_KEY")
_GEMINI_KEY = os.getenv("GEMINI_API_KEY")

# Both providers carry a timeout, and every call is additionally raced against
# SECTION_TIMEOUT_S below: a provider that simply never answers used to hang
# the download until the browser gave up, with nothing in the log after the
# retrieval line.
LLM_TIMEOUT_S = float(os.getenv("NOTES_LLM_TIMEOUT_S", "120"))
GEMINI_TIMEOUT_S = float(os.getenv("NOTES_GEMINI_TIMEOUT_S", "300"))
# One section has to fit inside a single minute's token allowance, prompt
# included, or the next section starts its life waiting out a 429.
SECTION_MAX_TOKENS = int(os.getenv("NOTES_SECTION_MAX_TOKENS", "3600"))
# Groq's limit is per minute, so sections are spaced rather than raced.
SECTION_PAUSE_S = float(os.getenv("NOTES_SECTION_PAUSE_S", "8"))

# Ordered fallback chains. Groq answers in seconds; Gemini catches what Groq
# drops. Within each provider the walk moves to the next model on decommission
# or 5xx — see app/services/llm_fallback.py. Override the order with the
# GROQ_NOTES_MODELS / GEMINI_NOTES_MODELS env vars (comma-separated).
notes_groq_chain = build_groq_chain("notes", timeout=LLM_TIMEOUT_S)
notes_gemini_chain = build_gemini_chain("notes", timeout=GEMINI_TIMEOUT_S)

# Compat aliases still referenced elsewhere in the module.
notes_llm = notes_groq_chain[0] if notes_groq_chain else None
notes_gemini = notes_gemini_chain[0] if notes_gemini_chain else None
notes_gemini_backup = notes_gemini_chain[1] if len(notes_gemini_chain) > 1 else None
NOTES_MODEL = getattr(notes_llm, "model", None) if notes_llm else None
NOTES_GEMINI_MODEL = getattr(notes_gemini, "model", None) if notes_gemini else None
NOTES_GEMINI_BACKUP_MODEL = getattr(notes_gemini_backup, "model", None) if notes_gemini_backup else None

_plan_chain = build_groq_chain("intent") + build_gemini_chain("intent")
_plan_llm = _plan_chain[0] if _plan_chain else None


# ── Data carried through the pipeline ─────────────────────────────────────

@dataclass
class Source:
    """One NCERT passage the notes were built from."""
    subject: str
    chapter: str
    page: int
    excerpt: str


@dataclass
class NotesDocument:
    title: str
    subtitle: str
    markdown: str
    figures: Dict[str, figures.Figure] = field(default_factory=dict)
    sources: List[Source] = field(default_factory=list)
    topics: List[str] = field(default_factory=list)
    subject_area: str = ""
    student_class: str = ""
    grounded: bool = False


# ── Chat -> topics ────────────────────────────────────────────────────────

def _history_text(history: Sequence[dict], max_chars: int = 6000) -> str:
    buf, total = [], 0
    for turn in reversed(list(history)[-24:]):
        content = (turn.get("content") or "").strip()
        if not content:
            continue
        line = f"{(turn.get('role') or '').upper()}: {content}"
        total += len(line) + 1
        if total > max_chars:
            break
        buf.append(line)
    return "\n".join(reversed(buf))


def _first_json_block(raw: str) -> Optional[str]:
    if not raw:
        return None
    start = raw.find("{")
    if start == -1:
        return None
    depth, in_string, escaped = 0, False, False
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


_TOPIC_STOP = {"the", "of", "and", "in", "a", "an", "to", "its", "by", "for"}


def _duplicate_topic(candidate: str, existing: Sequence[str]) -> bool:
    """True when a topic just renames one already on the list.

    The planner happily returned 'Reflex action', 'Reflex arc' and 'Spinal
    cord' for a single conversation, which would have produced three near
    identical sections built from the same chapter.
    """
    def terms(s: str) -> set:
        return {w for w in re.findall(r"[a-z]{3,}", s.lower()) if w not in _TOPIC_STOP}

    cand = terms(candidate)
    if not cand:
        return True
    for other in existing:
        prev = terms(other)
        if not prev:
            continue
        overlap = len(cand & prev) / min(len(cand), len(prev))
        if overlap >= 0.5:
            return True
    return False


def display_title(topic: str) -> str:
    """A heading a student would recognise, from whatever the planner returned.

    The planner is told to name topics like a contents page and still returns
    "Metal reactions with acids, oxygen, reactivity". The full phrase is a
    perfectly good retrieval query, so it is kept for searching and only the
    heading is trimmed — and only when the first clause can stand on its own,
    so a real chapter name like "Acids, Bases and Salts" survives intact.
    """
    head = re.split(r",| and ", topic or "", maxsplit=1)[0].strip(" .:-")
    if len(head.split()) >= 2:
        return head[0].upper() + head[1:]
    return (topic or "").strip()


async def plan_notes(history: Sequence[dict], student_class: str) -> dict:
    """{title, topics, subject_area} — what this chat was actually teaching."""
    text = _history_text(history)
    fallback = {"title": "Study Notes", "topics": [], "subject_area": ""}
    if not _plan_llm or not text:
        return fallback

    prompt = (
        f"You are preparing study notes for an Indian NCERT student in Class {student_class}.\n"
        "Read the chat below and identify the distinct topics the student was LEARNING\n"
        "about. Use the specific syllabus topic each time — e.g. 'Control and\n"
        "coordination', 'Nationalism in Europe', 'Refraction of light'.\n"
        "MERGE anything that belongs to one piece of syllabus into a SINGLE topic:\n"
        "'reflex action', 'reflex arc' and 'spinal cord' are one topic, not three.\n"
        "Return 1 topic for a focused chat and 2-3 ONLY when the student genuinely\n"
        "moved between different chapters or subjects.\n"
        "Name each topic the way a textbook contents page would — at most 6 words,\n"
        "never a comma-separated list of sub-topics.\n"
        "Ignore greetings, app chatter and anything not academic.\n"
        "Also give a short title for the whole notes document (max 8 words).\n\n"
        f"CHAT:\n{text}\n\n"
        "Reply with ONLY this JSON:\n"
        '{"title": "<document title>", "topics": ["<topic>", ...], '
        '"subject_area": "Science|Social Science|English|Mathematics"}'
    )
    try:
        resp = await ainvoke_with_fallback(
            _plan_chain, [HumanMessage(content=prompt)], label="NOTES-PLAN"
        )
        data = json.loads(_first_json_block(getattr(resp, "content", "") or "") or "{}")
    except Exception as e:
        print(f"[NOTES] topic planning failed: {e}")
        return fallback

    topics: List[str] = []
    for t in data.get("topics") or []:
        name = str(t or "").strip()
        if name and not _duplicate_topic(name, topics):
            topics.append(name)
        if len(topics) >= MAX_TOPICS:
            break
    return {
        "title": str(data.get("title") or "").strip() or "Study Notes",
        "topics": topics,
        "subject_area": str(data.get("subject_area") or "").strip(),
    }


# ── Topic -> NCERT passages ───────────────────────────────────────────────

# One embedding at a time; see the note in build_notes().
_EMBED_LOCK = asyncio.Lock()

# Exercise pages and answer keys ingest as rows of dot leaders, and a chunk of
# those teaches nothing while eating the prompt budget.
_JUNK_RE = re.compile(r"[.\u2026_\s]")


def _usable_passage(text: str) -> bool:
    """Reject fill-in-the-blank dot leaders and near-empty chunks."""
    if len(text) < 120:
        return False
    stripped = _JUNK_RE.sub("", text)
    return len(stripped) >= 0.55 * len(text)


_NEIGHBOUR_SQL = """
    SELECT subject, chapter, page_number, content
    FROM ncert_chunks
    WHERE chapter = $1
      AND page_number BETWEEN $2 AND $3
    ORDER BY page_number
    LIMIT 24
"""


def _keep_usable(rows) -> List[Source]:
    """Retrieval hits worth writing from: real prose, from a book in the
    medium the notes are sourced in."""
    out: List[Source] = []
    for row in dedupe_chunks(rows, limit=CHUNKS_PER_TOPIC * 2):
        text = (row["content"] or "").strip()
        if not _usable_passage(text):
            continue
        if medium_of(row["subject"]) != NOTES_MEDIUM and medium_of(row["chapter"]) != NOTES_MEDIUM:
            print(f"[NOTES] dropping {row['chapter']} — not a {NOTES_MEDIUM} book")
            continue
        out.append(Source(subject=row["subject"], chapter=row["chapter"],
                          page=row["page_number"], excerpt=text))
        if len(out) >= CHUNKS_PER_TOPIC:
            break
    return out


async def _relevant_only(topic: str, sources: List[Source]) -> List[Source]:
    """Drop passages that are not actually about the topic.

    A vector search returns the nearest passages whether or not any of them
    answer the question, so a topic the class's books do not cover comes back
    with the closest unrelated chapter instead of with nothing. Writing notes
    from those is how "Volcanoes" for Class 8 ended up sourced from a history
    chapter. The cross-encoder can tell the difference; cosine cannot (see
    app/services/rerank_service.py).
    """
    if not sources:
        return []
    docs = [s.excerpt for s in sources]
    try:
        ranked, backend = await asyncio.to_thread(
            rerank_service.rerank, topic, docs, len(docs))
    except Exception as e:
        print(f"[NOTES] rerank unavailable ({e}); keeping retrieval order")
        return sources
    if not ranked:
        return sources

    kept = [sources[idx] for idx, score in ranked if score >= RELEVANCE_FLOOR]
    if not kept:
        best = max(score for _, score in ranked)
        print(f"[NOTES] {topic!r}: nothing in this class's books is about it "
              f"(best {backend} relevance {best:.3f} < {RELEVANCE_FLOOR}); "
              f"the section will be written from the syllabus, unsourced")
    elif len(kept) != len(sources):
        print(f"[NOTES] {topic!r}: {len(sources) - len(kept)} off-topic "
              f"passage(s) dropped by the {backend} cross-encoder")
    return kept


async def _surrounding_pages(conn, sources: Sequence[Source]) -> List[Source]:
    """The pages either side of the ones retrieval matched.

    Retrieval returns the passages closest to the query, which are fragments of
    an explanation rather than the whole of it — the paragraph that defines the
    term is often on the page before the one that mentions it. Widening to the
    neighbouring pages of the same chapter is what lets a section cover the
    topic the way the chapter does, and it cannot wander off-syllabus because
    it never leaves that chapter.
    """
    if not sources:
        return []
    main = sources[0].chapter
    pages = sorted({s.page for s in sources if s.chapter == main})
    if not pages:
        return []
    try:
        rows = await conn.fetch(_NEIGHBOUR_SQL, main, max(1, pages[0] - 1), pages[-1] + 1)
    except Exception as e:
        print(f"[NOTES] could not widen {main}: {e}")
        return []

    seen = {(s.chapter, s.page, s.excerpt[:80]) for s in sources}
    extra = []
    for row in rows:
        text = (row["content"] or "").strip()
        key = (row["chapter"], row["page_number"], text[:80])
        if key in seen or not _usable_passage(text):
            continue
        seen.add(key)
        extra.append(Source(subject=row["subject"], chapter=row["chapter"],
                            page=row["page_number"], excerpt=text))
    return extra


async def retrieve_topic(topic: str, class_no: Optional[int]) -> List[Source]:
    """The chapter material for one topic, restricted to this class's books."""
    try:
        model = get_embedding_model()
        async with _EMBED_LOCK:
            vector = await asyncio.to_thread(model.encode, topic)
        vector_str = to_pgvector(vector.tolist())
        ts_query = _build_or_tsquery(topic)
    except Exception as e:
        print(f"[NOTES] could not embed {topic!r}: {e}")
        return []

    rows, conn_holder = [], None
    try:
        pool = await get_db_pool()
        if pool:
            async with pool.acquire() as conn:
                class_subjects = await _subjects_for_class(conn, class_no, NOTES_MEDIUM)
                rows = await hybrid_search(conn, vector_str, ts_query, class_subjects or None)
                if not rows and class_subjects:
                    print(f"[NOTES] {topic!r}: nothing in class {class_no} books; widening")
                    rows = await hybrid_search(conn, vector_str, ts_query, None)
                conn_holder = conn
                matched = await _relevant_only(topic, _keep_usable(rows))
                extra = await _surrounding_pages(conn, matched)
    except Exception as e:
        print(f"[NOTES] retrieval failed for {topic!r}: {e}")
        return []
    if conn_holder is None:
        return []

    out = matched + extra[:NEIGHBOUR_CHUNKS]
    print(f"[NOTES] {topic!r}: {len(matched)} matched + {len(extra[:NEIGHBOUR_CHUNKS])} "
          f"neighbouring passages from {len({s.chapter for s in out})} chapter(s)")
    return out


def _figure_catalogue(sources: Sequence[Source], topic: str) -> List[figures.Figure]:
    """Figures available to illustrate this topic, from its own chapters.

    Only English-medium chapters are considered. A Hindi chapter captions its
    figures "चित्र 1.2", which no English topic will ever match — the run that
    prompted this downloaded eight Hindi chapters, cropped zero figures from
    them, and blew the request's figure budget doing it.
    """
    chapters = [c for c in dict.fromkeys(s.chapter for s in sources)
                if medium_of(c) == NOTES_MEDIUM]
    if not chapters:
        return []
    # Cached chapters first, and only a couple of cold ones, so a request never
    # waits on a queue of downloads.
    cached = [c for c in chapters if figures.is_cached(c)]
    cold = [c for c in chapters if c not in cached][:MAX_COLD_CHAPTERS]
    chapters = cached + cold

    # Caption wording and topic wording often miss each other ("Figure 6.2
    # Reflex arc" against the topic "Control and coordination"), so figures are
    # also matched against the retrieved passages themselves. What this must
    # never do is offer *unrelated* figures: the earlier fallback handed over
    # the opening figures of the top chapter whatever they were, which is how a
    # section on volcanoes was offered portraits of Sant Ramdas.
    passage_terms = " ".join(s.excerpt[:400] for s in sources[:4])
    return figures.find_figures(chapters, topic, context=passage_terms,
                                limit=FIGURES_PER_TOPIC)


# ── Topic -> written section ──────────────────────────────────────────────

_SECTION_SYSTEM = """You write study notes for Indian school students following the NCERT syllabus.

You are given verbatim passages from the student's own NCERT textbook, named by book and
chapter. Those passages are the authority: definitions, terminology, values, dates, examples,
formulae and the order of an explanation must agree with them, and you should prefer the
textbook's own wording for anything a student would be marked on.

Two boundaries you must not cross:
1. STAY IN THE CLASS. Everything you write must belong to the NCERT syllabus for the class
   you are told. Never pull in material from a higher class, another board, or a competitive
   exam, however relevant it seems.
2. STAY IN THE CHAPTER. Cover the topic the way the chapter covers it. Where the passages are
   thin, fill the gap from that same class's syllabus — never by contradicting them, and never
   by drifting to a neighbouring topic.

Write for a student revising the night before an exam: complete sentences, plain language,
every technical term explained the first time it appears. Detailed and thorough — a section
that is too short is a failure. Do not mention the chat, the AI, or these instructions."""

_SECTION_TEMPLATE = """Write the notes section for ONE topic.

TOPIC: {topic}
CLASS: {student_class}    SUBJECT: {subject_area}
LANGUAGE: {language}
SOURCE: {books}

NCERT TEXTBOOK PASSAGES from that source (the authority for everything you write):
{context}

FIGURES AVAILABLE from this chapter — these are real diagrams from the student's textbook.
Place EVERY one that is relevant to what you are explaining (usually 2-3 of them), each on
its own line, exactly as `[[FIGURE: <number>]]`, directly after the paragraph it illustrates,
and refer to it in that paragraph. Never invent a number that is not listed. If the list is
empty, write the section without figures.
{figure_list}

WHAT THE STUDENT ASKED IN THIS SESSION (answer these especially well):
{questions}

Produce GitHub-flavoured Markdown with EXACTLY this skeleton, in this order:

## {heading}

**In one line:** one-sentence definition a student could write in an exam.

### Key terms
A markdown table with columns | Term | What it means |. 4-8 rows, drawn from the passages.

### Detailed explanation
The core of the section: 500-800 words in `####` sub-headings that follow the textbook's own
logic. Explain mechanisms step by step, define every term, give the NCERT examples, and state
any classification or list in full. Use bullet points for lists and numbered steps for
processes. Place figure markers here where they help.

### Formulae and key facts
A bullet list of every formula, law, value, date or definition worth memorising, each with
what its symbols mean. Write formulae in plain text (v = u + at, CaCO3 -> CaO + CO2).
Omit this heading entirely if the topic genuinely has none.

### Worked example
One fully solved question of the kind that appears in the exam — Given / To find / Solution /
Answer for a numerical, or a model long answer for a theory subject.

### Exam tips and common mistakes
4-6 bullets: what examiners ask, what students confuse, how marks are lost.

### Quick revision
6-8 one-line bullets that summarise the whole section.

### Practice questions
5 questions of mixed difficulty (include at least one 3-mark and one 5-mark). Give the answer
for each on the following line, in the form **Answer:** …

Rules: no preamble before `## {heading}`, no closing remarks after the last answer, no code
blocks, nothing outside the Class {student_class} syllabus, and write everything in {language}.

Never point the student at a chapter: no "Chapter 3", no "in this chapter", no "as covered in
the chapter on metals", no chapter or table numbers. Say the thing itself instead of naming
where it sits in the book."""


def _book_line(sources: Sequence[Source], student_class: str) -> str:
    """'NCERT Class 8 Science (English), pages 2-9'.

    Naming the book in the prompt is what keeps a section anchored to the
    material it is supposed to be about: without it the model treats the
    passages as loose facts and drifts towards whatever it knows best. The
    NCERT file code is deliberately left out — the model echoes whatever it is
    given, and "HECU105" means nothing to a student.
    """
    if not sources:
        return f"NCERT Class {student_class} (no textbook passage retrieved)"
    parts = []
    for book in dict.fromkeys(s.subject for s in sources):
        pages = sorted({s.page for s in sources if s.subject == book})
        span = f"page {pages[0]}" if len(pages) == 1 else f"pages {pages[0]}-{pages[-1]}"
        parts.append(f"{book.replace('_', ' ')}, {span}")
    return f"NCERT Class {student_class}: " + "; ".join(parts[:3])


_UNGROUNDED_NOTE = """(Nothing in the Class {student_class} books matched this topic — the
relevance check rejected every passage retrieved.)

Write the section from the Class {student_class} NCERT syllabus as you know it. Keep it strictly
at that class's level, do not attribute any statement to a page or figure, and do not invent
a textbook quotation. If the topic genuinely belongs to a different class, cover only the part
of it that a Class {student_class} student is expected to know."""


def _context_block(sources: Sequence[Source], budget: int = 6500) -> str:
    parts, total = [], 0
    for i, s in enumerate(sources, 1):
        body = re.sub(r"^\s*\[Book:[^\]]*\]\s*", "", s.excerpt)
        body = " ".join(body.split())[:1400]
        block = f"[{i}] (page {s.page})\n{body}"
        total += len(block)
        if total > budget:
            break
        parts.append(block)
    return "\n\n".join(parts)


def _questions_block(history: Sequence[dict], limit: int = 6) -> str:
    qs = [
        " ".join((t.get("content") or "").split())[:200]
        for t in history if (t.get("role") or "") == "user"
    ]
    qs = [q for q in qs if len(q) > 8][-limit:]
    return "\n".join(f"- {q}" for q in qs) if qs else "- (no specific questions asked)"


def _text_of(resp) -> str:
    """Gemini returns `content` as a list of parts; Groq returns a string."""
    content = getattr(resp, "content", "") or ""
    if isinstance(content, str):
        return content
    parts = []
    for part in content:
        if isinstance(part, str):
            parts.append(part)
        elif isinstance(part, dict):
            parts.append(str(part.get("text") or ""))
    return "".join(parts)


_RETRY_AFTER_RE = re.compile(r"try again in ([0-9.]+)s", re.I)


def _rate_limit_wait(error) -> Optional[float]:
    """Seconds Groq asked us to wait, when the failure was a rate limit."""
    text = str(error)
    if "rate_limit" not in text and "429" not in text:
        return None
    m = _RETRY_AFTER_RE.search(text)
    return min(float(m.group(1)) + 1.0, 30.0) if m else 12.0


def _providers():
    """Who writes a section, in the order they are asked.

    Groq answers first (seconds, not minutes), walking through every model on
    the answer chain — one model getting rate-limited or decommissioned no
    longer takes the whole document down. Then Gemini flashes in order (3.6,
    3.7, 3.8, …), which have the headroom Groq lacks. The chat-answer Gemini
    is kept as a final safety net; its 2048-token cap truncates a section, so
    it is a last resort, not a peer.
    """
    entries: List[tuple] = []
    for client in notes_groq_chain:
        entries.append((f"Groq[{client.model}]", client, LLM_TIMEOUT_S + 15))
    for client in notes_gemini_chain:
        entries.append((f"Gemini[{client.model}]", client, GEMINI_TIMEOUT_S + 15))
    if gemini_llm is not None and all(
        getattr(c, "model", None) != getattr(gemini_llm, "model", None)
        for c in notes_gemini_chain
    ):
        entries.append(("chat Gemini", gemini_llm, GEMINI_TIMEOUT_S + 15))
    return entries


async def _try_provider(name, client, budget, messages, errors) -> Optional[str]:
    """One attempt. Returns the text, or None with the reason recorded."""
    try:
        text = _text_of(await asyncio.wait_for(client.ainvoke(messages), budget))
        if text.strip():
            return text
        errors.append(f"{name}: empty response")
    except asyncio.TimeoutError:
        errors.append(f"{name}: timed out after {budget:.0f}s")
        print(f"[NOTES] {name} timed out; trying the next model")
    except Exception as e:
        errors.append(f"{name}: {e}")
        print(f"[NOTES] {name} failed ({str(e)[:200]})")
    return None


async def _invoke(messages) -> str:
    """Write one piece of the document, moving down the provider list.

    A rate-limited Groq is *not* waited out here: Gemini has the headroom and
    is already next in line, so sleeping first only added the wait to the
    download. The wait is kept as a last resort, for the case where every
    other provider also failed.
    """
    errors: List[str] = []
    deferred_wait: Optional[float] = None

    for name, client, budget in _providers():
        if client is None:
            continue
        before = len(errors)
        text = await _try_provider(name, client, budget, messages, errors)
        if text is not None:
            return text
        if deferred_wait is None and len(errors) > before:
            wait = _rate_limit_wait(errors[-1])
            if wait:
                deferred_wait = wait
                print(f"[NOTES] {name} is rate-limited for {wait:.0f}s; "
                      f"trying the next model instead of waiting")

    if deferred_wait is not None and notes_llm is not None:
        print(f"[NOTES] every model failed; waiting out Groq's {deferred_wait:.0f}s limit")
        await asyncio.sleep(deferred_wait)
        text = await _try_provider("Groq (retry)", notes_llm,
                                   LLM_TIMEOUT_S + 15, messages, errors)
        if text is not None:
            return text

    raise RuntimeError("; ".join(errors) or "no notes model configured")


def _strip_fences(md: str) -> str:
    """Models sometimes wrap the whole section in ```markdown … ```."""
    t = md.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*\n", "", t)
        t = re.sub(r"\n```\s*$", "", t)
    return t.strip()


async def write_section(topic: str, sources: Sequence[Source], catalogue: Sequence[figures.Figure],
                        history: Sequence[dict], student_class: str, subject_area: str,
                        language: str) -> str:
    figure_list = "\n".join(
        f"- {f.number}: {f.caption}" for f in catalogue
    ) or "(none available — write without figures)"

    context = (_context_block(sources) if sources
               else _UNGROUNDED_NOTE.format(student_class=student_class))
    prompt = _SECTION_TEMPLATE.format(
        topic=topic,
        heading=display_title(topic),
        books=_book_line(sources, student_class),
        student_class=student_class,
        subject_area=subject_area or "General",
        language=language or "English",
        context=context,
        figure_list=figure_list,
        questions=_questions_block(history),
    )
    md = await _invoke([SystemMessage(content=_SECTION_SYSTEM), HumanMessage(content=prompt)])
    md = _strip_fences(md)
    if md and not md.lstrip().startswith("## "):
        md = f"## {display_title(topic)}\n\n{md}"
    return md


_OVERVIEW_TEMPLATE = """Write the opening of a study-notes document for a Class {student_class}
student. The notes cover: {topic_list}.

Write in {language}, in Markdown, and produce ONLY:

## How to use these notes

One short paragraph (60-90 words) saying what this module covers and how the topics connect.

### Session summary
3-5 bullets recording what the student worked through in this session, based on their
questions below.

QUESTIONS THE STUDENT ASKED:
{questions}

No heading above `## How to use these notes`, and nothing after the last bullet."""


async def write_overview(topics: Sequence[str], history: Sequence[dict],
                         student_class: str, language: str) -> str:
    try:
        md = await _invoke([
            SystemMessage(content=_SECTION_SYSTEM),
            HumanMessage(content=_OVERVIEW_TEMPLATE.format(
                student_class=student_class,
                topic_list=", ".join(topics),
                language=language or "English",
                questions=_questions_block(history, limit=8),
            )),
        ])
        return _strip_fences(md)
    except Exception as e:
        print(f"[NOTES] overview failed: {e}")
        return ""


# ── Figure markers -> real figures ────────────────────────────────────────

_MARKER_RE = re.compile(r"\[\[\s*FIGURE\s*:\s*([^\]]+?)\s*\]\]", re.I)


def resolve_figures(markdown: str, catalogue: Sequence[figures.Figure]) -> tuple:
    """Turn `[[FIGURE: 6.3]]` markers into keyed placeholders the renderer can
    draw, dropping any the model invented and any it repeated."""
    by_number = {f.number: f for f in catalogue}
    used: Dict[str, figures.Figure] = {}
    seen_paths = set()

    def replace(m):
        raw = m.group(1).strip()
        fig = by_number.get(raw)
        if fig is None:
            key = re.sub(r"[^0-9.]", "", raw)
            fig = by_number.get(key)
        if fig is None:
            wanted = figures._terms(raw)
            ranked = sorted(
                ((figures.score_figure(f, wanted), f) for f in catalogue),
                key=lambda t: t[0], reverse=True,
            )
            fig = ranked[0][1] if ranked and ranked[0][0] > 0 else None
        if fig is None or fig.path in seen_paths:
            return ""
        seen_paths.add(fig.path)
        token = f"fig{len(used)}"
        used[token] = fig
        return f"[[IMG:{token}]]"

    return _MARKER_RE.sub(replace, markdown), used


# ── The whole document ────────────────────────────────────────────────────

async def build_notes(history: Sequence[dict], student_class: str = "10th",
                      language: str = "English", subjects: Optional[Sequence[str]] = None,
                      topic_hint: Optional[str] = None) -> NotesDocument:
    class_no = _parse_student_class(student_class)
    plan = await plan_notes(history, student_class)
    topics = plan["topics"]
    if topic_hint and topic_hint.strip():
        topics = [topic_hint.strip()] + [t for t in topics if t.lower() != topic_hint.strip().lower()]
        topics = topics[:MAX_TOPICS]
    if not topics:
        # Nothing academic was discussed. Say so honestly rather than inventing
        # a syllabus topic the student never touched.
        raise ValueError(
            "This chat has no study topic yet — ask a question about a subject first, "
            "then save the notes."
        )

    subject_area = plan["subject_area"] or (subjects[0] if subjects else "")
    print(f"[NOTES] class {class_no} · topics: {topics}")

    # Sequential on purpose: the embedding model is a single shared
    # SentenceTransformer, and encoding three topics from three threads at once
    # killed the process outright (silent exit, leaked loky semaphore). Three
    # short encodes cost well under a second anyway.
    retrieved = [await retrieve_topic(t, class_no) for t in topics]
    catalogues = []
    for topic, srcs in zip(topics, retrieved):
        # A chapter that has never been cropped can take a minute to parse
        # (jesc108 measured 85s), which nobody should wait for mid-download.
        # scripts/prewarm_figures.py exists so this is a cache read in
        # practice; the budget is what happens when it is not.
        try:
            catalogues.append(await asyncio.wait_for(
                asyncio.to_thread(_figure_catalogue, srcs, topic), FIGURE_BUDGET_S))
        except asyncio.TimeoutError:
            print(f"[NOTES] figure lookup for {topic!r} exceeded {FIGURE_BUDGET_S}s; "
                  f"writing this section without figures (the chapter keeps "
                  f"cropping in the background, so the next run will have them)")
            catalogues.append([])
        except Exception as e:
            print(f"[NOTES] figure lookup for {topic!r} failed: {e}")
            catalogues.append([])

    # Sequential, again for a rate limit rather than for style: sections are
    # thousands of tokens each and firing them together spends a whole minute's
    # allowance in one burst, which costs a topic.
    sections = []
    for index, (topic, srcs, cat) in enumerate(zip(topics, retrieved, catalogues)):
        if index:
            await asyncio.sleep(SECTION_PAUSE_S)
        print(f"[NOTES] writing {topic!r} with {len(cat)} candidate figure(s)")
        try:
            sections.append(await write_section(
                topic, srcs, cat, history, student_class, subject_area, language))
        except Exception as e:
            sections.append(e)

    overview = await write_overview(topics, history, student_class, language)

    body_parts = [overview] if overview else []
    all_figures: Dict[str, figures.Figure] = {}
    kept_topics: List[str] = []

    for topic, section, cat in zip(topics, sections, catalogues):
        if isinstance(section, Exception) or not section:
            print(f"[NOTES] section for {topic!r} failed: {section}")
            continue
        resolved, used = resolve_figures(section, cat)
        # Keys are per-section; re-key so two sections cannot collide.
        for token, fig in used.items():
            new_token = f"s{len(kept_topics)}{token}"
            resolved = resolved.replace(f"[[IMG:{token}]]", f"[[IMG:{new_token}]]")
            all_figures[new_token] = fig
        kept_topics.append(topic)
        body_parts.append(resolved)

    if not body_parts:
        raise RuntimeError("The notes model returned nothing for any topic.")

    sources: List[Source] = []
    seen = set()
    for srcs in retrieved:
        for s in srcs:
            key = (s.chapter, s.page)
            if key not in seen:
                seen.add(key)
                sources.append(s)

    headings = [display_title(t) for t in kept_topics]
    title = plan["title"] or " · ".join(headings[:2])
    subtitle = " · ".join(headings) if headings else subject_area
    return NotesDocument(
        title=title,
        subtitle=subtitle,
        markdown="\n\n".join(body_parts),
        figures=all_figures,
        sources=sources,
        topics=kept_topics,
        subject_area=subject_area,
        student_class=student_class,
        grounded=bool(sources),
    )
