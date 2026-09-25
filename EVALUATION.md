# RAGNOUS — Evaluation Report

*Run 2026-09-25T23:32:07 · 88 textbook questions + 15 off-syllabus questions · tutor measured through its real `/api/v1/chat` endpoint · grader `qwen/qwen3.8-27b`*

This report measures what a student actually gets from RAGNOUS. Every number is recomputed from the per-question data in [`accuracy_results.json`](RAGNOUS-BACKEND/app/evaluation/accuracy_results.json) by [`build_report.py`](RAGNOUS-BACKEND/app/evaluation/build_report.py). Every question, answer and grade is listed in [Appendix A](#appendix-a--every-question-and-answer), so any figure can be checked by hand.

### Summary

| Metric | Score | 95% CI |
|---|---|---|
| **Answer accuracy** — fully correct (LLM judge) | **92.3%** (72/78) | 84.2–96.4% |
| Answer accuracy — fully correct, adjusted by manual re-grade (estimate, §5.4) | **~75%** | 51–88% |
| Answer accuracy — correct or partially correct | **97.4%** (76/78) | 91.1–99.3% |
| Top-1 retrieval — right chapter ranked first | **96.6%** (85/88) | 90.5–98.8% |
| Top-3 retrieval — right chapter in the top 3 | **98.9%** (87/88) | 93.8–99.8% |
| Citation accuracy — answer cites the source chapter | **92.3%** (72/78) | 84.2–96.4% |
| Answers with at least one factual error | **3.8%** (3/78) | 1.3–10.7% |
| Off-syllabus questions wrongly shown as textbook-grounded | not measured — none could be answered (§8.1) | |
| Questions the tutor could not answer — free-tier daily quota exhausted | **24.3%** (25/103) | 17.0–33.4% |
| Grader agreement with manual re-grade | **75.0%** (15/20) | 53.1–88.8% |
| Latency per answer, median / 95th percentile | **7.9s** / **15.4s** | |

### Contents

1. [Evaluation objective](#1-evaluation-objective)
2. [Test dataset](#2-test-dataset)
3. [Evaluation methodology](#3-evaluation-methodology)
4. [Retrieval metrics](#4-retrieval-metrics)
5. [Answer quality](#5-answer-quality)
6. [Class-wise results](#6-class-wise-results)
7. [Failure cases](#7-failure-cases)
8. [Fallback model performance](#8-fallback-model-performance)
9. [Limitations](#9-limitations)
10. [Conclusions](#10-conclusions)
- [Appendix A — every question and answer](#appendix-a--every-question-and-answer)
- [Appendix B — reproducing this report](#appendix-b--reproducing-this-report)

## 1. Evaluation objective

RAGNOUS is a retrieval-augmented tutor. It is only as good as its ability to find the right page of the student's textbook and to answer faithfully from it. The evaluation answers five questions:

1. **Retrieval.** For a real student question, does the system find the chapter the answer lives in?
2. **Grounding.** Does the answer cite the textbook source it came from?
3. **Correctness.** Is the answer factually right and complete, judged against the textbook?
4. **Honesty.** When a question is outside the books, does the tutor say so instead of presenting general knowledge as *NCERT verified*?
5. **Resilience.** When the main answer model is rate-limited and a fallback model answers, how much does quality drop?

**In scope:** the text chat path (`POST /api/v1/chat`) for Classes 8–10, English-medium books, single-turn questions.  
**Out of scope:** voice mode, image and PDF questions, the notes PDF, 3D models, quizzes, and multi-turn conversations.

## 2. Test dataset

### 2.1 Source corpus

Questions were drawn from every English-medium chapter in the RAGNOUS index (15,951 chunks in Supabase Postgres + pgvector):

| Book | Chapters sampled | Questions |
|---|---|---|
| Class 8 Science — *Curiosity* | 13 | 13 |
| Class 8 Social Science — *Exploring Society: India and Beyond* | 7 | 7 |
| Class 8 Mathematics — *Ganita Prakash* Part 1 | 7 | 4 |
| Class 8 Mathematics — *Ganita Prakash* Part 2 | 7 | 7 |
| Class 9 Science | 13 | 13 |
| Class 9 Social Science — *Understanding Society: India and Beyond* | 9 | 9 |
| Class 10 Science | 13 | 13 |
| Class 10 Geography — *Contemporary India II* | 7 | 7 |
| Class 10 Economics — *Understanding Economic Development* | 5 | 5 |
| Class 10 History — *India and the Contemporary World II* | 5 | 5 |
| Class 10 Political Science — *Democratic Politics II* | 5 | 5 |
| **Total** | **91** | **88** |

### 2.2 How each test item was built

1. **Sampling.** For each chapter, candidate passages of 700–4,000 characters were shuffled with a fixed seed (`20260925`) and the first was used. Chapter prelims (forewords, committee lists) were excluded.
2. **Question writing.** A model was given the passage and the class, and told to write **one question a student of that class would genuinely type into a tutor**. The rules: ask about the passage's core idea (not a caption or page detail), use the student's own words rather than copying the passage, never mention "the passage" or "the chapter", and make the question understandable on its own.
3. **Reference answer and key points.** The same call produced a 2–4 sentence reference answer using only the passage, and 2–4 key points any correct answer must contain. These drive the grading.
4. **Rejection.** A passage with no teachable content (contents page, exercises only, garbled or machine-generated text) was rejected, and the next one tried, up to three per chapter.

**3 chapter(s) produced no usable passage in three tries and were dropped:** `hegp102`, `hegp103`, `hegp106`. All are Class 8 Mathematics. Their chunks were extracted by an LLM-assisted parser and are largely LaTeX fragments or parser chatter (see [§7.5](#75-index-quality-issues-found)).

### 2.3 Off-syllabus questions

15 fixed questions the books cannot answer: current affairs, university-level physics, programming, cooking, finance, and two Class 10 maths questions (Class 10 Mathematics is not in the index). For these the right behaviour is the *AI knowledge* badge and **no textbook citation**. They are listed in [§5.3](#53-off-syllabus-behaviour).

### 2.4 Dataset statistics

| Statistic | Value |
|---|---|
| Textbook questions | 88 (Class 8: 31, Class 9: 22, Class 10: 35) |
| Off-syllabus questions | 15 |
| Median question length | 17 words |
| Median share of question words copied verbatim from the passage | 75% |
| Key points per question | 3.0 on average |

### 2.5 Example test item

```json
{
  "id": "iesc103",
  "class": 9,
  "gold_chapter": "iesc103",
  "page": 8,
  "question": "What is the main function of connective tissue, and can you give two examples of it?",
  "reference_answer": "Connective tissue is a type of tissue that connects and supports other tissues in the body. Two examples of connective tissue are blood and bones.",
  "key_points": [
    "Connects and supports other tissues",
    "Blood is an example",
    "Bones are an example"
  ]
}
```

The full test set is in [`accuracy_testset.json`](RAGNOUS-BACKEND/app/evaluation/accuracy_testset.json).

## 3. Evaluation methodology

The method combines three standard techniques for evaluating retrieval-augmented systems:

- **Synthetic test-set generation from the corpus.** Questions are written from randomly sampled source passages, so each has a known correct source (the *gold chapter*). This is the approach used by RAG-evaluation toolkits such as RAGAS.
- **Ranked-retrieval metrics.** Recall@k and Mean Reciprocal Rank, measured at chapter level.
- **LLM-as-a-judge with reference answers.** A separate model grades each answer against the source passage, a reference answer and explicit key points. A sample of those grades is then re-checked by hand.

### 3.1 Pipeline

```mermaid
flowchart TD
    A["NCERT index<br/>91 English-medium chapters"] -->|"1 random passage per chapter (seeded)"| B["Question writer<br/>qwen/qwen3.8-27b"]
    B -->|"question + reference answer + key points"| C[("Test set<br/>88 textbook + 15 off-syllabus")]
    C -->|"POST /api/v1/chat as the student's class"| D["RAGNOUS tutor<br/>real endpoint, nothing mocked"]
    C -->|"same question"| E["Retrieval stack<br/>hybrid search + rerank"]
    E --> G["Top-1 / Top-3 / Recall@10 / MRR"]
    D -->|"citations + badge"| H["Citation accuracy<br/>badge precision<br/>off-syllabus honesty"]
    D -->|"answer"| F["Grader<br/>qwen/qwen3.8-27b"]
    F --> I["Verdict + factual errors"]
    I -.->|"sample"| J["Manual re-grade"]
    G --> K["build_report.py<br/>recomputes every number from raw rows"]
    H --> K
    I --> K
    J --> K
```

### 3.2 Steps

1. **Build the test set** as described in §2. It is saved once and reused, so reruns measure the same questions.
2. **Ask the live tutor.** Each question is sent to `/api/v1/chat` exactly as the frontend sends it, with the student's class set to the book's class, in a fresh conversation. Nothing is mocked: intent classification, retrieval, reranking, answer generation, citations and the confidence badge all run as in production. The response's answer, citations, badge, latency and the model that wrote it are recorded.
3. **Score retrieval.** The same question runs through the production retrieval stack on its own. We record where the gold chapter ranks among the reranked results.
4. **Grade the answer.** The grader sees the question, the source passage, the reference answer, the key points and the tutor's full answer, then returns a verdict, the number of key points covered, every factual error, and a short rationale. The rubric is in §3.5.
5. **Check off-syllabus honesty.** An off-syllabus answer fails if it carries any textbook citation or any badge other than *AI knowledge*.
6. **Aggregate.** Proportions are reported with Wilson 95% confidence intervals, overall and by class, subject, book and answering model.
7. **Verify the grader.** A sample of verdicts is re-graded by hand against the same rubric, and the agreement rate is reported (§5.4).
8. **Render the report** from the raw per-question rows, so no number is typed in by hand.

### 3.3 Metric definitions

For $N$ textbook questions, let $\text{rank}_i$ be the position of question $i$'s gold chapter in the reranked retrieval list (∞ if it is not in the top 10).

- **Top-1 retrieval (Recall@1)** = $\frac{1}{N}\sum_i \mathbb{1}[\text{rank}_i = 1]$. The right chapter is ranked first.
- **Top-3 retrieval (Recall@3)** = $\frac{1}{N}\sum_i \mathbb{1}[\text{rank}_i \le 3]$. The top three passages usually reach the answer model and become citations, so this is the practical bar.
- **Recall@10**: the same with $k = 10$, the full list that is reranked.
- **MRR** = $\frac{1}{N}\sum_i 1/\text{rank}_i$. 1.0 means the gold chapter was always first.
- **Citation accuracy**: share of answers whose returned citations include the gold chapter.
- **Badge precision**: of answers badged *NCERT verified*, the share that cite the gold chapter.
- **Answer accuracy (strict)** = correct ÷ graded. **Lenient** = (correct + partially correct) ÷ graded.
- **Factual error rate**: share of answers in which the grader flagged at least one false statement.
- **False-grounding rate**: share of off-syllabus questions answered with a citation or a grounded badge.
- **95% CI**: Wilson score interval, $\frac{\hat p + \frac{z^2}{2n} \pm z\sqrt{\frac{\hat p(1-\hat p)}{n} + \frac{z^2}{4n^2}}}{1 + \frac{z^2}{n}}$ with $z = 1.96$. It stays inside [0, 1] and is honest at small $n$ and near 100%.

### 3.4 Models and their roles

| Role | Model |
|---|---|
| Tutor answer model, primary | `openai/gpt-oss-120b` on Groq (58 of 103 answers) |
| Tutor answer fallbacks, in order | `qwen/qwen3-32b` → `moonshotai/kimi-k2-instruct` → `openai/gpt-oss-20b` (Groq) → Gemini 3.6 / 3.7 / 3.8 Flash |
| Tutor intent classifier | `openai/gpt-oss-20b` on Groq |
| Retrieval embedding | `paraphrase-multilingual-MiniLM-L12-v2` (384-d, local) |
| Reranker | Cohere `rerank-english-v3.0`, falling back to local `cross-encoder/ms-marco-MiniLM-L-6-v2` |
| Question writer | `qwen/qwen3.8-27b` on Groq |
| Answer grader | `qwen/qwen3.8-27b` on Groq |
| Manual re-grade | Claude Opus 5.5 (the AI assistant that built this evaluation), not a human teacher |

### 3.5 Grading rubric

- **correct**: answers the question asked, contains every key point (or clearly implies it), and has no factual errors.
- **partially correct**: right topic and mostly right, but misses a key point or contains a minor inaccuracy.
- **incorrect**: wrong, contradicts the textbook, answers a different question, or refuses or deflects.

Correct detail beyond the textbook is **not** penalised. Formatting, length, tone, diagrams, quizzes and follow-up suggestions are ignored. A request that fails with an error counts as incorrect.

### 3.6 Controls against a flattering result

- **No model grades its own answers.** The grader is not in the tutor's answer chain.
- **The real endpoint, not a replica.** Answers come from the same code path students use.
- **Random, seeded sampling of every chapter.** No hand-picked questions. The earlier hand-written retrieval check (14 Class 10 Science questions) reported 100% and is not used here.
- **The answering model is recorded.** Fallback answers are reported separately (§8), not averaged away.
- **Errors count as failures**, and rejected or dropped chapters are disclosed (§2.2).
- **Every number is recomputed from raw rows**, and every row is published (Appendix A).
- **The grader is spot-checked by hand** (§5.4).

## 4. Retrieval metrics

### 4.1 How retrieval works

```mermaid
flowchart LR
    Q["Student question<br/>+ class"] --> F["Class filter<br/>English-medium books"]
    F --> V["Vector search<br/>pgvector, top 40"]
    F --> K["Full-text search<br/>Postgres FTS, top 40"]
    V --> R["Weighted RRF fusion<br/>vector 1.0 / keyword 0.4, top 20"]
    K --> R
    R --> D["Dedup to 10"]
    D --> X["Cross-encoder rerank"]
    X --> B["Badge + up to 3 citations"]
    X --> L["Answer model"]
```

1. **Class filter.** The student's class selects its books: Science, Social Science and (for Class 8) Mathematics. Only English-medium books are searched, because the Class 8 Hindi and Punjabi PDFs extract as broken ligatures.
2. **Hybrid search.** Semantic similarity (384-d embeddings in pgvector) and keyword search (Postgres full-text) each return 40 chunks. Weighted Reciprocal Rank Fusion merges them into 20, so a passage that matches both by meaning and by exact term rises to the top.
3. **Dedup.** Near-identical chunks collapse to 10. Class 10 is stored twice in the index, and dedup keeps the copies from crowding out other passages.
4. **Rerank.** A cross-encoder reads each question and passage together and scores relevance from 0 to 1. This score orders the passages and sets the badge. *NCERT verified* needs a high rerank score, because raw embedding similarity cannot tell an answerable question from an unanswerable one: an unrelated physics question scores 0.56 cosine but 0.0002 on the cross-encoder.
5. **Citations.** The top passages go to the answer model, and up to three are returned to the student as book + page citations.

Retrieval is scored at **chapter** level. A chapter is what a citation points a student to, and exact chunk boundaries are an artefact of the text splitter. Top-k is measured on the retrieval stack with the raw question. Citation accuracy is measured on the full chat response, where the intent step may first expand the question.

### 4.2 Top-1 retrieval

| Metric | Score | 95% CI |
|---|---|---|
| Right chapter ranked first | **96.6%** (85/88) | 90.5–98.8% |
| MRR | **0.977** | |

By class: Class 8: 30/31 · Class 9: 20/22 · Class 10: 35/35

### 4.3 Top-3 retrieval

| Metric | Score | 95% CI |
|---|---|---|
| Right chapter in the top 3 | **98.9%** (87/88) | 93.8–99.8% |
| Right chapter in the top 10 | **98.9%** (87/88) | 93.8–99.8% |

By class: Class 8: 31/31 · Class 9: 21/22 · Class 10: 35/35

### 4.4 Citation accuracy

| Metric | Score | 95% CI |
|---|---|---|
| Answer cites the gold chapter | **92.3%** (72/78) | 84.2–96.4% |
| Answer has no citation at all | **7.7%** (6/78) | 3.6–15.8% |
| *NCERT verified* badge backed by the gold chapter | **100.0%** (67/67) | 94.6–100.0% |

Badges shown on textbook questions: *NCERT verified* 67, *AI knowledge* 6, *Extended reference* 5.

### 4.5 How to read the retrieval numbers

Retrieval is genuinely strong, but three properties of this test make it easier than real use:

- **The search space is one class.** After the class filter, the retriever chooses among 34 chapters for Class 8, 22 chapters for Class 9, 35 chapters for Class 10, not the whole corpus. That is how the product works, but it narrows the choice.
- **Questions were written from the passage they are scored against.** Despite the instruction to paraphrase, the median question reuses **75%** of its content words verbatim from the passage, and 81 of 88 reuse at least half. Real students' wording overlaps less, so expect real-world Top-1 to be somewhat lower.
- **One question per chapter, one run.** The confidence intervals are the honest error bars.

## 5. Answer quality

### 5.1 LLM judge results

| Verdict | Answers | Share |
|---|---|---|
| ✅ correct | 72 | 92% |
| 🟡 partial | 4 | 5% |
| ❌ incorrect | 2 | 3% |
| **Total graded** | **78** | |

| Metric | Score | 95% CI |
|---|---|---|
| Fully correct | **92.3%** (72/78) | 84.2–96.4% |
| Correct or partially correct | **97.4%** (76/78) | 91.1–99.3% |
| At least one factual error | **3.8%** (3/78) | 1.3–10.7% |
| Key points covered | **95.8%** (229/239) | |

Of the 6 answers that were not fully correct, retrieval had ranked the gold chapter first for 6. Those failures happened while writing the answer, not while finding the text (details in §7.3).

### 5.2 Factual errors flagged (5)

| # | Verdict | Flagged statement |
|---|---|---|
| [16](#q16) | ❌ incorrect | The tutor defines the right to choose as the ability to pick a product 'after getting correct information' and states it is violated when a seller gives 'false or incomplete information'. This conflates the Right to Choo… |
| [28](#q28) | 🟡 partial | Participation in party activities is relatively high and has been rising over the past three decades. |
| [28](#q28) | 🟡 partial | The share of members and those feeling close has increased steadily over the last thirty years. |
| [65](#q65) | ❌ incorrect | The textbook tells us that the exact reason a puri puffs up in hot oil is still not completely understood by scientists. |
| [65](#q65) | ❌ incorrect | It is a phenomenon that is still being investigated. |

### 5.3 Off-syllabus behaviour

**Not measured in this run.** The off-syllabus questions were asked last, after both Groq models had run out of their daily token budget, so none of them got an answer (§8.1). Rerun with `--retry-errors` once the quota refills to fill this section in.

| # | Question | Badge | Citations | Start of answer |
|---|---|---|---|---|
| [89](#q89) | Explain quantum field theory renormalization | no answer (quota) | — |  |
| [90](#q90) | Who won the 2024 T20 cricket world cup? | no answer (quota) | — |  |
| [91](#q91) | What is the capital of Brazil? | no answer (quota) | — |  |
| [92](#q92) | How do I cook pasta? | no answer (quota) | — |  |
| [93](#q93) | Who is the current CEO of Tesla? | no answer (quota) | — |  |
| [94](#q94) | What are the provisions of the GST Act 2017? | no answer (quota) | — |  |
| [95](#q95) | How does a transformer neural network work? | no answer (quota) | — |  |
| [96](#q96) | What is the derivative of sin x? | no answer (quota) | — |  |
| [97](#q97) | How do I solve a quadratic equation using the quadratic formula? | no answer (quota) | — |  |
| [98](#q98) | Write a Python function to reverse a list | no answer (quota) | — |  |
| [99](#q99) | What is the plot of Romeo and Juliet? | no answer (quota) | — |  |
| [100](#q100) | What is the share price of Reliance today? | no answer (quota) | — |  |
| [101](#q101) | How do black holes evaporate through Hawking radiation? | no answer (quota) | — |  |
| [102](#q102) | What are the rules for castling in chess? | no answer (quota) | — |  |
| [103](#q103) | Explain Einstein's general theory of relativity | no answer (quota) | — |  |

### 5.4 Grader verification

**Reviewer:** Claude Opus 5.5 (the AI assistant that built this evaluation), not a human teacher.  
**What was re-graded (20 answers):** All 6 answers the grader did not mark fully correct, plus a random sample (seed 20260925) of 14 of the 72 it marked correct. Each was re-graded against the same rubric after reading the question, key points, reference answer and the tutor's full answer; disputed textbook facts were checked against the index itself.

Agreement with the automated grader: **15/20 (75%)**.

The disagreements run in both directions:

- **Too strict:** 2 of the 6 answers the grader marked down are actually correct. In one, the grader penalised a fact that *is* in the textbook, just on a page it wasn't shown.
- **Too lenient:** 3 of the 14 sampled answers the grader called *correct* contain a factual slip in their explanation section. The core answer is right, but a detail is wrong, such as describing spindle-shaped cells backwards.

Applying both to the full set (the upgrades exactly; the downgrade rate of 21% extrapolated from the sample) gives an **estimated strict accuracy of about 75%**, against the grader's 92%. The *correct or partially correct* figure barely moves, because every disputed answer had a right core answer. Read the two numbers together: the tutor almost always gets the main point right, but about one answer in 5 has a wrong detail in its elaboration. The estimate rests on 3 slips in 14 sampled answers, so it is uncertain: carrying the downgrade rate's 95% interval through gives **51–88%**. A larger manual sample would narrow it.

| # | Grader | Manual | Agree | Note |
|---|---|---|---|---|
| [16](#q16) | ❌ incorrect | ❌ incorrect | yes | Defines the right to choose around being misinformed and gives a false-advertising example. That is the right to information; the textbook's example of the right to choose is being forced to buy an unwanted item alongside the one you wanted. |
| [28](#q28) | 🟡 partial | ✅ correct | **no** | Grader too strict. It flagged 'membership has gone up over three decades' as unsupported, but Democratic Politics II p.7 (jess404) says: 'Over the last three decades, the proportion of those who report to be members of political parties in India has gone up st… |
| [41](#q41) | 🟡 partial | 🟡 partial | yes | Links Panchayati Raj to Vedic Sabha/Samiti assemblies but misses the textbook's point that power was seen as a responsibility tied to social welfare. |
| [59](#q59) | 🟡 partial | 🟡 partial | yes | Covers institutions, citizens and everyday participation; misses that citizens must exercise their rights responsibly. |
| [65](#q65) | ❌ incorrect | ❌ incorrect | yes | Flawed test item: the passage poses 'why does a puri puff up?' as an open question and never answers it. The tutor's answer is still wrong. It claims the textbook says scientists do not fully understand puffing, which the book does not say, and never gives the… |
| [71](#q71) | 🟡 partial | ✅ correct | **no** | Grader too strict. The answer explains that transport links producers to consumers and markets and that development depends on moving goods quickly. The missing key point ('goods do not move on their own') is clearly implied. |
| [61](#q61) | ✅ correct | ✅ correct | yes | All key points present. |
| [27](#q27) | ✅ correct | ✅ correct | yes | All key points present; dependency-ratio explanation accurate. |
| [68](#q68) | ✅ correct | 🟡 partial | **no** | Grader too lenient. 1928, land revenue and Vallabhbhai Patel are right, but the explanation calls the protest a 'shiksha (strike)' (shiksha means education) and invents a field-work strike. Bardoli was a taluka of Surat district, not a district. |
| [44](#q44) | ✅ correct | ✅ correct | yes | All key points present. |
| [2](#q2) | ✅ correct | ✅ correct | yes | 10% rule and heat loss explained correctly. |
| [75](#q75) | ✅ correct | ✅ correct | yes | Sign convention stated correctly. |
| [11](#q11) | ✅ correct | ✅ correct | yes | All key points present. |
| [77](#q77) | ✅ correct | ✅ correct | yes | Oviduct path and fertilisation site correct; the extra detail (ampulla, fimbriae) is accurate. |
| [40](#q40) | ✅ correct | 🟡 partial | **no** | Grader too lenient. The definitions are right, but the explanation says all members of a population 'share the same genetic makeup', which is false. Individuals of one species vary genetically. |
| [14](#q14) | ✅ correct | ✅ correct | yes | All key points present. |
| [34](#q34) | ✅ correct | ✅ correct | yes | v = sqrt(2gh) derivation correct; mass and shape independence stated with the friction caveat. |
| [24](#q24) | ✅ correct | ✅ correct | yes | 919 girls per 1,000 boys (Census 2011) and sex-selective abortion correct. |
| [48](#q48) | ✅ correct | ✅ correct | yes | All key points present. |
| [72](#q72) | ✅ correct | 🟡 partial | **no** | Grader too lenient. The core idea (shape suits function) is right, but it describes spindle-shaped muscle cells as 'narrow in the middle and wider at the ends', which is backwards: spindle cells are thickest in the middle and taper at the ends. |

## 6. Class-wise results

| Class | Questions | Top-1 | Top-3 | Citation accuracy | Fully correct | Correct or partial |
|---|---|---|---|---|---|---|
| Class 8 | 31 | 97% | 100% | 89% | **96%** (27/28) | 96% |
| Class 9 | 22 | 91% | 95% | 84% | **89%** (17/19) | 100% |
| Class 10 | 35 | 100% | 100% | 100% | **90%** (28/31) | 97% |
| **All** | 88 | 97% | 99% | 92% | **92%** (72/78) | 97% |

By subject:

| Subject | Questions | Top-1 | Top-3 | Citation accuracy | Fully correct | Correct or partial |
|---|---|---|---|---|---|---|
| Science | 39 | 95% | 100% | 89% | **97%** (36/37) | 97% |
| Social Science | 38 | 97% | 97% | 94% | **84%** (26/31) | 97% |
| Mathematics | 11 | 100% | 100% | 100% | **100%** (10/10) | 100% |

By book:

| Book | Questions | Top-1 | Top-3 | Citation accuracy | Fully correct | Correct or partial |
|---|---|---|---|---|---|---|
| Class 8 Science — *Curiosity* | 13 | 92% | 100% | 85% | **92%** (12/13) | 92% |
| Class 8 Social Science — *Exploring Society: India and Beyond* | 7 | 100% | 100% | 80% | **100%** (5/5) | 100% |
| Class 8 Mathematics — *Ganita Prakash* Part 1 | 4 | 100% | 100% | 100% | **100%** (4/4) | 100% |
| Class 8 Mathematics — *Ganita Prakash* Part 2 | 7 | 100% | 100% | 100% | **100%** (6/6) | 100% |
| Class 9 Science | 13 | 92% | 100% | 83% | **100%** (12/12) | 100% |
| Class 9 Social Science — *Understanding Society: India and Beyond* | 9 | 89% | 89% | 86% | **71%** (5/7) | 100% |
| Class 10 Science | 13 | 100% | 100% | 100% | **100%** (12/12) | 100% |
| Class 10 Geography — *Contemporary India II* | 7 | 100% | 100% | 100% | **83%** (5/6) | 100% |
| Class 10 Economics — *Understanding Economic Development* | 5 | 100% | 100% | 100% | **75%** (3/4) | 75% |
| Class 10 History — *India and the Contemporary World II* | 5 | 100% | 100% | 100% | **100%** (4/4) | 100% |
| Class 10 Political Science — *Democratic Politics II* | 5 | 100% | 100% | 100% | **80%** (4/5) | 100% |

## 7. Failure cases

### 7.1 Retrieval misses — gold chapter not ranked first (3)

| # | Class | Question | Gold | Retrieved top 3 | Gold rank | Answer cited | Verdict |
|---|---|---|---|---|---|---|---|
| [38](#q38) | 9 | How do bacteria help in the nitrogen cycle, and what are the specific steps involved in nitrification? | `iesc113` | `iesc112`, `iesc113`, `iesc113` | 2 | — | ✅ correct |
| [58](#q58) | 8 | How thin is the Earth's crust compared to the whole planet, and why is this layer important? | `hecu113` | `hecu109`, `hecu113`, `hecu113` | 2 | — | ✅ correct |
| [79](#q79) | 9 | Which crops were first domesticated in the Ganga Plains of India compared to other parts of India? | `iest104` | `iest105`, `iest105`, `iest105` | >10 | — | no answer (quota) |

### 7.2 Answers that did not cite the gold chapter (6)

An answer ends up with no citation when every retrieved passage scores below the reranker's grounding threshold. The tutor then answers from general knowledge under the *AI knowledge* badge.

| # | Class | Question | Gold | Cited | Badge | Answered by | Verdict |
|---|---|---|---|---|---|---|---|
| [18](#q18) | 8 | Why does the smell of camphor spread to all corners of a room when it is heated? | `hecu107` | — | AI knowledge | `Groq[openai/gpt-oss-20b]` | ✅ correct |
| [38](#q38) | 9 | How do bacteria help in the nitrogen cycle, and what are the specific steps involved in nitrificatio… | `iesc113` | — | AI knowledge | `Groq[openai/gpt-oss-20b]` | ✅ correct |
| [50](#q50) | 9 | What are the main differences between the problems caused by errors in mitosis and errors in meiosis… | `iesc102` | — | AI knowledge | `Groq[openai/gpt-oss-120b]` | ✅ correct |
| [51](#q51) | 9 | What are some examples of landforms that are created by the action of waves and currents along the c… | `iest102` | — | AI knowledge | `Groq[openai/gpt-oss-120b]` | ✅ correct |
| [58](#q58) | 8 | How thin is the Earth's crust compared to the whole planet, and why is this layer important? | `hecu113` | — | AI knowledge | `Groq[openai/gpt-oss-120b]` | ✅ correct |
| [64](#q64) | 8 | How did Maharaja Ranjit Singh manage to unify the Sikh confederacies in Punjab, and what specific qu… | `hees102` | — | AI knowledge | `Groq[openai/gpt-oss-20b]` | ✅ correct |

### 7.3 Answers that were not fully correct (6)

**[#16](#q16) · ❌ incorrect · Class 10 · Class 10 Economics — *Understanding Economic Development* · `jess205`**  
*Question:* What is the right to choose for consumers, and can you give an example of how it might be violated?  
*Key points:* Consumers have the right to decide whether to continue receiving a service or buying a product.; This right applies to all consumers regardless of age, gender, or nature of service.; Violation occurs when customers are forced to buy unwanted items (e.g., buying a brush with toothpaste or a stove with gas connection).  
*Grader:* The tutor's answer fundamentally misinterprets the 'Right to Choose' by describing it as a right to be informed rather than a right to be free from coercion. It fails to mention the key concept of being forced to buy unwanted goods, which is the central definition and example provided in the textbook passage.  
*Factual errors flagged:* The tutor defines the right to choose as the ability to pick a product 'after getting correct information' and states it is violated when a seller gives 'false or incomplete information'. This conflates the Right to Choose with the Right to Information. According to the provided textbook passage, the Right to Choose is violated when a consumer is *forced* to buy an unwanted item (e.g., buying a brush with toothpaste or a stove with a gas connection), not merely when they are misled by false information.  
*Pipeline:* gold chapter ranked first, cited · badge *NCERT verified* · answered by `Groq[openai/gpt-oss-120b]` · 5.61s

**[#28](#q28) · 🟡 partial · Class 10 · Class 10 Political Science — *Democratic Politics II* · `jess404`**  
*Question:* Is it true that political parties are unpopular and people don't participate in them in India? What does the survey evidence say about trust and participation levels?  
*Key points:* The belief of unpopularity/indifference is only partly true for India.; Political parties are one of the least trusted institutions in South Asia and globally.; Participation in political party activities is fairly high in India.; The proportion of party members in India is higher than in many other countries.  
*Grader:* The answer correctly identifies that the belief in unpopularity is partly true, that trust is low, and that participation is high relative to other countries. However, it introduces factual claims that participation has been 'rising' or 'increased steadily' over the last three decades, which is not supported by the provided textbook passage (which only states the level was 'fairly high' based on surveys over several decades, without specifying a trend of increase). This constitutes a minor inaccuracy/hallucination relative to the source text.  
*Factual errors flagged:* Participation in party activities is relatively high and has been rising over the past three decades.; The share of members and those feeling close has increased steadily over the last thirty years.  
*Pipeline:* gold chapter ranked first, cited · badge *NCERT verified* · answered by `Groq[openai/gpt-oss-120b]` · 5.49s

**[#41](#q41) · 🟡 partial · Class 9 · Class 9 Social Science — *Understanding Society: India and Beyond* · `iest101`**  
*Question:* How does the Panchayati Raj system connect to ancient Indian political thought?  
*Key points:* Panchayati Raj promotes local self-governance and public participation at the village level; It is an example of how ancient ideas continue to shape modern democratic practices; Ancient Indian political thought viewed power as a responsibility and linked politics with social welfare  
*Grader:* The answer correctly identifies that Panchayati Raj promotes local self-governance and public participation, and it connects this to ancient ideas of collective decision-making. However, it misses the specific key point from the textbook that ancient Indian political thought viewed 'power as a responsibility' and explicitly linked politics with 'social welfare' and 'economics,' instead focusing primarily on the structural parallel of assemblies (Sabha/Samiti).  
*Pipeline:* gold chapter ranked first, cited · badge *NCERT verified* · answered by `Groq[openai/gpt-oss-120b]` · 6.23s

**[#59](#q59) · 🟡 partial · Class 9 · Class 9 Social Science — *Understanding Society: India and Beyond* · `iest106`**  
*Question:* According to the text, why is democracy considered more than just holding elections?  
*Key points:* Democracy requires constant care, awareness, and active participation.; It involves both institutions and citizens.; It is reflected in everyday life, not just elections.; Citizens must exercise rights responsibly.  
*Grader:* The answer correctly identifies that democracy involves institutions, citizens, and everyday participation, covering three of the four key points. However, it misses the specific point that citizens must exercise their rights 'responsibly,' which is explicitly mentioned in the textbook passage and reference answer.  
*Pipeline:* gold chapter ranked first, cited · badge *NCERT verified* · answered by `Groq[openai/gpt-oss-120b]` · 6.48s

**[#65](#q65) · ❌ incorrect · Class 8 · Class 8 Science — *Curiosity* · `hecu101`**  
*Question:* Why does a puri puff up when it is cooked in hot oil?  
*Key points:* The passage frames the puffing as a question to be investigated.; It encourages asking 'why' about everyday phenomena.; It does not explicitly state the physical cause (like steam expansion) in this specific excerpt.  
*Grader:* The tutor's answer contains a significant factual error by claiming the textbook states the phenomenon is 'still not completely understood by scientists' and 'still being investigated.' The provided passage frames the puffing as a simple everyday phenomenon for students to investigate to 'think like a scientist,' not as an unsolved scientific mystery. Furthermore, the answer fails to address the key points that the passage frames this as a question for investigation and encourages asking 'why' about everyday phenomena, instead inventing a narrative about systematic scientific research that is not in the text.  
*Factual errors flagged:* The textbook tells us that the exact reason a puri puffs up in hot oil is still not completely understood by scientists.; It is a phenomenon that is still being investigated.  
*Pipeline:* gold chapter ranked first, cited · badge *NCERT verified* · answered by `Groq[openai/gpt-oss-120b]` · 8.92s

**[#71](#q71) · 🟡 partial · Class 10 · Class 10 Geography — *Contemporary India II* · `jess107`**  
*Question:* Why is efficient transport considered essential for the fast development of a country?  
*Key points:* Development depends on both production and the movement of goods/services.; Goods and services do not move from supply to demand locations on their own.; Efficient transport is a prerequisite for fast development.  
*Grader:* The answer correctly identifies that development depends on the movement of goods and that efficient transport is essential for this, covering two key points. However, it fails to explicitly state the specific textbook point that 'goods and services do not move from supply to demand locations on their own,' which is a core reason cited in the passage for the necessity of transport.  
*Pipeline:* gold chapter ranked first, cited · badge *Extended reference* · answered by `Groq[openai/gpt-oss-20b]` · 6.97s

### 7.4 Off-syllabus questions shown as textbook-grounded (0)

None.

### 7.5 Index quality issues found

| Check | Value |
|---|---|
| Chunks in `ncert_chunks` | 15,951 |
| Chunk texts stored more than once | 5,717 |
| Chunks containing LLM chatter instead of textbook text | 274 |
| Chapters with no usable passage (dropped from the test) | 3 |

The duplicates come from Class 10 being ingested twice under two naming schemes. Query-time dedup hides them from students, but they waste index space and reranker slots. The chatter chunks ("It seems that the provided text does not contain any mathematical formulas…") came from an LLM-assisted PDF parse, mostly of the Class 8 Mathematics volumes. That parse is also why three maths chapters yielded no usable passage.

## 8. Fallback model performance

The tutor walks an ordered chain of answer models and uses the first that responds. Groq's free tier allows `gpt-oss-120b` 8,000 tokens per minute, so under load the primary model is rate-limited and a fallback answers. The eval paced its requests the way a single student would, and recorded which model wrote every answer.

| Answered by | Answers | Fully correct | Correct or partial | Citation accuracy | Median latency |
|---|---|---|---|---|---|
| `Groq[openai/gpt-oss-120b]` | 58 | **91%** (53/58) | 97% | 95% | 7.4s |
| `Groq[openai/gpt-oss-20b]` | 20 | **95%** (19/20) | 100% | 85% | 8.3s |

Models that failed on the way down the chain (one count per request):

| Model | Failed requests |
|---|---|
| `Groq[moonshotai/kimi-k2-instruct]` | 45 |
| `Groq[openai/gpt-oss-120b]` | 45 |
| `Groq[qwen/qwen3-32b]` | 45 |
| `Gemini[gemini-3.6-flash]` | 25 |
| `Gemini[gemini-3.7-flash]` | 25 |
| `Gemini[gemini-3.8-flash]` | 25 |
| `Groq[openai/gpt-oss-20b]` | 25 |

`qwen/qwen3-32b` and `moonshotai/kimi-k2-instruct` are no longer served by Groq, so they fail on every fallback. They add a wasted round trip each before the chain reaches `gpt-oss-20b`. Removing them from `GROQ_ANSWER_MODELS` is a free latency win.

### 8.1 Capacity: questions the tutor could not answer

Every model in the chain runs on a free tier, and each tier has a hard daily ceiling:

| Model tier | Free-tier limit | State during the run |
|---|---|---|
| Groq `gpt-oss-120b` (primary answer) | 200,000 tokens/day, refilling ~139 tokens/min | Exhausted partway through |
| Groq `gpt-oss-20b` (intent step + answer fallback) | 200,000 tokens/day | Exhausted partway through |
| Gemini 3.6 / 3.7 / 3.8 Flash | 20 requests/day per model | Already exhausted before the run |

**25 of 103 questions (24%) got no answer.** The endpoint returned HTTP 500 because every model in the chain refused: both Groq models over their daily token budget, and Gemini over its daily request quota. The first failure was question #79. After that the tutor could only answer while the Groq budget trickled back. Affected: [79](#q79), [80](#q80), [81](#q81), [82](#q82), [83](#q83), [84](#q84), [85](#q85), [86](#q86), [87](#q87), [88](#q88), [89](#q89), [90](#q90), [91](#q91), [92](#q92), [93](#q93), [94](#q94), [95](#q95), [96](#q96), [97](#q97), [98](#q98), [99](#q99), [100](#q100), [101](#q101), [102](#q102), [103](#q103).

These 10 textbook and 15 off-syllabus questions are **excluded from the answer-quality, citation and off-syllabus metrics**, because a quota error says nothing about whether the tutor answers correctly. They still count in the retrieval metrics (§4), which do not use the answer models. With one tutor request costing about 4,000–5,000 Groq tokens (intent + answer), the free keys serve roughly **40–50 questions a day**. That is the tutor's real capacity ceiling today, and the first thing to change before anyone else uses it.

## 9. Limitations

- **Automated grading.** Verdicts come from `qwen/qwen3.8-27b`, a 27B-parameter model, not a teacher. The manual re-grade (§5.4) measures how far to trust it. Treat differences of a few points as noise.
- **One model both wrote the questions and graded the answers.** It never graded its own *answers* (the tutor wrote those), but it may favour answers that resemble its own reference answers.
- **Synthetic questions are easier than real ones.** They sit close to the textbook's wording (§4.5), and there are no typos, Hinglish, follow-up turns or photographed problems.
- **One run, one question per chapter.** Rerunning with `--rebuild` draws a new sample. Expect each figure to move within its confidence interval.
- **Class 8 Mathematics was mislabelled "Geography" in the question-writer prompt** because of a subject-mapping bug in the eval (since fixed). The writer still saw the real maths passage, so the questions are maths questions.
- **25 questions got no answer** because the free-tier quotas ran out (§8.1). They are excluded from answer-quality metrics, which are therefore computed on 78 of 88 textbook questions. They are the questions that happened to be asked once the quota was nearly spent, not chosen by difficulty.
- **Out of scope:** voice, images, notes PDF, 3D models, quizzes and multi-turn chat were not measured.

## 10. Conclusions

1. **The automated grader rates 92% of answers fully correct** (72/78; 95% CI 84.2–96.4%), and 97% at least partially correct. A manual re-grade suggests the strict figure is closer to **~75%**: the core answer is almost always right, but roughly one answer in 5 has a wrong detail in its explanation (§5.4).
2. **Retrieval is the strongest part of the system.** The right chapter is ranked first for 97% of questions and is in the top 3 for 99%. Class filter, hybrid search and cross-encoder rerank work together here, but real students' phrasing will be harder than these questions (§4.5).
3. **Citations are reliable.** 92% of answers cite the chapter the question came from, and 100% of *NCERT verified* badges are backed by the right chapter. Most misses are answers with no citation at all (6), not wrong citations (§7.2).
4. **Off-syllabus honesty was not measured in this run.** The quota ran out before those questions were asked (§8.1).
5. **Fallback answers are as accurate, but cite less.** The primary `gpt-oss-120b` answered 58 questions (91% fully correct, 95% citing the gold chapter). Fallback models answered 20 after the primary hit its rate limit (95% fully correct, 85% citing the gold chapter). The fallback sample is small, so treat the gap as indicative (§8).
6. **The weakest area is Social Science** (84% fully correct, 31 questions). The misses are listed in §7.3.
7. **Capacity, not quality, is the binding constraint.** 25 of 103 questions got an HTTP 500 because every free-tier model was out of daily quota. On these keys the tutor serves roughly 40–50 questions a day (§8.1).
8. **Highest-value fixes**, in order: move the answer models off free tiers (a paid Groq tier alone removes the daily ceiling); drop the two dead Groq models from the answer chain; re-ingest the Class 8 Mathematics books with a clean parser and delete the chatter chunks; remove the duplicate Class 10 copy.

## Appendix A — every question and answer

Each entry shows the question exactly as sent, what the textbook passage required, what retrieval found, the tutor's full answer exactly as returned, and the grade. Click to expand.

<a id="q1"></a>
<details><summary>#1 · ✅ correct · Class 9 · Class 9 Science · What is the main function of connective tissue, and can you give two examples of it?</summary>

**Question:** What is the main function of connective tissue, and can you give two examples of it?

**Source:** Class 9 Science, chapter `iesc103`, page 8

**Reference answer:** Connective tissue is a type of tissue that connects and supports other tissues in the body. Two examples of connective tissue are blood and bones.

**Key points:** Connects and supports other tissues; Blood is an example; Bones are an example

**Retrieval top 3:** `iesc103`, `iesc103`, `iesc103` (gold chapter ranked first)

**Citations returned:** `iesc103` p.8, `iesc103` p.16 · **Badge:** Extended reference · **Answered by:** `Groq[openai/gpt-oss-120b]` · **Latency:** 5.08s

**Tutor's answer:**

~~~~text
**Direct answer**  
The main function of connective tissue is to **connect and support other tissues and organs** in the body. Two common examples are **blood** and **bone**.

**Why it works (the mechanism)**  
Connective tissue contains cells embedded in an extracellular matrix. The matrix (fibres and ground substance) provides the physical link between different parts of the body, while the cells (e.g., fibroblasts, blood cells) produce and maintain this matrix. In bone, the matrix is mineralised, giving a hard, rigid framework that supports the skeleton. In blood, the matrix is a fluid plasma that transports nutrients, gases, and waste, thereby linking distant tissues.

**Ground it in real life**  
Think of the railway network in India: the tracks (matrix) hold the trains (cells) and connect cities (organs) so people and goods can move around. Similarly, bone is like a sturdy bridge that holds the body upright, while blood is like a fast‑moving cargo train delivering supplies everywhere.

**Worked analogy**  
- **Bridge (bone)** – a solid, immovable structure that supports traffic (muscles, organs) and keeps everything stable.  
- **Cargo train (blood)** – a fluid carrier that moves passengers (oxygen, nutrients) and waste between stations (tissues).  

Both are parts of the same transportation system, just with different physical properties, just as bone and blood are both connective tissues but differ in consistency.

**Common mistake or misconception**  
Students often think connective tissue only refers to hard tissues like bone and ignore fluid tissues such as blood; in fact, both belong to the same class because they share the role of connecting and supporting the body.

**Try this →**  
Name one more connective tissue besides blood and bone and state its primary supportive role.
~~~~

**Grade:** ✅ correct — The tutor correctly identifies the main function of connective tissue as connecting and supporting other tissues, and provides blood and bones as examples, covering all key points. The additional details about the extracellular matrix and analogies are factually accurate and do not contradict the textbook.

</details>

<a id="q2"></a>
<details><summary>#2 · ✅ correct · Class 10 · Class 10 Science · Why do food chains usually have only three or four steps?</summary>

**Question:** Why do food chains usually have only three or four steps?

**Source:** Class 10 Science, chapter `jesc113`, page 3

**Reference answer:** Food chains are short because a large amount of energy is lost as heat, used for digestion, work, and growth at each trophic level. On average, only about 10% of the energy from one level is transferred to the next. After four levels, very little usable energy remains to support further consumers.

**Key points:** Energy is lost at each trophic level (as heat, for work, etc.); Only about 10% of energy is transferred to the next level; Very little usable energy remains after four trophic levels

**Retrieval top 3:** `jesc113`, `jesc113`, `jess303` (gold chapter ranked first)

**Citations returned:** `jesc113` p.3 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-120b]` · **Latency:** 5.52s

**Tutor's answer:**

~~~~text
**Direct answer**  
Food chains normally contain only three or four trophic levels because only a small fraction of the energy stored in food is passed on to the next level. Most of the energy is lost as heat, used for digestion, movement and growth, leaving insufficient energy to support many higher levels.

**Why it works (the mechanism)**  
When a producer (autotroph) captures solar energy, only about **10 %** of that energy is converted into the body matter of the primary consumer that eats it. The remaining **90 %** is lost as heat to the environment, used for metabolic work, or expelled as waste. The same 10 % rule applies at every subsequent step: secondary consumers receive only 10 % of the energy that primary consumers obtained, tertiary consumers get 10 % of that, and so on. Because the energy available drops dramatically at each level, very little usable energy remains after three or four transfers, limiting the length of the chain.

**Ground it in real life**  
Consider a typical Indian pond:  
1. **Algae** (producers) use sunlight to make food.  
2. **Zooplankton** (primary consumers) eat the algae.  
3. **Small fish** (secondary consumers) feed on zooplankton.  
4. **Bigger fish or a heron** (tertiary consumers) prey on the small fish.  
Beyond the heron, there is hardly enough energy left to support another predator, so the chain stops at about four steps.

**Worked analogy**  
Think of a salary that is taxed heavily at each promotion. Suppose a worker earns ₹100,000. After the first promotion, 10 % (₹10,000) is retained as take‑home pay, while the rest is “lost” to taxes and expenses. After a second promotion, only 10 % of that ₹10,000 (₹1,000) remains, and after a third promotion, just ₹100 is left. Just as the money dwindles quickly, the usable energy shrinks dramatically, preventing many more “promotions” (trophic levels) in a food chain.

**Common mistake or misconception**  
Students often think that the total energy stays the same through the food chain, but in reality most of it is lost as heat and waste at each step.

**Try this →**  
If 2000 kcal of energy is stored in the leaves of a plant, approximately how many kilocalories are available to a tertiary consumer that feeds on a secondary consumer? (Hint: apply the 10 % rule three times.)
~~~~

**Grade:** ✅ correct — The answer correctly identifies that energy is lost at each trophic level, explicitly states the 10% transfer rule, and explains that very little usable energy remains after four levels, covering all key points without factual errors.

</details>

<a id="q3"></a>
<details><summary>#3 · ✅ correct · Class 9 · Class 9 Science · What is the difference between sublimation and deposition?</summary>

**Question:** What is the difference between sublimation and deposition?

**Source:** Class 9 Science, chapter `iesc105`, page 18

**Reference answer:** Sublimation is the process where a solid changes directly into a vapour without becoming a liquid, which happens below its melting point. Deposition is the reverse process, where a vapour cools and changes back into a solid without passing through the liquid state.

**Key points:** Sublimation is the transition from solid to vapour.; Sublimation occurs without passing through the liquid state.; Deposition is the transition from vapour to solid.; Deposition occurs without passing through the liquid state.

**Retrieval top 3:** `iesc105`, `iesc105`, `iesc105` (gold chapter ranked first)

**Citations returned:** `iesc105` p.18 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-120b]` · **Latency:** 8.55s

**Tutor's answer:**

~~~~text
**Direct answer**  
Sublimation is the change of a solid directly into its vapour without becoming a liquid first. Deposition is the reverse process: vapour turns straight into a solid, again bypassing the liquid state.

**Why it works (the mechanism)**  
In sublimation the solid particles gain enough kinetic energy to break the intermolecular forces that hold them together, so they escape as gas even though the temperature is below the melting point. In deposition the vapour particles lose kinetic energy (usually by cooling); the reduced motion allows intermolecular forces to pull the particles together into a solid lattice, so the gas becomes a solid without forming a liquid.

**Ground it in real life**  
- Dry ice (solid CO₂) placed at room temperature disappears as a white fog – that fog is the vapour formed by sublimation.  
- Frost that appears on a cold window in the early morning is water vapour from the air turning directly into tiny ice crystals – that is deposition.

**Worked analogy**  
Think of a crowd of people at a concert.  
- In sublimation, the crowd (solid) suddenly runs out of the venue (vapour) without anyone stopping in the hallway (liquid).  
- In deposition, people outside the venue (vapour) get so cold that they immediately huddle together and form a solid line at the entrance, never walking through the hallway.  
The crowd’s movement represents the particles gaining or losing energy, and the hallway represents the liquid phase that is skipped.

**Common mistake or misconception**  
Students often mix up deposition with condensation; condensation is vapour → liquid, whereas deposition skips the liquid stage and goes straight to solid.

**Try this →**  
If you place a piece of solid iodine in a warm, dry room, what phase change will you observe first? (Answer: it will sublimate, turning directly into violet vapour.)
~~~~

**Grade:** ✅ correct — The tutor's answer correctly defines both sublimation and deposition, explicitly stating that both processes bypass the liquid state. It covers all key points from the reference answer and provides accurate additional context and examples without any factual errors.

</details>

<a id="q4"></a>
<details><summary>#4 · ✅ correct · Class 8 · Class 8 Social Science — Exploring Society: India and Beyond · What is the 'natural resource curse' and how has India managed to avoid it?</summary>

**Question:** What is the 'natural resource curse' and how has India managed to avoid it?

**Source:** Class 8 Social Science — *Exploring Society: India and Beyond*, chapter `hees101`, page 11

**Reference answer:** The 'natural resource curse' is a phenomenon where regions with abundant natural resources experience slower economic growth because they fail to develop industries that convert these resources into higher-value products. India has generally avoided this curse by investing in the development of such industries to meet its growing needs.

**Key points:** It refers to slower economic growth despite having abundant natural resources.; It happens because economies fail to develop industries to convert resources into higher-value products.; India avoided it by investing in the development of such industries.

**Retrieval top 3:** `hees101`, `hees101`, `hees101` (gold chapter ranked first)

**Citations returned:** `hees101` p.11 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-120b]` · **Latency:** 8.66s

**Tutor's answer:**

~~~~text
**Direct answer**  
The ‘natural resource curse’ (or ‘paradox of plenty’) is the situation where a country that is rich in natural resources does not become economically prosperous because it fails to turn those resources into higher‑value products. India has largely avoided this trap by investing in industries that process its resources and by diversifying its economy.

**Why it works (the mechanism)**  
- When a resource is abundant, an economy may rely heavily on exporting the raw material, neglecting other sectors such as manufacturing or services.  
- This over‑reliance can lead to weak institutions, low technological development, and volatile earnings.  
- India countered these risks by:  
  1. Building factories and plants that convert raw resources (e.g., coal, iron ore) into finished goods like steel, cement, and electricity.  
  2. Encouraging research, skill development, and infrastructure that support value‑addition.  
  3. Diversifying into sectors such as information technology, agriculture, and services, so growth does not hinge on a single resource.

**Ground it in real life**  
Think of the coal we use for electricity. Instead of just burning it and discarding the ash, India has set up power plants, steel mills, and cement factories that transform the same coal into electricity, steel rods for bridges, and cement for houses—products that are far more valuable than raw coal alone.

**Fresh analogy**  
Imagine a kitchen stocked with a huge variety of fresh vegetables. If you only eat them raw, you get limited nutrition and taste. But if you cook, ferment, or blend them into soups, pickles, and salads, you obtain a richer, more diverse diet. Similarly, a country with abundant “raw vegetables” (natural resources) must “cook” them—process and add value—to reap greater economic benefits.  

**Common mistake**  
Students often assume that simply possessing large reserves of a resource automatically makes a nation rich; the key is how those resources are utilized and added value to.

**Try this →**  
Can you name a country that has struggled with the natural resource curse and suggest one way it could add value to its resources?
~~~~

**Grade:** ✅ correct — The tutor's answer correctly defines the natural resource curse as slower growth despite abundance due to a failure to convert resources into higher-value products. It accurately states that India avoided this by investing in industries to process these resources, covering all key points from the textbook passage without factual errors.

</details>

<a id="q5"></a>
<details><summary>#5 · ✅ correct · Class 8 · Class 8 Social Science — Exploring Society: India and Beyond · How big is the scale of the Lok Sabha elections in India, and who helps manage it?</summary>

**Question:** How big is the scale of the Lok Sabha elections in India, and who helps manage it?

**Source:** Class 8 Social Science — *Exploring Society: India and Beyond*, chapter `hees105`, page 12

**Reference answer:** The Lok Sabha elections are massive, involving about 980 million voters, 543 parliamentary constituencies, and over 1 million polling stations. To handle this huge task, the Election Commission of India upgrades its capacities and deploys many people, including teachers, to assist in the process.

**Key points:** Approximately 980 million voters; 543 parliamentary constituencies; Over 1 million polling stations; Teachers and many others are deployed to assist

**Retrieval top 3:** `hees105`, `hees105`, `hees105` (gold chapter ranked first)

**Citations returned:** `hees105` p.12, `hees105` p.3, `hees105` p.5 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-120b]` · **Latency:** 8.51s

**Tutor's answer:**

~~~~text
**Direct answer**  
The 2024 Lok Sabha election involved roughly **980 million eligible voters**, spread over **543 parliamentary constituencies** and more than **1 million polling stations**. The Election Commission of India (ECI) oversees the process, and a huge workforce—including teachers and other volunteers—helps run it smoothly.

**Why it works (the mechanism)**  
Because the electorate is enormous, the ECI must break the country into manageable units: each constituency (543 in total) contains many polling stations (over a million) where voters cast their ballots. To staff each station, the commission recruits a large pool of election officials and auxiliary personnel. Teachers are often enlisted because they are organized, familiar with local communities, and can be quickly mobilised to assist on election day.

**Ground it in real life**  
Imagine a school with 2,000 students needing to take an exam simultaneously. You would divide the school into many classrooms, assign a teacher to each room, and bring in extra staff to supervise. Now scale that up to a nation where the “students” are 980 million people and the “classrooms” are over a million polling booths—that’s why the election effort looks massive.

**Worked analogy**  
Think of the election as a gigantic **relay race**:
- **Runners** = the 980 million voters who must each receive the baton (the ballot).  
- **Tracks** = the 543 constituencies that guide the runners along a set route.  
- **Exchange zones** = the 1 million polling stations where the baton is handed over safely.  
- **Coaches and support crew** = the ECI, election officials, and teachers who ensure every runner knows the route, the baton is passed correctly, and no one is left behind.  
Just as a relay race succeeds only when every segment is well‑coordinated, the election works only when all these parts function together.

**Common mistake or misconception**  
Students often think only a few officials run the election, but in reality a vast network of volunteers—especially teachers—plays a crucial role.

**Try this →**  
If the number of polling stations were doubled while the number of voters stayed the same, would each station handle more, fewer, or the same number of voters on average? (Think it through.)
~~~~

**Grade:** ✅ correct — The tutor's answer accurately includes all four key points: approximately 980 million voters, 543 constituencies, over 1 million polling stations, and the deployment of teachers/officials. The additional analogies and explanations are factually sound and do not contradict the textbook.

</details>

<a id="q6"></a>
<details><summary>#6 · ✅ correct · Class 10 · Class 10 Political Science — Democratic Politics II · Why is India considered a federation according to the constitutional structure?</summary>

**Question:** Why is India considered a federation according to the constitutional structure?

**Source:** Class 10 Political Science — *Democratic Politics II*, chapter `jess402`, page 16

**Reference answer:** India is a federation because the powers of the Union and State Governments are clearly specified in the Constitution. Each level of government has exclusive jurisdiction over its respective subjects, ensuring a clear demarcation of powers.

**Key points:** Powers of Union and State Governments are specified in the Constitution; Governments have exclusive jurisdiction on their respective subjects; Powers are clearly demarcated between federal and provincial/state levels

**Retrieval top 3:** `jess402`, `jess402`, `jess402` (gold chapter ranked first)

**Citations returned:** `jess402` p.4, `jess402` p.7 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-120b]` · **Latency:** 10.32s

**Tutor's answer:**

~~~~text
**Direct answer**  
India is called a federation because its Constitution creates a Union of States with a two‑tier system of government, dividing powers between the Centre and the individual States.

**Why it works (the mechanism)**  
The Constitution lists subjects in three categories – Union List, State List and Concurrent List. Each list assigns exclusive or shared authority to either the Central government or the State governments. This separation of powers, together with the term “Union of States” used in the preamble, embodies the core principle of federalism: a balance of authority between a central authority and regional units.

**Ground it in real life**  
Think of a large Indian railway station. The railway board (the Centre) decides on the overall schedule, safety standards and ticket pricing, while each platform manager (the State) looks after the cleanliness, local announcements and platform‑specific crowd control. Both work together, but each has its own clearly defined duties.

**Worked analogy**  
Imagine a cricket team where the captain (the Centre) decides the overall strategy, selects the playing XI and sets the field placements. The regional coaches (the States) train the players, manage fitness, and decide on local practice drills. The captain cannot micromanage every drill, and the coaches cannot change the team’s overall strategy. This division of responsibilities mirrors how India’s federal structure allocates powers.

**Common mistake or misconception**  
A frequent error is to think that because the Constitution never uses the word “federation,” India cannot be a federation; the structural division of powers is what defines a federation, not the terminology.

**Try this →**  
If the Constitution gave the Centre exclusive power over education, would India still be a federation? (Think about the need for separate State powers.)
~~~~

**Grade:** ✅ correct — The answer correctly identifies that India is a federation because the Constitution specifies the powers of the Union and State governments, ensuring exclusive jurisdiction and clear demarcation of powers. It accurately reflects the key points from the reference answer and textbook passage without factual errors.

</details>

<a id="q7"></a>
<details><summary>#7 · ✅ correct · Class 9 · Class 9 Science · Why are atoms considered electrically neutral even though they contain protons and electro…</summary>

**Question:** Why are atoms considered electrically neutral even though they contain protons and electrons?

**Source:** Class 9 Science, chapter `iesc108`, page 6

**Reference answer:** Atoms are electrically neutral because the total positive charge from the protons is equal to the total negative charge from the electrons. This balance of charges results in a net charge of zero for the atom.

**Key points:** Total positive charge equals total negative charge; Protons provide positive charge; Electrons provide negative charge; Net charge is zero

**Retrieval top 3:** `iesc108`, `iesc108`, `iesc108` (gold chapter ranked first)

**Citations returned:** `iesc108` p.6, `iesc108` p.8 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-20b]` · **Latency:** 10.31s

**Tutor's answer:**

~~~~text
**Direct answer**  
An atom is electrically neutral because the total positive charge from its protons is exactly balanced by the total negative charge from its electrons. Since the charges cancel each other out, the net charge of the atom is zero.

**Why it works (the mechanism)**  
A proton carries a charge of +1 e (electron‑charge unit) and an electron carries –1 e. In a neutral atom the number of protons equals the number of electrons, so the sum of all charges is  
$$(+1)\times \text{(number of protons)} + (-1)\times \text{(number of electrons)} = 0.$$  
Neutrons, which have no charge, do not affect this balance. Thus the atom’s overall charge is zero.

**Ground it in real life**  
Think of a small village where every house has one boy and one girl. The boys and girls together keep the village balanced; no side dominates. Similarly, in an atom the protons (boys) and electrons (girls) keep the electrical balance, making the atom neutral.

**Worked example or fresh analogy**  
Suppose a helium atom has 2 protons and 2 electrons.  
Positive charge = $+1 \times 2 = +2$  
Negative charge = $-1 \times 2 = -2$  
Net charge = $+2 + (-2) = 0$.  
So the helium atom is neutral.  
In the village analogy, if the village had 2 boys and 2 girls, the numbers match and the village remains balanced.

**Common mistake or misconception**  
Students often think that because protons are inside the nucleus and electrons orbit outside, the atom must be charged. In reality, the charges are equal in magnitude and opposite in sign, so they cancel out.

**Try this →**  
How many electrons would a sodium atom (with 11 protons) need to be electrically neutral?
~~~~

**Grade:** ✅ correct — The answer correctly explains that atoms are neutral because the total positive charge from protons equals the total negative charge from electrons, resulting in a net charge of zero. It covers all required key points and provides accurate examples without factual errors.

</details>

<a id="q8"></a>
<details><summary>#8 · ✅ correct · Class 10 · Class 10 History — India and the Contemporary World II · How do we know that oceanic trade was important on the western coast of India starting fro…</summary>

**Question:** How do we know that oceanic trade was important on the western coast of India starting from the ninth century?

**Source:** Class 10 History — *India and the Contemporary World II*, chapter `jess303`, page 3

**Reference answer:** The significance of oceanic trade is indicated by the regular appearance of ship images on memorial stones found along the western coast from the ninth century onwards. An example of such an image is preserved in the Goa Museum.

**Key points:** Images of ships appear regularly on memorial stones; These stones are found on the western coast; This trend started from the ninth century

**Retrieval top 3:** `jess303`, `jess303`, `jess303` (gold chapter ranked first)

**Citations returned:** `jess303` p.3, `jess303` p.16 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-120b]` · **Latency:** 7.33s

**Tutor's answer:**

~~~~text
**Direct answer**  
Archaeologists have found many ship carvings on memorial stones along India’s western coast that date back to the ninth century. The regular appearance of these images shows that oceanic trade was already a prominent activity there.

**Why it works (the mechanism)**  
*Memorial stones* (stone slabs erected to commemorate the dead) often carry decorative motifs that reflect the life and times of the people who erected them. When traders or seafarers commissioned such stones, they frequently included *ship images* to symbolize their livelihood. The repeated presence of these ship motifs from the ninth century onward provides concrete, datable evidence that maritime commerce was a routine and valued part of coastal society.

**Ground it in real life**  
Think of a modern family photo album: if many pictures show a member driving a bike, you can infer that biking is a common activity for that family. Similarly, the “photo album” of the past—these stone carvings—repeatedly shows ships, telling us that sailing and trade were common on the western coast.

**Worked analogy**  
Imagine a school wall where students post stickers of their favorite sports. If, over several years, you see a steady stream of cricket stickers and only a few football stickers, you conclude cricket is the dominant sport. In the same way, the “wall” of memorial stones displays many ship stickers (carvings) from the ninth century onward, indicating that maritime trade was the dominant “sport” of the coastal community.

**Common mistake or misconception**  
Students sometimes think that because written records are scarce, trade must have been insignificant; however, material evidence like these ship carvings provides a reliable indicator of active oceanic commerce.

**Try this →**  
If you saw a memorial stone from the 10th century with a ship carving, what would that suggest about the local economy at that time?
~~~~

**Grade:** ✅ correct — The tutor's answer correctly identifies that images of ships appear regularly on memorial stones found on the western coast starting from the ninth century, covering all key points without factual errors.

</details>

<a id="q9"></a>
<details><summary>#9 · ✅ correct · Class 10 · Class 10 Science · What happens to the body if a child doesn't get enough growth hormone, and which gland mak…</summary>

**Question:** What happens to the body if a child doesn't get enough growth hormone, and which gland makes it?

**Source:** Class 10 Science, chapter `jesc106`, page 11

**Reference answer:** Growth hormone is secreted by the pituitary gland and regulates the growth and development of the body. If there is a deficiency of this hormone during childhood, it leads to a condition called dwarfism.

**Key points:** Growth hormone is secreted by the pituitary gland; It regulates growth and development; Deficiency in childhood leads to dwarfism

**Retrieval top 3:** `jesc106`, `jesc106`, `jesc106` (gold chapter ranked first)

**Citations returned:** `jesc106` p.11 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-120b]` · **Latency:** 7.73s

**Tutor's answer:**

~~~~text
**Direct answer**  
If a child does not receive enough growth hormone, their growth and overall development are impaired, often resulting in a condition called dwarfism (very short stature). The hormone is secreted by the pituitary gland.

**Why it works (the mechanism)**  
Growth hormone (GH) is released by the pituitary gland into the bloodstream. It stimulates the liver and other tissues to produce insulin‑like growth factor‑1 (IGF‑1), which promotes the multiplication of cells and the lengthening of bones, especially at the growth plates. When GH is lacking, IGF‑1 levels stay low, so the growth plates receive insufficient signals to lengthen, and the child’s height remains far below the normal range for age.

**Ground it in real life**  
Think of a school building under construction. The pituitary gland is the foreman who orders the delivery of bricks (IGF‑1). If the foreman stops ordering bricks, the workers cannot add new floors, and the building stays short. Similarly, without enough GH, the body cannot add the “floors” (bone length) needed for normal height.

**Worked analogy**  
Imagine a garden where a sprinkler (GH) waters the plants (bones). The water triggers the plants to grow taller. If the sprinkler is turned off, the plants receive no water and stay short. In the body, the pituitary’s GH is the sprinkler, and the bones are the plants that need that signal to grow.

**Common mistake or misconception**  
Students often think that a lack of GH only makes a child “thin” or “weak”; the key effect is stunted linear growth, leading to dwarfism, while other body functions may remain normal.

**Try this →**  
If a child’s growth hormone is missing, which part of the body (the growth plates in long bones) fails to receive the signal to lengthen?
~~~~

**Grade:** ✅ correct — The answer correctly identifies the pituitary gland as the source of growth hormone, states that it regulates growth and development, and explains that deficiency in childhood leads to dwarfism. The additional mechanistic details (IGF-1, growth plates) are factually accurate and do not contradict the textbook.

</details>

<a id="q10"></a>
<details><summary>#10 · ✅ correct · Class 8 · Class 8 Science — Curiosity · How can we tell that a mixture of iron filings and sulfur is a mixture and not a compound,…</summary>

**Question:** How can we tell that a mixture of iron filings and sulfur is a mixture and not a compound, based on its reaction with a magnet and acid?

**Source:** Class 8 Science — *Curiosity*, chapter `hecu108`, page 13

**Reference answer:** In the mixture, the iron filings retain their magnetic properties and can be separated using a magnet, whereas in a compound, the elements lose their individual properties. Additionally, when dilute hydrochloric acid is added to the mixture, only the iron reacts to produce hydrogen gas, which burns with a 'pop' sound, showing that the components remain distinct.

**Key points:** Iron filings in the mixture are attracted to a magnet and can be separated.; The components of the mixture retain their individual properties.; Only the iron in the mixture reacts with dilute hydrochloric acid to produce hydrogen gas.

**Retrieval top 3:** `hecu108`, `hecu108`, `hecu108` (gold chapter ranked first)

**Citations returned:** `hecu108` p.17, `hecu108` p.13, `hecu108` p.10 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-120b]` · **Latency:** 6.22s

**Tutor's answer:**

~~~~text
**Direct answer**  
The iron‑sulfur sample is a mixture because the iron filings are attracted to a magnet while the sulfur is not, and when dilute hydrochloric acid is added only the iron part reacts to give iron chloride, leaving the sulfur unchanged. A true compound would show a single, uniform response to both tests.

**Why it works (the mechanism)**  
- **Magnet test:** Iron is a ferromagnetic element; its particles are pulled toward a magnet. Sulfur is non‑magnetic, so it remains unaffected. In a mixture the two substances retain their individual magnetic properties, allowing the magnetic iron to be separated.  
- **Acid test:** Dilute hydrochloric acid reacts with iron (or iron sulfide) to produce iron chloride and hydrogen gas (or hydrogen sulfide in the case of iron sulfide). In the original iron‑filings + sulfur mixture, only the iron filings react, while the sulfur does not dissolve or change, showing that the components behave independently. A compound would have a single, characteristic reaction, not separate reactions of its constituent elements.

**Ground it in real life**  
Think of a bag of mixed sand and iron nails. When you pass a magnet over the bag, the nails stick while the sand falls through. Similarly, if you pour a little vinegar (acid) on the bag, the nails may rust (react) but the sand stays unchanged. The distinct behaviours tell you the bag contains a mixture, not a new material.

**Worked analogy**  
Imagine a fruit salad made of apple slices and orange segments. If you use a fruit‑picker that only grabs apples, the oranges stay behind. If you add lemon juice, only the apples may turn brown (react) while the oranges keep their taste. The fact that each fruit keeps its own property shows the salad is a mixture, not a single “fruit‑blend” that would behave uniformly.

**Common mistake**  
Students often think that because two substances are mixed together they automatically form a compound; forgetting that the original substances keep their individual physical and chemical properties leads to this error.

**Try this →**  
If you place a magnet near a sample of iron filings mixed with sand, what happens to each component? (Answer in one sentence.)
~~~~

**Grade:** ✅ correct — The answer correctly identifies that iron filings are attracted to a magnet and can be separated, that components retain their individual properties, and that only the iron reacts with dilute hydrochloric acid to produce hydrogen gas. It accurately distinguishes the mixture from a compound based on these observations without factual errors.

</details>

<a id="q11"></a>
<details><summary>#11 · ✅ correct · Class 10 · Class 10 Economics — Understanding Economic Development · How does underemployment show up in the service sector in cities, and why do people end up…</summary>

**Question:** How does underemployment show up in the service sector in cities, and why do people end up doing these jobs?

**Source:** Class 10 Economics — *Understanding Economic Development*, chapter `jess202`, page 9

**Reference answer:** In urban service sectors, underemployment is seen in casual workers like painters or plumbers who search for daily work and often go without it, or street vendors who work all day for very little money. These individuals engage in such low-paying, irregular work because they lack access to better employment opportunities.

**Key points:** Casual workers in urban service sectors (e.g., painters, plumbers) often cannot find work every day.; Street vendors or cart pushers may work all day but earn very little.; The primary reason for this underemployment is the lack of better opportunities.

**Retrieval top 3:** `jess202`, `jess202`, `jess202` (gold chapter ranked first)

**Citations returned:** `jess202` p.9 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-120b]` · **Latency:** 7.39s

**Tutor's answer:**

~~~~text
**Direct answer**  
In cities, under‑employment appears as large numbers of casual workers in the service sector who take on daily‑wage jobs such as painters, plumbers, repair persons, street‑vendors or cart‑pushers. They often work only a few hours or earn very little because they do not have access to regular, better‑paid jobs.

**Why it works (the mechanism)**  
*Under‑employment* means people are employed but work less than their capacity or potential, a hidden form of unemployment. In the urban unorganised sector, most jobs are irregular and low‑paid. When formal factories shut or when there are insufficient skilled positions, workers move to any available work—odd jobs, vending, or daily‑wage tasks—to earn something. The lack of stable employment opportunities forces them into these precarious roles.

**Ground it in real life**  
Imagine a plumber in Mumbai who can handle complex installations but, because construction projects are scarce, he spends his mornings fixing a leaking tap for a neighbour and his afternoons selling bottled water on a street corner. He is earning, but far below what his skills could command.

**Worked analogy**  
Think of a school student who is excellent at mathematics but, because the school only offers a few math worksheets each week, the student ends up solving only simple addition problems and spends the rest of the time copying notes for other subjects. The student’s ability is under‑used, just as a skilled worker’s capacity is under‑used when limited to odd, low‑pay jobs. Both situations illustrate hidden under‑employment.

**Common mistake or misconception**  
Students often think under‑employment means having no job at all, whereas it actually refers to having a job that does not fully utilise one’s skills or time.

**Try this →**  
If a factory closes and 50 workers become street‑vendors, what does this shift illustrate about under‑employment in the urban service sector?
~~~~

**Grade:** ✅ correct — The tutor's answer correctly identifies casual workers (painters, plumbers) and street vendors as examples of underemployment in the urban service sector, noting their irregular work and low earnings. It also accurately states that the primary reason for this situation is the lack of better opportunities, covering all key points from the reference answer and textbook passage.

</details>

<a id="q12"></a>
<details><summary>#12 · ✅ correct · Class 9 · Class 9 Science · Why is it important to use standard SI units like kilograms instead of local or different…</summary>

**Question:** Why is it important to use standard SI units like kilograms instead of local or different units for measuring things?

**Source:** Class 9 Science, chapter `iesc101`, page 3

**Reference answer:** Using standard SI units avoids confusion and errors that can arise from converting between different units, such as pounds and kilograms. It ensures that measurements mean the same amount everywhere, which is crucial for fairness in daily life and trade. Additionally, standard units allow scientific results to be compared consistently across different places.

**Key points:** Avoids conversions and errors; Ensures measurements are consistent everywhere; Allows scientific results to be compared; Ensures fairness in daily life and trade

**Retrieval top 3:** `iesc101`, `iesc108`, `iesc105` (gold chapter ranked first)

**Citations returned:** `iesc101` p.3, `iesc108` p.15, `iest108` p.8 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-20b]` · **Latency:** 9.85s

**Tutor's answer:**

~~~~text
**Direct answer**  
Using standard SI units, like kilograms, keeps measurements consistent everywhere. It eliminates the need for conversions, reduces errors, and lets people compare results or trade goods fairly.

**Why it works (the mechanism)**  
When everyone uses the same unit, a quantity measured in one place can be directly understood in another. If a shop in Mumbai sells 1 kg of rice and a shop in Kolkata sells 1 kg, the buyer knows they are getting the same mass. If local units varied, a buyer would have to convert, risking mistakes. Standard units also form the basis of scientific equations, so data from different experiments can be combined without extra steps.

**Ground it in real life**  
Imagine buying a packet of spices in Delhi and another in Chennai. Both say “1 kg” on the label. You can trust that the weight is the same, so you can compare prices or mix them in a recipe. If Delhi used “pound” and Chennai used “kilogram”, you’d need to remember that 1 lb ≈ 0.45 kg, and a slip could cost you money or spoil a dish.

**Worked example or fresh analogy**  
Suppose a farmer sells 5 kg of wheat in a market. A buyer in another town uses “pound” as the local unit.  
1 kg ≈ 2.204 lb, so 5 kg ≈ 11.02 lb.  
If the buyer mistakenly thinks 5 kg is 5 lb, they will pay for only a little more than half the wheat. Using the same unit (kg) avoids this confusion.

**Common mistake or misconception**  
Students often think local units are fine because they’re familiar, but this leads to hidden errors when comparing or trading across regions.

**Try this →**  
If a shop in Bangalore sells 2 kg of sugar and a shop in Hyderabad sells 4 lb, how many kilograms of sugar does the Hyderabad shop have?
~~~~

**Grade:** ✅ correct — The tutor's answer explicitly covers all four key points: avoiding conversions/errors, ensuring consistency everywhere, allowing scientific comparison, and ensuring fairness in trade. The examples provided are factually accurate and align with the textbook passage.

</details>

<a id="q13"></a>
<details><summary>#13 · ✅ correct · Class 9 · Class 9 Social Science — Understanding Society: India and Beyond · What are the main gases that make up the Earth's atmosphere and what are their approximate…</summary>

**Question:** What are the main gases that make up the Earth's atmosphere and what are their approximate percentages?

**Source:** Class 9 Social Science — *Understanding Society: India and Beyond*, chapter `iest103`, page 2

**Reference answer:** The Earth's atmosphere is primarily composed of nitrogen and oxygen, which are essential for life. Nitrogen makes up about 78% of the atmosphere, while oxygen accounts for 21%. The remaining small portion consists of other gases like argon, carbon dioxide, and water vapour.

**Key points:** Nitrogen is the most abundant gas at 78%; Oxygen is the second most abundant gas at 21%; Other gases like argon and carbon dioxide make up the remaining small percentage

**Retrieval top 3:** `iest103`, `iesc113`, `iesc113` (gold chapter ranked first)

**Citations returned:** `iest103` p.2, `iesc113` p.6, `iesc113` p.15 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-120b]` · **Latency:** 15.94s

**Tutor's answer:**

~~~~text
**Direct answer**  
The Earth’s atmosphere is dominated by nitrogen (about 78 %) and oxygen (about 21 %). The remaining ≈ 1 % consists mainly of argon (≈ 0.93 %), carbon‑dioxide (≈ 0.04 %), and a small, variable amount of water vapour (roughly 0.1–0.4 %).  

**Why it works (the mechanism)**  
The gases are mixed by turbulent motions in the lower atmosphere (the troposphere). Because nitrogen and oxygen are the lightest major constituents that are not chemically reactive, they accumulate in the largest proportions. Argon, a noble gas, is chemically inert and therefore remains unchanged, giving a steady ~0.93 % share. Carbon‑dioxide, though produced by respiration and combustion, is present in a much smaller amount because it is continuously taken up by plants during photosynthesis. Water vapour is not a fixed component; its concentration changes with temperature and location, ranging from 0.1 % over dry regions to about 0.4 % in humid, tropical air.  

**Ground it in real life**  
When you breathe in, you inhale air that is roughly three‑quarters nitrogen and one‑quarter oxygen – the same mix that keeps fires burning in a kitchen stove. The tiny amount of argon is harmless and invisible, while the trace carbon‑dioxide you exhale is part of the same cycle that plants use to grow.  

**Worked analogy**  
Think of the atmosphere as a large bowl of mixed fruit salad.  
- **Nitrogen** is the bulk fruit, like a lot of bananas, making up most of the bowl (78 %).  
- **Oxygen** is the next big fruit, say apples, filling about one‑fifth of the bowl (21 %).  
- **Argon** and **carbon‑dioxide** are the few grapes and berries sprinkled on top (≈ 1 % total).  
- **Water vapour** is the drizzle of syrup that varies in amount depending on how hot the kitchen is (0.1–0.4 %).  
Just as the salad’s taste is determined by the dominant fruits, the atmosphere’s physical and chemical behavior is set by its dominant gases.  

**Common mistake or misconception**  
Students often think carbon‑dioxide makes up a large part of air because it is frequently mentioned in climate discussions; in reality, it is only about 0.04 % of the total volume.  

**Try this →**  
If you had 100 L of dry air at sea level, how many liters of nitrogen would it contain? (Answer in your head.)
~~~~

**Grade:** ✅ correct — The tutor correctly identifies nitrogen (78%) and oxygen (21%) as the primary gases and lists argon and carbon dioxide as the main components of the remaining percentage, aligning perfectly with the textbook passage and key points. The additional explanations and analogies are factually accurate and do not contradict the source material.

</details>

<a id="q14"></a>
<details><summary>#14 · ✅ correct · Class 10 · Class 10 Economics — Understanding Economic Development · Why is it important to consider non-material factors like respect and security alongside i…</summary>

**Question:** Why is it important to consider non-material factors like respect and security alongside income when thinking about a good life?

**Source:** Class 10 Economics — *Understanding Economic Development*, chapter `jess201`, page 4

**Reference answer:** While money is necessary for survival, the quality of life also depends on non-material aspects such as equal treatment, freedom, and security. These factors are often more important than material goods because they address needs that cannot be bought, such as the value of friendship and social respect.

**Key points:** Quality of life depends on both material and non-material factors; Non-material factors include equal treatment, freedom, security, and respect; Material goods alone are not sufficient for a complete life

**Retrieval top 3:** `jess201`, `jess201`, `jess201` (gold chapter ranked first)

**Citations returned:** `jess201` p.4, `jess201` p.9 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-120b]` · **Latency:** 6.4s

**Tutor's answer:**

~~~~text
**Direct answer**  
A good life is not measured only by how much money one earns. Respect, security, freedom and equal treatment give people dignity and peace of mind, which are essential for overall well‑being even when income is high.

**Why it works (the mechanism)**  
Income provides the material means to buy food, shelter and other goods, but *non‑material factors* such as respect and security satisfy psychological and social needs. When people feel respected, they experience self‑esteem; when they feel secure, they are free from fear of loss or violence. These needs are part of the broader concept of *quality of life*, which combines both material and non‑material dimensions. If either side is missing, the overall satisfaction drops, because human well‑being requires both physical comfort and emotional/social fulfillment.

**Ground it in real life**  
Think of a farmer who earns a good profit from his crops. If his market price falls tomorrow or he faces a threat of land grabbing, his sense of security vanishes, and the money he earned no longer guarantees a peaceful life. Similarly, a well‑paid employee who is constantly insulted at work may feel unhappy despite a high salary.

**Fresh analogy**  
Imagine a *two‑wheel bicycle*: one wheel represents income (the material side) and the other wheel represents respect, security and freedom (the non‑material side). If only the front wheel turns while the rear wheel is stuck, the bike wobbles and cannot move smoothly. Both wheels must rotate together for a balanced, steady ride. Likewise, a balanced life needs both material resources and non‑material well‑being.

**Common mistake or misconception**  
Students often think that more money automatically guarantees happiness, overlooking that without respect and security, material wealth cannot deliver true satisfaction.

**Try this →**  
If a person earns a high salary but feels unsafe at home, would you say their life is “good”? Explain in one sentence.
~~~~

**Grade:** ✅ correct — The answer correctly identifies that quality of life depends on both material and non-material factors, explicitly lists the non-material factors (respect, security, freedom, equal treatment) mentioned in the textbook, and explains that material goods alone are insufficient for a complete life. It aligns perfectly with the provided passage and reference answer.

</details>

<a id="q15"></a>
<details><summary>#15 · ✅ correct · Class 9 · Class 9 Social Science — Understanding Society: India and Beyond · Was the varna system in early Vedic times strictly based on birth, and what factors actual…</summary>

**Question:** Was the varna system in early Vedic times strictly based on birth, and what factors actually determined a person's social identity?

**Source:** Class 9 Social Science — *Understanding Society: India and Beyond*, chapter `iest105`, page 24

**Reference answer:** No, early Vedic texts do not indicate a fixed social status strictly based on birth. Instead, social identity was shaped by complex and overlapping factors such as ethnicity, geographic region, village affiliation, gotra, language, and especially occupation. At this stage, occupations were flexible and mobile rather than strictly hereditary.

**Key points:** Early Vedic texts did not show a fixed status strictly based on birth.; Social identity was shaped by factors like ethnicity, region, gotra, language, and occupation.; Occupations were flexible and mobile, not strictly hereditary.

**Retrieval top 3:** `iest105`, `iest105`, `iest105` (gold chapter ranked first)

**Citations returned:** `iest105` p.25 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-120b]` · **Latency:** 11.24s

**Tutor's answer:**

~~~~text
**Direct answer**  
In early Vedic society the varṇa of a person was not fixed solely by birth. It was mainly linked to one’s occupation, knowledge, deeds, and the kind of power or wealth a person possessed. Over time, social identity became more rigid through factors such as endogamous marriage, inter‑varṇa intermarriage, and regional differences, giving rise to the later jati system.

**Why it works (the mechanism)**  
- **Functional basis**: The four varṇas (Brahmin, Kshatriya, Vaishya, Shudra) were originally defined by the *functions* people performed in society—learning and religious duties, protection and warfare, trade and agriculture, and service work.  
- **Deeds over birth**: Texts like the Buddhist Sutta Nipāta stress that a Brahmin is identified by his actions, not by lineage. Hence, knowledge, political power, and wealth could elevate a person’s status.  
- **Emergence of jati**: As communities migrated and began marrying within their own groups (*endogamy*), distinct hereditary groups (jātis) formed. Inter‑varṇa marriages and territorial isolation reinforced these hereditary identities, making birth a stronger determinant later on.

**Ground it in real life**  
Think of a village market where a skilled carpenter, a successful trader, and a learned priest each earn respect because of what they do, not because of the family they were born into. Over generations, if the carpenter’s children only marry other carpenters and stay in the same area, the community starts labeling them as a distinct “carpenter jati,” and birth begins to matter more than skill.

**Analogy**  
Imagine a school where students are placed in clubs based on their talents: a science club for those who love experiments, a sports club for athletes, an arts club for painters, and a service club for helpers. Initially, a student can join any club by showing ability. Later, if the school decides that children of current club members must stay in the same club, the clubs become hereditary groups. The original functional grouping (talent) shifts to a birth‑based system (family tradition). This mirrors how early varṇa (function) turned into jati (birth‑based) over time.

**Common mistake or misconception**  
Students often think the varṇa system was always a rigid birth‑based hierarchy; in fact, early Vedic varṇa was fluid and based on occupation and deeds.

**Try this →**  
If a person in early Vedic times became a great scholar, how would that affect his varṇa status according to the functional view?
~~~~

**Grade:** ✅ correct — The tutor correctly states that early Vedic varna was not strictly based on birth but on occupation and other factors, and notes that occupations were flexible/mobile (implied by 'fluid' and 'functional basis'). It covers all key points from the textbook passage without factual errors.

</details>

<a id="q16"></a>
<details><summary>#16 · ❌ incorrect · Class 10 · Class 10 Economics — Understanding Economic Development · What is the right to choose for consumers, and can you give an example of how it might be…</summary>

**Question:** What is the right to choose for consumers, and can you give an example of how it might be violated?

**Source:** Class 10 Economics — *Understanding Economic Development*, chapter `jess205`, page 8

**Reference answer:** The right to choose means that any consumer, regardless of age, gender, or the type of service, has the freedom to decide whether to continue receiving a service or buying a product. This right is violated when a seller forces a customer to buy an unwanted item, such as a shopkeeper insisting that a customer must buy a toothbrush to get toothpaste, or a gas dealer requiring the purchase of a specific stove for a new connection.

**Key points:** Consumers have the right to decide whether to continue receiving a service or buying a product.; This right applies to all consumers regardless of age, gender, or nature of service.; Violation occurs when customers are forced to buy unwanted items (e.g., buying a brush with toothpaste or a stove with gas connection).

**Retrieval top 3:** `jess205`, `jess205`, `jess205` (gold chapter ranked first)

**Citations returned:** `jess205` p.9 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-120b]` · **Latency:** 5.61s

**Tutor's answer:**

~~~~text
**Direct answer**  
The right to choose lets a consumer pick any product or service after getting correct information about it, without being forced or misled. It is violated when a seller gives false or incomplete information, making the consumer buy something they would not have chosen otherwise.

**Why it works (the mechanism)**  
When consumers are aware of their rights, they can compare options, check labels, and ask questions. This informed decision‑making prevents unfair trade practices. If a seller hides facts or advertises falsely, the consumer cannot make a genuine choice, so the market fails to protect the buyer’s interest.

**Ground it in real life**  
Imagine buying a packet of rice that claims “615 g more in every 500 g pack.” The claim is impossible; the consumer is misled into thinking they are getting extra quantity, so the purchase is not based on true information.

**Worked example or fresh analogy**  
Think of a school cafeteria offering two types of sandwiches. If the menu says the veg sandwich contains “fresh lettuce” but actually uses wilted leaves, students cannot truly decide which sandwich is healthier. The false description removes the right to choose the better option, just as a deceptive advertisement does for shoppers in a market.

**Common mistake or misconception**  
Students often think any advertisement is acceptable; they forget that misleading claims directly breach the consumer’s right to choose.

**Try this →**  
If a mobile phone advertisement says “battery lasts 48 hours” but the phone actually lasts only 30 hours, does this violate the consumer’s right to choose? Why?
~~~~

**Grade:** ❌ incorrect — The tutor's answer fundamentally misinterprets the 'Right to Choose' by describing it as a right to be informed rather than a right to be free from coercion. It fails to mention the key concept of being forced to buy unwanted goods, which is the central definition and example provided in the textbook passage.  
**Factual errors:** The tutor defines the right to choose as the ability to pick a product 'after getting correct information' and states it is violated when a seller gives 'false or incomplete information'. This conflates the Right to Choose with the Right to Information. According to the provided textbook passage, the Right to Choose is violated when a consumer is *forced* to buy an unwanted item (e.g., buying a brush with toothpaste or a stove with a gas connection), not merely when they are misled by false information.

</details>

<a id="q17"></a>
<details><summary>#17 · ✅ correct · Class 10 · Class 10 Science · What are the products formed when metal carbonates react with acids?</summary>

**Question:** What are the products formed when metal carbonates react with acids?

**Source:** Class 10 Science, chapter `jesc102`, page 5

**Reference answer:** When metal carbonates react with acids, they produce a corresponding salt, carbon dioxide gas, and water. This reaction is a general characteristic of all metal carbonates and hydrogencarbonates.

**Key points:** corresponding salt; carbon dioxide; water

**Retrieval top 3:** `jesc102`, `jesc102`, `jesc104` (gold chapter ranked first)

**Citations returned:** `jesc102` p.17, `jesc102` p.5, `jesc104` p.17 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-120b]` · **Latency:** 7.61s

**Tutor's answer:**

~~~~text
**Direct answer**  
When a metal carbonate (or metal hydrogencarbonate) reacts with an acid, the products are a salt of that metal, carbon dioxide gas ($\mathrm{CO_2}$) and water.

**Why it works (the mechanism)**  
The acid supplies hydrogen ions ($\mathrm{H^+}$) that combine with the carbonate ion ($\mathrm{CO_3^{2-}}$) or hydrogencarbonate ion ($\mathrm{HCO_3^-}$).  
1. $\mathrm{H^+}$ attacks the carbonate ion, breaking it into $\mathrm{CO_2}$ and water.  
2. The remaining metal ion ($\mathrm{M^{n+}}$) pairs with the anion from the acid (e.g., $\mathrm{Cl^-}$) to form the salt $\mathrm{MCl}$ (or the appropriate salt).  
Overall, the reaction can be written as:  
$\mathrm{MCO_3 + 2\,H^+ → M^{n+} + CO_2\uparrow + H_2O}$  
or for hydrogencarbonate:  
$\mathrm{MHCO_3 + H^+ → M^{n+} + CO_2\uparrow + H_2O}$.

**Ground it in real life**  
Think of the fizz you see when you add lemon juice (acidic) to baking soda (sodium carbonate) while cleaning a kitchen countertop. The bubbling is the release of $\mathrm{CO_2}$, and the leftover solution contains sodium chloride (common salt) and water.

**Worked example / analogy**  
*Analogy*: Imagine a dance floor where the acid’s hydrogen ions are eager partners. The carbonate ion is a group of three dancers (one carbon and three oxygens). When the hydrogen partners join, two of the oxygens pair with hydrogen to leave the floor as $\mathrm{CO_2}$ (a gas that “escapes”), while the remaining carbon and one oxygen combine with water. The metal ion, waiting on the side, now grabs the acid’s anion to form a stable “dance pair” – the salt.

**Common mistake or misconception**  
Students often think only carbon dioxide is produced, forgetting that water and a metal salt are also formed.

**Try this →**  
If calcium carbonate reacts with hydrochloric acid, write the balanced chemical equation and name the salt produced.
~~~~

**Grade:** ✅ correct — The tutor correctly identifies the three products (salt, carbon dioxide, and water) as required by the key points. The additional explanation and analogy are factually accurate and do not contradict the textbook passage.

</details>

<a id="q18"></a>
<details><summary>#18 · ✅ correct · Class 8 · Class 8 Science — Curiosity · Why does the smell of camphor spread to all corners of a room when it is heated?</summary>

**Question:** Why does the smell of camphor spread to all corners of a room when it is heated?

**Source:** Class 8 Science — *Curiosity*, chapter `hecu107`, page 17

**Reference answer:** When camphor is heated, energy is added to it. This energy is then released in the form of a smell, which allows the fragrance to reach all corners of the room.

**Key points:** Heating adds energy to the camphor; The energy is released as a smell; This release allows the fragrance to spread to all corners

**Retrieval top 3:** `hecu107`, `hecu107`, `hecu104` (gold chapter ranked first)

**Citations returned:** — · **Badge:** AI knowledge · **Answered by:** `Groq[openai/gpt-oss-20b]` · **Latency:** 10.85s

**Tutor's answer:**

~~~~text
**Direct answer**  
When camphor is heated it sublimates, turning directly from a solid into a gas. The vapour molecules spread through the room by the random motion of air particles, so the fragrance reaches every corner.

**Why it works**  
Heating supplies kinetic energy to camphor molecules, breaking the forces that keep them together in the solid. Once the energy is high enough, the molecules escape as gas (sublimation). Gas molecules move randomly; each collision with an air molecule transfers momentum and carries the camphor vapour further. Because the concentration of vapour is highest near the heat source, a concentration gradient drives diffusion, pushing the molecules from the hot spot to the rest of the room until the scent is felt everywhere.

**Ground it in real life**  
Think of a small dish of camphor on a stove in a kitchen. As it heats, you can immediately smell it in the living room, the corridor, and even the balcony. It’s the same way a candle’s wax vapours fill a room or how steam from a boiling pot spreads through the kitchen.

**Worked example / fresh analogy**  
Imagine a crowded market square. A few people (camphor molecules) enter the square and mingle. They keep moving randomly, bumping into others (air molecules). Each bump carries them a little farther. Eventually, those few people are spread all over the square, and anyone walking through can see or feel them. The market’s crowd movement is like the random motion of air that carries the camphor vapour to every corner.

**Common mistake**  
Students often think the smell spreads because of a breeze or wind, but it is actually the random, continuous motion of gas molecules (diffusion) that distributes the fragrance.

**Try this →**  
If you place a small piece of camphor in a sealed jar, will you still smell it after a few minutes? Why or why not?
~~~~

**Grade:** ✅ correct — The tutor's answer correctly explains that heating adds energy (kinetic energy) to the camphor, causing it to sublime into gas (releasing the smell), and that the random motion/diffusion of these gas molecules allows the fragrance to spread to all corners. While the textbook uses simplified language ('energy is released as a smell'), the tutor's scientifically accurate explanation of sublimation and diffusion covers all required key points without factual errors.

</details>

<a id="q19"></a>
<details><summary>#19 · ✅ correct · Class 9 · Class 9 Science · Why do scientists group living organisms together and what is this process called?</summary>

**Question:** Why do scientists group living organisms together and what is this process called?

**Source:** Class 9 Science, chapter `iesc112`, page 5

**Reference answer:** Scientists group organisms based on their similarities and differences to make the study of vast diversity easier and more systematic. This grouping helps in organizing information and understanding the relationships between different living beings. This scientific system of grouping is known as biological classification.

**Key points:** Grouping is based on similarities and/or differences; It makes the study of organisms organized and systematic; It helps understand relationships among organisms; The process is called biological classification

**Retrieval top 3:** `iesc112`, `iesc103`, `iesc112` (gold chapter ranked first)

**Citations returned:** `iesc112` p.4, `iesc103` p.1 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-120b]` · **Latency:** 7.42s

**Tutor's answer:**

~~~~text
**Direct answer**  
Scientists group living organisms to bring order to the immense variety of life on Earth; this systematic grouping is called **classification**.

**Why it works (the mechanism)**  
Classification arranges organisms based on shared characteristics so that similar species are placed together. Scientists examine features such as external shape, mode of nutrition, internal structures, cell type, ecological role, reproduction, and genetic similarity. By comparing these traits, they can infer relationships and place organisms into hierarchical groups (kingdom, phylum, class, etc.), making it easier to study and communicate about them.

**Ground it in real life**  
Think of a grocery store: items are arranged in aisles—fruits together, cereals together, spices together—so you can find what you need quickly. Similarly, classification puts organisms with common “ingredients” (traits) in the same “aisle,” helping biologists locate and compare them efficiently.

**Worked analogy**  
Imagine a school library where books are sorted first by subject (science, literature, history), then by grade level, and finally by author.  
- **Subject** ↔ **Kingdom** (broadest similarity)  
- **Grade level** ↔ **Phylum/Class** (more specific traits)  
- **Author** ↔ **Species** (most specific identity)  
Just as the library system lets you locate a particular book without scanning every shelf, biological classification lets scientists pinpoint an organism’s identity among millions.

**Common mistake or misconception**  
Students often think classification is based only on visible traits; in reality, modern classification also relies heavily on internal structures, cell type, and genetic similarity.

**Try this →**  
If you see a plant that makes its own food and cannot move, which two classification criteria (from the list above) would place it in the kingdom *Plantae*?
~~~~

**Grade:** ✅ correct — The answer correctly identifies the process as biological classification (or classification) and explains that it is based on similarities and differences to make study systematic and to understand relationships. All key points from the reference are covered, and the additional details provided are factually accurate.

</details>

<a id="q20"></a>
<details><summary>#20 · ✅ correct · Class 10 · Class 10 History — India and the Contemporary World II · Why did King Victor Emmanuel II of Sardinia-Piedmont take on the task of unifying Italy af…</summary>

**Question:** Why did King Victor Emmanuel II of Sardinia-Piedmont take on the task of unifying Italy after the failed uprisings of 1831 and 1848?

**Source:** Class 10 History — *India and the Contemporary World II*, chapter `jess301`, page 20

**Reference answer:** After the revolutionary uprisings in 1831 and 1848 failed, the responsibility for unifying Italy shifted to Sardinia-Piedmont under King Victor Emmanuel II. The ruling elites of this region supported unification because they believed a unified Italy would bring them economic development and political dominance.

**Key points:** The responsibility fell on Sardinia-Piedmont after the failures of 1831 and 1848.; King Victor Emmanuel II was the ruler of Sardinia-Piedmont.; The ruling elites wanted unification for economic development.; The ruling elites wanted unification for political dominance.

**Retrieval top 3:** `jess301`, `jess301`, `jess301` (gold chapter ranked first)

**Citations returned:** `jess301` p.20, `jess301` p.21 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-120b]` · **Latency:** 10.72s

**Tutor's answer:**

~~~~text
**Direct answer**  
After the 1831 and 1848 uprisings failed, the responsibility for Italian unification shifted to Sardinia‑Piedmont, whose king was Victor Emmanuel II. He took on the task because his kingdom was the only Italian‑ruled state with the political stability, diplomatic contacts and military capacity to lead a concerted effort, and because unification promised the ruling elites economic growth and greater political influence.

**Why it works (the mechanism)**  
1. **Vacuum of leadership** – The failed revolts left no viable revolutionary government to continue the nationalist cause.  
2. **Sardinia‑Piedmont’s position** – It was ruled by an Italian princely house (Victor Emmanuel II) rather than a foreign power, giving it legitimacy among Italians.  
3. **Resources and diplomacy** – The kingdom possessed a relatively modern army and could forge alliances (later with France) to challenge Austrian dominance.  
4. **Elite incentives** – Unification meant larger internal markets, industrial development, and a stronger unified state where the existing elites could retain or expand their power.

**Ground it in real life**  
Think of a neighbourhood where several families own small shops, but only one family runs a well‑stocked grocery store with a delivery truck. When the smaller shops’ attempts to organize a market fail, the grocery‑store family steps in because it already has the resources and can bring all the shops together under one roof, promising better sales for everyone.

**Analogy**  
Imagine a school where several clubs try to organize a big cultural fest, but their efforts collapse. The only club that has a proper auditorium, funding, and a respected captain steps forward to host the fest. By doing so, the captain not only gives the fest a chance to happen but also gains recognition for the whole school and for the club’s members. Similarly, Victor Emmanuel II’s Sardinia‑Piedmont provided the “auditorium” and “funding” needed for Italian unification, while the ruling elites saw a chance to boost their own standing.

**Common mistake or misconception**  
Students often think the unification was driven solely by popular popular uprisings; in reality, it was largely orchestrated by the existing elite leadership of Sardinia‑Piedmont.

**Try this →**  
If the earlier uprisings had succeeded, would Sardinia‑Piedmont still have been the leader of unification? Explain in one sentence.
~~~~

**Grade:** ✅ correct — The tutor's answer correctly identifies that the responsibility fell on Sardinia-Piedmont after the 1831 and 1848 failures, names King Victor Emmanuel II as the ruler, and explicitly states that the ruling elites sought economic development and political dominance. All key points from the reference answer are present and accurate.

</details>

<a id="q21"></a>
<details><summary>#21 · ✅ correct · Class 10 · Class 10 Economics — Understanding Economic Development · How does the chapter define globalisation, and what role do MNCs play in it?</summary>

**Question:** How does the chapter define globalisation, and what role do MNCs play in it?

**Source:** Class 10 Economics — *Understanding Economic Development*, chapter `jess204`, page 1

**Reference answer:** The chapter defines globalisation as the integration between countries through foreign trade and foreign investments by multinational corporations (MNCs). MNCs are described as a major force in the globalisation process over the past thirty years, connecting distant regions of the world by spreading their production to other countries.

**Key points:** Globalisation is defined as integration through foreign trade and foreign investments by MNCs.; MNCs are a major force in the globalisation process.; MNCs connect distant regions by spreading production to other countries.

**Retrieval top 3:** `jess204`, `jess204`, `jess204` (gold chapter ranked first)

**Citations returned:** `jess204` p.1, `jess204` p.18, `jess204` p.9 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-120b]` · **Latency:** 9.7s

**Tutor's answer:**

~~~~text
**Direct answer**  
The chapter defines globalisation as the integration of countries through foreign trade and foreign investment by multinational corporations (MNCs). MNCs act as the main engine of this process by setting up production in different nations, seeking cheaper locations, and linking markets together.

**Why it works (the mechanism)**  
*Foreign trade* moves goods and services across borders, creating mutual dependence between economies. *Foreign investment* (especially by MNCs) brings capital, technology and managerial expertise into the host country. When an MNC establishes a plant abroad, it creates a supply‑chain link: raw material may come from one country, components from another, and the finished product is sold worldwide. These links knit the economies together, making them part of a single, inter‑connected system.

**Ground it in real life**  
Consider a popular Indian smartphone brand that designs its phones in Bangalore, sources chips from South Korea, assembles the device in Vietnam, and sells it in Europe and Africa. The brand’s overseas factories and the cross‑border flow of components are exactly the kind of foreign investment and trade that the chapter calls globalisation.

**Fresh analogy**  
Think of a country as a town and an MNC as a railway company.  
- The town’s local market is the domestic economy.  
- The railway company builds tracks (invests) that connect the town to neighboring towns (other countries).  
- Trains (goods, services, technology) run on these tracks, carrying passengers (capital) and cargo (products) back and forth.  
Just as the railway network ties towns together, MNC‑driven trade and investment tie nations into a global network.

**Common mistake or misconception**  
Students often think globalisation is only about the movement of goods, overlooking the crucial role of foreign investment and the activities of MNCs.

**Try this →**  
Name two ways an MNC can link its home country with a host country besides exporting finished products.
~~~~

**Grade:** ✅ correct — The tutor's answer accurately defines globalisation as integration through foreign trade and investment by MNCs, identifies MNCs as the main force (major force) in the process, and explains how they connect regions by spreading production. All key points from the reference answer are present and factually consistent with the textbook passage.

</details>

<a id="q22"></a>
<details><summary>#22 · ✅ correct · Class 10 · Class 10 Political Science — Democratic Politics II · Why do the elders in Lebanon prefer the current political system over a standard democracy…</summary>

**Question:** Why do the elders in Lebanon prefer the current political system over a standard democracy where anyone can run for president?

**Source:** Class 10 Political Science — *Democratic Politics II*, chapter `jess401`, page 7

**Reference answer:** The elders believe that the present system is the best guarantee for peace. They hold this view because they have witnessed the bloodshed caused by the civil war, suggesting that a 'normal' democracy might lead to similar violence.

**Key points:** The current system is seen as the best guarantee for peace.; Elders have experienced the bloodshed of the civil war.; They fear that a standard democracy could lead to conflict.

**Retrieval top 3:** `jess401`, `jess401`, `jess405` (gold chapter ranked first)

**Citations returned:** `jess401` p.7 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-120b]` · **Latency:** 8.26s

**Tutor's answer:**

~~~~text
**Direct answer**  
The elders favour Lebanon’s sect‑based power‑sharing system because, in their view, it has kept the country from sliding back into the sectarian violence of the civil war. They believe that reserving the top posts for specific communities guarantees peace and stability, something a “regular” open‑contest democracy might jeopardise.

**Why it works (the mechanism)**  
The current arrangement allocates the presidency to a Maronite Christian, the prime‑ministership to a Sunni Muslim, the deputy prime‑ministership to an Orthodox Christian and the speaker’s chair to a Shi’a Muslim. By fixing these posts to particular groups, the system creates a **balance of power**: no single community can dominate the executive, and each major sect has a guaranteed voice in decision‑making. This institutionalised sharing is seen as a safeguard against the mistrust and rivalry that sparked the 1975‑1990 civil war.

**Ground it in real life**  
Think of a school classroom where the teacher assigns the role of class monitor, note‑taker, and time‑keeper to students from different friend groups. Even if one group becomes larger, each still retains a leadership position, preventing any one group from taking over all responsibilities and keeping the class harmonious.

**Worked analogy**  
Imagine a three‑lane road where each lane is reserved for a different type of vehicle: cars, buses, and bikes. If any lane were opened to all vehicles, the larger, faster cars could block the road, leaving buses and bikes stuck. By keeping each lane exclusive, traffic flows smoothly and accidents are avoided. Similarly, Lebanon’s reserved political “lanes” keep communal “traffic” moving without one group overwhelming the others, preserving peace.

**Common mistake or misconception**  
A frequent error is to think that a standard democracy automatically ensures fairness; in a deeply divided society, unrestricted elections can actually deepen divisions rather than heal them.

**Try this →**  
If a community that is now a minority suddenly became the majority, would the elders still prefer the same power‑sharing rule? Why or why not?
~~~~

**Grade:** ✅ correct — The answer correctly identifies that the elders prefer the current system because it is the best guarantee for peace, citing their experience of the civil war's bloodshed and the fear that a standard democracy could lead to conflict. It accurately reflects the textbook passage while providing additional context.

</details>

<a id="q23"></a>
<details><summary>#23 · ✅ correct · Class 8 · Class 8 Mathematics — Ganita Prakash Part 1 · If I use 15 mL of blue paint and mix it with yellow paint in a 3:5 ratio, how much yellow…</summary>

**Question:** If I use 15 mL of blue paint and mix it with yellow paint in a 3:5 ratio, how much yellow paint do I need? Also, if I add 20 mL more yellow paint to this mixture, what will be the new ratio of blue to yellow?

**Source:** Class 8 Mathematics — *Ganita Prakash* Part 1, chapter `hegp107`, page 0

**Reference answer:** To maintain a 3:5 ratio with 15 mL of blue paint, you need 25 mL of yellow paint. If you add 20 mL of yellow paint to the existing 25 mL, the total yellow paint becomes 45 mL. The new ratio of blue (15 mL) to yellow (45 mL) simplifies to 1:3.

**Key points:** 25 mL of yellow paint is needed initially; Total yellow paint becomes 45 mL after adding 20 mL; The new ratio is 1:3

**Retrieval top 3:** `hegp107`, `hegp107`, `hegp107` (gold chapter ranked first)

**Citations returned:** `hegp107` p.0 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-120b]` · **Latency:** 11.88s

**Tutor's answer:**

~~~~text
**Direct answer**  
You need **25 mL** of yellow paint to keep the 3 : 5 ratio with 15 mL of blue paint. After adding another 20 mL of yellow, the mixture contains 15 mL blue and 45 mL yellow, giving a new ratio of **1 : 3** (blue : yellow).

**Why it works (the mechanism)**  
A ratio \(a:b\) means the quantities are in the proportion \(a/b\).  
- For the original ratio \(3:5\), the amount of yellow should be \(\frac{5}{3}\) times the amount of blue.  
  \[
  \text{Yellow needed}=15\text{ mL}\times\frac{5}{3}=25\text{ mL}
  \]  
- After adding 20 mL more yellow, the total yellow becomes \(25\text{ mL}+20\text{ mL}=45\text{ mL}\).  
  The new ratio is  
  \[
  \frac{\text{Blue}}{\text{Yellow}}=\frac{15}{45}=\frac{1}{3}\; \Rightarrow\; 1:3.
  \]

**Ground it in real life**  
Think of mixing paint for a school art project. If you want the exact shade that needs three parts blue to five parts yellow, you must measure the yellow carefully; otherwise the colour will turn too green or too purple. Adding extra yellow later lightens the colour, just as the ratio shifts from 3 : 5 to 1 : 3.

**Worked example**  
Suppose you have 12 mL of red paint and need to keep a 2 : 7 ratio with white paint.  
1. Required white = \(12 \times \frac{7}{2}=42\) mL.  
2. If you later add 10 mL more white, total white = \(42+10=52\) mL.  
3. New ratio = \(\frac{12}{52} = \frac{3}{13}\) → 3 : 13.  
This mirrors the blue‑yellow calculation: keep the proportion, then adjust the total and recompute the ratio.

**Common mistake or misconception**  
Students often add the extra yellow to the original 15 mL of blue and then incorrectly write the new ratio as 15 : 20 instead of recomputing the total yellow (45 mL) before forming the ratio.

**Try this →**  
If you start with 9 mL of green paint and need a 4 : 9 ratio with yellow paint, how many millilitres of yellow are required? (Answer in your head.)
~~~~

**Grade:** ✅ correct — The tutor correctly calculates the initial amount of yellow paint (25 mL), the total yellow paint after addition (45 mL), and the simplified new ratio (1:3). All key points are present and mathematically accurate.

</details>

<a id="q24"></a>
<details><summary>#24 · ✅ correct · Class 10 · Class 10 Political Science — Democratic Politics II · What is the child sex ratio in India according to the 2011 Census, and what is the main re…</summary>

**Question:** What is the child sex ratio in India according to the 2011 Census, and what is the main reason given for its decline?

**Source:** Class 10 Political Science — *Democratic Politics II*, chapter `jess403`, page 5

**Reference answer:** According to the 2011 Census, the child sex ratio in India declined to 919 girl children per thousand boys. This decline is attributed to sex-selective abortion, where parents in many parts of India prefer sons and abort female fetuses before birth.

**Key points:** The child sex ratio is 919 girls per 1000 boys.; The data is from the 2011 Census.; The decline is caused by sex-selective abortion due to a preference for sons.

**Retrieval top 3:** `jess403`, `jess403`, `jess403` (gold chapter ranked first)

**Citations returned:** `jess403` p.5 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-120b]` · **Latency:** 15.44s

**Tutor's answer:**

~~~~text
**Direct answer**  
The 2011 Census puts the child sex ratio in India at **919 girls for every 1,000 boys**.  
Its decline is mainly because many parents prefer sons and resort to **sex‑selective abortion of girl fetuses**.

**Why it works (the mechanism)**  
*Child sex ratio* measures the number of girls per 1,000 boys in the 0‑6 year age group. When families favor sons, they may use prenatal diagnostic techniques to discover the fetus’s sex. If the fetus is a girl, they may abort it—a practice called **sex‑selective abortion**. Each such abortion removes a potential girl from the population, lowering the ratio of girls to boys.

**Ground it in real life**  
Imagine a village where most families want a boy to carry on the family name and help with farm work. When a couple learns through an ultrasound that they are expecting a girl, they may feel pressured to terminate the pregnancy. Over many such decisions, the number of girls born in that village drops, pulling down the overall child sex ratio.

**Worked example / analogy**  
Think of a jar that initially contains 1,000 red marbles (boys) and 1,000 blue marbles (girls). If, over time, people remove 81 blue marbles but keep all the red ones, the jar will end up with 1,000 red and 919 blue marbles. The proportion of blue to red has fallen from 1:1 to 919:1, just as the child sex ratio falls from an ideal 1,000 girls per 1,000 boys to 919 girls per 1,000 boys because of selective removal of girls.

**Common mistake or misconception**  
Students often think the ratio drops because fewer boys are born, but the real driver is the **excess removal of girl embryos**, not a change in boy births.

**Try this →**  
If a district had 10,000 births and the child sex ratio was 919, how many of those births would be girls? (Hint: use the ratio 919 girls : 1,000 boys.)
~~~~

**Grade:** ✅ correct — The tutor correctly identifies the child sex ratio as 919 girls per 1,000 boys based on the 2011 Census and accurately attributes the decline to sex-selective abortion driven by a preference for sons, covering all required key points without factual errors.

</details>

<a id="q25"></a>
<details><summary>#25 · ✅ correct · Class 10 · Class 10 Geography — Contemporary India II · What is the main goal of the Jal Jeevan Mission regarding water supply to rural households…</summary>

**Question:** What is the main goal of the Jal Jeevan Mission regarding water supply to rural households?

**Source:** Class 10 Geography — *Contemporary India II*, chapter `jess103`, page 3

**Reference answer:** The goal of the Jal Jeevan Mission is to ensure that every rural household receives a regular, long-term supply of potable piped water. This service level is defined as 55 litres per capita per day, with a focus on keeping the tap water connections functional.

**Key points:** Every rural household gets assured supply of potable piped water; Service level is 55 litres per capita per day; Supply must be regular and on a long-term basis; Functionality of tap water connections is ensured

**Retrieval top 3:** `jess103`, `jess103`, `jess103` (gold chapter ranked first)

**Citations returned:** `jess103` p.3 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-120b]` · **Latency:** 6.57s

**Tutor's answer:**

~~~~text
**Direct answer**  
The Jal Jeevan Mission aims to provide every rural household with a reliable, piped supply of safe drinking water – at least 55 litres per person per day on a regular, long‑term basis.

**Why it works (the mechanism)**  
The mission focuses on installing and maintaining functional tap connections in villages. By ensuring that each connection delivers potable (drink‑able) water, the target of 55 L per capita per day can be met consistently, which improves health and reduces dependence on unsafe sources.

**Ground it in real life**  
Think of a typical Indian village where families currently fetch water from wells or hand‑pumps. With a working tap at home, they no longer need to walk long distances, and the water they receive meets safety standards, just like the water that comes out of a city’s municipal tap.

**Analogy**  
Imagine a school that wants every student to have a personal notebook. Simply handing out notebooks isn’t enough; the school must also ensure each notebook is intact, has pages, and is usable throughout the year. Similarly, the Jal Jeevan Mission not only installs taps but also guarantees they stay functional and supply clean water continuously.

**Common mistake or misconception**  
Students often think the mission only builds new wells, but its real focus is on **piped, potable water** that meets the 55 L per capita per day standard.

**Try this →**  
If a village has 200 households and each person needs 55 L per day, how many litres of water must the mission supply each day for the whole village? (Assume an average of 5 people per household.)
~~~~

**Grade:** ✅ correct — The tutor's answer accurately states the goal of the Jal Jeevan Mission, covering all four key points: assured supply to every rural household, the specific service level of 55 litres per capita per day, the regular and long-term nature of the supply, and the focus on the functionality of tap connections. There are no factual errors.

</details>

<a id="q26"></a>
<details><summary>#26 · ✅ correct · Class 10 · Class 10 Geography — Contemporary India II · What are the three phases of treating industrial effluents and what methods are used in ea…</summary>

**Question:** What are the three phases of treating industrial effluents and what methods are used in each?

**Source:** Class 10 Geography — *Contemporary India II*, chapter `jess106`, page 9

**Reference answer:** The treatment of industrial effluents is done in three phases: primary, secondary, and tertiary. Primary treatment uses mechanical means like screening, grinding, flocculation, and sedimentation. Secondary treatment relies on biological processes, while tertiary treatment combines biological, chemical, and physical processes to recycle wastewater.

**Key points:** Primary treatment uses mechanical means (screening, grinding, flocculation, sedimentation); Secondary treatment uses biological processes; Tertiary treatment uses biological, chemical, and physical processes for recycling

**Retrieval top 3:** `jess106`, `jesc102`, `jesc104` (gold chapter ranked first)

**Citations returned:** `jess106` p.9 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-20b]` · **Latency:** 10.06s

**Tutor's answer:**

~~~~text
**Direct answer**  
Industrial effluents are treated in three phases:  
1. **Primary treatment** – physical removal of large solids and grit.  
2. **Secondary treatment** – biological processes that break down dissolved organics.  
3. **Tertiary treatment** – advanced chemical, physical or biological steps that polish the water and enable recycling.

**Why it works**  
- **Primary**: Screening and sedimentation separate heavy particles and suspended matter, reducing the load on later stages.  
- **Secondary**: Microorganisms in activated sludge or biofilm reactors oxidise organic pollutants, converting them into biomass, CO₂ and water.  
- **Tertiary**: Chemical coagulation or flocculation removes fine colloids; filtration removes remaining solids; disinfection (chlorination, UV) kills pathogens; adsorption or ion‑exchange removes specific contaminants. Together these steps reduce turbidity, biochemical oxygen demand (BOD), chemical oxygen demand (COD) and toxic substances to acceptable levels.

**Ground it in real life**  
Think of a textile mill in Varanasi. The raw wastewater first passes through a grit chamber (primary). Then it goes to a bioreactor where bacteria eat the dyes and sugars (secondary). Finally, the water is treated with lime and sand filters, and a UV lamp to kill any remaining microbes before it is reused for dyeing again (tertiary).

**Worked example or fresh analogy**  
Imagine cleaning a dirty kitchen sink: first you scrape off food scraps (primary), then you let a bowl of soap and water sit so the microbes break down grease (secondary), and finally you rinse with hot water and a disinfectant spray to make the sink spotless (tertiary). Each step removes a different kind of dirt, just as each treatment phase removes a different kind of pollutant.

**Common mistake or misconception**  
Students often think that a single chemical treatment can clean all industrial waste; in reality, physical, biological and chemical steps are usually required in sequence.

**Try this →**  
If a factory’s effluent still smells after secondary treatment, which tertiary step would most likely remove the odor?
~~~~

**Grade:** ✅ correct — The tutor correctly identifies the three phases of industrial effluent treatment as primary, secondary, and tertiary. It accurately describes primary treatment as mechanical/physical (mentioning screening and sedimentation), secondary as biological, and tertiary as a combination of biological, chemical, and physical processes for recycling, aligning with the textbook passage and key points.

</details>

<a id="q27"></a>
<details><summary>#27 · ✅ correct · Class 8 · Class 8 Social Science — Exploring Society: India and Beyond · What is a demographic dividend and how does it help a country's economy?</summary>

**Question:** What is a demographic dividend and how does it help a country's economy?

**Source:** Class 8 Social Science — *Exploring Society: India and Beyond*, chapter `hees107`, page 8

**Reference answer:** A demographic dividend is the benefit a country receives when it has a large number of young and working people. It helps the economy because when more people are working and earning while fewer depend on them, the country can grow businesses and improve living standards.

**Key points:** It is the benefit from having a large number of young and working people.; It occurs when more people are working/earning and fewer are dependent.; It allows the country to grow businesses and improve living standards.

**Retrieval top 3:** `hees107`, `hees107`, `hees107` (gold chapter ranked first)

**Citations returned:** `hees107` p.8 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-120b]` · **Latency:** 9.62s

**Tutor's answer:**

~~~~text
**Direct answer**  
A demographic dividend is the boost to a country’s economy that occurs when a large share of the population is of working age while the number of dependents (children and elderly) is relatively low. With more people able to work and earn, businesses can expand and living standards can rise.

**Why it works (the mechanism)**  
When the proportion of workers rises, the labour force becomes larger and the dependency ratio falls. This means fewer resources are needed for non‑productive support (like schooling very young children or caring for many elderly), leaving more income and savings for investment. If those workers have **human capital** – the knowledge, skills, experience and ability to perform work – they can create greater economic value, driving higher production and growth.

**Ground it in real life**  
India’s population today has a big “young‑adult” segment. If these young adults receive good education, health care, and skill training, they can take up jobs in factories, services, and technology, helping the country’s businesses grow and improving household incomes.

**Worked analogy**  
Think of a kitchen where most people are chefs and only a few are diners. With many chefs (workers) and few diners (dependents), the kitchen can prepare more dishes (goods and services) quickly. However, the chefs must know the recipes and have proper utensils (education, health, skills). When they do, the kitchen runs efficiently and serves more customers, just as a nation enjoys higher economic output during a demographic dividend.

**Common mistake or misconception**  
Students often think that simply having a large population guarantees economic growth, but without adequate education, health and skill development the potential dividend is not realised.

**Try this →**  
If a country’s working‑age population rises from 50 % to 60 % while the dependent population falls, what two things must the government ensure to actually reap a demographic dividend? (Think about the qualities of human capital.)
~~~~

**Grade:** ✅ correct — The tutor's answer correctly defines the demographic dividend as the benefit from a large working-age population with fewer dependents. It explicitly states that this allows for business growth and improved living standards, covering all key points from the reference answer and textbook passage without any factual errors.

</details>

<a id="q28"></a>
<details><summary>#28 · 🟡 partial · Class 10 · Class 10 Political Science — Democratic Politics II · Is it true that political parties are unpopular and people don't participate in them in In…</summary>

**Question:** Is it true that political parties are unpopular and people don't participate in them in India? What does the survey evidence say about trust and participation levels?

**Source:** Class 10 Political Science — *Democratic Politics II*, chapter `jess404`, page 7

**Reference answer:** The belief that political parties are unpopular and citizens are indifferent is only partly true for India. While surveys show that political parties are among the least trusted institutions in South Asia and globally, with more people expressing little or no trust than those with some or great trust, the level of participation in party activities remains fairly high. In fact, the proportion of people who are members of a political party is higher in India than in many other countries.

**Key points:** The belief of unpopularity/indifference is only partly true for India.; Political parties are one of the least trusted institutions in South Asia and globally.; Participation in political party activities is fairly high in India.; The proportion of party members in India is higher than in many other countries.

**Retrieval top 3:** `jess404`, `jess404`, `jess404` (gold chapter ranked first)

**Citations returned:** `jess404` p.7, `jess404` p.12 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-120b]` · **Latency:** 5.49s

**Tutor's answer:**

~~~~text
**Direct answer**  
Surveys show that many Indians do not place much trust in political parties, but a sizable and growing number still join or feel close to them. Participation in party activities is relatively high and has been rising over the past three decades.

**Why it works (the mechanism)**  
- **Trust level**: Respondents were asked how much they trust political parties. More people chose “not much” or “not at all” than those who chose “some” or “great” trust, indicating low confidence in parties.  
- **Participation level**: Separate questions asked whether people were members of a party or felt close to one. The answers showed a higher proportion of members in India than in several advanced democracies, and the share of members and those feeling close has increased steadily over the last thirty years.  
Thus, even though trust is low, the willingness to associate with a party remains strong, likely because parties still serve as the primary channel for political expression and collective action.

**Ground it in real life**  
Think of a popular cricket league. Fans may criticize the board’s decisions (low trust) but still buy tickets, wear team jerseys, and cheer loudly at matches (high participation). Similarly, Indians may doubt parties yet continue to join them or feel attached.

**Analogy**  
Imagine a neighborhood’s community garden. Residents often complain that the garden committee makes poor choices (low trust). Yet many still volunteer to plant seeds, water plants, and attend meetings because the garden is the only place to influence local greenery. The garden’s health depends on that continued involvement, just as democracy relies on party participation despite distrust.

**Common mistake or misconception**  
Students sometimes think low trust automatically means people stay away from parties; the surveys reveal that participation can stay high even when trust is weak.

**Try this →**  
If a survey shows 60 % low trust but 30 % party membership, does the data support the claim that “unpopularity leads to no participation”? Explain in one sentence.
~~~~

**Grade:** 🟡 partial — The answer correctly identifies that the belief in unpopularity is partly true, that trust is low, and that participation is high relative to other countries. However, it introduces factual claims that participation has been 'rising' or 'increased steadily' over the last three decades, which is not supported by the provided textbook passage (which only states the level was 'fairly high' based on surveys over several decades, without specifying a trend of increase). This constitutes a minor inaccuracy/hallucination relative to the source text.  
**Factual errors:** Participation in party activities is relatively high and has been rising over the past three decades.; The share of members and those feeling close has increased steadily over the last thirty years.

</details>

<a id="q29"></a>
<details><summary>#29 · ✅ correct · Class 8 · Class 8 Science — Curiosity · How do communicable diseases actually spread from one person to another?</summary>

**Question:** How do communicable diseases actually spread from one person to another?

**Source:** Class 8 Science — *Curiosity*, chapter `hecu103`, page 5

**Reference answer:** Communicable diseases are caused by pathogens that can enter the body through the air, contaminated food, or water. They spread from an infected person to a healthy one via the air (through coughing or sneezing), direct contact (like shaking hands), or indirect contact (such as sharing items).

**Key points:** Caused by pathogens; Spread through air (coughing/sneezing); Spread through direct contact (e.g., handshakes); Spread through indirect contact or contaminated food/water

**Retrieval top 3:** `hecu103`, `hecu103`, `hecu103` (gold chapter ranked first)

**Citations returned:** `hecu103` p.5, `hecu103` p.6 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-120b]` · **Latency:** 6.8s

**Tutor's answer:**

~~~~text
**Direct answer**  
Communicable diseases spread when disease‑causing pathogens move from an infected person to a healthy person. This can happen through the air (coughing or sneezing), by direct contact (handshake, touch), by indirect contact (sharing personal items or contaminated surfaces), through contaminated food or water, and via insect vectors such as mosquitoes.

**Why it works (the mechanism)**  
- **Pathogen** – a microscopic organism (virus, bacterium, fungus, etc.) that can cause disease.  
- **Airborne transmission** – when an infected person coughs or sneezes, tiny droplets containing pathogens are expelled. These droplets travel through the air and can be inhaled by a nearby person, depositing the pathogen in the respiratory tract.  
- **Direct contact transmission** – pathogens on the skin or bodily fluids of an infected person are transferred directly to another person’s skin or mucous membranes when they touch (e.g., handshake).  
- **Indirect contact transmission** – the pathogen first lands on a surface or personal item (like a pen, mobile phone, or door handle). When a healthy person later touches that surface and then touches their face, the pathogen enters the body.  
- **Food‑water transmission** – pathogens can survive in contaminated water or food. Consuming these introduces the pathogen into the digestive system.  
- **Vector‑borne transmission** – certain insects (vectors) such as mosquitoes or houseflies pick up pathogens from an infected host and inject them into a new host when they bite.

**Ground it in real life**  
- In a crowded classroom, a child with flu coughs; the droplets travel a few meters and classmates inhale them, catching the flu.  
- After a cricket match, teammates shake hands; if one player has a skin infection, the bacteria can spread to the others.  
- Sharing a water bottle after a school trip can transmit diarrhoeal disease if the bottle was contaminated.  
- During monsoon, mosquitoes breed in stagnant water and can spread dengue from one person to another.

**Worked analogy**  
Think of a pathogen as a secret note.  
- **Airborne spread** – the note is tossed into the air and lands directly into another’s lap.  
- **Direct contact** – you hand the note directly to a friend.  
- **Indirect contact** – you leave the note on a desk; later, another student picks it up and reads it.  
- **Food‑water spread** – the note is hidden inside a snack; anyone who eats the snack finds the note.  
- **Vector spread** – a messenger (mosquito) picks up the note from one person and delivers it to another when it lands.  
In each case, the note (pathogen) moves from one person to another, causing the “message” (disease) to be received.

**Common mistake or misconception**  
Students often think only coughing or sneezing spreads diseases, overlooking contact with contaminated surfaces, food, water, or insect bites.

**Try this →**  
Identify the main mode of transmission for each disease: (a) cholera, (b) dengue, (c) common cold.
~~~~

**Grade:** ✅ correct — The answer correctly identifies pathogens as the cause and lists all required modes of transmission (air, direct contact, indirect contact, and contaminated food/water) as per the textbook. It also accurately includes vector-borne transmission which is shown in the textbook figure, and contains no factual errors.

</details>

<a id="q30"></a>
<details><summary>#30 · ✅ correct · Class 8 · Class 8 Science — Curiosity · How can we make an electromagnet stronger, and how do we change which end is the north pol…</summary>

**Question:** How can we make an electromagnet stronger, and how do we change which end is the north pole?

**Source:** Class 8 Science — *Curiosity*, chapter `hecu104`, page 6

**Reference answer:** The strength of an electromagnet can be increased by increasing the amount of electric current flowing through the coil or by increasing the number of turns in the coil. The poles of the electromagnet can be reversed by changing the direction of the current.

**Key points:** Strength increases with more current; Strength increases with more turns in the coil; Poles reverse when current direction changes

**Retrieval top 3:** `hecu104`, `hecu104`, `hecu104` (gold chapter ranked first)

**Citations returned:** `hecu104` p.6, `hecu104` p.5 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-120b]` · **Latency:** 10.61s

**Tutor's answer:**

~~~~text
**Direct answer**  
An electromagnet becomes stronger if you increase the electric current through the coil or increase the number of turns of the wire (or both). Adding an iron core also boosts its strength. To make the opposite end act as the north pole, simply reverse the direction of the current flowing through the coil.

**Why it works (the mechanism)**  
*Current* determines the magnetic field produced by a coil: a larger current creates a stronger field. The magnetic field of a solenoid is proportional to the product of current (*I*) and the number of turns (*N*) – more turns mean the field lines add up, giving a stronger overall field. An iron core concentrates the field lines, acting like a magnetic “lens” that makes the field inside the coil much stronger. The polarity of the electromagnet follows the right‑hand rule: if the current flows clockwise when viewed from one end, that end becomes the south pole; reversing the current flips the direction of the field, so the opposite end becomes the north pole.

**Ground it in real life**  
Think of the electric bell in a school’s notice board. When you press the button, more current flows through its coil, making the bell’s magnet pull the striker harder. If you replace the coil with a longer one (more turns) or use a bigger battery, the bell rings louder because the electromagnet is stronger. Similarly, a crane’s lifting electromagnet uses many turns of thick wire and a massive iron core to pick up heavy steel plates; reversing the current lets the operator release the load safely.

**Worked example / fresh analogy**  
*Analogy*: Imagine a line of people passing a ball. Each person (turn of wire) adds a little push to the ball (magnetic field). If more people join the line, the ball gets a bigger overall push. If each person throws the ball harder (greater current), the push is also stronger. Adding a smooth, low‑friction tube (iron core) guides the ball straight, making the push feel even more powerful. Reversing the direction in which the ball is thrown (changing current direction) makes the ball travel the opposite way, just like swapping the north and south poles.

**Common mistake or misconception**  
Students often think that simply adding more batteries always makes a stronger magnet, forgetting that the coil’s resistance limits the current; without enough turns or a suitable core, the increase may be minimal.

**Try this →**  
If you double the number of turns in a coil while keeping the same battery, how does the magnetic field strength change? (Answer: it roughly doubles.)
~~~~

**Grade:** ✅ correct — The tutor's answer correctly identifies that increasing current or the number of turns strengthens the electromagnet, and that reversing the current direction reverses the poles. It also provides accurate additional context (iron core, right-hand rule) without contradicting the textbook.

</details>

<a id="q31"></a>
<details><summary>#31 · ✅ correct · Class 10 · Class 10 Science · Besides water, what other raw materials do plants take from the soil, and why is nitrogen…</summary>

**Question:** Besides water, what other raw materials do plants take from the soil, and why is nitrogen specifically important for them?

**Source:** Class 10 Science, chapter `jesc105`, page 5

**Reference answer:** Plants take up nitrogen, phosphorus, iron, and magnesium from the soil. Nitrogen is an essential element because it is used in the synthesis of proteins and other compounds.

**Key points:** Plants absorb nitrogen, phosphorus, iron, and magnesium from the soil.; Nitrogen is essential for the synthesis of proteins and other compounds.

**Retrieval top 3:** `jesc105`, `jesc105`, `jesc105` (gold chapter ranked first)

**Citations returned:** `jesc105` p.16, `jesc105` p.5 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-120b]` · **Latency:** 7.33s

**Tutor's answer:**

~~~~text
**Direct answer**  
Plants absorb several inorganic minerals from the soil besides water – chiefly nitrogen, phosphorus, iron and magnesium. Nitrogen is especially vital because it is a key component of proteins and other nitrogen‑containing compounds that the plant needs to build its body.

**Why it works (the mechanism)**  
Root hairs take up mineral ions dissolved in soil water.  
- **Nitrogen** enters mainly as nitrate (NO₃⁻) or nitrite (NO₂⁻) ions. Inside the plant, these ions are reduced to ammonium (NH₄⁺) and incorporated into amino acids, the building blocks of **proteins** (the enzymes and structural molecules).  
- **Phosphorus** is taken up as phosphate (PO₄³⁻) and forms part of ATP, the energy‑currency, and nucleic acids.  
- **Iron** and **magnesium** are essential for chlorophyll synthesis and for many enzyme functions.  
Thus, without sufficient nitrogen, the plant cannot synthesize enough proteins, which hampers growth and metabolism.

**Ground it in real life**  
When farmers apply “nitrogen‑rich” fertilizers (like urea), they are directly supplying the nitrogen that crops would otherwise obtain slowly from the soil. A nitrogen‑deficient field shows pale, yellowish leaves because chlorophyll production drops without enough protein‑based enzymes.

**Worked analogy**  
Think of a plant as a construction site.  
- **Soil minerals** are the raw building materials delivered to the site.  
- **Nitrogen** is like the steel rods that give strength to the framework; without enough steel, the building (the plant) cannot stand firm.  
- **Phosphorus, iron, magnesium** are the bricks, wiring, and paint that complete the structure.  
Just as a building collapses without sufficient steel, a plant’s growth stalls without adequate nitrogen.

**Common mistake or misconception**  
Students often think nitrogen is only needed for “green colour”; actually, its primary role is to form proteins and nucleic acids, which drive all metabolic activities.

**Try this →**  
If a plant’s soil lacks nitrogen but has plenty of phosphorus and iron, what visible symptom would you expect on its leaves?
~~~~

**Grade:** ✅ correct — The answer correctly identifies nitrogen, phosphorus, iron, and magnesium as the raw materials taken from the soil. It also accurately states that nitrogen is essential for the synthesis of proteins and other compounds, aligning with the textbook passage. The additional details provided are factually correct and do not contradict the source material.

</details>

<a id="q32"></a>
<details><summary>#32 · ✅ correct · Class 9 · Class 9 Science · Why does a coconut break when it is dropped from a height and hits the ground?</summary>

**Question:** Why does a coconut break when it is dropped from a height and hits the ground?

**Source:** Class 9 Science, chapter `iesc106`, page 12

**Reference answer:** When the coconut hits the ground, it stops in a very short time. To change its velocity so quickly, the ground must exert a very large force on it. This large force is what breaks the shell.

**Key points:** The coconut stops in a very short time upon impact.; A large force is required to change its velocity so quickly.; The ground exerts this large force on the coconut.; This large force breaks the shell.

**Retrieval top 3:** `iesc106`, `iesc107`, `iesc107` (gold chapter ranked first)

**Citations returned:** `iesc106` p.12 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-20b]` · **Latency:** 7.87s

**Tutor's answer:**

~~~~text
**Direct answer**  
When a coconut falls, it gains speed and then stops almost instantly upon hitting the ground. The ground must apply a very large force to bring its velocity to zero in a short time, and that force exceeds the strength of the coconut shell, so it cracks.

**Why it works (the mechanism)**  
The force needed to stop an object is given by  
\[
F = \frac{\Delta p}{\Delta t}
\]
where \(\Delta p\) is the change in momentum and \(\Delta t\) is the time taken to stop.  
A falling coconut has a large momentum because of its mass and the speed it reaches just before impact. If the stopping time \(\Delta t\) is very small (the coconut hits the ground and stops in a few milliseconds), the required force \(F\) becomes very large. The coconut shell cannot withstand this high impact force, so it fractures.

**Ground it in real life**  
Think of a cricket ball thrown at a bat. The ball’s speed is high, and the bat must stop it in a tiny fraction of a second. The sudden deceleration creates a large force that can dent the bat or even break it if the ball is too hard. Similarly, a coconut falling from a tree hits the ground so fast that the shell cracks.

**Worked example or fresh analogy**  
Imagine a 10‑kg coconut falling from 5 m.  
- Its speed just before impact: \(v = \sqrt{2gh} = \sqrt{2 \times 9.8 \times 5} \approx 9.9\ \text{m/s}\).  
- Momentum: \(p = mv = 10 \times 9.9 = 99\ \text{kg·m/s}\).  
If the coconut stops in \(0.01\ \text{s}\) (a very short time), the force is  
\[
F = \frac{99}{0.01} = 9900\ \text{N}.
\]
A force of nearly 10 kN is far greater than the shell’s breaking strength, so the coconut shatters.

**Common mistake or misconception**  
Students often think the coconut breaks because it is heavy, but it is actually the rapid deceleration that creates the large force, not the weight alone.

**Try this →**  
If a 5‑kg coconut falls from 2 m and stops in 0.02 s, what is the average force exerted by the ground? (Do the calculation in your head.)
~~~~

**Grade:** ✅ correct — The tutor's answer correctly identifies that the coconut stops in a very short time, requiring a large force from the ground to change its velocity, which breaks the shell. All key points from the reference answer are present, and the additional physics explanation (momentum/impulse) is factually accurate and appropriate for the context.

</details>

<a id="q33"></a>
<details><summary>#33 · ✅ correct · Class 10 · Class 10 Science · What is a word equation and how is it used to represent a chemical reaction?</summary>

**Question:** What is a word equation and how is it used to represent a chemical reaction?

**Source:** Class 10 Science, chapter `jesc101`, page 2

**Reference answer:** A word equation is a shorter, simpler way to describe a chemical reaction compared to writing it out in a full sentence. It lists the reactants on the left side and the products on the right side, separated by an arrow. For example, the burning of magnesium is written as Magnesium + Oxygen → Magnesium oxide.

**Key points:** It is a shorter form of describing a chemical reaction.; It uses reactants and products separated by an arrow.; It is simpler than a full sentence description.

**Retrieval top 3:** `jesc101`, `jesc101`, `jesc101` (gold chapter ranked first)

**Citations returned:** `jesc101` p.3, `jesc101` p.2, `jesc101` p.14 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-120b]` · **Latency:** 11.15s

**Tutor's answer:**

~~~~text
**Direct answer**  
A word‑equation writes the names of the reactants on the left, an arrow (→) in the middle, and the names of the products on the right. It tells you which substances are involved and the direction of the chemical change.

**Why it works (the mechanism)**  
- **Reactants** are the starting materials; they are placed on the left‑hand side (LHS).  
- **Products** are the new substances formed; they go on the right‑hand side (RHS).  
- A **plus sign (+)** separates more than one reactant or product.  
- The **arrow (→)** points toward the products, indicating the direction of the reaction.  
The word‑equation therefore captures the qualitative change while obeying the law of conservation of mass—what goes in must come out, even though it does not show the exact number of atoms.

**Ground it in real life**  
When a strip of magnesium ribbon is heated in air, you observe a bright white flame and a white powder left behind. In words this is written as:  

`Magnesium + Oxygen → Magnesium oxide`  

The equation tells you that magnesium and oxygen are the reactants and magnesium oxide is the product.

**Worked example / fresh analogy**  
Think of making a simple snack:  

`Bread + Butter → Buttered Bread`  

- **Bread** and **Butter** are the reactants (ingredients).  
- The **arrow** shows that we combine them.  
- **Buttered Bread** is the product (the final snack).  

Just as the snack is formed by mixing the ingredients, a chemical reaction forms products by mixing reactants.

**Common mistake or misconception**  
Students often think a word‑equation gives the amounts or physical states of the substances; it only names the reactants and products, not their quantities or phases.

**Try this →**  
Write a word‑equation for the reaction of sodium metal with chlorine gas to form sodium chloride.
~~~~

**Grade:** ✅ correct — The answer correctly defines a word equation as a shorter form of describing a reaction, explicitly mentions reactants and products separated by an arrow, and provides the correct example from the textbook. It covers all key points without factual errors.

</details>

<a id="q34"></a>
<details><summary>#34 · ✅ correct · Class 9 · Class 9 Science · If a child slides down a slide of height h, does the speed at the bottom depend on the mas…</summary>

**Question:** If a child slides down a slide of height h, does the speed at the bottom depend on the mass of the child or the shape of the slide?

**Source:** Class 9 Science, chapter `iesc107`, page 13

**Reference answer:** No, the speed at the bottom does not depend on the mass of the child or the shape of the slide. It depends only on the height of the slide. This is because the potential energy at the top converts entirely into kinetic energy at the bottom, leading to a velocity determined solely by the height.

**Key points:** Velocity depends only on height h; Mass of the child does not affect the final velocity; Shape of the slide does not affect the final velocity

**Retrieval top 3:** `iesc107`, `iesc107`, `iesc107` (gold chapter ranked first)

**Citations returned:** `iesc107` p.1 · **Badge:** Extended reference · **Answered by:** `Groq[openai/gpt-oss-120b]` · **Latency:** 9.39s

**Tutor's answer:**

~~~~text
**Direct answer**  
The speed of a child at the bottom of a slide depends only on the vertical height $h$ of the slide; it is independent of the child’s mass and the shape of the slide (provided friction and air resistance are negligible).

**Why it works (the mechanism)**  
At the top the child possesses gravitational potential energy $E_{\text{p}} = mgh$, where $m$ is mass, $g$ is the acceleration due to gravity, and $h$ is the vertical drop. As the child slides down, this energy is transformed into kinetic energy $E_{\text{k}} = \frac12 mv^{2}$ at the bottom. Setting the two equal (energy conservation) gives  

\[
mgh = \frac12 mv^{2}\;\Longrightarrow\; v = \sqrt{2gh}.
\]

The mass $m$ cancels out, leaving a speed that depends only on $g$ and $h$. The shape of the slide influences the distance travelled but not the vertical drop; with negligible friction, the same $h$ yields the same $v$ regardless of curvature.

**Ground it in real life**  
Think of a water droplet sliding down a smooth, curved gutter versus a straight metal rod of the same height. Whether the droplet follows a winding path or a straight line, when it reaches the bottom its speed is determined by how far it fell vertically, not by its weight or the gutter’s shape.

**Worked example**  
A playground slide is 2.5 m high. Ignoring friction, find the speed of a 30 kg child at the bottom.

1. Use $v = \sqrt{2gh}$ with $g = 9.8\ \text{m s}^{-2}$ and $h = 2.5\ \text{m}$:  
   \[
   v = \sqrt{2 \times 9.8 \times 2.5}
     = \sqrt{49}
     = 7.0\ \text{m s}^{-1}.
   \]

2. Notice that the child’s mass never entered the calculation; a lighter or heavier child would have the same $7.0\ \text{m s}^{-1}$ speed under the same conditions.

**Common mistake or misconception**  
Students often think a heavier child will slide faster because “more weight means more force,” but the extra weight also increases inertia proportionally, so the speed remains unchanged.

**Try this →**  
If two slides have the same height $h$ but one is longer because it is more gently curved, will a child reach the bottom faster on the longer slide? (Think about the role of friction.)
~~~~

**Grade:** ✅ correct — The tutor correctly states that the speed depends only on the height, explicitly noting that mass and shape do not affect the final velocity (under the assumption of negligible friction). The derivation and explanation align perfectly with the textbook passage and physics principles.

</details>

<a id="q35"></a>
<details><summary>#35 · ✅ correct · Class 8 · Class 8 Science — Curiosity · How does temperature affect the solubility of solids and gases in liquids?</summary>

**Question:** How does temperature affect the solubility of solids and gases in liquids?

**Source:** Class 8 Science — *Curiosity*, chapter `hecu109`, page 16

**Reference answer:** In liquids, the solubility of solids generally increases when the temperature rises. Conversely, the solubility of gases decreases as the temperature increases.

**Key points:** Solubility of solids increases with temperature; Solubility of gases decreases with temperature

**Retrieval top 3:** `hecu109`, `hecu109`, `hecu107` (gold chapter ranked first)

**Citations returned:** `hecu109` p.15, `hecu109` p.7 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-120b]` · **Latency:** 9.95s

**Tutor's answer:**

~~~~text
**Direct answer**  
When the temperature of a liquid is raised, most solid solutes dissolve more readily, while most gaseous solutes become less soluble.

**Why it works (the mechanism)**  
*Solids*: Dissolving a solid requires breaking the attractive forces that hold its particles together. Higher temperature gives the solvent molecules extra kinetic energy, which helps them overcome these forces and surround the solid particles, forming new solute‑solvent interactions. Hence, solubility of solids usually **increases** with temperature.  
*Gases*: Gas molecules are held in the liquid by weak intermolecular attractions. Raising the temperature increases the kinetic energy of the gas molecules, making them more likely to escape back into the gas phase. Consequently, the liquid can hold **fewer** gas molecules, so gas solubility **decreases** with temperature.

**Ground it in real life**  
- A spoonful of sugar disappears quickly in hot tea but dissolves slowly in iced tea.  
- A chilled carbonated drink stays fizzy, but when left out in the sun the bubbles disappear as the CO₂ escapes.

**Worked example / fresh analogy**  
*Analogy*: Think of a crowded bus (the liquid) trying to accommodate passengers (solute particles).  
- For solid passengers, the bus driver (temperature) opens the doors wider, allowing more people to board comfortably – the bus can hold more solid passengers.  
- For gas passengers, a warm day makes everyone restless; they start pushing to get off, so fewer gas passengers stay inside.  
Thus, raising temperature “opens the doors” for solids but “pushes out” gases.

**Common mistake or misconception**  
Students often assume that **all** solutes become more soluble when the liquid is heated, forgetting that gases behave oppositely.

**Try this →**  
If you dissolve a fixed amount of salt in 100 mL of water at 20 °C and then heat the solution to 80 °C, will the amount of salt that can stay dissolved increase, decrease, or stay the same? (Answer in your head.)
~~~~

**Grade:** ✅ correct — The tutor's answer correctly states that the solubility of solids increases with temperature and the solubility of gases decreases with temperature, covering both key points required. The additional explanations and examples are factually accurate and do not contradict the textbook passage.

</details>

<a id="q36"></a>
<details><summary>#36 · ✅ correct · Class 8 · Class 8 Mathematics — Ganita Prakash Part 1 · What are the main properties of a parallelogram regarding its sides and angles?</summary>

**Question:** What are the main properties of a parallelogram regarding its sides and angles?

**Source:** Class 8 Mathematics — *Ganita Prakash* Part 1, chapter `hegp104`, page 0

**Reference answer:** In a parallelogram, the opposite sides are equal in length and parallel to each other. Additionally, the opposite angles are equal, and the adjacent angles add up to 180 degrees.

**Key points:** Opposite sides are equal; Opposite sides are parallel; Opposite angles are equal; Adjacent angles sum to 180 degrees

**Retrieval top 3:** `hegp104`, `hegp104`, `hegp104` (gold chapter ranked first)

**Citations returned:** `hegp104` p.0 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-120b]` · **Latency:** 6.77s

**Tutor's answer:**

~~~~text
**Direct answer**  
In a parallelogram:  
- Opposite sides are parallel and equal in length.  
- Opposite angles are equal.  
- Any two adjacent angles add up to $180^\circ$ (they are supplementary).

**Why it works (the mechanism)**  
When two pairs of opposite sides are parallel, each pair acts as a transversal for the other. Parallel lines cut by a transversal give equal alternate interior angles, which makes the opposite angles of the parallelogram equal. The same parallelism also forces each pair of adjacent angles to be supplementary because the interior angles on the same side of a transversal sum to $180^\circ$. By drawing a diagonal, the parallelogram is split into two congruent triangles; congruence (SSS) shows the opposite sides have the same length.

**Ground it in real life**  
Think of a typical Indian newspaper page when it’s folded: the front and back panels form a parallelogram. The left and right edges are parallel and of the same length, and the top and bottom edges are also parallel and equal. The corners opposite each other look identical because their angles are equal, and the top‑right and top‑left corners together make a straight line ($180^\circ$).

**Worked example**  
Consider parallelogram $ABCD$ with $\angle A = 70^\circ$.  
- Since adjacent angles are supplementary: $\angle B = 180^\circ - 70^\circ = 110^\circ$.  
- Opposite angles are equal, so $\angle C = 70^\circ$ and $\angle D = 110^\circ$.  
If side $AB = 8\,$cm, then opposite side $CD = 8\,$cm (equal lengths).  

**Common mistake**  
Students often think *all* sides of a parallelogram are equal; that property belongs only to a rhombus, a special type of parallelogram.

**Try this →**  
If one interior angle of a parallelogram is $45^\circ$, what is the measure of each of the other three angles?
~~~~

**Grade:** ✅ correct — The tutor's answer explicitly states all four required properties: opposite sides are equal, opposite sides are parallel, opposite angles are equal, and adjacent angles sum to 180 degrees. The additional explanations and examples are factually accurate and do not contradict the textbook passage.

</details>

<a id="q37"></a>
<details><summary>#37 · ✅ correct · Class 9 · Class 9 Social Science — Understanding Society: India and Beyond · What is the main difference between how governments and enterprises use economic data for…</summary>

**Question:** What is the main difference between how governments and enterprises use economic data for their decision-making?

**Source:** Class 9 Social Science — *Understanding Society: India and Beyond*, chapter `iest108`, page 4

**Reference answer:** Governments use revenue from taxation to plan spending on infrastructure and welfare programs. In contrast, enterprises analyze market trends and new innovations to better serve customers and maximize their profits.

**Key points:** Governments plan expenditure on infrastructure and welfare; Enterprises aim to serve customers and maximize profits; Enterprises study market trends and innovations

**Retrieval top 3:** `iest108`, `iest108`, `iest108` (gold chapter ranked first)

**Citations returned:** `iest108` p.4 · **Badge:** Extended reference · **Answered by:** `Groq[openai/gpt-oss-120b]` · **Latency:** 6.25s

**Tutor's answer:**

~~~~text
**Direct answer**  
Governments analyse economic data to plan and allocate resources for the overall welfare and stability of society, whereas enterprises use the same kind of data mainly to improve efficiency, competitiveness and profit.

**Why it works (the mechanism)**  
- **Government**: looks at aggregate indicators such as national income, unemployment, inflation, and sector‑wise output. These figures help formulate policies (subsidies, taxes, public‑investment) that aim at equitable distribution, price stability and long‑term growth.  
- **Enterprise**: focuses on firm‑level data like production costs, market demand, price elasticity and competitor prices. This information is used to optimise output, set prices and maximise profit margins. The differing objectives—public welfare vs. profit—drive the distinct ways data are interpreted and acted upon.

**Ground it in real life**  
- The Ministry of Agriculture studies crop‑yield statistics to decide how much fertilizer subsidy to give farmers, ensuring food security.  
- A local mango‑processing unit examines last‑year sales numbers and input costs to decide whether to increase its production capacity this season.

**Worked analogy**  
Think of a **school principal** and a **student** preparing for exams:  
- The principal looks at the school’s overall performance data (average scores, attendance, resource gaps) to allocate funds for libraries, labs, or teacher training—aiming to uplift the whole school.  
- The student examines his own marks in each subject, identifies weak areas, and decides how much time to spend on each topic to raise his personal score.  
Both use data, but the principal’s goal is the collective good of the school, while the student’s goal is personal achievement. This mirrors how governments and enterprises treat economic data.

**Common mistake or misconception**  
Students often assume that governments and businesses treat data identically, forgetting that governments prioritize societal objectives while enterprises prioritize profit.

**Try this →**  
If a city’s traffic‑congestion data show a 20 % rise in peak‑hour jams, would the municipal council and a ride‑sharing company likely use that information for the same purpose? Why?
~~~~

**Grade:** ✅ correct — The answer correctly identifies that governments plan for welfare/infrastructure (implied by 'overall welfare', 'public-investment', and the example of fertilizer subsidies) and that enterprises aim to maximize profits by studying market trends (implied by 'market demand', 'competitor prices', and the mango unit example). All key points are present or clearly implied, and there are no factual errors.

</details>

<a id="q38"></a>
<details><summary>#38 · ✅ correct · Class 9 · Class 9 Science · How do bacteria help in the nitrogen cycle, and what are the specific steps involved in ni…</summary>

**Question:** How do bacteria help in the nitrogen cycle, and what are the specific steps involved in nitrification?

**Source:** Class 9 Science, chapter `iesc113`, page 13

**Reference answer:** Nitrogen-fixing bacteria like Rhizobium and Azotobacter convert atmospheric nitrogen gas into ammonia. In the nitrification process, nitrifying bacteria first convert ammonia into nitrite, and then other bacteria convert that nitrite into nitrate.

**Key points:** Nitrogen-fixing bacteria convert atmospheric N2 into ammonia; Nitrification involves converting ammonia into nitrite; Nitrification also involves converting nitrite into nitrate

**Retrieval top 3:** `iesc112`, `iesc113`, `iesc113` (gold chapter not ranked first)

**Citations returned:** — · **Badge:** AI knowledge · **Answered by:** `Groq[openai/gpt-oss-20b]` · **Latency:** 8.91s

**Tutor's answer:**

~~~~text
**Direct answer**  
Bacteria are the main workers of the nitrogen cycle. They convert atmospheric nitrogen into forms that plants can use, and they also recycle nitrogen back into the air. In nitrification, two groups of bacteria act in sequence: first, *Nitrosomonas* oxidise ammonia (NH₃) to nitrite (NO₂⁻); then *Nitrobacter* oxidise nitrite to nitrate (NO₃⁻).  

**Why it works (the mechanism)**  
1. **Nitrogen fixation** – free‑living bacteria (e.g., *Azotobacter*) and symbiotic bacteria in legume roots (*Rhizobium*) use the enzyme nitrogenase to break the strong N≡N bond in atmospheric N₂, producing ammonia (NH₃).  
2. **Ammonification** – decomposers (bacteria and fungi) break down dead organic matter, releasing ammonia back into the soil.  
3. **Nitrification** – a two‑step aerobic process:  
   * *Nitrosomonas* (ammonia‑oxidising bacteria) convert NH₃ + O₂ → NO₂⁻ + H₂O + energy.  
   * *Nitrobacter* (nitrite‑oxidising bacteria) convert NO₂⁻ + O₂ → NO₃⁻ + energy.  
4. **Assimilation** – plants absorb NO₃⁻ (or NH₄⁺) through their roots and use it to build proteins and nucleic acids.  
5. **Denitrification** – anaerobic bacteria (e.g., *Pseudomonas*) reduce NO₃⁻ back to N₂ gas, completing the cycle.  

**Ground it in real life**  
Think of a local vegetable market: farmers grow beans (a legume) that host *Rhizobium* in their root nodules. These bacteria fix nitrogen, giving the beans the nutrients they need. After the harvest, the fallen leaves decompose, releasing ammonia. In the soil, *Nitrosomonas* and *Nitrobacter* work like a two‑stage cleaning crew, turning ammonia into nitrate that the next crop can absorb. When the soil becomes water‑logged, denitrifying bacteria kick in, sending excess nitrogen back into the atmosphere as harmless N₂ gas.  

**Worked analogy**  
Imagine a three‑stage factory:  
- **Stage 1 (Fixation)** – a machine (nitrogenase) breaks a tough lock (N₂ triple bond) to produce a key (NH₃).  
- **Stage 2 (Nitrification)** – two workers:  
  *Worker A* (Nitrosomonas) changes the key into a half‑key (NO₂⁻).  
  *Worker B* (Nitrobacter) then turns the half‑key into a full key (NO₃⁻).  
- **Stage 3 (Denitrification)** – a final worker (denitrifying bacteria) takes the full key and turns it back into the original lock (N₂), ready to start the cycle again.  

**Common mistake or misconception**  
Students often think that nitrification is a single step; in reality it requires two distinct bacterial groups, each performing a separate oxidation.  

**Try this →**  
If a farmer applies urea (NH₂CONH₂) to the field, what bacterial processes will convert it into a form the plants can use, and what will happen if the soil becomes saturated with water?
~~~~

**Grade:** ✅ correct — The tutor's answer correctly identifies that nitrogen-fixing bacteria convert atmospheric N2 into ammonia and accurately describes the two steps of nitrification (ammonia to nitrite by Nitrosomonas, and nitrite to nitrate by Nitrobacter). All key points are present and factually accurate according to the provided textbook passage.

</details>

<a id="q39"></a>
<details><summary>#39 · ✅ correct · Class 10 · Class 10 Political Science — Democratic Politics II · Why does the text say that people complaining about democracy is actually a sign of its su…</summary>

**Question:** Why does the text say that people complaining about democracy is actually a sign of its success?

**Source:** Class 10 Political Science — *Democratic Politics II*, chapter `jess405`, page 10

**Reference answer:** Complaints show that people have developed political awareness and the ability to critically examine those in power. This indicates that individuals have transformed from passive subjects into active citizens who value their democratic rights and expect more from the system.

**Key points:** Complaints indicate people have developed awareness and critical thinking about power holders.; It shows the transformation of people from subjects to citizens.; It reflects that citizens value their democratic rights and expect improvements.

**Retrieval top 3:** `jess405`, `jess405`, `jess405` (gold chapter ranked first)

**Citations returned:** `jess405` p.10, `jess405` p.4 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-120b]` · **Latency:** 6.68s

**Tutor's answer:**

~~~~text
**Direct answer**  
The text says that complaints about democracy are a sign of its success because they show that people are aware of their rights, can voice expectations, and treat themselves as citizens rather than passive subjects.

**Why it works (the mechanism)**  
In a democratic system freedom of expression is guaranteed. When citizens use that freedom to criticize or demand better governance, it means they feel empowered to hold power‑holders accountable. This critical awareness and public dissent indicate that democracy is functioning: the people are not merely obeying, they are actively participating and expecting improvement.

**Ground it in real life**  
Think of the massive street protests in India when a new law is announced. Citizens gather, chant, and file petitions because they believe their voices can influence the government. Their willingness to speak out demonstrates that democratic institutions are allowing such expression.

**Fresh analogy**  
Imagine a classroom where the teacher encourages every student to raise a hand and question the lesson. If students start pointing out mistakes or suggesting better ways to solve a problem, the teacher knows the class is engaged and the learning environment is healthy. Similarly, when citizens point out flaws in governance, it shows the democratic “classroom” is active and effective.

**Common mistake or misconception**  
A common trap is to think that any criticism means democracy is failing; in reality, open criticism is a hallmark of a thriving democracy.

**Try this →**  
Recall a recent news story where people protested a government decision. How does that protest illustrate the idea that dissent signals a successful democracy?
~~~~

**Grade:** ✅ correct — The tutor's answer correctly identifies that complaints indicate political awareness and critical thinking (Key Point 1), explicitly mentions the transformation from passive subjects to active citizens (Key Point 2), and notes that citizens value their rights and expect improvements (Key Point 3). The additional context and analogy are factually sound and do not contradict the textbook.

</details>

<a id="q40"></a>
<details><summary>#40 · ✅ correct · Class 8 · Class 8 Science — Curiosity · What is the difference between a population and a community in a habitat?</summary>

**Question:** What is the difference between a population and a community in a habitat?

**Source:** Class 8 Science — *Curiosity*, chapter `hecu112`, page 5

**Reference answer:** A population is a group of the same type of organisms living in a habitat at a specific time. A community consists of different populations of various organisms, such as plants, animals, and microorganisms, that share the same habitat and interact with one another.

**Key points:** Population is a group of the same type of organisms; Community comprises different populations; Community includes biotic components like plants, animals, and microorganisms

**Retrieval top 3:** `hecu112`, `hecu112`, `hecu112` (gold chapter ranked first)

**Citations returned:** `hecu112` p.5, `hecu112` p.18 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-120b]` · **Latency:** 7.17s

**Tutor's answer:**

~~~~text
**Direct answer**  
A *population* is all the individuals of the same species living together in a particular habitat at a given time. A *community* is the assemblage of several different populations (different species) that coexist in the same habitat and interact with one another.

**Why it works (the mechanism)**  
- *Population* refers to one species only, so all members share the same genetic makeup and ecological needs (food, water, space).  
- *Community* includes multiple species, each forming its own population, and the members depend on each other for food, shelter, pollination, etc. The interactions—such as predation, competition, and symbiosis—link the populations together, making the community a more complex, inter‑dependent unit.

**Ground it in real life**  
Imagine a small garden pond. The 30 koi fish swimming there constitute a *population* of one species. The pond also contains 50 water lilies, 200 tiny algae cells, and 15 snails. Together, the fish, lilies, algae, and snails form the *community* of the pond, because they all live in the same water and affect each other’s survival.

**Analogy**  
Think of a *population* as a single classroom of students studying the same subject. A *community* is the whole school, where many classrooms (different subjects) exist together, share facilities, and influence each other’s daily routine. Just as the school’s atmosphere depends on the interaction of all classrooms, a biological community depends on the interaction of all its populations.

**Common mistake or misconception**  
Students often treat “population” and “community” as synonyms, but a population involves only one species, whereas a community comprises several different species living together.

**Try this →**  
If a pond has 40 guppies (one species) and also 20 water plants, 15 snails, and 10 dragonfly larvae, which group is the *population* and which is the *community*?
~~~~

**Grade:** ✅ correct — The tutor's answer correctly defines a population as a group of the same type of organisms (species) and a community as different populations sharing a habitat. It explicitly mentions biotic components like plants, animals, and microorganisms (via the pond example with lilies, fish, algae, and snails), covering all key points without factual errors.

</details>

<a id="q41"></a>
<details><summary>#41 · 🟡 partial · Class 9 · Class 9 Social Science — Understanding Society: India and Beyond · How does the Panchayati Raj system connect to ancient Indian political thought?</summary>

**Question:** How does the Panchayati Raj system connect to ancient Indian political thought?

**Source:** Class 9 Social Science — *Understanding Society: India and Beyond*, chapter `iest101`, page 9

**Reference answer:** The Panchayati Raj system is a modern institution that promotes local self-governance and public participation at the village level. It reflects the ancient Indian tradition of viewing power as a responsibility and linking politics with social life and welfare, showing how these old ideas continue to shape India's democratic practices today.

**Key points:** Panchayati Raj promotes local self-governance and public participation at the village level; It is an example of how ancient ideas continue to shape modern democratic practices; Ancient Indian political thought viewed power as a responsibility and linked politics with social welfare

**Retrieval top 3:** `iest101`, `iest106`, `iest105` (gold chapter ranked first)

**Citations returned:** `iest101` p.9 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-120b]` · **Latency:** 6.23s

**Tutor's answer:**

~~~~text
**Direct answer**  
The Panchayati Raj system mirrors ancient Indian ideas of local self‑governance, such as the Vedic *Sabhā* and *Samiti* assemblies, by giving villages a democratic platform to discuss and decide on community matters.

**Why it works (the mechanism)**  
In Vedic times, the *Sabhā* (a smaller, elite body) handled judicial functions while the *Samiti* (a larger gathering) dealt with policy and political affairs, representing a broader segment of society. This dual‑assembly model introduced the principle of collective decision‑making and public participation. Modern Panchayati Raj adopts the same spirit: elected representatives at the village (Gram Panchayat), block (Panchayat Samiti), and district (Zila Parishad) levels deliberate on local issues, ensuring that governance is rooted in the community rather than imposed from above.

**Ground it in real life**  
When a village council meets to decide on building a new well, it works just like a *Samiti*—farmers, shopkeepers, and women gather, voice their concerns, and vote on the plan. The decision is then implemented locally, reflecting the ancient practice of people directly influencing policies that affect them.

**Worked analogy**  
Think of a school’s student council: the class representatives (like the *Sabhā*) handle minor disputes, while the whole‑school assembly (like the *Samiti*) debates major rules such as the dress code. Both bodies give students a say in matters that impact daily life. Similarly, Panchayati Raj provides villagers a structured forum to manage local affairs, just as the ancient assemblies gave citizens a voice in governance.

**Common mistake or misconception**  
Students often think Panchayati Raj is merely a top‑down administrative layer, but it actually embodies the ancient democratic principle of grassroots participation.

**Try this →**  
If a Gram Panchayat decides to allocate funds for a school library, which ancient assembly—*Sabhā* or *Samiti*—does this decision resemble, and why?
~~~~

**Grade:** 🟡 partial — The answer correctly identifies that Panchayati Raj promotes local self-governance and public participation, and it connects this to ancient ideas of collective decision-making. However, it misses the specific key point from the textbook that ancient Indian political thought viewed 'power as a responsibility' and explicitly linked politics with 'social welfare' and 'economics,' instead focusing primarily on the structural parallel of assemblies (Sabha/Samiti).

</details>

<a id="q42"></a>
<details><summary>#42 · ✅ correct · Class 8 · Class 8 Science — Curiosity · Why are convex mirrors used at road intersections and in big stores?</summary>

**Question:** Why are convex mirrors used at road intersections and in big stores?

**Source:** Class 8 Science — *Curiosity*, chapter `hecu110`, page 6

**Reference answer:** Convex mirrors are used at road intersections and sharp bends because they provide a wider area of the road behind, helping drivers see vehicles from the other side to prevent collisions. In big stores, they are installed to monitor a large area to help deter thefts.

**Key points:** They provide a wider field of view/area.; At roads, they help prevent collisions by showing visibility from both sides.; In stores, they are used to monitor large areas to deter theft.

**Retrieval top 3:** `hecu110`, `hecu110`, `hecu110` (gold chapter ranked first)

**Citations returned:** `hecu110` p.6, `hecu110` p.14 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-120b]` · **Latency:** 6.23s

**Tutor's answer:**

~~~~text
**Direct answer**  
Convex mirrors are placed at road intersections and in big stores because their outward‑curved surface spreads reflected light over a wide field of view. This lets drivers see traffic coming from the opposite side and lets shopkeepers monitor a large area, helping to avoid collisions and deter theft.

**Why it works (the mechanism)**  
A convex mirror has a bulging (outward) surface. When a light ray strikes it, the ray reflects outward, diverging away from the mirror. Because the reflected rays spread out, the mirror can capture light from a larger angular region than a flat or concave mirror. The brain interprets these diverging rays as an erect (upright) but diminished (smaller) image of whatever is behind the mirror. The trade‑off—smaller image for a wider view—is exactly what is needed at intersections and in stores.

**Ground it in real life**  
- At a busy crossroads, a convex side‑view mirror mounted on a pole lets drivers on either road glimpse the opposite lane, even though the vehicles are far away.  
- In a supermarket, a convex surveillance mirror placed near a shelf corner lets the shopkeeper see shoppers approaching from many directions without moving around.

**Worked example / fresh analogy**  
Think of a fish‑eye camera lens. The lens is convex and captures a panoramic scene, but the picture on the screen looks smaller and slightly distorted. Similarly, a convex mirror “captures” a panoramic view of the road or store floor, presenting it as a reduced, upright image that the observer can scan quickly.

**Common mistake or misconception**  
Students often think a convex mirror makes objects look larger; in fact, the image is always smaller (diminished) even though the view is wider.

**Try this →**  
If you stand 3 m away from a convex side‑view mirror, will the image of you appear larger or smaller than your actual size? (Answer: smaller.)
~~~~

**Grade:** ✅ correct — The tutor's answer correctly identifies that convex mirrors provide a wider field of view, which helps prevent collisions at road intersections by showing visibility from both sides, and deters theft in stores by monitoring large areas. All key points from the reference answer are present and factually accurate.

</details>

<a id="q43"></a>
<details><summary>#43 · ✅ correct · Class 8 · Class 8 Mathematics — Ganita Prakash Part 2 · If a small pump takes 3 hours to fill a tank and a large pump takes 2 hours, how long will…</summary>

**Question:** If a small pump takes 3 hours to fill a tank and a large pump takes 2 hours, how long will it take to fill the tank if both are used together?

**Source:** Class 8 Mathematics — *Ganita Prakash* Part 2, chapter `hegp203`, page 0

**Reference answer:** It will take 1.2 hours (or 6/5 hours) to fill the tank. This is calculated by adding their individual rates (1/3 + 1/2 = 5/6 tanks per hour) and then dividing the total work (1 tank) by this combined rate.

**Key points:** The combined rate is 5/6 tanks per hour; The total time required is 1.2 hours or 6/5 hours

**Retrieval top 3:** `hegp203`, `hegp203`, `hegp203` (gold chapter ranked first)

**Citations returned:** `hegp203` p.0 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-120b]` · **Latency:** 11.23s

**Tutor's answer:**

~~~~text
**Direct answer**  
When the small pump (3 h per tank) and the large pump (2 h per tank) work together, the tank will be filled in **1.2 hours** (i.e., 1 hour 12 minutes).

**Why it works (the mechanism)**  
Each pump has a *rate of filling* – the fraction of a tank it can fill in one hour.  

- Small pump: $\displaystyle \frac{1\text{ tank}}{3\text{ h}} = \frac{1}{3}\text{ tank/h}$  
- Large pump: $\displaystyle \frac{1\text{ tank}}{2\text{ h}} = \frac{1}{2}\text{ tank/h}$  

When both operate simultaneously, their rates add (they work independently).  

\[
\text{Combined rate}= \frac{1}{3}+\frac{1}{2}= \frac{2}{6}+\frac{3}{6}= \frac{5}{6}\text{ tank/h}
\]

Time required $T$ is the total work (1 tank) divided by the combined rate:

\[
T=\frac{1\text{ tank}}{5/6\text{ tank/h}}=\frac{6}{5}\text{ h}=1.2\text{ h}
\]

**Ground it in real life**  
Think of two water taps filling a bucket: one drips slowly (takes 3 h alone) and the other faster (takes 2 h). Opening both taps at once lets the bucket fill quicker because the water from each tap adds up.

**Worked example**  
Suppose you have a garden pond that needs 500 litres of water.  
- Small pump delivers $500/3 \approx 166.7$ L per hour.  
- Large pump delivers $500/2 = 250$ L per hour.  

Combined delivery = $166.7 + 250 = 416.7$ L per hour.  

Time to fill 500 L:

\[
T = \frac{500\text{ L}}{416.7\text{ L/h}} \approx 1.2\text{ h}
\]

So the pond fills in the same 1.2 hours as the tank.

**Common mistake**  
Students often add the times (3 h + 2 h) and then divide by 2, giving 2.5 h, which ignores that rates, not times, add when machines work together.

**Try this →**  
If a third pump can fill the same tank in 6 hours, how long will it take when all three pumps operate together?
~~~~

**Grade:** ✅ correct — The tutor correctly calculates the combined rate as 5/6 tanks per hour and the total time as 1.2 hours (or 6/5 hours), matching the reference answer and textbook passage exactly. The explanation is clear, factually accurate, and addresses the specific question asked.

</details>

<a id="q44"></a>
<details><summary>#44 · ✅ correct · Class 8 · Class 8 Social Science — Exploring Society: India and Beyond · How can citizens help make our democracy stronger and overcome challenges related to the l…</summary>

**Question:** How can citizens help make our democracy stronger and overcome challenges related to the legislature?

**Source:** Class 8 Social Science — *Exploring Society: India and Beyond*, chapter `hees106`, page 22

**Reference answer:** Citizens can strengthen democracy by staying informed about important social matters and actively participating in public discussions. They should engage in debates on digital platforms provided by government departments and interact constructively with political representatives regarding policies.

**Key points:** Staying informed on matters of importance to society; Participating in public discussions and debates (including on digital spaces); Constructively engaging with political representatives on policies

**Retrieval top 3:** `hees106`, `hees106`, `hees106` (gold chapter ranked first)

**Citations returned:** `hees106` p.22, `hees106` p.21, `hees106` p.19 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-20b]` · **Latency:** 6.23s

**Tutor's answer:**

~~~~text
**Direct answer**  
Citizens can strengthen democracy by staying informed, asking questions, sharing their views, and actively participating in public discussions and elections. They should use digital platforms, attend local meetings, and engage with elected representatives to hold them accountable and ensure that debates in the legislature are constructive and focused on public needs.

**Why it works (the mechanism)**  
1. **Information** – When citizens know what laws are being debated, they can judge whether those laws serve the public interest.  
2. **Questioning** – By asking legislators questions, citizens bring issues to the forefront and force representatives to explain their positions.  
3. **Sharing views** – Public opinion, expressed through media or community forums, creates pressure on lawmakers to act responsibly.  
4. **Participation** – Attending debates, town‑hall meetings, or voting lets citizens directly influence the choice of representatives and the policies they enact.  
5. **Accountability** – Continuous engagement keeps legislators answerable; if they ignore public concerns, citizens can demand changes or even replace them in elections.  
These steps create a feedback loop where informed citizens shape legislation, and effective legislation, in turn, improves governance.

**Flowchart of the process**

**Ground it in real life**  
Think of a local panchayat meeting where villagers discuss a new road plan. If everyone reads the proposal, asks the sarpanch why a certain route was chosen, and shares their concerns, the plan can be improved. When the next village election comes, those who were active can vote for a leader who listened and acted responsibly.

**Common mistake or misconception**  
Students often think that only politicians can change laws, forgetting that an informed and active citizenry is the engine that keeps the legislature responsive and accountable.
~~~~

**Grade:** ✅ correct — The tutor's answer covers all three key points: staying informed, participating in public discussions (including digital platforms), and engaging constructively with political representatives. The additional details provided are factually accurate and consistent with the textbook passage.

</details>

<a id="q45"></a>
<details><summary>#45 · ✅ correct · Class 8 · Class 8 Science — Curiosity · Why does the air stop moving between two connected balloons when they are left for a while…</summary>

**Question:** Why does the air stop moving between two connected balloons when they are left for a while?

**Source:** Class 8 Science — *Curiosity*, chapter `hecu106`, page 9

**Reference answer:** Air flows from the inflated balloon to the uninflated one because the pressure is higher in the inflated balloon. This flow continues until the air pressure in both balloons becomes equal. Once the pressures are equal, the movement of air stops and the balloons reach almost the same size.

**Key points:** Air moves from high pressure to low pressure; Flow stops when pressure in both balloons becomes equal; Balloons attain almost the same size at equilibrium

**Retrieval top 3:** `hecu106`, `hecu106`, `hecu106` (gold chapter ranked first)

**Citations returned:** `hecu106` p.9 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-120b]` · **Latency:** 6.15s

**Tutor's answer:**

~~~~text
**Direct answer**  
Air stops moving between the two balloons because the pressure inside both balloons becomes equal. When the pressures are the same, there is no longer a pressure gradient to drive the flow of air.

**Why it works (the mechanism)**  
- Initially, one balloon is more inflated, so the air inside it exerts a higher pressure than the air in the less‑inflated balloon.  
- Air always moves from a region of higher pressure to a region of lower pressure.  
- As air passes through the connecting tube, the more‑inflated balloon loses some air while the less‑inflated balloon gains air.  
- This exchange continues until the pressures in the two balloons equalise.  
- Once the pressures are equal, the driving force (the pressure difference) disappears, so the net movement of air stops.

**Ground it in real life**  
Think of a kitchen pressure cooker: when the cooker is sealed, steam builds up high pressure inside. If you open the valve, steam rushes out because the pressure inside is higher than the atmospheric pressure outside. When the cooker cools and the internal pressure drops to match the outside pressure, the steam flow stops. The balloons behave the same way—air moves only while a pressure difference exists.

**Worked example / fresh analogy**  
*Analogy*: Imagine two rooms connected by a door. One room is crowded with people (high “pressure”), the other is almost empty (low “pressure”). People will walk through the door into the empty room until both rooms have roughly the same number of people. At that point, no one feels a push to move, so the flow stops. In the balloon case, “people” are air molecules and the “crowdedness” is air pressure.

**Common mistake or misconception**  
Students often think the air stops because the balloons become perfectly the same size; actually, the flow stops when the pressures equalise, which usually makes the balloons almost, but not necessarily exactly, the same size.

**Try this →**  
If one balloon initially has a pressure of 110 kPa and the other 100 kPa, after a long time the pressure in both balloons will be **approximately 105 kPa** (the average), and the air will no longer move.
~~~~

**Grade:** ✅ correct — The tutor's answer correctly explains that air moves from high to low pressure and stops when the pressures equalize, resulting in balloons of almost the same size. It covers all key points from the textbook passage without any factual errors.

</details>

<a id="q46"></a>
<details><summary>#46 · ✅ correct · Class 8 · Class 8 Science — Curiosity · Why does the weight of an object change on different planets like the Moon or Mars, but it…</summary>

**Question:** Why does the weight of an object change on different planets like the Moon or Mars, but its mass stays the same?

**Source:** Class 8 Science — *Curiosity*, chapter `hecu105`, page 14

**Reference answer:** Mass is the amount of matter in an object and remains constant regardless of location. Weight is the gravitational force exerted by a planet on the object, which varies because gravitational force differs from place to place and between different planets.

**Key points:** Mass is the amount of matter and does not change.; Weight is the gravitational force pulling the object.; Gravitational force varies between different planets or locations.

**Retrieval top 3:** `hecu105`, `hecu105`, `hecu109` (gold chapter ranked first)

**Citations returned:** `hecu105` p.14 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-120b]` · **Latency:** 6.51s

**Tutor's answer:**

~~~~text
**Direct answer**  
Weight changes on different planets because the gravitational pull (the force with which the planet attracts the object) is different on each planet. Mass, which is the amount of matter in the object, does not depend on the planet and therefore remains the same.

**Why it works (the mechanism)**  
Weight is the gravitational force given by the formula  

\[
\text{Weight} = \text{mass} \times g
\]

where **mass** (measured in kilograms) is the quantity of matter, and **\(g\)** is the acceleration due to gravity of the planet (measured in m s\(^{-2}\)).  
On Earth \(g \approx 10\) m s\(^{-2}\); on the Moon \(g \approx 1.6\) m s\(^{-2}\); on Mars \(g \approx 3.8\) m s\(^{-2}\). Since \(g\) varies, the product \(mg\) – the weight – also varies, while the mass \(m\) stays unchanged.

**Ground it in real life**  
When you step onto a weighing scale on Earth, it shows about 500 N for a 50 kg person. If the same person were on the Moon, the scale would show only about 80 N, yet the person’s body still contains the same 50 kg of matter. The scale’s reading changes because the Moon’s gravity is weaker.

**Worked example**  
A school bag has a mass of \(8\) kg.  

- On Earth:  
  \[
  \text{Weight}_{\text{Earth}} = 8\ \text{kg} \times 10\ \text{m s}^{-2} = 80\ \text{N}
  \]

- On Mars:  
  \[
  \text{Weight}_{\text{Mars}} = 8\ \text{kg} \times 3.8\ \text{m s}^{-2} = 30.4\ \text{N}
  \]

The bag’s mass stays \(8\) kg in both cases, but its weight drops from \(80\) N on Earth to \(30.4\) N on Mars because Mars’s gravity is weaker.

**Common mistake or misconception**  
Students often think “weight” and “mass” are the same and that an object’s weight is constant everywhere; actually, weight depends on the local gravitational acceleration, while mass is invariant.

**Try this →**  
If a 5 kg stone is placed on the Moon where \(g = 1.6\) m s\(^{-2}\), what is its weight in newtons?
~~~~

**Grade:** ✅ correct — The answer correctly defines mass as the amount of matter that remains constant and weight as the gravitational force that varies due to different gravitational accelerations on different planets. It includes all required key points and provides accurate calculations and examples consistent with the textbook data.

</details>

<a id="q47"></a>
<details><summary>#47 · ✅ correct · Class 10 · Class 10 Geography — Contemporary India II · Which state has the largest wind farm cluster in India, and what are the main sources used…</summary>

**Question:** Which state has the largest wind farm cluster in India, and what are the main sources used to produce biogas in rural areas?

**Source:** Class 10 Geography — *Contemporary India II*, chapter `jess105`, page 13

**Reference answer:** The largest wind farm cluster in India is located in Tamil Nadu, specifically stretching from Nagarcoil to Madurai. Biogas in rural areas is produced using shrubs, farm waste, and animal or human waste.

**Key points:** Largest wind farm cluster is in Tamil Nadu (Nagarcoil to Madurai); Biogas is produced from shrubs, farm waste, and animal/human waste

**Retrieval top 3:** `jess105`, `jess105`, `jess104` (gold chapter ranked first)

**Citations returned:** `jess105` p.13 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-120b]` · **Latency:** 6.85s

**Tutor's answer:**

~~~~text
**Direct answer**  
The biggest wind‑farm cluster in India is in **Tamil Nadu**, stretching from Nagarcoil to Madurai.  
Biogas in rural households is mainly produced from **shrubs, farm waste, animal waste and human waste**.

**Why it works (the mechanism)**  
Wind farms consist of many wind‑mills that convert the kinetic energy of moving air into electrical energy via rotating blades attached to a generator. The more consistent and strong the wind in a region, the larger the amount of electricity that can be generated, which is why Tamil Nadu, with its long coastal stretch, hosts the largest cluster.  

Biogas is generated through **anaerobic digestion** – the breakdown of organic material by microorganisms in the absence of oxygen. The organic feedstock (shrubs, crop residues, animal dung, human excreta) is placed in a sealed digester where bacteria decompose it, releasing a mixture of methane (CH₄) and carbon dioxide (CO₂). The methane is the combustible component that can be used for cooking, lighting or heating.

**Ground it in real life**  
You may have seen wind‑mills dotting the horizon near the coast of Tamil Nadu, their slow‑turning blades feeding electricity to nearby villages. In many Indian villages, families collect cow dung and kitchen waste, put it in a small underground pit, and after a few days obtain a steady flame of biogas for their stoves – a clean alternative to firewood.

**Fresh analogy**  
Think of a wind‑farm as a row of giant pinwheels that catch the wind’s “push” and turn it into a “pull” on an electric generator, just as a water‑wheel uses flowing water to lift a bucket. Similarly, a biogas digester works like a sealed compost heap: instead of producing a smelly pile, the microbes inside trap the released gases, turning kitchen scraps and animal waste into a useful fuel, much like a pressure cooker traps steam to cook food faster.

**Common mistake**  
Students often think biogas comes only from animal dung, forgetting that plant residues, shrubs and even human waste also serve as important feedstock.

**Try this →**  
If a village installs a biogas digester that receives equal amounts of farm waste and animal dung, which two sources together will provide the majority of the gas? (Answer: farm waste + animal waste.)
~~~~

**Grade:** ✅ correct — The tutor correctly identifies Tamil Nadu (Nagarcoil to Madurai) as the location of the largest wind farm cluster and lists shrubs, farm waste, and animal/human waste as the sources for biogas, matching the textbook passage and reference answer. The additional explanations regarding the mechanism of wind and biogas production are factually accurate and do not contradict the source material.

</details>

<a id="q48"></a>
<details><summary>#48 · ✅ correct · Class 10 · Class 10 Geography — Contemporary India II · What are sacred groves and how do tribal beliefs help in their conservation?</summary>

**Question:** What are sacred groves and how do tribal beliefs help in their conservation?

**Source:** Class 10 Geography — *Contemporary India II*, chapter `jess102`, page 5

**Reference answer:** Sacred groves are patches of virgin forests that have been preserved in their pristine form due to the age-old tribal belief in nature worship. This belief holds that all creations of nature must be protected, leading local communities to leave these specific forest areas untouched and ban any interference with them.

**Key points:** Sacred groves are virgin forests preserved in pristine form.; They are protected due to tribal beliefs in nature worship.; Local people leave these areas untouched and ban interference.

**Retrieval top 3:** `jess102`, `jess302`, `jess102` (gold chapter ranked first)

**Citations returned:** `jess102` p.5 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-120b]` · **Latency:** 17.71s

**Tutor's answer:**

~~~~text
**Direct answer**  
Sacred groves are small patches of forest that are left completely untouched because local people consider them holy. Tribal belief systems that view every element of nature as sacred lead communities to protect these groves and prohibit any kind of exploitation.

**Why it works (the mechanism)**  
- *Nature worship* (the belief that all creations of nature must be protected) creates a cultural rule that the grove is inviolable.  
- This rule translates into a *social taboo* against cutting trees, hunting, or gathering inside the grove.  
- Because the taboo is reinforced by rituals and reverence for specific trees (e.g., mahua, kadamba, tamarind, mango, peepal, banyan), community members voluntarily enforce the protection, keeping the grove in its virgin state.

**Ground it in real life**  
Think of a village temple that everyone respects; no one would dare deface it. Similarly, a sacred grove is treated like a living temple of the forest. In many Indian villages you will see a cluster of peepal or banyan trees that are never cut because they are believed to house deities.

**Fresh analogy**  
Imagine a school library that is declared “off‑limits” for any kind of damage because the principal says the books are the school’s treasure. Every student, out of respect for that rule, avoids tearing pages or writing on the books. In the same way, a sacred grove is the “library of nature” that the tribe declares off‑limits, and the community’s respect for the rule preserves the “books” (trees, animals, plants) inside it.

**Common mistake or misconception**  
Students often think sacred groves are protected only by government laws, but in reality they survive mainly because of deep‑rooted tribal beliefs and community enforcement.

**Try this →**  
If a village decides to cut a tree in a sacred grove, what cultural consequence would likely stop them? (Think of the belief that the grove is a dwelling of deities.)
~~~~

**Grade:** ✅ correct — The tutor's answer correctly identifies sacred groves as untouched virgin forests, attributes their preservation to tribal nature worship beliefs, and explains that local communities ban interference. All key points from the reference answer are present and accurate.

</details>

<a id="q49"></a>
<details><summary>#49 · ✅ correct · Class 8 · Class 8 Mathematics — Ganita Prakash Part 2 · If I multiply every number in a list by 5, how does the average change?</summary>

**Question:** If I multiply every number in a list by 5, how does the average change?

**Source:** Class 8 Mathematics — *Ganita Prakash* Part 2, chapter `hegp205`, page 0

**Reference answer:** The new average becomes 5 times the original average. This is because multiplying every value by a constant factor results in the sum being multiplied by that same factor, which in turn multiplies the average by that factor.

**Key points:** The new average is 5 times the original average; The average scales by the same factor as the data values

**Retrieval top 3:** `hegp205`, `hegp205`, `hegp205` (gold chapter ranked first)

**Citations returned:** `hegp205` p.0 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-120b]` · **Latency:** 4.54s

**Tutor's answer:**

~~~~text
**Direct answer**  
If every number in a list is multiplied by 5, the new average becomes five times the original average.

**Why it works (the mechanism)**  
The average (mean) of a set of numbers is  
\[
a=\frac{x_1+x_2+\dots+x_n}{n}.
\]  
Multiplying each term by 5 gives the new sum \((5x_1+5x_2+\dots+5x_n)=5(x_1+x_2+\dots+x_n)\).  
Dividing this new sum by the same count \(n\) yields  
\[
\text{New average}= \frac{5(x_1+x_2+\dots+x_n)}{n}=5\left(\frac{x_1+x_2+\dots+x_n}{n}\right)=5a.
\]  
Thus the average scales directly with the constant factor applied to every entry.

**Ground it in real life**  
Imagine the average weight of a basket of mangoes is 200 g. If each mango were artificially “heavier” by a factor of 5 (say we consider the weight in a different unit like “five‑gram blocks”), the average weight would become \(5 \times 200 = 1000\) g.

**Worked example**  
Suppose the numbers are \(2, 4, 6\).  
- Original average: \(\displaystyle a=\frac{2+4+6}{3}=4\).  
- Multiply each by 5: \(10, 20, 30\).  
- New average: \(\displaystyle \frac{10+20+30}{3}=20\).  
Indeed, \(20 = 5 \times 4\).

**Common mistake or misconception**  
Students sometimes think the average stays the same because the number of items doesn’t change; however, scaling every value changes the sum, and the average scales by the same factor.

**Try this →**  
If the average of a list is 12 and every element is multiplied by 5, what is the new average?
~~~~

**Grade:** ✅ correct — The tutor correctly states that the new average is 5 times the original average and explains that the average scales by the same factor as the data values. The mathematical derivation and examples are accurate and align with the textbook passage.

</details>

<a id="q50"></a>
<details><summary>#50 · ✅ correct · Class 9 · Class 9 Science · What are the main differences between the problems caused by errors in mitosis and errors…</summary>

**Question:** What are the main differences between the problems caused by errors in mitosis and errors in meiosis?

**Source:** Class 9 Science, chapter `iesc102`, page 15

**Reference answer:** Errors in mitosis lead to uncontrolled cell division, which can cause tumours and an abnormal number of chromosomes in body cells. In contrast, errors in meiosis can result in genetic disorders with developmental or physical issues, as well as early pregnancy loss or reduced fertility.

**Key points:** Mitosis errors cause uncontrolled cell division, tumours, and abnormal chromosome numbers in body cells.; Meiosis errors cause genetic disorders, developmental/physical problems, early pregnancy loss, or reduced fertility.

**Retrieval top 3:** `iesc102`, `iesc102`, `iesc111` (gold chapter ranked first)

**Citations returned:** — · **Badge:** AI knowledge · **Answered by:** `Groq[openai/gpt-oss-120b]` · **Latency:** 41.32s

**Tutor's answer:**

~~~~text
**Direct answer**  
Errors in mitosis mainly affect somatic (body) cells, leading to problems such as tissue malfunctions or cancers in the individual. Errors in meiosis affect germ cells, producing abnormal gametes that can cause chromosomal disorders (e.g., Down syndrome) in the offspring.

**Why it works (the mechanism)**  
- **Mitosis** copies the entire diploid set of chromosomes and separates sister chromatids. If a chromosome is lost, gained, or rearranged, every cell derived from that division carries the defect, so the defect can proliferate throughout the body.  
- **Meiosis** halves the chromosome number and includes recombination. Mistakes such as nondisjunction (failure of homologous chromosomes or sister chromatids to separate) produce gametes with an extra or missing chromosome. When such a gamete fuses with a normal one, the resulting zygote has an abnormal chromosome number, leading to genetic disorders that are passed to the next generation.

**Ground it in real life**  
Think of a factory assembly line. A mistake in the regular production line (mitosis) means many finished products on the market are faulty, causing widespread consumer complaints. A mistake in the design blueprint (meiosis) means the next batch of products is built with a wrong specification, and every item from that batch will have the same flaw, affecting future customers.

**Fresh analogy**  
Imagine copying a textbook.  
- **Mitosis** is like making multiple photocopies of the same book for a class. If the original page has a typo, every copy will contain that typo, and many students will read the wrong information.  
- **Meiosis** is like creating a new edition where you remove half the chapters and shuffle sections to make a summary. If you accidentally omit an entire chapter (nondisjunction), the summary will miss crucial content, and anyone using that summary will have an incomplete understanding, affecting future learning.  
Both errors are problematic, but mitotic errors spread within the same generation, while meiotic errors affect the next generation.

**Common mistake or misconception**  
Students often think errors in meiosis are less serious because only four cells are produced, but the resulting abnormal gametes can cause severe hereditary disorders.

**Try this →**  
If a somatic cell loses one chromosome during mitosis, what type of problem is most likely to appear in the individual? (Answer in one word.)
~~~~

**Grade:** ✅ correct — The tutor's answer correctly identifies that mitosis errors lead to issues in somatic cells (like cancer/tumours) and meiosis errors lead to genetic disorders in offspring (like Down syndrome), covering all key points from the reference answer and textbook passage. The additional details provided are factually accurate and do not contradict the source material.

</details>

<a id="q51"></a>
<details><summary>#51 · ✅ correct · Class 9 · Class 9 Social Science — Understanding Society: India and Beyond · What are some examples of landforms that are created by the action of waves and currents a…</summary>

**Question:** What are some examples of landforms that are created by the action of waves and currents along the coastline?

**Source:** Class 9 Social Science — *Understanding Society: India and Beyond*, chapter `iest102`, page 13

**Reference answer:** The action of waves and currents creates various landforms along the coastline, including beaches, sand bars, sea cliffs, sea caves, arches, and stacks.

**Key points:** Beaches; Sand bars; Sea cliffs; Sea caves, arches, and stacks

**Retrieval top 3:** `iest102`, `iest102`, `iest102` (gold chapter ranked first)

**Citations returned:** — · **Badge:** AI knowledge · **Answered by:** `Groq[openai/gpt-oss-120b]` · **Latency:** 12.35s

**Tutor's answer:**

~~~~text
**Direct answer**  
Beaches, sea cliffs, wave‑cut platforms, sea caves, arches, stacks, spits and barrier islands are typical landforms created by the action of waves and currents along a coastline.  

**Why it works (the mechanism)**  
Waves constantly strike the shore with kinetic energy. Where the rock is hard, the repeated impact erodes material, forming **sea cliffs**. As the cliff retreats, the base is worn away and a flat **wave‑cut platform** is left behind. Continued erosion can enlarge cracks into **sea caves**, and when caves on opposite sides meet, an **arch** forms; collapse of the arch leaves an isolated **stack**.  
Conversely, when waves lose energy (for example in a sheltered bay), they **deposit** the carried sand and gravel. This deposition builds up a **beach**. Long‑shore drift can move the deposited material along the coast, accumulating it into a **spit** that projects outwards, and if a sand‑filled barrier encloses a lagoon, a **barrier island** is created.  

**Ground it in real life**  
The golden sands of Puri Beach are a classic beach formed by wave deposition. The towering cliffs at Mahabalipuram are sea cliffs, and the sea‑cave‑like hollows you can see near the Konark shoreline are examples of sea caves.  

**Worked analogy**  
Think of waves as a moving conveyor belt in a factory. The belt picks up loose sand (the sediment) from one end (the open sea) and carries it toward the shore. When the belt slows down near the shore, it drops the sand, building up a **beach**. If the belt runs over a hard rock surface, it scrapes away bits of rock, carving out a **cliff** and, over time, a **platform** at its base—just as a grinder shapes a piece of wood.  

**Common mistake or misconception**  
Students often list **dunes** as wave‑formed landforms, but dunes are primarily created by wind, not by wave or current action.  

**Try this →**  
When you walk along a coastline and see a tall, isolated rock pillar standing near the water, what wave‑driven process likely formed it? (Answer: it is a **stack**, formed after a sea arch collapses.)
~~~~

**Grade:** ✅ correct — The tutor's answer includes all the required key points (beaches, sand bars implied via barrier islands/spits or explicitly missing but the core list is covered, sea cliffs, sea caves, arches, stacks). Although 'sand bars' are not explicitly listed as a standalone item in the first sentence, the answer covers the other specific landforms (cliffs, caves, arches, stacks, beaches) and adds correct additional examples like spits and barrier islands. The explanation is factually accurate and aligns with the textbook passage.

</details>

<a id="q52"></a>
<details><summary>#52 · ✅ correct · Class 8 · Class 8 Mathematics — Ganita Prakash Part 2 · How do I convert a fraction into a percentage? Can you show me with an example like 9/34?</summary>

**Question:** How do I convert a fraction into a percentage? Can you show me with an example like 9/34?

**Source:** Class 8 Mathematics — *Ganita Prakash* Part 2, chapter `hegp201`, page 0

**Reference answer:** To convert a fraction to a percentage, you multiply the fraction by 100. For example, to convert 9/34, you calculate (9/34) × 100, which equals approximately 26.47%.

**Key points:** Multiply the fraction by 100; The example 9/34 converts to 26.47%

**Retrieval top 3:** `hegp201`, `hegp201`, `hegp201` (gold chapter ranked first)

**Citations returned:** `hegp201` p.0 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-20b]` · **Latency:** 7.23s

**Tutor's answer:**

~~~~text
**Direct answer**  
To change a fraction into a percentage, multiply the fraction by 100.  
For example, \( \frac{9}{34} \times 100 = 26.47\% \).

**Why it works (the mechanism)**  
A percentage is a way of writing a fraction with the denominator 100.  
So to express any fraction as a percentage, we ask: “How many parts out of 100 are equal to this fraction?”  
Mathematically, we multiply the fraction by 100:  
\[
\frac{a}{b}\times 100 = \frac{a\times 100}{b}\% .
\]
This keeps the value the same but changes the denominator to 100, giving a percentage.

**Ground it in real life**  
Imagine a school selling sweets. If a stall offers 9 out of every 34 sweets as a free sample, you can tell the customers that the free‑sample rate is about 26.47 % of the total sweets sold. Saying “26.47 % of the sweets are free” is easier to understand than “9 out of 34”.

**Worked example**  
Calculate the percentage of \( \frac{9}{34} \):

1. Divide 9 by 34:  
   \( \frac{9}{34} \approx 0.2647 \).
2. Multiply by 100 to convert to a percentage:  
   \( 0.2647 \times 100 = 26.47 \).
3. Write the result with the percent sign:  
   \( 26.47\% \).

So, \( \frac{9}{34} = 26.47\% \).

**Common mistake or misconception**  
Students often think they should divide by 100 instead of multiplying, which would give a much smaller number and is incorrect.

**Try this →**  
Convert \( \frac{3}{5} \) into a percentage.
~~~~

**Grade:** ✅ correct — The tutor correctly states the method (multiplying by 100) and provides the accurate conversion for the specific example (9/34 = 26.47%). The explanation is clear, factually sound, and directly answers the student's question.

</details>

<a id="q53"></a>
<details><summary>#53 · ✅ correct · Class 10 · Class 10 Science · Why did Mendel cross the F1 tall plants with themselves, and what did he find out about th…</summary>

**Question:** Why did Mendel cross the F1 tall plants with themselves, and what did he find out about the F2 generation compared to the parental generation?

**Source:** Class 10 Science, chapter `jesc108`, page 3

**Reference answer:** Mendel self-pollinated the F1 tall plants to check if they were genetically identical to the original tall parents. While the offspring of the parental plants were all tall, the F2 progeny from the F1 plants showed a different pattern, indicating that the F1 plants were not exactly the same as the parental tall plants.

**Key points:** Mendel self-pollinated F1 plants to test if they were identical to parental tall plants; Progeny of parental plants were all tall; F2 progeny from F1 plants differed from the parental outcome

**Retrieval top 3:** `jesc108`, `jesc108`, `jesc108` (gold chapter ranked first)

**Citations returned:** `jesc108` p.3 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-120b]` · **Latency:** 7.36s

**Tutor's answer:**

~~~~text
**Direct answer**  
Mendel let the F₁ tall plants self‑pollinate (crossed them with themselves) to see whether the hidden short‑plant trait would re‑appear. In the F₂ generation he found that not all plants were tall; about one‑quarter were short, giving a 3 tall : 1 short ratio, unlike the pure‑tall parental generation.

**Why it works (the mechanism)**  
Each pea plant carries two copies of a gene for height – one from each parent.  
*Tall* (T) is **dominant**, so a plant with at least one T allele (TT or Tt) looks tall. *Short* (t) is **recessive**, showing only when both copies are t (tt).  
When the F₁ plants (all Tt) produce gametes, the two alleles separate (Mendel’s **law of segregation**) so each gamete gets either T or t. Random fusion of these gametes gives the F₂ genotypes in the ratio 1 TT : 2 Tt : 1 tt, which translates to the observed 3 tall : 1 short phenotype.

**Ground it in real life**  
Think of the common Indian garden pea you see in your backyard. If you start with a pure tall line and cross it with a pure short line, the first generation looks uniformly tall. But when those tall plants are allowed to self‑seed, you’ll soon notice a few short plants sprouting among the tall ones – exactly what Mendel recorded.

**Worked analogy**  
Imagine a pair of shoes: each shoe has a left and a right half. The colour of the pair is decided by the colour of the left shoe (dominant red) unless **both** shoes are white (recessive).  
- The parental generation: both shoes are red → all offspring look red.  
- F₁ generation: each pair gets one red shoe and one white shoe (heterozygous) → still looks red.  
- When these pairs are mixed again (self‑cross), the shoes are shuffled. One‑quarter of the new pairs end up with two white shoes, so they appear white, while the rest still look red. This mirrors the 3 red : 1 white (tall : short) outcome.

**Common mistake**  
Students often think that because the parents are tall, *all* their descendants must be tall; they forget that the recessive short allele can re‑appear when the alleles segregate.

**Try this →**  
If you start with a pure‑short (tt) plant and cross it with a pure‑tall (TT) plant, what phenotypic ratio would you expect in the F₂ generation after the F₁ plants self‑pollinate? (Answer in your head.)
~~~~

**Grade:** ✅ correct — The tutor correctly identifies that Mendel self-pollinated F1 plants to test if they were identical to the parents (implied by checking for the reappearance of the hidden trait). It explicitly states that the parental generation was pure tall (all tall) and that the F2 generation differed with a 3:1 ratio, covering all key points. The additional genetic explanation is factually accurate.

</details>

<a id="q54"></a>
<details><summary>#54 · ✅ correct · Class 10 · Class 10 Science · How do we name a carbon compound if it has a functional group that is added as a suffix st…</summary>

**Question:** How do we name a carbon compound if it has a functional group that is added as a suffix starting with a vowel?

**Source:** Class 10 Science, chapter `jesc104`, page 10

**Reference answer:** When the suffix for a functional group begins with a vowel (a, e, i, o, u), the final 'e' of the carbon chain name is deleted before adding the suffix. For example, a three-carbon chain (propane) would lose its 'e' to become 'propan' before the suffix is attached.

**Key points:** The final 'e' of the carbon chain name is deleted.; This rule applies when the functional group suffix starts with a vowel (a, e, i, o, u).; The appropriate suffix is then added to the modified chain name.

**Retrieval top 3:** `jesc104`, `jesc104`, `jesc104` (gold chapter ranked first)

**Citations returned:** `jesc104` p.10 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-120b]` · **Latency:** 5.65s

**Tutor's answer:**

~~~~text
**Direct answer**  
When the functional‑group name is used as a suffix and that suffix begins with a vowel (a, e, i, o, u), we first drop the final “e” of the parent hydrocarbon name and then attach the suffix.

**Why it works (the mechanism)**  
1. Identify the longest carbon chain and write its base name (e.g., *propane* for three carbons).  
2. Check the functional group’s suffix. If it starts with a vowel (e.g., **‑one** for a ketone, **‑al** for an aldehyde, **‑oic acid** for a carboxylic acid), the “e” at the end of the parent name would create a double vowel when simply added (e.g., *propane‑one*).  
3. To avoid this, the terminal “e” of the parent name is removed, giving *propan* + *‑one* = **propanone**. The same rule gives *ethanal* (ethane → ethan + al) and *butanoic acid* (butane → butan + oic acid).

**Ground it in real life**  
Think of writing a street address on a mailbox. If the house number ends with “e” and the street name starts with a vowel, you’d omit the extra “e” to keep the address tidy—just like we drop the “e” before adding a vowel‑starting suffix.

**Worked example**  
Name the compound CH₃‑CH₂‑C(=O)‑CH₃.  

1. Longest chain = 4 carbons → *butane*.  
2. Functional group = carbonyl (ketone) with suffix **‑one** (starts with a vowel).  
3. Remove the final “e” from *butane*: *butan*.  
4. Attach the suffix: *butan* + *‑one* = **butanone** (commonly called methyl ethyl ketone).  

**Common mistake or misconception**  
Students often keep the “e” and write *butane‑one*, which is incorrect because the extra “e” creates an awkward double vowel.

**Try this →**  
What is the IUPAC name of CH₃‑CH₂‑CH₂‑CHO? (Hint: the aldehyde suffix starts with a vowel.)
~~~~

**Grade:** ✅ correct — The tutor correctly identifies that the final 'e' is deleted when the suffix starts with a vowel, explicitly lists the vowels, and explains that the suffix is then added to the modified name. All key points from the reference answer are present and accurate.

</details>

<a id="q55"></a>
<details><summary>#55 · ✅ correct · Class 9 · Class 9 Science · What are the main parts of a flower that we can see in a longitudinal section?</summary>

**Question:** What are the main parts of a flower that we can see in a longitudinal section?

**Source:** Class 9 Science, chapter `iesc111`, page 7

**Reference answer:** The main parts of a flower include the sepals, petals, stamen, and pistil. The stamen consists of the anther and filament, while the pistil is made up of the stigma, style, and ovary.

**Key points:** Sepals and petals are outer parts; Stamen consists of anther and filament; Pistil consists of stigma, style, and ovary

**Retrieval top 3:** `iesc111`, `iesc111`, `iesc111` (gold chapter ranked first)

**Citations returned:** `iesc111` p.8, `iesc111` p.7 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-120b]` · **Latency:** 9.25s

**Tutor's answer:**

~~~~text
**Direct answer**  
In a longitudinal section of a complete flower you can see four whorls: the sepals, the petals, the stamens (filament + anther) and the pistil (stigma + style + ovary).

**Why it works (the mechanism)**  
- **Sepals** form the outermost whorl; they are usually green and protect the bud before it opens.  
- **Petals** lie just inside the sepals; their bright colour and fragrance attract pollinators.  
- **Stamens** are the male reproductive organs. Each stamen has a slender **filament** that holds up the **anther**, where pollen grains (male gametes) are produced.  
- **Pistil** is the female organ, composed of three parts: the sticky **stigma** (receives pollen), the elongated **style** (connects stigma to ovary), and the **ovary** (contains ovules that develop into seeds after fertilisation).  

A longitudinal cut runs from the top of the flower down through its centre, so it slices through each whorl and reveals all these structures in a single view.

**Ground it in real life**  
When you look at a sliced rose bud, the outer green leaf‑like pieces you see are the sepals, the pink layers are the petals, the tiny stalks with yellow caps are the stamens, and the central column ending in a swollen base is the pistil.

**Analogy**  
Think of a flower as a stage set: the **sepals** are the backstage curtains that protect the actors, the **petals** are the colourful set pieces that draw the audience’s eye, the **stamens** are the male actors delivering their lines (pollen), and the **pistil** is the lead actress with three costume pieces (stigma, style, ovary) that receive the lines and produce the next act (seeds). Just as a stage view from the side shows all these elements in order, a longitudinal section shows all flower parts in sequence.

**Common mistake or misconception**  
Students often ignore sepals and petals, thinking only stamens and pistil matter for reproduction; however, all four whorls are visible and play roles in protecting and attracting pollinators.

**Try this →**  
If you cut a lily flower lengthwise, can you name the part you see at the very tip of the central column?  

**Parts to identify:**  
- **Sepal** — protects the bud, outermost whorl  
- **Petal** — attracts pollinators, coloured inner whorl  
- **Stamen** — male organ (filament + anther) producing pollen  
- **Pistil** — female organ (stigma + style + ovary) receiving pollen and housing ovules

![Related visual for parts of a flower](https://s3.ap-southeast-2.amazonaws.com/super-tutor/production/educational-images/biology/flower_angiosperm_structure_labeled_diagram.png)
~~~~

**Grade:** ✅ correct — The tutor's answer correctly identifies all four main parts of the flower (sepals, petals, stamen, pistil) and accurately lists the sub-components of the stamen (anther, filament) and pistil (stigma, style, ovary), covering all required key points without factual errors.

</details>

<a id="q56"></a>
<details><summary>#56 · ✅ correct · Class 10 · Class 10 Science · Who suggested that a magnet exerts an equal and opposite force on a current-carrying condu…</summary>

**Question:** Who suggested that a magnet exerts an equal and opposite force on a current-carrying conductor, and what is the name of this scientist?

**Source:** Class 10 Science, chapter `jesc112`, page 8

**Reference answer:** The French scientist Andre Marie Ampere suggested that a magnet exerts an equal and opposite force on a current-carrying conductor. He lived from 1775 to 1836.

**Key points:** Andre Marie Ampere; French scientist; Equal and opposite force

**Retrieval top 3:** `jesc112`, `jesc112`, `jesc112` (gold chapter ranked first)

**Citations returned:** `jesc112` p.8, `jesc112` p.12, `jesc112` p.9 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-120b]` · **Latency:** 6.39s

**Tutor's answer:**

~~~~text
**Direct answer**  
The idea was proposed by the French scientist **André Marie Ampère**.

**Why it works (the mechanism)**  
Ampère reasoned that a magnetic field exerts a force on a current‑carrying conductor (the familiar “motor effect”). By Newton’s third law—every action has an equal and opposite reaction—the conductor must in turn exert an equal‑magnitude, opposite‑direction force on the magnet that creates the field. Thus the magnet and the conductor push on each other with the same strength.

**Ground it in real life**  
When a current‑carrying wire is placed near a bar magnet, the wire moves (or tries to move) because of the magnetic field. At the same time, you can feel the magnet being tugged toward the wire if you hold it loosely. This mutual push‑pull is exactly what Ampère described.

**Fresh analogy**  
Imagine two children on a seesaw. When one child pushes down on their side, the other child is lifted up with an equal and opposite reaction. Similarly, the magnetic field pushes on the current‑carrying wire, and the wire pushes back on the magnet with the same force.

**Common mistake or misconception**  
Students often think only the conductor experiences a force, forgetting that the magnet experiences an equal and opposite force as well.

**Try this →**  
If a straight wire carrying a current is placed perpendicular to a uniform magnetic field, which way does the magnet feel the force? (Think about the direction of the force on the wire and then apply the action‑reaction principle.)
~~~~

**Grade:** ✅ correct — The tutor correctly identifies Andre Marie Ampere as the French scientist who suggested the magnet exerts an equal and opposite force on the current-carrying conductor, covering all key points without factual errors.

</details>

<a id="q57"></a>
<details><summary>#57 · ✅ correct · Class 10 · Class 10 Geography — Contemporary India II · Why do some regions in India that are rich in natural resources remain economically backwa…</summary>

**Question:** Why do some regions in India that are rich in natural resources remain economically backward, while others with fewer resources are developed?

**Source:** Class 10 Geography — *Contemporary India II*, chapter `jess101`, page 3

**Reference answer:** Resources alone are not enough for development; they must be accompanied by appropriate technological development and institutional changes. Without these factors, even resource-rich regions can remain economically backward, while regions with a poor resource base can still develop if they have the right technology and institutions.

**Key points:** Resources contribute to development only when accompanied by appropriate technological development.; Institutional changes are also necessary for resources to aid development.; Lack of technology and institutions can hinder development despite resource richness.

**Retrieval top 3:** `jess101`, `jess101`, `jess101` (gold chapter ranked first)

**Citations returned:** `jess101` p.3, `jess101` p.2 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-20b]` · **Latency:** 8.62s

**Tutor's answer:**

~~~~text
**Direct answer**  
A region that has many natural resources can stay economically backward if it lacks the technology to extract and use those resources, the infrastructure to transport them, and good institutions that create a favourable business climate.  
Conversely, a resource‑poor region can develop when it invests in technology, education, and institutions that turn other inputs (labour, capital, ideas) into productive goods and services.

**Why it works (the mechanism)**  
1. **Resources are raw material** – minerals, water, forests, etc.  
2. **Technology** (machines, processes, skills) turns raw material into finished products or services.  
3. **Institutions** (laws, governance, finance, markets) provide the rules and incentives for people to invest, innovate, and trade.  
4. Without steps 2 and 3, the raw material remains untapped or is extracted by outsiders, so the local economy does not grow.  
5. When technology and institutions are in place, the region can add value locally, create jobs, and generate income, leading to overall development.

**Ground it in real life**  
- *Jharkhand* has huge coal and iron ore deposits, but poor roads and limited power supply mean mines cannot operate efficiently, so the state remains one of the poorest.  
- *Arunachal Pradesh* has abundant water, yet its remote location and lack of highways keep it isolated from markets.  
- *Rajasthan* is rich in solar and wind potential, but water scarcity limits large‑scale power projects.  
- *Ladakh* has a vibrant culture but lacks water, roads, and minerals, so tourism is the main source of income.  
- In contrast, *Tamil Nadu* uses its technology (electronics, textiles) and strong institutions (stable laws, good ports) to become one of the most industrialised states, even though it is not the richest in natural resources.

**Worked example / fresh analogy**  
Think of a fruit tree (the natural resource).  
- If the tree is planted but no gardener (technology) waters or prunes it, the fruit never ripens.  
- Even if the fruit ripens, without a market (institutions) to sell it, the fruit rots on the tree.  
- A small seed (resource‑poor area) can grow into a healthy tree if a gardener and a market are already there.  
Thus, the presence of resources is like the fruit; technology and institutions are the gardener and market that make the fruit useful and profitable.

**Common mistake or misconception**  
Students often think “more resources = more wealth.” In reality, resources alone do not guarantee development; technology and institutions are essential to convert those resources into economic progress.

**Try this →**  
If a region has abundant coal but no roads, what two missing elements are most likely preventing it from becoming economically developed?
~~~~

**Grade:** ✅ correct — The answer correctly identifies that resources require appropriate technological development and institutional changes to contribute to economic development, aligning perfectly with the textbook passage and reference answer. It covers all key points without factual errors.

</details>

<a id="q58"></a>
<details><summary>#58 · ✅ correct · Class 8 · Class 8 Science — Curiosity · How thin is the Earth's crust compared to the whole planet, and why is this layer importan…</summary>

**Question:** How thin is the Earth's crust compared to the whole planet, and why is this layer important?

**Source:** Class 8 Science — *Curiosity*, chapter `hecu113`, page 2

**Reference answer:** The Earth's crust is extremely thin compared to the planet's total size, similar to the skin of an apple if Earth were that size. This layer is crucial because it is the only place where all life on Earth, including mountains, rivers, forests, animals, and people, exists.

**Key points:** Crust is very thin compared to Earth's size (like apple skin); All life exists on the crust; Crust is the life-supporting layer

**Retrieval top 3:** `hecu109`, `hecu113`, `hecu113` (gold chapter not ranked first)

**Citations returned:** — · **Badge:** AI knowledge · **Answered by:** `Groq[openai/gpt-oss-120b]` · **Latency:** 9.25s

**Tutor's answer:**

~~~~text
**Direct answer**  
The Earth's crust is only a few kilometres thick under the oceans (≈ 5 km) and up to about 30–70 km under the continents, which is less than 1 % of the planet’s total radius. Despite being so thin, the crust is vital because it holds the soil, minerals, water and all the ecosystems on which life depends.

**Why it works (the mechanism)**  
The crust is the outermost solid layer of the Earth. It sits above the much thicker mantle and the core, so its thickness is tiny compared with the planet’s radius (≈ 6 370 km). Its composition—silica, aluminium, oxygen, and other light elements—makes it less dense than the layers below, allowing it to “float” on the mantle. All terrestrial life lives on this layer because it provides a stable surface, supplies nutrients from rocks, and stores water in soils and aquifers.

**Ground it in real life**  
Think of the crust as the thin, edible shell of a boiled egg. Just as the shell protects the yolk and white while being only a fraction of the egg’s size, the crust protects the hotter interior and gives us the land on which we grow rice, build houses, and play cricket.

**Fresh analogy**  
Imagine the Earth as a multi‑layered cake:  
- The bottom dense layer is the **core** (the cake’s heavy base).  
- Above it is the **mantle** (the thick, soft sponge).  
- The **crust** is the thin frosting on top.  
The frosting is only a few millimetres thick compared with the whole cake, yet it’s the part we see, taste, and interact with. Similarly, the crust is the thin outer “frosting” that we live on and use for all our activities.

**Common mistake or misconception**  
Students often think the crust is a thick, solid shell like the mantle, but in reality it is a very thin, fragile layer—less than 1 % of Earth’s size.

**Try this →**  
If Earth’s radius is about 6 370 km, what percentage of that radius does an average continental crust thickness of 35 km represent? (Answer in %.)
~~~~

**Grade:** ✅ correct — The answer correctly identifies the crust as extremely thin (using the apple skin analogy implied by the 'less than 1%' and 'thin shell' descriptions) and explicitly states that all life/ecosystems exist on this layer. The additional factual details about thickness and composition are accurate and do not contradict the textbook.

</details>

<a id="q59"></a>
<details><summary>#59 · 🟡 partial · Class 9 · Class 9 Social Science — Understanding Society: India and Beyond · According to the text, why is democracy considered more than just holding elections?</summary>

**Question:** According to the text, why is democracy considered more than just holding elections?

**Source:** Class 9 Social Science — *Understanding Society: India and Beyond*, chapter `iest106`, page 18

**Reference answer:** Democracy is not limited to elections because it requires constant care, awareness, and active participation from both institutions and citizens. It is reflected in everyday life, where individuals are expected to exercise their constitutional rights responsibly.

**Key points:** Democracy requires constant care, awareness, and active participation.; It involves both institutions and citizens.; It is reflected in everyday life, not just elections.; Citizens must exercise rights responsibly.

**Retrieval top 3:** `iest106`, `iest106`, `iest106` (gold chapter ranked first)

**Citations returned:** `iest106` p.14, `iest106` p.21 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-120b]` · **Latency:** 6.48s

**Tutor's answer:**

~~~~text
**Direct answer**  
Democracy is more than just holding elections because it also involves the institutions, rules and everyday practices that let people take part in decision‑making at local, state and national levels. It requires continuous citizen participation in shaping policies, public consultations, and community initiatives, guided by principles such as transparency, inclusion and accountability.

**Why it works (the mechanism)**  
- **Institutions and rules** provide the framework (parliaments, local councils, courts) within which decisions are made.  
- **Participatory mechanisms** (e.g., public hearings, community‑driven projects) let citizens influence policy *between* elections.  
- **Democratic principles**—transparency (open processes), participation (active involvement), inclusion (all voices heard), fairness, equality, accountability, respect for diverse opinions—ensure that power is not concentrated in a few and that decisions reflect the collective will. Together these elements keep democracy alive beyond the periodic ballot.

**Ground it in real life**  
Think of a school’s student council. The election of the president is just one part; the council also holds regular meetings where students discuss cafeteria menus, organize cultural events, and raise safety concerns. Those ongoing discussions and actions embody democratic practice just as much as the vote that chose the president.

**Fresh analogy**  
Imagine democracy as a **river**. The election is like the river’s source—important, but brief. The continuous flow of water represents everyday citizen participation: streams joining from villages (local councils), tributaries of public consultations, and irrigation channels of community projects. The river stays alive only while all these streams keep feeding it; without them, it would dry up even if the source remains.

**Common mistake or misconception**  
Students often think democracy ends once the election results are declared, overlooking the constant civic engagement that sustains it.

**Try this →**  
If a city holds a public hearing on a new park, is that an example of democracy beyond elections? (Answer in one word.)
~~~~

**Grade:** 🟡 partial — The answer correctly identifies that democracy involves institutions, citizens, and everyday participation, covering three of the four key points. However, it misses the specific point that citizens must exercise their rights 'responsibly,' which is explicitly mentioned in the textbook passage and reference answer.

</details>

<a id="q60"></a>
<details><summary>#60 · ✅ correct · Class 8 · Class 8 Mathematics — Ganita Prakash Part 1 · What is the relationship between the sum of the first n odd numbers and perfect squares?</summary>

**Question:** What is the relationship between the sum of the first n odd numbers and perfect squares?

**Source:** Class 8 Mathematics — *Ganita Prakash* Part 1, chapter `hegp101`, page 0

**Reference answer:** The sum of the first n odd numbers is equal to the square of n. This means that if you add up the first n odd numbers, the result will always be a perfect square, specifically n squared.

**Key points:** The sum of the first n odd numbers equals n squared; This sum results in a perfect square

**Retrieval top 3:** `hegp101`, `hegp101`, `hegp101` (gold chapter ranked first)

**Citations returned:** `hegp101` p.0 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-120b]` · **Latency:** 10.61s

**Tutor's answer:**

~~~~text
**Direct answer**  
The sum of the first \(n\) odd numbers equals \(n^2\). In other words, adding the odd numbers \(1,3,5,\dots,(2n-1)\) always gives a perfect square.

**Why it works (the mechanism)**  
Each odd number can be written as \(2k-1\) where \(k\) runs from 1 to \(n\).  
\[
\sum_{k=1}^{n}(2k-1)=\bigl(2\sum_{k=1}^{n}k\bigr)-\sum_{k=1}^{n}1
=2\frac{n(n+1)}{2}-n=n^2 .
\]  
Thus the total grows exactly as the square of the count of terms. This also follows by induction: assume the sum of the first \(n\) odd numbers is \(n^2\); adding the next odd number \((2n+1)\) gives \(n^2+2n+1=(n+1)^2\).

**Ground it in real life**  
Imagine arranging sweets (e.g., laddus) on a square tray. Start with one laddu, then add a layer of 3 around it to make a \(2\times2\) square, then 5 more to get a \(3\times3\) square, and so on. Each new layer adds the next odd number, and the total number of sweets always forms a perfect square.

**Worked example**  
Find the sum of the first 5 odd numbers.

\[
\begin{aligned}
1+3+5+7+9 &= (1+3)+(5+7)+9 \\
          &= 4+12+9 \\
          &= 25 \\
          &= 5^2 .
\end{aligned}
\]

So the sum is \(25\), a perfect square \(5^2\).

**Common mistake**  
Students often think the sum of the first \(n\) odd numbers is \(n(n+1)\); the correct result is \(n^2\) because each new odd term adds exactly the amount needed to increase the square from \(n^2\) to \((n+1)^2\).

**Try this →**  
What is the sum of the first 7 odd numbers? (Hint: it should equal \(7^2\).)
~~~~

**Grade:** ✅ correct — The tutor correctly states that the sum of the first n odd numbers is n squared and explicitly identifies this result as a perfect square. The explanation includes valid mathematical proofs and examples without any factual errors.

</details>

<a id="q61"></a>
<details><summary>#61 · ✅ correct · Class 9 · Class 9 Social Science — Understanding Society: India and Beyond · What is the main role of political parties in helping voters during elections?</summary>

**Question:** What is the main role of political parties in helping voters during elections?

**Source:** Class 9 Social Science — *Understanding Society: India and Beyond*, chapter `iest107`, page 19

**Reference answer:** Political parties play a fundamental role by presenting diverse viewpoints through their programmes and policies. This helps voters make informed choices when casting their votes.

**Key points:** Presenting diverse viewpoints; Using programmes and policies; Helping voters make informed choices

**Retrieval top 3:** `iest107`, `iest107`, `iest107` (gold chapter ranked first)

**Citations returned:** `iest107` p.19, `iest107` p.12, `iest107` p.15 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-120b]` · **Latency:** 7.07s

**Tutor's answer:**

~~~~text
**Direct answer**  
Political parties help voters by presenting clear programmes, policies and viewpoints, so that voters can compare options and make an informed choice during elections.

**Why it works (the mechanism)**  
A party formulates a *manifesto* (a written list of promises and policy plans).  
- The manifesto summarises the party’s stance on social, economic, cultural and political issues.  
- Voters read or hear these summaries through rallies, media and debates, which lets them match the party’s ideas with their own priorities.  
- By organising public opinion around these ideas, parties create a common platform that simplifies the many individual candidates into understandable choices.

**Ground it in real life**  
Think of a school election where each class forms a committee and prepares a list of promises—better playgrounds, more books, etc. When you, as a student, read those lists, you can decide which committee’s plan fits your wishes best. Political parties work the same way on a national or state level.

**Worked analogy**  
Imagine a menu at a restaurant.  
- Each *dish* on the menu represents a policy (e.g., “free education” is a main course).  
- The *menu* itself is the party’s manifesto, showing all the dishes they will serve if elected.  
- As a diner (voter), you look at the menu, compare the dishes, and choose the restaurant (party) that offers the meals you like.  
Thus, the party’s role is like providing a clear menu that guides your selection.

**Common mistake or misconception**  
Students often think parties only help by fielding candidates, but their crucial function is to *communicate* policies and organise public opinion for voters.

**Try this →**  
If two parties promise different actions on school fees, which party’s manifesto would help you decide which one to vote for?
~~~~

**Grade:** ✅ correct — The tutor's answer explicitly covers all three key points: presenting diverse viewpoints, using programmes/policies (manifestos), and helping voters make informed choices. The additional explanations and analogies are factually correct and do not contradict the textbook.

</details>

<a id="q62"></a>
<details><summary>#62 · ✅ correct · Class 8 · Class 8 Mathematics — Ganita Prakash Part 2 · If I have to use the digits 2, 3, and 5 exactly once to form a multiplication problem (lik…</summary>

**Question:** If I have to use the digits 2, 3, and 5 exactly once to form a multiplication problem (like a two-digit number times a one-digit number), which combination gives the largest product?

**Source:** Class 8 Mathematics — *Ganita Prakash* Part 2, chapter `hegp206`, page 0

**Reference answer:** The largest product is obtained by multiplying 53 by 2. This results in 106, which is higher than the products from other combinations like 52 x 3 (156 is incorrect, 52*3=156? Wait, 52*3 = 156. 53*2 = 106. 32*5 = 160. Let me re-read the passage logic. The passage lists 53x2, 52x3, 32x5 as the ones to compare. It expands 32x5 and 52x3. It does not explicitly state the final answer in the text provided, but the structure implies finding the max. Let's look at the math: 53*2=106, 52*3=156, 32*5=160. The passage groups them but doesn't explicitly say which is largest in the text snippet. However, the question asks for the largest product possible. The passage provides the specific products to compare: 53x2, 52x3, and 32x5. A student would calculate these. 32x5 is 160. 52x3 is 156. 53x2 is 106. So 32x5 is the largest. The passage supports the *method* of comparing these specific top candidates. I will frame the answer around identifying the correct combination from the listed candidates.

**Key points:** The possible high-value combinations to compare are 53 x 2, 52 x 3, and 32 x 5.; Calculating these shows that 32 x 5 yields the highest value (160).; The strategy involves placing the largest digit in the tens place of the two-digit number to maximize the product.

**Retrieval top 3:** `hegp206`, `hegp206`, `hegp102` (gold chapter ranked first)

**Citations returned:** `hegp206` p.0, `hegp102` p.0 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-20b]` · **Latency:** 8.35s

**Tutor's answer:**

~~~~text
**Direct answer**  
The largest product is obtained with the multiplication \(32 \times 5\), which equals \(160\).

**Why it works**  
A two‑digit number is \(10\times(\text{tens digit}) + (\text{units digit})\).  
The product is \((10t + u)\times m\).  
To make this as large as possible we want the tens digit \(t\) and the multiplier \(m\) to be the largest digits available.  
Using the digits 2, 3, 5, the best choice is \(t=3\) and \(m=5\), giving \(32 \times 5\).  
The remaining digit 2 becomes the units digit.  
Other arrangements give smaller products:  
\(53 \times 2 = 106\),  
\(52 \times 3 = 156\),  
\(35 \times 2 = 70\),  
\(25 \times 3 = 75\),  
\(23 \times 5 = 115\).

**Ground it in real life**  
Imagine you are buying 32 mangoes and each mango costs ₹5. The total cost is ₹160, which is more than buying 53 mangoes at ₹2 each (₹106) or 52 mangoes at ₹3 each (₹156). The larger total comes from having a higher price per mango (the multiplier) and a larger number of mangoes (the tens digit).

**Worked example**  
Compute \(32 \times 5\):  
\(32 \times 5 = (3 \times 10 \times 5) + (2 \times 5) = 150 + 10 = 160\).  
So the product is \(160\).

**Common mistake**  
Students often think the two‑digit number should be the largest (e.g., 53) and ignore that the multiplier also matters. The product depends on both parts, not just the size of the two‑digit number.

**Try this →**  
What is the product of \(52 \times 3\)?
~~~~

**Grade:** ✅ correct — The tutor correctly identifies 32 x 5 as the combination yielding the largest product (160), explicitly compares the key candidates (53x2, 52x3, 32x5) as required, and explains the strategy of maximizing the tens digit and multiplier. All calculations are factually accurate.

</details>

<a id="q63"></a>
<details><summary>#63 · ✅ correct · Class 10 · Class 10 Science · How does a convex lens form an image?</summary>

**Question:** How does a convex lens form an image?

**Source:** Class 10 Science, chapter `jesc109`, page 19

**Reference answer:** A convex lens forms an image by refracting light. When light rays pass through the lens, they bend or change direction, which allows them to converge and create an image on a screen.

**Key points:** Convex lenses form images; By refracting light

**Retrieval top 3:** `jesc109`, `jesc109`, `jesc109` (gold chapter ranked first)

**Citations returned:** `jesc109` p.19, `jesc109` p.22, `jesc109` p.27 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-120b]` · **Latency:** 8.91s

**Tutor's answer:**

~~~~text
**Direct answer**  
A convex (converging) lens bends incoming light rays so that they meet at a point on the other side of the lens. The point where the rays meet is the image; its position, size and nature (real‑inverted or virtual‑upright) depend on how far the object is from the lens.

**Why it works (the mechanism)**  
- **Refraction** – When light passes from air into the glass of the lens, its speed changes, causing the ray to bend toward the normal. Because the lens is thicker in the middle, parallel rays are bent more strongly there and converge.  
- **Focal point (F)** – A ray that enters the lens parallel to the principal axis emerges through the focal point on the opposite side.  
- **Principal ray through the centre** – A ray that passes through the optical centre of the lens continues in a straight line (no deviation).  
- **Ray through the focal point** – A ray that heads toward the focal point on the object side emerges from the lens parallel to the principal axis.  
- The three rays intersect at a single point; that intersection is the image.  
- The **lens formula** $ \frac{1}{f} = \frac{1}{v} + \frac{1}{u} $ (with sign convention: $f>0$ for convex, $u$ negative, $v$ positive for real images) relates object distance $u$, image distance $v$ and focal length $f$.

**Ground it in real life**  
A camera lens works on the same principle: the scene (object) is far away, light rays enter the convex lens, converge to form a real image on the sensor. Similarly, a magnifying glass is a convex lens used with the object inside the focal length, producing a virtual, upright, enlarged image that you can see directly.

**Worked example**  
An object 5 cm tall is placed 25 cm from a convex lens of focal length 10 cm. Find the image distance, height and nature.

1. Use the lens formula (take $u = -25$ cm, $f = +10$ cm):  
   \[
   \frac{1}{10} = \frac{1}{v} + \frac{1}{-25}
   \]  
   \[
   \frac{1}{v} = \frac{1}{10} + \frac{1}{25} = \frac{5}{50} + \frac{2}{50} = \frac{7}{50}
   \]  
   \[
   v = \frac{50}{7} \approx 7.14\text{ cm}
   \]  
2. Magnification $m = \frac{v}{u} = \frac{7.14}{-25} = -0.286$.  
3. Image height $h' = m \times h = -0.286 \times 5 \approx -1.43\text{ cm}$ (negative sign → inverted).  

**Result:** The image forms 7.1 cm on the other side of the lens, is inverted, and about one‑third the size of the object.

**Common mistake**  
Students often forget the sign convention and treat all distances as positive, which leads to wrong image positions and wrong conclusions about whether the image is real or virtual.

**Try this →**  
If an object is placed 5 cm from a convex lens of focal length 10 cm, where will the image form and what will be its nature? (Answer: virtual, upright, on the same side as the object).
~~~~

**Grade:** ✅ correct — The answer correctly identifies that convex lenses form images by refracting light, covering both key points. The additional details regarding ray diagrams, the lens formula, and worked examples are factually accurate and appropriate for the context, even if they go beyond the specific textbook passage provided.

</details>

<a id="q64"></a>
<details><summary>#64 · ✅ correct · Class 8 · Class 8 Social Science — Exploring Society: India and Beyond · How did Maharaja Ranjit Singh manage to unify the Sikh confederacies in Punjab, and what s…</summary>

**Question:** How did Maharaja Ranjit Singh manage to unify the Sikh confederacies in Punjab, and what specific qualities helped him build a strong centralized state?

**Source:** Class 8 Social Science — *Exploring Society: India and Beyond*, chapter `hees102`, page 32

**Reference answer:** Maharaja Ranjit Singh unified the Sikh confederacies at the turn of the 19th century. He achieved this by utilizing his military acumen, diplomatic skills, and religious tolerance, which allowed him to establish a strong, centralised state.

**Key points:** Unified Sikh confederacies at the turn of the 19th century; Used military acumen and diplomatic skill; Practiced religious tolerance; Established a strong, centralised state

**Retrieval top 3:** `hees102`, `hees102`, `hees103` (gold chapter ranked first)

**Citations returned:** — · **Badge:** AI knowledge · **Answered by:** `Groq[openai/gpt-oss-20b]` · **Latency:** 9.92s

**Tutor's answer:**

~~~~text
**Direct answer**  
Maharaja Ranjit Singh brought the scattered Sikh Misls together by first defeating them in a series of decisive battles, then absorbing them through alliances and diplomatic agreements. After unification he set up a standing army, a modern bureaucracy, and a secular legal system, which turned the loose confederacy into a strong, centralized state.  

**Why it works (the mechanism)**  
1. **Military strength** – Ranjit Singh built a professional army with modern artillery and disciplined infantry, which won key battles against rival Misls.  
2. **Strategic alliances** – He married into influential families and offered positions to former rivals, turning enemies into allies.  
3. **Merit‑based administration** – He appointed capable officers from all communities, creating a loyal civil service that could manage revenue, law, and order.  
4. **Secular governance** – By patronising temples, mosques, and gurdwaras alike, he earned the trust of diverse groups, ensuring social stability.  
5. **Centralised institutions** – He established a capital at Lahore, built forts, and set up a court that applied uniform laws, replacing the fragmented local rule of the Misls.  

**Ground it in real life**  
Think of a neighbourhood where many small shops sell the same goods. If one shop owner hires a good manager, offers fair prices, and invites the other shopkeepers to share profits, the whole area can become a single, well‑run market. Ranjit Singh was that manager for the Sikh Misls, turning many independent stalls into one thriving bazaar.  

**Worked example / fresh analogy**  
Imagine you have 10 different puzzle pieces (the Misls).  
- First, you use a strong glue (military victories) to attach the pieces together.  
- Then you smooth the edges (alliances and marriages) so they fit perfectly.  
- Finally, you paint the whole puzzle a single colour (centralised laws and secular policy) so it looks like one picture.  
The result is a complete, unified image—just as Ranjit Singh’s Punjab became a single, coherent state.  

**Common mistake or misconception**  
Students often think the unification happened only by force; in reality, diplomacy and administrative reforms were equally crucial.  

**Try this →**  
Name one administrative reform that helped Ranjit Singh keep the newly united Sikh Empire stable.
~~~~

**Grade:** ✅ correct — The answer correctly identifies the unification of Sikh confederacies (Misls) at the turn of the 19th century and explicitly mentions military acumen, diplomatic skills (alliances), and religious tolerance (secular governance) as the key qualities that led to a strong, centralized state. All key points from the reference are present, and the additional details provided are historically accurate and do not contradict the textbook.

</details>

<a id="q65"></a>
<details><summary>#65 · ❌ incorrect · Class 8 · Class 8 Science — Curiosity · Why does a puri puff up when it is cooked in hot oil?</summary>

**Question:** Why does a puri puff up when it is cooked in hot oil?

**Source:** Class 8 Science — *Curiosity*, chapter `hecu101`, page 6

**Reference answer:** The passage does not provide the specific scientific reason for the puffing, but it identifies this as a phenomenon that can be investigated by asking scientific questions. It suggests that by observing this everyday event and asking 'why' it happens, students can think like scientists.

**Key points:** The passage frames the puffing as a question to be investigated.; It encourages asking 'why' about everyday phenomena.; It does not explicitly state the physical cause (like steam expansion) in this specific excerpt.

**Retrieval top 3:** `hecu101`, `hecu101`, `hecu101` (gold chapter ranked first)

**Citations returned:** `hecu101` p.6, `hecu101` p.7 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-120b]` · **Latency:** 8.92s

**Tutor's answer:**

~~~~text
**Direct answer**  
The textbook tells us that the exact reason a puri puffs up in hot oil is still not completely understood by scientists. It is a phenomenon that is still being investigated.

**Why it works (the mechanism)**  
Scientists treat the puffing as a *systematic investigation*: they first identify what can be *controlled* (variables such as dough thickness, size, type of flour, oil temperature, way of dropping the dough) and then decide what they will *observe* (whether the puri puffs, how much oil splatters, any smell or smoke). By changing one controlled factor at a time and recording the observations, they try to discover the cause of the puffing.

**Ground it in real life**  
When you fry puris at home, you may notice that a thin‑rolled dough puffed more than a thick one, or that a hotter oil makes the puff appear faster. These everyday clues are exactly the kind of observations the textbook suggests you note down.

**Worked example or fresh analogy**  
*Analogy*: Think of the puri as a balloon that you are trying to inflate. The dough is the balloon skin, and the hot oil is the air you blow into it. By changing the *size of the opening* (how thick you roll the dough) or the *pressure of the air* (how hot the oil is), you can see different amounts of inflation. Just as you would test different balloon sizes and pressures to see which inflates best, scientists test different dough thicknesses and oil temperatures to see which makes the puri puff best.

**Common mistake or misconception**  
A common trap is to assume that a single factor, like oil temperature alone, is responsible for puffing without testing other variables.

**Try this →**  
If you could change only one thing about the dough—its thickness, flour type, or adding a tiny hole—what do you think would happen to the puffing? Think about which change might give the biggest difference.
~~~~

**Grade:** ❌ incorrect — The tutor's answer contains a significant factual error by claiming the textbook states the phenomenon is 'still not completely understood by scientists' and 'still being investigated.' The provided passage frames the puffing as a simple everyday phenomenon for students to investigate to 'think like a scientist,' not as an unsolved scientific mystery. Furthermore, the answer fails to address the key points that the passage frames this as a question for investigation and encourages asking 'why' about everyday phenomena, instead inventing a narrative about systematic scientific research that is not in the text.  
**Factual errors:** The textbook tells us that the exact reason a puri puffs up in hot oil is still not completely understood by scientists.; It is a phenomenon that is still being investigated.

</details>

<a id="q66"></a>
<details><summary>#66 · ✅ correct · Class 10 · Class 10 Science · What are ionic compounds and how are they formed?</summary>

**Question:** What are ionic compounds and how are they formed?

**Source:** Class 10 Science, chapter `jesc103`, page 12

**Reference answer:** Ionic compounds, also known as electrovalent compounds, are formed when electrons are transferred from a metal to a non-metal. This process results in the formation of cations and anions that combine to create the compound.

**Key points:** Formed by transfer of electrons from metal to non-metal; Also called electrovalent compounds; Involves formation of cations and anions

**Retrieval top 3:** `jesc103`, `jesc103`, `jesc103` (gold chapter ranked first)

**Citations returned:** `jesc103` p.12, `jesc102` p.17, `jesc103` p.13 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-120b]` · **Latency:** 10.55s

**Tutor's answer:**

~~~~text
**Direct answer**  
Ionic compounds are substances formed when a metal transfers one or more electrons to a non‑metal, producing positively charged metal ions (cations) and negatively charged non‑metal ions (anions) that attract each other electrostatically.

**Why it works (the mechanism)**  
1. A metal atom has loosely held outer‑most electrons. It *loses* these electrons, becoming a positively charged ion (e.g., Mg → Mg²⁺ + 2e⁻).  
2. A non‑metal atom has a strong tendency to gain electrons to complete its outer shell. It *accepts* the electrons, becoming a negatively charged ion (e.g., Cl + e⁻ → Cl⁻).  
3. The opposite charges on the newly formed ions create an electrostatic force of attraction, pulling the ions together to form a crystal lattice – the ionic compound (e.g., Mg²⁺ + 2Cl⁻ → MgCl₂).

**Ground it in real life**  
Common table salt (sodium chloride) is an ionic compound. Sodium (a metal) gives up one electron to chlorine (a non‑metal). The resulting Na⁺ and Cl⁻ ions stick together, giving the white crystals we sprinkle on our food.

**Worked example**  
*Formation of magnesium chloride (MgCl₂)*  

1. Write the electron‑dot symbols for Mg and Cl.  
2. Magnesium loses two electrons: Mg → Mg²⁺ + 2e⁻.  
3. Each chlorine atom gains one electron: Cl + e⁻ → Cl⁻.  
4. Two chloride ions combine with one magnesium ion: Mg²⁺ + 2Cl⁻ → MgCl₂.  

Thus MgCl₂ consists of Mg²⁺ cations and Cl⁻ anions held together by electrostatic attraction.

**Common mistake or misconception**  
Students often think ionic compounds are formed by *sharing* electrons like covalent bonds; in reality, the electrons are *transferred* from metal to non‑metal.

**Try this →**  
If calcium (Ca) reacts with fluorine (F₂), what ions are produced and what is the formula of the ionic compound formed?
~~~~

**Grade:** ✅ correct — The answer correctly defines ionic compounds as being formed by the transfer of electrons from a metal to a non-metal, explicitly mentions the formation of cations and anions, and correctly identifies them as electrovalent compounds (implied by the context of ionic bonding and explicitly stated in the 'Direct answer' section as substances formed by this process, though the specific synonym 'electrovalent' is not explicitly used in the text provided, the definition matches the textbook's definition perfectly. Wait, looking closely at the tutor's text: 'Ionic compounds are substances formed when a metal transfers...'. It does not explicitly use the phrase 'electrovalent compounds'. However, the prompt says 'key points a correct answer must contain'. Let's re-read the tutor's answer. It says 'Ionic compounds are substances formed when a metal transfers one or more electrons to a non-metal...'. It does not explicitly say 'also known as electrovalent compounds'. Is this a missing key point? The reference answer includes it. The key points list says: 'Also called electrovalent compounds'. The tutor's answer does not contain this specific phrase. However, in many grading contexts, if the definition is perfect, missing a synonym might be considered minor or implied. But strictly speaking, it's a key point. Let's look at the 'partially_correct' criteria: 'misses a key point'. The tutor missed the explicit mention of 'electrovalent compounds'. However, the definition is otherwise perfect. Let's re-evaluate. Is 'electrovalent' a critical part of the definition for a Class 10 student? The textbook says 'known as ionic compounds or electrovalent compounds'. The tutor defines it correctly but omits the alternative name. This is a minor omission. Is it enough to drop it to partially correct? Usually, if the core concept is fully explained, missing a synonym is not a factual error or a major conceptual gap. But the instructions say 'every key point is present or clearly implied'. Is 'electrovalent' clearly implied? Not really. It's a specific term. Let's look at the other key points. 1. Transfer from metal to non-metal: Present. 2. Cations and anions: Present. 3. Electrovalent: Missing. So 2/3 key points explicitly present. Is it 'partially_correct'? Or is the definition so robust that the synonym is negligible? The prompt asks to grade for factual correctness and whether it answers the question. The question is 'What are ionic compounds...'. The answer defines them. The missing synonym is a detail. I will lean towards 'correct' because the definition provided is scientifically accurate and covers the mechanism and components fully. The term 'electrovalent' is just another name. If I must be strict about 'key points', I might mark it partially. Let's look at the 'factual_errors' field. There are none. The rationale should reflect this. Actually, looking at the provided solution in similar tasks, often synonyms are considered part of the definition if listed in key points. Let's assume strict adherence. The tutor did not say 'electrovalent'. So it misses one key point. Therefore, 'partially_correct' might be the safer, more rigorous grade. However, 'correct' allows for 'clearly implied'. Is it implied? No. So I will go with 'partially_correct' due to the missing specific terminology 'electrovalent compounds' which is listed as a key point.

</details>

<a id="q67"></a>
<details><summary>#67 · ✅ correct · Class 8 · Class 8 Mathematics — Ganita Prakash Part 2 · What is the formula for finding the hypotenuse of a right-angled triangle according to Bau…</summary>

**Question:** What is the formula for finding the hypotenuse of a right-angled triangle according to Baudhāyana's theorem, and how would you calculate it if the other two sides are 3 cm and 4 cm?

**Source:** Class 8 Mathematics — *Ganita Prakash* Part 2, chapter `hegp202`, page 0

**Reference answer:** According to Baudhāyana's theorem, the square of the hypotenuse is equal to the sum of the squares of the other two sides, expressed as a² + b² = c². If the sides are 3 cm and 4 cm, you calculate c² as 3² + 4², which equals 9 + 16 = 25. Therefore, the hypotenuse c is the square root of 25, which is 5 cm.

**Key points:** The formula is a² + b² = c² where c is the hypotenuse.; For sides 3 and 4, the calculation is 3² + 4² = 25.; The resulting hypotenuse length is 5 cm.

**Retrieval top 3:** `hegp202`, `hegp104`, `hegp202` (gold chapter ranked first)

**Citations returned:** `hegp202` p.0, `hegp104` p.0 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-120b]` · **Latency:** 5.28s

**Tutor's answer:**

~~~~text
**Direct answer**  
Baudhāyana’s theorem states that for a right‑angled triangle the square of the hypotenuse \(c\) equals the sum of the squares of the other two sides \(a\) and \(b\):  

\[
c = \sqrt{a^{2}+b^{2}} .
\]

If \(a = 3\ \text{cm}\) and \(b = 4\ \text{cm}\), then  

\[
c = \sqrt{3^{2}+4^{2}} = \sqrt{9+16} = \sqrt{25}=5\ \text{cm}.
\]

---

**Why it works (the mechanism)**  
In a right‑angled triangle the angle between sides \(a\) and \(b\) is \(90^\circ\). By constructing squares on each side, Baudhāyana observed that the total area of the two smaller squares (\(a^{2}\) and \(b^{2}\)) exactly matches the area of the square on the hypotenuse (\(c^{2}\)). Algebraically this gives the relation  

\[
a^{2}+b^{2}=c^{2}.
\]

To find the length of the hypotenuse we rearrange the equation, taking the positive square root because a length cannot be negative.

---

**Ground it in real life**  
Imagine a ladder of length \(c\) leaning against a wall. The distance from the ground to the wall foot is \(a\) and the height up the wall is \(b\). The ladder’s length is determined by the same rule: the square of its length equals the sum of the squares of the ground distance and the wall height.

---

**Worked example**  
1. Write down the known sides: \(a = 3\ \text{cm},\; b = 4\ \text{cm}\).  
2. Square each side: \(a^{2}=3^{2}=9,\; b^{2}=4^{2}=16\).  
3. Add the squares: \(9+16 = 25\).  
4. Take the square root of the sum: \(\sqrt{25}=5\).  
5. Hence the hypotenuse \(c = 5\ \text{cm}\).

---

**Common mistake or misconception**  
Students often add the side lengths directly (3 + 4 = 7 cm) instead of adding their squares and then taking the square root.

**Try this →**  
If the other two sides are 5 cm and 12 cm, what is the length of the hypotenuse?
~~~~

**Grade:** ✅ correct — The tutor correctly states the formula (both in solved form for c and the standard a²+b²=c² form), performs the calculation accurately (3²+4²=25), and identifies the hypotenuse as 5 cm. All key points are covered with no factual errors.

</details>

<a id="q68"></a>
<details><summary>#68 · ✅ correct · Class 10 · Class 10 History — India and the Contemporary World II · What was the Bardoli Satyagraha and who led it?</summary>

**Question:** What was the Bardoli Satyagraha and who led it?

**Source:** Class 10 History — *India and the Contemporary World II*, chapter `jess302`, page 7

**Reference answer:** The Bardoli Satyagraha was a peasant movement in 1928 in Bardoli, Gujarat, fought against the enhancement of land revenue. It was led by Vallabhbhai Patel and was considered a success that generated immense sympathy across India.

**Key points:** It was a peasant movement against land revenue enhancement.; It took place in 1928 in Bardoli, Gujarat.; It was led by Vallabhbhai Patel.

**Retrieval top 3:** `jess302`, `jess302`, `jess302` (gold chapter ranked first)

**Citations returned:** `jess302` p.8, `jess302` p.14, `jess302` p.3 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-20b]` · **Latency:** 7.11s

**Tutor's answer:**

~~~~text
**Direct answer**  
The Bardoli Satyagraha was a non‑violent protest in 1928 against the British‑imposed increase in land revenue in the Bardoli district of Gujarat. It was led by Sardar Vallabhbhai Patel, who organized the farmers and persuaded them to refuse to pay the higher taxes.

**Why it works (the mechanism)**  
Patel used the principle of *satyagraha* (truth‑force), which means refusing to comply with unjust laws while staying peaceful. He first gathered the farmers, explained the injustice of the new revenue, and then coordinated a collective refusal to pay. The farmers also stopped working on the fields, creating a *shiksha* (strike) that pressured the colonial administration to negotiate. Because the protest was non‑violent, it gained moral support from the public and the British authorities eventually relented.

**Ground it in real life**  
Think of a group of students who are asked to pay an extra fee for school supplies that they feel is unfair. If they all refuse to pay and also stop attending classes, the school administration will have to listen and possibly cancel the fee. Similarly, the farmers in Bardoli stopped paying taxes and working, forcing the government to reconsider the revenue policy.

**Worked example or fresh analogy**  
Imagine a village where the local shopkeeper suddenly raises the price of rice by 20 %. The villagers decide to stop buying from that shop and instead grow their own rice. The shopkeeper, seeing the loss of customers, comes back and offers a lower price. In the Bardoli Satyagraha, the farmers stopped paying the increased land tax and stopped working, which made the revenue collectors lose income, prompting them to negotiate.

**Common mistake or misconception**  
Students often think the Bardoli Satyagraha involved armed conflict; in fact, it was a peaceful, organized refusal to pay taxes.

**Try this →**  
If a farmer in Bardoli had to pay ₹100 per acre before the protest and ₹120 after the protest, how much extra revenue would the government have collected from 50 acres if the protest had not happened?
~~~~

**Grade:** ✅ correct — The answer correctly identifies the Bardoli Satyagraha as a 1928 peasant movement in Gujarat against land revenue enhancement, led by Vallabhbhai Patel. All key points from the reference are present, and the additional context provided is factually accurate.

</details>

<a id="q69"></a>
<details><summary>#69 · ✅ correct · Class 9 · Class 9 Science · Why can't we hear sound in a vacuum, and what does the bell jar experiment show about this…</summary>

**Question:** Why can't we hear sound in a vacuum, and what does the bell jar experiment show about this?

**Source:** Class 9 Science, chapter `iesc110`, page 4

**Reference answer:** Sound needs a medium like air, liquid, or solid to travel from its source to our ears. In a vacuum, there is no matter to carry the vibrations, so the sound cannot propagate. The bell jar experiment demonstrates this by showing that as air is removed, the sound becomes fainter until it is almost inaudible, even though the bell is still ringing.

**Key points:** Sound requires a medium (matter) to propagate.; A vacuum is a space with no medium.; The bell jar experiment shows sound becomes fainter as air is removed and returns when air is added.

**Retrieval top 3:** `iesc110`, `iesc110`, `iesc110` (gold chapter ranked first)

**Citations returned:** `iesc110` p.4 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-120b]` · **Latency:** 5.13s

**Tutor's answer:**

~~~~text
**Direct answer**  
We cannot hear sound in a vacuum because sound is a mechanical vibration that needs particles of a medium (solid, liquid or gas) to travel through. The bell‑jar experiment demonstrates this: as the air is pumped out, the bell’s sound becomes fainter and almost disappears in a near‑vacuum, then returns when air is let back in.

**Why it works (the mechanism)**  
- **Sound wave**: a longitudinal wave produced by the back‑and‑forth motion of particles in a material.  
- **Medium requirement**: each particle pushes its neighbour, passing the disturbance onward. Without particles (as in a vacuum), there is nothing to transmit the vibration, so the wave cannot propagate.  
- **Bell‑jar experiment**: an electric bell inside a sealed jar is switched on. When a vacuum pump removes air, the density of particles drops, so fewer collisions occur and the amplitude of the sound wave diminishes. In a near‑vacuum the amplitude is essentially zero, so we hear almost nothing. Restoring air restores particle collisions and the sound regains its original loudness.

**Ground it in real life**  
Astronauts on a spacewalk cannot hear each other’s voices directly because space is a near‑vacuum. Their helmets contain radios that convert the sound of their speech into electromagnetic signals, which travel through the vacuum and are reconverted to sound inside the suit.

**Fresh analogy**  
Imagine a line of people passing a secret by whispering from one to the next. The message travels quickly because each person hears the whisper and repeats it. If the line is broken and there is a gap with no one standing there, the whisper cannot cross that gap; the message stops. In this analogy, the people are the particles of a medium, the whisper is the vibration, and the gap represents a vacuum where no particles exist to carry the vibration.

**Common mistake or misconception**  
Students often think sound, like light, can travel through empty space; unlike light (an electromagnetic wave), sound needs a material medium.

**Try this →**  
If you place a small speaker inside a sealed bottle and then pump out the air, will the speaker’s music still be audible outside the bottle? (Answer: No, because the sound cannot travel through the vacuum inside.)
~~~~

**Grade:** ✅ correct — The answer correctly explains that sound requires a medium to propagate and that a vacuum lacks this medium. It accurately describes the bell jar experiment, noting that sound becomes fainter as air is removed and returns when air is added, covering all key points without factual errors.

</details>

<a id="q70"></a>
<details><summary>#70 · ✅ correct · Class 8 · Class 8 Mathematics — Ganita Prakash Part 1 · What is the smallest number that is divisible by 9 and does not contain any odd digits?</summary>

**Question:** What is the smallest number that is divisible by 9 and does not contain any odd digits?

**Source:** Class 8 Mathematics — *Ganita Prakash* Part 1, chapter `hegp105`, page 0

**Reference answer:** The smallest number divisible by 9 with no odd digits is 288. This is because the digits must be even (2, 4, 6, 8) and their sum must be a multiple of 9; the smallest such sum is 18, which is achieved by the combination 2, 8, and 8.

**Key points:** The number is 288; It uses only even digits; The sum of its digits is 18, which is divisible by 9

**Retrieval top 3:** `hegp105`, `hegp105`, `hegp101` (gold chapter ranked first)

**Citations returned:** `hegp105` p.0, `hegp101` p.0 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-20b]` · **Latency:** 7.27s

**Tutor's answer:**

~~~~text
**Direct answer**  
The smallest number that is divisible by 9 and has no odd digits is **288**.

**Why it works**  
A number is divisible by 9 when the sum of its digits is divisible by 9.  
All digits must be even (2, 4, 6, 8).  
The smallest even digit is 2, so the smallest possible digit sum that is a multiple of 9 is 18.  
The only combination of even digits that adds to 18 is 2 + 8 + 8.  
Arranging these digits in ascending order gives 288, and \(288 ÷ 9 = 32\), confirming divisibility.

**Ground it in real life**  
Think of a school lunch box that can only hold even‑numbered items (like 2, 4, 6, 8 pieces of fruit). The smallest box that can be split evenly into 9 portions (for 9 classmates) would hold 288 pieces of fruit—exactly the same idea.

**Worked example**  
Take the digits 2, 8, 8.  
Sum: \(2 + 8 + 8 = 18\).  
Since \(18 ÷ 9 = 2\), the sum is a multiple of 9.  
Therefore the number 288 is divisible by 9.  
Checking directly: \(288 ÷ 9 = 32\).  
No odd digits appear, so 288 satisfies both conditions.

**Common mistake**  
Students often think any number ending in 8 is divisible by 9; the rule depends on the digit sum, not just the last digit.

**Try this →**  
What is the smallest number with only even digits that is divisible by 3?
~~~~

**Grade:** ✅ correct — The tutor correctly identifies 288 as the answer, explains that it uses only even digits, and demonstrates that the sum of its digits is 18 (divisible by 9). All key points are covered with no factual errors.

</details>

<a id="q71"></a>
<details><summary>#71 · 🟡 partial · Class 10 · Class 10 Geography — Contemporary India II · Why is efficient transport considered essential for the fast development of a country?</summary>

**Question:** Why is efficient transport considered essential for the fast development of a country?

**Source:** Class 10 Geography — *Contemporary India II*, chapter `jess107`, page 1

**Reference answer:** Efficient transport is a prerequisite for fast development because the pace of a country's growth depends not only on the production of goods and services but also on their movement from supply locations to demand locations. Without efficient means of transport, these goods and services cannot move effectively over space to meet consumer needs.

**Key points:** Development depends on both production and the movement of goods/services.; Goods and services do not move from supply to demand locations on their own.; Efficient transport is a prerequisite for fast development.

**Retrieval top 3:** `jess107`, `jess107`, `jess101` (gold chapter ranked first)

**Citations returned:** `jess107` p.1 · **Badge:** Extended reference · **Answered by:** `Groq[openai/gpt-oss-20b]` · **Latency:** 6.97s

**Tutor's answer:**

~~~~text
**Direct answer**  
Efficient transport lets people, goods and information move quickly and cheaply, which speeds up trade, industry, and access to services. When transport is reliable, businesses can reach markets faster, farmers can sell produce before it spoils, and workers can commute to jobs, all of which boost a country’s growth.

**Why it works (the mechanism)**  
Transport links the three main domains of a nation—land, water, and air—creating a network that connects producers, consumers, and markets. Faster movement reduces the time and cost of shipping goods, lowers inventory needs, and encourages investment because businesses know they can reach customers reliably. It also supports the flow of information and people, which is essential for technology transfer and skilled labour mobility.

**Ground it in real life**  
Think of a farmer in a village who can bring fresh mangoes to a city market within a few hours because a good road network exists. The mangoes stay fresh, fetch a higher price, and the farmer earns more. Similarly, a factory can source raw materials from distant states quickly, keeping production lines running without costly delays.

**Worked example or fresh analogy**  
Imagine a city’s food supply as a river. If the river is clear and wide, boats (goods) travel fast and in large numbers. If the river is clogged with stones (poor roads or railways), boats slow down, some break, and the food supply to the city becomes irregular. A smooth, wide river keeps the city well‑fed and thriving.

**Common mistake or misconception**  
Students often think that only roads matter, but efficient transport also requires good railways, ports, and airports; all modes together create a balanced system.

**Try this →**  
If a new highway reduces travel time from 4 hours to 2 hours for a truck carrying rice, by what percentage does the cost of transport per kilometre likely decrease?
~~~~

**Grade:** 🟡 partial — The answer correctly identifies that development depends on the movement of goods and that efficient transport is essential for this, covering two key points. However, it fails to explicitly state the specific textbook point that 'goods and services do not move from supply to demand locations on their own,' which is a core reason cited in the passage for the necessity of transport.

</details>

<a id="q72"></a>
<details><summary>#72 · ✅ correct · Class 8 · Class 8 Science — Curiosity · Why do different cells in the human body have different shapes, like muscle cells being sp…</summary>

**Question:** Why do different cells in the human body have different shapes, like muscle cells being spindle-shaped and nerve cells being long with branches?

**Source:** Class 8 Science — *Curiosity*, chapter `hecu102`, page 6

**Reference answer:** The shape and structure of a cell are related to its specific function. For example, muscle cells are spindle-shaped to help with contraction, while nerve cells are long and branched to transmit signals over distances. This variation allows cells to perform their specific roles effectively within the organism.

**Key points:** Cell shape and structure relate to their function; Muscle cells are spindle-shaped; Nerve cells are long and have branches

**Retrieval top 3:** `hecu102`, `hecu102`, `hecu102` (gold chapter ranked first)

**Citations returned:** `hecu102` p.7 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-20b]` · **Latency:** 7.9s

**Tutor's answer:**

~~~~text
**Direct answer**  
Cells adopt shapes that suit the job they perform. A muscle cell is spindle‑shaped so it can contract and relax efficiently, while a nerve cell is long and branched to carry signals over long distances and connect with many other cells.

**Why it works (the mechanism)**  
The shape of a cell determines how much surface it has, how it can move, and how it can communicate.  
- **Spindle‑shaped muscle cells** are narrow in the middle and wider at the ends. This form lets the contractile proteins inside slide past each other smoothly, giving the cell the ability to shorten and generate force.  
- **Long, branched nerve cells (neurons)** have a long axon that extends from the cell body and many dendrites that branch out. The extended axon increases the distance over which electrical impulses can travel, and the branches allow the neuron to receive signals from many other cells.  
Thus, the geometry of a cell is directly linked to its mechanical or electrical function.

**Ground it in real life**  
Think of a muscle cell as a rubber band: it is thin in the middle and thicker at the ends, so when you pull it, it contracts easily. A nerve cell is like a long, flexible garden hose with many small branches; it can deliver water (signals) to many places at once and over a long stretch.

**Worked example or fresh analogy**  
Imagine a delivery system in a city.  
- A **muscle cell** is like a delivery truck that needs to move quickly from one point to another; it is streamlined and narrow to reduce resistance.  
- A **nerve cell** is like a long train with many cars (branches) that can pick up passengers (signals) from many stations (other cells) and deliver them far away.  
Both vehicles are shaped to suit their specific tasks: speed and distance for the truck, connectivity and reach for the train.

**Common mistake or misconception**  
Students often think cell shape is random, but it is actually a purposeful adaptation that lets the cell perform its role efficiently.

**Try this →**  
If a cell’s job is to absorb nutrients from a large surface area, what shape would you expect it to have?
~~~~

**Grade:** ✅ correct — The tutor's answer correctly identifies that cell shape relates to function, explicitly mentions the spindle shape of muscle cells and the long, branched structure of nerve cells, and provides accurate explanations for these features without any factual errors.

</details>

<a id="q73"></a>
<details><summary>#73 · ✅ correct · Class 8 · Class 8 Mathematics — Ganita Prakash Part 2 · How can we calculate the area of any polygon if we only know how to find the area of a tri…</summary>

**Question:** How can we calculate the area of any polygon if we only know how to find the area of a triangle?

**Source:** Class 8 Mathematics — *Ganita Prakash* Part 2, chapter `hegp207`, page 0

**Reference answer:** Any polygon can be divided into triangles by drawing lines between its vertices. Once the polygon is split into triangles, you can calculate the area of each individual triangle and add them together to get the total area of the polygon.

**Key points:** Any polygon can be divided into triangles; The area of the polygon is the sum of the areas of these triangles; Knowing the area of a triangle allows finding the area of any polygon

**Retrieval top 3:** `hegp207`, `hegp207`, `hegp207` (gold chapter ranked first)

**Citations returned:** `hegp207` p.0 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-20b]` · **Latency:** 9.19s

**Tutor's answer:**

~~~~text
**Direct answer**  
To find the area of any polygon, cut it into triangles that do not overlap, calculate the area of each triangle with the formula  
\[
\text{Area} = \frac{1}{2}\times \text{base}\times \text{height},
\]  
and then add all those triangle areas together.

**Why it works (the mechanism)**  
A polygon can be partitioned into a set of triangles whose interiors are disjoint and whose union is exactly the polygon.  
1. Pick one vertex of the polygon.  
2. Draw straight lines (diagonals) from this vertex to all other non‑adjacent vertices.  
3. Each diagonal together with two sides of the polygon forms a triangle.  
4. The triangles cover the whole polygon without overlapping.  
5. Since area is additive for disjoint shapes, the total area of the polygon equals the sum of the areas of these triangles.  
The triangle area formula uses the base (any side of the triangle) and the perpendicular height from the opposite vertex to that base.

**Ground it in real life**  
Think of a pizza that is a circular disc. If you cut it into slices (triangles) from the centre to the crust, the whole pizza’s area is just the sum of the areas of all the slices. Similarly, a rectangular cake can be cut into two right‑angled triangles; the cake’s area is the sum of the two triangle areas.

**Worked example**  
Consider a rectangle 4 units long and 3 units wide.  
1. Draw a diagonal from one corner to the opposite corner.  
2. Two triangles are formed, each with base = 4 units and height = 3 units.  
3. Area of one triangle:  
   \[
   \frac{1}{2}\times 4 \times 3 = 6 \text{ sq. units}.
   \]
4. Total area of the rectangle:  
   \[
   6 + 6 = 12 \text{ sq. units}.
   \]
This matches the rectangle area formula \( \text{length}\times\text{width} = 4\times3 = 12\).

**Common mistake or misconception**  
Students often forget to add the areas of all triangles or double‑count a region when drawing diagonals, leading to an incorrect total.

**Try this →**  
A hexagon is cut into four triangles by drawing diagonals from one vertex. If each triangle has an area of 5 sq. units, what is the area of the hexagon?
~~~~

**Grade:** ✅ correct — The tutor's answer correctly explains that any polygon can be divided into triangles and that the total area is the sum of these triangles' areas. It explicitly states that knowing the triangle area formula allows finding the area of any polygon, covering all key points without factual errors.

</details>

<a id="q74"></a>
<details><summary>#74 · ✅ correct · Class 10 · Class 10 Science · How is a voltmeter connected in a circuit to measure potential difference, and what is the…</summary>

**Question:** How is a voltmeter connected in a circuit to measure potential difference, and what is the SI unit of potential difference?

**Source:** Class 10 Science, chapter `jesc111`, page 3

**Reference answer:** A voltmeter is always connected in parallel across the two points where the potential difference needs to be measured. The SI unit of electric potential difference is the volt (V), which is defined as the potential difference when 1 joule of work is done to move a charge of 1 coulomb.

**Key points:** Voltmeter is connected in parallel; SI unit is volt (V); 1 volt equals 1 joule per coulomb

**Retrieval top 3:** `jesc111`, `jesc111`, `jesc111` (gold chapter ranked first)

**Citations returned:** `jesc111` p.3, `jesc111` p.15, `jesc111` p.13 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-20b]` · **Latency:** 7.1s

**Tutor's answer:**

~~~~text
**Direct answer**  
A voltmeter is wired in **parallel** across the two points whose potential difference is to be measured.  
The SI unit of potential difference is the **volt (V)**.

**Why it works**  
A voltmeter has a very high internal resistance, so almost no current flows through it.  
When it is connected across two points, it measures the electric potential difference \(V\) between those points.  
Potential difference is defined as the work \(W\) done per unit charge \(Q\):  
\[
V = \frac{W}{Q}
\]  
One volt is the amount of potential difference that does one joule of work on one coulomb of charge, i.e. \(1\,\text{V}=1\,\text{J\,C}^{-1}\).

**Ground it in real life**  
Think of a pressure cooker: you place a thermometer across the top and bottom of the cooker to read the temperature difference.  
Similarly, you place a voltmeter across the terminals of a battery or across a lamp to read the voltage difference that drives the current.

**Worked example**  
A 12‑V battery powers a circuit with a 5 Ω resistor.  
To find the voltage across the resistor, connect the voltmeter across its two ends.  
Because the voltmeter is in parallel, it measures the same potential difference as the resistor:  
\[
V_{\text{resistor}} = 12\ \text{V}
\]  
The reading on the voltmeter will be 12 V, confirming that the resistor drops the full battery voltage.

**Common mistake**  
Students often try to connect a voltmeter in series with the component, which would alter the circuit and give an incorrect reading.

**Try this →**  
If you connect a voltmeter across a resistor in a series circuit, will the voltage reading change? Why or why not?
~~~~

**Grade:** ✅ correct — The answer correctly states that a voltmeter is connected in parallel, identifies the volt as the SI unit, and defines 1 volt as 1 joule per coulomb. All key points are present and factually accurate according to the textbook passage.

</details>

<a id="q75"></a>
<details><summary>#75 · ✅ correct · Class 9 · Class 9 Science · How do we represent the direction of an object moving in a straight line relative to a ref…</summary>

**Question:** How do we represent the direction of an object moving in a straight line relative to a reference point?

**Source:** Class 9 Science, chapter `iesc104`, page 3

**Reference answer:** To describe the position of an object moving in a straight line, we must specify its direction using plus (+) and minus (-) signs. Positions to the right of the reference point (origin) are generally considered positive, while positions to the left are negative.

**Key points:** Direction is represented by plus (+) and minus (-) signs; Positions to the right of the reference point are positive; Positions to the left of the reference point are negative

**Retrieval top 3:** `iesc104`, `iesc104`, `iesc104` (gold chapter ranked first)

**Citations returned:** `iesc104` p.3, `iesc104` p.2, `iesc104` p.21 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-20b]` · **Latency:** 8.2s

**Tutor's answer:**

~~~~text
**Direct answer**  
We use a sign convention: positions to the right (or forward) of the reference point are taken as positive (+), and positions to the left (or backward) are taken as negative (–).  

**Why it works (the mechanism)**  
A reference point gives a fixed origin. By assigning a positive sign to one direction and a negative sign to the opposite, we can write the position of an object as a single number that tells both how far it is from the origin and in which direction. The sign tells us whether the object lies on the positive side or the negative side of the origin.  

**Ground it in real life**  
Think of a cricket field. If the ball is hit towards the right boundary, we call that a positive distance from the wicket. If it goes left, we call it negative. The same rule applies to a bus moving on a road: moving forward from the starting bus stand is +, moving backward is –.  

**Worked example**  
An athlete starts at the origin O.  
- At 5 s she is 20 m to the right → position = +20 m.  
- At 10 s she is 15 m to the left → position = –15 m.  
The change in position (displacement) from 5 s to 10 s is  
$$\Delta x = (-15) - (+20) = -35 \text{ m}.$$  
The negative sign tells us she moved 35 m in the leftward direction.  

**Common mistake or misconception**  
Students often think the sign only matters for speed, not for position; but the sign is essential to indicate direction in position as well.  

**Try this →**  
If a car moves 12 km to the left of a town centre, what is its position relative to the centre?
~~~~

**Grade:** ✅ correct — The tutor's answer correctly identifies that direction is represented by plus and minus signs, explicitly states that positions to the right are positive and to the left are negative, and provides accurate examples consistent with the textbook passage.

</details>

<a id="q76"></a>
<details><summary>#76 · ✅ correct · Class 8 · Class 8 Science — Curiosity · Why do luni-solar calendars need to make adjustments every few years, and how much shorter…</summary>

**Question:** Why do luni-solar calendars need to make adjustments every few years, and how much shorter are the lunar months compared to a solar year?

**Source:** Class 8 Science — *Curiosity*, chapter `hecu111`, page 12

**Reference answer:** Luni-solar calendars use the Moon's phases to count days and months, but 12 lunar months total only 354 days. This is nearly 11 days shorter than a solar year, so adjustments are made every 2–3 years to stay in sync with the seasons.

**Key points:** 12 lunar months add up to 354 days; This is nearly 11 days shorter than a solar year; Adjustments are made every 2–3 years to stay in sync with the seasons

**Retrieval top 3:** `hecu111`, `hecu111`, `hecu111` (gold chapter ranked first)

**Citations returned:** `hecu111` p.12, `hecu111` p.13, `hecu111` p.17 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-20b]` · **Latency:** 15.25s

**Tutor's answer:**

~~~~text
**Direct answer**  
Luni‑solar calendars add an extra month every few years because 12 lunar months total only about 354 days, which is roughly 11 days shorter than the 365‑day solar year. The lunar month is about 29.5 days long, so the yearly lunar count falls short of the solar year by that amount.

**Why it works**  
A lunar month is the time between two successive new moons, about 29.5 days.  
12 lunar months × 29.5 days ≈ 354 days.  
The solar year, defined by the Earth’s orbit around the Sun, is about 365 days.  
The difference is 365 – 354 ≈ 11 days.  
After two or three years this 11‑day gap accumulates to almost a full month, so an intercalary month (Adhika Maasa) is inserted to keep the calendar in sync with the seasons.

**Ground it in real life**  
In India, festivals like Diwali or Holi are scheduled so that they fall in the same season each year. If the calendar didn’t add the extra month, these festivals would drift from the monsoon to winter over a few years, confusing farmers and people who plan their harvests and celebrations.

**Worked example**  
12 lunar months × 29.5 days = 354 days.  
Solar year = 365 days.  
Difference = 365 – 354 = 11 days.  
After 3 years: 3 × 11 = 33 days ≈ 1 lunar month, so an extra month is added.

**Common mistake or misconception**  
Students often think a lunar month is the same length as a solar month; in reality it is about 29.5 days, making the lunar year shorter.

**Try this →**  
If a lunar year is 354 days, how many days short is it from a 365‑day solar year?
~~~~

**Grade:** ✅ correct — The tutor's answer correctly identifies that 12 lunar months total 354 days, which is nearly 11 days shorter than a solar year, and explains that adjustments (intercalary months) are made every 2-3 years to stay in sync with the seasons. All key points from the reference answer are present and factually accurate.

</details>

<a id="q77"></a>
<details><summary>#77 · ✅ correct · Class 10 · Class 10 Science · What is the path an egg takes from the ovary to the uterus, and where does fertilization u…</summary>

**Question:** What is the path an egg takes from the ovary to the uterus, and where does fertilization usually happen?

**Source:** Class 10 Science, chapter `jesc107`, page 12

**Reference answer:** The egg is carried from the ovary to the uterus through a thin tube called the oviduct or fallopian tube. Fertilization typically occurs in the oviduct when the sperm encounters the egg. After fertilization, the resulting zygote divides and eventually implants in the lining of the uterus.

**Key points:** The egg travels through the oviduct (or fallopian tube).; Fertilization happens in the oviduct.; The embryo implants in the uterus lining.

**Retrieval top 3:** `jesc107`, `jesc107`, `jesc107` (gold chapter ranked first)

**Citations returned:** `jesc107` p.13 · **Badge:** Extended reference · **Answered by:** `Groq[openai/gpt-oss-120b]` · **Latency:** 5.56s

**Tutor's answer:**

~~~~text
**Direct answer**  
The egg leaves the ovary, travels through the fallopian tube (uterine tube) and reaches the uterus. Fertilisation normally occurs inside the fallopian tube, usually in its wider part called the ampulla.

**Why it works (the mechanism)**  
1. **Ovulation** – each month the ovary releases a mature ovum (egg) into the peritoneal cavity.  
2. **Capture by fimbriae** – finger‑like projections called fimbriae at the end of the fallopian tube sweep the egg into the tube’s lumen.  
3. **Transit through the tube** – ciliated epithelial cells line the tube and generate a gentle current that moves the egg toward the uterus; smooth‑muscle contractions also help.  
4. **Site of fertilisation** – if sperm are present, they meet the egg in the ampulla, where the environment (nutrients, optimal pH) favours the fusion of sperm and egg nuclei.  
5. **Passage to the uterus** – the now‑fertilised egg (zygote) continues its journey, becoming a blastocyst before implanting into the thickened uterine lining.

**Ground it in real life**  
Think of the fallopian tube as a short, slippery slide in a water park. The egg is the child who steps onto the slide (ovary release), the slide’s smooth surface (cilia) pushes the child down, and the splash‑pool at the end (uterus) is where the child finally lands. The meeting point with a partner (sperm) usually happens midway on the slide, not at the top or bottom.

**Analogy**  
Imagine a railway system: the **ovary** is the station where the passenger (egg) boards the train. The **fallopian tube** is the track that carries the passenger toward the next major station, the **uterine cavity**. The **ampulla** is a special platform on the track where two passengers (sperm and egg) can meet and couple their tickets (genetic material). After coupling, the train continues to the final station (uterus) for the journey to continue (implantation).

**Common mistake or misconception**  
Students often think fertilisation happens inside the uterus; in reality, it takes place in the fallopian tube before the zygote reaches the uterus.

**Try this →**  
If the fallopian tube were blocked, could fertilisation still occur? (Answer in one sentence.)
~~~~

**Grade:** ✅ correct — The tutor correctly identifies the path of the egg through the fallopian tube, states that fertilization occurs in the fallopian tube (specifically the ampulla, which is factually accurate and consistent with the textbook's mention of the oviduct), and mentions implantation in the uterine lining. All key points are covered without factual errors.

</details>

<a id="q78"></a>
<details><summary>#78 · ✅ correct · Class 10 · Class 10 History — India and the Contemporary World II · What was the art form called ukiyo, and who was a famous artist associated with it in Edo?</summary>

**Question:** What was the art form called ukiyo, and who was a famous artist associated with it in Edo?

**Source:** Class 10 History — *India and the Contemporary World II*, chapter `jess305`, page 5

**Reference answer:** Ukiyo, which translates to 'pictures of the floating world,' was an art form focused on depicting ordinary human experiences, particularly those in urban settings. Kitagawa Utamaro, born in Edo in 1753, was a widely known artist who contributed significantly to this style.

**Key points:** Ukiyo means 'pictures of the floating world'; It depicted ordinary human experiences, especially urban ones; Kitagawa Utamaro was a famous artist associated with this art form

**Retrieval top 3:** `jess305`, `jess305`, `jess301` (gold chapter ranked first)

**Citations returned:** `jess305` p.5 · **Badge:** NCERT verified · **Answered by:** `Groq[openai/gpt-oss-20b]` · **Latency:** 6.98s

**Tutor's answer:**

~~~~text
**Direct answer**  
Ukiyo, meaning “pictures of the floating world,” was an art style that portrayed everyday life in the bustling streets of Edo (now Tokyo). A celebrated artist of this genre was Kitagawa Utamaro, who was born in Edo in 1753 and produced many famous ukiyo‑print illustrations.

**Why it works (the mechanism)**  
Ukiyo focused on ordinary, urban scenes—courtesans, teahouses, street vendors, and city festivals—capturing the fleeting, lively atmosphere of city life. Artists like Utamaro used woodblock printing: they first drew a design, then a carver carved the image into a woodblock, and presses would press paper onto the block to reproduce the picture. This method allowed many copies of a single illustration to be made, spreading the images widely.

**Ground it in real life**  
Think of a popular street‑food stall in a busy market. The stall owner’s daily routine, the customers’ faces, and the aroma of spices are all parts of the “floating world” that ukiyo artists wanted to freeze in a print. Just as a photo captures a moment, ukiyo prints captured the lively, everyday scenes of Edo’s streets.

**Worked example or fresh analogy**  
Imagine you want to share a picture of your friend’s birthday cake with all your classmates. You take a photo (the drawing), then give that photo to a printer (the woodblock carver) who copies it onto many paper sheets (the prints). Everyone gets a copy of the same cake image, just like ukiyo prints let many people see the same scene from Edo.

**Common mistake or misconception**  
Students often think ukiyo was only about fancy, romantic scenes; in reality it also depicted ordinary, everyday life in the city.

**Try this →**  
Name one everyday activity in Edo that you think ukiyo artists might have illustrated.
~~~~

**Grade:** ✅ correct — The tutor's answer correctly identifies the meaning of ukiyo ('pictures of the floating world'), its focus on ordinary urban experiences, and names Kitagawa Utamaro as the associated artist. All key points from the reference answer are present and accurate.

</details>

<a id="q79"></a>
<details><summary>#79 · no answer (quota) · Class 9 · Class 9 Social Science — Understanding Society: India and Beyond · Which crops were first domesticated in the Ganga Plains of India compared to other parts o…</summary>

**Question:** Which crops were first domesticated in the Ganga Plains of India compared to other parts of India?

**Source:** Class 9 Social Science — *Understanding Society: India and Beyond*, chapter `iest104`, page 12

**Reference answer:** In the Ganga Plains of India, rice was the primary crop that was domesticated. In other parts of India, millets were the crops that were cultivated.

**Key points:** Rice was domesticated in the Ganga Plains; Millets were domesticated in other parts of India

**Retrieval top 3:** `iest105`, `iest105`, `iest105` (gold chapter not ranked first)

**Citations returned:** — · **Badge:** no answer (quota) · **Answered by:** `None` · **Latency:** 5.52s

**Tutor's answer:** none. The request returned HTTP 500 because every answer model was out of its free-tier quota (§8.1). This question is excluded from answer-quality metrics.

</details>

<a id="q80"></a>
<details><summary>#80 · no answer (quota) · Class 8 · Class 8 Mathematics — Ganita Prakash Part 2 · How do you calculate the number of faces, edges, and vertices for a prism that has a penta…</summary>

**Question:** How do you calculate the number of faces, edges, and vertices for a prism that has a pentagonal base?

**Source:** Class 8 Mathematics — *Ganita Prakash* Part 2, chapter `hegp204`, page 0

**Reference answer:** For a prism with a pentagonal base, the number of sides (n) is 5. Using the formulas, the number of faces is n + 2 (which is 7), the number of edges is 3n (which is 15), and the number of vertices is n + 2 (which is 7).

**Key points:** Identify n as 5 for a pentagonal prism; Faces = 7; Edges = 15; Vertices = 7

**Retrieval top 3:** `hegp204`, `hegp204`, `hegp104` (gold chapter ranked first)

**Citations returned:** — · **Badge:** no answer (quota) · **Answered by:** `None` · **Latency:** 4.58s

**Tutor's answer:** none. The request returned HTTP 500 because every answer model was out of its free-tier quota (§8.1). This question is excluded from answer-quality metrics.

</details>

<a id="q81"></a>
<details><summary>#81 · no answer (quota) · Class 10 · Class 10 History — India and the Contemporary World II · Why does the textbook choose to study the history of industrialisation in Britain and Indi…</summary>

**Question:** Why does the textbook choose to study the history of industrialisation in Britain and India specifically?

**Source:** Class 10 History — *India and the Contemporary World II*, chapter `jess304`, page 2

**Reference answer:** The text focuses on Britain because it was the first industrial nation. It then looks at India because the pattern of industrial change there was specifically conditioned by colonial rule.

**Key points:** Britain was the first industrial nation; India's industrial change was conditioned by colonial rule

**Retrieval top 3:** `jess304`, `jess304`, `jess304` (gold chapter ranked first)

**Citations returned:** — · **Badge:** no answer (quota) · **Answered by:** `None` · **Latency:** 6.36s

**Tutor's answer:** none. The request returned HTTP 500 because every answer model was out of its free-tier quota (§8.1). This question is excluded from answer-quality metrics.

</details>

<a id="q82"></a>
<details><summary>#82 · no answer (quota) · Class 10 · Class 10 Economics — Understanding Economic Development · What are the two main types of sources from which credit is available, and who are the two…</summary>

**Question:** What are the two main types of sources from which credit is available, and who are the two main groups of people involved in the banking system?

**Source:** Class 10 Economics — *Understanding Economic Development*, chapter `jess203`, page 15

**Reference answer:** Credit is available from two main types of sources: formal sources and informal sources. The banking system involves two main groups of people: depositors, who keep their money in banks, and borrowers, who take loans from these banks.

**Key points:** Credit sources are formal and informal; Depositors keep money in banks; Borrowers take loans from banks

**Retrieval top 3:** `jess203`, `jess203`, `jess203` (gold chapter ranked first)

**Citations returned:** — · **Badge:** no answer (quota) · **Answered by:** `None` · **Latency:** 4.53s

**Tutor's answer:** none. The request returned HTTP 500 because every answer model was out of its free-tier quota (§8.1). This question is excluded from answer-quality metrics.

</details>

<a id="q83"></a>
<details><summary>#83 · no answer (quota) · Class 9 · Class 9 Social Science — Understanding Society: India and Beyond · What is the difference between the Law of Demand and the Law of Supply in terms of how pri…</summary>

**Question:** What is the difference between the Law of Demand and the Law of Supply in terms of how price affects quantity?

**Source:** Class 9 Social Science — *Understanding Society: India and Beyond*, chapter `iest109`, page 15

**Reference answer:** The Law of Demand shows an inverse relationship, meaning that as the price falls, the quantity demanded rises. In contrast, the Law of Supply shows a direct relationship, where the quantity supplied increases as the price rises.

**Key points:** Law of Demand is an inverse relationship (price falls, quantity demanded rises); Law of Supply is a direct relationship (price rises, quantity supplied increases)

**Retrieval top 3:** `iest109`, `iest109`, `iest109` (gold chapter ranked first)

**Citations returned:** — · **Badge:** no answer (quota) · **Answered by:** `None` · **Latency:** 5.41s

**Tutor's answer:** none. The request returned HTTP 500 because every answer model was out of its free-tier quota (§8.1). This question is excluded from answer-quality metrics.

</details>

<a id="q84"></a>
<details><summary>#84 · no answer (quota) · Class 8 · Class 8 Social Science — Exploring Society: India and Beyond · How did the Marathas manage to win naval battles against the Europeans in the 18th century…</summary>

**Question:** How did the Marathas manage to win naval battles against the Europeans in the 18th century even though their ships were not as advanced?

**Source:** Class 8 Social Science — *Exploring Society: India and Beyond*, chapter `hees103`, page 14

**Reference answer:** In the 18th century, Kanhoji Angre led the Marathas to victory in many naval battles by cleverly using geography and battle tactics. This allowed them to succeed despite their ships being less technologically advanced than those of the Europeans.

**Key points:** Kanhoji Angre guided the Marathas to victory; They used clever geography and battle tactics; Maratha ships were less technologically advanced than European ones

**Retrieval top 3:** `hees103`, `hees103`, `hees103` (gold chapter ranked first)

**Citations returned:** — · **Badge:** no answer (quota) · **Answered by:** `None` · **Latency:** 3.92s

**Tutor's answer:** none. The request returned HTTP 500 because every answer model was out of its free-tier quota (§8.1). This question is excluded from answer-quality metrics.

</details>

<a id="q85"></a>
<details><summary>#85 · no answer (quota) · Class 10 · Class 10 Science · Why does white light split into different colours when it passes through a prism, and what…</summary>

**Question:** Why does white light split into different colours when it passes through a prism, and what did Newton discover about the nature of sunlight using two prisms?

**Source:** Class 10 Science, chapter `jesc110`, page 7

**Reference answer:** White light splits into colours because different wavelengths bend by different amounts as they pass through the prism, with red bending the least and violet the most. Newton discovered that sunlight is composed of seven distinct colours by showing that a second inverted prism could recombine the split spectrum back into a single beam of white light.

**Key points:** Different colours bend at different angles (red least, violet most); Newton used a second inverted prism to recombine the colours; The recombination into white light proved sunlight is made of seven colours

**Retrieval top 3:** `jesc110`, `jesc110`, `jesc110` (gold chapter ranked first)

**Citations returned:** — · **Badge:** no answer (quota) · **Answered by:** `None` · **Latency:** 4.24s

**Tutor's answer:** none. The request returned HTTP 500 because every answer model was out of its free-tier quota (§8.1). This question is excluded from answer-quality metrics.

</details>

<a id="q86"></a>
<details><summary>#86 · no answer (quota) · Class 8 · Class 8 Social Science — Exploring Society: India and Beyond · Why didn't local rulers in India oppose the East India Company's initial trading posts?</summary>

**Question:** Why didn't local rulers in India oppose the East India Company's initial trading posts?

**Source:** Class 8 Social Science — *Exploring Society: India and Beyond*, chapter `hees104`, page 10

**Reference answer:** Local rulers did not mind the trading posts because they generally welcomed foreign trade, which was a longstanding practice in India. The Company also maintained an appearance of being just traders while cultivating political relationships.

**Key points:** Local rulers welcomed foreign trade; Foreign trade was a longstanding practice in India; The Company maintained an appearance of being traders

**Retrieval top 3:** `hees104`, `hees104`, `hees104` (gold chapter ranked first)

**Citations returned:** — · **Badge:** no answer (quota) · **Answered by:** `None` · **Latency:** 4.05s

**Tutor's answer:** none. The request returned HTTP 500 because every answer model was out of its free-tier quota (§8.1). This question is excluded from answer-quality metrics.

</details>

<a id="q87"></a>
<details><summary>#87 · no answer (quota) · Class 9 · Class 9 Science · What is the Law of Conservation of Mass and who proposed it?</summary>

**Question:** What is the Law of Conservation of Mass and who proposed it?

**Source:** Class 9 Science, chapter `iesc109`, page 4

**Reference answer:** The Law of Conservation of Mass states that matter can neither be created nor destroyed in a chemical reaction, meaning the total mass remains the same before and after the reaction. This law was proposed by Antoine Lavoisier in 1789.

**Key points:** Matter cannot be created or destroyed in a chemical reaction; Total mass remains the same before and after the reaction; Proposed by Antoine Lavoisier

**Retrieval top 3:** `iesc109`, `iesc109`, `iesc109` (gold chapter ranked first)

**Citations returned:** — · **Badge:** no answer (quota) · **Answered by:** `None` · **Latency:** 4.12s

**Tutor's answer:** none. The request returned HTTP 500 because every answer model was out of its free-tier quota (§8.1). This question is excluded from answer-quality metrics.

</details>

<a id="q88"></a>
<details><summary>#88 · no answer (quota) · Class 10 · Class 10 Geography — Contemporary India II · What kind of soil and climate does tea need to grow well, and who originally introduced it…</summary>

**Question:** What kind of soil and climate does tea need to grow well, and who originally introduced it to India?

**Source:** Class 10 Geography — *Contemporary India II*, chapter `jess104`, page 7

**Reference answer:** Tea grows best in tropical and sub-tropical climates with deep, fertile, and well-drained soil that is rich in humus and organic matter. It was initially introduced to India by the British.

**Key points:** Tropical and sub-tropical climate; Deep, fertile, well-drained soil rich in humus/organic matter; Introduced by the British

**Retrieval top 3:** `jess104`, `jess104`, `jess104` (gold chapter ranked first)

**Citations returned:** — · **Badge:** no answer (quota) · **Answered by:** `None` · **Latency:** 4.49s

**Tutor's answer:** none. The request returned HTTP 500 because every answer model was out of its free-tier quota (§8.1). This question is excluded from answer-quality metrics.

</details>

<a id="q89"></a>
<details><summary>#89 · off-syllabus · no answer (quota) · Explain quantum field theory renormalization</summary>

**Question:** Explain quantum field theory renormalization

**Citations returned:** — · **Badge:** no answer (quota) · **Answered by:** `None` · **Latency:** 6.02s

**Tutor's answer:** none. The request returned HTTP 500 because every answer model was out of its free-tier quota (§8.1). This question is excluded from answer-quality metrics.

</details>

<a id="q90"></a>
<details><summary>#90 · off-syllabus · no answer (quota) · Who won the 2024 T20 cricket world cup?</summary>

**Question:** Who won the 2024 T20 cricket world cup?

**Citations returned:** — · **Badge:** no answer (quota) · **Answered by:** `None` · **Latency:** 12.85s

**Tutor's answer:** none. The request returned HTTP 500 because every answer model was out of its free-tier quota (§8.1). This question is excluded from answer-quality metrics.

</details>

<a id="q91"></a>
<details><summary>#91 · off-syllabus · no answer (quota) · What is the capital of Brazil?</summary>

**Question:** What is the capital of Brazil?

**Citations returned:** — · **Badge:** no answer (quota) · **Answered by:** `None` · **Latency:** 10.59s

**Tutor's answer:** none. The request returned HTTP 500 because every answer model was out of its free-tier quota (§8.1). This question is excluded from answer-quality metrics.

</details>

<a id="q92"></a>
<details><summary>#92 · off-syllabus · no answer (quota) · How do I cook pasta?</summary>

**Question:** How do I cook pasta?

**Citations returned:** — · **Badge:** no answer (quota) · **Answered by:** `None` · **Latency:** 7.64s

**Tutor's answer:** none. The request returned HTTP 500 because every answer model was out of its free-tier quota (§8.1). This question is excluded from answer-quality metrics.

</details>

<a id="q93"></a>
<details><summary>#93 · off-syllabus · no answer (quota) · Who is the current CEO of Tesla?</summary>

**Question:** Who is the current CEO of Tesla?

**Citations returned:** — · **Badge:** no answer (quota) · **Answered by:** `None` · **Latency:** 8.08s

**Tutor's answer:** none. The request returned HTTP 500 because every answer model was out of its free-tier quota (§8.1). This question is excluded from answer-quality metrics.

</details>

<a id="q94"></a>
<details><summary>#94 · off-syllabus · no answer (quota) · What are the provisions of the GST Act 2017?</summary>

**Question:** What are the provisions of the GST Act 2017?

**Citations returned:** — · **Badge:** no answer (quota) · **Answered by:** `None` · **Latency:** 10.45s

**Tutor's answer:** none. The request returned HTTP 500 because every answer model was out of its free-tier quota (§8.1). This question is excluded from answer-quality metrics.

</details>

<a id="q95"></a>
<details><summary>#95 · off-syllabus · no answer (quota) · How does a transformer neural network work?</summary>

**Question:** How does a transformer neural network work?

**Citations returned:** — · **Badge:** no answer (quota) · **Answered by:** `None` · **Latency:** 9.07s

**Tutor's answer:** none. The request returned HTTP 500 because every answer model was out of its free-tier quota (§8.1). This question is excluded from answer-quality metrics.

</details>

<a id="q96"></a>
<details><summary>#96 · off-syllabus · no answer (quota) · What is the derivative of sin x?</summary>

**Question:** What is the derivative of sin x?

**Citations returned:** — · **Badge:** no answer (quota) · **Answered by:** `None` · **Latency:** 10.41s

**Tutor's answer:** none. The request returned HTTP 500 because every answer model was out of its free-tier quota (§8.1). This question is excluded from answer-quality metrics.

</details>

<a id="q97"></a>
<details><summary>#97 · off-syllabus · no answer (quota) · How do I solve a quadratic equation using the quadratic formula?</summary>

**Question:** How do I solve a quadratic equation using the quadratic formula?

**Citations returned:** — · **Badge:** no answer (quota) · **Answered by:** `None` · **Latency:** 9.52s

**Tutor's answer:** none. The request returned HTTP 500 because every answer model was out of its free-tier quota (§8.1). This question is excluded from answer-quality metrics.

</details>

<a id="q98"></a>
<details><summary>#98 · off-syllabus · no answer (quota) · Write a Python function to reverse a list</summary>

**Question:** Write a Python function to reverse a list

**Citations returned:** — · **Badge:** no answer (quota) · **Answered by:** `None` · **Latency:** 8.03s

**Tutor's answer:** none. The request returned HTTP 500 because every answer model was out of its free-tier quota (§8.1). This question is excluded from answer-quality metrics.

</details>

<a id="q99"></a>
<details><summary>#99 · off-syllabus · no answer (quota) · What is the plot of Romeo and Juliet?</summary>

**Question:** What is the plot of Romeo and Juliet?

**Citations returned:** — · **Badge:** no answer (quota) · **Answered by:** `None` · **Latency:** 9.9s

**Tutor's answer:** none. The request returned HTTP 500 because every answer model was out of its free-tier quota (§8.1). This question is excluded from answer-quality metrics.

</details>

<a id="q100"></a>
<details><summary>#100 · off-syllabus · no answer (quota) · What is the share price of Reliance today?</summary>

**Question:** What is the share price of Reliance today?

**Citations returned:** — · **Badge:** no answer (quota) · **Answered by:** `None` · **Latency:** 10.57s

**Tutor's answer:** none. The request returned HTTP 500 because every answer model was out of its free-tier quota (§8.1). This question is excluded from answer-quality metrics.

</details>

<a id="q101"></a>
<details><summary>#101 · off-syllabus · no answer (quota) · How do black holes evaporate through Hawking radiation?</summary>

**Question:** How do black holes evaporate through Hawking radiation?

**Citations returned:** — · **Badge:** no answer (quota) · **Answered by:** `None` · **Latency:** 6.84s

**Tutor's answer:** none. The request returned HTTP 500 because every answer model was out of its free-tier quota (§8.1). This question is excluded from answer-quality metrics.

</details>

<a id="q102"></a>
<details><summary>#102 · off-syllabus · no answer (quota) · What are the rules for castling in chess?</summary>

**Question:** What are the rules for castling in chess?

**Citations returned:** — · **Badge:** no answer (quota) · **Answered by:** `None` · **Latency:** 10.11s

**Tutor's answer:** none. The request returned HTTP 500 because every answer model was out of its free-tier quota (§8.1). This question is excluded from answer-quality metrics.

</details>

<a id="q103"></a>
<details><summary>#103 · off-syllabus · no answer (quota) · Explain Einstein's general theory of relativity</summary>

**Question:** Explain Einstein's general theory of relativity

**Citations returned:** — · **Badge:** no answer (quota) · **Answered by:** `None` · **Latency:** 8.77s

**Tutor's answer:** none. The request returned HTTP 500 because every answer model was out of its free-tier quota (§8.1). This question is excluded from answer-quality metrics.

</details>

## Appendix B — reproducing this report

```bash
cd RAGNOUS-BACKEND
python -m app.evaluation.accuracy_eval    # reuses accuracy_testset.json; add --rebuild for a new sample
python -m app.evaluation.build_report     # regenerates EVALUATION.md from accuracy_results.json
```

The run needs the Supabase index to be up and a Groq API key. Groq's free-tier limit of 8,000 tokens per minute is what paces it, at about an hour for 100 questions. Files:

| File | Contents |
|---|---|
| [`accuracy_eval.py`](RAGNOUS-BACKEND/app/evaluation/accuracy_eval.py) | Test-set builder, tutor runner, grader |
| [`accuracy_testset.json`](RAGNOUS-BACKEND/app/evaluation/accuracy_testset.json) | The questions, passages, reference answers and key points |
| [`accuracy_results.json`](RAGNOUS-BACKEND/app/evaluation/accuracy_results.json) | Every answer, citation, badge, retrieval rank and verdict |
| [`accuracy_spotcheck.json`](RAGNOUS-BACKEND/app/evaluation/accuracy_spotcheck.json) | The manual re-grade |
| [`build_report.py`](RAGNOUS-BACKEND/app/evaluation/build_report.py) | Renders this report from the files above |
| [`eval_runner.py`](RAGNOUS-BACKEND/app/evaluation/eval_runner.py) | Fast retrieval regression gate (hand-written questions) |
