# RAGNOUS Backend Architecture Documentation

## Overview

The RAGNOUS Backend is a highly optimized, asynchronous FastAPI-based application designed to serve as the intelligence engine for the AI Vidyarthi platform. It orchestrates multiple Large Language Models (LLMs), handles vector embeddings for Retrieval-Augmented Generation (RAG), and dynamically generates interactive content such as Mermaid.js flowcharts and React Three Fiber (R3F) 3D models with HTML overlays.

## Core Technologies & Tools

1. **FastAPI**: The core web framework used for building the asynchronous, high-performance REST API.
2. **LangChain**: Used for prompt management, LLM abstraction, and structuring the conversation flow (System/Human/AI messages).
3. **Groq (Llama 3.x)**: Used for primary inference (Llama-3.3-70b) and fast intent routing (Llama-3.1-8b).
4. **Google Gemini**: Integrated as an alternative LLM provider (Gemini 3.6 Flash) for multimodal or specific generative tasks.
5. **SentenceTransformers**: Used to generate fast, local multilingual embeddings (`paraphrase-multilingual-MiniLM-L12-v2`) for vector search.
6. **Qdrant / Vector DB (Asyncpg)**: Used to store and retrieve NCERT curriculum context for accurate, hallucination-free answers.
7. **Tavily API**: Integrated for asynchronous web search capabilities when curriculum data is insufficient.
8. **Cohere**: Configured for Cross-Encoder reranking to ensure the most relevant context is prioritized during RAG.

## Project Structure

```text
RAGNOUS-BACKEND/
├── app/
│   ├── api/          # API Routers and endpoints (e.g., v1/chat.py)
│   ├── core/         # Core application config and lifecycle events
│   ├── db/           # Database connections (Vector DB, asyncpg)
│   ├── agents/       # Specific LLM agent logic and routing
│   ├── services/     # Business logic, embedding generation, external API calls
│   ├── memory/       # Conversation history management
│   ├── evaluation/   # Tracing and LLM evaluation metrics
│   ├── knowledge_graph/# Knowledge graph integration
│   └── main.py       # FastAPI application entry point
├── scripts/          # Utility, ingest and cache-warming scripts
├── data/             # NCERT books, unpacked chapters, cropped figures, 3D meshes
├── tests/            # Pytest test suites (e.g., test_gemini.py)
├── docs/             # Technical documentation and architecture references
├── Dockerfile        # Containerization configuration
└── requirements.txt  # Python package dependencies
```

## Advanced Features & Workflows

### 1. Dynamic Intent Routing
Instead of relying on a single monolithic LLM prompt, the backend uses a smaller, faster model (Llama-3.1-8b-instant) to determine the user's intent. Based on this intent (e.g., needing a 3D model, a flowchart, or a web search), the backend dynamically injects specific instruction sets into the main LLM (Llama-3.3-70b) prompt. This saves context tokens and greatly improves generation accuracy.

### 2. Retrieval-Augmented Generation (RAG)
For academic and curriculum-related questions, the backend converts the query into a vector embedding using `SentenceTransformers`. It searches the vector database for NCERT-verified context. It calculates a `confidenceScore` (mapped from vector similarity) and returns this score alongside the citations directly to the frontend, allowing the UI to display an "NCERT Verified" badge.

### 3. Dynamic R3F 3D Generation & HTML Overlays
When a 3D model is requested, the backend injects the `R3F_BLOCK` instruction set. This prompts the LLM to write a functional React Three Fiber component.
- **HTML Annotations**: A recent addition allows the LLM to place `<Html>` tags (from `@react-three/drei`) inside the 3D space to label parts of the model (e.g., labeling parts of an atom or the layers of the Earth).
- **Spatial Awareness**: The instructions explicitly teach the LLM to position these HTML labels dynamically using math (`Math.sin`, `Math.cos`) to ensure labels don't overlap and maintain a distance factor.

### 4. Mermaid Flowchart Generation
If the user asks for a process or timeline, the backend injects the `MERMAID_BLOCK` instruction set. It forces the LLM to output valid `mermaid.js` graph syntax within Markdown fences, completely eliminating syntax errors (like spaces in node IDs or illegal characters) that small LLMs typically struggle with.

### 5. Study Notes ("Save as notes")

`POST /api/v1/export/pdf` does **not** summarise the transcript. It reads the chat only to
decide what the student was learning, then writes a study module for those topics out of the
NCERT corpus for their class:

1. `notes_service.plan_notes()` extracts 1-3 syllabus topics (and merges near-duplicates —
   "reflex action" and "reflex arc" are one topic).
2. `notes_service.retrieve_topic()` runs the same hybrid retrieval the tutor uses, scoped to
   the books belonging to the student's class, then applies two filters that decide whether
   the notes are on-syllabus at all:
   - **Medium.** Class 8 was ingested in English, Hindi and Punjabi (674 / 770 / 952 chunks),
     and the Devanagari and Gurmukhi PDFs extract as mangled ligatures — so an English chat
     retrieved unreadable source text more often than not. Only English-medium books are used
     (`search_service.medium_of`, `NOTES_SOURCE_MEDIUM`).
   - **Relevance.** A vector search always returns *something*: asked for "Volcanoes" against
     the Class 8 books, which have no volcanoes chapter, it returned the history chapter on
     Shivaji. Every passage is scored by the cross-encoder and dropped below
     `NOTES_RELEVANCE_FLOOR`; when nothing survives the section is written from the class
     syllabus and explicitly *not* attributed to any page.
   Surviving passages are then widened to the neighbouring pages of the same chapter, so a
   section covers the topic the way the chapter does rather than as isolated fragments.
3. `ncert_figure_service` supplies the figure catalogue for those chapters. The model places
   real figures by number rather than inventing image-search queries.
4. Each topic is written as its own LLM call (one call for the whole document capped out at
   the answer model's 2048 tokens) and the sections are concatenated. Providers are tried in
   order — Groq, Gemini 3.7, Gemini 3.6, the chat model — because Groq is fast but limited to
   8,000 tokens/minute on this key, while Gemini has headroom and answers 503 under load. A
   rate-limited Groq is not waited out while another provider is available.
5. `notes_render.render_notes_pdf()` parses the markdown properly — headings, nested lists,
   tables, callouts, LaTeX and chemical formulae — and builds the PDF twice, because the
   footer's "Page X of Y" needs a page count that only exists after the first pass.

**Figures come from the textbook, never from the web.** `ncert_figure_service` opens the
NCERT chapter PDF behind a retrieved passage, finds each "Figure 6.3 …" caption, and crops
the drawing above it. Books present in `data/books/` are used directly; anything else is
fetched from `https://ncert.nic.in/textbook/pdf/<code>.pdf` — the same book, same class.
Crops are cached under `data/figures/<chapter>/`; warm them ahead of time with:

```bash
python -m scripts.prewarm_figures
```

Extraction takes seconds to a minute per chapter, so a request only ever reads the cache
(with a short budget for a cold chapter, after which the section is written without figures).

### 6. Deepgram Voice Agent Integration
The backend serves structured endpoints that the frontend (`LiveTutorButton.tsx`) utilizes during Voice Agent sessions. The frontend executes a `query_knowledge_base` function call to the backend, retrieves the fully formulated NCERT-grounded answer, and injects it into the chat window instantly while the Deepgram Text-to-Speech synthesizes the audio.

## API Endpoints

- `GET /` & `GET /api/v1/health`: Health checks and warmup endpoints to preload embeddings and DB pools into memory on server start.
- `POST /api/v1/chat`: Main conversational endpoint handling text chat, RAG, and UI component generation.
- `POST /api/v1/export/pdf`: Builds the study-notes PDF for the topics in a chat. Takes
  `history`, `student_class`, `language` and `subjects`; returns the PDF, or `422` with a
  sentence for the student when the chat has no academic topic yet.

## Deployment & Setup

Ensure all necessary API keys are populated in the `.env` file:
- `GROQ_API_KEY`
- `GEMINI_API_KEY`
- `TAVILY_API_KEY`
- `COHERE_API_KEY`

Run the server locally using:
```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```
