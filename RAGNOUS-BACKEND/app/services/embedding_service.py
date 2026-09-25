"""Single source of truth for the retrieval embedding model.

The model name used to be hard-coded in four places — app/api/v1/chat.py and
the three scripts/*_ingest.py — and they did not agree. scripts/ingest.py
embedded with Cohere embed-multilingual-v3.0 (1024-d) while everything else,
including the query path, used paraphrase-multilingual-MiniLM-L12-v2 (384-d),
and all four wrote to or read from the same ncert_chunks.embedding column.
Only one of those can ever work: vectors written by one model are meaningless
to the other even when the dimensions happen to line up, and when they don't,
Postgres rejects the insert or the `<=>` comparison outright.

Import EMBEDDING_MODEL_NAME / EMBEDDING_DIM / get_embedding_model() from here
instead of naming a model directly, so writer and reader cannot drift apart
again. Changing the model means changing it here *and* running a migration
that alters the column dimension and re-embeds the corpus — the stored vectors
are not portable between models.
"""

from typing import List, Sequence

from sentence_transformers import SentenceTransformer

# Multilingual on purpose: the corpus and the students span English, Hindi and
# Punjabi. Measured on the Class 10 Science corpus (630 chunks, 13 chapters),
# this model retrieves the correct chapter at Recall@1 = 100% over a 12-query
# set, with a mean top-1 cosine of 0.691 — well clear of COSINE_HIGH.
EMBEDDING_MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"

# Must equal the VECTOR(n) declared for ncert_chunks.embedding in
# app/db/schema.sql. verify_embedding_dim() below asserts this at startup.
EMBEDDING_DIM = 384

_model: SentenceTransformer | None = None


def get_embedding_model() -> SentenceTransformer:
    """Process-wide singleton. Loading the weights costs seconds, so the API
    and the ingest scripts each pay it exactly once."""
    global _model
    if _model is None:
        print(f"Loading embedding model {EMBEDDING_MODEL_NAME} ({EMBEDDING_DIM}-d)...")
        _model = SentenceTransformer(EMBEDDING_MODEL_NAME)
    return _model


def embed_texts(texts: Sequence[str], batch_size: int = 64) -> List[List[float]]:
    """Embed documents for storage."""
    return get_embedding_model().encode(
        list(texts), batch_size=batch_size, show_progress_bar=False
    ).tolist()


def embed_query(text: str) -> List[float]:
    """Embed a single query for search."""
    return get_embedding_model().encode(text).tolist()


def to_pgvector(embedding: Sequence[float]) -> str:
    """pgvector literal, e.g. '[0.1,0.2,...]'."""
    return f"[{','.join(map(str, embedding))}]"


def verify_embedding_dim() -> int:
    """Fail loudly if the loaded model stops matching EMBEDDING_DIM.

    A silent mismatch here is expensive: every INSERT and every `<=>` against
    the column fails, and both call sites swallow the exception, so retrieval
    degrades to nothing without an error reaching the caller.
    """
    model = get_embedding_model()
    # Renamed in newer sentence-transformers; support both.
    getter = getattr(model, "get_embedding_dimension", None) or \
        model.get_sentence_embedding_dimension
    actual = getter()
    if actual != EMBEDDING_DIM:
        raise RuntimeError(
            f"{EMBEDDING_MODEL_NAME} produces {actual}-d vectors but "
            f"EMBEDDING_DIM is {EMBEDDING_DIM}. Update EMBEDDING_DIM, ALTER "
            f"ncert_chunks.embedding to VECTOR({actual}), and re-embed the "
            f"corpus — stored vectors are not portable between models."
        )
    return actual
