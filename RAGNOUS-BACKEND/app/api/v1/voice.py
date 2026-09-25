"""Server-side proxy for every Deepgram call the browser makes.

The Deepgram key used to live in the Vite bundle, which meant anyone who
loaded the app could read it and spend against the account. It now lives only
here: the browser opens a socket to us, we open the socket to Deepgram with
the key attached, and we shovel frames between the two.

Note that CORS does not apply to WebSockets — a browser will happily let any
page open a socket to localhost:8000. So each endpoint checks Origin itself;
without that, moving the key server-side would just turn this backend into an
open relay for the same key.
"""

import asyncio
import json
import os
import re
from typing import Optional
from urllib.parse import urlencode

import httpx
import websockets
from fastapi import APIRouter, HTTPException, Request, WebSocket
from fastapi.responses import Response
from pydantic import BaseModel, Field
from websockets.exceptions import ConnectionClosed

router = APIRouter()

DEEPGRAM_AGENT_URL = "wss://agent.deepgram.com/v1/agent/converse"
DEEPGRAM_LISTEN_URL = "wss://api.deepgram.com/v1/listen"
DEEPGRAM_SPEAK_URL = "https://api.deepgram.com/v1/speak"

_DEFAULT_ORIGINS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:4173",
    "http://127.0.0.1:4173",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
)

# Query params the dictation socket may set. Everything else is dropped rather
# than forwarded, so a crafted URL cannot aim our key at arbitrary features.
_LISTEN_ALLOWED_PARAMS = {
    "model",
    "language",
    "smart_format",
    "interim_results",
    "punctuate",
    "endpointing",
    "encoding",
    "sample_rate",
    "channels",
    "vad_events",
}

_MODEL_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

# ── English-only lock for the voice tutor ────────────────────────────────
# The Deepgram Agent socket carries a JSON Settings message from the browser
# on connect (and may carry UpdatePrompt frames later). Both of those decide
# what language the bot listens for, what voice it speaks in, and what its
# system prompt says. Left untouched the browser could pick Hindi ASR, a
# Hindi Aura voice or a prompt that lets the agent reply in Hindi. This
# module clamps all three server-side so the client cannot opt out.
_ENGLISH_ONLY_CLAUSE = (
    "\n\nLANGUAGE LOCK — ABSOLUTE: You MUST speak and reply ONLY in English. "
    "Never speak, write, translate to, or acknowledge requests in Hindi, "
    "Hinglish, Punjabi, Bengali, Tamil, Telugu, Marathi, Gujarati, Kannada, "
    "Malayalam, Urdu, or any language other than English. If the student "
    "speaks to you in another language, respond in English, and gently say "
    "one short sentence in English asking them to continue in English. Do "
    "not repeat their words in their language."
)
# Default English voice used whenever the client asked for a non-English one.
# aura-asteria-en is the same model /speak already defaults to, so behaviour
# is consistent across the read-aloud button and the live tutor.
_DEFAULT_ENGLISH_VOICE = "aura-asteria-en"
# A Deepgram voice model is considered English when its id ends in `-en` (or
# an `-en-XX` locale). Everything else is replaced with the default above.
_ENGLISH_VOICE_RE = re.compile(r"-en(?:-[a-z]{2})?$", re.IGNORECASE)


def _looks_english_voice(model: object) -> bool:
    return isinstance(model, str) and bool(_ENGLISH_VOICE_RE.search(model))


def _clamp_speak_provider(provider: dict) -> None:
    """Force a Deepgram Aura English voice on a Deepgram speak provider.

    Only Deepgram Aura providers are clamped: their ``model`` id encodes the
    language (``aura-*-en``, ``aura-*-hi`` …) so we can meaningfully swap it.
    ElevenLabs / Cartesia / OpenAI providers use provider-specific ``model_id``
    and ``voice_id`` fields whose names do not encode language; blindly setting
    ``model = "aura-asteria-en"`` on those produces an invalid config Deepgram
    rejects with UNPARSABLE_CLIENT_MESSAGE on ``agent.speak``. For those we
    leave the provider alone and rely on the prompt-level LANGUAGE LOCK plus
    the ASR language to keep the tutor in English.
    """
    if not isinstance(provider, dict):
        return
    ptype = provider.get("type")
    # Deepgram is the only provider we understand well enough to clamp safely.
    if ptype not in (None, "deepgram"):
        return
    model = provider.get("model")
    if not _looks_english_voice(model):
        provider["model"] = _DEFAULT_ENGLISH_VOICE
    for alt in ("voice", "voice_id", "voice_name"):
        alt_val = provider.get(alt)
        if isinstance(alt_val, str) and not _looks_english_voice(alt_val):
            provider[alt] = _DEFAULT_ENGLISH_VOICE


def _append_english_clause(prompt: object) -> str:
    """Add the language-lock clause once; never twice."""
    text = prompt if isinstance(prompt, str) else ""
    if "LANGUAGE LOCK — ABSOLUTE" in text:
        return text
    return text + _ENGLISH_ONLY_CLAUSE


def _clamp_agent_config(agent: dict) -> None:
    """Rewrite the Deepgram Agent config so only English is possible.

    We do NOT set a language field anywhere. Deepgram's v1 Agent API rejects
    ``agent.listen.language`` outright (UNPARSABLE_CLIENT_MESSAGE), and rejects
    ``agent.language`` whenever the client uses the V2 (Flux) listen API — as
    the live tutor does with ``flux-general-multi``. English is instead pinned
    by (a) clamping the TTS voice to an English Aura model and (b) appending
    the LANGUAGE LOCK clause to the Think prompt, which together stop the bot
    from replying in anything else.
    """
    if not isinstance(agent, dict):
        return

    # Strip any stale language field the client may have set — Deepgram will
    # reject either of these once Flux is in play, or on the listen block at all.
    agent.pop("language", None)
    listen = agent.get("listen")
    if isinstance(listen, dict):
        listen.pop("language", None)
        provider = listen.get("provider")
        if isinstance(provider, dict):
            provider.pop("language", None)

    # TTS (speak) — force an English Aura voice.
    speak = agent.get("speak")
    if isinstance(speak, dict):
        # Some agent configs accept a list of fallbacks under `speak`; both
        # shapes (single dict / list) are covered.
        providers = speak.get("provider")
        if isinstance(providers, dict):
            _clamp_speak_provider(providers)
        elif isinstance(providers, list):
            for p in providers:
                _clamp_speak_provider(p)
        # Legacy schema kept the model id at the outer speak level. Only touch
        # it if it is actually there — inventing one alongside `provider` in
        # the current schema produces an "unparseable agent.speak" error.
        if "model" in speak:
            _clamp_speak_provider(speak)

    # The system prompt for the thinking model — append the lock clause so
    # even if the browser's prompt hinted at Hindi, ours overrides it.
    think = agent.get("think")
    if isinstance(think, dict):
        think["prompt"] = _append_english_clause(think.get("prompt"))
        # Some schemas nest the prompt under a `provider.prompt` field.
        provider = think.get("provider")
        if isinstance(provider, dict) and "prompt" in provider:
            provider["prompt"] = _append_english_clause(provider.get("prompt"))


def _english_lock_frame(text: str) -> str:
    """Inspect one text frame browser→Deepgram; clamp if it configures the bot.

    Everything else (transcripts, tool responses, keepalives) is passed through
    unchanged. A frame that is not JSON is also passed through — Deepgram
    would have rejected it anyway if it were structural.
    """
    stripped = text.strip()
    if not stripped or stripped[0] != "{":
        return text
    try:
        obj = json.loads(stripped)
    except (ValueError, TypeError):
        return text
    if not isinstance(obj, dict):
        return text

    msg_type = obj.get("type")
    if msg_type == "Settings":
        agent = obj.get("agent")
        if isinstance(agent, dict):
            _clamp_agent_config(agent)
    elif msg_type == "UpdatePrompt":
        obj["prompt"] = _append_english_clause(obj.get("prompt"))
    elif msg_type == "UpdateSpeak":
        # UpdateSpeak may pass a provider dict or a bare model id.
        provider = obj.get("provider")
        if isinstance(provider, dict):
            _clamp_speak_provider(provider)
        elif isinstance(provider, str) and not _looks_english_voice(provider):
            obj["provider"] = _DEFAULT_ENGLISH_VOICE
        _clamp_speak_provider(obj)
    else:
        return text

    return json.dumps(obj, ensure_ascii=False)


def _allowed_origins() -> set:
    configured = os.getenv("ALLOWED_ORIGINS", "")
    if configured.strip():
        return {o.strip().rstrip("/") for o in configured.split(",") if o.strip()}
    return set(_DEFAULT_ORIGINS)


def _origin_ok(origin: Optional[str]) -> bool:
    # Non-browser clients (curl, tests) send no Origin at all. Browsers always
    # do, and they are the only thing this guard is defending against.
    if not origin:
        return True
    return origin.rstrip("/") in _allowed_origins()


def _api_key() -> Optional[str]:
    key = os.getenv("DEEPGRAM_API_KEY", "").strip()
    return key or None


async def _pump_client_to_deepgram(ws: WebSocket, dg, *, english_lock: bool = False) -> None:
    """Browser → Deepgram, preserving text/binary framing.

    When ``english_lock`` is set (the Agent socket), text frames go through
    ``_english_lock_frame`` so the browser cannot pick Hindi ASR, a non-English
    Aura voice, or a prompt that would let the bot answer in another language.
    Binary frames (raw audio) are always passed through untouched.
    """
    while True:
        message = await ws.receive()
        if message.get("type") == "websocket.disconnect":
            return
        data = message.get("bytes")
        if data is not None:
            await dg.send(data)
            continue
        text = message.get("text")
        if text is not None:
            if english_lock:
                text = _english_lock_frame(text)
            await dg.send(text)


async def _pump_deepgram_to_client(ws: WebSocket, dg) -> None:
    """Deepgram → browser, same framing in the other direction."""
    async for message in dg:
        if isinstance(message, (bytes, bytearray)):
            await ws.send_bytes(bytes(message))
        else:
            await ws.send_text(message)


async def _relay(ws: WebSocket, upstream_url: str, *, english_lock: bool = False) -> None:
    origin = ws.headers.get("origin")
    if not _origin_ok(origin):
        # Refuse before the handshake completes so the page gets a hard failure.
        await ws.close(code=1008, reason="Origin not allowed")
        return

    key = _api_key()
    await ws.accept()
    if not key:
        await ws.close(code=1011, reason="DEEPGRAM_API_KEY is not set on the server")
        return

    try:
        async with websockets.connect(
            upstream_url,
            additional_headers={"Authorization": f"Token {key}"},
            max_size=None,       # agent audio frames run large
            ping_interval=5,
            ping_timeout=20,
            close_timeout=5,
        ) as dg:
            pumps = [
                asyncio.create_task(_pump_client_to_deepgram(ws, dg, english_lock=english_lock)),
                asyncio.create_task(_pump_deepgram_to_client(ws, dg)),
            ]
            try:
                # Either side hanging up ends the call; don't leave the other
                # pump parked on a socket that is never going to speak again.
                done, pending = await asyncio.wait(
                    pumps, return_when=asyncio.FIRST_COMPLETED
                )
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                for task in done:
                    exc = task.exception()
                    if exc and not isinstance(exc, (ConnectionClosed, RuntimeError)):
                        raise exc
            finally:
                for task in pumps:
                    task.cancel()
    except websockets.exceptions.InvalidStatus as exc:
        status = getattr(getattr(exc, "response", None), "status_code", "unknown")
        print(f"[voice] Deepgram rejected the upstream socket: HTTP {status}")
        await _safe_close(ws, 1011, f"Deepgram rejected the connection ({status})")
        return
    except ConnectionClosed:
        pass
    except Exception as exc:  # noqa: BLE001 - the socket must always be closed
        print(f"[voice] relay error: {type(exc).__name__}: {exc}")
        await _safe_close(ws, 1011, "Voice relay failed")
        return

    await _safe_close(ws, 1000, "session ended")


async def _safe_close(ws: WebSocket, code: int, reason: str) -> None:
    try:
        await ws.close(code=code, reason=reason)
    except RuntimeError:
        pass  # already closed by the client


@router.websocket("/agent")
async def agent_socket(ws: WebSocket) -> None:
    """Live tutor. The browser drives the Deepgram Agent protocol end to end;
    we attach credentials AND enforce the English-only language lock so the
    bot can never be pushed into Hindi (or any other language) by the client."""
    await _relay(ws, DEEPGRAM_AGENT_URL, english_lock=True)


@router.websocket("/listen")
async def listen_socket(ws: WebSocket) -> None:
    """Composer dictation. Always transcribes English — a request for any
    other language is ignored, because the live tutor is English-only and
    dictation must match."""
    params = {
        k: v for k, v in ws.query_params.items() if k in _LISTEN_ALLOWED_PARAMS
    }
    params.setdefault("model", "nova-2")
    params.setdefault("smart_format", "true")
    params.setdefault("interim_results", "true")
    # The client cannot opt out of English on this endpoint.
    params["language"] = "en"
    await _relay(ws, f"{DEEPGRAM_LISTEN_URL}?{urlencode(params)}")


class SpeakRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)
    model: str = Field(default="aura-asteria-en")


@router.post("/speak")
async def speak(body: SpeakRequest, request: Request) -> Response:
    """Read-aloud for a chat message. Returns the mp3 bytes straight through."""
    if not _origin_ok(request.headers.get("origin")):
        raise HTTPException(status_code=403, detail="Origin not allowed")

    key = _api_key()
    if not key:
        raise HTTPException(status_code=503, detail="DEEPGRAM_API_KEY is not set on the server")

    if not _MODEL_RE.match(body.model):
        raise HTTPException(status_code=400, detail="Unrecognised voice model")

    # Same English-only lock as the live tutor: a Hindi (or any non-English)
    # Aura voice is silently swapped for the default English voice so the
    # read-aloud button cannot be nudged into another language by the client.
    if not _looks_english_voice(body.model):
        print(f"[voice] rejecting non-English speak model {body.model!r}; using {_DEFAULT_ENGLISH_VOICE}")
        body.model = _DEFAULT_ENGLISH_VOICE

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            res = await client.post(
                DEEPGRAM_SPEAK_URL,
                params={"model": body.model, "encoding": "mp3"},
                headers={
                    "Authorization": f"Token {key}",
                    "Content-Type": "application/json",
                },
                json={"text": body.text},
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach Deepgram: {exc}") from exc

    if res.status_code != 200:
        # Deepgram's body can carry account detail; keep it in the server log.
        print(f"[voice] Deepgram speak failed {res.status_code}: {res.text[:400]}")
        raise HTTPException(status_code=502, detail="Text-to-speech failed")

    return Response(
        content=res.content,
        media_type="audio/mpeg",
        headers={"Cache-Control": "no-store"},
    )
