"""POST /api/v1/export/pdf — "Save as notes".

The endpoint is thin on purpose:

    notes_service.build_notes()   chat -> topics -> NCERT retrieval -> sections
    notes_render.render_notes_pdf()   markdown + textbook figures -> PDF

What used to live here was a single LLM call over the chat transcript plus a
regex markdown printer. Both moved: the synthesis into
app/services/notes_service.py, the rendering into app/api/v1/notes_render.py.
"""

import os
import re
import uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from typing import Dict, List, Optional

from app.services import notes_service
# Re-exported: callers (and RAGNOUS-BACKEND/test_pdf.py) import the renderer
# from here, and the endpoint below is the only other user.
from app.api.v1.notes_render import generate_study_notes_pdf, render_notes_pdf  # noqa: F401

router = APIRouter()


class ExportRequest(BaseModel):
    history: List[Dict[str, str]]
    # The student's class decides which books are searched, so notes for a
    # Class 8 chat are never built out of the Class 10 chapter. Defaulted so an
    # older client that only sends `history` still works.
    student_class: str = Field(default="10th", alias="student_class")
    language: str = "English"
    subjects: List[str] = Field(default_factory=list)
    topic_hint: Optional[str] = None

    model_config = {"populate_by_name": True}


def cleanup_file(filepath: str):
    if os.path.exists(filepath):
        try:
            os.remove(filepath)
        except Exception:
            pass


def _safe_filename(title: str) -> str:
    name = re.sub(r"[^A-Za-z0-9 _-]+", "", title or "").strip() or "Study Notes"
    return re.sub(r"\s+", "_", name)[:60] + ".pdf"


@router.post("/pdf")
async def export_chat_pdf(req: ExportRequest, background_tasks: BackgroundTasks):
    if not req.history:
        raise HTTPException(status_code=400, detail="History is empty")

    try:
        doc = await notes_service.build_notes(
            history=req.history,
            student_class=req.student_class,
            language=req.language,
            subjects=req.subjects,
            topic_hint=req.topic_hint,
        )
    except ValueError as e:
        # No academic topic in the chat — a 422 the client can show verbatim.
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        print(f"[NOTES] synthesis failed: {e}")
        raise HTTPException(status_code=500, detail=f"Could not build notes: {e}")

    tmp_filename = f"/tmp/ragnous_notes_{uuid.uuid4().hex}.pdf"
    try:
        render_notes_pdf(doc, tmp_filename)
    except Exception as e:
        cleanup_file(tmp_filename)
        print(f"[NOTES] PDF render failed: {e}")
        raise HTTPException(status_code=500, detail=f"Could not render notes: {e}")

    background_tasks.add_task(cleanup_file, tmp_filename)
    print(f"[NOTES] built {doc.title!r}: {len(doc.topics)} topic(s), "
          f"{len(doc.figures)} figure(s), {len(doc.sources)} source page(s)")
    return FileResponse(
        tmp_filename,
        media_type="application/pdf",
        filename=_safe_filename(doc.title),
        headers={"X-Notes-Title": doc.title[:120]},
    )
