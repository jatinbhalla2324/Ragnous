"""Structured logger for RAGNOUS.

The existing code prints tagged lines ("[RAG] …", "[GRAPH SYNTH] …"). Those
stay readable — this module wraps them so tools that expect JSON (Datadog,
Loki, `jq` on a captured log) can consume the same lines.

`log.info("event", key=value)` writes both to stderr (human-readable) and to
Python's stdlib logging (structured). We do not import `structlog` — it's a
transitive that would balloon the container image; a small wrapper on
`logging.getLogger` covers 95% of the value.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any


_CONFIGURED = False


def configure_logging() -> None:
    """Configure stdlib logging once at process start."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s :: %(message)s",
        "%H:%M:%S",
    ))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    _CONFIGURED = True


def get_logger(name: str) -> "TaggedLogger":
    """Return a logger whose `.info("event", k=v)` emits a JSON payload."""
    configure_logging()
    return TaggedLogger(logging.getLogger(name))


class TaggedLogger:
    """Thin wrapper that JSON-encodes kwargs into the log message."""

    def __init__(self, backing: logging.Logger) -> None:
        self._log = backing

    def _emit(self, level: int, event: str, **fields: Any) -> None:
        if fields:
            payload = json.dumps(fields, default=str, sort_keys=True)
            self._log.log(level, "%s %s", event, payload)
        else:
            self._log.log(level, "%s", event)

    def debug(self, event: str, **fields: Any) -> None:
        self._emit(logging.DEBUG, event, **fields)

    def info(self, event: str, **fields: Any) -> None:
        self._emit(logging.INFO, event, **fields)

    def warning(self, event: str, **fields: Any) -> None:
        self._emit(logging.WARNING, event, **fields)

    def error(self, event: str, **fields: Any) -> None:
        self._emit(logging.ERROR, event, **fields)

    def exception(self, event: str, **fields: Any) -> None:
        self._log.exception("%s %s", event, json.dumps(fields, default=str, sort_keys=True))
