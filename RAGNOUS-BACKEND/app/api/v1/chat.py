

import os
import asyncio
import asyncpg
import re
import json
import urllib.parse
import urllib.request
import time
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional, Dict
from datetime import datetime
from dotenv import load_dotenv

from langchain_openai import ChatOpenAI
from langchain_groq import ChatGroq
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
from tavily import AsyncTavilyClient

from app.services.youtube_service import find_lesson_videos
from app.services.pyq_service import fetch_pyq
from app.services.embedding_service import (
    get_embedding_model,
    to_pgvector,
    verify_embedding_dim,
)
# The hybrid query and the dedup pass live in search_service so that
# app/evaluation/eval_runner.py measures the exact retrieval production runs.
from app.services.search_service import (
    dedupe_chunks,
    hybrid_search,
    only_medium,
    _parse_student_class,
    _subjects_for_class,
)
from app.services import rerank_service
from app.services import model3d_service
from app.services.llm_fallback import (
    ainvoke_with_fallback,
    build_gemini_chain,
    build_groq_chain,
)

load_dotenv(override=True)

router = APIRouter()

# Initialize embedding model. The model and its dimension live in
# embedding_service so the query path and the ingest scripts cannot drift onto
# different models — which is exactly what had happened.
embedding_model = get_embedding_model()
verify_embedding_dim()

# ============================================================
# NVIDIA NIM API SETUP (COMMENTED OUT TEMPORARILY)
# ============================================================
# NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")
#
# if not NVIDIA_API_KEY:
#     raise RuntimeError(
#         "No NVIDIA API key found. Set NVIDIA_API_KEY in your .env file."
#     )
#
# _nim_ultra = ChatOpenAI(
#     base_url="https://integrate.api.nvidia.com/v1",
#     api_key=NVIDIA_API_KEY,
#     model="nvidia/nemotron-3-ultra-550b-a55b",
#     temperature=0.2,
#     max_tokens=8192,
#     request_timeout=90,
#     max_retries=0
# )
#
# _nim_deepseek = ChatOpenAI(
#     base_url="https://integrate.api.nvidia.com/v1",
#     api_key=NVIDIA_API_KEY,
#     model="deepseek-ai/deepseek-v4-pro",
#     temperature=0.2,
#     max_tokens=8192,
#     request_timeout=60,
#     max_retries=0
# )
#
# _nim_super = ChatOpenAI(
#     base_url="https://integrate.api.nvidia.com/v1",
#     api_key=NVIDIA_API_KEY,
#     model="nvidia/nemotron-3-super-120b-a12b",
#     temperature=0.2,
#     max_tokens=8192,
#     request_timeout=60,
#     max_retries=0
# )
#
# llm = _nim_ultra.with_fallbacks([_nim_deepseek, _nim_super])
# _intent_llm = _nim_super.with_fallbacks([_nim_ultra])

# ============================================================
# GROQ API SETUP
# ============================================================
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# GROQ_API_KEY is no longer strictly required: the fallback chain can run on
# Gemini alone when Groq is unset. The hard check for "at least one provider
# configured" lives below, after both chains have been built.

# Groq retired every Llama chat model; llama-3.3-70b-versatile and
# llama-3.1-8b-instant now 404 on this key, which took Ragnous X1 down entirely.
# `curl https://api.groq.com/openai/v1/models` lists what the key can actually
# reach — check it there before changing these two ids.
#
# The `llm_fallback` helper walks ordered chains built from env-configurable
# model lists so the tutor no longer goes down when one Groq model is
# decommissioned or one Gemini flash is throwing 503s. Ordering:
#   Groq answer:  gpt-oss-120b → qwen3-32b → kimi-k2 → gpt-oss-20b
#   Gemini flash: 3.6 → 3.7 → 3.8
# Override with GROQ_ANSWER_MODELS / GEMINI_ANSWER_MODELS (comma-separated).
groq_answer_chain = build_groq_chain("answer")
gemini_answer_chain = build_gemini_chain("answer")
groq_intent_chain = build_groq_chain("intent")

# The first-choice clients kept under the old names so the endpoint below
# (and notes_service, which imports `gemini_llm`) does not need a rename.
llm = groq_answer_chain[0] if groq_answer_chain else None
gemini_llm = gemini_answer_chain[0] if gemini_answer_chain else None
_intent_llm = groq_intent_chain[0] if groq_intent_chain else None

if llm is None and gemini_llm is None:
    raise RuntimeError(
        "No answer model configured. Set GROQ_API_KEY and/or GEMINI_API_KEY in .env."
    )
if _intent_llm is None:
    # The intent planner is Groq-only historically; if the key is gone the
    # main chain has already raised above.
    _intent_llm = gemini_answer_chain[0] if gemini_answer_chain else None

# Kept for logging / debug prints that used to name the specific model chosen.
GROQ_ANSWER_MODEL = getattr(llm, "model", None) if llm else None
GROQ_INTENT_MODEL = getattr(_intent_llm, "model", None) if _intent_llm else None

# Initialize Tavily Client
tavily_client = AsyncTavilyClient(api_key=os.getenv("TAVILY_API_KEY"))

# Cross-encoder reranking is owned by app/services/rerank_service.py, which
# picks a backend per call: Cohere when COHERE_API_KEY is set, otherwise a
# local cross-encoder. The client is created there, lazily.
# Thresholds now live in RERANK_* / COSINE_* below — the old single pair could
# not serve both score scales and was unreachable anyway (every comparison was
# `or`-ed with a lower hardcoded constant).

DB_POOL = None




# -------- Jatin Bhalla's Work Changes 4 - Date: 2026-07-07 -----------
# Pulled the Mermaid and R3F instruction sets out of the always-on system_prompt
# and into standalone module-level constants. They are now only appended via
# dynamic_instruction when intent.get("flowchart") / intent.get("3d_model") is
# actually True (see chat_endpoint below). Previously both full rule sets were
# injected into EVERY request regardless of intent, which is a lot of dead
# weight for a small model (llama-3.1-8b-instant) to read through and prioritize
# against on turns that need neither. The R3F block itself was also rewritten:
# the old version asked the model to make open-ended aesthetic judgments
# ("does every surface respond to light?", "use your spatial reasoning") across
# a long prose quality bar. Small models are weak at open-ended aesthetic
# judgment and at prioritizing many simultaneous soft rules, so this version
# replaces judgment calls with a fixed template to copy, a background lookup
# table (fixes the "always black with white dots" issue by making background
# a table lookup instead of a decision), and an explicit label-position formula
# instead of "use your spatial reasoning".

MERMAID_BLOCK = """
# FLOWCHART GENERATION (Mermaid.js)

You are drawing a diagram because the topic is a MULTI-STEP process the student
struggles to picture from prose alone. The diagram must actually teach the
process — a decorative three-box chart is worse than no diagram at all.

## What the flowchart is FOR (and what it is NOT)
- It IS for the CAUSAL / SEQUENTIAL structure of a process the student must
  hold in their head: inputs → transformation → outputs, decision branches,
  feedback loops, sub-stages, and where the process can go wrong.
- It IS NOT a summary of the answer, a mind-map of vocabulary, or a bulleted
  list drawn as boxes. If the "process" is only two boxes long, do NOT emit a
  flowchart at all — a sentence is better. Reply with plain text instead.

## NCERT SCOPE — non-negotiable
- Every node, edge label, sub-stage and quantity in the diagram MUST come from
  material taught in the NCERT curriculum for the student's class (or an
  earlier class as revision). No advanced-college terms, no competitive-exam
  detail (Krebs-cycle intermediates for a Class 8 question, Nernst equations
  for a Class 10 question, university-level biochemistry, etc.).
- If unsure whether a step is in the syllabus, omit it. A shorter correct
  diagram beats a longer one that goes beyond the syllabus.

## STRUCTURE (aim for a diagram that genuinely teaches)
- Target 8-18 nodes for a real process. Fewer than 6 means either the topic
  did not need a diagram or you missed sub-stages; more than 20 means the
  student will lose the thread.
- Use SUBGRAPHS to name the stages of a multi-stage process (e.g. "Light
  reactions" and "Calvin cycle" for photosynthesis; "Ingestion", "Digestion",
  "Absorption", "Assimilation", "Egestion" for the alimentary canal).
- Use DECISION DIAMONDS `{"…?"}` wherever the process actually branches
  (e.g. "Is water available?" in transpiration; "Is the temperature above the
  critical value?" in a state change). If the process has no real decision,
  use rectangles only — do not invent branches.
- LABEL EVERY ARROW that carries information (substrate, energy, message,
  cause). "-- 'ATP' -->", "-- 'blocks release' -->", "-- 'yes' -->".
- Show FEEDBACK LOOPS when they exist (e.g. hormonal negative feedback in the
  endocrine system, thermostat-style regulation in body temperature).
- Mark the input(s) with a rounded start node `("…")` and the final output(s)
  with a rounded end node so the student can see where the process begins
  and ends.

## SYNTAX RULEBOOK (violate any of these and the chart fails to render)
1. Start with EXACTLY: `graph TD`  (top-down; do not use LR unless the topic
   is a genuine timeline, in which case use `graph LR`).
2. Node IDs are simple alphanumerics with NO spaces: A, B, S1, D2.
3. Every label is wrapped in double quotes: `A["Photosynthesis begins"]`.
4. NEVER put `()` `[]` `{}` `/` `:` `;` `&` `#` `"` inside a label. Replace with
   words: "and" not "&", "or" not "/", "step 1" not "step (1)".
5. Arrows allowed: `-->`, `-- "text" -->`, `==>`, `-.->`. Nothing else.
6. Shapes: `["text"]` rectangle, `("text")` rounded (use for start/end),
   `{"text?"}` diamond (use for decisions), `[/"text"/]` parallelogram
   (use for inputs/outputs of the whole system).
7. Subgraphs use: `subgraph Stage1 ["Light-dependent reactions"]` … `end`.
8. Styling: `style NodeID fill:#1E293B,stroke:#38BDF8,color:#E2E8F0` for the
   start/end; `style NodeID fill:#0F172A,stroke:#F59E0B,color:#F59E0B` for
   every decision diamond so the student sees the branch points at a glance.

## WORKED EXAMPLE (photosynthesis, Class 10 scope)
```mermaid
graph TD
    Start(["Sunlight, water, CO2 enter the leaf"])
    subgraph LR ["Light-dependent reactions (in the thylakoid)"]
        A["Chlorophyll absorbs sunlight"] --> B["Water molecules split"]
        B -- "releases" --> O2["Oxygen leaves through stomata"]
        B -- "energy" --> C["ATP and NADPH are formed"]
    end
    subgraph CC ["Calvin cycle (in the stroma)"]
        D["CO2 combines with RuBP"] --> E["Series of reactions use ATP and NADPH"]
        E --> F["Glucose is built"]
    end
    Start --> A
    C -- "ATP and NADPH" --> D
    F --> Check{"Enough sunlight and water still available?"}
    Check -- "Yes" --> A
    Check -- "No" --> End(["Photosynthesis pauses"])
    style Start fill:#1E293B,stroke:#38BDF8,color:#E2E8F0
    style End fill:#1E293B,stroke:#38BDF8,color:#E2E8F0
    style Check fill:#0F172A,stroke:#F59E0B,color:#F59E0B
```

## OUTPUT RULES
- Wrap the diagram in a SINGLE ```mermaid … ``` fence. Never split it.
- Place the diagram AFTER the "Direct answer" and "Why it works (mechanism)"
  parts of the response, not before them — the diagram illustrates the
  mechanism you just explained, it does not replace it.
- Do not include the diagram's code in the "Worked example" part; the fence
  itself IS the visual. The "Worked example" part still runs as text.
- If, on reflection, the topic is a single-step fact (definition, one formula
  application, one date), OMIT the flowchart entirely and continue with the
  normal five-part answer. A diagram that adds no information distracts.
"""

WIDGET_3D_BLOCK = """
--- 3D WIDGET GENERATION ---
You ARE capable of rendering interactive 3D models. NEVER apologise or say you cannot display 3D graphics. You display one by emitting the exact token below; the UI intercepts it and renders a real 3D model.

Emit the raw token on its own line. Do NOT wrap it in markdown fences and do NOT write code.

Choose the type by WHAT THE THING PHYSICALLY IS — this is the single most
important decision, and getting it wrong shows the student a meaningless object:

1. MOLECULE — use ONLY for an actual chemical substance: a compound, an element,
   a molecule, or a named protein (water, glucose, methane, benzene, DNA, ethanol).
   A body part is NOT a molecule. An organ is NOT a molecule. A planet, machine,
   animal or building is NOT a molecule.
   [WIDGET_3D_INTENT: {"type": "molecule", "query": "<CHEMICAL NAME>"}]

2. SEARCH — use for any real physical object, in ANY subject:
   - Biology: organs, body parts, cells, organelles, organisms, plant parts
   - Physics: machines and apparatus (motor, generator, transformer, pulley,
     lever, lens, prism, telescope, magnet, turbine, engine, pendulum)
   - Chemistry: apparatus and crystal/lattice structures (NOT single compounds,
     which are type "molecule")
   - Geography: landforms and earth features (volcano, fault, glacier, river,
     mountain, dam), and astronomy (Earth, Moon, planets, satellites, rockets)
   [WIDGET_3D_INTENT: {"type": "search", "query": "<OBJECT NAME>"}]

3. GENERATE — use only for something that does not exist as a real object, e.g.
   an abstract geometric solid or a hypothetical design.
   [WIDGET_3D_INTENT: {"type": "generate", "query": "<OBJECT DESCRIPTION>"}]

Worked routing examples:
- "3D model of the human brain"  -> type "search",   query "human brain"
- "show me a water molecule"     -> type "molecule", query "water"
- "3D model of an atom"          -> type "search",   query "atom"
- "model of the heart"           -> type "search",   query "human heart"
- "structure of glucose"         -> type "molecule", query "glucose"

LABELLING THE MODEL (required for "search" and "generate"):
Add a "labels" array naming the parts a Class __CLASS__ student must be able to
identify on this object. 3 to 6 labels. Each label is:
  {"name": "<NCERT term>", "note": "<what it does, 8-16 words>", "anchor": "<where it sits>"}

The "note" is the part a student actually learns from, so make it teach: say
what the part DOES, or what happens there, in the vocabulary of the Class
__CLASS__ textbook. Write "converts electrical energy into rotation using a
magnetic field", not "part of the motor". Never just repeat the part's name.

"anchor" MUST be built only from these words, joined by hyphens:
  top, bottom, front, back, left, right, center
Examples of valid anchors: "top", "back-bottom", "front-left", "center".
Describe where the part sits on the object as it faces the viewer.

Full example:
[WIDGET_3D_INTENT: {"type": "search", "query": "human brain", "labels": [
{"name": "Cerebrum", "note": "thinking, memory and voluntary action", "anchor": "top-front"},
{"name": "Cerebellum", "note": "balance and muscle coordination", "anchor": "back-bottom"},
{"name": "Brain stem", "note": "controls breathing and heartbeat", "anchor": "bottom"}
]}]
"""

IMAGE_LEGEND_BLOCK = """
--- LABELLED DIAGRAM SUPPORT ---
A labelled educational diagram is being attached to your answer automatically.
After your bullet points, add a final section in EXACTLY this form:

**Parts to identify:**
- **<Component name>** — <what it does, max 12 words>

List every important component a Class {student_class} student must be able to
name in this diagram (between 3 and 6 components). Use the standard NCERT term
for each part. Do not describe the picture itself, and do not mention that an
image was attached.
"""

VIDEO_BLOCK = """
--- VIDEO LESSON ATTACHED ---
A short YouTube lesson on this topic is being attached below your answer
automatically, and the student can play it inside this chat.

1. First re-explain the concept from scratch in the SIMPLEST possible words,
   as if the earlier explanation never happened. Use one everyday analogy.
2. Do NOT repeat your previous wording — the student already did not follow it.
3. Do NOT paste a YouTube link, a URL, or a video title yourself; the player is
   added for you.
4. Finish with exactly one short line inviting them to watch, e.g.
   "Play the video below — it walks through this visually."
"""
# -------- End Work Changes 4 -----------


# ══════════════════════════════════════════════════════════════════════════
#  Jatin Bhalla's Work Changes 6 — video lesson support
#
#  A student who says "I still don't get it" needs a different medium, not a
#  third paraphrase of the same bullet points. Two things trigger a video:
#  an explicit ask ("show me a video"), and a confusion signal. Both are
#  detected with regex BEFORE the lesson-planner LLM sees the turn, because
#  llama-3.1-8b-instant routinely reads "I can't understand" as a plain
#  request to re-explain and returns teaching_aid "none".
# ══════════════════════════════════════════════════════════════════════════

# The student explicitly asked for a video.
_VIDEO_REQUEST_RE = re.compile(
    r"\b(?:"
    r"video|videos|youtube|yt\b|"
    r"lecture|animation|animated (?:video|explanation)|"
    r"visual(?:ly)? (?:explain|show)|"
    r"kisi ?video|koi ?video|video ?dikh|video ?bhej"   # common Hinglish forms
    r")\b",
    re.IGNORECASE,
)

# The student is stuck. Kept deliberately tight — a question that merely
# contains the word "confusing" as subject matter must not trigger this.
_CONFUSION_RE = re.compile(
    r"(?:"
    r"\b(?:i|we)\s+(?:still\s+|really\s+|just\s+)?(?:can(?:no|')?t|cannot|don'?t|do not|couldn'?t)\s+"
    r"(?:seem to\s+)?(?:understand|get|follow|grasp|catch)\b"
    r"|\b(?:not|didn'?t|dont|don'?t)\s+(?:able to\s+)?(?:understand|get) (?:it|this|that|anything)\b"
    r"|\bi'?m\s+(?:so\s+|really\s+|totally\s+)?(?:confused|lost|stuck)\b"
    r"|\b(?:this|it|that)\s+(?:is|was|seems)\s+(?:too\s+)?(?:hard|difficult|confusing|complicated)\s*"
    r"(?:for me|to understand)?\b"
    r"|\bstill\s+(?:not clear|unclear|confusing|confused)\b"
    r"|\b(?:samajh|samjh)\s*(?:nahi|nhi|ni)\b"            # "samajh nahi aaya"
    r"|\bexplain\s+(?:it\s+)?(?:again|once more|differently|in another way)\b"
    r"|\bmakes? no sense\b"
    r")",
    re.IGNORECASE,
)


# Words that carry no subject matter. If nothing else survives in the topic,
# the student named no topic at all and it must come from the previous turn.
_CONTENTLESS_TOPIC_WORDS = {
    "video", "videos", "youtube", "lecture", "animation", "clip", "watch",
    "understand", "understanding", "understood", "confused", "confusing",
    "lost", "stuck", "again", "explain", "explanation", "simpler", "simple",
    "this", "that", "these", "those", "topic", "concept", "chapter", "thing",
    "stuff", "anything", "everything", "nothing", "cannot", "cant", "dont",
    "didnt", "didn", "wasnt", "isnt", "havent",
    "not", "still", "please", "help", "samajh", "nahi", "nhi", "aaya",
    # Bare video-request scaffolding. Without these, "can u provide me video"
    # keeps enough tokens to look like it names a topic, and the search runs
    # on "provide video Class 8 NCERT explained" — which returns whatever
    # random NCERT clip that query happens to rank highest, not the topic the
    # student was just being taught.
    "can", "could", "would", "will", "provide", "give", "send", "share",
    "show", "put", "get", "find", "want", "need", "make", "bring", "fetch",
    "gimme", "gimmie", "pls", "plz", "pleez", "u", "ur", "you",
    "kar", "karo", "kro", "krdo", "de", "dedo", "chahiye", "bhejo", "dikhao",
}


def _video_trigger(query: str) -> Optional[str]:
    """'requested' | 'confused' | None — why a video should be attached."""
    text = query or ""
    if _VIDEO_REQUEST_RE.search(text):
        return "requested"
    if _CONFUSION_RE.search(text):
        return "confused"
    return None


# Topics that are never school work, whatever the planner says. A small model
# will occasionally mark "video of the IPL final" as in-syllabus because the
# rest of the JSON is about school.
# Shared by the non-academic gate, the topic key and the syllabus check.
# The 3D-specific scoring that used to live beside it now belongs to
# app/services/model3d_service.py.
_STOPWORDS = {
    "a", "an", "the", "of", "for", "and", "in", "on", "with", "to",
    "3d", "model", "models", "structure", "diagram", "show", "me",
}


def _tokenize(text: str) -> set:
    """Lowercase word set with stopwords and short noise dropped."""
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 1}


_NON_ACADEMIC_TERMS = {
    "movie", "film", "trailer", "song", "songs", "music", "lyrics", "album",
    "cricket", "ipl", "football", "match", "highlights", "goals", "wwe",
    "game", "gaming", "gameplay", "minecraft", "freefire", "bgmi", "pubg",
    "vlog", "prank", "comedy", "meme", "memes", "cartoon", "anime", "webseries",
    "actor", "actress", "celebrity", "influencer", "tiktok", "reels",
    "recipe", "cooking", "makeup", "fashion", "workout", "gym",
}


def _is_non_academic(topic: str) -> bool:
    return bool(_tokenize(topic) & _NON_ACADEMIC_TERMS)


def _video_syllabus_verdict(
    topic: str,
    class_no: Optional[int],
    planner_verdict: Optional[bool],
    tier: str,
    searched_class_books: bool,
) -> bool:
    """May a video be shown for `topic` to a Class `class_no` student?

    Three signals, in decreasing authority:

      1. An obviously non-academic topic is always refused.
      2. The class's OWN textbooks. If retrieval — already restricted to the
         books belonging to this class — found the topic with medium or high
         confidence, it is in syllabus by definition, and that overrides the
         planner. This is what stops a correct topic being refused because an
         8b model mis-remembered which class teaches it.
      3. Otherwise the planner's per-class judgement, which is the only signal
         available for topics whose book has not been ingested yet.

    An absent planner verdict allows the video: refusing on missing data would
    silently break the feature for every turn where the JSON failed to parse.
    """
    if _is_non_academic(topic):
        print(f"[YT GATE] {topic!r} is not a school subject — refused")
        return False

    if searched_class_books and tier in ("high", "medium"):
        print(f"[YT GATE] {topic!r} found in class {class_no} textbooks — allowed")
        return True

    if planner_verdict is False:
        print(f"[YT GATE] planner says {topic!r} is outside class {class_no} — refused")
        return False

    return True


def _out_of_syllabus_video_reply(topic: str, class_no: Optional[int]) -> str:
    """What the student sees when the video they want is off-syllabus."""
    class_label = f"Class {class_no}" if class_no else "your class"
    subject = topic.strip() or "that topic"
    return (
        f"**Outside your syllabus:** I could not place *{subject}* anywhere in the "
        f"{class_label} NCERT syllabus, so I am not going to pull up a video for it.\n"
        f"- **Why:** I only show lessons that match what {class_label} is actually assessed on, "
        f"so your study time goes to the right material.\n"
        f"- **What I can do:** name any {class_label} chapter or concept and I will find you a "
        f"video for it right away."
    )


# ══════════════════════════════════════════════════════════════════════════
#  Jatin Bhalla's Work Changes 7 — previous-year question insight
#
#  Shown ONCE per topic, on the turn where the student first asks about it.
#  Not on follow-ups, not while they are discussing the answer, and never
#  twice for the same topic — the frontend sends back the topics it has
#  already displayed a card for.
# ══════════════════════════════════════════════════════════════════════════

# An actual question being asked, rather than a reply inside a discussion.
_QUESTION_OPENERS = re.compile(
    r"^\s*(?:what|which|why|how|when|where|who|whose|whom|define|explain|"
    r"describe|state|list|name|give|write|discuss|derive|prove|compare|"
    r"differentiate|distinguish|summari[sz]e|tell me about|teach me|"
    r"kya|kyun|kaise|batao|samjhao)\b",
    re.IGNORECASE,
)

# Short conversational turns that can look like questions but carry no topic.
_CHITCHAT = re.compile(
    r"^\s*(?:hi|hey|hello|yo|thanks|thank you|ok(?:ay)?|got it|cool|nice|"
    r"great|good|yes|no|yeah|nope|sure|hmm+|bye|thik hai|theek hai|accha)"
    r"[\s!.?]*$",
    re.IGNORECASE,
)


# A short question that leans on a pronoun is asking about the answer already
# on screen ("who led it?", "why did they do that?"), not opening a new topic.
_PRONOUN_FOLLOWUP = re.compile(
    r"\b(?:it|its|this|that|these|those|they|them|their|he|she|him|her|his|there)\b",
    re.IGNORECASE,
)


def _is_topic_question(query: str) -> bool:
    """True when this turn is a fresh question about a topic."""
    text = (query or "").strip()
    if len(text) < 8 or _CHITCHAT.match(text):
        return False

    # Guards the "show it once" rule independently of the planner: a follow-up
    # can be resolved to a differently-worded topic, which would slip past the
    # already-shown list and put a second card in the middle of a discussion.
    if len(text.split()) <= 6 and _PRONOUN_FOLLOWUP.search(text):
        return False
    # A question mark alone is not enough ("really?"), and its absence is not
    # disqualifying — students type "explain photosynthesis" far more often
    # than they punctuate.
    return bool(_QUESTION_OPENERS.match(text) or ("?" in text and len(text.split()) >= 3))


def _topic_key(topic: str) -> str:
    """Comparison key so 'the Non-Cooperation Movement' and 'non cooperation
    movement' are recognised as the same topic already shown."""
    return " ".join(sorted(_tokenize(topic)))


def _last_assistant_topic(history: List[dict]) -> str:
    """Heading of the previous answer, used as the topic when the student
    only says "I don't understand" and names no subject at all."""
    for turn in reversed(history or []):
        if turn.get("role") != "assistant":
            continue
        text = (turn.get("content") or "").strip()
        if not text:
            continue
        # Prefer the first **bold heading**, else the opening sentence.
        heading = re.search(r"\*\*(.+?)\*\*", text)
        if heading:
            # Headings arrive as "**Human Eye:**"; the colon would end up in
            # the YouTube query.
            return heading.group(1).strip().strip(":—-").strip()[:80]
        return re.split(r"[.!?\n]", text)[0].strip()[:80]
    return ""
# -------- End Work Changes 6 -----------


# ══════════════════════════════════════════════════════════════════════════
#  Jatin Bhalla's Work Changes 5 — retrieval, confidence & tool routing
#
#  Fixes four separate defects found while auditing the RAG path:
#    1. the class filter matched the wrong column and silently returned
#       0 books for classes 9/11/12 and the wrong single book for class 10;
#    2. the keyword half of the "hybrid" search never matched anything, so
#       search was pure vector;
#    3. confidence thresholds were dead code and mixed two incompatible
#       score scales, so "NCERT Verified" was shown for any hit at all;
#    4. the 3D widget type came straight from the LLM with no validation,
#       so an organ could be routed to the molecule renderer.
# ══════════════════════════════════════════════════════════════════════════

# ── Class resolution ──────────────────────────────────────────────────────
# The `subject` column was populated from filenames, so it holds two totally
# different naming schemes: NCERT file codes ('jesc1dd', 'iest1dd') and
# hand-typed names ('class 10 science', '8th_science_english'). The old query
# did `subject ILIKE '%10th%'`, which matches neither scheme reliably:
# classes 9/11/12 matched nothing and class 10 matched only the one book whose
# name happened to contain the literal text "10th". Both schemes are parsed
# here instead, and the resolved subject list is passed to SQL explicitly.

# Class resolution lives in app/services/search_service.py so that
# app/evaluation/eval_runner.py can apply the same class filter production
# does — an eval that searches the whole corpus measures a path real
# requests never take.

# ── Keyword query building ────────────────────────────────────────────────
# websearch_to_tsquery() ANDs every unquoted term, so feeding it the expanded
# multi-word query required ALL lexemes to appear in one chunk. Measured on the
# live index: the expanded query matched 0 rows while a single keyword matched
# 112. The keyword half of the hybrid search was dead. An OR query is built
# here so it actually contributes ranking signal.

_TS_STOPWORDS = {
    "what", "which", "when", "where", "who", "why", "how", "does", "did", "do",
    "is", "are", "was", "were", "the", "a", "an", "of", "for", "and", "or",
    "in", "on", "with", "to", "from", "by", "as", "at", "it", "its", "this",
    "that", "these", "those", "me", "my", "you", "your", "explain", "tell",
    "give", "show", "describe", "define", "please", "can", "will", "would",
    "about", "some", "more", "using", "into", "than", "then", "them",
    "their", "there", "here", "also", "any", "all", "get", "got", "make",
    "made", "want", "need", "know", "much", "many", "such", "very", "only",
}


def _build_or_tsquery(text: str, limit: int = 12) -> str:
    """OR-joined tsquery string, e.g. 'photosynthesis | chlorophyll'."""
    words = re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}", (text or "").lower())
    seen, terms = set(), []
    for w in words:
        w = w.strip("-")
        if w in _TS_STOPWORDS or w in seen or len(w) < 3:
            continue
        seen.add(w)
        terms.append(w)
        if len(terms) >= limit:
            break
    return " | ".join(terms)


# Language of the books retrieval may read from (the notes pipeline uses the
# same default via NOTES_SOURCE_MEDIUM). Not the language the tutor replies in.
SOURCE_MEDIUM = os.getenv("CHAT_SOURCE_MEDIUM", "en")


# ── Confidence tiering ────────────────────────────────────────────────────
# Two different scales reach this code: Cohere rerank relevance (when the
# reranker runs) and raw cosine similarity (when it doesn't). They are
# distributed very differently, so a single threshold pair cannot serve both —
# the old code compared whichever one it happened to have against the same
# constants, and those constants were themselves unreachable because each
# check was `or`-ed with a hardcoded lower number.

RERANK_HIGH = float(os.getenv("RAG_RERANK_HIGH", "0.45"))
RERANK_MEDIUM = float(os.getenv("RAG_RERANK_MEDIUM", "0.12"))
COSINE_HIGH = float(os.getenv("RAG_COSINE_HIGH", "0.55"))
COSINE_MEDIUM = float(os.getenv("RAG_COSINE_MEDIUM", "0.35"))


def _confidence_tier(score: float, kind: str) -> str:
    """'high' | 'medium' | 'low' for a score on the given scale."""
    if score is None:
        return "low"
    high, medium = (
        (RERANK_HIGH, RERANK_MEDIUM) if kind == "rerank" else (COSINE_HIGH, COSINE_MEDIUM)
    )
    if score >= high:
        return "high"
    if score >= medium:
        return "medium"
    return "low"


# Tier -> the badge the student sees. All three modes are reachable now;
# previously "extended_reference" could never be produced and every retrieval
# hit, however weak, was labelled "NCERT Verified".
_TIER_TO_MODE = {
    "high": "ncert_verified",
    "medium": "extended_reference",
    "low": "ai_knowledge",
}


def _confidence_percent(score: float, kind: str, tier: str) -> Optional[float]:
    """Map a raw score to a percentage that agrees with the badge shown.

    The old formula was `30 + score * 140`, which saturated at 99% for any
    cosine above 0.5 and reported a confident-looking number for weak hits.
    Each tier now occupies its own band, so the number and the badge can
    never disagree.
    """
    if score is None:
        return None

    if kind == "rerank":
        high, medium = RERANK_HIGH, RERANK_MEDIUM
    else:
        high, medium = COSINE_HIGH, COSINE_MEDIUM

    if tier == "high":
        span = max(1e-6, 1.0 - high)
        pct = 82.0 + 15.0 * min(1.0, (score - high) / span)
    elif tier == "medium":
        span = max(1e-6, high - medium)
        pct = 58.0 + 22.0 * ((score - medium) / span)
    else:
        span = max(1e-6, medium)
        pct = 30.0 + 25.0 * max(0.0, min(1.0, score / span))

    return round(max(30.0, min(97.0, pct)), 1)


# ── 3D routing validation ─────────────────────────────────────────────────
# The widget type used to be taken straight from the LLM. A small model
# routed "human brain" to the molecule renderer, PubChem resolved the word to
# an unrelated compound, and the student was shown a meaningless wireframe.
# The type is now validated against what the object actually is.

_NON_MOLECULE_TERMS = {
    # anatomy / organs
    "brain", "heart", "lung", "lungs", "kidney", "liver", "stomach", "eye",
    "ear", "nose", "tongue", "skin", "bone", "bones", "skeleton", "skull",
    "muscle", "neuron", "nerve", "spine", "tooth", "teeth", "intestine",
    "body", "hand", "leg", "arm", "organ", "cell", "tissue",
    # organisms
    "plant", "leaf", "flower", "root", "tree", "animal", "human", "insect",
    "fish", "bird", "frog", "butterfly",
    # physics / astronomy / objects
    "atom", "planet", "earth", "moon", "sun", "star", "galaxy", "solar",
    "rover", "satellite", "rocket", "telescope", "microscope", "engine",
    "motor", "machine", "pump", "lever", "pulley", "circuit", "magnet",
    "volcano", "mountain", "river", "building", "monument", "fort", "temple",
}

_MOLECULE_HINTS = {
    "molecule", "compound", "chemical", "formula", "structure", "bond",
    "protein", "acid", "base", "salt", "oxide", "polymer", "isomer",
}


def _validate_widget_type(wtype: str, query: str) -> str:
    """Correct an implausible 3D widget type before it reaches a renderer."""
    tokens = set(re.findall(r"[a-z]+", (query or "").lower()))

    if wtype == "molecule":
        # An organ/organism/object is not a chemical, whatever the LLM said.
        if tokens & _NON_MOLECULE_TERMS and not (tokens & _MOLECULE_HINTS):
            print(
                f"[3D ROUTE] corrected molecule -> search for {query!r} "
                f"(matched physical-object term)"
            )
            return "search"
    return wtype


# ── Curriculum gate for images ────────────────────────────────────────────
# Images are restricted to Class 8-12 academic subjects and biased towards
# labelled diagrams rather than decorative photographs.

CURRICULUM_SUBJECTS = {
    "science", "physics", "chemistry", "biology", "mathematics", "maths",
    "math", "geography", "history", "civics", "political science",
    "economics", "social science", "environmental science", "evs",
    "computer science", "biotechnology",
}


def _is_curriculum_subject(subject_area: str) -> bool:
    if not subject_area:
        return False
    s = subject_area.strip().lower()
    return any(allowed in s or s in allowed for allowed in CURRICULUM_SUBJECTS)


def _extract_widget_intent(content: str):
    """Pull the [WIDGET_3D_INTENT: {...}] token out of a reply.

    Returns (payload, start, end); payload is None when there is no usable
    token. `start`/`end` bound the whole token so it can be cut from the text.

    A regex cannot do this safely. The token carries a "labels" array of
    nested objects and the model wraps it across lines, so the previous
    `{.*?}` pattern (non-greedy, and `.` never matching a newline) latched
    onto the LAST label object and its closing bracket instead of the real
    payload — which both left a stray "}]" in the visible answer and fed
    json.loads an object with no "type", so no widget was ever built.
    Braces are balanced explicitly here, ignoring any inside strings.
    """
    marker = "[WIDGET_3D_INTENT:"
    start = content.find(marker)
    if start == -1:
        return None, -1, -1

    open_idx = content.find("{", start)
    if open_idx == -1:
        return None, -1, -1

    depth = 0
    in_string = False
    escaped = False

    for i in range(open_idx, len(content)):
        ch = content[i]

        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                payload_str = content[open_idx:i + 1]
                # Swallow the token's own closing bracket so it can't survive
                # in the visible text.
                end = i + 1
                while end < len(content) and content[end].isspace():
                    end += 1
                if end < len(content) and content[end] == "]":
                    end += 1
                try:
                    return json.loads(payload_str), start, end
                except json.JSONDecodeError as e:
                    print(f"[3D ROUTE] malformed widget payload: {e}")
                    # Still report the span so the raw token gets stripped
                    # rather than shown to the student.
                    return None, start, end

    return None, -1, -1


def _build_diagram_query(topic: str, subject_area: str, class_no: Optional[int]) -> str:
    """Search string biased towards labelled NCERT-style teaching diagrams."""
    parts = [topic.strip(), "labelled diagram"]
    if subject_area:
        parts.append(subject_area.strip())
    parts.append("NCERT")
    if class_no:
        # Always pin to the student's exact class so we never pull a diagram
        # from a different grade's textbook.
        parts.append(f"class {class_no}")
    parts.append("textbook educational")
    return " ".join(p for p in parts if p)


# Topics/keywords where a labelled diagram genuinely adds value over text alone.
# Anything NOT in this set should be answered with text only — no image fetch.
_DIAGRAM_KEYWORDS = {
    # Biology / Life Science
    "cell", "cells", "nucleus", "mitochondria", "chloroplast", "membrane",
    "heart", "brain", "kidney", "liver", "lung", "lungs", "eye", "ear",
    "neuron", "nerve", "skeleton", "bone", "muscle", "stomach", "intestine",
    "flower", "leaf", "root", "stem", "seed", "germination", "photosynthesis",
    "respiration", "digestion", "reproduction", "chromosome", "dna",
    "amoeba", "paramecium", "euglena", "hydra", "frog", "earthworm",
    # Chemistry
    "atom", "molecule", "bond", "structure", "compound", "crystal",
    "reaction", "electrolysis", "cell battery", "electrode",
    "periodic table", "element", "orbital",
    # Physics
    "circuit", "magnet", "lens", "mirror", "prism", "wave", "reflection",
    "refraction", "diffraction", "force", "pulley", "lever", "gear",
    "electromagnet", "motor", "generator", "transformer",
    "eclipse", "solar", "lunar", "planet", "orbit", "satellite",
    # Geography / Earth Science
    "map", "river", "mountain", "volcano", "earthquake", "fault",
    "soil", "rock", "layers", "atmosphere", "weather", "climate",
    "water cycle", "carbon cycle", "nitrogen cycle",
    # General visual subjects
    "diagram", "cross section", "cross-section", "structure",
    "anatomy", "labelled", "labeled",
}


def _genuinely_needs_diagram(topic: str, subject_area: str) -> bool:
    """Return True ONLY when a labelled diagram would meaningfully help the
    student understand — i.e., the topic has a visual/spatial component.

    History facts, definitions, poems, dates, formulas without structure, and
    general explanations do NOT need an image. This keeps Tavily calls rare.
    """
    tokens = set(re.findall(r"[a-z]{3,}", (topic + " " + subject_area).lower()))
    return bool(tokens & _DIAGRAM_KEYWORDS)
# -------- End Work Changes 5 -----------


async def get_db_pool():
    global DB_POOL
    if DB_POOL is None:
        db_url = os.getenv("DATABASE_URL")
        if not db_url:
            return None
        if db_url.startswith("postgresql+asyncpg://"):
            db_url = db_url.replace("+asyncpg", "")
        try:
            DB_POOL = await asyncpg.create_pool(
                db_url,
                min_size=1,
                max_size=3,
                command_timeout=5
            )
        except Exception as e:
            print(f"[WARN] Failed to connect to DB pool: {e}")
            return None
    return DB_POOL


class Attachment(BaseModel):
    """
    One file the student attached to this turn, already read by
    /api/v1/attachments. The browser holds the parsed result and sends it back
    here — this backend stores nothing between requests.
    """
    name: str = "file"
    mime: str = ""
    kind: str = "document"          # "image" | "document"
    size: int = 0
    text: str = ""
    # Images always carry one. Documents only do when their text could not be
    # extracted (a scan) and the file itself has to be shown to a vision model.
    data_url: str = ""
    page_count: Optional[int] = None
    truncated: bool = False
    note: str = ""


class ChatRequest(BaseModel):
    query: str = ""
    language: str = "English"
    student_class: str = "8th"
    model: str = "ragnous_x1"
    history: List[dict] = []  # [{role: "user"|"assistant", content: "..."}]
    # Topics this session has already shown a previous-year-questions card for.
    # The backend is stateless, so the client is the only thing that knows what
    # the student has already been told; without it the card would reappear on
    # every turn about the same chapter.
    pyq_topics_seen: List[str] = []
    # Files attached to THIS turn only. Earlier turns' files survive as a short
    # "[Attached: …]" marker the client appends to the history text — replaying
    # every past image on every turn would be ruinous for latency and cost.
    attachments: List[Attachment] = []


# Total extracted text all attachments on one turn may contribute. Each file is
# already capped at 30k chars by file_service; this caps the sum, so six long
# PDFs cannot crowd the retrieved NCERT context out of the prompt.
MAX_ATTACHMENT_CONTEXT_CHARS = 48_000


def _attachment_label(att: "Attachment") -> str:
    """How one file is named in prompts and log lines."""
    if att.kind == "image":
        return f"image “{att.name}”"
    if att.page_count:
        return f"“{att.name}” ({att.page_count} page{'s' if att.page_count != 1 else ''})"
    return f"“{att.name}”"


def _attachment_context(attachments: List["Attachment"]) -> str:
    """
    The text block describing every attached file, prepended to the student's
    question. Files with no extractable text (images, scans) are still listed —
    the model needs to know they exist and are coming as pictures, otherwise it
    answers as if the student sent nothing.
    """
    if not attachments:
        return ""

    blocks: List[str] = []
    budget = MAX_ATTACHMENT_CONTEXT_CHARS

    for att in attachments:
        header = f"[FILE: {att.name}]"
        if att.kind == "image":
            blocks.append(f"{header}\n(An image. It is attached to this message — look at it.)")
            continue
        if not att.text:
            reason = att.note or "No selectable text; the pages are attached as images."
            blocks.append(f"{header}\n({reason})")
            continue

        body = att.text[:budget]
        budget -= len(body)
        if att.truncated or len(body) < len(att.text):
            body += "\n[… the rest of this file was too long to include …]"
        blocks.append(f"{header}\n{body}")
        if budget <= 0:
            break

    joined = "\n\n".join(blocks)
    return (
        "=== FILES THE STUDENT ATTACHED ===\n"
        f"{joined}\n"
        "=== END OF ATTACHED FILES ===\n\n"
    )


def _attachment_media_parts(attachments: List["Attachment"]) -> List[dict]:
    """
    The image/PDF parts a vision model receives alongside the text. Only files
    that actually carry bytes produce one — a PDF whose text extracted cleanly
    is already in the text block and does not need to be sent twice.
    """
    parts: List[dict] = []
    for att in attachments:
        if not att.data_url or not att.data_url.startswith("data:"):
            continue
        header, _, payload = att.data_url.partition(",")
        if not payload:
            continue
        mime = header[5:].split(";")[0] or "application/octet-stream"
        if mime.startswith("image/"):
            parts.append({"type": "image_url", "image_url": {"url": att.data_url}})
        else:
            parts.append({"type": "media", "mime_type": mime, "data": payload})
    return parts


def _attachment_query_seed(attachments: List["Attachment"]) -> str:
    """
    What to search NCERT for when the student attached a file and typed nothing.

    Retrieval, the lesson planner and the exam-history lookup all key off the
    question text, so an empty one would send the whole turn down the
    "conversational, no topic" path. The first lines of the document name the
    topic well enough to keep those stages useful; for images there is nothing
    to read yet, so the filename is all we have.
    """
    names = ", ".join(a.name for a in attachments[:3]) or "the attached file"
    snippet = ""
    for att in attachments:
        if att.text:
            snippet = " ".join(att.text.split())[:300]
            break
    if snippet:
        return f"Explain and solve what is in the attached file ({names}): {snippet}"
    return f"Explain and solve the question in the attached file ({names})."


class IntentParser(BaseModel):
    flowchart: bool = Field(description="Set to true ONLY if user asks to draw or show a mermaid flowchart/diagram of a process.")
    image: bool = Field(description="Set to true ONLY if the user asks for a real photograph or web image of something.")
    notes: bool = Field(description="Set to true ONLY if the user explicitly asks for study notes, to generate a pdf, or to download notes.")
    widget_3d: bool = Field(..., alias="3d_model")


class Citation(BaseModel):
    chapter: str
    page: int
    source: str


class ChatResponse(BaseModel):
    id: str
    role: str
    content: str
    confidenceMode: str
    confidenceScore: Optional[float] = None
    citations: List[Citation]
    artifact: Optional[Dict] = None
    # Separate from `artifact` on purpose: the schema carries one artifact, and
    # exam-question insight must not cost the student their diagram or video.
    pyq: Optional[Dict] = None
    createdAt: str


@router.get("/warmup")
@router.post("/warmup")
async def warmup_endpoint():
    """Warm up PyTorch embedding model & DB pool for zero first-request latency."""
    start_time = datetime.now()
    
    # 1. Warm up vector encoder + cross-encoder
    model_warmed = False
    try:
        await asyncio.to_thread(embedding_model.encode, "NCERT Warmup Test Query")
        # The local cross-encoder is loaded lazily on first use; without this
        # the first real request pays for the weights load.
        await asyncio.to_thread(rerank_service.warmup)
        model_warmed = True
    except Exception as e:
        print(f"[WARMUP WARN] Vector model warmup exception: {e}")

    # 2. Warm up DB connection pool
    db_warmed = False
    try:
        pool = await get_db_pool()
        if pool:
            async with pool.acquire() as conn:
                await conn.fetchval("SELECT 1;")
            db_warmed = True

    except Exception as e:
        print(f"[WARMUP WARN] DB pool warmup exception: {e}")

    elapsed_ms = round((datetime.now() - start_time).total_seconds() * 1000, 2)
    print(f"[BACKEND WARMUP] Model: {model_warmed}, DB: {db_warmed}, Latency: {elapsed_ms}ms")

    return {
        "status": "ready",
        "model_warmed": model_warmed,
        "db_warmed": db_warmed,
        "latency_ms": elapsed_ms
    }


@router.post("", response_model=ChatResponse)
async def chat_endpoint(req: ChatRequest):

    try:
        # 0a. Attachments.
        #
        # Read once, up front, because everything downstream keys off the
        # question text: an attach-only turn ("here, solve this") arrives with
        # req.query empty, and retrieval, the lesson planner and the exam-history
        # lookup would all see a blank question. Seeding req.query from the file
        # keeps the whole existing pipeline working unchanged.
        attachments = req.attachments[:6]
        attachment_context = _attachment_context(attachments)
        media_parts = _attachment_media_parts(attachments)

        if attachments and not req.query.strip():
            req.query = _attachment_query_seed(attachments)

        if attachments:
            print(
                f"[ATTACH] {len(attachments)} file(s): "
                + ", ".join(_attachment_label(a) for a in attachments)
                + f" | media_parts={len(media_parts)}"
            )

        # 0. Teaching-plan classification & semantic query expansion.
        #
        # This used to ask for three independent booleans, so the model could
        # set several at once (or none) and nothing decided which aid actually
        # helped the student most. It now makes ONE pedagogical choice — what
        # is the best way to teach THIS question — and reports the subject and
        # the concrete topic the aid should depict.
        student_class_no = _parse_student_class(req.student_class)

        intent_prompt = f"""You are the lesson planner for an NCERT tutor for Indian school students (Class 8-12).

Student's class: {req.student_class}
Student's question: "{req.query}"

Decide the single BEST way to teach this specific question, then reply with ONLY a valid JSON object, nothing else:
{{"teaching_aid": "none"|"flowchart"|"image"|"3d"|"video", "aid_topic": string, "subject_area": string, "needs_notes": boolean, "in_syllabus": boolean, "expanded_query": string}}

How to choose teaching_aid — pick the ONE that genuinely helps most:
- "flowchart": choose this when the topic is a MULTI-STEP process, cycle, pathway, mechanism, classification tree, cause-effect chain, life-cycle, historical timeline of causes/consequences, or a decision procedure — AND the topic is in NCERT for Class {req.student_class} (or an earlier class). Fire it in BOTH cases:
    (a) SEMANTIC cue — the topic itself is a process. Examples: photosynthesis stages, respiration pathway, digestion, water cycle, nitrogen cycle, mitosis/meiosis phases, reflex arc, how a bill becomes law, how a cyclone forms, extraction of a metal from its ore, working of a nuclear reactor, journey of food through the alimentary canal, formation of soil, Non-Cooperation Movement's cause-and-effect chain, the process of natural selection.
    (b) SENTIMENT cue — the student's wording shows they need the steps unpacked: "how does X work", "what are the steps", "walk me through", "step by step", "explain the process", "in what order", "kaise hota hai", "samajh nahi aaya", "confused about the steps", or asks the same process-topic a second time. Fire even if they did not say "flowchart".
  Do NOT fire "flowchart" for pure definitions, single-fact recall, one-shot numerical problems, historical dates, poem/prose analysis, or anything outside NCERT for this class.
- "image": the student must recognise the real appearance of something, or must learn to NAME the parts of a structure (cell diagram, parts of a flower, human eye, map features).
- "3d": the answer depends on understanding shape, spatial arrangement or volume that a flat picture cannot convey. This applies across every subject — organs and cells (Biology), machines, lenses and apparatus (Physics), crystal and molecular geometry (Chemistry), landforms, earth structure and astronomy (Geography).
- "video": the student asked to watch or be shown a video/lecture, OR says they did not understand, are confused or stuck, and need the topic taught again in another medium.
- "none": the answer is definitional, numerical, historical or conversational, and a visual would add nothing. Prefer "none" when in doubt — an unnecessary visual distracts.

When BOTH "flowchart" and "video" would fit, pick "flowchart" — the diagram teaches the process on-page; the video is a fallback for another turn.

Other fields:
- aid_topic: the exact NCERT topic this question is about, as a short noun phrase ("human heart", "water cycle", "Non-Cooperation Movement", "glucose molecule"). Fill this in even when teaching_aid is "none" — it names the topic, not the picture. Use the formal syllabus term, never the student's abbreviation.
- subject_area: the school subject this belongs to (Physics, Chemistry, Biology, Science, Mathematics, Geography, History, Civics, Economics). Use "other" if it is not a school subject.
- needs_notes: true ONLY if the student explicitly asked for study notes or a PDF.
- in_syllabus: true if aid_topic is taught anywhere in the NCERT syllabus for Class {req.student_class} or in the classes BELOW it (revision of an earlier class is fine). false if it belongs only to a HIGHER class, to a college/competitive-exam course, or is not a school subject at all (films, songs, cricket, games, celebrities, gadgets). Judge the topic, not the wording. Examples for Class 8: photosynthesis -> true, friction -> true, integration by parts -> false, organic reaction mechanisms -> false, IPL highlights -> false.
- expanded_query: the student's question rewritten with the formal NCERT terms it implies ("ncp" -> "Non-Cooperation Movement", "plants make food" -> "photosynthesis"). Keep the original wording and append the formal terms.
"""
        try:
            intent_res = await ainvoke_with_fallback(
                groq_intent_chain or gemini_answer_chain,
                [HumanMessage(content=intent_prompt)],
                label="INTENT",
            )
            intent_str = intent_res.content.replace('```json', '').replace('```', '').strip()
            intent = json.loads(intent_str)
            semantic_query = intent.get("expanded_query") or req.query
        except Exception as e:
            print(f"[WARN] Intent parsing/expansion failed: {e}")
            intent = {}
            semantic_query = req.query

        teaching_aid = (intent.get("teaching_aid") or "none").strip().lower()
        if teaching_aid not in ("none", "flowchart", "image", "3d", "video"):
            teaching_aid = "none"
        aid_topic = (intent.get("aid_topic") or "").strip() or req.query
        subject_area = (intent.get("subject_area") or "").strip()
        needs_notes = bool(intent.get("needs_notes"))
        # None = the planner did not answer (bad JSON, failed call). Absence of
        # a verdict must not be read as "out of syllabus".
        raw_in_syllabus = intent.get("in_syllabus")
        intent_in_syllabus = None if raw_in_syllabus is None else bool(raw_in_syllabus)

        # An explicit "show me a video" or a confusion signal overrides the
        # planner. The 8b planner reads "I can't understand this" as a plain
        # re-explain request and answers "none", which is exactly the turn where
        # the student most needs a different medium.
        video_trigger = _video_trigger(req.query)
        if video_trigger and not needs_notes:
            if teaching_aid != "video":
                print(f"[AID] forcing video — trigger={video_trigger}")
            teaching_aid = "video"

        # "I don't get it" names no topic, so the aid_topic the planner echoed
        # back is the confusion phrase itself. Carry the topic over from the
        # previous answer instead, or searching YouTube is pointless.
        if teaching_aid == "video":
            # Carry over ONLY when the topic has no subject matter left in it.
            # Requiring two content words was wrong: "friction" is a complete
            # topic on its own, and it was being thrown away in favour of
            # whatever the previous answer happened to be about.
            if not (_tokenize(aid_topic) - _CONTENTLESS_TOPIC_WORDS):
                carried = _last_assistant_topic(req.history[:-1])
                if carried:
                    print(f"[AID] video topic carried from previous turn: {carried!r}")
                    aid_topic = carried

        # Images are restricted to Class 8-12 academic subjects. An off-syllabus
        # or non-academic topic gets the explanation without a picture.
        if teaching_aid == "image" and not _is_curriculum_subject(subject_area):
            print(
                f"[AID] image suppressed — subject_area {subject_area!r} is "
                f"not a Class 8-12 curriculum subject"
            )
            teaching_aid = "none"

        print(
            f"[AID] query={req.query!r} -> aid={teaching_aid} topic={aid_topic!r} "
            f"subject={subject_area!r} class={student_class_no}"
        )

        # Downstream flags derive from the single decision above.
        intent = {
            "image": teaching_aid == "image",
            "flowchart": teaching_aid == "flowchart",
            "3d_model": teaching_aid == "3d",
            "video": teaching_aid == "video",
            "notes": needs_notes,
        }

        # The video search is NOT started here. A video may only be shown for a
        # topic inside this student's own syllabus, and that is decided below,
        # once retrieval has had a chance to look for the topic in this class's
        # own textbooks. It still runs concurrently with generation.
        video_task = None
        video_in_syllabus = intent_in_syllabus

        # ── Previous-year questions ───────────────────────────────────────
        # Fires only on the turn where the student first ASKS about a topic —
        # not while they are talking through the answer, and never twice for
        # the same topic. Started here so the web search and the extraction run
        # against the clock of the main answer rather than after it.
        pyq_task = None
        # `intent` has been replaced by the flag dict above, so the planner's
        # topic comes from aid_topic — which falls back to the raw query when
        # the planner gave nothing usable.
        pyq_task_topic = aid_topic if aid_topic != req.query else semantic_query
        pyq_topic = pyq_task_topic.strip()
        if (
            _is_topic_question(req.query)
            and not video_trigger
            and not needs_notes
            and intent_in_syllabus is not False
            and not _is_non_academic(pyq_topic)
        ):
            already_shown = {_topic_key(t) for t in (req.pyq_topics_seen or [])}
            if _topic_key(pyq_topic) in already_shown:
                print(f"[PYQ] {pyq_topic!r} already shown this session; skipping")
            else:
                print(f"[PYQ] looking up exam history for {pyq_topic!r}")
                pyq_task = asyncio.create_task(
                    fetch_pyq(
                        tavily_client,
                        pyq_topic,
                        subject_area,
                        student_class_no,
                    )
                )

        # "I still don't get it" has no retrievable content of its own — embedding
        # it searches the textbook for the phrase, not for the concept. Retrieve
        # against the topic carried over from the previous answer instead.
        if video_trigger == "confused" and aid_topic and aid_topic != req.query:
            semantic_query = aid_topic
            print(f"[RAG] confusion turn; retrieving against {aid_topic!r}")

        # 1. Embed the query (using semantic_query for vector accuracy)
        query_vector = await asyncio.to_thread(embedding_model.encode, semantic_query)
        query_vector = query_vector.tolist()

        vector_str = to_pgvector(query_vector)
        ts_query = _build_or_tsquery(semantic_query)

        rows = []
        # True only while the results in `rows` came from this class's own
        # books — the syllabus gate below may not treat a global-corpus hit as
        # proof that the topic belongs to this class.
        searched_class_books = False
        try:
            pool = await get_db_pool()
            if pool:
                async with pool.acquire() as conn:
                    # Resolve the student's class to the actual `subject` values
                    # that belong to it. The old `subject ILIKE '%10th%'` filter
                    # matched 0 books for classes 9/11/12 and, for class 10,
                    # matched only the one book with "10th" in its name — so a
                    # Class 10 science question was filtered to a history book.
                    # English-medium books only: Class 8 is also ingested in
                    # Hindi and Punjabi, whose PDFs extract as mangled
                    # ligatures. The answer is still written in the student's
                    # chosen language — only the source text is English.
                    class_subjects = await _subjects_for_class(
                        conn, student_class_no, SOURCE_MEDIUM
                    )
                    if class_subjects:
                        print(
                            f"[RAG] class {student_class_no}: restricting to "
                            f"{len(class_subjects)} book(s)"
                        )
                    else:
                        print(
                            f"[RAG] no books mapped to class {student_class_no!r}; "
                            f"searching the whole corpus"
                        )

                    rows = await hybrid_search(
                        conn, vector_str, ts_query, class_subjects or None
                    )
                    searched_class_books = bool(class_subjects and rows)

                    # Widen to the whole corpus if the class had nothing useful.
                    if not rows and class_subjects:
                        print("[RAG] class-filtered search empty; retrying globally")
                        rows = only_medium(
                            await hybrid_search(conn, vector_str, ts_query, None),
                            SOURCE_MEDIUM,
                        )
                        searched_class_books = False
        except Exception as db_err:
            print(f"[WARN] Database connection or query failed: {db_err}. Defaulting to AI Brain & Tavily Web Search.")
            rows = []

        if rows:
            deduped = dedupe_chunks(rows, limit=10)
            if len(deduped) != len(rows):
                print(f"[RAG] deduplicated {len(rows)} -> {len(deduped)} chunks")
            rows = deduped


        context_parts = []
        citations = []
        best_score = 0.0
        # Which scale best_score is on — cross-encoder relevance (Cohere or the
        # local model, both 0-1) and raw cosine are distributed differently and
        # must not share thresholds.
        score_kind = "cosine"
        # (score, row) for every chunk kept, so citations can be filtered by
        # the relevance of the individual chunk rather than emitted blindly.
        scored_chunks = []

        if not rows:
            context = "No relevant context found in the textbook vector database."
        else:
            docs = [row['content'] for row in rows]

            # 3. Cross-encoder reranking. rerank_service tries Cohere first and
            # falls back to a local cross-encoder, so the raw-cosine path below
            # is now reached only if both are unavailable.
            #
            # -------- Jatin Bhalla's Work Changes 3 - Date: 2026-07-06 -----------
            # Both backends are synchronous — calling them directly here blocked
            # the event loop for the whole round-trip. Offloaded to a thread so
            # other work (the intent/Tavily tasks) proceeds meanwhile.
            reranked, rerank_backend = await asyncio.to_thread(
                rerank_service.rerank, semantic_query, docs, 3
            )
            # -------- End Work Changes 3 -----------

            if reranked:
                # Cohere relevance and the local cross-encoder's squashed logit
                # are both 0-1 relevance, so they share the RERANK_* thresholds.
                score_kind = "rerank"
                best_score = reranked[0][1]
                scored_chunks = [(score, rows[idx]) for idx, score in reranked]
                print(f"[RAG] reranked with {rerank_backend} cross-encoder")
            else:
                # Both cross-encoders unavailable. Cosine is a different scale
                # AND is not separable on this corpus (an out-of-corpus query
                # measured 0.560 while a genuine hit measured 0.367), so the
                # tiering below refuses to certify anything scored this way.
                score_kind = "cosine"
                # `rows` is ordered by RRF fused_score, not by cosine, so
                # rows[0] is not necessarily the closest chunk in vector space:
                # a chunk pulled up by the keyword arm can sit first with a weak
                # vector_score. Taking rows[0]'s score as best_score understated
                # confidence and threw away correct textbook context. Sort the
                # candidates by cosine before slicing, and take the max.
                scored_chunks = sorted(
                    ((r["vector_score"] or 0.0, r) for r in rows),
                    key=lambda sr: sr[0],
                    reverse=True,
                )[:3]
                best_score = scored_chunks[0][0] if scored_chunks else 0.0
                print("[RAG] no cross-encoder available; scoring on raw cosine")

            context_parts = [r["content"] for _, r in scored_chunks]
            context = "\n\n---\n\n".join(context_parts)

        # Confidence tier drives BOTH the prompt mode and the badge the student
        # sees, so the two can no longer disagree.
        confidence_tier = _confidence_tier(best_score, score_kind) if rows else "low"

        # Raw cosine may not certify an answer as textbook-grounded at all.
        # Measured against the Class 10 Science corpus, "explain quantum field
        # theory renormalization" — a topic the books do not contain — scored
        # 0.560 cosine, clearing COSINE_HIGH (0.55) and earning an "NCERT
        # Verified" badge, while the genuine question "state Ohm's law" scored
        # only 0.367. The out-of-corpus query outranked the real one, so no
        # cosine threshold can separate them; a bi-encoder measures topical
        # closeness, not whether a passage answers the question. Both
        # cross-encoders scored that same pair below 0.001.
        #
        # This path is now rare — rerank_service falls back to a local
        # cross-encoder that needs no API key — so dropping to "low" here costs
        # almost nothing and stops the badge from ever overstating the evidence.
        if score_kind != "rerank" and confidence_tier != "low":
            print(f"[RAG] cosine-only score cannot certify grounding; "
                  f"{confidence_tier} -> low")
            confidence_tier = "low"
        print(
            f"[RAG] best_score={best_score:.4f} ({score_kind}) -> tier={confidence_tier} "
            f"from {len(rows)} chunk(s)"
        )

        # Cite only chunks that actually cleared the relevance floor, and only
        # when the answer is genuinely grounded in them. Previously all three
        # reranked chunks became citations regardless of score — including in
        # last-resort mode, where the model was told to ignore them entirely —
        # so the UI could attribute a web-sourced answer to an NCERT chapter.
        citation_floor = RERANK_MEDIUM if score_kind == "rerank" else COSINE_MEDIUM
        if confidence_tier != "low":
            seen_refs = set()
            for score, r in scored_chunks:
                if score < citation_floor:
                    continue
                ref = (r["chapter"], r["page_number"] or 0)
                if ref in seen_refs:
                    continue
                seen_refs.add(ref)
                citations.append(
                    Citation(chapter=r["chapter"], page=r["page_number"] or 0, source=r["subject"])
                )
                if len(citations) >= 3:
                    break
        if not citations and confidence_tier != "low":
            # Nothing cleared the floor; the answer is not textbook-grounded.
            confidence_tier = "low"
            print("[RAG] no chunk cleared the citation floor; downgrading to low")

        # ── Class-scope gate for video lessons ────────────────────────────
        # A video is only offered for something this student's class actually
        # studies. Anything else is refused outright rather than answered with
        # a lesson from a syllabus they are not being assessed on.
        if intent.get("video"):
            video_in_syllabus = _video_syllabus_verdict(
                aid_topic,
                student_class_no,
                intent_in_syllabus,
                confidence_tier,
                searched_class_books,
            )

            if not video_in_syllabus:
                # Deterministic refusal — not routed through the LLM, so it
                # cannot be talked out of the class restriction by the phrasing
                # of the next request.
                print(
                    f"[YT GATE] refusing video for {aid_topic!r} "
                    f"(class {student_class_no})"
                )
                if pyq_task:
                    pyq_task.cancel()
                return ChatResponse(
                    id=os.urandom(4).hex(),
                    role="assistant",
                    content=_out_of_syllabus_video_reply(aid_topic, student_class_no),
                    confidenceMode="ai_knowledge",
                    confidenceScore=None,
                    citations=[],
                    artifact=None,
                    createdAt=datetime.utcnow().isoformat() + "Z",
                )

            # In syllabus. Start the lookup now so it overlaps generation.
            print(f"[AID] video search: topic={aid_topic!r} class={student_class_no}")
            video_task = asyncio.create_task(
                asyncio.to_thread(
                    find_lesson_videos,
                    aid_topic,
                    subject_area,
                    student_class_no,
                    3,
                )
            )

        # Best-effort subject hint from retrieval, used later to help the Tool
        # Routing Protocol pick the right visualization tool for this question
        detected_subject = citations[0].source if citations else "unclear from retrieval"

        tavily_task = None
        tavily_text_task = None
        if intent.get("image"):
            # Gate 1: only fetch when the topic has a genuine visual/spatial
            # component (anatomy, circuits, diagrams, maps, etc.).
            # History definitions, poems, and general explanations are text-only.
            if _genuinely_needs_diagram(aid_topic, subject_area):
                # Build a class-specific, labelled-diagram query so Tavily
                # returns an NCERT-style educational image, not a stock photo.
                diagram_query = _build_diagram_query(aid_topic, subject_area, student_class_no)
                print(f"[AID] image search (class {student_class_no}): {diagram_query!r}")
                tavily_task = asyncio.create_task(
                    tavily_client.search(
                        query=diagram_query,
                        include_images=True,
                        max_results=3,
                        search_depth="basic",
                    )
                )
            else:
                print(
                    f"[AID] image suppressed — topic {aid_topic!r} does not "
                    f"genuinely benefit from a diagram"
                )

        # The trigger has to be read on whichever scale best_score is actually
        # on. The old hardcoded 0.15 was a rerank-scale number applied to both:
        # measured over 8 out-of-corpus queries, the lowest cosine seen was
        # 0.171, so on the cosine path this never fired even once. A question
        # the textbooks could not answer therefore got no citations AND no web
        # search, leaving the model to answer from memory unaided.
        # Keying off the tier rather than re-deriving a threshold also covers
        # the two other ways an answer ends up ungrounded: no rows at all, and
        # the citation-floor downgrade above. Web search now fires exactly when
        # the answer is not textbook-grounded.
        if confidence_tier == "low":
            print(
                f"[INFO] Low {score_kind} confidence ({best_score:.4f}); "
                f"launching Tavily text search for: {semantic_query}"
            )
            # This path only fires when vector retrieval failed to ground the
            # answer — it is the last chance at real evidence before the model
            # falls back to memory. `advanced` returns fuller passages and
            # ranks better on educational queries; the extra ~1s is a fair
            # trade for grounding, and even more so for the voice tutor where
            # the alternative is an unsourced spoken answer.
            tavily_text_task = asyncio.create_task(
                tavily_client.search(
                    query=semantic_query + f" NCERT class {student_class_no or ''}",
                    include_images=False,
                    max_results=5,
                    search_depth="advanced",
                )
            )

        dynamic_instruction = ""
        # The flowchart is emitted only when the planner asked for one AND the
        # topic is not explicitly outside the student's syllabus. The planner's
        # own rule already bakes in-syllabus into the decision, but a defensive
        # gate here means an off-syllabus follow-up question in the same chat
        # cannot end up with a Mermaid diagram of, say, university-level
        # biochemistry attached to it.
        if intent.get("flowchart") and intent_in_syllabus is not False:
            dynamic_instruction += MERMAID_BLOCK
        if intent.get("3d_model"):
            # A sentinel, not str.format() — the block is full of literal JSON
            # braces that format() would try to interpret as fields.
            dynamic_instruction += WIDGET_3D_BLOCK.replace(
                "__CLASS__", str(req.student_class)
            )
        if intent.get("image"):
            # The student asked to see something, so the answer must name and
            # explain each labelled part rather than just captioning a picture.
            dynamic_instruction += IMAGE_LEGEND_BLOCK.format(
                student_class=req.student_class
            )
        if intent.get("video"):
            dynamic_instruction += VIDEO_BLOCK

        NOTES_BLOCK = """
--- URGENT OVERRIDE: NOTES GENERATION ---
CRITICAL: The user has requested study notes or a PDF. This is a meta-request.
1. DO NOT reject the query or say it is outside the NCERT curriculum.
2. You MUST output EXACTLY the following token at the end of your response:
[WIDGET_NOTES]
"""
        if intent.get("notes"):
            dynamic_instruction += NOTES_BLOCK

        # An attached file changes what the student is asking for: they want THIS
        # page worked through, not a general lecture on the chapter. Without this
        # the tutor treats the file as background reading and answers around it.
        ATTACHMENT_BLOCK = """
--- THE STUDENT ATTACHED FILES TO THIS MESSAGE ---
Their file is the subject of this turn. Follow these rules:
1. Answer about what is actually IN the file. Quote the exact question, heading or
   numbers you are working from so the student knows you read the right thing.
2. If the file contains a question or exercise, solve it — showing the steps — instead
   of explaining the topic in general.
3. If the file has several questions, work through them in order, numbered as the file
   numbers them.
4. If you genuinely cannot make out part of the file (blurred photo, missing page),
   say which part and ask for it again. NEVER invent the contents of a file.
5. The retrieved NCERT context below is supporting material. When it disagrees with
   the student's file, answer about the file and note the difference.
6. The response format rules above still apply.
"""
        if attachments:
            dynamic_instruction += ATTACHMENT_BLOCK

        # Same tier that produces the badge, so "NCERT Verified" in the UI now
        # means the answer really was written from high-scoring textbook text.
        # The refusal string is intentionally identical to the one in the system
        # prompt's Section A so the model never has to pick between two variants
        # of the same message; the divergence used to cause different refusals
        # depending on which tier fired.
        _REFUSAL = (
            "I'm sorry, but this question seems to be outside the NCERT syllabus "
            "for your class. I can only help with topics from your curriculum. "
            "Try asking me something from your textbook!"
        )
        if confidence_tier == "high":
            confidence_mode_label = "NCERT Vector Primary — high grounding"
            mode_instruction = (
                "STRICT VECTOR RULE: build the answer FIRST AND EXCLUSIVELY from the retrieved "
                "textbook chunks. Do not add facts from outside the chunks when the chunks "
                "already cover the point. Never print source names, book titles, chapter "
                "numbers or page numbers in the reply."
            )
        elif confidence_tier == "medium":
            confidence_mode_label = "NCERT Vector Reference — moderate grounding"
            mode_instruction = (
                "STRICT VECTOR RULE: rely primarily on the retrieved textbook chunks; the "
                "surrounding NCERT context in your training may fill small gaps but must not "
                "contradict the chunks. Never print source names, book titles, chapter numbers "
                "or page numbers in the reply."
            )
        else:
            confidence_mode_label = "AI knowledge — last resort (low vector confidence)"
            mode_instruction = (
                "LAST RESORT MODE: the retrieval did not return relevant textbook context. "
                "You may use the [Web Search Results] block if present, but ONLY when the topic "
                "belongs to the NCERT curriculum for the student's class. If the topic is clearly "
                f"outside NCERT, reply with EXACTLY this line and nothing else:\n{_REFUSAL}\n"
                "Never print source names, book titles, chapter numbers or page numbers in the reply."
            )
            
        if intent.get("notes"):
            mode_instruction = (
                "META REQUEST MODE: the student has asked for study notes / PDF. Skip the "
                "five-part teaching structure — the widget instructions below own this turn. "
                "Do not attempt to summarise the whole topic in the reply text."
            )

        if video_trigger and confidence_tier == "low":
            # "I still don't understand" and "show me a video" retrieve nothing,
            # so Last Resort Mode would fire its out-of-syllabus refusal at a
            # student who is simply stuck on the topic already being discussed.
            mode_instruction = (
                "FOLLOW-UP MODE: the student is asking about a topic already covered earlier "
                "in this conversation, either because they did not understand it or because "
                "they want it taught differently. This is NOT a new out-of-syllabus question — "
                "never reply that it is beyond your reach. Re-teach the SAME topic using the "
                "five-part structure above, and per Section D's Confused-signal rule use a "
                "COMPLETELY FRESH analogy — do not reword the earlier explanation. Never print "
                "source names, book titles, chapter numbers or page numbers."
            )

        if tavily_text_task:
            try:
                tavily_text_res = await tavily_text_task
                results = tavily_text_res.get("results", [])
                if results:
                    web_context = "\n\n---\n\n".join([f"Web Source: {r['title']}\n{r['content']}" for r in results])
                    context += f"\n\n[Web Search Results (Out of Syllabus)]\n{web_context}"
            except Exception as e:
                print(f"[ERROR] Tavily Text Extraction failed: {e}")

        system_prompt = f"""You are RAGNOUS — a patient, expert tutor for Indian school students (Class 6-12) who follow the NCERT curriculum. You teach for real understanding, not just to hand over an answer.

===============================================================
SECTION A — WHERE THE ANSWER COMES FROM (strict priority order)
===============================================================
1. **Retrieved NCERT textbook chunks** (below) are the PRIMARY and most trusted source. Read every chunk fully before you write. Build the answer from them.
2. **Web Search Results** (Tavily) — used ONLY when retrieval is empty or clearly unrelated to the student's question, AND the topic belongs to the NCERT curriculum for Class {req.student_class}. Look for a "[Web Search Results]" block below the vector context.
3. **Refusal** — if both fail AND the topic is outside NCERT for Class {req.student_class}, reply with EXACTLY:
   "I'm sorry, but this question seems to be outside the NCERT syllabus for your class. I can only help with topics from your curriculum. Try asking me something from your textbook!"

Never fabricate facts. Never print or mention "Source:", chapter numbers, page numbers, book titles, or file names — even if the retrieved chunks contain them.

Retrieved Textbook Chunks:
{context}

Current Mode: {confidence_mode_label}
{mode_instruction}

===============================================================
SECTION B — HOW TO TEACH FOR DEPTH (the answer structure)
===============================================================
Every non-trivial answer follows these five parts in order. Use **bold** headings so each part is visible. Skip a part ONLY when it genuinely does not apply (e.g. a greeting or meta request).

1. **Direct answer.** Two to four sentences that answer the student's actual question in plain English. No preamble, no "great question", no restating the question back.

2. **Why it works (the mechanism).** Explain the CAUSE, the step-by-step process, or the derivation — not just what happens. Introduce the correct technical terms and define each in plain words the first time it appears, e.g. "*meristem* (the growing tissue at the tip of a root or shoot)". For quantitative topics, show the formula and the units.

3. **Ground it in real life.** Anchor the idea in something the student has seen, eaten, touched, or done. Prefer Indian contexts where they fit naturally — a pressure cooker, the monsoon, cricket, a local market, an autorickshaw, jalebi syrup, a ceiling fan — instead of generic Western examples.

4. **Worked example or fresh analogy.**
   • For Maths, Physics, Chemistry (numerical): solve ONE small numerical example end to end. Show every algebraic step; do not skip a line. State the answer with correct units and, where relevant, significant figures.
   • For Biology, History, Geography, Civics, Economics: give a vivid analogy in which every piece of the analogy maps to one piece of the concept. The analogy must be structurally parallel, not just decorative. Finish it with a one-line bridge back to the real concept.

5. **Common mistake or misconception.** In one sentence, name the trap students usually fall into on this topic and why the correct idea is different. Skip only for greetings, meta requests, or when a widget block below says otherwise.

End with ONE short **"Try this →"** question the student can answer in their head — a genuine check of understanding, not a rhetorical restatement. Skip the "Try this →" for greetings, meta requests (notes/PDF), and turns where a widget block below instructs otherwise.

===============================================================
SECTION C — FORMATTING
===============================================================
- Use short paragraphs. One idea per sentence.
- Bullet or numbered lists are fine INSIDE a part; the answer as a whole is NOT a flat bullet list.
- Maths uses LaTeX: `$...$` for inline, `$$...$$` for display equations. Never Unicode superscripts like x² — use `x^2`.
- Chemistry equations use plain text arrows (→) and subscripts inside `$...$`.
- No emojis, except a single 👍 or 🌟 when praising a correct student answer.
- No horizontal rules, no closing summary paragraph.
- Do NOT print "Source:", page numbers, chapter numbers, book titles, or file names — ever.

===============================================================
SECTION D — ADAPTIVE DEPTH
===============================================================
- **Confused signal** — the student says "I don't understand", "samajh nahi aaya", "explain again", "confused", "still don't get it", "ek baar aur batao", "not clear", "makes no sense", or asks about the SAME topic a second time: use a COMPLETELY FRESH analogy (not the previous one reworded), slow the mechanism explanation down, and shrink each sentence.
- **Deeper request** — the student says "prove", "derive", "compare", "critically analyse", "in detail", "long answer", "board exam answer": expand SECTION B step 2. Include the full derivation, both sides of the comparison, or the critical evaluation. Language stays plain; the content does not get watered down.
- **Narrow follow-up** — the student asks a specific sub-question ("but why does it slow down?", "what happens if the temperature rises?"): answer THAT question directly. Do not restart the topic from scratch. Reuse the terms you have already introduced in this conversation.
- **Wrong-answer correction** — the student submitted a wrong answer: name what they got right first, then show precisely where the reasoning went off, then the fix. Never scold.

===============================================================
SECTION E — NON-NEGOTIABLES
===============================================================
- Respond entirely in **{req.language}**. Keep technical/scientific terms in English when they have no natural translation, and translate them in brackets the first time they appear.
- If retrieval is thin and the topic is still in-syllabus, write from the surrounding NCERT context you do have and say plainly that the textbook coverage is brief.
- Never invent quotations, dates, figures, or formulas. If a specific number is not in the retrieved chunks and you are not certain, say "the exact value depends on the source" instead of guessing.
- Never disparage the student or another teacher. Corrections are gentle.
- Follow every widget/dynamic block below to the letter — they override the "Try this →" line and the response shape when they apply.

{dynamic_instruction}
"""
        # -------- End Work Changes 4 -----------

        # Build message chain: system + history + current query
        history_messages = []
        for h in req.history[:-1]:  # exclude last item (that's the current query)
            if h.get("role") == "user":
                history_messages.append(HumanMessage(content=h["content"]))
            elif h.get("role") == "assistant":
                history_messages.append(AIMessage(content=h["content"]))

        # The turn's own text: attached documents first, then the question, so
        # the model has read the file before it reads what to do with it.
        turn_text = f"{attachment_context}{req.query}" if attachment_context else req.query

        # Images and scanned pages ride alongside the text as separate parts.
        # Only a vision model can accept them, so text-only fallbacks get a
        # plain string built from `turn_text` instead.
        if media_parts:
            human_content = [{"type": "text", "text": turn_text}, *media_parts]
        else:
            human_content = turn_text

        messages = [
            SystemMessage(content=system_prompt),
            *history_messages,
            HumanMessage(content=human_content)
        ]

        # NOTE: this used to branch to a different model for 3D requests; both
        # branches now point at the same NIM chain, so it's a single call. The
        # actual tool selection (GeoGebra/Hunyuan3D/Simulator/Sketchfab) happens
        # inside this same generation via the Tool Routing Protocol in the prompt.
        #
        # Pictures override the model picker: Groq's gpt-oss cannot see, so a
        # turn carrying an image has to go to Gemini whichever model the student
        # chose. The vision path uses ONLY the Gemini chain; the text path
        # walks Groq first (fast) then falls through to Gemini.
        if media_parts:
            if not gemini_answer_chain:
                # Nothing here can see. Say so rather than answering around the image.
                return ChatResponse(
                    id=str(int(datetime.now().timestamp() * 1000)),
                    role="assistant",
                    content=(
                        "I can't read images right now — the vision model isn't configured "
                        "on this server. Type the question out and I'll solve it with you."
                    ),
                    confidenceMode="ai_knowledge",
                    citations=[],
                    createdAt=datetime.now().isoformat(),
                )
            primary_chain = gemini_answer_chain
            text_only_chain: list = []
        elif req.model == "ragnous_pro_x1" and gemini_answer_chain:
            primary_chain = gemini_answer_chain + groq_answer_chain
            text_only_chain = groq_answer_chain
        else:
            primary_chain = groq_answer_chain + gemini_answer_chain
            text_only_chain = groq_answer_chain

        # The ordered walk replaces the two-tier "primary + one fallback" that
        # used to break whenever the ONE fallback was also down. Groq gpt-oss
        # -> qwen -> kimi -> gpt-oss-20b -> Gemini 3.6 -> 3.7 -> 3.8, or a
        # subset thereof depending on env config and what images are attached.
        try:
            response = await ainvoke_with_fallback(primary_chain, messages, label="CHAT")
        except RuntimeError:
            # Every vision-capable model failed. If we have any text-only Groq
            # models left, drop the images and try those with a note in the
            # prompt so the model tells the student it could not see them.
            if not media_parts or not text_only_chain:
                raise
            print("[WARN] All vision models failed; falling back to text-only Groq chain")
            fallback_text = turn_text + (
                "\n\n(The student also attached images I cannot see on this "
                "model. Answer from the text above and say plainly that you "
                "could not look at the picture.)"
            )
            fallback_messages = [
                SystemMessage(content=system_prompt),
                *history_messages,
                HumanMessage(content=fallback_text)
            ]
            response = await ainvoke_with_fallback(text_only_chain, fallback_messages, label="CHAT")
        content = response.content

        # Google GenAI sometimes returns a list of chunks instead of a string
        if isinstance(content, list):
            content = "".join([c.get("text", "") if isinstance(c, dict) else str(c) for c in content])
        elif not isinstance(content, str):
            content = str(content)


        # Optional local debug dump — guarded so it can never break a request in
        # environments where this hardcoded path doesn't exist (e.g. Cloud Run).
        # Set RAGNOUS_DEBUG_LOG_PATH locally if you want this back.
        debug_log_path = os.getenv("RAGNOUS_DEBUG_LOG_PATH")
        if debug_log_path:
            try:
                with open(debug_log_path, "w") as f:
                    f.write(content)
            except OSError as e:
                print(f"[WARN] Could not write debug log to {debug_log_path}: {e}")

        # Parse artifacts: mermaid, interactive_simulator, r3f_model.
        #
        # Order matters here. Each exact-labeled block is searched for and
        # stripped from `content` unconditionally (so raw code fences never
        # leak into the visible chat text), but only the FIRST one found is
        # surfaced as the structured `artifact` (the response schema only
        # carries one). The loose r3f fallback — which matches ANY fence
        # containing "mesh"/"Scene"/"@react-three" — only runs if none of the
        # exact labels matched anything; previously it ran unconditionally and
        # BEFORE the interactive_simulator check, so it could steal and
        # mislabel a simulator block before that check ever got to see it.
        artifact = None

        # 1. 3D Widget Intents
        intent_json, widget_start, widget_end = _extract_widget_intent(content)
        if widget_start != -1:
            # The token is cut from the visible answer no matter what happens
            # below, so a malformed or unresolvable one never reaches the UI.
            content = content[:widget_start] + content[widget_end:]
        if intent_json:
            try:
                wtype = intent_json.get("type")
                query = intent_json.get("query", "") or aid_topic

                # Do not trust the LLM's routing blindly. Asking for "3D model
                # of the human brain" was routed to "molecule"; PubChem
                # resolved the word to an unrelated compound and the student
                # got a meaningless wireframe instead of a brain.
                wtype = _validate_widget_type(wtype, query)

                # Resolution lives in model3d_service: curated model first,
                # then Sketchfab, and only then AI generation. It used to run
                # in the opposite order, inline and synchronously, which both
                # blocked the worker for up to 30s and showed a generated blob
                # in place of a real scanned model.
                widget_data = await model3d_service.resolve_widget(wtype, query)

                if widget_data:
                    # Part labels ride along with the model so the student can
                    # see what is what, not just an unannotated shape. For a
                    # curated topic the NCERT part list wins over whatever the
                    # answer model volunteered.
                    widget_labels = model3d_service.normalize_labels(
                        intent_json.get("labels"), widget_data.get("topic", "")
                    )
                    if widget_labels:
                        widget_data["labels"] = widget_labels
                        print(f"[3D LABELS] {[l['name'] for l in widget_labels]}")
                    artifact = {"type": "widget_3d", "data": widget_data}
            except Exception as e:
                print(f"Widget parsing error: {e}")

        # 1.5. Notes Widget
        if not artifact and "[WIDGET_NOTES]" in content:
            content = content.replace("[WIDGET_NOTES]", "").strip()
            artifact = {"type": "notes", "data": {}}
            if not content:
                content = "I've synthesized our conversation. How would you like to receive your study notes?"

        # 2. Flowcharts (The Mermaid block)
        mermaid_match = re.search(r"```\s*mermaid[^\n]*\n(.*?)(?:\n\s*```|$)", content, re.IGNORECASE | re.DOTALL)
        if mermaid_match and artifact is None:
            artifact = {"type": "mermaid", "data": mermaid_match.group(1).strip()}
            content = re.sub(r"```\s*mermaid[^\n]*\n.*?(?:\n\s*```|$)", "", content, count=1, flags=re.IGNORECASE | re.DOTALL)

        # 3. Video lesson. Awaited here so the search overlapped generation.
        #
        # This claims the artifact slot ahead of anything the model volunteered:
        # the response schema carries a single artifact, and when the student
        # asked to watch something (or said they were lost), the player is the
        # thing they are waiting for.
        if video_task:
            try:
                videos = await video_task
            except Exception as e:
                print(f"[YT] lookup failed: {e}")
                videos = []

            if videos:
                artifact = {
                    "type": "youtube",
                    "data": {
                        "topic": aid_topic,
                        "reason": video_trigger or "requested",
                        "videos": videos,
                    },
                }
            else:
                # Nothing worth showing. Say so rather than leaving the student
                # waiting for a player that will never appear.
                content += (
                    "\n\nI could not find a video lesson I trust for this one — "
                    "ask me to break any step down further and I will."
                )

        # Models paste a link anyway despite being told not to. A bare YouTube
        # URL next to a working player is noise, and half of the ones they
        # invent from memory are dead video IDs.
        if artifact and artifact.get("type") == "youtube":
            content = re.sub(
                r"\[([^\]]*)\]\((?:https?://)?(?:www\.)?(?:youtube\.com|youtu\.be)/[^)]*\)",
                r"\1",
                content,
            )
            content = re.sub(
                r"(?:https?://)?(?:www\.)?(?:youtube\.com/watch\?\S+|youtu\.be/\S+)",
                "",
                content,
            )

        # Tidy up blank-line gaps left behind by block removal
        content = re.sub(r'\n{3,}', '\n\n', content).strip()

        # If Groq used general knowledge, extract the URL and format it
        if "[NO_CITATION]" in content:
            parts = content.split("[NO_CITATION]")
            content = parts[0].strip()

            url_str = ""
            if len(parts) > 1 and "URL:" in parts[1]:
                url_str = parts[1].split("URL:")[1].strip()

            if not url_str:
                url_str = "https://en.wikipedia.org"

            citations = [Citation(chapter="Internet", page=0, source=url_str)]

        # The badge comes from the retrieval tier that actually produced the
        # answer. The old rule was `ncert_verified unless citations are empty`,
        # which labelled ANY hit — however weak — as NCERT Verified, never once
        # produced "extended_reference", and ignored best_score entirely.
        confidence_mode = _TIER_TO_MODE[confidence_tier]
        if citations and citations[0].chapter == "Internet":
            confidence_mode = "ai_knowledge"

        confidence_score = (
            _confidence_percent(best_score, score_kind, confidence_tier)
            if citations
            else None  # no grounding -> no meter, rather than a made-up number
        )
        print(f"[RAG] mode={confidence_mode} score={confidence_score} citations={len(citations)}")

        # Semantic Image Retrieval using Tavily
        # Only attach the image if:
        #   1. A Tavily image task was actually launched (topic passed the whitelist gate).
        #   2. The returned URL looks like a real image (has an image extension or
        #      comes from a known educational domain) — not a logo, ad, or off-topic photo.
        if tavily_task:
            try:
                tavily_img_res = await tavily_task
                images = tavily_img_res.get("images", [])
                _EDU_DOMAINS = (
                    "ncert.nic.in", "cbse", "byjus", "toppr", "vedantu",
                    "britannica", "wikipedia", "wikimedia", "khanacademy",
                    "teachoo", "meritnation", "extramarks", "doubtnut",
                    "embibe", "unacademy",
                )
                _IMG_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg")
                chosen_url = None
                for img_url in images:
                    url_lower = img_url.lower()
                    has_ext = any(url_lower.split("?")[0].endswith(ext) for ext in _IMG_EXTS)
                    from_edu = any(domain in url_lower for domain in _EDU_DOMAINS)
                    if has_ext or from_edu:
                        chosen_url = img_url
                        break
                if chosen_url:
                    print(f"[AID] attaching image: {chosen_url!r}")
                    content += f"\n\n![Related visual for {aid_topic}]({chosen_url})"
                else:
                    print(f"[AID] no suitable image found from Tavily (all {len(images)} rejected)")
            except Exception as e:
                print(f"[ERROR] Tavily Image Extraction failed: {e}")

        # Exam-history card. Awaited last so its web search + extraction ran
        # underneath everything above instead of adding to the wait.
        pyq = None
        if pyq_task:
            try:
                pyq = await pyq_task
            except Exception as e:
                print(f"[PYQ] lookup failed: {e}")

        return ChatResponse(
            id=os.urandom(4).hex(),
            role="assistant",
            content=content,
            confidenceMode=confidence_mode,
            confidenceScore=confidence_score,
            citations=citations,
            artifact=artifact,
            pyq=pyq,
            createdAt=datetime.utcnow().isoformat() + "Z"
        )
    except Exception as e:
        print(f"Error in chat endpoint: {e}")
        raise HTTPException(status_code=500, detail=str(e))