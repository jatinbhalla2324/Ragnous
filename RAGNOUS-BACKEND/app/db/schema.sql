-- Core user & profile tables
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email TEXT UNIQUE NOT NULL,
    hashed_password TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'student',   -- student | parent | admin
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE semantic_profiles (
    user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    learning_style TEXT,
    language_preference TEXT DEFAULT 'hinglish',
    weak_subjects JSONB DEFAULT '[]',
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- NCERT knowledge base
CREATE TABLE ncert_chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    subject TEXT NOT NULL,
    chapter TEXT NOT NULL,
    page_number INT,
    content TEXT NOT NULL,
    -- 384 = paraphrase-multilingual-MiniLM-L12-v2, the model used by BOTH
    -- scripts/*_ingest.py and the query path in app/api/v1/chat.py. This was
    -- VECTOR(1024) (Cohere embed-multilingual-v3.0) while every writer and the
    -- reader used 384-dim MiniLM vectors, so the two could never agree: at
    -- 1024 every bulk_ingest INSERT fails and every `embedding <=> $1` query
    -- errors out. Changing the model means changing this number too.
    embedding VECTOR(384),
    tsv TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', content)) STORED
);
-- HNSW rather than IVFFlat. IVFFlat needs to be built *after* the data is
-- loaded (an index built on an empty table has useless centroids) and needs
-- `ivfflat.probes` raised at query time — the default of 1 scans a single
-- cluster and quietly misses true nearest neighbours. HNSW has neither
-- footgun and degrades far more gracefully under the post-filtering the
-- hybrid query does (subject = ANY(...), chapter NOT LIKE '%ps').
CREATE INDEX idx_ncert_embedding ON ncert_chunks
    USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
CREATE INDEX idx_ncert_tsv ON ncert_chunks USING GIN (tsv);
-- The hybrid query filters on `subject` before ranking; without this the
-- class-scoped search does a sequential scan of the whole corpus.
CREATE INDEX idx_ncert_subject ON ncert_chunks (subject);

-- Episodic memory (compressed session summaries)
CREATE TABLE episodic_memories (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    summary TEXT NOT NULL,
    embedding VECTOR(384),   -- same model as ncert_chunks; keep the two in step
    session_start TIMESTAMPTZ,
    session_end TIMESTAMPTZ
);

-- Concept dependency graph
CREATE TABLE concepts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    subject TEXT NOT NULL
);
CREATE TABLE concept_edges (
    parent_id UUID REFERENCES concepts(id),
    child_id UUID REFERENCES concepts(id),
    relation TEXT DEFAULT 'prerequisite',
    PRIMARY KEY (parent_id, child_id)
);

-- Weak topic tracking + revision scheduling
CREATE TABLE weak_topics (
    user_id UUID REFERENCES users(id),
    concept_id UUID REFERENCES concepts(id),
    confidence_score FLOAT DEFAULT 0.0,
    last_reviewed TIMESTAMPTZ,
    next_review TIMESTAMPTZ,
    PRIMARY KEY (user_id, concept_id)
);

-- PYQs and generated practice questions
CREATE TABLE pyqs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    subject TEXT, chapter TEXT, year INT,
    question TEXT, marks INT, rubric TEXT
);

-- Curated YouTube mapping
CREATE TABLE topic_videos (
    topic_id UUID REFERENCES concepts(id),
    youtube_url TEXT NOT NULL,
    title TEXT
);

-- Evaluation logs
CREATE TABLE eval_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    query TEXT, retrieval_precision FLOAT, retrieval_recall FLOAT,
    citation_accuracy FLOAT, hallucination_flag BOOLEAN,
    latency_ms INT, tokens_used INT, cost_usd NUMERIC,
    created_at TIMESTAMPTZ DEFAULT now()
);
