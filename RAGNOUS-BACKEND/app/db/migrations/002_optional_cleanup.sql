-- 002_optional_cleanup.sql
--
-- OPTIONAL. Neither step is required for retrieval to work — measured against
-- the live index as-is, Recall@1 is 92.9% and MRR 0.929 (see
-- `python -m app.evaluation.eval_runner`). Both steps raise the ceiling.
--
-- Run the two sections independently; each is idempotent.
-- ⚠️  Section A DELETES rows. Take a snapshot first.

-- ── A. Remove duplicate chunks ────────────────────────────────────────────
-- Every Class 10 book was ingested twice, once under its NCERT code
-- ('jesc1dd') and once under a typed-in name ('class 10 science'), leaving
-- 5,924 redundant chunks. app/services/search_service.py dedups at query time,
-- but only AFTER the SQL `LIMIT 20` — so duplicates crowd out distinct
-- passages before the dedup ever sees them, and real recall is lower than the
-- numbers suggest.
--
-- Uncomment to run. Keeps the lowest id of each identical content.
--
--   CREATE TABLE ncert_chunks_backup AS SELECT * FROM ncert_chunks;
--
--   DELETE FROM ncert_chunks a
--   USING ncert_chunks b
--   WHERE a.content = b.content
--     AND a.id > b.id;
--
--   -- Expect 0 rows afterwards:
--   SELECT content, count(*) FROM ncert_chunks
--   GROUP BY 1 HAVING count(*) > 1 LIMIT 5;
--
--   -- Once eval_runner still passes: DROP TABLE ncert_chunks_backup;

-- ── B. Replace the IVFFlat index with HNSW ────────────────────────────────
-- IVFFlat recall depends on `ivfflat.probes`, which defaults to 1 — a single
-- cluster scanned per query, silently missing true nearest neighbours. The app
-- never set it. Its centroids also come from whatever rows existed at build
-- time, so it degrades as the corpus grows. HNSW has neither problem and holds
-- up better under the post-filtering the hybrid query applies
-- (subject = ANY(...), chapter NOT LIKE '%ps').
--
-- Takes a few minutes on ~16k rows. Build the new index before dropping the
-- old one so queries stay served throughout.

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_ncert_embedding_hnsw
    ON ncert_chunks USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

DROP INDEX CONCURRENTLY IF EXISTS idx_ncert_embedding;
ALTER INDEX idx_ncert_embedding_hnsw RENAME TO idx_ncert_embedding;

ANALYZE ncert_chunks;
