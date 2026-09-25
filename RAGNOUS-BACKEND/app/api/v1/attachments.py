"""
The endpoint behind the paperclip in the composer.

The browser posts the files the moment they are dropped or picked, so the
student sees "read" or a clear error while they are still typing their
question, rather than discovering a bad file after pressing send. What comes
back is the whole parsed attachment: the chat request echoes it straight back,
which keeps this backend as stateless as the rest of it.
"""

import asyncio
from typing import List, Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from app.services.file_service import (
    MAX_FILES,
    MAX_TOTAL_BYTES,
    AttachmentError,
    parse_attachment,
)

router = APIRouter()


class ParsedAttachment(BaseModel):
    name: str
    mime: str
    kind: str                       # "image" | "document"
    size: int
    text: str = ""
    # Images always carry one; documents only when the text could not be
    # extracted and the file itself has to be shown to a vision model.
    data_url: str = ""
    page_count: Optional[int] = None
    truncated: bool = False
    width: Optional[int] = None
    height: Optional[int] = None
    note: str = ""


class RejectedAttachment(BaseModel):
    name: str
    error: str


class AttachmentsResponse(BaseModel):
    attachments: List[ParsedAttachment]
    rejected: List[RejectedAttachment]


@router.post("", response_model=AttachmentsResponse)
async def upload_attachments(files: List[UploadFile] = File(...)):
    if not files:
        raise HTTPException(status_code=400, detail="No files were uploaded.")
    if len(files) > MAX_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"You can attach up to {MAX_FILES} files at a time.",
        )

    accepted: List[ParsedAttachment] = []
    rejected: List[RejectedAttachment] = []
    total = 0

    for upload in files:
        name = upload.filename or "file"
        try:
            data = await upload.read()
        finally:
            await upload.close()

        total += len(data)
        if total > MAX_TOTAL_BYTES:
            rejected.append(RejectedAttachment(
                name=name,
                error=f"Together these files exceed the {MAX_TOTAL_BYTES // 1_048_576} MB limit.",
            ))
            continue

        try:
            # Pillow and pypdf are both blocking and can take a second on a big
            # scan, so they run off the event loop.
            parsed = await asyncio.to_thread(parse_attachment, name, upload.content_type, data)
            accepted.append(ParsedAttachment(**parsed))
        except AttachmentError as e:
            rejected.append(RejectedAttachment(name=name, error=str(e)))
        except Exception as e:
            print(f"[ATTACHMENT ERROR] {name}: {e}")
            rejected.append(RejectedAttachment(
                name=name,
                error=f"Something went wrong while reading “{name}”.",
            ))

    if not accepted and rejected:
        # Every file failed — surface the reasons rather than an empty success.
        raise HTTPException(
            status_code=422,
            detail={"rejected": [r.model_dump() for r in rejected]},
        )

    return AttachmentsResponse(attachments=accepted, rejected=rejected)
