"""
Serves a 3D model without ever storing one.

The mesh is fetched from upstream and streamed straight to the browser, so the
bytes live only for the length of the request — nothing is written to disk and
there is nothing to clean up afterwards.

The artifact saved in a student's chat history points at a key here, never at
an upstream URL. That matters because Sketchfab's signed URLs expire after 300
seconds: a stored URL would be dead by the next lesson, while a key can simply
be resolved again on demand.
"""

import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.services import model3d_service

router = APIRouter()

# sk_<32 hex> for a Sketchfab model, tp_<16 hex> for a generated one.
_KEY_PATTERN = re.compile(r"^(sk_[0-9a-f]{32}|tp_[0-9a-f]{16})$")


@router.get("/{filename}")
async def get_model(filename: str):
    key = filename[:-4] if filename.endswith(".glb") else filename

    # The key is interpolated into an upstream request, so it is matched
    # against an exact shape rather than merely escaped.
    if not _KEY_PATTERN.match(key):
        raise HTTPException(status_code=404, detail="Unknown model")

    source = await model3d_service.source_url_for(key)
    if not source:
        # A generated model whose upstream link has lapsed, or a lookup that
        # upstream refused. The viewer shows its "could not load" state.
        raise HTTPException(status_code=404, detail="Model is no longer available")

    try:
        stream = model3d_service.stream_model(source)
    except Exception as e:
        print(f"[3D] streaming {key} failed: {e}")
        raise HTTPException(status_code=502, detail="Could not fetch the model")

    return StreamingResponse(
        stream,
        media_type="model/gltf-binary",
        headers={
            # Never stored by us, and never stored by the browser either.
            "Cache-Control": "no-store",
            "Content-Disposition": f'inline; filename="{key}.glb"',
        },
    )
