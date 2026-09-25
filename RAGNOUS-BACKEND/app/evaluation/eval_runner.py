"""End-to-end retrieval evaluation against the live index.

Runs a gold query set through the *same* hybrid query, dedup pass, reranker and
confidence tiering that app/api/v1/chat.py uses, and fails loudly if retrieval
quality drops below the configured gates.

    python -m app.evaluation.eval_runner
    python -m app.evaluation.eval_runner --no-rerank    # measure the fallback path
    python -m app.evaluation.eval_runner --verbose

Exits non-zero on regression, so it can gate a deploy or a re-ingest. Had this
existed, three of the defects it now guards against would have been caught the
day they landed: a table that answers no queries at all, a corpus missing a
chapter, and a confidence badge that certifies answers the corpus cannot
support.
"""

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

import asyncpg
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.evaluation.metrics import EvalReport, QueryResult, confidence_tier  # noqa: E402
from app.services.embedding_service import (  # noqa: E402
    EMBEDDING_DIM,
    embed_query,
    to_pgvector,
    verify_embedding_dim,
)
from app.services.search_service import (  # noqa: E402
    dedupe_chunks,
    hybrid_search,
    only_medium,
    _subjects_for_class,
)
from app.services import rerank_service  # noqa: E402

load_dotenv(override=True)


# ── Gold set ──────────────────────────────────────────────────────────────
# (query, gold chapter or None, student class)
#
# `None` marks an out-of-corpus query: the books genuinely cannot answer it,
# and the correct behaviour is tier "low", no citations, and a hand-off to web
# search. These are not padding — they are the half of the set that catches
# false confidence, which is the failure mode that actually misleads a student.
GOLD_SET: List[Tuple[str, Optional[str], int]] = [
    # ── Class 10 Science (jesc1*) ──
    ("What is a balanced chemical equation?",                        "jesc101", 10),
    ("What is the difference between an acid and a base?",           "jesc102", 10),
    ("How do metals react with acids?",                              "jesc103", 10),
    ("What are covalent bonds in carbon compounds?",                 "jesc104", 10),
    ("Explain the process of photosynthesis in plants",              "jesc105", 10),
    ("What is the role of the pancreas in human digestion?",         "jesc105", 10),
    ("What are reflex actions and how do they work?",                "jesc106", 10),
    ("How do organisms reproduce asexually?",                        "jesc107", 10),
    # Regression guard: jesc108 (Heredity) was absent from the index because
    # the pypdf decompression guard in bulk_ingest.py was a silent no-op.
    ("What are Mendel's laws of inheritance?",                       "jesc108", 10),
    ("Explain reflection of light by spherical mirrors",             "jesc109", 10),
    ("How does the human eye focus on objects at different distances?", "jesc110", 10),
    ("State Ohm's law",                                              "jesc111", 10),
    ("Explain the magnetic field around a current carrying conductor", "jesc112", 10),
    ("What is a food chain and a trophic level?",                    "jesc113", 10),
    # ── Out-of-corpus: must NOT be presented as textbook-grounded ──
    ("Explain quantum field theory renormalization",                 None,      10),
    ("Who won the 2024 cricket world cup?",                          None,      10),
    ("What is the capital of Brazil?",                               None,      10),
    ("How do I cook pasta?",                                         None,      10),
    ("Who is the current CEO of Tesla?",                             None,      10),
    ("What are the provisions of the GST Act 2017?",                 None,      10),
]

# Gates. The measured baseline against the live index is Recall@1 = 100% and
# MRR = 1.000, stable across repeated runs. These sit one miss below that (14
# answerable queries, so a single miss is 92.9%) — enough headroom that a
# reworded question does not trip the alarm, tight enough that a real
# regression does. They are an alarm, not a target to tune against.
GATES = {
    "recall@1": 0.85,
    "recall@3": 0.90,
    "mrr": 0.88,
    "max_false_confidence_rate": 0.0,   # never certify an unanswerable query
}

THRESHOLDS = {
    "rerank_high":   float(os.getenv("RAG_RERANK_HIGH", "0.45")),
    "rerank_medium": float(os.getenv("RAG_RERANK_MEDIUM", "0.12")),
    "cosine_high":   float(os.getenv("RAG_COSINE_HIGH", "0.55")),
    "cosine_medium": float(os.getenv("RAG_COSINE_MEDIUM", "0.35")),
}


def _build_or_tsquery(text: str, limit: int = 12) -> str:
    """Kept in step with app/api/v1/chat.py."""
    import re
    stop = {
        "what", "which", "when", "where", "who", "why", "how", "does", "did", "do",
        "is", "are", "was", "were", "the", "a", "an", "of", "for", "and", "or",
        "in", "on", "with", "to", "from", "by", "as", "at", "it", "its", "this",
        "that", "these", "those", "me", "my", "you", "your", "explain", "tell",
        "give", "show", "describe", "define", "please", "can", "will", "would",
        "about", "some", "more", "using", "into", "than", "then", "them",
        "their", "there", "here", "also", "any", "all", "get", "got", "make",
        "made", "want", "need", "know", "much", "many", "such", "very", "only",
    }
    seen, terms = set(), []
    for w in re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}", (text or "").lower()):
        w = w.strip("-")
        if w in stop or w in seen or len(w) < 3:
            continue
        seen.add(w)
        terms.append(w)
        if len(terms) >= limit:
            break
    return " | ".join(terms)


async def preflight(conn) -> None:
    """Fail fast on the conditions that make the whole eval meaningless."""
    total = await conn.fetchval("SELECT count(*) FROM ncert_chunks")
    if not total:
        raise SystemExit("❌ ncert_chunks is empty — run scripts/bulk_ingest.py first.")

    dims = await conn.fetch(
        "SELECT vector_dims(embedding) AS d, count(*) AS n FROM ncert_chunks "
        "WHERE embedding IS NOT NULL GROUP BY 1 ORDER BY 2 DESC"
    )
    if not dims:
        raise SystemExit("❌ every row has a NULL embedding — the ingest stored no vectors.")
    if len(dims) > 1:
        detail = ", ".join(f"{r['d']}-d x{r['n']}" for r in dims)
        raise SystemExit(
            f"❌ mixed embedding dimensions in one column ({detail}). More than one "
            f"model wrote to ncert_chunks; see app/db/migrations/001_align_embedding_dim.sql."
        )
    if dims[0]["d"] != EMBEDDING_DIM:
        raise SystemExit(
            f"❌ stored vectors are {dims[0]['d']}-d but the query model produces "
            f"{EMBEDDING_DIM}-d. Nothing can be retrieved. Run "
            f"app/db/migrations/001_align_embedding_dim.sql and re-ingest."
        )

    null_emb = await conn.fetchval(
        "SELECT count(*) FROM ncert_chunks WHERE embedding IS NULL"
    )
    print(f"✓ index: {total} chunks, {EMBEDDING_DIM}-d, {null_emb} without an embedding")

    dupes = await conn.fetchval(
        "SELECT count(*) FROM (SELECT content FROM ncert_chunks "
        "GROUP BY content HAVING count(*) > 1) d"
    )
    if dupes:
        print(f"⚠️  {dupes} chunk texts are stored more than once (duplicate ingest).")


async def run_one(conn, query: str, gold: Optional[str],
                  class_no: int, use_rerank: bool) -> QueryResult:
    t0 = time.perf_counter()

    vector_str = to_pgvector(embed_query(query))
    ts_query = _build_or_tsquery(query)

    # Apply the same class filter production applies. Without it the search
    # spans every book in the corpus — Class 8 and 9 science included — and
    # Class 10 questions get answered from a Class 8 chapter, which is a
    # property of the eval, not of the retriever.
    class_subjects = await _subjects_for_class(conn, class_no, "en")
    rows = await hybrid_search(conn, vector_str, ts_query, class_subjects or None)
    if not rows and class_subjects:
        rows = only_medium(await hybrid_search(conn, vector_str, ts_query, None), "en")
    rows = dedupe_chunks(rows, limit=10) if rows else []

    score_kind, top_score = "cosine", 0.0
    ordered = list(rows)

    if rows and use_rerank:
        reranked, backend = await asyncio.to_thread(
            rerank_service.rerank, query, [r["content"] for r in rows], len(rows)
        )
        if reranked:
            score_kind = "rerank"
            top_score = reranked[0][1]
            ordered = [rows[i] for i, _ in reranked]

    if score_kind == "cosine" and rows:
        ordered = sorted(rows, key=lambda r: r["vector_score"] or 0.0, reverse=True)
        top_score = ordered[0]["vector_score"] or 0.0

    tier = confidence_tier(top_score, score_kind, THRESHOLDS) if rows else "low"
    # Mirrors chat.py: a cosine-only score may not certify grounding at all.
    if score_kind != "rerank" and tier != "low":
        tier = "low"

    return QueryResult(
        query=query,
        gold_chapter=gold,
        retrieved_chapters=[r["chapter"] for r in ordered],
        top_score=float(top_score),
        score_kind=score_kind,
        tier=tier,
        latency_ms=(time.perf_counter() - t0) * 1000,
    )


async def main(use_rerank: bool, verbose: bool) -> int:
    db_url = os.getenv("DATABASE_URL", "").replace("+asyncpg", "")
    if not db_url:
        raise SystemExit("❌ DATABASE_URL is not set.")

    verify_embedding_dim()

    if use_rerank and not os.getenv("COHERE_API_KEY"):
        print("ℹ️  COHERE_API_KEY not set — rerank_service will use the local "
              "cross-encoder, which is what production would do too.")

    try:
        conn = await asyncpg.connect(db_url, timeout=30)
    except Exception as e:
        raise SystemExit(
            f"❌ cannot reach the vector database: {e}\n"
            f"   Retrieval is entirely non-functional while this is true, and "
            f"   chat.py swallows this error — every answer silently falls back "
            f"   to the model's own knowledge."
        )

    try:
        await preflight(conn)
        print(f"\nrunning {len(GOLD_SET)} queries "
              f"({'with' if use_rerank else 'without'} cross-encoder rerank)...\n")

        report = EvalReport()
        for query, gold, class_no in GOLD_SET:
            r = await run_one(conn, query, gold, class_no, use_rerank)
            report.results.append(r)
            if verbose or (not r.is_negative and not r.hit_at_1) or r.false_confidence:
                mark = "✗" if (r.false_confidence or (not r.is_negative and not r.hit_at_1)) else "·"
                got = r.retrieved_chapters[0] if r.retrieved_chapters else "—"
                print(f" {mark} {query[:52]:<54} want={gold or 'NONE':<8} got={got:<8} "
                      f"{r.top_score:.3f} ({r.score_kind}) tier={r.tier}")
    finally:
        await conn.close()

    print("\n" + report.summary())

    failures = []
    if report.recall(1) < GATES["recall@1"]:
        failures.append(f"Recall@1 {report.recall(1):.1%} < {GATES['recall@1']:.0%}")
    if report.recall(3) < GATES["recall@3"]:
        failures.append(f"Recall@3 {report.recall(3):.1%} < {GATES['recall@3']:.0%}")
    if report.mrr < GATES["mrr"]:
        failures.append(f"MRR {report.mrr:.3f} < {GATES['mrr']}")
    if report.false_confidence_rate > GATES["max_false_confidence_rate"]:
        failures.append(
            f"false-confidence rate {report.false_confidence_rate:.1%} > "
            f"{GATES['max_false_confidence_rate']:.0%} — out-of-corpus queries are "
            f"being shown to students as textbook-grounded"
        )

    if failures:
        print("\n❌ RETRIEVAL REGRESSION")
        for f in failures:
            print(f"   - {f}")
        return 1

    print("\n✅ retrieval quality within gates")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Evaluate RAG retrieval quality.")
    ap.add_argument("--no-rerank", action="store_true",
                    help="skip Cohere and measure the raw cosine fallback path")
    ap.add_argument("--verbose", action="store_true", help="print every query")
    args = ap.parse_args()
    sys.exit(asyncio.run(main(use_rerank=not args.no_rerank, verbose=args.verbose)))
