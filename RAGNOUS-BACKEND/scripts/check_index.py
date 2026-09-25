"""Read-only health check for the vector index.

Answers the question "is what I already uploaded usable, or do I need to
re-ingest?" without changing anything. Run this BEFORE
app/db/migrations/001_align_embedding_dim.sql — that migration truncates the
table, and if the index turns out to be healthy you do not need it.

    python scripts/check_index.py
"""

import asyncio
import os
import sys
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.embedding_service import (  # noqa: E402
    EMBEDDING_DIM,
    EMBEDDING_MODEL_NAME,
)
from app.services.search_service import IVFFLAT_PROBES  # noqa: E402

load_dotenv(override=True)

# Chapters the app filters out at query time anyway (answer keys).
_EXCLUDED_SUFFIX = "ps"


async def main() -> int:
    db_url = os.getenv("DATABASE_URL", "").replace("+asyncpg", "")
    if not db_url:
        print("❌ DATABASE_URL is not set in .env")
        return 1

    try:
        conn = await asyncpg.connect(db_url, timeout=30)
    except Exception as e:
        print(f"❌ cannot reach the database: {e}\n")
        print("   While this is true retrieval does nothing at all: chat.py catches")
        print("   the failure, logs a [WARN], and answers from the model's own")
        print("   knowledge with no citations. Fix connectivity first — nothing")
        print("   below can be assessed until then.")
        return 1

    problems, warnings = [], []
    try:
        print("=" * 70)
        print(f"query-side model : {EMBEDDING_MODEL_NAME} ({EMBEDDING_DIM}-d)")
        print("=" * 70)

        # ── Declared column type ──
        declared = await conn.fetchval("""
            SELECT format_type(a.atttypid, a.atttypmod)
            FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
            WHERE c.relname = 'ncert_chunks' AND a.attname = 'embedding'
        """)
        print(f"\ncolumn type      : {declared}")
        if declared and declared != f"vector({EMBEDDING_DIM})":
            problems.append(
                f"embedding column is {declared}, but the query model produces "
                f"{EMBEDDING_DIM}-d vectors — no query can ever match."
            )

        total = await conn.fetchval("SELECT count(*) FROM ncert_chunks")
        null_emb = await conn.fetchval(
            "SELECT count(*) FROM ncert_chunks WHERE embedding IS NULL")
        print(f"rows             : {total} ({null_emb} with no embedding)")
        if not total:
            problems.append("ncert_chunks is empty — nothing was ever stored.")
        if null_emb:
            warnings.append(f"{null_emb} rows have a NULL embedding and can never be retrieved.")

        # ── Dimensions actually stored ──
        if total:
            dims = await conn.fetch(
                "SELECT vector_dims(embedding) AS d, count(*) AS n FROM ncert_chunks "
                "WHERE embedding IS NOT NULL GROUP BY 1 ORDER BY 2 DESC")
            print("stored dims      : " +
                  ", ".join(f"{r['d']}-d x{r['n']}" for r in dims) if dims else "none")
            if len(dims) > 1:
                problems.append(
                    "more than one embedding dimension is present — two different "
                    "models wrote to the same column. Only one set is usable.")
            elif dims and dims[0]["d"] != EMBEDDING_DIM:
                problems.append(
                    f"stored vectors are {dims[0]['d']}-d, query vectors are "
                    f"{EMBEDDING_DIM}-d. Retrieval returns nothing.")

        # ── Indexes ──
        idx = await conn.fetch(
            "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'ncert_chunks'")
        print("\nindexes:")
        for r in idx:
            print(f"  - {r['indexname']}")
        defs = " ".join(r["indexdef"].lower() for r in idx)
        if "hnsw" not in defs and "ivfflat" not in defs:
            warnings.append("no ANN index on `embedding` — every query is a full scan.")
        elif "ivfflat" in defs:
            warnings.append(
                f"IVFFlat index. The query path now sets ivfflat.probes="
                f"{IVFFLAT_PROBES} per query, which recovers the recall the "
                f"default of 1 loses — measured, a Class 10 question whose "
                f"chapter was absent from the top 10 at probes=1 took the top "
                f"three slots at probes=10. HNSW removes the knob entirely: see "
                f"app/db/migrations/002_optional_cleanup.sql, section B.")
        if "tsv" not in defs:
            warnings.append("no GIN index on `tsv` — the keyword half of the hybrid "
                            "search will be slow or unusable.")

        # ── Corpus shape ──
        if total:
            print("\ncorpus:")
            rows = await conn.fetch(
                "SELECT subject, count(*) AS c, count(DISTINCT chapter) AS ch "
                "FROM ncert_chunks GROUP BY 1 ORDER BY c DESC")
            for r in rows:
                print(f"  {r['c']:>7} chunks  {r['ch']:>3} chapters  {r['subject']!r}")

            dupes = await conn.fetchval(
                "SELECT COALESCE(sum(n - 1), 0) FROM (SELECT count(*) AS n FROM "
                "ncert_chunks GROUP BY content HAVING count(*) > 1) d")
            print(f"\nduplicate chunks : {dupes}")
            if dupes:
                warnings.append(
                    f"{dupes} chunks are redundant copies. The query path dedups at "
                    f"runtime, but duplicates crowd out distinct passages before the "
                    f"dedup ever sees them, so real recall is lower than it looks.")

            # Chapters that produced suspiciously little text usually failed to
            # parse rather than genuinely being short.
            thin = await conn.fetch(
                "SELECT subject, chapter, count(*) AS c FROM ncert_chunks "
                "GROUP BY 1, 2 HAVING count(*) <= 2 ORDER BY 3")
            thin = [r for r in thin if not r["chapter"].endswith(_EXCLUDED_SUFFIX)]
            if thin:
                print("\nchapters with <= 2 chunks (likely a failed PDF parse):")
                for r in thin:
                    print(f"  {r['c']:>3}  {r['subject']!r} / {r['chapter']!r}")
                warnings.append(f"{len(thin)} chapter(s) stored almost no text.")
    finally:
        await conn.close()

    print("\n" + "=" * 70)
    if problems:
        print("❌ THE INDEX IS NOT USABLE AS-IS\n")
        for p in problems:
            print(f"   - {p}")
        print("\n   Re-ingest is required:")
        print("     psql \"$DATABASE_URL\" -f app/db/migrations/001_align_embedding_dim.sql")
        print("     python scripts/bulk_ingest.py")
        return 1

    if warnings:
        print("⚠️  USABLE, WITH PROBLEMS\n")
        for w in warnings:
            print(f"   - {w}")
        print("\n   None of these stop retrieval working — verify actual quality with:")
        print("     python -m app.evaluation.eval_runner")
        print("   Optional cleanups: app/db/migrations/002_optional_cleanup.sql")
        return 0

    print("✅ index looks healthy. Verify retrieval quality with:")
    print("     python -m app.evaluation.eval_runner")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
