"""Off-request-path notes-PDF rendering.

The synchronous export endpoint at `/api/v1/export/pdf` still exists for the
"download now" path — this task is here for the future "email it to me" flow
that the artifact card exposes but currently disables. When the frontend lifts
the disabled state, it will POST a job body here and poll the result.

Kept lean by design: it is one wrapper around `notes_service.build_notes` +
`api.v1.notes_render.render_notes_pdf`. Celery uploads the resulting bytes to
wherever `NOTES_STORAGE_URL` points; a file:// URL is fine for local dev.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import urllib.parse
from typing import Dict, List

from app.workers.celery_app import celery_app


@celery_app.task(name="app.workers.tasks_export.render_notes_pdf")
def render_notes_pdf_task(
    history: List[Dict],
    student_class: str = "10th",
    language: str = "English",
    subjects: List[str] | None = None,
    topic_hint: str | None = None,
) -> Dict:
    """Return `{"path": <where the PDF ended up>, "title": <doc title>}`.

    Callers poll the Celery result and hand the path to the file service. The
    render itself is expensive (multiple LLM calls, figure crop reads, PDF
    generation) — off the request path is exactly where it belongs.
    """
    async def _build():
        from app.services import notes_service
        return await notes_service.build_notes(
            history=history,
            student_class=student_class,
            language=language,
            subjects=subjects or [],
            topic_hint=topic_hint,
        )

    doc = asyncio.run(_build())

    from app.api.v1.notes_render import render_notes_pdf

    fd, tmp_path = tempfile.mkstemp(prefix="ragnous_notes_", suffix=".pdf")
    os.close(fd)
    render_notes_pdf(doc, tmp_path)

    storage_url = os.getenv("NOTES_STORAGE_URL")
    if storage_url:
        parsed = urllib.parse.urlparse(storage_url)
        if parsed.scheme == "file":
            target_dir = parsed.path
            os.makedirs(target_dir, exist_ok=True)
            final_path = os.path.join(target_dir, os.path.basename(tmp_path))
            os.rename(tmp_path, final_path)
            tmp_path = final_path
        # An S3-style scheme would upload here; kept out of scope until an
        # actual deployment target picks one.

    return {"path": tmp_path, "title": getattr(doc, "title", "Study Notes")}
