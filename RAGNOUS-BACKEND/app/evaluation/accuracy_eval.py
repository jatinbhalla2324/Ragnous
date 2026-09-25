"""End-to-end accuracy of the tutor, measured the way a student meets it.

    python -m app.evaluation.accuracy_eval              # build the test set if missing, then run
    python -m app.evaluation.accuracy_eval --rebuild    # draw a fresh test set first
    python -m app.evaluation.accuracy_eval --judge gemini   # write/grade with Gemini Flash instead

eval_runner.py is a retrieval alarm: 14 hand-written Class 10 Science questions,
each phrased like its chapter title, which is why it reads 100%. This measures
the product instead. It draws one passage at random from every English-medium
chapter in the index, has a model write the question a student of that class
would actually type, sends it through the real /api/v1/chat endpoint, and
scores what comes back:

  retrieval   Recall@1/@3/@10 and MRR on the production retrieval path
  grounding   whether the answer cites the chapter the question came from
  accuracy    whether the answer is correct, judged against the source passage
  negatives   off-syllabus questions that still get an NCERT citation or badge

The writer/judge defaults to Groq's qwen/qwen3.8-27b, which is not in the
tutor's answer chain (gpt-oss-120b -> ... -> Gemini Flash), so no model grades
its own answers. `--judge gemini` uses Gemini Flash 3.8 -> 3.7 -> 3.6 -> 3.5,
but the free tier allows 20 requests per model per day — too few for a full run.

The three stages overlap: the tutor answers each question as soon as it is
written, and grading starts as soon as an answer lands. The tutor and the judge
draw on separate Groq per-minute token budgets, so neither starves the other.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import json
import math
import os
import random
import re
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
load_dotenv(override=True)

from app.evaluation.eval_runner import run_one  # noqa: E402
from app.services.search_service import _class_from_subject, medium_of  # noqa: E402

HERE = Path(__file__).resolve().parent
TESTSET_PATH = HERE / "accuracy_testset.json"
RESULTS_PATH = HERE / "accuracy_results.json"

JUDGES = {
    "qwen": [("groq", "qwen/qwen3.8-27b")],
    "gemini": [("gemini", m) for m in
               ("gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash")],
}
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{}:generateContent"
LLM_CONCURRENCY = 2
SEED = 20260925

# A real chapter code: jesc105 = Class 10 (j), English (e), Science (sc), book 1, ch 05.
# Excludes the "…ps" prelims (forewords, committee lists) and anything unparsed.
CHAPTER_RE = re.compile(r"^[a-l]e[a-z]{2}\d{3}$")
AREA = {"sc": "Science", "cu": "Science", "ss": "Social Science",
        "es": "Social Science", "st": "Social Science", "gp": "Mathematics"}

# The index cannot answer these. The right response is no NCERT citation and
# the "ai_knowledge" badge — anything else tells a student a textbook said it.
NEGATIVES = [
    "Explain quantum field theory renormalization",
    "Who won the 2024 T20 cricket world cup?",
    "What is the capital of Brazil?",
    "How do I cook pasta?",
    "Who is the current CEO of Tesla?",
    "What are the provisions of the GST Act 2017?",
    "How does a transformer neural network work?",
    "What is the derivative of sin x?",
    "How do I solve a quadratic equation using the quadratic formula?",
    "Write a Python function to reverse a list",
    "What is the plot of Romeo and Juliet?",
    "What is the share price of Reliance today?",
    "How do black holes evaporate through Hawking radiation?",
    "What are the rules for castling in chess?",
    "Explain Einstein's general theory of relativity",
]


# ── Writer / judge model ──────────────────────────────────────────────────

async def llm_json(http: httpx.AsyncClient, judge: str, prompt: str,
                   required: List[str]) -> Tuple[dict, str]:
    """JSON from the first model in JUDGES[judge] that returns every key in
    `required`. Rate limits are waited out for as long as the provider asks."""
    errors: List[str] = []
    for attempt in range(8):
        for provider, model in JUDGES[judge]:
            try:
                if provider == "groq":
                    r = await http.post(GROQ_URL, timeout=120, headers={
                        "Authorization": f"Bearer {os.environ['GROQ_API_KEY']}"}, json={
                        "model": model, "temperature": 0, "reasoning_effort": "none",
                        "response_format": {"type": "json_object"},
                        "messages": [{"role": "user", "content": prompt}]})
                    if r.status_code == 429:
                        wait = float(r.headers.get("retry-after", 10))
                        errors.append(f"{model}: 429, retry after {wait:.0f}s")
                        await asyncio.sleep(min(wait, 60) + 1)
                        continue
                    r.raise_for_status()
                    text = r.json()["choices"][0]["message"]["content"]
                else:
                    # Header, not ?key=: httpx logs every request URL at INFO.
                    r = await http.post(GEMINI_URL.format(model), timeout=150, headers={
                        "x-goog-api-key": os.environ["GEMINI_API_KEY"]}, json={
                        "contents": [{"parts": [{"text": prompt}]}],
                        "generationConfig": {"responseMimeType": "application/json"}})
                    r.raise_for_status()
                    parts = r.json()["candidates"][0]["content"]["parts"]
                    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
                data = json.loads(text)
                if all(k in data for k in required):
                    return data, model
                errors.append(f"{model}: missing keys")
            except (httpx.HTTPError, KeyError, IndexError, json.JSONDecodeError) as e:
                errors.append(f"{model}: {type(e).__name__}")
        await asyncio.sleep(10 * (attempt + 1))
    raise RuntimeError("judge failed: " + "; ".join(errors[-4:]))


QUESTION_KEYS = ["usable", "question", "reference_answer", "key_points"]
QUESTION_PROMPT = """You are building a test set for an AI tutor used by Indian school students studying NCERT textbooks.

Below is one passage from a Class {cls} {area} textbook. Write ONE question that a Class {cls} student might genuinely type into a tutor app, whose answer is clearly and fully supported by this passage.

Rules:
- Ask about the core idea of the passage: a concept, process, cause, definition, comparison or important fact. Not a trivial detail such as a page number, a caption, a name in passing, or an activity instruction.
- Phrase it the way a student would, in your own words. Do not copy distinctive phrases from the passage. Never mention "the passage", "the text", "the chapter", "the figure" or "the activity".
- The question must stand alone: someone who never saw the passage must know exactly what is being asked.
- If the passage has no teachable content (contents page, exercises only, acknowledgements, index, garbled or machine-generated text), set usable to false and leave the other fields empty.

PASSAGE:
\"\"\"
{passage}
\"\"\"

Respond with a JSON object:
{{"usable": true or false,
  "question": "the student's question",
  "reference_answer": "2-4 sentences answering it, using only the passage",
  "key_points": ["2-4 short facts any correct answer must contain"]}}"""

VERDICT_KEYS = ["verdict", "key_points_covered", "factual_errors", "rationale"]
JUDGE_PROMPT = """You are grading an AI tutor's answer to a question from a Class {cls} NCERT student.

QUESTION: {question}

TEXTBOOK PASSAGE the question was written from:
\"\"\"
{passage}
\"\"\"

REFERENCE ANSWER: {reference}

KEY POINTS a correct answer must contain:
{key_points}

TUTOR'S ANSWER:
\"\"\"
{answer}
\"\"\"

Grade the tutor's answer for factual correctness and whether it answers the question asked.
- "correct": answers the question; every key point is present or clearly implied; no factual errors.
- "partially_correct": on the right topic and mostly right, but misses a key point or contains a minor inaccuracy.
- "incorrect": wrong, contradicts the textbook, answers a different question, or refuses or deflects.

Correct detail beyond the textbook is fine and must not be penalised. Ignore formatting, length, tone, diagrams, quizzes and follow-up suggestions.

Respond with a JSON object:
{{"verdict": "correct" | "partially_correct" | "incorrect",
  "key_points_covered": <how many key points the answer contains>,
  "factual_errors": [<every false statement, or one contradicting the passage; empty if none>],
  "rationale": "<one or two sentences>"}}"""


def _strip_header(content: str) -> str:
    return re.sub(r"^\[Book:[^\]]*\]\s*", "", content or "").strip()


# ── Stage 1: questions ────────────────────────────────────────────────────

async def draw_passages(conn) -> List[Tuple[str, list]]:
    """Up to three candidate passages per English-medium chapter, seeded."""
    rows = await conn.fetch("SELECT subject, chapter FROM ncert_chunks GROUP BY 1, 2")
    books: Dict[str, List[str]] = {}
    for r in rows:
        if medium_of(r["subject"]) == "en" and CHAPTER_RE.match(r["chapter"] or ""):
            books.setdefault(r["chapter"], []).append(r["subject"])
    out = []
    for chapter in sorted(books):
        chunks = list(await conn.fetch(
            "SELECT id, subject, page_number, content FROM ncert_chunks "
            "WHERE chapter = $1 AND subject = ANY($2) AND length(content) BETWEEN 700 AND 4000 "
            "ORDER BY id", chapter, books[chapter]))
        random.Random(f"{SEED}:{chapter}").shuffle(chunks)
        out.append((chapter, chunks[:3]))
    random.Random(SEED).shuffle(out)
    return out


async def write_question(http, judge: str, chapter: str, chunks: list) -> Optional[Dict[str, Any]]:
    cls = _class_from_subject(chapter)
    area = AREA.get(chapter[2:4], "Social Science")
    for chunk in chunks:
        passage = _strip_header(chunk["content"])
        q, model = await llm_json(http, judge, QUESTION_PROMPT.format(
            cls=cls, area=area, passage=passage), QUESTION_KEYS)
        if q.get("usable") and str(q.get("question", "")).strip() and q.get("key_points"):
            return {
                "id": chapter, "class": cls, "area": area, "gold_chapter": chapter,
                "subject": chunk["subject"], "page": chunk["page_number"],
                "passage": passage, "question": q["question"].strip(),
                "reference_answer": str(q["reference_answer"]).strip(),
                "key_points": [str(k) for k in q["key_points"]], "written_by": model,
            }
    return None


# ── Stage 2: the tutor ────────────────────────────────────────────────────

def _answer_chain_labels() -> List[str]:
    from app.api.v1 import chat
    from app.services.llm_fallback import _client_label
    return [_client_label(c) for c in chat.groq_answer_chain + chat.gemini_answer_chain]


async def ask_tutor(client: httpx.AsyncClient, item: Dict[str, Any],
                    chain: List[str]) -> Dict[str, Any]:
    """One real /api/v1/chat request, plus which model ended up answering."""
    log = io.StringIO()
    t0 = time.perf_counter()
    with contextlib.redirect_stdout(log):
        try:
            r = await client.post("/api/v1/chat", json={
                "query": item["question"], "student_class": f"{item['class']}th"})
            status, body = r.status_code, (r.json() if r.status_code == 200 else {})
        except Exception as e:  # the endpoint itself blew up
            status, body = 0, {"error": f"{type(e).__name__}: {e}"}
    latency = time.perf_counter() - t0

    lines = log.getvalue().splitlines()
    failed = {m.group(1) for ln in lines if (m := re.match(r"\[CHAT\] (\S+) (?:failed|timed out)", ln))}
    answered_by = next((c for c in chain if c not in failed), None) if status == 200 else None
    return {
        "status": status,
        "answer": body.get("content", ""),
        "citations": body.get("citations") or [],
        "confidence_mode": body.get("confidenceMode"),
        "confidence_score": body.get("confidenceScore"),
        "latency_s": round(latency, 2),
        "answered_by": answered_by,
        "fallbacks": sorted(failed),
    }


# ── Stage 3: scoring ──────────────────────────────────────────────────────

async def grade(http, judge: str, row: Dict[str, Any]) -> None:
    if row["status"] != 200:
        # A student who got an error got no answer — that is a miss.
        row.update(verdict="incorrect", factual_errors=[], key_points_covered=0,
                   rationale="endpoint error", judged_by=None)
        return
    v, model = await llm_json(http, judge, JUDGE_PROMPT.format(
        cls=row["class"], question=row["question"], passage=row["passage"],
        reference=row["reference_answer"],
        key_points="\n".join(f"- {k}" for k in row["key_points"]),
        answer=row["answer"]), VERDICT_KEYS)
    verdict = str(v["verdict"]).strip().lower().replace(" ", "_")
    if verdict not in ("correct", "partially_correct", "incorrect"):
        verdict = "incorrect"
    row.update(verdict=verdict, factual_errors=list(v.get("factual_errors") or []),
               key_points_covered=v.get("key_points_covered"),
               rationale=v.get("rationale"), judged_by=model)


def wilson(k: int, n: int, z: float = 1.96) -> Tuple[float, float]:
    """95% confidence interval for a proportion — small samples lie."""
    if not n:
        return 0.0, 0.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def summarise(pos: List[Dict[str, Any]], neg: List[Dict[str, Any]], corpus: Dict[str, int]) -> Dict[str, Any]:
    judged = [p for p in pos if p.get("verdict")]
    n = len(judged)
    count = lambda v: sum(p["verdict"] == v for p in judged)  # noqa: E731
    correct, partial = count("correct"), count("partially_correct")

    def share(k: int, total: int) -> Dict[str, Any]:
        lo, hi = wilson(k, total)
        return {"k": k, "n": total, "pct": round(100 * k / total, 1) if total else 0.0,
                "ci95": [round(100 * lo, 1), round(100 * hi, 1)]}

    ret = [p["retrieval"] for p in pos if p.get("retrieval")]
    cites_gold = sum(any(c.get("chapter") == p["gold_chapter"] for c in p["citations"]) for p in pos)
    verified = [p for p in pos if p["confidence_mode"] == "ncert_verified"]
    verified_right = sum(any(c.get("chapter") == p["gold_chapter"] for c in p["citations"]) for p in verified)
    false_ground = sum(bool(x["citations"]) or x["confidence_mode"] != "ai_knowledge" for x in neg)

    by_group: Dict[str, Dict[str, Any]] = {}
    for p in judged:
        for key in (f"Class {p['class']}", p["area"]):
            g = by_group.setdefault(key, {"n": 0, "correct": 0})
            g["n"] += 1
            g["correct"] += p["verdict"] == "correct"
    for g in by_group.values():
        g["pct"] = round(100 * g["correct"] / g["n"], 1)

    by_model: Dict[str, Dict[str, Any]] = {}
    for p in judged:
        g = by_model.setdefault(p["answered_by"] or "none", {"n": 0, "correct": 0})
        g["n"] += 1
        g["correct"] += p["verdict"] == "correct"

    lat = sorted(p["latency_s"] for p in pos + neg if p["status"] == 200)
    return {
        "answer_accuracy_strict": share(correct, n),
        "answer_accuracy_lenient": share(correct + partial, n),
        "verdicts": {"correct": correct, "partially_correct": partial, "incorrect": count("incorrect")},
        "answers_with_factual_errors": share(sum(bool(p["factual_errors"]) for p in judged), n),
        "citation_hits_gold_chapter": share(cites_gold, len(pos)),
        "answers_with_no_citation": share(sum(not p["citations"] for p in pos), len(pos)),
        "ncert_verified_badge_on_right_chapter": share(verified_right, len(verified)),
        "retrieval": {
            "recall@1": share(sum(r["hit1"] for r in ret), len(ret)),
            "recall@3": share(sum(r["hit3"] for r in ret), len(ret)),
            "recall@10": share(sum(r["hit10"] for r in ret), len(ret)),
            "mrr": round(statistics.mean(r["rr"] for r in ret), 3) if ret else 0.0,
        },
        "negatives_falsely_grounded": share(false_ground, len(neg)),
        "by_group": by_group,
        "by_answering_model": by_model,
        "latency_s": {"p50": lat[len(lat) // 2] if lat else None,
                      "p95": lat[int(len(lat) * 0.95)] if lat else None},
        "endpoint_errors": sum(p["status"] != 200 for p in pos + neg),
        "corpus": corpus,
    }


async def corpus_health(conn) -> Dict[str, int]:
    return {
        "chunks": await conn.fetchval("SELECT count(*) FROM ncert_chunks"),
        "duplicate_texts": await conn.fetchval(
            "SELECT count(*) FROM (SELECT content FROM ncert_chunks GROUP BY content HAVING count(*) > 1) d"),
        # An LLM-assisted parse stored the model's own chatter as textbook text.
        "llm_chatter_chunks": await conn.fetchval(
            "SELECT count(*) FROM ncert_chunks WHERE content ILIKE '%the provided text%' "
            "OR content ILIKE '%please share%' OR content ILIKE '%I will extract%'"),
    }


def print_report(s: Dict[str, Any]) -> None:
    f = lambda d: f"{d['pct']:5.1f}%  ({d['k']}/{d['n']}, 95% CI {d['ci95'][0]}–{d['ci95'][1]}%)"  # noqa: E731
    print("\n" + "─" * 72)
    print(f"  ANSWER ACCURACY (strict)      {f(s['answer_accuracy_strict'])}")
    print(f"  answer accuracy (lenient)     {f(s['answer_accuracy_lenient'])}")
    print(f"  verdicts                      {s['verdicts']}")
    print(f"  answers with a factual error  {f(s['answers_with_factual_errors'])}")
    print(f"  cites the right chapter       {f(s['citation_hits_gold_chapter'])}")
    print(f"  answers with no citation      {f(s['answers_with_no_citation'])}")
    print(f"  'NCERT verified' is right     {f(s['ncert_verified_badge_on_right_chapter'])}")
    r = s["retrieval"]
    print(f"  retrieval Recall@1            {f(r['recall@1'])}")
    print(f"  retrieval Recall@3            {f(r['recall@3'])}")
    print(f"  retrieval Recall@10           {f(r['recall@10'])}")
    print(f"  retrieval MRR                 {r['mrr']}")
    print(f"  off-syllabus shown as NCERT   {f(s['negatives_falsely_grounded'])}")
    print(f"  latency p50 / p95             {s['latency_s']['p50']}s / {s['latency_s']['p95']}s")
    print(f"  by group                      { {k: v['pct'] for k, v in sorted(s['by_group'].items())} }")
    print(f"  by answering model            {s['by_answering_model']}")
    print(f"  endpoint errors               {s['endpoint_errors']}")
    print(f"  corpus                        {s['corpus']}")
    print("─" * 72)


# ── Driver ────────────────────────────────────────────────────────────────

async def main(rebuild: bool, judge: str, limit: Optional[int]) -> None:
    from app.api.v1.chat import get_db_pool
    from app.main import app

    pool = await get_db_pool()
    if pool is None:
        raise SystemExit("❌ cannot reach the database — is Supabase paused?")
    async with pool.acquire() as conn:
        corpus = await corpus_health(conn)
        cached = None if rebuild or not TESTSET_PATH.exists() else json.loads(TESTSET_PATH.read_text())
        passages = [] if cached else await draw_passages(conn)
    if limit:
        passages = passages[:limit]

    chain = _answer_chain_labels()
    print(f"tutor answer chain: {chain}\nwriter/judge: {JUDGES[judge]}", flush=True)

    queue: asyncio.Queue = asyncio.Queue()
    written: List[Dict[str, Any]] = []
    rows: List[Dict[str, Any]] = []
    grading: List[asyncio.Task] = []
    sem = asyncio.Semaphore(LLM_CONCURRENCY)
    t_start = time.time()

    async with httpx.AsyncClient() as http:

        async def producer() -> None:
            if cached:
                items = cached["positives"][:limit] if limit else cached["positives"]
                for item in items:
                    await queue.put(item)
            else:
                async def one(chapter, chunks):
                    async with sem:
                        try:
                            item = await write_question(http, judge, chapter, chunks)
                        except RuntimeError as e:
                            print(f"  {chapter}: {e}", flush=True)
                            return
                    if item:
                        written.append(item)
                        await queue.put(item)
                await asyncio.gather(*(one(c, ch) for c, ch in passages))
                if limit:  # a smoke test must not overwrite the real test set
                    return await finish()
                TESTSET_PATH.write_text(json.dumps({
                    "created": datetime.now().isoformat(timespec="seconds"), "seed": SEED,
                    "written_by": JUDGES[judge], "chapters_sampled": sorted(c for c, _ in passages),
                    "positives": written,
                    "negatives": [{"id": f"neg{i:02d}", "class": 10, "question": q, "gold_chapter": None}
                                  for i, q in enumerate(NEGATIVES, 1)],
                }, indent=2, ensure_ascii=False))
                print(f"test set saved: {len(written)} questions from {len(passages)} chapters", flush=True)
            await finish()

        async def finish() -> None:
            for i, q in enumerate(NEGATIVES[:2] if limit else NEGATIVES, 1):
                await queue.put({"id": f"neg{i:02d}", "class": 10, "question": q, "gold_chapter": None})
            await queue.put(None)

        async def judge_row(row):
            async with sem:
                try:
                    await grade(http, judge, row)
                except RuntimeError as e:
                    print(f"  grading {row['id']} failed: {e}", flush=True)
                    return
            tally = [r for r in rows if r.get("verdict")]
            ok = sum(r["verdict"] == "correct" for r in tally)
            print(f"      graded {row['id']}: {row['verdict']:<17} running accuracy "
                  f"{ok}/{len(tally)} = {100 * ok / len(tally):.0f}%", flush=True)

        async def tutor() -> None:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://eval",
                                         timeout=300) as client:
                while (item := await queue.get()) is not None:
                    row = {**item, **await ask_tutor(client, item, chain)}
                    if item["gold_chapter"]:
                        async with pool.acquire() as conn:
                            r = await run_one(conn, item["question"], item["gold_chapter"],
                                              item["class"], use_rerank=True)
                        ranked = r.retrieved_chapters
                        rank = next((k for k, c in enumerate(ranked, 1) if c == item["gold_chapter"]), None)
                        row["retrieval"] = {"top": ranked[:3], "hit1": rank == 1,
                                            "hit3": bool(rank and rank <= 3),
                                            "hit10": bool(rank and rank <= 10),
                                            "rr": 1 / rank if rank else 0.0}
                        grading.append(asyncio.create_task(judge_row(row)))
                    rows.append(row)
                    cited = ",".join(sorted({c.get("chapter", "?") for c in row["citations"]})) or "-"
                    print(f"[{len(rows):>3}] {time.time() - t_start:>5.0f}s  {row['latency_s']:>5.1f}s "
                          f"{row['confidence_mode'] or 'ERROR':<18} gold={item['gold_chapter'] or 'NONE':<8} "
                          f"cited={cited:<16} by={row['answered_by']}", flush=True)
                    RESULTS_PATH.write_text(json.dumps({"rows": rows}, indent=2, ensure_ascii=False))
                    # Keep under gpt-oss-120b's 8k tokens/min so we measure the
                    # tutor one student meets, not its rate-limit fallbacks.
                    await asyncio.sleep(30 if row["fallbacks"] else 5)

        await asyncio.gather(producer(), tutor())
        await asyncio.gather(*grading)

    pos_rows = [r for r in rows if r["gold_chapter"]]
    neg_rows = [r for r in rows if not r["gold_chapter"]]
    summary = summarise(pos_rows, neg_rows, corpus)
    RESULTS_PATH.write_text(json.dumps({
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "judge": JUDGES[judge], "summary": summary, "rows": rows,
    }, indent=2, ensure_ascii=False))
    print_report(summary)
    print(f"per-question detail: {RESULTS_PATH}  ({time.time() - t_start:.0f}s)")


async def retry_errors(judge: str, pause: float) -> None:
    """Ask again, once, every question whose request failed outright.

    A 500 here means every model in the tutor's chain refused at the same
    moment — both Groq models at their per-minute token limit and Gemini out of
    its daily free quota. That is a capacity finding, not an answer-quality
    one, so the first attempt is kept on the row (`first_attempt`) and reported
    separately, and the retry is paced well under the limits.
    """
    from app.api.v1.chat import get_db_pool
    from app.main import app

    data = json.loads(RESULTS_PATH.read_text())
    rows = data["rows"]
    todo = [r for r in rows if r["status"] != 200]
    print(f"retrying {len(todo)} failed request(s), {pause:.0f}s apart", flush=True)
    await get_db_pool()
    chain = _answer_chain_labels()
    async with httpx.AsyncClient() as http, httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://eval", timeout=300) as client:
        for i, r in enumerate(todo, 1):
            first = {"status": r["status"], "fallbacks": r["fallbacks"], "latency_s": r["latency_s"]}
            for k in ("verdict", "factual_errors", "key_points_covered", "rationale", "judged_by"):
                r.pop(k, None)
            r.update(await ask_tutor(client, r, chain), first_attempt=first)
            if r["gold_chapter"]:
                await grade(http, judge, r)
            print(f"  [{i}/{len(todo)}] {r['id']}: HTTP {r['status']} by={r['answered_by']} "
                  f"verdict={r.get('verdict', '-')}", flush=True)
            RESULTS_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False))
            if i < len(todo):
                await asyncio.sleep(pause)

    pos_rows = [r for r in rows if r["gold_chapter"]]
    neg_rows = [r for r in rows if not r["gold_chapter"]]
    data["summary"] = summarise(pos_rows, neg_rows, data.get("summary", {}).get("corpus", {}))
    data["retried_at"] = datetime.now().isoformat(timespec="seconds")
    RESULTS_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    print_report(data["summary"])


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Measure the tutor's end-to-end accuracy.")
    ap.add_argument("--rebuild", action="store_true", help="draw a fresh test set")
    ap.add_argument("--judge", choices=sorted(JUDGES), default="qwen",
                    help="model that writes questions and grades answers")
    ap.add_argument("--limit", type=int, help="only the first N chapters (smoke test)")
    ap.add_argument("--retry-errors", action="store_true",
                    help="re-ask, once, only the questions whose request failed in the last run")
    ap.add_argument("--pause", type=float, default=45, help="seconds between retried requests")
    args = ap.parse_args()
    if args.retry_errors:
        asyncio.run(retry_errors(args.judge, args.pause))
    else:
        asyncio.run(main(args.rebuild, args.judge, args.limit))
