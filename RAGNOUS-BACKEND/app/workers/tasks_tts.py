"""Off-request-path Deepgram TTS renders.

The voice endpoint pipes speech synthesis directly through Deepgram over
WebSocket — that path is already low-latency. This task is the *batch* variant:
one call given a long answer, returns a cached audio blob. Used by
"read this whole answer aloud" and any future audiobook-style summary export.

We ship a `.mp3` because it's the smallest widely-playable format Deepgram
emits; the browser's `<audio>` handles it without transcoding.
"""

from __future__ import annotations

import base64
import os
from typing import Dict

import httpx

from app.workers.celery_app import celery_app


DEEPGRAM_SPEAK_URL = "https://api.deepgram.com/v1/speak"


@celery_app.task(name="app.workers.tasks_tts.render_tts")
def render_tts(text: str, voice: str = "aura-asteria-en") -> Dict:
    """Return `{"audio_base64": ...}` or `{"error": ...}` on failure.

    Base64 chosen over a filesystem path because most consumers (frontend
    `<audio src="data:...">`, mobile clients, tests) prefer bytes-in-hand to
    a shared file. If audio size ever crosses a few MB routinely, swap for a
    signed URL pointing at object storage.
    """
    api_key = os.getenv("DEEPGRAM_API_KEY")
    if not api_key:
        return {"error": "DEEPGRAM_API_KEY not set"}

    if not text.strip():
        return {"error": "empty text"}

    # 4000 chars is Deepgram's `/speak` upper bound. Truncate rather than
    # split: this task is used for a single answer at a time, and an answer
    # longer than 4000 chars is very rarely worth reading aloud in full.
    payload = {"text": text[:4000]}
    params = {"model": voice, "encoding": "mp3"}
    headers = {
        "Authorization": f"Token {api_key}",
        "Content-Type": "application/json",
    }

    try:
        with httpx.Client(timeout=60.0) as client:
            res = client.post(
                DEEPGRAM_SPEAK_URL,
                params=params,
                headers=headers,
                json=payload,
            )
        res.raise_for_status()
    except httpx.HTTPError as exc:
        return {"error": f"deepgram error: {exc}"}

    return {"audio_base64": base64.b64encode(res.content).decode("ascii")}
