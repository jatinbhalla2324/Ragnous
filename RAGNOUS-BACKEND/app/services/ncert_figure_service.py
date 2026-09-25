"""NCERT figure extraction.

Study notes illustrate themselves with the *student's own textbook* figures:
every image in a generated PDF is cropped straight out of the NCERT chapter
the surrounding text was retrieved from. Nothing is fetched from the open web
here, so a diagram can never be off-syllabus, off-class, or a stock photo of
the wrong thing.

How a figure is found
---------------------
1. `ncert_chunks.chapter` holds the NCERT file code the passage came from
   ("jesc106"). That code names a real PDF inside data/books/**, so a citation
   resolves to a page of a book on disk.
2. On that page, a caption block ("Figure 6.3 Human brain") anchors the figure.
3. The figure itself is the connected cluster of vector drawings and raster
   images sitting directly above the caption.

The one rule that keeps crops honest: **the crop may never overlap a paragraph
of body text.** Growing stops at a paragraph rather than swallowing it, which
is what separates a clean diagram from a screenshot of half a page. An earlier
version grew first and trimmed afterwards, and produced crops containing the
chapter title and three paragraphs of prose.

Everything is cached: a chapter is parsed once, its figures are written to
data/figures/<chapter>/ with a manifest, and later calls read the manifest.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import urllib.request
import zipfile
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

import pymupdf

# ── Where the books live ──────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data"
BOOK_DIRS = [DATA_DIR / "books", DATA_DIR / "math_books"]
# Chapter PDFs shipped inside zips are unpacked here once, so the zip is never
# opened on a request path.
UNPACK_DIR = DATA_DIR / "books_unpacked"
FIGURE_DIR = DATA_DIR / "figures"

RENDER_DPI = int(os.getenv("NOTES_FIGURE_DPI", "160"))

# Only some classes' books are on disk (data/books holds Class 10 plus two
# Class 8 geography volumes), but ncert_chunks covers Classes 8-12 — those
# chapters were ingested from files nobody kept. NCERT publishes every chapter
# at a stable URL keyed by the same code, so a missing book is fetched from the
# publisher itself rather than degrading to a web image search: same book, same
# class, same edition.
NCERT_PDF_URL = os.getenv("NCERT_PDF_URL", "https://ncert.nic.in/textbook/pdf/{code}.pdf")
ALLOW_DOWNLOAD = os.getenv("NOTES_ALLOW_NCERT_DOWNLOAD", "1") not in ("0", "false", "False")
DOWNLOAD_DIR = UNPACK_DIR / "ncert_download"
DOWNLOAD_TIMEOUT = int(os.getenv("NCERT_PDF_TIMEOUT", "45"))
# A real chapter is megabytes; anything tiny is an error page wearing a .pdf.
MIN_PDF_BYTES = 40_000

# ── Caption grammar ───────────────────────────────────────────────────────
# NCERT captions read "Figure 6.3 Human brain" in Science and "Fig. 1 — The
# Dream of Worldwide Democratic and Social Republics…" in History, so the
# number is chapter-scoped in one book and plain in the other. Bold is faked by
# overprinting the same string 4-5 times, so the tag has to be de-duplicated
# out of the extracted text.
_CAPTION_RE = re.compile(
    r"^\s*(?:Figure|Fig\.?|Map|Photograph)\s*(\d+(?:\.\d+)?)\s*[—–\-:.]?\s+\S",
    re.I,
)

# A text block this long is a paragraph, not a figure label.
_BODY_WORDS = 12
_MERGE_GAP = 6.0          # pt; how far apart two strokes can be and still be
                          # part of the same drawing
_MIN_W, _MIN_H = 75.0, 55.0
_MAX_FIGURE_FRACTION = 0.75   # of page height


@dataclass
class Figure:
    chapter: str
    page: int
    number: str          # "6.3"
    caption: str         # "Figure 6.3 Human brain"
    path: str            # PNG on disk
    width: int
    height: int

    @property
    def label(self) -> str:
        return self.caption


# ── Book index: chapter code -> PDF on disk ───────────────────────────────
_INDEX: Optional[Dict[str, Path]] = None
_INDEX_LOCK = threading.Lock()


def _unpack_zips() -> None:
    """Extract every book zip once into data/books_unpacked/<zip stem>/."""
    UNPACK_DIR.mkdir(parents=True, exist_ok=True)
    for root in BOOK_DIRS:
        if not root.exists():
            continue
        for zp in root.rglob("*.zip"):
            target = UNPACK_DIR / zp.stem
            marker = target / ".unpacked"
            if marker.exists():
                continue
            try:
                target.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(zp) as zf:
                    for member in zf.namelist():
                        if not member.lower().endswith(".pdf"):
                            continue
                        # Flatten: the code in the filename is the only key we
                        # need, and nested folders differ between zips.
                        name = Path(member).name
                        with zf.open(member) as src, open(target / name, "wb") as dst:
                            shutil.copyfileobj(src, dst)
                marker.write_text("ok")
            except Exception as e:  # a corrupt zip must not take notes down
                print(f"[FIGURES] could not unpack {zp.name}: {e}")


def _build_index() -> Dict[str, Path]:
    _unpack_zips()
    index: Dict[str, Path] = {}
    roots = BOOK_DIRS + [UNPACK_DIR]
    for root in roots:
        if not root.exists():
            continue
        for pdf in root.rglob("*.pdf"):
            index.setdefault(pdf.stem.lower(), pdf)
    print(f"[FIGURES] book index: {len(index)} chapter PDFs on disk")
    return index


_CODE_RE = re.compile(r"^[a-z]{2,6}\d{0,3}[a-z0-9]{0,3}$")


def _download_chapter(code: str) -> Optional[Path]:
    """Fetch one chapter PDF from ncert.nic.in into the local cache."""
    if not ALLOW_DOWNLOAD or not _CODE_RE.match(code):
        return None
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    target = DOWNLOAD_DIR / f"{code}.pdf"
    if target.exists() and target.stat().st_size >= MIN_PDF_BYTES:
        return target

    url = NCERT_PDF_URL.format(code=code)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "RAGNOUS/1.0 (study notes)"})
        with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT) as resp:
            data = resp.read()
    except Exception as e:
        print(f"[FIGURES] could not fetch {url}: {e}")
        return None

    if len(data) < MIN_PDF_BYTES or not data.startswith(b"%PDF"):
        print(f"[FIGURES] {url} did not return a PDF ({len(data)} bytes)")
        return None
    target.write_bytes(data)
    print(f"[FIGURES] downloaded {code}.pdf from NCERT ({len(data) // 1024} KB)")
    with _INDEX_LOCK:
        if _INDEX is not None:
            _INDEX[code] = target
    return target


def chapter_pdf(chapter_code: str) -> Optional[Path]:
    """The PDF behind an `ncert_chunks.chapter` value.

    Local books first; otherwise the same chapter from ncert.nic.in.
    """
    global _INDEX
    if not chapter_code:
        return None
    code = str(chapter_code).strip().lower()
    with _INDEX_LOCK:
        if _INDEX is None:
            _INDEX = _build_index()
        found = _INDEX.get(code)
    return found or _download_chapter(code)


# ── Page geometry helpers ─────────────────────────────────────────────────
def _area(r: pymupdf.Rect) -> float:
    return max(0.0, r.width) * max(0.0, r.height)


def _inter(a: pymupdf.Rect, b: pymupdf.Rect) -> float:
    r = pymupdf.Rect(a) & b
    return 0.0 if r.is_empty else _area(r)


def _x_overlap(a: pymupdf.Rect, b: pymupdf.Rect) -> float:
    return max(0.0, min(a.x1, b.x1) - max(a.x0, b.x0))


def _thicken(r: pymupdf.Rect) -> pymupdf.Rect:
    """Give a hairline some thickness.

    A leader line from a diagram to its label is a rect of zero height, and
    PyMuPDF reports *no* intersection for an empty rect — so every label line
    was invisible to the cluster and figures came out with their labels sliced
    off. Half a point in each direction is enough to make them count.
    """
    if r.width < 1:
        r.x0, r.x1 = r.x0 - 0.5, r.x1 + 0.5
    if r.height < 1:
        r.y0, r.y1 = r.y0 - 0.5, r.y1 + 0.5
    return r


# Overprinted bold leaves the tag repeated, sometimes with a letter split off
# it ("Fig. 4.9: F ig. 4.9: Fig. 4.9: Groundnut…"), so repeats are stripped in
# a loop rather than once.
_TAG_RE = re.compile(
    r"^\s*(?:[A-Za-z]\s+)?(?:Figure|Fig|Map|Photograph|ig)\.?\s*(\d+(?:\.\d+)?)\s*[:—–\-.]*\s*",
    re.I,
)
_SPLIT_INITIAL_RE = re.compile(r"^([A-Za-z])\s+(?=[a-z]{2})")


def _clean_caption(text: str) -> tuple:
    """('Figure 6.3 Human brain', '6.3') from the page's repeated-bold soup."""
    t = " ".join(text.split())
    number = ""
    while True:
        m = _TAG_RE.match(t)
        if not m or not t[m.end():].strip():
            break
        number = m.group(1)
        t = t[m.end():]
    t = _SPLIT_INITIAL_RE.sub(r"\1", t)
    # NCERT stamps every page with a reprint line; it is not part of a caption.
    t = re.sub(r"\s*Reprint\s*\d{4}-\d{2}\s*$", "", t).strip(" .:-—–")
    label = f"Figure {number}" if number else "Figure"
    return (f"{label} {t}".strip(), number)


def _blocks(page: pymupdf.Page):
    """(text blocks, image rects) for a page."""
    texts, images = [], []
    for b in page.get_text("dict")["blocks"]:
        rect = pymupdf.Rect(b["bbox"])
        if b["type"] != 0:
            images.append(rect)
            continue
        txt = " ".join(s["text"] for line in b["lines"] for s in line["spans"])
        txt = " ".join(txt.split())
        if txt:
            texts.append((rect, txt))
    return texts, images


def _text_panels(filled: Sequence[pymupdf.Rect], texts) -> List[pymupdf.Rect]:
    """Boxes that exist to hold text, not to draw a figure.

    NCERT's "Activity" and "Do You Know?" panels are tinted rectangles whose
    contents are short bulleted lines — no single line is long enough to read
    as a paragraph, so the paragraph test above misses them and a figure beside
    one grew straight through the gutter and swallowed it.

    Two things separate a panel from a filled shape inside a diagram: a panel
    is a large tinted rectangle, and the text it contains is *sentences*.
    Figure labels ("Cerebrum", "H2 gas") average one or two words, so the mean
    words-per-line test is what keeps this from eating real diagrams.
    """
    panels = []
    for r in filled:
        if _area(r) < 3000:
            continue
        inside = [t for tr, t in texts if _inter(r, tr) > 0.7 * _area(tr)]
        words = sum(len(t.split()) for t in inside)
        if words < 12 or not inside:
            continue
        if words / len(inside) < 3.0:
            continue          # a cloud of one-word labels: that is a diagram
        panels.append(r)
    return panels


def _graphics(page: pymupdf.Page, body: Sequence[pymupdf.Rect],
              images: Sequence[pymupdf.Rect], texts) -> tuple:
    """(figure strokes, regions the crop must not enter).

    Dropped from the strokes: the page background, any box drawn *behind* a
    paragraph, and the text panels above — without those exclusions a figure
    printed beside an Activity box grew across the gutter and swallowed it.
    """
    page_area = _area(page.rect)
    raw: List[pymupdf.Rect] = []
    filled: List[pymupdf.Rect] = []
    for d in page.get_drawings():
        r = _thicken(pymupdf.Rect(d["rect"]))
        if r.width < 2 and r.height < 2:
            continue
        if _area(r) > 0.45 * page_area:
            continue
        raw.append(r)
        fill = d.get("fill")
        if d.get("type") in ("f", "fs") and fill and min(fill) < 0.97:
            filled.append(r)
    for r in images:
        if _area(r) > 0.9 * page_area:
            continue
        raw.append(r)

    panels = _text_panels(filled, texts)
    forbidden = list(body) + panels

    keep = []
    for r in raw:
        if any(_inter(r, b) > 0.45 * _area(b) for b in body):
            continue
        if any(_inter(r, p) > 0.5 * _area(r) for p in panels):
            continue
        keep.append(r)
    return keep, forbidden


def _ceiling(cap_rect: pymupdf.Rect, captions, page: pymupdf.Page) -> float:
    """A figure never reaches above the caption of the figure above it."""
    y = page.rect.y0
    for r, *_ in captions:
        if r is cap_rect:
            continue
        if r.y1 <= cap_rect.y0 - 2 and _x_overlap(r, cap_rect) > 0.2 * min(r.width, cap_rect.width):
            y = max(y, r.y1)
    return y


def _seed(cap_rect: pymupdf.Rect, gfx: Sequence[pymupdf.Rect], y_lo: float,
          page: pymupdf.Page) -> Optional[pymupdf.Rect]:
    """The graphic this caption is captioning: nearest above, best overlap."""
    best, best_key = None, None
    for r in gfx:
        if r.y1 > cap_rect.y0 + 4 or r.y0 < y_lo - 2:
            continue
        ov = _x_overlap(r, cap_rect)
        if ov <= 0:
            continue
        key = (cap_rect.y0 - r.y1) - 0.15 * ov
        if best_key is None or key < best_key:
            best, best_key = r, key
    if best is not None:
        return best
    # Captions printed above their figure (NCERT does this for a few maps).
    for r in sorted(gfx, key=lambda r: r.y0):
        if r.y0 >= cap_rect.y1 - 4 and _x_overlap(r, cap_rect) > 0:
            if r.y0 - cap_rect.y1 < page.rect.height * 0.4:
                return r
    return None


def _grow(seed: pymupdf.Rect, gfx: Sequence[pymupdf.Rect],
          forbidden: Sequence[pymupdf.Rect], texts, cap_rect: pymupdf.Rect,
          y_lo: float, page: pymupdf.Page) -> pymupdf.Rect:
    """Flood-merge neighbouring strokes, refusing any growth that would put a
    paragraph inside the crop. That single refusal is what keeps figures clean."""
    max_h = page.rect.height * _MAX_FIGURE_FRACTION

    def allowed(rect: pymupdf.Rect) -> bool:
        if rect.height > max_h:
            return False
        return not any(_inter(rect, b) > 1.0 for b in forbidden)

    u = pymupdf.Rect(seed)
    for _ in range(14):
        grown = pymupdf.Rect(u.x0 - _MERGE_GAP, u.y0 - _MERGE_GAP,
                             u.x1 + _MERGE_GAP, u.y1 + _MERGE_GAP)
        changed = False
        for r in gfx:
            if r.y0 < y_lo - 2 or r.y0 > cap_rect.y1 + 4:
                continue
            if u.contains(r) or not grown.intersects(r):
                continue
            cand = pymupdf.Rect(u) | r
            if allowed(cand):
                u, changed = cand, True
        if not changed:
            break

    # Pull in the figure's own labels ("Cerebrum", "Axon") the same way.
    for r, t in texts:
        if len(t.split()) > 8 or _CAPTION_RE.match(t) or r.height > 70:
            continue
        near = pymupdf.Rect(u.x0 - 12, u.y0 - 12, u.x1 + 12, u.y1 + 12)
        if not near.intersects(r):
            continue
        cand = pymupdf.Rect(u) | r
        if allowed(cand) and _area(cand) < 1.35 * _area(u):
            u = cand
    return u


def _extract_page(page: pymupdf.Page, chapter: str, out_dir: Path,
                  page_no: int) -> List[Figure]:
    texts, images = _blocks(page)
    captions = [(r,) + _clean_caption(t) for r, t in texts
                if _CAPTION_RE.match(t) and len(t) > 9]
    if not captions:
        return []
    body = [r for r, t in texts if len(t.split()) >= _BODY_WORDS]
    gfx, forbidden = _graphics(page, body, images, texts)
    found: List[Figure] = []

    for cap_rect, caption, number in captions:
        y_lo = _ceiling(cap_rect, captions, page)
        seed = _seed(cap_rect, gfx, y_lo, page)
        if seed is None:
            continue
        rect = _grow(seed, gfx, forbidden, texts, cap_rect, y_lo, page)
        if cap_rect.y0 > rect.y0:
            rect.y1 = min(rect.y1, cap_rect.y0 - 3)
        rect &= page.rect
        if rect.width < _MIN_W or rect.height < _MIN_H:
            continue
        # Last defence: a crop holding real prose is not a figure.
        prose = sum(len(t.split()) for r, t in texts
                    if len(t.split()) >= _BODY_WORDS and _inter(rect, r) > 0.4 * _area(r))
        if prose > 25:
            continue

        number = number or str(page_no)
        padded = pymupdf.Rect(rect.x0 - 3, rect.y0 - 3, rect.x1 + 3, rect.y1 + 3) & page.rect
        pix = page.get_pixmap(clip=padded, dpi=RENDER_DPI)
        fname = f"p{page_no}_{number.replace('.', '-')}.png"
        fpath = out_dir / fname
        pix.save(str(fpath))
        found.append(Figure(
            chapter=chapter, page=page_no, number=number, caption=caption,
            path=str(fpath), width=pix.width, height=pix.height,
        ))
    return found


# ── Public API ────────────────────────────────────────────────────────────
_CHAPTER_CACHE: Dict[str, List[Figure]] = {}
_CHAPTER_LOCK = threading.Lock()


def figures_for_chapter(chapter_code: str, force: bool = False) -> List[Figure]:
    """Every figure in one NCERT chapter, cropped and cached on disk."""
    code = (chapter_code or "").strip().lower()
    if not code:
        return []
    with _CHAPTER_LOCK:
        if not force and code in _CHAPTER_CACHE:
            return _CHAPTER_CACHE[code]

    out_dir = FIGURE_DIR / code
    manifest = out_dir / "manifest.json"
    if manifest.exists() and not force:
        try:
            data = json.loads(manifest.read_text())
            figs = [Figure(**f) for f in data if Path(f["path"]).exists()]
            with _CHAPTER_LOCK:
                _CHAPTER_CACHE[code] = figs
            return figs
        except Exception as e:
            print(f"[FIGURES] manifest unreadable for {code}: {e}")

    pdf = chapter_pdf(code)
    if pdf is None:
        with _CHAPTER_LOCK:
            _CHAPTER_CACHE[code] = []
        return []

    out_dir.mkdir(parents=True, exist_ok=True)
    figs: List[Figure] = []
    try:
        doc = pymupdf.open(pdf)
        for i in range(doc.page_count):
            try:
                figs.extend(_extract_page(doc[i], code, out_dir, i + 1))
            except Exception as e:
                print(f"[FIGURES] {code} page {i+1} failed: {e}")
        doc.close()
    except Exception as e:
        print(f"[FIGURES] could not open {pdf}: {e}")

    try:
        manifest.write_text(json.dumps([asdict(f) for f in figs], indent=1))
    except Exception:
        pass
    print(f"[FIGURES] {code}: {len(figs)} figures")
    with _CHAPTER_LOCK:
        _CHAPTER_CACHE[code] = figs
    return figs


def is_cached(chapter_code: str) -> bool:
    """True when this chapter's figures are already cropped on disk.

    Lets a caller prefer chapters it can read instantly over ones that need a
    download and a parse first.
    """
    code = (chapter_code or "").strip().lower()
    if not code:
        return False
    with _CHAPTER_LOCK:
        if code in _CHAPTER_CACHE:
            return True
    return (FIGURE_DIR / code / "manifest.json").exists()


_STOP = {
    "the", "a", "an", "of", "and", "or", "in", "on", "to", "for", "with", "by",
    "is", "are", "was", "were", "its", "it", "this", "that", "from", "at", "as",
    "figure", "fig", "map", "diagram", "showing", "shows", "different", "types",
    "class", "chapter", "ncert", "labelled", "labeled", "structure",
}


def _terms(text: str) -> set:
    return {w for w in re.findall(r"[a-z]{3,}", (text or "").lower()) if w not in _STOP}


def score_figure(fig: Figure, topic_terms: set) -> float:
    """How well a figure's caption answers a topic. 0 means unrelated."""
    cap = _terms(fig.caption)
    if not cap or not topic_terms:
        return 0.0
    hits = cap & topic_terms
    if not hits:
        return 0.0
    return len(hits) / len(cap) + 0.15 * len(hits)


def find_figures(chapters: Iterable[str], topic: str, context: str = "",
                 limit: int = 3, min_score: float = 0.12) -> List[Figure]:
    """Best figures for `topic` from the chapters the notes were built on.

    `context` is the retrieved textbook text, matched at a lower weight: a
    caption like "Figure 6.2 Reflex arc" shares no word with the topic
    "Control and coordination", but plenty with the passages. The topic still
    outweighs it, so a figure that names the topic outright ranks first.

    Deduplicated by caption, so a figure present in two ingested copies of the
    same book is not shown twice.
    """
    topic_terms = _terms(topic)
    context_terms = _terms(context) - topic_terms
    scored = []
    seen = set()
    for code in dict.fromkeys(c for c in chapters if c):
        for fig in figures_for_chapter(code):
            key = fig.caption.lower()
            if key in seen:
                continue
            seen.add(key)
            score = score_figure(fig, topic_terms) + 0.35 * score_figure(fig, context_terms)
            if score >= min_score:
                scored.append((score, fig))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [f for _, f in scored[:limit]]


if __name__ == "__main__":  # manual check: python -m app.services.ncert_figure_service jesc106
    import sys
    for code in sys.argv[1:]:
        for f in figures_for_chapter(code, force=True):
            print(f"{f.chapter} p{f.page} {f.width}x{f.height} :: {f.caption[:70]}")
