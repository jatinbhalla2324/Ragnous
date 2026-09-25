"""JWT + password helpers for a future auth layer.

The frontend deliberately removed its login/signup pages this session — this
project is not a product, so nothing consumes these functions today. They
still live here because:

- The moment the project graduates into anything multi-tenant (a classroom
  deployment, a school pilot), a working `hash_password` / `verify_password`
  / `create_access_token` set is the difference between an evening's work
  and a week's.
- `python-jose` and `passlib[bcrypt]` are already in `requirements.txt`, so
  we lose nothing by writing the helpers.

All three functions are pure — no I/O, no globals — so unit tests need no
fixtures.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from jose import JWTError, jwt
from passlib.context import CryptContext


_pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain: str) -> str:
    return _pwd_ctx.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd_ctx.verify(plain, hashed)


def _secret() -> str:
    key = os.getenv("JWT_SECRET")
    if not key:
        raise RuntimeError("JWT_SECRET is not configured")
    return key


def _algorithm() -> str:
    return os.getenv("JWT_ALGORITHM", "HS256")


def create_access_token(subject: str, extra: Dict[str, Any] | None = None,
                        ttl_minutes: int = 60) -> str:
    """Standard short-lived access token. `subject` is the user id."""
    payload: Dict[str, Any] = {
        "sub": subject,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, _secret(), algorithm=_algorithm())


def decode_token(token: str) -> Dict[str, Any]:
    """Return the payload or raise `JWTError` — the caller decides how to
    surface an invalid token (401 for FastAPI, silent for background tasks)."""
    return jwt.decode(token, _secret(), algorithms=[_algorithm()])


__all__ = [
    "JWTError",
    "create_access_token",
    "decode_token",
    "hash_password",
    "verify_password",
]
