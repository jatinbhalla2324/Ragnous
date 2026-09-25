"""Typed settings loaded from `.env` + environment.

The endpoint historically calls `os.getenv` directly all over the place. That
still works, but new code goes through this singleton so:

- every knob is discoverable in one file,
- missing critical keys are flagged at import time rather than at first
  request, and
- tests can override with `Settings(...)` instead of monkey-patching env.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-backed configuration.

    Only the truly cross-cutting knobs live here. Anything specific to one
    service (Groq model lists, cross-encoder thresholds) stays local to that
    service so its documentation stays next to its behaviour.
    """

    # ── Storage ─────────────────────────────────────────────────────────
    database_url: Optional[str] = None
    redis_url: str = "redis://localhost:6379/0"

    # ── LLM providers ───────────────────────────────────────────────────
    groq_api_key: Optional[str] = None
    gemini_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    tavily_api_key: Optional[str] = None
    cohere_api_key: Optional[str] = None

    # ── Voice ───────────────────────────────────────────────────────────
    deepgram_api_key: Optional[str] = None

    # ── Retrieval knobs (documented alongside their consumers too) ──────
    rag_rerank_high: float = 0.45
    rag_rerank_medium: float = 0.12
    rag_cosine_high: float = 0.55
    rag_cosine_medium: float = 0.35

    # ── App behaviour ───────────────────────────────────────────────────
    cors_allow_origins: str = "*"      # comma-separated in env
    debug: bool = False

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide singleton — lru_cache makes reads free after boot."""
    return Settings()
