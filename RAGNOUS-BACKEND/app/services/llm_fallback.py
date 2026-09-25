"""Ordered LLM fallback chains for Groq and Gemini.

The Groq free tier lists several models any key can reach (openai gpt-oss,
qwen, kimi, deepseek). Each has its own outage window and a decommission on
one does not take another down — llama-3.3 got retired in the middle of a
session, and having gpt-oss and qwen listed after it kept the tutor answering.
The same is true on the Gemini side, where a given `gemini-3.x-flash`
occasionally returns 503 "high demand" — walking to the next flash model
usually succeeds on the same key.

Callers pick a *role* (``answer``, ``intent``, ``notes``, ``quiz``); the role
selects the tuned constructor kwargs (temperature, ``max_tokens``, timeout).
Model lists come from env variables so a new model on either provider can be
picked up without a code change.

``ainvoke_with_fallback()`` walks the chain in order and returns the first
successful response. The full-chain builder puts every Groq model first (they
answer in seconds) followed by every Gemini flash (slower, but the headroom on
this key is what catches Groq's per-minute cap).
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Iterable, List, Optional, Sequence

from langchain_core.messages import BaseMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_groq import ChatGroq


# ── Defaults ──────────────────────────────────────────────────────────────
# Each list is ordered: index 0 is tried first, and the walk stops on the
# first success. The Groq lists include models the free/on-demand tier of a
# stock key can reach as of this file being written — gpt-oss (the current
# workhorse), qwen (fast general purpose), kimi (large context), and the
# smaller gpt-oss-20b as a last resort for prompts that just need to complete.
# Gemini defaults follow the user's preferred order (3.6 primary).
_DEFAULT_GROQ_ANSWER_MODELS = (
    "openai/gpt-oss-120b,"
    "qwen/qwen3-32b,"
    "moonshotai/kimi-k2-instruct,"
    "openai/gpt-oss-20b"
)
_DEFAULT_GROQ_INTENT_MODELS = (
    "openai/gpt-oss-20b,"
    "qwen/qwen3-32b,"
    "openai/gpt-oss-120b"
)
_DEFAULT_GEMINI_MODELS = "gemini-3.6-flash,gemini-3.7-flash,gemini-3.8-flash"


# Constructor kwargs per role. Kept here so the same tuning applies whichever
# model in the chain is picked.
# Chat answer size: 2048 truncated the five-part depth structure mid-example,
# so it is set to fit a rich answer (direct + mechanism + real-world + worked
# example + misconception + Try-this) without a summary paragraph. 3072 still
# leaves headroom under Groq's 8000-tokens-per-minute cap for three answers
# per minute, which matches typical interactive chat pacing.
_ANSWER_MAX_TOKENS = int(os.getenv("CHAT_ANSWER_MAX_TOKENS", "3072"))

_ROLE_GROQ_KWARGS = {
    "answer":  dict(temperature=0.2, max_tokens=_ANSWER_MAX_TOKENS),
    # Small planner behind intent parsing: short JSON, cheap model preferred.
    "intent":  dict(temperature=0.2, max_tokens=512),
    # One notes section per call. Long form, so tokens are wide.
    "notes":   dict(temperature=0.3, max_tokens=int(os.getenv("NOTES_SECTION_MAX_TOKENS", "3600"))),
    # MCQ quiz. Slightly smaller than notes, still needs room for 5-6 items.
    "quiz":    dict(temperature=0.2, max_tokens=2600),
}

_ROLE_GEMINI_KWARGS = {
    "answer":  dict(temperature=0.2, max_tokens=_ANSWER_MAX_TOKENS),
    "intent":  dict(temperature=0.2, max_tokens=512),
    "notes":   dict(temperature=0.3, max_output_tokens=8192),
    "quiz":    dict(temperature=0.2, max_tokens=2600),
}


# ── Env parsing ───────────────────────────────────────────────────────────

def _split_models(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    return [m.strip() for m in raw.split(",") if m.strip()]


def _resolve_model_list(list_env: str, legacy_env: str, default_csv: str) -> List[str]:
    """Read the ordered model list from env, honouring the older single-model
    variable (``GROQ_ANSWER_MODEL``, ``GEMINI_NOTES_MODEL``) so an operator
    who pinned one model does not lose that preference — it becomes the head
    of the chain, followed by the defaults with any duplicates removed."""
    listed = _split_models(os.getenv(list_env))
    if listed:
        return listed

    default = _split_models(default_csv)
    legacy = (os.getenv(legacy_env) or "").strip()
    if not legacy:
        return default

    ordered = [legacy]
    for m in default:
        if m != legacy:
            ordered.append(m)
    return ordered


# ── Client builders ───────────────────────────────────────────────────────

def _build_groq(model: str, role: str, timeout: Optional[float]) -> Optional[ChatGroq]:
    key = os.getenv("GROQ_API_KEY")
    if not key:
        return None
    kwargs = dict(_ROLE_GROQ_KWARGS[role])
    # No client-side retry: langchain would sleep through a 429 and look like a
    # 135-second hang; the fallback walk moves to the next model instead. See
    # the note in notes_service._invoke.
    kwargs["max_retries"] = 0
    if timeout is not None:
        kwargs["timeout"] = timeout
    return ChatGroq(api_key=key, model=model, **kwargs)


def _build_gemini(model: str, role: str, timeout: Optional[float]) -> Optional[ChatGoogleGenerativeAI]:
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        return None
    kwargs = dict(_ROLE_GEMINI_KWARGS[role])
    kwargs["max_retries"] = 1
    if timeout is not None:
        kwargs["timeout"] = timeout
    return ChatGoogleGenerativeAI(model=model, google_api_key=key, **kwargs)


def build_groq_chain(role: str, timeout: Optional[float] = None) -> List[ChatGroq]:
    """Ordered Groq clients for the given role. Empty if no key is set."""
    if role == "answer":
        models = _resolve_model_list("GROQ_ANSWER_MODELS", "GROQ_ANSWER_MODEL", _DEFAULT_GROQ_ANSWER_MODELS)
    elif role == "intent":
        models = _resolve_model_list("GROQ_INTENT_MODELS", "GROQ_INTENT_MODEL", _DEFAULT_GROQ_INTENT_MODELS)
    elif role == "notes":
        models = _resolve_model_list("GROQ_NOTES_MODELS", "GROQ_NOTES_MODEL",
                                     os.getenv("GROQ_ANSWER_MODELS") or _DEFAULT_GROQ_ANSWER_MODELS)
    elif role == "quiz":
        models = _resolve_model_list("GROQ_QUIZ_MODELS", "GROQ_QUIZ_MODEL",
                                     os.getenv("GROQ_ANSWER_MODELS") or _DEFAULT_GROQ_ANSWER_MODELS)
    else:
        raise ValueError(f"unknown role: {role}")

    chain: List[ChatGroq] = []
    for m in models:
        client = _build_groq(m, role, timeout)
        if client is not None:
            chain.append(client)
    return chain


def build_gemini_chain(role: str, timeout: Optional[float] = None) -> List[ChatGoogleGenerativeAI]:
    """Ordered Gemini clients for the given role. Empty if no key is set."""
    env_key = {
        "answer": "GEMINI_ANSWER_MODELS",
        "intent": "GEMINI_INTENT_MODELS",
        "notes":  "GEMINI_NOTES_MODELS",
        "quiz":   "GEMINI_QUIZ_MODELS",
    }[role]
    legacy_key = {
        "answer": "GEMINI_ANSWER_MODEL",
        "intent": "GEMINI_INTENT_MODEL",
        "notes":  "GEMINI_NOTES_MODEL",
        "quiz":   "GEMINI_QUIZ_MODEL",
    }[role]
    models = _resolve_model_list(env_key, legacy_key, _DEFAULT_GEMINI_MODELS)

    chain: List[ChatGoogleGenerativeAI] = []
    for m in models:
        client = _build_gemini(m, role, timeout)
        if client is not None:
            chain.append(client)
    return chain


def build_full_chain(role: str, *, groq_timeout: Optional[float] = None,
                     gemini_timeout: Optional[float] = None) -> List[Any]:
    """Groq clients first (fast), then Gemini flashes (slow but rate-loose)."""
    return build_groq_chain(role, groq_timeout) + build_gemini_chain(role, gemini_timeout)


# ── The walk ──────────────────────────────────────────────────────────────

def _client_label(client: Any) -> str:
    model = getattr(client, "model", None) or getattr(client, "model_name", None) or "?"
    provider = "Groq" if isinstance(client, ChatGroq) else (
        "Gemini" if isinstance(client, ChatGoogleGenerativeAI) else type(client).__name__
    )
    return f"{provider}[{model}]"


async def ainvoke_with_fallback(chain: Sequence[Any], messages: Iterable[BaseMessage],
                                *, timeout: Optional[float] = None,
                                label: str = "LLM") -> Any:
    """Try each client in ``chain`` in order; return the first response.

    ``timeout`` (seconds) races each individual call, so one model that never
    answers cannot hold up the whole chain. A per-call timeout on top of the
    client's own is defence in depth: the langchain clients occasionally hang
    past their configured timeout when the socket stalls mid-stream.
    """
    if not chain:
        raise RuntimeError(f"{label}: no models configured")

    errors: List[str] = []
    msg_list = list(messages)

    for client in chain:
        tag = _client_label(client)
        try:
            if timeout is not None:
                return await asyncio.wait_for(client.ainvoke(msg_list), timeout)
            return await client.ainvoke(msg_list)
        except asyncio.TimeoutError:
            errors.append(f"{tag}: timed out after {timeout:.0f}s")
            print(f"[{label}] {tag} timed out; trying next model")
        except Exception as e:
            errors.append(f"{tag}: {str(e)[:200]}")
            print(f"[{label}] {tag} failed ({str(e)[:200]}); trying next model")

    raise RuntimeError(f"{label}: every model failed — {'; '.join(errors)}")
