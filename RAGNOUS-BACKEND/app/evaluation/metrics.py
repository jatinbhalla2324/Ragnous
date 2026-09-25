"""Retrieval metrics for the RAG pipeline.

Deliberately dependency-free and pure so they can be unit-tested and reused by
both app/evaluation/eval_runner.py and any ad-hoc analysis.

Relevance here is judged at *chapter* granularity, not chunk. NCERT chapters
are the unit a student and a citation both care about, and chunk boundaries
are an artefact of the 1000/200 splitter — insisting on an exact chunk id
would measure the splitter rather than the retriever.
"""

from dataclasses import dataclass, field
from statistics import mean
from typing import Dict, List, Optional, Sequence


def recall_at_k(retrieved: Sequence[str], relevant: str, k: int) -> bool:
    """Did the gold chapter appear anywhere in the top k?"""
    return relevant in list(retrieved)[:k]


def reciprocal_rank(retrieved: Sequence[str], relevant: str) -> float:
    """1/rank of the first correct chapter, 0.0 if it never appears."""
    for i, chapter in enumerate(retrieved, start=1):
        if chapter == relevant:
            return 1.0 / i
    return 0.0


def mrr(all_retrieved: Sequence[Sequence[str]], all_relevant: Sequence[str]) -> float:
    if not all_retrieved:
        return 0.0
    return mean(
        reciprocal_rank(r, g) for r, g in zip(all_retrieved, all_relevant)
    )


def confidence_tier(score: float, kind: str, thresholds: Dict[str, float]) -> str:
    """Mirror of _confidence_tier in app/api/v1/chat.py, taking its thresholds
    explicitly so the eval can sweep them without importing app state."""
    if score is None:
        return "low"
    high = thresholds["rerank_high" if kind == "rerank" else "cosine_high"]
    medium = thresholds["rerank_medium" if kind == "rerank" else "cosine_medium"]
    if score >= high:
        return "high"
    if score >= medium:
        return "medium"
    return "low"


@dataclass
class QueryResult:
    """One evaluated query."""
    query: str
    gold_chapter: Optional[str]      # None => the corpus should NOT answer this
    retrieved_chapters: List[str]
    top_score: float
    score_kind: str                  # "rerank" | "cosine"
    tier: str
    latency_ms: float = 0.0

    @property
    def is_negative(self) -> bool:
        """A query the corpus genuinely cannot answer. The right behaviour is a
        `low` tier, no citation, and a hand-off to web search."""
        return self.gold_chapter is None

    @property
    def hit_at_1(self) -> bool:
        return (not self.is_negative) and recall_at_k(self.retrieved_chapters, self.gold_chapter, 1)

    @property
    def hit_at_3(self) -> bool:
        return (not self.is_negative) and recall_at_k(self.retrieved_chapters, self.gold_chapter, 3)

    @property
    def hit_at_10(self) -> bool:
        return (not self.is_negative) and recall_at_k(self.retrieved_chapters, self.gold_chapter, 10)

    @property
    def false_confidence(self) -> bool:
        """The dangerous failure: an unanswerable query presented to the
        student as textbook-grounded. Measured on the Class 10 Science corpus,
        raw cosine did exactly this — "explain quantum field theory
        renormalization" scored 0.560, clearing COSINE_HIGH, while the
        cross-encoder scored the same pair 0.0001."""
        return self.is_negative and self.tier != "low"


@dataclass
class EvalReport:
    results: List[QueryResult] = field(default_factory=list)

    @property
    def positives(self) -> List[QueryResult]:
        return [r for r in self.results if not r.is_negative]

    @property
    def negatives(self) -> List[QueryResult]:
        return [r for r in self.results if r.is_negative]

    def recall(self, k: int) -> float:
        pos = self.positives
        if not pos:
            return 0.0
        attr = {1: "hit_at_1", 3: "hit_at_3", 10: "hit_at_10"}[k]
        return sum(getattr(r, attr) for r in pos) / len(pos)

    @property
    def mrr(self) -> float:
        pos = self.positives
        return mrr([r.retrieved_chapters for r in pos], [r.gold_chapter for r in pos])

    @property
    def false_confidence_rate(self) -> float:
        neg = self.negatives
        if not neg:
            return 0.0
        return sum(r.false_confidence for r in neg) / len(neg)

    @property
    def tier_counts(self) -> Dict[str, int]:
        counts = {"high": 0, "medium": 0, "low": 0}
        for r in self.results:
            counts[r.tier] = counts.get(r.tier, 0) + 1
        return counts

    @property
    def p50_latency_ms(self) -> float:
        lat = sorted(r.latency_ms for r in self.results)
        return lat[len(lat) // 2] if lat else 0.0

    def summary(self) -> str:
        pos, neg = self.positives, self.negatives
        lines = [
            "─" * 68,
            f"queries: {len(self.results)}  ({len(pos)} answerable, {len(neg)} out-of-corpus)",
            "",
            f"  Recall@1              {self.recall(1):>7.1%}   ({sum(r.hit_at_1 for r in pos)}/{len(pos)})",
            f"  Recall@3              {self.recall(3):>7.1%}   ({sum(r.hit_at_3 for r in pos)}/{len(pos)})",
            f"  Recall@10             {self.recall(10):>7.1%}   ({sum(r.hit_at_10 for r in pos)}/{len(pos)})",
            f"  MRR                   {self.mrr:>7.3f}",
            "",
            f"  false-confidence rate {self.false_confidence_rate:>7.1%}   "
            f"({sum(r.false_confidence for r in neg)}/{len(neg)} out-of-corpus queries "
            f"shown as textbook-grounded)",
            f"  tier distribution     {self.tier_counts}",
            f"  p50 latency           {self.p50_latency_ms:>7.0f} ms",
            "─" * 68,
        ]
        return "\n".join(lines)
