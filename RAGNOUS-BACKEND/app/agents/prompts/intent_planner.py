"""The prompt that turns a student's turn into a lesson plan JSON.

Text lifted from the original in-line `intent_prompt` in `chat.py`, packaged
as a builder so the intent-classifier node can pass in the runtime values
(class, student text) without threading f-strings through the graph.
"""

from __future__ import annotations


INTENT_JSON_SHAPE = (
    '{"teaching_aid": "none"|"flowchart"|"image"|"3d"|"video", '
    '"aid_topic": string, "subject_area": string, '
    '"needs_notes": boolean, "in_syllabus": boolean, '
    '"expanded_query": string}'
)


def build_intent_prompt(student_class: str, query: str) -> str:
    """The single-shot JSON prompt for the tiny planner LLM."""
    return f"""You are the lesson planner for an NCERT tutor for Indian school students (Class 8-12).

Student's class: {student_class}
Student's question: "{query}"

Decide the single BEST way to teach this specific question, then reply with ONLY a valid JSON object, nothing else:
{INTENT_JSON_SHAPE}

How to choose teaching_aid — pick the ONE that genuinely helps most:
- "flowchart": choose this when the topic is a MULTI-STEP process, cycle, pathway, mechanism, classification tree, cause-effect chain, life-cycle, historical timeline of causes/consequences, or a decision procedure — AND the topic is in NCERT for Class {student_class}.
- "image": the student must recognise the real appearance of something, or must learn to NAME the parts of a structure.
- "3d": the answer depends on understanding shape, spatial arrangement or volume that a flat picture cannot convey.
- "video": the student asked to watch or be shown a video/lecture, OR says they did not understand.
- "none": the answer is definitional, numerical, historical or conversational. Prefer "none" when in doubt.

When BOTH "flowchart" and "video" would fit, pick "flowchart".

Other fields:
- aid_topic: the exact NCERT topic this question is about, as a short noun phrase. Fill this in even when teaching_aid is "none".
- subject_area: the school subject this belongs to. Use "other" if it is not a school subject.
- needs_notes: true ONLY if the student explicitly asked for study notes or a PDF.
- in_syllabus: true if aid_topic is taught anywhere in the NCERT syllabus for Class {student_class} or in earlier classes.
- expanded_query: the student's question rewritten with the formal NCERT terms it implies. Keep the original wording and append the formal terms.
"""
