"""Shared retrieval primitives: the hybrid query and the dedup pass.

These live here rather than inside app/api/v1/chat.py so that
app/evaluation/eval_runner.py can measure the *exact* query production runs.
An eval that reimplements the retrieval SQL drifts away from the real one and
stops being evidence of anything — which is how a 1024-vs-384 dimension
mismatch survived in the repo unnoticed.

Importing this module has no side effects: no model weights, no API clients,
no environment variables required.
"""

import os
import re
import time
from typing import Dict, List, Optional, Sequence

# ── IVFFlat probe count ───────────────────────────────────────────────────
# pgvector's IVFFlat scans `ivfflat.probes` clusters per query and defaults to
# 1, which is badly lossy. Measured against the live index, with probes at the
# default: "what are reflex actions and how do they work?" did not return the
# correct chapter anywhere in the top 10; at probes=10 that chapter took the
# top three slots. Nothing about the query changed — only how much of the index
# was looked at.
#
# This is a stopgap that needs no index rebuild. The real fix is the HNSW index
# in app/db/migrations/002_optional_cleanup.sql, which has no such knob; the
# setting is simply ignored once that is in place.
IVFFLAT_PROBES = int(os.getenv("RAG_IVFFLAT_PROBES", "10"))

# RRF arm weights. The keyword arm is an OR over query terms, so a query like
# "state Ohm's law" matches every Political Science chapter containing "state"
# or "law" — generic words that carry no topical signal here but rank as highly
# as "ohm" does. With both arms weighted equally those chapters crowded the
# fused top-20 and pushed the correct Physics chapter out entirely. The keyword
# arm still earns its place on exact terms the embedding misses, so it is
# down-weighted rather than removed.
RRF_VECTOR_WEIGHT = float(os.getenv("RAG_RRF_VECTOR_WEIGHT", "1.0"))
RRF_KEYWORD_WEIGHT = float(os.getenv("RAG_RRF_KEYWORD_WEIGHT", "0.4"))

# Reciprocal Rank Fusion. An earlier version summed cosine similarity (0-1) and
# ts_rank_cd (typically 0.0x) with fixed 0.7/0.3 weights, so the keyword term
# was numerically negligible even when it did match. RRF combines the two
# *rankings* instead of their raw scores, which needs no calibration between
# the scales.
#
# $1 = query vector (pgvector literal)   $2 = OR-joined tsquery
# $3 = subject allowlist (text[], NULL to search the whole corpus)
# $4 = vector arm RRF weight             $5 = keyword arm RRF weight
HYBRID_SQL = """
    WITH vec AS (
        SELECT id, subject, chapter, page_number, content,
               (1 - (embedding <=> $1::vector)) AS vector_score,
               row_number() OVER (ORDER BY embedding <=> $1::vector) AS rnk
        FROM ncert_chunks
        WHERE chapter NOT LIKE '%ps'
          AND content NOT ILIKE '%acknowledgements%'
          AND ($3::text[] IS NULL OR subject = ANY($3))
        ORDER BY embedding <=> $1::vector
        LIMIT 40
    ),
    kw AS (
        SELECT id, subject, chapter, page_number, content,
               (1 - (embedding <=> $1::vector)) AS vector_score,
               row_number() OVER (
                   ORDER BY ts_rank_cd(tsv, to_tsquery('english', $2)) DESC
               ) AS rnk
        FROM ncert_chunks
        WHERE $2 <> ''
          AND tsv @@ to_tsquery('english', $2)
          AND chapter NOT LIKE '%ps'
          AND content NOT ILIKE '%acknowledgements%'
          AND ($3::text[] IS NULL OR subject = ANY($3))
        ORDER BY ts_rank_cd(tsv, to_tsquery('english', $2)) DESC
        LIMIT 40
    )
    SELECT COALESCE(v.id, k.id)                     AS id,
           COALESCE(v.subject, k.subject)           AS subject,
           COALESCE(v.chapter, k.chapter)           AS chapter,
           COALESCE(v.page_number, k.page_number)   AS page_number,
           COALESCE(v.content, k.content)           AS content,
           COALESCE(v.vector_score, k.vector_score) AS vector_score,
           COALESCE($4::float / (60 + v.rnk), 0)
             + COALESCE($5::float / (60 + k.rnk), 0) AS fused_score
    FROM vec v
    FULL OUTER JOIN kw k ON v.id = k.id
    ORDER BY fused_score DESC
    LIMIT 20
"""

# One ingest script prefixes chunks with "[Book: … | Page: …]" and another did
# not, so the same passage can exist in both forms.
_BOOK_HEADER_RE = re.compile(r"^\s*\[Book:[^\]]*\]\s*")


def dedupe_chunks(rows: Sequence, limit: int = 10) -> List:
    """Collapse chunks whose text is the same passage.

    Every Class 10 book was ingested twice (once under its NCERT code, once
    under a typed-in name), so thousands of chunk texts exist in duplicate.
    Without this the top-k was routinely half-filled with the same text, which
    both wasted context and inflated apparent agreement between sources.

    Once the corpus has been re-ingested from a truncated table this should be
    a no-op — but it stays as a guard, because a duplicate ingest is silent at
    query time and only shows up as degraded answers.
    """
    seen, deduped = set(), []
    for r in rows:
        body = _BOOK_HEADER_RE.sub("", r["content"] or "")
        key = " ".join(body.split()).lower()[:400]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)
    return deduped[:limit]


# ── Class resolution ──────────────────────────────────────────────────────
# NCERT filename convention: first letter encodes the class.
_NCERT_CODE_CLASS = {
    "a": 1, "b": 2, "c": 3, "d": 4, "e": 5, "f": 6,
    "g": 7, "h": 8, "i": 9, "j": 10, "k": 11, "l": 12,
}
# e.g. jesc1dd -> j(class 10) e(English medium) sc(Science) 1
_NCERT_CODE_RE = re.compile(r"^([a-l])([ehu])([a-z]{2})\d")

SUPPORTED_CLASSES = (8, 9, 10, 11, 12)


def _class_from_subject(subject: str) -> Optional[int]:
    """Class number for a `subject` value under either naming scheme."""
    if not subject:
        return None
    s = subject.strip().lower()

    # "class 10 science", "class 10th india and contemporary world 2"
    m = re.search(r"\b(?:class|std|grade)\s*_?(\d{1,2})", s)
    if m and 1 <= int(m.group(1)) <= 12:
        return int(m.group(1))

    # "8th_science_english", "10 maths"
    m = re.match(r"^(\d{1,2})\s*(?:th|st|nd|rd)?[_\s-]", s)
    if m and 1 <= int(m.group(1)) <= 12:
        return int(m.group(1))

    # NCERT file code
    m = _NCERT_CODE_RE.match(s)
    if m:
        return _NCERT_CODE_CLASS.get(m.group(1))

    return None


def _parse_student_class(raw: str) -> Optional[int]:
    """'10th' / 'Class 10' / '10' -> 10."""
    if not raw:
        return None
    m = re.search(r"(\d{1,2})", str(raw))
    if not m:
        return None
    n = int(m.group(1))
    return n if 1 <= n <= 12 else None


# ── Medium (language of the book) ─────────────────────────────────────────
# Class 8 was ingested in three media: 674 English chunks against 770 Hindi and
# 952 Punjabi. So an English question about Class 8 retrieves a non-English book
# more often than not — and the Devanagari and Gurmukhi PDFs extract as mangled
# ligatures ("कोकर्कयाएँ आकयार और आमयाप"), which is worse than useless as context.
# Callers that need one medium ask for it explicitly.
_MEDIUM_WORDS = {
    "english": "en", "hindi": "hi", "punjabi": "pa", "urdu": "ur",
    "marathi": "mr", "bengali": "bn",
}
# Second letter of an NCERT file code: jesc101 -> e -> English medium.
_CODE_MEDIUM = {"e": "en", "h": "hi", "u": "ur", "p": "pa"}


def medium_of(name: str) -> str:
    """Language a book or chapter code is written in. Defaults to English.

    Works on both naming schemes in the corpus: the typed-in names
    ("8th_science_punjabi") and the NCERT file codes ("hhcu109" -> Hindi).
    """
    if not name:
        return "en"
    text = str(name).strip().lower()
    for word, code in _MEDIUM_WORDS.items():
        if word in text:
            return code
    m = re.match(r"^([a-l])([a-z])[a-z]", text)
    if m:
        return _CODE_MEDIUM.get(m.group(2), "en")
    return "en"


def only_medium(rows, medium: str):
    """Rows from books written in `medium` — for a search that ran unfiltered.

    The class filter carries the medium, but the widen-to-everything fallback
    passes no subjects at all, so without this a Class 8 English question that
    misses its own books lands straight back in the Hindi and Punjabi extracts.
    """
    return [r for r in rows if medium_of(r["subject"]) == medium]


# subject -> class, cached; the book set changes only on re-ingest.
_SUBJECT_CLASS_CACHE: Dict[str, Optional[int]] = {}
_SUBJECT_CACHE_AT: float = 0.0
_SUBJECT_CACHE_TTL = 900  # seconds


async def _subjects_for_class(conn, class_no: Optional[int],
                              medium: Optional[str] = None) -> List[str]:
    """Subject values belonging to `class_no` ([] means 'do not filter').

    Pass `medium` ("en") to keep only books written in that language; if the
    class has none in that medium, every book for the class is returned rather
    than nothing.
    """
    global _SUBJECT_CLASS_CACHE, _SUBJECT_CACHE_AT

    if class_no is None:
        return []

    now = time.time()
    if not _SUBJECT_CLASS_CACHE or (now - _SUBJECT_CACHE_AT) > _SUBJECT_CACHE_TTL:
        rows = await conn.fetch("SELECT DISTINCT subject FROM ncert_chunks")
        _SUBJECT_CLASS_CACHE = {
            r["subject"]: _class_from_subject(r["subject"]) for r in rows
        }
        _SUBJECT_CACHE_AT = now
        resolved = sum(1 for v in _SUBJECT_CLASS_CACHE.values() if v)
        print(
            f"[RAG] subject index rebuilt: {len(_SUBJECT_CLASS_CACHE)} books, "
            f"{resolved} resolved to a class"
        )

    subjects = [s for s, c in _SUBJECT_CLASS_CACHE.items() if c == class_no]
    if not medium:
        return subjects
    same_medium = [s for s in subjects if medium_of(s) == medium]
    if same_medium:
        return same_medium
    print(f"[RAG] class {class_no} has no {medium} books; using all {len(subjects)}")
    return subjects


async def hybrid_search(conn, vector_str: str, ts_query: str,
                        class_subjects: Optional[List[str]]):
    """Run the hybrid query with the right IVFFlat probe count.

    `SET LOCAL` needs a transaction, and it is scoped to that transaction, so
    it cannot leak into other queries on a pooled connection.
    """
    async with conn.transaction():
        try:
            await conn.execute(f"SET LOCAL ivfflat.probes = {IVFFLAT_PROBES}")
        except Exception:
            # Not an IVFFlat index (HNSW ignores this), or the setting is
            # unavailable. Neither is a reason to fail the search.
            pass
        return await conn.fetch(
            HYBRID_SQL, vector_str, ts_query, class_subjects,
            RRF_VECTOR_WEIGHT, RRF_KEYWORD_WEIGHT,
        )
