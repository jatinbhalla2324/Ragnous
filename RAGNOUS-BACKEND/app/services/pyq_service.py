"""Previous-year exam question lookup for a topic.

When a student asks about a topic for the first time, this searches the public
web (Tavily) for board-exam papers and question banks covering it, and pulls
the questions out of those pages. The student sees how often the topic has come
up and what form the questions took.

Extraction is deliberately NOT done by an LLM. Two reasons, both learned the
hard way:

  * Cost and limits. This runs on every new topic, alongside the answer, on the
    same Groq account — a 3-4k-token extraction call per question tripped the
    tokens-per-minute ceiling and took the student's actual answer down with it.
    Parsing costs nothing and cannot rate-limit.
  * Honesty. Asked to "extract" questions, a model writes fluent paraphrases and
    remembers questions from neighbouring chapters. Everything here is a literal
    substring of a fetched page, so a question shown to a student is a question
    that was really printed somewhere, and the frequency figure is COUNTED from
    the years printed beside them rather than asserted by anything.

A thin result is dropped rather than shown: students plan revision around these
numbers, and a card built from one vague sentence is worse than no card.

Class-awareness note: CBSE only sits board exams in Class 10 and Class 12. For
Classes 8, 9 and 11 there is no such thing as a "previous year board paper",
and the previous version of this file silently blurred that line — the query
still asked for "board exam questions", the extractor still picked up any year
tag it could see, and a Class 8 student was shown Class 10 questions labelled
as if they had been on their own exams. This version separates the two: real
board papers for 10 and 12, sample/practice papers for the others, with a
`paperType` field the UI uses to word the card honestly.
"""

import re
from datetime import datetime
from typing import Dict, List, Optional, Set

# CBSE board exams exist only for these classes; everything else is
# school-level assessment / sample paper material and must be labelled as
# such rather than as "board previous years".
_BOARD_CLASSES = {10, 12}

_OLDEST_YEAR = 2010
_MIN_QUESTIONS = 2
_MAX_QUESTIONS = 8

# Widened from the old 25-220. Real 1-mark board MCQs run as short as
# "Define osmosis." (15 chars) and long-answer / case-study questions run
# past 220, so the previous window silently dropped both ends of what a
# real paper actually looks like and kept the mid-length prose typical of
# question-bank blogs — which is exactly the material LEAST likely to have
# actually been on a paper.
_MIN_LEN = 15
_MAX_LEN = 350

_BOARD_SEARCH_TEMPLATE = (
    "{topic} class {class_no} {subject} previous year board exam questions "
    "CBSE question paper with year"
)

# For classes without board exams, ask Tavily for the material that actually
# exists — sample papers and school-level assessment questions — so the
# returned pages are the ones the student's teacher would set from.
_SAMPLE_SEARCH_TEMPLATE = (
    "{topic} class {class_no} {subject} CBSE sample paper questions "
    "important questions NCERT chapter test"
)

# Page furniture that sits in the same text as the questions.
_JUNK = re.compile(
    r"(?:https?://|www\.|©|cookie|subscribe|download (?:the )?(?:pdf|app)|"
    r"click here|sign ?up|log ?in|advertisement|all rights reserved|"
    r"read more|share this|whatsapp|telegram|comment)",
    re.IGNORECASE,
)

# Command words that open an exam question even without a question mark
# ("Explain any three effects…"). Interrogative pronouns are deliberately NOT
# here: "When they heard of the Movement, thousands of workers…" is a sentence
# out of a passage, not a question, and only the '?' can tell the difference.
_INTERROGATIVE = re.compile(
    r"^(?:name|define|state|list|explain|describe|discuss|write|give|mention|"
    r"identify|distinguish|differentiate|compare|derive|prove|show|calculate|"
    r"find|evaluate|examine|analyse|analyze|justify|assess|elaborate|"
    r"illustrate|trace|suggest|choose|fill|complete|match|assertion)\b",
    re.IGNORECASE,
)

# Long-answer verbs vs one-mark forms, used only when the paper did not print
# the marks.
_LONG_FORM = re.compile(
    r"^(?:explain|describe|discuss|elaborate|examine|evaluate|analyse|analyze|"
    r"justify|assess|critically|illustrate|trace)\b",
    re.IGNORECASE,
)
_OBJECTIVE_FORM = re.compile(
    r"^(?:name|define|state|choose|fill|match|complete|assertion|true or false|"
    r"write the full form|who|when|which one)\b",
    re.IGNORECASE,
)

# Numbering at the head of a question: "Q.3", "12.", "(iv)", "(a)".
_NUMBERING = re.compile(
    r"^(?:q(?:uestion)?\s*[.:\-]?\s*\d+\s*[.):\-]?|\d+\s*[.)]|\([ivxlcdm]+\)|\([a-z]\))\s*",
    re.IGNORECASE,
)

_BOARD_TAG = r"(?:cbse|delhi|od|ai|d|c|all india|outside delhi|foreign|board|compartment|comptt|set\s*[i1-3]+)"

# A year tag is only trusted when the paper actually MARKED it as one:
# either bracketed at the tail ("(CBSE 2020)", "[2019]", "(2013 OD)"),
# or written next to a board qualifier ("Delhi 2018", "2020 CBSE").
# The old pattern accepted a bare four-digit year at the end of the line,
# which happily grabbed years mentioned inside the question itself
# ("Which policy did the government adopt in 2020?") and treated them as
# paper years — inflating both the year list and the confidence of the card.
_YEAR_TAG = re.compile(
    r"(?:"
    rf"[\(\[]\s*(?:{_BOARD_TAG}[\s,]*)*((?:19|20)\d{{2}})(?:\s*[-–]\s*\d{{2,4}})?(?:[\s,]*{_BOARD_TAG})*\s*[\)\]]"
    r"|"
    rf"(?:^|[\s\-–,])(?:{_BOARD_TAG})[\s,]+((?:19|20)\d{{2}})(?:\s*[-–]\s*\d{{2,4}})?"
    r"|"
    rf"(?:^|[\s\-–,])((?:19|20)\d{{2}})[\s,]+(?:{_BOARD_TAG})"
    r")\s*$",
    re.IGNORECASE,
)

# "(3 marks)", "[5]", "3M" printed with a question.
_MARKS_TAG = re.compile(
    r"[\(\[\s]\s*(\d{1,2})\s*(?:marks?|m)\s*[\)\]]?\s*$", re.IGNORECASE
)

# Words that appear across half the syllabus and identify nothing on their own.
_GENERIC_TOPIC_WORDS = {
    "movement", "system", "process", "theory", "chapter", "class", "india",
    "indian", "world", "life", "science", "history", "national", "effects",
    "structure", "function", "types", "concept", "study", "modern", "human",
    "important", "questions", "answers", "exam", "board",
}


def _norm_year(value) -> Optional[int]:
    try:
        year = int(value)
    except (TypeError, ValueError):
        return None
    return year if _OLDEST_YEAR <= year <= datetime.utcnow().year else None


def _words(text: str) -> List[str]:
    return re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower()).split()


def _topic_signature(topic: str) -> Set[str]:
    """Words that identify the topic, for filtering questions.

    Short but distinctive tokens ("dna", "atp", "rna", "ohm", "ph") were
    thrown away by the old ``len(w) > 3`` cutoff, and the guard downstream
    then degenerated to accepting every question on the page — so a student
    who asked about DNA was shown RNA and protein-synthesis questions too.
    This keeps the > 3 preference when it produces a usable signature and
    falls back to short content words when it does not, rather than to
    nothing at all.
    """
    all_tokens = {w for w in _words(topic) if len(w) >= 2}
    long_distinctive = {w for w in all_tokens if len(w) > 3} - _GENERIC_TOPIC_WORDS
    if long_distinctive:
        return long_distinctive
    non_generic = all_tokens - _GENERIC_TOPIC_WORDS
    return non_generic or all_tokens


def _split_candidates(text: str) -> List[str]:
    """Every line or sentence in a page that might be an exam question."""
    text = re.sub(r"[ \t]+", " ", text or "")
    candidates: List[str] = []

    for line in re.split(r"[\n\r]+", text):
        line = line.strip(" •-–—*|")
        if not line:
            continue
        # A single line often holds several numbered questions run together.
        parts = re.split(r"(?<=[?.])\s+(?=(?:Q\.?\s*\d+|\d+[.)]\s|[A-Z]))", line)
        for part in parts:
            part = part.strip()
            if part:
                candidates.append(part)

    return candidates


def _extract_year(question: str) -> tuple:
    """(year, question_without_tag). year is None if no *trusted* tag was
    printed on this question. The extractor no longer accepts a bare year at
    end-of-line — see the note on ``_YEAR_TAG``."""
    match = _YEAR_TAG.search(question)
    if not match:
        return None, question
    raw = match.group(1) or match.group(2) or match.group(3)
    year = _norm_year(raw)
    if not year:
        return None, question
    return year, question[: match.start()].strip()


def _extract_from_text(text: str, topic_words: Set[str], allow_year: bool) -> List[dict]:
    """Questions about ``topic_words`` that are literally printed in ``text``.

    ``allow_year`` is false for classes with no board exams — the year printed
    beside a question there would either be irrelevant (a Class 10 board tag
    on a page a Class 8 student's search returned) or actively misleading, so
    the year metadata is dropped rather than shown.
    """
    found = []

    for raw in _split_candidates(text):
        if _JUNK.search(raw):
            continue

        # Markdown headings and list marks come through in raw_content, and
        # they sit in FRONT of the numbering ("### (ii) How desert plants…").
        question = re.sub(r"^[#>*\-–—•\s]+", "", raw).strip()
        # Papers chain their labels: "(b). (i) How many bulbs…". Strip until
        # nothing more comes off, rather than one level.
        for _ in range(3):
            stripped = _NUMBERING.sub("", question).strip(" .)")
            if stripped == question:
                break
            question = stripped

        # Pull the marks tag first — it can sit outside the year tag.
        year = None
        marks = None

        marks_match = _MARKS_TAG.search(question)
        if marks_match:
            value = int(marks_match.group(1))
            if 1 <= value <= 10:
                marks = value
            question = question[: marks_match.start()].strip()

        if allow_year:
            year, question = _extract_year(question)

        # "(Board Term I 2016)" loses its year to the tag parser above and
        # leaves "(Board Term I" hanging off the end. Any unclosed bracket
        # fragment at the tail is paper metadata, not part of the question.
        if question.count("(") > question.count(")"):
            question = re.sub(r"\s*\([^()]{0,40}$", "", question)
        if question.count("[") > question.count("]"):
            question = re.sub(r"\s*\[[^\[\]]{0,40}$", "", question)

        question = question.strip(" -–—:;,.|")
        if not (_MIN_LEN <= len(question) <= _MAX_LEN):
            continue

        # It must read as a question, not as an answer or a heading.
        if not (question.endswith("?") or _INTERROGATIVE.match(question)):
            continue

        # And it must be about the topic the student asked about — pages on the
        # Non-Cooperation Movement also print Civil Disobedience questions.
        # ``topic_words`` is now always non-empty (see ``_topic_signature``),
        # so this guard fires for every extraction — the old bug where short
        # topic names bypassed it is gone.
        if not (topic_words & set(_words(question))):
            continue

        found.append({
            "text": question[:_MAX_LEN],
            "marks": marks,
            "year": year,
            "type": _classify(question, marks),
        })

    return found


def _classify(question: str, marks: Optional[int]) -> str:
    """Prefer the marks the paper printed; fall back to the command word."""
    if isinstance(marks, int):
        if marks <= 1:
            return "objective"
        if marks <= 3:
            return "short"
        return "long"
    if _LONG_FORM.match(question):
        return "long"
    if _OBJECTIVE_FORM.match(question):
        return "objective"
    return "short"


def _dedupe(questions: List[dict]) -> List[dict]:
    """Fold obvious reprints of the same question into one entry.

    The old check treated question A as a duplicate of B whenever one word-set
    was a subset of the other. That is fine for numbering variants but eats
    every short form of a question the moment a longer form appears: "Explain
    photosynthesis" is a subset of almost every longer photosynthesis question
    and would silently disappear. Jaccard overlap (≥ 0.7) matches genuine
    reprints without swallowing distinct questions on the same topic.
    """
    unique: List[dict] = []
    kept_keys: List[Set[str]] = []

    for question in questions:
        key = {w for w in _words(question["text"]) if len(w) > 3}
        if not key:
            continue

        is_dupe = False
        for other in kept_keys:
            if not other:
                continue
            inter = len(key & other)
            union = len(key | other)
            if union and inter / union >= 0.7:
                is_dupe = True
                break
        if is_dupe:
            continue

        kept_keys.append(key)
        unique.append(question)

    return unique


def _rank(question: dict) -> tuple:
    """Questions carrying a real year and real marks first — those are the ones
    that came off an actual paper rather than out of a practice list."""
    return (question["year"] is not None, question["marks"] is not None)


async def fetch_pyq(
    tavily_client,
    topic: str,
    subject_area: str = "",
    class_no: Optional[int] = None,
) -> Optional[Dict]:
    """Previous-year question summary for ``topic``, or None if there isn't one.

    Returns ``paperType`` alongside the questions so the UI can word the card
    accurately — "In the exams" for real board classes (10, 12) and "In
    sample papers" for the classes that never sit a board exam.
    """
    topic = (topic or "").strip()
    if not topic or not tavily_client:
        return None

    # Two important guards, both of which the old code got wrong:
    #   * class_no is REQUIRED. The old ``class_no or 10`` silently answered
    #     every unknown-class request with a Class 10 search, so any upstream
    #     parse bug pushed Class 10 board questions in front of Class 8/9/11
    #     students.
    #   * a class outside 8-12 is refused rather than searched.
    if class_no is None or not (8 <= class_no <= 12):
        print(f"[PYQ] refusing lookup: class {class_no!r} outside 8-12")
        return None

    is_board_class = class_no in _BOARD_CLASSES
    template = _BOARD_SEARCH_TEMPLATE if is_board_class else _SAMPLE_SEARCH_TEMPLATE
    query = template.format(
        topic=topic,
        class_no=class_no,
        subject=(subject_area or "").strip(),
    )
    # Collapse the double space when subject_area is blank.
    query = re.sub(r"\s+", " ", query).strip()

    try:
        search = await tavily_client.search(
            query=query,
            include_images=False,
            max_results=5,
            search_depth="advanced",
            # The snippet drops the "(CBSE 2020)" tags printed beside the
            # questions, and those tags are the whole point of the figure.
            include_raw_content=True,
        )
    except Exception as e:
        print(f"[PYQ] Tavily search failed: {e}")
        return None

    results = (search or {}).get("results") or []
    if not results:
        print(f"[PYQ] no web results for {topic!r}")
        return None

    topic_words = _topic_signature(topic)
    if not topic_words:
        # A topic with nothing left after stopword removal ("the class")
        # would silently accept every question on the page. Refuse.
        print(f"[PYQ] topic {topic!r} has no distinctive words; skipping")
        return None

    questions: List[dict] = []
    sources = []

    for result in results[:5]:
        text = (result.get("raw_content") or "").strip() or (result.get("content") or "").strip()
        if not text:
            continue

        hits = _extract_from_text(text[:20000], topic_words, allow_year=is_board_class)
        if hits:
            questions.extend(hits)
            sources.append({
                "title": (result.get("title") or "Question bank")[:90],
                "url": result.get("url") or "",
            })

    questions = _dedupe(questions)
    if len(questions) < _MIN_QUESTIONS:
        print(f"[PYQ] only {len(questions)} usable question(s) for {topic!r}; skipping")
        return None

    questions.sort(key=_rank, reverse=True)
    questions = questions[:_MAX_QUESTIONS]

    # Anti-hallucination gate for board classes.
    #
    # A card that says "In the exams" is a claim these questions came off a
    # real board paper. That claim is only credible when at least SOME of
    # the extracted questions carry a printed paper-year tag — otherwise
    # what we scraped was a question-bank blog, not board archives, and
    # showing it as board history is exactly the "fluent paraphrase"
    # failure the module docstring warns against. For sample-paper classes
    # we skip this gate: the card there is labelled "In sample papers"
    # and does not claim a year in the first place.
    if is_board_class:
        with_year = sum(1 for q in questions if q["year"] is not None)
        if with_year == 0:
            print(
                f"[PYQ] {topic!r}: {len(questions)} question(s) but 0 carry a "
                f"paper-year tag — refusing to present as board PYQs"
            )
            return None

    # Counted, never asserted: ``years`` holds only years printed beside a
    # question the student can read for themselves in the list below. Sample
    # classes contribute no years by construction (allow_year=False).
    years = sorted({q["year"] for q in questions if q["year"]}, reverse=True)
    by_type = {
        t: sum(1 for q in questions if q["type"] == t)
        for t in ("objective", "short", "long")
    }

    paper_type = "board" if is_board_class else "sample"
    print(
        f"[PYQ] {topic!r} (class {class_no}, {paper_type}): "
        f"{len(questions)} question(s) from {len(sources)} source(s), "
        f"years={years or 'none printed'}"
    )

    return {
        "topic": topic,
        "paperType": paper_type,
        "questionCount": len(questions),
        "years": years,
        "yearCount": len(years),
        "byType": by_type,
        "questions": questions,
        "sources": sources[:4],
    }
