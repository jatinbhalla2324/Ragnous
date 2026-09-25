"""Per-route rate limits using SlowAPI.

Attached to the app in `main.py` via `attach_rate_limiter(app)`. Individual
routes opt in with `@limiter.limit("10/minute")` — no global cap by default,
since ceilings that fit an easy home connection strangle a classroom of 30
students behind one router.

Keying is by client IP by default (SlowAPI's `get_remote_address`); once auth
lands, swap the key function for `f"user:{req.state.user_id}"` so a shared IP
doesn't punish everyone. Because we deliberately deleted auth from the FE
this session, the IP key is what we've got.
"""

from __future__ import annotations

from fastapi import FastAPI
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address


limiter = Limiter(key_func=get_remote_address, default_limits=[])


def attach_rate_limiter(app: FastAPI) -> None:
    """Wire the limiter into `app`. Idempotent — safe on hot reload."""
    if getattr(app.state, "_slowapi_attached", False):
        return
    app.state.limiter = limiter
    app.add_middleware(SlowAPIMiddleware)

    @app.exception_handler(RateLimitExceeded)
    async def _rate_limit_handler(_request, exc: RateLimitExceeded):
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=429,
            content={"detail": f"Rate limit exceeded: {exc.detail}"},
        )

    app.state._slowapi_attached = True
