"""
Reading what a student attaches to a chat turn.

The tutor answers from text, so every attachment has to become either text the
prompt can carry or an image part a vision model can look at. This module owns
that conversion and nothing else: it takes raw bytes off the wire and returns a
plain dict the chat endpoint can hand to an LLM.

Two deliberate choices:

  * Nothing is stored. The parsed result goes back to the browser, which sends
    it along with the next chat request. The rest of this backend is stateless
    for the same reason — there is no session table to hang an upload off.
  * Images are re-encoded before they leave here. A phone photo is 4000px wide
    and several megabytes; the vision model gains nothing above ~1568px and the
    browser has to hold the base64 in memory until the turn is sent.
"""

import base64
import html
import io
import re
import zipfile
from typing import Optional

from pypdf import PdfReader

# Per-file and per-request ceilings. A textbook chapter photographed at full
# resolution is comfortably under 20 MB; anything larger is a scan of a whole
# book and would not fit a prompt anyway.
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_TOTAL_BYTES = 40 * 1024 * 1024
MAX_FILES = 6

# How much extracted text one document may contribute to a prompt. Past this
# the context window is the binding constraint, not the file.
MAX_TEXT_CHARS = 30_000

# Longest edge an image is resized to before base64-encoding.
IMAGE_MAX_EDGE = 1568
IMAGE_JPEG_QUALITY = 88
# Keep PNG (sharper for diagrams and screenshots of text) only while it stays
# small; above this a photo-like PNG is re-encoded as JPEG instead.
PNG_KEEP_MAX_BYTES = 1_500_000

# A PDF that yields fewer characters than this per page is almost certainly a
# scan. Text extraction has nothing to give, so the pages themselves have to be
# shown to a vision model.
SCANNED_CHARS_PER_PAGE = 40
# Largest PDF we are willing to ship to the model as raw bytes.
NATIVE_PDF_MAX_BYTES = 8 * 1024 * 1024

TEXT_MIMES = {
    "text/plain",
    "text/markdown",
    "text/csv",
    "text/tab-separated-values",
    "application/json",
    "application/xml",
    "text/xml",
    "text/html",
}

TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".csv", ".tsv", ".json",
    ".xml", ".html", ".htm", ".log", ".rtf",
}

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class AttachmentError(ValueError):
    """A file we cannot read. The message is shown to the student verbatim."""


def _extension(filename: str) -> str:
    dot = filename.rfind(".")
    return filename[dot:].lower() if dot != -1 else ""


def _clip(text: str) -> tuple[str, bool]:
    """Trim to MAX_TEXT_CHARS, reporting whether anything was dropped."""
    if len(text) <= MAX_TEXT_CHARS:
        return text, False
    return text[:MAX_TEXT_CHARS].rstrip(), True


def _tidy(text: str) -> str:
    """Collapse the runs of blank lines that PDF and DOCX extraction leave behind."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ── Images ──────────────────────────────────────────────────────────────────

def _read_image(filename: str, mime: str, data: bytes) -> dict:
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:  # pragma: no cover - Pillow is a hard dependency
        raise AttachmentError("Image support is not installed on the server.") from exc

    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as exc:
        raise AttachmentError(
            f"Could not read “{filename}” as an image. PNG, JPG or WEBP work best."
        ) from exc

    # Phone cameras record orientation in EXIF rather than in the pixels; without
    # this a photo taken in portrait reaches the model on its side.
    img = ImageOps.exif_transpose(img)
    width, height = img.size

    if max(img.size) > IMAGE_MAX_EDGE:
        img.thumbnail((IMAGE_MAX_EDGE, IMAGE_MAX_EDGE), Image.LANCZOS)

    keep_png = img.format == "PNG" or _extension(filename) == ".png"
    out = io.BytesIO()
    if keep_png:
        png_source = img if img.mode in ("RGB", "RGBA", "L") else img.convert("RGBA")
        png_source.save(out, format="PNG", optimize=True)
        if out.tell() <= PNG_KEEP_MAX_BYTES:
            encoded_mime = "image/png"
        else:
            keep_png = False
            out = io.BytesIO()

    if not keep_png:
        # Flatten onto white: JPEG has no alpha, and the default black backing
        # turns a transparent diagram into an unreadable silhouette.
        if img.mode in ("RGBA", "LA", "P"):
            flat = Image.new("RGB", img.size, (255, 255, 255))
            rgba = img.convert("RGBA")
            flat.paste(rgba, mask=rgba.split()[-1])
            img = flat
        elif img.mode != "RGB":
            img = img.convert("RGB")
        img.save(out, format="JPEG", quality=IMAGE_JPEG_QUALITY, optimize=True)
        encoded_mime = "image/jpeg"

    encoded = base64.b64encode(out.getvalue()).decode("ascii")

    return {
        "kind": "image",
        "mime": encoded_mime,
        "text": "",
        "data_url": f"data:{encoded_mime};base64,{encoded}",
        "page_count": None,
        "truncated": False,
        "width": width,
        "height": height,
        "note": "",
    }


# ── PDF ─────────────────────────────────────────────────────────────────────

def _read_pdf(filename: str, data: bytes) -> dict:
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:
        raise AttachmentError(f"Could not open “{filename}” — the PDF looks damaged.") from exc

    if getattr(reader, "is_encrypted", False):
        # An empty user password is common for "print-protected" papers and
        # decrypts silently; a real password is a dead end.
        try:
            if reader.decrypt("") == 0:
                raise AttachmentError(
                    f"“{filename}” is password-protected. Remove the password and try again."
                )
        except AttachmentError:
            raise
        except Exception as exc:
            raise AttachmentError(
                f"“{filename}” is password-protected. Remove the password and try again."
            ) from exc

    pages: list[str] = []
    for index, page in enumerate(reader.pages, start=1):
        try:
            page_text = page.extract_text() or ""
        except Exception:
            page_text = ""
        page_text = _tidy(page_text)
        if page_text:
            pages.append(f"--- Page {index} ---\n{page_text}")

    page_count = len(reader.pages)
    body = _tidy("\n\n".join(pages))
    text, truncated = _clip(body)

    # Little or no extractable text means a scan. Ship the bytes themselves so a
    # vision model can read the pages; saying "empty file" would be wrong.
    scanned = len(body) < max(200, SCANNED_CHARS_PER_PAGE * page_count)
    data_url = ""
    note = ""
    if scanned:
        if len(data) <= NATIVE_PDF_MAX_BYTES:
            encoded = base64.b64encode(data).decode("ascii")
            data_url = f"data:application/pdf;base64,{encoded}"
            note = "Scanned PDF — the pages are read as images."
        else:
            raise AttachmentError(
                f"“{filename}” looks like a scan with no selectable text, and is too "
                "large to read as images. Try attaching the pages as photos instead."
            )

    return {
        "kind": "document",
        "mime": "application/pdf",
        "text": text,
        "data_url": data_url,
        "page_count": page_count,
        "truncated": truncated,
        "width": None,
        "height": None,
        "note": note,
    }


# ── DOCX ────────────────────────────────────────────────────────────────────

def _read_docx(filename: str, data: bytes) -> dict:
    """
    Pull the visible text out of a .docx.

    A .docx is a zip of XML, and the paragraph text lives in word/document.xml.
    Reading it directly keeps this off a new dependency for what amounts to
    "turn paragraph tags into newlines and drop the rest of the markup".
    """
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            xml = archive.read("word/document.xml").decode("utf-8", "replace")
    except KeyError as exc:
        raise AttachmentError(f"“{filename}” is not a readable Word document.") from exc
    except Exception as exc:
        raise AttachmentError(f"Could not open “{filename}” — the file looks damaged.") from exc

    xml = re.sub(r"<w:br\b[^>]*/?>", "\n", xml)
    xml = re.sub(r"<w:tab\b[^>]*/?>", "\t", xml)
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"</w:tr>", "\n", xml)
    xml = re.sub(r"</w:tc>", "\t", xml)
    body = _tidy(html.unescape(re.sub(r"<[^>]+>", "", xml)))

    if not body:
        raise AttachmentError(f"“{filename}” has no readable text in it.")

    text, truncated = _clip(body)
    return {
        "kind": "document",
        "mime": DOCX_MIME,
        "text": text,
        "data_url": "",
        "page_count": None,
        "truncated": truncated,
        "width": None,
        "height": None,
        "note": "",
    }


# ── Plain text ──────────────────────────────────────────────────────────────

def _read_text(filename: str, mime: str, data: bytes) -> dict:
    body = _tidy(data.decode("utf-8", "replace"))
    if not body:
        raise AttachmentError(f"“{filename}” is empty.")
    text, truncated = _clip(body)
    return {
        "kind": "document",
        "mime": mime or "text/plain",
        "text": text,
        "data_url": "",
        "page_count": None,
        "truncated": truncated,
        "width": None,
        "height": None,
        "note": "",
    }


# ── Entry point ─────────────────────────────────────────────────────────────

def parse_attachment(filename: str, mime: Optional[str], data: bytes) -> dict:
    """
    Turn one uploaded file into the shape the chat endpoint consumes.

    Raises AttachmentError with a message meant for the student when the file
    cannot be read; the caller reports it against that one file and keeps the
    rest of the batch.
    """
    filename = (filename or "file").strip() or "file"
    mime = (mime or "").split(";")[0].strip().lower()
    extension = _extension(filename)

    if not data:
        raise AttachmentError(f"“{filename}” is empty.")
    if len(data) > MAX_FILE_BYTES:
        raise AttachmentError(
            f"“{filename}” is {len(data) / 1_048_576:.1f} MB. The limit is "
            f"{MAX_FILE_BYTES // 1_048_576} MB per file."
        )

    if mime.startswith("image/") or extension in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}:
        parsed = _read_image(filename, mime, data)
    elif mime == "application/pdf" or extension == ".pdf":
        parsed = _read_pdf(filename, data)
    elif mime == DOCX_MIME or extension == ".docx":
        parsed = _read_docx(filename, data)
    elif mime in TEXT_MIMES or extension in TEXT_EXTENSIONS:
        parsed = _read_text(filename, mime, data)
    elif extension == ".doc":
        raise AttachmentError(
            f"“{filename}” is in the old .doc format. Save it as .docx or PDF and try again."
        )
    else:
        raise AttachmentError(
            f"“{filename}” is not a file type I can read. Attach a PDF, Word document, "
            "text file, or an image."
        )

    parsed["name"] = filename
    parsed["size"] = len(data)
    return parsed
