-- 001_align_embedding_dim.sql
--
-- Brings ncert_chunks.embedding in line with app/services/embedding_service.py.
--
-- WHY
--   app/db/schema.sql declared VECTOR(1024) ("matches Cohere multilingual-v3")
--   while the query path in app/api/v1/chat.py and two of the three ingest
--   scripts used paraphrase-multilingual-MiniLM-L12-v2, which is 384-d. Reader
--   and writer could never agree at those settings.
--
-- STATUS ON THE LIVE DATABASE (checked 2026-08-27)
--   The live column is ALREADY vector(384) and all 15,929 rows are 384-d, so
--   it had been altered by hand at some point and schema.sql was simply stale.
--   This migration is therefore a NO-OP there and is kept for fresh installs
--   and for anyone restoring from the old schema.sql.
--
--   Run scripts/check_index.py first — it prints the current column type and
--   the dimensions actually stored, and tells you whether this is needed.
--
-- SAFE TO RUN REPEATEDLY. It changes nothing when the column already matches,
-- and it never deletes data. If the dimension genuinely differs, the existing
-- vectors cannot be converted — only recomputed — so it refuses to guess and
-- tells you to re-ingest instead.

DO $$
DECLARE
    current_type text;
    row_count    bigint;
BEGIN
    SELECT format_type(a.atttypid, a.atttypmod) INTO current_type
    FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
    WHERE c.relname = 'ncert_chunks' AND a.attname = 'embedding';

    SELECT count(*) INTO row_count FROM ncert_chunks;

    IF current_type = 'vector(384)' THEN
        RAISE NOTICE 'embedding is already vector(384) — nothing to do.';
    ELSIF row_count = 0 THEN
        EXECUTE 'ALTER TABLE ncert_chunks ALTER COLUMN embedding TYPE VECTOR(384)';
        RAISE NOTICE 'table empty; altered % -> vector(384).', current_type;
    ELSE
        RAISE EXCEPTION
            'embedding is % with % existing rows. Those vectors were produced '
            'by a different model and cannot be converted to 384-d — they have '
            'to be recomputed from the source text. Back up, TRUNCATE '
            'ncert_chunks, re-run this migration, then run '
            'scripts/bulk_ingest.py.', current_type, row_count;
    END IF;
END $$;

-- Generated column; added only if the table predates it.
ALTER TABLE ncert_chunks
    ADD COLUMN IF NOT EXISTS tsv TSVECTOR
    GENERATED ALWAYS AS (to_tsvector('english', content)) STORED;

CREATE INDEX IF NOT EXISTS idx_ncert_tsv ON ncert_chunks USING GIN (tsv);

-- The hybrid query filters on subject before ranking (subject = ANY($3)).
CREATE INDEX IF NOT EXISTS idx_ncert_subject ON ncert_chunks (subject);
