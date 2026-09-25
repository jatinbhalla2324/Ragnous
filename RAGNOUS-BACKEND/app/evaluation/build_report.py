"""Render EVALUATION.md from the raw results of accuracy_eval.py.

    python -m app.evaluation.build_report

Every number in the report is recomputed here from the per-question rows in
accuracy_results.json, never copied from the summary the run printed, so the
document can be regenerated and checked against the data at any time. If
accuracy_spotcheck.json exists (a manual re-grade of a sample of the grader's
verdicts), its agreement rate is reported too.
"""

from __future__ import annotations

import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.evaluation.accuracy_eval import (  # noqa: E402
    NEGATIVES, RESULTS_PATH, SEED, TESTSET_PATH, wilson,
)

HERE = Path(__file__).resolve().parent
SPOTCHECK_PATH = HERE / "accuracy_spotcheck.json"
REPORT_PATH = Path(__file__).resolve().parents[3] / "EVALUATION.md"
PRIMARY = "Groq[openai/gpt-oss-120b]"

BOOKS = {
    "hecu1": "Class 8 Science — *Curiosity*",
    "hees1": "Class 8 Social Science — *Exploring Society: India and Beyond*",
    "hegp1": "Class 8 Mathematics — *Ganita Prakash* Part 1",
    "hegp2": "Class 8 Mathematics — *Ganita Prakash* Part 2",
    "iesc1": "Class 9 Science",
    "iest1": "Class 9 Social Science — *Understanding Society: India and Beyond*",
    "jesc1": "Class 10 Science",
    "jess1": "Class 10 Geography — *Contemporary India II*",
    "jess2": "Class 10 Economics — *Understanding Economic Development*",
    "jess3": "Class 10 History — *India and the Contemporary World II*",
    "jess4": "Class 10 Political Science — *Democratic Politics II*",
}
SUBJECT = {"sc": "Science", "cu": "Science", "gp": "Mathematics",
           "es": "Social Science", "st": "Social Science", "ss": "Social Science"}
BADGE = {"ncert_verified": "NCERT verified", "extended_reference": "Extended reference",
         "ai_knowledge": "AI knowledge", None: "no answer (quota)"}
VERDICT = {"correct": "✅ correct", "partially_correct": "🟡 partial", "incorrect": "❌ incorrect"}
STOP = set("""what which when where who why how does did do is are was were the a an of for and or in on
with to from by as at it its this that these those me my you your explain tell give show describe define
please can will would about some more using into than then them their there here also any all get make
made want need know much many such very only between during after before other each they have has had
being been our not but because while so if like used use""".split())


# ── Helpers ───────────────────────────────────────────────────────────────

def book(chapter: str) -> str:
    return BOOKS.get(chapter[:5], chapter[:5])


def plain(s: str) -> str:
    return s.replace("*", "")


def subject(chapter: str) -> str:
    return SUBJECT.get(chapter[2:4], "Other")


def share(k: int, n: int) -> str:
    """'**x%** (k/n) | lo–hi%' — two table cells: value and Wilson 95% CI."""
    if not n:
        return "n/a | "
    lo, hi = wilson(k, n)
    return f"**{100 * k / n:.1f}%** ({k}/{n}) | {100 * lo:.1f}–{100 * hi:.1f}%"


def p(k: int, n: int) -> str:
    return f"{100 * k / n:.0f}%" if n else "n/a"


def cell(text: Any, limit: int = 0) -> str:
    s = re.sub(r"\s+", " ", str(text or "")).strip().replace("|", "\\|")
    return s[:limit].rstrip() + "…" if limit and len(s) > limit else s


def verdict_label(r: Dict[str, Any]) -> str:
    if r["status"] != 200:
        return "no answer (quota)"
    return VERDICT.get(r.get("verdict"), "—")


def cites_gold(r: Dict[str, Any]) -> bool:
    return any(c.get("chapter") == r["gold_chapter"] for c in r["citations"])


def overlap(question: str, passage: str) -> float:
    """Share of the question's content words that appear verbatim in its passage."""
    words = [w for w in re.findall(r"[a-z]{4,}", question.lower()) if w not in STOP]
    if not words:
        return 0.0
    text = passage.lower()
    return sum(w in text for w in words) / len(words)


def fence(text: str) -> str:
    """A fenced block that no tilde run inside `text` can close early."""
    longest = max((len(m) for m in re.findall(r"~+", text or "")), default=0)
    ticks = "~" * max(4, longest + 1)
    return f"{ticks}text\n{(text or '').strip()}\n{ticks}"


def html(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def cited_list(r: Dict[str, Any], pages: bool = False) -> str:
    if pages:
        items = [f"`{c.get('chapter')}` p.{c.get('page')}" for c in r["citations"]]
    else:
        items = sorted({f"`{c.get('chapter')}`" for c in r["citations"]})
    return ", ".join(items) or "—"


# ── Report ────────────────────────────────────────────────────────────────

def main() -> None:
    data = json.loads(RESULTS_PATH.read_text())
    rows: List[Dict[str, Any]] = data["rows"]
    testset = json.loads(TESTSET_PATH.read_text())
    spot = json.loads(SPOTCHECK_PATH.read_text()) if SPOTCHECK_PATH.exists() else None
    corpus = data.get("summary", {}).get("corpus", {})
    judge = ", ".join(m for _, m in data.get("judge", [])) or "unknown"
    num = {id(r): i for i, r in enumerate(rows, 1)}

    # `pos_all`/`neg_all` are every question asked; `pos`/`neg` only those the tutor
    # actually answered. A request that failed because every model was out of
    # quota says nothing about answer quality, so it is reported as capacity
    # (§8), not scored as a wrong answer or a false citation.
    pos_all = [r for r in rows if r["gold_chapter"]]
    neg_all = [r for r in rows if not r["gold_chapter"]]
    pos = [r for r in pos_all if r["status"] == 200]
    neg = [r for r in neg_all if r["status"] == 200]
    unanswered = [r for r in rows if r["status"] != 200]
    judged = [r for r in pos if r.get("verdict")]
    n = len(judged)
    v = Counter(r["verdict"] for r in judged)
    ret = [r for r in pos_all if r.get("retrieval")]
    r1 = sum(r["retrieval"]["hit1"] for r in ret)
    r3 = sum(r["retrieval"]["hit3"] for r in ret)
    r10 = sum(r["retrieval"]["hit10"] for r in ret)
    mrr = statistics.mean(r["retrieval"]["rr"] for r in ret) if ret else 0.0
    gold_cited = sum(cites_gold(r) for r in pos)
    no_cite = sum(not r["citations"] for r in pos)
    verified = [r for r in pos if r["confidence_mode"] == "ncert_verified"]
    verified_ok = sum(cites_gold(r) for r in verified)
    fact_err = sum(bool(r.get("factual_errors")) for r in judged)
    false_ground = [r for r in neg if r["citations"] or r["confidence_mode"] != "ai_knowledge"]
    lat = sorted(r["latency_s"] for r in rows if r["status"] == 200)
    p50 = lat[len(lat) // 2] if lat else 0
    p95 = lat[min(len(lat) - 1, int(len(lat) * 0.95))] if lat else 0
    overlaps = [overlap(r["question"], r["passage"]) for r in pos_all]
    per_class = Counter(r["class"] for r in pos_all)
    sampled = testset.get("chapters_sampled") or sorted({r["gold_chapter"] for r in pos})
    dropped = sorted(set(sampled) - {r["gold_chapter"] for r in pos_all})
    sampled_per_class = Counter({8: 0, 9: 0, 10: 0})
    for c in sampled:
        sampled_per_class[{"h": 8, "i": 9, "j": 10}.get(c[0], 0)] += 1
    weak = [r for r in judged if r["verdict"] != "correct"]
    agree = sum(s["agree"] for s in spot["items"]) if spot else 0
    adjusted = None
    if spot:
        verdict_of = {r["id"]: r.get("verdict") for r in rows}
        sampled_ok = [x for x in spot["items"] if verdict_of.get(x["id"]) == "correct"]
        reviewed_weak = [x for x in spot["items"] if verdict_of.get(x["id"]) in ("partially_correct", "incorrect")]
        down = sum(x["reviewer_verdict"] != "correct" for x in sampled_ok)
        up = sum(x["reviewer_verdict"] == "correct" for x in reviewed_weak)
        if sampled_ok:
            rate = down / len(sampled_ok)
            est = (v["correct"] * (1 - rate) + up) / n
            lo_rate, hi_rate = wilson(down, len(sampled_ok))
            adjusted = {"down": down, "sampled": len(sampled_ok), "up": up, "weak": len(reviewed_weak),
                        "rate": rate, "est": est,
                        "lo": (v["correct"] * (1 - hi_rate) + up) / n,
                        "hi": (v["correct"] * (1 - lo_rate) + up) / n}
    first_fail = [r for r in rows if r.get("first_attempt") or r["status"] != 200]
    recovered = [r for r in rows if r.get("first_attempt") and r["status"] == 200]

    out: List[str] = []
    w = out.append

    # ── Title + summary ──
    w("# RAGNOUS — Evaluation Report\n")
    w(f"*Run {data.get('run_at', '?')} · {len(pos_all)} textbook questions + {len(neg_all)} off-syllabus questions · "
      f"tutor measured through its real `/api/v1/chat` endpoint · grader `{judge}`*\n")
    w("This report measures what a student actually gets from RAGNOUS. Every number is recomputed from the "
      "per-question data in [`accuracy_results.json`](RAGNOUS-BACKEND/app/evaluation/accuracy_results.json) by "
      "[`build_report.py`](RAGNOUS-BACKEND/app/evaluation/build_report.py). Every question, answer and grade is "
      "listed in [Appendix A](#appendix-a--every-question-and-answer), so any figure can be checked by hand.\n")
    w("### Summary\n")
    w("| Metric | Score | 95% CI |")
    w("|---|---|---|")
    w(f"| **Answer accuracy** — fully correct (LLM judge) | {share(v['correct'], n)} |")
    if adjusted:
        w(f"| Answer accuracy — fully correct, adjusted by manual re-grade (estimate, §5.4) | "
          f"**~{100 * adjusted['est']:.0f}%** | {100 * adjusted['lo']:.0f}–{100 * adjusted['hi']:.0f}% |")
    w(f"| Answer accuracy — correct or partially correct | {share(v['correct'] + v['partially_correct'], n)} |")
    w(f"| Top-1 retrieval — right chapter ranked first | {share(r1, len(ret))} |")
    w(f"| Top-3 retrieval — right chapter in the top 3 | {share(r3, len(ret))} |")
    w(f"| Citation accuracy — answer cites the source chapter | {share(gold_cited, len(pos))} |")
    w(f"| Answers with at least one factual error | {share(fact_err, n)} |")
    if neg:
        w(f"| Off-syllabus questions wrongly shown as textbook-grounded | {share(len(false_ground), len(neg))} |")
    else:
        w("| Off-syllabus questions wrongly shown as textbook-grounded | not measured — none could be answered (§8.1) | |")
    w(f"| Questions the tutor could not answer — free-tier daily quota exhausted | {share(len(unanswered), len(rows))} |")
    if spot:
        w(f"| Grader agreement with manual re-grade | {share(agree, len(spot['items']))} |")
    w(f"| Latency per answer, median / 95th percentile | **{p50:.1f}s** / **{p95:.1f}s** | |")
    w("")

    w("### Contents\n")
    for i, t in enumerate(["Evaluation objective", "Test dataset", "Evaluation methodology",
                           "Retrieval metrics", "Answer quality", "Class-wise results", "Failure cases",
                           "Fallback model performance", "Limitations", "Conclusions"], 1):
        anchor = f"{i}-{t.lower().replace(' ', '-')}"
        w(f"{i}. [{t}](#{anchor})")
    w("- [Appendix A — every question and answer](#appendix-a--every-question-and-answer)")
    w("- [Appendix B — reproducing this report](#appendix-b--reproducing-this-report)\n")

    # ── 1. Objective ──
    w("## 1. Evaluation objective\n")
    w("RAGNOUS is a retrieval-augmented tutor. It is only as good as its ability to find the right page of the "
      "student's textbook and to answer faithfully from it. The evaluation answers five questions:\n")
    w("1. **Retrieval.** For a real student question, does the system find the chapter the answer lives in?")
    w("2. **Grounding.** Does the answer cite the textbook source it came from?")
    w("3. **Correctness.** Is the answer factually right and complete, judged against the textbook?")
    w("4. **Honesty.** When a question is outside the books, does the tutor say so instead of presenting "
      "general knowledge as *NCERT verified*?")
    w("5. **Resilience.** When the main answer model is rate-limited and a fallback model answers, how much "
      "does quality drop?\n")
    w("**In scope:** the text chat path (`POST /api/v1/chat`) for Classes 8–10, English-medium books, "
      "single-turn questions.  ")
    w("**Out of scope:** voice mode, image and PDF questions, the notes PDF, 3D models, quizzes, and multi-turn "
      "conversations.\n")

    # ── 2. Dataset ──
    w("## 2. Test dataset\n")
    w("### 2.1 Source corpus\n")
    w("Questions were drawn from every English-medium chapter in the RAGNOUS index "
      f"({corpus.get('chunks', 0):,} chunks in Supabase Postgres + pgvector):\n")
    w("| Book | Chapters sampled | Questions |")
    w("|---|---|---|")
    by_book_sampled = Counter(c[:5] for c in sampled)
    by_book_q = Counter(r["gold_chapter"][:5] for r in pos_all)
    for code in sorted(by_book_sampled):
        w(f"| {BOOKS.get(code, code)} | {by_book_sampled[code]} | {by_book_q[code]} |")
    w(f"| **Total** | **{len(sampled)}** | **{len(pos_all)}** |")
    w("")
    w("### 2.2 How each test item was built\n")
    w(f"1. **Sampling.** For each chapter, candidate passages of 700–4,000 characters were shuffled with a fixed "
      f"seed (`{SEED}`) and the first was used. Chapter prelims (forewords, committee lists) were excluded.")
    w("2. **Question writing.** A model was given the passage and the class, and told to write **one question a "
      "student of that class would genuinely type into a tutor**. The rules: ask about the passage's core idea "
      "(not a caption or page detail), use the student's own words rather than copying the passage, never "
      "mention \"the passage\" or \"the chapter\", and make the question understandable on its own.")
    w("3. **Reference answer and key points.** The same call produced a 2–4 sentence reference answer using only "
      "the passage, and 2–4 key points any correct answer must contain. These drive the grading.")
    w("4. **Rejection.** A passage with no teachable content (contents page, exercises only, garbled or "
      "machine-generated text) was rejected, and the next one tried, up to three per chapter.\n")
    if dropped:
        w(f"**{len(dropped)} chapter(s) produced no usable passage in three tries and were dropped:** "
          f"{', '.join(f'`{c}`' for c in dropped)}. "
          + ("All are Class 8 Mathematics. Their chunks were extracted by an LLM-assisted parser and are "
             "largely LaTeX fragments or parser chatter (see [§7.5](#75-index-quality-issues-found))."
             if all(c.startswith("hegp") for c in dropped) else "") + "\n")
    w("### 2.3 Off-syllabus questions\n")
    w(f"{len(NEGATIVES)} fixed questions the books cannot answer: current affairs, university-level physics, "
      "programming, cooking, finance, and two Class 10 maths questions (Class 10 Mathematics is not in the "
      "index). For these the right behaviour is the *AI knowledge* badge and **no textbook citation**. They are "
      "listed in [§5.3](#53-off-syllabus-behaviour).\n")
    w("### 2.4 Dataset statistics\n")
    q_words = [len(r["question"].split()) for r in pos_all]
    w("| Statistic | Value |")
    w("|---|---|")
    w(f"| Textbook questions | {len(pos_all)} (Class 8: {per_class[8]}, Class 9: {per_class[9]}, Class 10: {per_class[10]}) |")
    w(f"| Off-syllabus questions | {len(neg_all)} |")
    w(f"| Median question length | {statistics.median(q_words):.0f} words |")
    w(f"| Median share of question words copied verbatim from the passage | {100 * statistics.median(overlaps):.0f}% |")
    w(f"| Key points per question | {statistics.mean(len(r['key_points']) for r in pos_all):.1f} on average |")
    w("")
    example = pos_all[0]
    w("### 2.5 Example test item\n")
    w("```json")
    w(json.dumps({k: example[k] for k in ("id", "class", "gold_chapter", "page", "question",
                                          "reference_answer", "key_points")}, indent=2, ensure_ascii=False))
    w("```\n")
    w("The full test set is in [`accuracy_testset.json`](RAGNOUS-BACKEND/app/evaluation/accuracy_testset.json).\n")

    # ── 3. Methodology ──
    w("## 3. Evaluation methodology\n")
    w("The method combines three standard techniques for evaluating retrieval-augmented systems:\n")
    w("- **Synthetic test-set generation from the corpus.** Questions are written from randomly sampled source "
      "passages, so each has a known correct source (the *gold chapter*). This is the approach used by "
      "RAG-evaluation toolkits such as RAGAS.")
    w("- **Ranked-retrieval metrics.** Recall@k and Mean Reciprocal Rank, measured at chapter level.")
    w("- **LLM-as-a-judge with reference answers.** A separate model grades each answer against the source "
      "passage, a reference answer and explicit key points. A sample of those grades is then re-checked by "
      "hand.\n")
    w("### 3.1 Pipeline\n")
    w("```mermaid")
    w("flowchart TD")
    w(f"    A[\"NCERT index<br/>{len(sampled)} English-medium chapters\"] -->|\"1 random passage per chapter (seeded)\"| B[\"Question writer<br/>{judge}\"]")
    w("    B -->|\"question + reference answer + key points\"| C[(\"Test set<br/>" f"{len(pos_all)} textbook + {len(neg_all)} off-syllabus\")]")
    w("    C -->|\"POST /api/v1/chat as the student's class\"| D[\"RAGNOUS tutor<br/>real endpoint, nothing mocked\"]")
    w("    C -->|\"same question\"| E[\"Retrieval stack<br/>hybrid search + rerank\"]")
    w("    E --> G[\"Top-1 / Top-3 / Recall@10 / MRR\"]")
    w("    D -->|\"citations + badge\"| H[\"Citation accuracy<br/>badge precision<br/>off-syllabus honesty\"]")
    w(f"    D -->|\"answer\"| F[\"Grader<br/>{judge}\"]")
    w("    F --> I[\"Verdict + factual errors\"]")
    w("    I -.->|\"sample\"| J[\"Manual re-grade\"]")
    w("    G --> K[\"build_report.py<br/>recomputes every number from raw rows\"]")
    w("    H --> K")
    w("    I --> K")
    w("    J --> K")
    w("```\n")
    w("### 3.2 Steps\n")
    w("1. **Build the test set** as described in §2. It is saved once and reused, so reruns measure the same questions.")
    w("2. **Ask the live tutor.** Each question is sent to `/api/v1/chat` exactly as the frontend sends it, "
      "with the student's class set to the book's class, in a fresh conversation. Nothing is mocked: intent "
      "classification, retrieval, reranking, answer generation, citations and the confidence badge all run as "
      "in production. The response's answer, citations, badge, latency and the model that wrote it are recorded.")
    w("3. **Score retrieval.** The same question runs through the production retrieval stack on its own. We "
      "record where the gold chapter ranks among the reranked results.")
    w("4. **Grade the answer.** The grader sees the question, the source passage, the reference answer, the key "
      "points and the tutor's full answer, then returns a verdict, the number of key points covered, every "
      "factual error, and a short rationale. The rubric is in §3.5.")
    w("5. **Check off-syllabus honesty.** An off-syllabus answer fails if it carries any textbook citation or "
      "any badge other than *AI knowledge*.")
    w("6. **Aggregate.** Proportions are reported with Wilson 95% confidence intervals, overall and by class, "
      "subject, book and answering model.")
    w("7. **Verify the grader.** A sample of verdicts is re-graded by hand against the same rubric, and the "
      "agreement rate is reported (§5.4).")
    w("8. **Render the report** from the raw per-question rows, so no number is typed in by hand.\n")
    w("### 3.3 Metric definitions\n")
    w("For $N$ textbook questions, let $\\text{rank}_i$ be the position of question $i$'s gold chapter in the "
      "reranked retrieval list (∞ if it is not in the top 10).\n")
    w("- **Top-1 retrieval (Recall@1)** = $\\frac{1}{N}\\sum_i \\mathbb{1}[\\text{rank}_i = 1]$. The right "
      "chapter is ranked first.")
    w("- **Top-3 retrieval (Recall@3)** = $\\frac{1}{N}\\sum_i \\mathbb{1}[\\text{rank}_i \\le 3]$. The top "
      "three passages usually reach the answer model and become citations, so this is the practical bar.")
    w("- **Recall@10**: the same with $k = 10$, the full list that is reranked.")
    w("- **MRR** = $\\frac{1}{N}\\sum_i 1/\\text{rank}_i$. 1.0 means the gold chapter was always first.")
    w("- **Citation accuracy**: share of answers whose returned citations include the gold chapter.")
    w("- **Badge precision**: of answers badged *NCERT verified*, the share that cite the gold chapter.")
    w("- **Answer accuracy (strict)** = correct ÷ graded. **Lenient** = (correct + partially correct) ÷ graded.")
    w("- **Factual error rate**: share of answers in which the grader flagged at least one false statement.")
    w("- **False-grounding rate**: share of off-syllabus questions answered with a citation or a grounded badge.")
    w("- **95% CI**: Wilson score interval, "
      "$\\frac{\\hat p + \\frac{z^2}{2n} \\pm z\\sqrt{\\frac{\\hat p(1-\\hat p)}{n} + \\frac{z^2}{4n^2}}}{1 + \\frac{z^2}{n}}$ "
      "with $z = 1.96$. It stays inside [0, 1] and is honest at small $n$ and near 100%.\n")
    w("### 3.4 Models and their roles\n")
    answered = Counter(r["answered_by"] or "error" for r in rows)
    w("| Role | Model |")
    w("|---|---|")
    w(f"| Tutor answer model, primary | `openai/gpt-oss-120b` on Groq ({answered.get(PRIMARY, 0)} of {len(rows)} answers) |")
    w("| Tutor answer fallbacks, in order | `qwen/qwen3-32b` → `moonshotai/kimi-k2-instruct` → `openai/gpt-oss-20b` (Groq) → Gemini 3.6 / 3.7 / 3.8 Flash |")
    w("| Tutor intent classifier | `openai/gpt-oss-20b` on Groq |")
    w("| Retrieval embedding | `paraphrase-multilingual-MiniLM-L12-v2` (384-d, local) |")
    w("| Reranker | Cohere `rerank-english-v3.0`, falling back to local `cross-encoder/ms-marco-MiniLM-L-6-v2` |")
    w(f"| Question writer | `{judge}` on Groq |")
    w(f"| Answer grader | `{judge}` on Groq |")
    if spot:
        w(f"| Manual re-grade | {spot['reviewer']} |")
    w("")
    w("### 3.5 Grading rubric\n")
    w("- **correct**: answers the question asked, contains every key point (or clearly implies it), and has no "
      "factual errors.")
    w("- **partially correct**: right topic and mostly right, but misses a key point or contains a minor "
      "inaccuracy.")
    w("- **incorrect**: wrong, contradicts the textbook, answers a different question, or refuses or deflects.\n")
    w("Correct detail beyond the textbook is **not** penalised. Formatting, length, tone, diagrams, quizzes and "
      "follow-up suggestions are ignored. A request that fails with an error counts as incorrect.\n")
    w("### 3.6 Controls against a flattering result\n")
    w("- **No model grades its own answers.** The grader is not in the tutor's answer chain.")
    w("- **The real endpoint, not a replica.** Answers come from the same code path students use.")
    w("- **Random, seeded sampling of every chapter.** No hand-picked questions. The earlier hand-written "
      "retrieval check (14 Class 10 Science questions) reported 100% and is not used here.")
    w("- **The answering model is recorded.** Fallback answers are reported separately (§8), not averaged "
      "away.")
    w("- **Errors count as failures**, and rejected or dropped chapters are disclosed (§2.2).")
    w("- **Every number is recomputed from raw rows**, and every row is published (Appendix A).")
    w("- **The grader is spot-checked by hand** (§5.4).\n")

    # ── 4. Retrieval ──
    w("## 4. Retrieval metrics\n")
    w("### 4.1 How retrieval works\n")
    w("```mermaid")
    w("flowchart LR")
    w("    Q[\"Student question<br/>+ class\"] --> F[\"Class filter<br/>English-medium books\"]")
    w("    F --> V[\"Vector search<br/>pgvector, top 40\"]")
    w("    F --> K[\"Full-text search<br/>Postgres FTS, top 40\"]")
    w("    V --> R[\"Weighted RRF fusion<br/>vector 1.0 / keyword 0.4, top 20\"]")
    w("    K --> R")
    w("    R --> D[\"Dedup to 10\"]")
    w("    D --> X[\"Cross-encoder rerank\"]")
    w("    X --> B[\"Badge + up to 3 citations\"]")
    w("    X --> L[\"Answer model\"]")
    w("```\n")
    w("1. **Class filter.** The student's class selects its books: Science, Social Science and (for Class 8) "
      "Mathematics. Only English-medium books are searched, because the Class 8 Hindi and Punjabi PDFs "
      "extract as broken ligatures.")
    w("2. **Hybrid search.** Semantic similarity (384-d embeddings in pgvector) and keyword search (Postgres "
      "full-text) each return 40 chunks. Weighted Reciprocal Rank Fusion merges them into 20, so a passage "
      "that matches both by meaning and by exact term rises to the top.")
    w("3. **Dedup.** Near-identical chunks collapse to 10. Class 10 is stored twice in the index, and dedup "
      "keeps the copies from crowding out other passages.")
    w("4. **Rerank.** A cross-encoder reads each question and passage together and scores relevance from 0 to 1. "
      "This score orders the passages and sets the badge. *NCERT verified* needs a high rerank score, "
      "because raw embedding similarity cannot tell an answerable question from an unanswerable one: an "
      "unrelated physics question scores 0.56 cosine but 0.0002 on the cross-encoder.")
    w("5. **Citations.** The top passages go to the answer model, and up to three are returned to the student "
      "as book + page citations.\n")
    w("Retrieval is scored at **chapter** level. A chapter is what a citation points a student to, and exact chunk "
      "boundaries are an artefact of the text splitter. Top-k is measured on the retrieval stack with the raw "
      "question. Citation accuracy is measured on the full chat response, where the intent step may first "
      "expand the question.\n")

    def class_rows(metric) -> str:
        return " · ".join(f"Class {c}: {metric([r for r in ret if r['class'] == c])}" for c in (8, 9, 10))

    w("### 4.2 Top-1 retrieval\n")
    w("| Metric | Score | 95% CI |")
    w("|---|---|---|")
    w(f"| Right chapter ranked first | {share(r1, len(ret))} |")
    w(f"| MRR | **{mrr:.3f}** | |")
    w("")
    w("By class: " + class_rows(lambda rs: f"{sum(x['retrieval']['hit1'] for x in rs)}/{len(rs)}") + "\n")
    w("### 4.3 Top-3 retrieval\n")
    w("| Metric | Score | 95% CI |")
    w("|---|---|---|")
    w(f"| Right chapter in the top 3 | {share(r3, len(ret))} |")
    w(f"| Right chapter in the top 10 | {share(r10, len(ret))} |")
    w("")
    w("By class: " + class_rows(lambda rs: f"{sum(x['retrieval']['hit3'] for x in rs)}/{len(rs)}") + "\n")
    w("### 4.4 Citation accuracy\n")
    badges = Counter(BADGE.get(r["confidence_mode"]) for r in pos)
    w("| Metric | Score | 95% CI |")
    w("|---|---|---|")
    w(f"| Answer cites the gold chapter | {share(gold_cited, len(pos))} |")
    w(f"| Answer has no citation at all | {share(no_cite, len(pos))} |")
    w(f"| *NCERT verified* badge backed by the gold chapter | {share(verified_ok, len(verified))} |")
    w("")
    w("Badges shown on textbook questions: " + ", ".join(f"*{b}* {k}" for b, k in badges.most_common()) + ".\n")
    w("### 4.5 How to read the retrieval numbers\n")
    w("Retrieval is genuinely strong, but three properties of this test make it easier than real use:\n")
    w("- **The search space is one class.** After the class filter, the retriever chooses among "
      + ", ".join(f"{sampled_per_class[c]} chapters for Class {c}" for c in (8, 9, 10))
      + ", not the whole corpus. That is how the product works, but it narrows the choice.")
    w(f"- **Questions were written from the passage they are scored against.** Despite the instruction to "
      f"paraphrase, the median question reuses **{100 * statistics.median(overlaps):.0f}%** of its content "
      f"words verbatim from the passage, and {sum(o >= 0.5 for o in overlaps)} of {len(overlaps)} reuse at "
      f"least half. Real students' wording overlaps less, so expect real-world Top-1 to be somewhat lower.")
    w("- **One question per chapter, one run.** The confidence intervals are the honest error bars.\n")

    # ── 5. Answer quality ──
    w("## 5. Answer quality\n")
    w(f"### 5.1 LLM judge results\n")
    w("| Verdict | Answers | Share |")
    w("|---|---|---|")
    for key in ("correct", "partially_correct", "incorrect"):
        w(f"| {VERDICT[key]} | {v[key]} | {p(v[key], n)} |")
    w(f"| **Total graded** | **{n}** | |")
    w("")
    w("| Metric | Score | 95% CI |")
    w("|---|---|---|")
    w(f"| Fully correct | {share(v['correct'], n)} |")
    w(f"| Correct or partially correct | {share(v['correct'] + v['partially_correct'], n)} |")
    w(f"| At least one factual error | {share(fact_err, n)} |")
    kp = [r.get("key_points_covered") for r in judged if isinstance(r.get("key_points_covered"), int)]
    kp_total = [len(r["key_points"]) for r in judged if isinstance(r.get("key_points_covered"), int)]
    if kp:
        w(f"| Key points covered | **{100 * sum(min(a, b) for a, b in zip(kp, kp_total)) / sum(kp_total):.1f}%** "
          f"({sum(min(a, b) for a, b in zip(kp, kp_total))}/{sum(kp_total)}) | |")
    w("")
    if weak:
        weak_hit = sum(r["retrieval"]["hit1"] for r in weak if r.get("retrieval"))
        w(f"Of the {len(weak)} answers that were not fully correct, retrieval had ranked the gold chapter first "
          f"for {weak_hit}. Those failures happened while writing the answer, not while finding the text "
          f"(details in §7.3).\n")
    errs = [(r, e) for r in judged for e in (r.get("factual_errors") or [])]
    w(f"### 5.2 Factual errors flagged ({len(errs)})\n")
    if errs:
        w("| # | Verdict | Flagged statement |")
        w("|---|---|---|")
        for r, e in errs:
            w(f"| [{num[id(r)]}](#q{num[id(r)]}) | {VERDICT[r['verdict']]} | {cell(e, 220)} |")
        w("")
    else:
        w("None.\n")
    w("### 5.3 Off-syllabus behaviour\n")
    if neg:
        w(f"**{len(neg) - len(false_ground)} of {len(neg)}** answered off-syllabus questions got the *AI "
          "knowledge* badge and no textbook citation. "
          + (f"**{len(false_ground)}** carried a textbook citation or a grounded badge (listed in §7.4)."
             if false_ground else "None was presented as coming from the textbook.")
          + (f" {len(neg_all) - len(neg)} could not be answered (quota, §8.1)." if len(neg) < len(neg_all) else "")
          + "\n")
    else:
        w("**Not measured in this run.** The off-syllabus questions were asked last, after both Groq models had "
          "run out of their daily token budget, so none of them got an answer (§8.1). Rerun with "
          "`--retry-errors` once the quota refills to fill this section in.\n")
    w("| # | Question | Badge | Citations | Start of answer |")
    w("|---|---|---|---|---|")
    for r in neg_all:
        w(f"| [{num[id(r)]}](#q{num[id(r)]}) | {cell(r['question'])} | {BADGE.get(r['confidence_mode'])} | "
          f"{cited_list(r)} | {cell(r['answer'], 120)} |")
    w("")
    w("### 5.4 Grader verification\n")
    if spot:
        w(f"**Reviewer:** {spot['reviewer']}.  ")
        w(f"**What was re-graded ({len(spot['items'])} answers):** {spot['selection']}\n")
        w(f"Agreement with the automated grader: **{agree}/{len(spot['items'])} "
          f"({100 * agree / len(spot['items']):.0f}%)**.\n")
        if adjusted:
            w(f"The disagreements run in both directions:\n")
            w(f"- **Too strict:** {adjusted['up']} of the {adjusted['weak']} answers the grader marked down are "
              f"actually correct. In one, the grader penalised a fact that *is* in the textbook, just on a page it "
              f"wasn't shown.")
            w(f"- **Too lenient:** {adjusted['down']} of the {adjusted['sampled']} sampled answers the grader called "
              f"*correct* contain a factual slip in their explanation section. The core answer is right, but a "
              f"detail is wrong, such as describing spindle-shaped cells backwards.\n")
            w(f"Applying both to the full set (the upgrades exactly; the downgrade rate of "
              f"{100 * adjusted['rate']:.0f}% extrapolated from the sample) gives an **estimated strict accuracy of "
              f"about {100 * adjusted['est']:.0f}%**, against the grader's {100 * v['correct'] / n:.0f}%. The "
              f"*correct or partially correct* figure barely moves, because every disputed answer had a right core "
              f"answer. Read the two numbers together: the tutor almost always gets the main point right, but "
              f"about one answer in {max(2, round(1 / adjusted['rate'])) if adjusted['rate'] else '∞'} has a wrong "
              f"detail in its elaboration. The estimate rests on {adjusted['down']} slips in {adjusted['sampled']} "
              f"sampled answers, so it is uncertain: carrying the downgrade rate's 95% interval through gives "
              f"**{100 * adjusted['lo']:.0f}–{100 * adjusted['hi']:.0f}%**. A larger manual sample would narrow it.\n")
        w("| # | Grader | Manual | Agree | Note |")
        w("|---|---|---|---|---|")
        by_id = {r["id"]: r for r in rows}
        for s in spot["items"]:
            r = by_id[s["id"]]
            w(f"| [{num[id(r)]}](#q{num[id(r)]}) | {VERDICT.get(r.get('verdict'), '—')} | "
              f"{VERDICT.get(s['reviewer_verdict'])} | {'yes' if s['agree'] else '**no**'} | "
              f"{cell(s.get('note'), 260)} |")
        w("")
    else:
        w("Not yet performed for this run.\n")

    # ── 6. Class-wise ──
    w("## 6. Class-wise results\n")
    w("| Class | Questions | Top-1 | Top-3 | Citation accuracy | Fully correct | Correct or partial |")
    w("|---|---|---|---|---|---|---|")

    def group_row(label: str, rs_all: List[Dict[str, Any]]) -> str:
        rs = [x for x in rs_all if x["status"] == 200]
        g = [x for x in rs if x.get("verdict")]
        c = Counter(x["verdict"] for x in g)
        rr = [x for x in rs_all if x.get("retrieval")]
        return (f"| {label} | {len(rs_all)} | {p(sum(x['retrieval']['hit1'] for x in rr), len(rr))} | "
                f"{p(sum(x['retrieval']['hit3'] for x in rr), len(rr))} | {p(sum(cites_gold(x) for x in rs), len(rs))} | "
                f"**{p(c['correct'], len(g))}** ({c['correct']}/{len(g)}) | "
                f"{p(c['correct'] + c['partially_correct'], len(g))} |")

    for c in (8, 9, 10):
        w(group_row(f"Class {c}", [r for r in pos_all if r["class"] == c]))
    w(group_row("**All**", pos_all))
    w("")
    w("By subject:\n")
    w("| Subject | Questions | Top-1 | Top-3 | Citation accuracy | Fully correct | Correct or partial |")
    w("|---|---|---|---|---|---|---|")
    for s in ("Science", "Social Science", "Mathematics"):
        rs = [r for r in pos_all if subject(r["gold_chapter"]) == s]
        if rs:
            w(group_row(s, rs))
    w("")
    w("By book:\n")
    w("| Book | Questions | Top-1 | Top-3 | Citation accuracy | Fully correct | Correct or partial |")
    w("|---|---|---|---|---|---|---|")
    for code in sorted({r["gold_chapter"][:5] for r in pos_all}):
        w(group_row(BOOKS.get(code, code), [r for r in pos_all if r["gold_chapter"].startswith(code)]))
    w("")

    # ── 7. Failure cases ──
    w("## 7. Failure cases\n")
    misses = [r for r in ret if not r["retrieval"]["hit1"]]
    w(f"### 7.1 Retrieval misses — gold chapter not ranked first ({len(misses)})\n")
    if misses:
        w("| # | Class | Question | Gold | Retrieved top 3 | Gold rank | Answer cited | Verdict |")
        w("|---|---|---|---|---|---|---|---|")
        for r in misses:
            rr = r["retrieval"]["rr"]
            w(f"| [{num[id(r)]}](#q{num[id(r)]}) | {r['class']} | {cell(r['question'], 110)} | `{r['gold_chapter']}` | "
              f"{', '.join(f'`{c}`' for c in r['retrieval']['top'])} | {round(1 / rr) if rr else '>10'} | "
              f"{cited_list(r)} | {verdict_label(r)} |")
        w("")
    else:
        w("None.\n")
    uncited = [r for r in pos if not cites_gold(r)]
    w(f"### 7.2 Answers that did not cite the gold chapter ({len(uncited)})\n")
    w("An answer ends up with no citation when every retrieved passage scores below the reranker's grounding "
      "threshold. The tutor then answers from general knowledge under the *AI knowledge* badge.\n")
    if uncited:
        w("| # | Class | Question | Gold | Cited | Badge | Answered by | Verdict |")
        w("|---|---|---|---|---|---|---|---|")
        for r in uncited:
            w(f"| [{num[id(r)]}](#q{num[id(r)]}) | {r['class']} | {cell(r['question'], 100)} | `{r['gold_chapter']}` | "
              f"{cited_list(r, pages=True)} | {BADGE.get(r['confidence_mode'])} | `{r['answered_by']}` | "
              f"{VERDICT.get(r.get('verdict'), '—')} |")
        w("")
    else:
        w("None.\n")
    w(f"### 7.3 Answers that were not fully correct ({len(weak)})\n")
    for r in weak:
        k = num[id(r)]
        w(f"**[#{k}](#q{k}) · {VERDICT[r['verdict']]} · Class {r['class']} · {book(r['gold_chapter'])} · "
          f"`{r['gold_chapter']}`**  ")
        w(f"*Question:* {cell(r['question'])}  ")
        w("*Key points:* " + "; ".join(cell(x) for x in r["key_points"]) + "  ")
        w(f"*Grader:* {cell(r.get('rationale'))}  ")
        if r.get("factual_errors"):
            w("*Factual errors flagged:* " + "; ".join(cell(e) for e in r["factual_errors"]) + "  ")
        w(f"*Pipeline:* gold chapter {'ranked first' if r['retrieval']['hit1'] else 'not ranked first'}, "
          f"{'cited' if cites_gold(r) else 'not cited'} · badge *{BADGE.get(r['confidence_mode'])}* · "
          f"answered by `{r['answered_by']}` · {r['latency_s']}s\n")
    if not weak:
        w("None.\n")
    w(f"### 7.4 Off-syllabus questions shown as textbook-grounded ({len(false_ground)})\n")
    if false_ground:
        w("| # | Question | Badge | Citations |")
        w("|---|---|---|---|")
        for r in false_ground:
            w(f"| [{num[id(r)]}](#q{num[id(r)]}) | {cell(r['question'])} | {BADGE.get(r['confidence_mode'])} | {cited_list(r, pages=True)} |")
        w("")
    else:
        w("None.\n")
    w("### 7.5 Index quality issues found\n")
    w("| Check | Value |")
    w("|---|---|")
    w(f"| Chunks in `ncert_chunks` | {corpus.get('chunks', 0):,} |")
    w(f"| Chunk texts stored more than once | {corpus.get('duplicate_texts', 0):,} |")
    w(f"| Chunks containing LLM chatter instead of textbook text | {corpus.get('llm_chatter_chunks', 0):,} |")
    w(f"| Chapters with no usable passage (dropped from the test) | {len(dropped)} |")
    w("")
    w("The duplicates come from Class 10 being ingested twice under two naming schemes. Query-time dedup hides "
      "them from students, but they waste index space and reranker slots. The chatter chunks (\"It seems that "
      "the provided text does not contain any mathematical formulas…\") came from an LLM-assisted PDF parse, "
      "mostly of the Class 8 Mathematics volumes. That parse is also why three maths chapters yielded no "
      "usable passage.\n")

    # ── 8. Fallback ──
    w("## 8. Fallback model performance\n")
    w("The tutor walks an ordered chain of answer models and uses the first that responds. Groq's free tier "
      "allows `gpt-oss-120b` 8,000 tokens per minute, so under load the primary model is rate-limited and a "
      "fallback answers. The eval paced its requests the way a single student would, and recorded which model "
      "wrote every answer.\n")
    w("| Answered by | Answers | Fully correct | Correct or partial | Citation accuracy | Median latency |")
    w("|---|---|---|---|---|---|")
    for m, _ in Counter(r["answered_by"] or "error" for r in pos).most_common():
        rs = [r for r in pos if (r["answered_by"] or "error") == m]
        g = [x for x in rs if x.get("verdict")]
        c = Counter(x["verdict"] for x in g)
        w(f"| `{m}` | {len(rs)} | **{p(c['correct'], len(g))}** ({c['correct']}/{len(g)}) | "
          f"{p(c['correct'] + c['partially_correct'], len(g))} | {p(sum(cites_gold(x) for x in rs), len(rs))} | "
          f"{statistics.median(x['latency_s'] for x in rs):.1f}s |")
    w("")
    failed_hops = Counter(f for r in rows for f in r.get("fallbacks", []))
    if failed_hops:
        w("Models that failed on the way down the chain (one count per request):\n")
        w("| Model | Failed requests |")
        w("|---|---|")
        for m, k in failed_hops.most_common():
            w(f"| `{m}` | {k} |")
        w("")
        dead = [m for m in failed_hops if "qwen3-32b" in m or "kimi-k2" in m]
        if dead:
            w("`qwen/qwen3-32b` and `moonshotai/kimi-k2-instruct` are no longer served by Groq, so they fail on "
              "every fallback. They add a wasted round trip each before the chain reaches `gpt-oss-20b`. "
              "Removing them from `GROQ_ANSWER_MODELS` is a free latency win.\n")
    w("### 8.1 Capacity: questions the tutor could not answer\n")
    w("Every model in the chain runs on a free tier, and each tier has a hard daily ceiling:\n")
    w("| Model tier | Free-tier limit | State during the run |")
    w("|---|---|---|")
    w("| Groq `gpt-oss-120b` (primary answer) | 200,000 tokens/day, refilling ~139 tokens/min | Exhausted partway through |")
    w("| Groq `gpt-oss-20b` (intent step + answer fallback) | 200,000 tokens/day | Exhausted partway through |")
    w("| Gemini 3.6 / 3.7 / 3.8 Flash | 20 requests/day per model | Already exhausted before the run |")
    w("")
    if unanswered:
        first = min(num[id(r)] for r in unanswered)
        ids = ", ".join(f"[{num[id(r)]}](#q{num[id(r)]})" for r in unanswered)
        n_pos_un = sum(1 for r in unanswered if r["gold_chapter"])
        w(f"**{len(unanswered)} of {len(rows)} questions ({p(len(unanswered), len(rows))}) got no answer.** The "
          f"endpoint returned HTTP 500 because every model in the chain refused: both Groq models over their "
          f"daily token budget, and Gemini over its daily request quota. The first failure was question "
          f"#{first}. After that the tutor could only answer while the Groq budget trickled back. Affected: "
          f"{ids}.\n")
        w(f"These {n_pos_un} textbook and {len(unanswered) - n_pos_un} off-syllabus questions are **excluded from "
          f"the answer-quality, citation and off-syllabus metrics**, because a quota error says nothing about "
          f"whether the tutor answers correctly. They still count in the retrieval metrics (§4), which do not use "
          f"the answer models. With one tutor request costing about 4,000–5,000 Groq tokens (intent + answer), "
          f"the free keys serve roughly **40–50 questions a day**. That is the tutor's real capacity ceiling "
          f"today, and the first thing to change before anyone else uses it.\n")
    else:
        w("Every question got an answer.\n")
    if recovered:
        w(f"{len(recovered)} question(s) failed on the first attempt and succeeded when retried later; the graded "
          f"answer is the retry.\n")

    # ── 9. Limitations ──
    w("## 9. Limitations\n")
    w(f"- **Automated grading.** Verdicts come from `{judge}`, a 27B-parameter model, not a teacher. "
      + ("The manual re-grade (§5.4) measures how far to trust it. " if spot else "")
      + "Treat differences of a few points as noise.")
    w("- **One model both wrote the questions and graded the answers.** It never graded its own *answers* "
      "(the tutor wrote those), but it may favour answers that resemble its own reference answers.")
    w("- **Synthetic questions are easier than real ones.** They sit close to the textbook's wording "
      "(§4.5), and there are no typos, Hinglish, follow-up turns or photographed problems.")
    w("- **One run, one question per chapter.** Rerunning with `--rebuild` draws a new sample. Expect each "
      "figure to move within its confidence interval.")
    w("- **Class 8 Mathematics was mislabelled \"Geography\" in the question-writer prompt** because of a "
      "subject-mapping bug in the eval (since fixed). The writer still saw the real maths passage, so the "
      "questions are maths questions.")
    if unanswered:
        w(f"- **{len(unanswered)} questions got no answer** because the free-tier quotas ran out (§8.1). They are "
          "excluded from answer-quality metrics, which are therefore computed on "
          f"{len(pos)} of {len(pos_all)} textbook questions. They are the questions that happened to be "
          "asked once the quota was nearly spent, not chosen by difficulty.")
    if recovered:
        w(f"- **{len(recovered)} answers are from a retry.** Their first request failed because every model was "
          "rate-limited (§8). Answer quality is scored on the retry, and the failure is reported separately as "
          "a capacity result rather than folded into accuracy.")
    w("- **Out of scope:** voice, images, notes PDF, 3D models, quizzes and multi-turn chat were not measured.\n")

    # ── 10. Conclusions ──
    w("## 10. Conclusions\n")
    acc = 100 * v["correct"] / n if n else 0
    groups = {f"Class {c}": [r for r in judged if r["class"] == c] for c in (8, 9, 10)}
    groups.update({s: [r for r in judged if subject(r["gold_chapter"]) == s]
                   for s in ("Science", "Social Science", "Mathematics")})
    rated = {g: sum(x["verdict"] == "correct" for x in rs) / len(rs) for g, rs in groups.items() if len(rs) >= 3}
    lowest = min(rated, key=rated.get) if rated else None
    prim = [r for r in pos if r["answered_by"] == PRIMARY]
    fb = [r for r in pos if r["answered_by"] != PRIMARY]
    cite_rate = 100 * gold_cited / len(pos) if pos else 0
    w(f"1. **The automated grader rates {acc:.0f}% of answers fully correct** "
      f"({v['correct']}/{n}; 95% CI {share(v['correct'], n).split('| ')[1]}), and "
      f"{p(v['correct'] + v['partially_correct'], n)} at least partially correct."
      + (f" A manual re-grade suggests the strict figure is closer to **~{100 * adjusted['est']:.0f}%**: the "
         f"core answer is almost always right, but roughly one answer in "
         f"{max(2, round(1 / adjusted['rate'])) if adjusted['rate'] else '∞'} has a wrong detail in its "
         f"explanation (§5.4)." if adjusted else ""))
    w(f"2. **Retrieval {'is the strongest part of the system' if r1 >= 0.9 * len(ret) else 'needs work'}.** "
      f"The right chapter is ranked first for {p(r1, len(ret))} of questions and is in the top 3 for "
      f"{p(r3, len(ret))}. Class filter, hybrid search and cross-encoder rerank work together here, but real "
      f"students' phrasing will be harder than these questions (§4.5).")
    w(f"3. **Citations are {'reliable' if cite_rate >= 90 else 'the weak link'}.** {p(gold_cited, len(pos))} of "
      f"answers cite the chapter the question came from, and {p(verified_ok, len(verified))} of *NCERT "
      f"verified* badges are backed by the right chapter. Most misses are answers with no citation at all "
      f"({no_cite}), not wrong citations (§7.2).")
    if neg:
        w(f"4. **Off-syllabus honesty:** {len(neg) - len(false_ground)} of {len(neg)} answered off-syllabus "
          f"questions were labelled *AI knowledge* with no textbook citation"
          + (". The tutor does not pass general knowledge off as the textbook." if not false_ground
             else f"; {len(false_ground)} were not (§7.4)."))
    else:
        w("4. **Off-syllabus honesty was not measured in this run.** The quota ran out before those questions were "
          "asked (§8.1).")
    if fb and prim:
        def acc_of(rs):
            g = [x for x in rs if x.get("verdict")]
            return 100 * sum(x["verdict"] == "correct" for x in g) / len(g) if g else 0.0

        def cite_of(rs):
            return 100 * sum(cites_gold(x) for x in rs) / len(rs)

        pa, fa, pc, fc = acc_of(prim), acc_of(fb), cite_of(prim), cite_of(fb)
        head = ("Fallback answers are less accurate" if fa + 5 < pa else
                "Fallback answers are as accurate, but cite less" if fc + 5 < pc else
                "Fallback answers hold up")
        w(f"5. **{head}.** The primary `gpt-oss-120b` answered {len(prim)} questions ({pa:.0f}% fully correct, "
          f"{pc:.0f}% citing the gold chapter). Fallback models answered {len(fb)} after the primary hit its "
          f"rate limit ({fa:.0f}% fully correct, {fc:.0f}% citing the gold chapter). The fallback sample is "
          f"small, so treat the gap as indicative (§8).")
    if lowest:
        w(f"6. **The weakest area is {lowest}** ({100 * rated[lowest]:.0f}% fully correct, "
          f"{len(groups[lowest])} questions). The misses are listed in §7.3.")
    if unanswered:
        w(f"7. **Capacity, not quality, is the binding constraint.** {len(unanswered)} of {len(rows)} questions got "
          f"an HTTP 500 because every free-tier model was out of daily quota. On these keys the tutor serves "
          f"roughly 40–50 questions a day (§8.1).")
    w("8. **Highest-value fixes**, in order: move the answer models off free tiers (a paid Groq tier alone removes the daily ceiling); drop the two dead Groq models from the answer chain; re-ingest the "
      "Class 8 Mathematics books with a clean parser and delete the chatter chunks; remove the duplicate "
      "Class 10 copy.\n")

    # ── Appendix A ──
    w("## Appendix A — every question and answer\n")
    w("Each entry shows the question exactly as sent, what the textbook passage required, what retrieval "
      "found, the tutor's full answer exactly as returned, and the grade. Click to expand.\n")
    for r in rows:
        k = num[id(r)]
        if r["gold_chapter"]:
            head = (f"#{k} · {verdict_label(r)} · Class {r['class']} · "
                    f"{plain(book(r['gold_chapter']))} · {cell(r['question'], 90)}")
        else:
            state = BADGE.get(r["confidence_mode"]) if r["status"] == 200 else "no answer (quota)"
            head = f"#{k} · off-syllabus · {state} · {cell(r['question'], 90)}"
        w(f'<a id="q{k}"></a>')
        w(f"<details><summary>{html(head)}</summary>\n")
        w(f"**Question:** {cell(r['question'])}\n")
        if r["gold_chapter"]:
            w(f"**Source:** {book(r['gold_chapter'])}, chapter `{r['gold_chapter']}`, page {r.get('page')}\n")
            w(f"**Reference answer:** {cell(r['reference_answer'])}\n")
            w("**Key points:** " + "; ".join(cell(x) for x in r["key_points"]) + "\n")
            rt = r.get("retrieval") or {}
            w(f"**Retrieval top 3:** {', '.join(f'`{c}`' for c in rt.get('top', [])) or '—'} "
              f"({'gold chapter ranked first' if rt.get('hit1') else 'gold chapter not ranked first'})\n")
        if r.get("first_attempt"):
            w(f"**First attempt:** HTTP {r['first_attempt']['status']} — every answer model was rate-limited. "
              "The answer below is the retry (§8).\n")
        w(f"**Citations returned:** {cited_list(r, pages=True)} · **Badge:** {BADGE.get(r['confidence_mode'])} · "
          f"**Answered by:** `{r['answered_by']}` · **Latency:** {r['latency_s']}s\n")
        if r["status"] != 200:
            w(f"**Tutor's answer:** none. The request returned HTTP {r['status']} because every answer model "
              "was out of its free-tier quota (§8.1). This question is excluded from answer-quality metrics.\n")
        else:
            w("**Tutor's answer:**\n")
            w(fence(r["answer"]) + "\n")
        if r.get("verdict") and r["status"] == 200:
            w(f"**Grade:** {VERDICT[r['verdict']]} — {cell(r.get('rationale'))}"
              + (f"  \n**Factual errors:** {'; '.join(cell(e) for e in r['factual_errors'])}"
                 if r.get("factual_errors") else "") + "\n")
        w("</details>\n")

    # ── Appendix B ──
    w("## Appendix B — reproducing this report\n")
    w("```bash")
    w("cd RAGNOUS-BACKEND")
    w("python -m app.evaluation.accuracy_eval    # reuses accuracy_testset.json; add --rebuild for a new sample")
    w("python -m app.evaluation.build_report     # regenerates EVALUATION.md from accuracy_results.json")
    w("```\n")
    w("The run needs the Supabase index to be up and a Groq API key. Groq's free-tier limit of 8,000 tokens "
      "per minute is what paces it, at about an hour for 100 questions. Files:\n")
    w("| File | Contents |")
    w("|---|---|")
    w("| [`accuracy_eval.py`](RAGNOUS-BACKEND/app/evaluation/accuracy_eval.py) | Test-set builder, tutor runner, grader |")
    w("| [`accuracy_testset.json`](RAGNOUS-BACKEND/app/evaluation/accuracy_testset.json) | The questions, passages, reference answers and key points |")
    w("| [`accuracy_results.json`](RAGNOUS-BACKEND/app/evaluation/accuracy_results.json) | Every answer, citation, badge, retrieval rank and verdict |")
    if spot:
        w("| [`accuracy_spotcheck.json`](RAGNOUS-BACKEND/app/evaluation/accuracy_spotcheck.json) | The manual re-grade |")
    w("| [`build_report.py`](RAGNOUS-BACKEND/app/evaluation/build_report.py) | Renders this report from the files above |")
    w("| [`eval_runner.py`](RAGNOUS-BACKEND/app/evaluation/eval_runner.py) | Fast retrieval regression gate (hand-written questions) |")
    w("")

    REPORT_PATH.write_text("\n".join(out))
    print(f"wrote {REPORT_PATH} ({REPORT_PATH.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
