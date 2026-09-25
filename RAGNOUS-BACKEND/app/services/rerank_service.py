"""Cross-encoder reranking, with a local fallback.

WHY THIS EXISTS
    Retrieval used to fall back to raw bi-encoder cosine whenever Cohere was
    unconfigured or its call failed, and cosine cannot separate answerable
    questions from unanswerable ones on this corpus. Measured against Class 10
    Science:

        query                                    cosine   cross-encoder
        "explain quantum field theory renorm."    0.560          0.0002
        "state Ohm's law"                         0.367          0.9177

    The out-of-corpus question scored *higher* than a genuine hit. No threshold
    on cosine can split those, so on the fallback path a question the books
    could not answer was badged "NCERT Verified" while a real one was dropped.
    That is not a tuning problem — a bi-encoder measures topical closeness, not
    whether a passage answers a question, and only a cross-encoder sees the
    query and the passage together.

    So the fallback is now a *local* cross-encoder rather than cosine. It needs
    no API key and no network, which means the pipeline keeps its precision
    when Cohere is down — the case that previously produced the false badges.

SCALES
    Both backends return relevance in 0-1, so RERANK_HIGH / RERANK_MEDIUM apply
    to either. Cohere returns that directly; the local model emits a logit,
    which is squashed with a sigmoid. Raw cosine is a genuinely different scale
    and is reported as such by the caller, which then refuses to certify it.
"""

import math
import os
import time
from typing import List, Optional, Sequence, Tuple

# cross-encoder/ms-marco-MiniLM-L-6-v2: ~80MB, CPU-fast, trained for exactly
# this (query-passage relevance). English-only, unlike the multilingual
# bi-encoder — acceptable because it only reorders candidates the multilingual
# retriever already found, and never removes them.
LOCAL_RERANK_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"
COHERE_RERANK_MODEL = "rerank-english-v3.0"

_local_model = None
_cohere_client = None
_cohere_disabled = False        # hard-off: bad key
_cohere_cooldown_until = 0.0    # soft-off: rate limited

# A Cohere Trial key allows 10 rerank calls/minute — comfortably less than one
# per student turn at any real usage. Without a cooldown every request pays a
# failed round-trip before falling back, so back off for a while after a 429
# instead of rediscovering the limit on each turn.
_COHERE_COOLDOWN_SECONDS = 60.0


def _get_local_model():
    global _local_model
    if _local_model is None:
        from sentence_transformers import CrossEncoder
        print(f"Loading local cross-encoder {LOCAL_RERANK_MODEL}...")
        _local_model = CrossEncoder(LOCAL_RERANK_MODEL, max_length=512)
    return _local_model


def _get_cohere_client():
    global _cohere_client
    if _cohere_client is None and os.getenv("COHERE_API_KEY"):
        import cohere
        _cohere_client = cohere.ClientV2(os.getenv("COHERE_API_KEY"))
    return _cohere_client


def warmup() -> None:
    """Load the local model up front so the first real request doesn't pay for it."""
    _get_local_model().predict([("warmup query", "warmup passage")])


def rerank_local(query: str, documents: Sequence[str], top_n: int = 3
                 ) -> List[Tuple[int, float]]:
    """(original_index, relevance 0-1), best first."""
    if not documents:
        return []
    logits = _get_local_model().predict([(query, d) for d in documents])
    scored = [(i, 1.0 / (1.0 + math.exp(-float(x)))) for i, x in enumerate(logits)]
    scored.sort(key=lambda t: t[1], reverse=True)
    return scored[:top_n]


def rerank_cohere(query: str, documents: Sequence[str], top_n: int = 3
                  ) -> List[Tuple[int, float]]:
    client = _get_cohere_client()
    if client is None:
        raise RuntimeError("COHERE_API_KEY is not set")
    res = client.rerank(
        query=query, documents=list(documents),
        model=COHERE_RERANK_MODEL, top_n=min(top_n, len(documents)),
    )
    return [(r.index, r.relevance_score) for r in res.results]


def rerank(query: str, documents: Sequence[str], top_n: int = 3,
           prefer: Optional[str] = None) -> Tuple[List[Tuple[int, float]], str]:
    """Rerank, returning (results, backend).

    `backend` is "cohere", "local", or "none" — the caller uses it to decide
    whether the score may certify an answer as textbook-grounded. Both
    cross-encoder backends produce scores on the RERANK_* scale; "none" means
    reranking was impossible and the caller must fall back to cosine and refuse
    to certify.
    """
    global _cohere_disabled, _cohere_cooldown_until

    if not documents:
        return [], "none"

    order = ["cohere", "local"] if prefer != "local" else ["local", "cohere"]
    for backend in order:
        if backend == "cohere":
            if (_cohere_disabled
                    or time.monotonic() < _cohere_cooldown_until
                    or not os.getenv("COHERE_API_KEY")):
                continue
            try:
                return rerank_cohere(query, documents, top_n), "cohere"
            except Exception as e:
                # Cohere exceptions stringify to the full response including
                # every header, which buries the actual cause in the logs.
                detail = str(e)
                if "message" in detail:
                    detail = detail[detail.index("message"):][:200]
                else:
                    detail = detail[:200]

                # Read the status off the exception. Substring-matching the
                # stringified error is not safe: it embeds every response
                # header, and a random x-debug-trace-id containing "401" would
                # permanently disable Cohere for the whole process.
                status = getattr(e, "status_code", None)

                if status in (401, 403):
                    _cohere_disabled = True
                    print(f"[WARN] Cohere rejected the API key; using the local "
                          f"cross-encoder from now on. {detail}")
                elif status == 429:
                    _cohere_cooldown_until = time.monotonic() + _COHERE_COOLDOWN_SECONDS
                    print(f"[WARN] Cohere rate-limited; using the local "
                          f"cross-encoder for {_COHERE_COOLDOWN_SECONDS:.0f}s. {detail}")
                else:
                    print(f"[WARN] Cohere rerank failed; using the local "
                          f"cross-encoder. {detail}")
        else:
            try:
                return rerank_local(query, documents, top_n), "local"
            except Exception as e:
                print(f"[WARN] local cross-encoder failed: {e}")

    return [], "none"
