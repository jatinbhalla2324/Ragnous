"""Markdown -> study-notes PDF.

The previous renderer split the model's output on blank lines and matched
`# `, `## `, `### ` — so tables printed as pipes, numbered lists lost their
numbers, nested bullets flattened, and every image was stretched into the same
4x3 box regardless of shape. This module parses the markdown properly and
draws each construct as the flowable it should be.

Handled: headings (with a live table of contents and PDF bookmarks), ordered
and unordered lists with nesting, tables, block quotes and callouts, fenced
code, horizontal rules, mermaid diagrams, inline bold/italic/code, a useful
subset of LaTeX and chemical formulae, and `[[IMG:token]]` placeholders that
resolve to figures cropped from the student's own NCERT chapters.

Visual design (palette, page chrome, cover) stays in pdf_theme.py.
"""

from __future__ import annotations

import base64
import html
import io
import os
import re
import unicodedata
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

import requests
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import (
    Image,
    KeepTogether,
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus.tableofcontents import TableOfContents

from app.api.v1.pdf_theme import (
    ACCENT,
    CODE_BORDER,
    PAGE_SIZE,
    PRIMARY,
    TABLE_ALT_ROW,
    TABLE_HEADER_BG,
    build_cover_page,
    build_styles,
    page_chrome,
    rule,
)

MERMAID_API_URL = os.getenv("MERMAID_API_URL", "https://kroki.io/mermaid/png/")

LEFT_MARGIN = RIGHT_MARGIN = 0.78 * inch
TOP_MARGIN = 0.95 * inch
BOTTOM_MARGIN = 0.75 * inch
CONTENT_WIDTH = PAGE_SIZE[0] - LEFT_MARGIN - RIGHT_MARGIN
MAX_IMAGE_HEIGHT = 3.7 * inch


# ── Inline formatting ─────────────────────────────────────────────────────

_GREEK = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "Delta": "Δ",
    "epsilon": "ε", "theta": "θ", "lambda": "λ", "mu": "μ", "pi": "π",
    "rho": "ρ", "sigma": "σ", "tau": "τ", "phi": "φ", "omega": "ω",
    "Omega": "Ω",
}
_LATEX_SYMBOLS = {
    r"\times": "×", r"\cdot": "·", r"\div": "÷", r"\pm": "±", r"\mp": "∓",
    r"\leq": "≤", r"\le": "≤", r"\geq": "≥", r"\ge": "≥", r"\neq": "≠",
    r"\approx": "≈", r"\equiv": "≡", r"\rightleftharpoons": "⇌",
    r"\rightarrow": "→", r"\longrightarrow": "→", r"\to": "→",
    r"\leftarrow": "←", r"\leftrightarrow": "↔", r"\Rightarrow": "⇒",
    r"\uparrow": "↑", r"\downarrow": "↓", r"\infty": "∞",
    r"\degree": "°", r"\circ": "°", r"\sqrt": "√", r"\sum": "∑",
    r"\propto": "∝", r"\therefore": "∴", r"\ldots": "…", r"\dots": "…",
    r"\%": "%", r"\left": "", r"\right": "", r"\,": " ", r"\;": " ",
    r"\!": "", r"\quad": "  ", r"\ ": " ",
}
# Whatever the model still writes as a LaTeX command after the table above:
# print the word, never the backslash. "H_2\uparrow" reading as "H2\uparrow"
# on the page is the failure this prevents.
_LEFTOVER_TEX_RE = re.compile(r"\\([a-zA-Z]+)")

# A chemical formula, and only that: two or more element-shaped units where at
# least one carries a digit (H2O, CaCO3, ZnSO4, C6H12O6). Deliberately narrow —
# "Class 10" and "1.5" must not acquire subscripts, and an all-caps word like
# NCERT is rejected by the digit test below. Requiring a digit on *every* unit
# was the earlier mistake: it silently skipped CaCO3 and ZnSO4, the two most
# common formulae in the Class 10 chapter. Subscripts cap at two digits, which
# every school formula obeys (C12H22O11) and which is what stops an NCERT
# chapter code from being set as one — "HECU105" rendered as HECU₁₀₅.
_CHEM_RE = re.compile(r"\b((?:[A-Z][a-z]?\d{0,2}){2,})\b")
# Braced forms take the whole group; bare forms take ONE character, because
# `H_2O` means H₂O — an earlier greedy version subscripted "2O".
_SUP_BRACED_RE = re.compile(r"(?<=[A-Za-z0-9\)\]])\^\{([^{}]{1,8})\}")
_SUB_BRACED_RE = re.compile(r"(?<=[A-Za-z0-9\)\]])_\{([^{}]{1,8})\}")
_SUP_RE = re.compile(r"(?<=[A-Za-z0-9\)\]])\^(-?[0-9A-Za-z])")
_SUB_RE = re.compile(r"(?<=[A-Za-z\)\]])_([0-9A-Za-z])")


def _frac(m) -> str:
    """\\frac{1}{2} -> 1/2, but \\frac{a+b}{2} -> (a+b)/2."""
    def wrap(part: str) -> str:
        part = part.strip()
        return part if re.fullmatch(r"[A-Za-z0-9.]{1,4}", part) else f"({part})"
    return f"{wrap(m.group(1))}/{wrap(m.group(2))}"


def _latex_to_text(s: str) -> str:
    """Enough LaTeX to read a formula. Not a typesetter — a de-mangler."""
    s = re.sub(r"\\frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}", _frac, s)
    s = re.sub(r"\\sqrt\s*\{([^{}]*)\}", r"√(\1)", s)
    s = re.sub(r"\\text\s*\{([^{}]*)\}", r"\1", s)
    s = re.sub(r"\\mathrm\s*\{([^{}]*)\}", r"\1", s)
    for name, ch in _GREEK.items():
        s = s.replace("\\" + name, ch)
    for tex, ch in _LATEX_SYMBOLS.items():
        s = s.replace(tex, ch)
    s = _LEFTOVER_TEX_RE.sub(r"\1", s)
    return s


def _strip_math_delimiters(s: str) -> str:
    s = re.sub(r"\$\$(.+?)\$\$", lambda m: _latex_to_text(m.group(1)), s, flags=re.S)
    s = re.sub(r"\$(.+?)\$", lambda m: _latex_to_text(m.group(1)), s, flags=re.S)
    s = re.sub(r"\\\((.+?)\\\)", lambda m: _latex_to_text(m.group(1)), s, flags=re.S)
    s = re.sub(r"\\\[(.+?)\\\]", lambda m: _latex_to_text(m.group(1)), s, flags=re.S)
    return _latex_to_text(s)


def _chem_subscripts(s: str) -> str:
    def sub(m):
        token = m.group(1)
        if not re.search(r"\d", token) or len(token) > 16:
            return token
        return re.sub(r"(\d+)", r"<sub>\1</sub>", token)
    return _CHEM_RE.sub(sub, s)


# ── Glyph safety ──────────────────────────────────────────────────────────
# ReportLab's built-in Helvetica is WinAnsi-encoded, so anything outside that
# set is drawn as a black box — and notes are full of such characters: the
# model writes CO₂, →, ≈, λ and non-breaking hyphens, and a page came back
# reading "CO■" and "sub■layers".
#
# Two layers deal with it. Sub/superscript digits become real <sub>/<super>
# tags, which reads better than the raw character would anyway. Anything else
# outside WinAnsi is drawn from a Unicode TrueType font when the machine has
# one, and otherwise spelled out in ASCII. (Drawing these from the built-in
# Symbol font is the classic ReportLab trick and it does NOT work here —
# measured: every such glyph still came out as a box.)
_SYMBOL_FONT_NAME = "NotesSymbols"
_SYMBOL_FONT_CANDIDATES = (
    os.getenv("NOTES_SYMBOL_FONT", ""),
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "C:/Windows/Fonts/arial.ttf",
)


def _load_symbol_font():
    """(font name, set of codepoints) for the first Unicode TTF we can find."""
    for path in _SYMBOL_FONT_CANDIDATES:
        if not path or not os.path.exists(path):
            continue
        try:
            font = TTFont(_SYMBOL_FONT_NAME, path)
            pdfmetrics.registerFont(font)
            print(f"[NOTES] symbols drawn from {os.path.basename(path)}")
            return _SYMBOL_FONT_NAME, frozenset(font.face.charToGlyph)
        except Exception as e:
            print(f"[NOTES] could not load {path}: {e}")
    print("[NOTES] no Unicode font found; symbols will be spelled out in ASCII")
    return None, frozenset()


_SYMBOL_FONT, _SYMBOL_GLYPHS = _load_symbol_font()

# Used when no Unicode font is installed. Readable beats decorative: a student
# can read "Metal + Acid -> Salt", but not "Metal + Acid ■ Salt".
_ASCII_FALLBACK = {
    "→": " -> ", "←": " <- ", "↔": " <-> ", "⇌": " <=> ", "⇒": " => ",
    "↑": "(up)", "↓": "(down)", "≤": "<=", "≥": ">=", "≠": "!=", "≈": "~",
    "≡": "=", "∞": "infinity", "√": "root ", "∝": " is proportional to ",
    "∴": "therefore ", "∑": "sum ", "∈": " in ", "±": "+/-",
    "α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "ε": "epsilon",
    "θ": "theta", "κ": "kappa", "λ": "lambda", "μ": "mu", "ν": "nu",
    "π": "pi", "ρ": "rho", "σ": "sigma", "τ": "tau", "φ": "phi",
    "ω": "omega", "Δ": "delta ", "Ω": "ohm", "Σ": "sum ",
    "\u2011": "-", "\u2010": "-", "\u2012": "-", "\u2212": "-", "\u00a0": " ",
    "\u200b": "", "\u2044": "/", "\u25aa": "-", "\u25cf": "-",
    "✓": "(yes)", "✗": "(no)",
}
_SUBSCRIPT_DIGITS = {chr(0x2080 + d): str(d) for d in range(10)}
_SUPERSCRIPT_DIGITS = {
    "⁰": "0", "⁴": "4", "⁵": "5", "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9",
    "⁺": "+", "⁻": "-", "ⁿ": "n",
}


def _encodable(ch: str) -> bool:
    try:
        ch.encode("cp1252")
        return True
    except UnicodeEncodeError:
        return False


def _safe_glyphs(text: str) -> str:
    """Replace characters Helvetica cannot draw.

    Runs on already-escaped text and may emit <font>/<sub>/<super> tags, so
    nothing after it may escape the result again.
    """
    out = []
    for ch in text:
        if ch in _SUBSCRIPT_DIGITS:
            out.append(f"<sub>{_SUBSCRIPT_DIGITS[ch]}</sub>")
        elif ch in _SUPERSCRIPT_DIGITS:
            out.append(f"<super>{_SUPERSCRIPT_DIGITS[ch]}</super>")
        elif _encodable(ch):
            out.append(ch)
        elif _SYMBOL_FONT and ord(ch) in _SYMBOL_GLYPHS:
            out.append(f'<font face="{_SYMBOL_FONT}">{ch}</font>')
        elif ch in _ASCII_FALLBACK:
            out.append(_ASCII_FALLBACK[ch])
        else:
            # Last resort: strip the accent or shape rather than print a box.
            folded = unicodedata.normalize("NFKD", ch)
            out.append("".join(c for c in folded if _encodable(c)))
    return "".join(out)


def safe_text(text: str) -> str:
    """Glyph-safe plain text for chrome (titles, captions) that skips inline()."""
    return _safe_glyphs(html.escape(text or "", quote=False))


def inline(text: str) -> str:
    """Markdown span syntax -> ReportLab's mini-HTML, safely escaped."""
    if not text:
        return ""
    text = _strip_math_delimiters(text)

    code_spans: List[str] = []

    def stash_code(m):
        code_spans.append(m.group(1))
        return f"\x00{len(code_spans) - 1}\x00"

    text = re.sub(r"`([^`]+)`", stash_code, text)
    text = html.escape(text, quote=False)

    text = re.sub(r"\*\*\*(.+?)\*\*\*", r"<b><i>\1</i></b>", text, flags=re.S)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text, flags=re.S)
    text = re.sub(r"(?<![\w*])\*([^*\n]+?)\*(?![\w*])", r"<i>\1</i>", text)
    text = re.sub(r"(?<![\w_])_([^_\n]+?)_(?![\w_])", r"<i>\1</i>", text)
    # Markdown links: the PDF is read on paper as often as on screen, so show
    # the text and keep the target clickable.
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
                  r'<link href="\2" color="#2F5D8C">\1</link>', text)

    text = _chem_subscripts(text)
    text = _SUP_BRACED_RE.sub(r"<super>\1</super>", text)
    text = _SUB_BRACED_RE.sub(r"<sub>\1</sub>", text)
    text = _SUP_RE.sub(r"<super>\1</super>", text)
    text = _SUB_RE.sub(r"<sub>\1</sub>", text)

    text = _safe_glyphs(text)
    # 10<super>-</super><super>7</super> -> 10<super>-7</super>
    text = re.sub(r"</(super|sub)><\1>", "", text)

    for i, code in enumerate(code_spans):
        text = text.replace(
            f"\x00{i}\x00",
            f'<font face="Courier" size="9">'
            f'{_safe_glyphs(html.escape(code, quote=False))}</font>',
        )
    return text


# ── Block model ───────────────────────────────────────────────────────────

@dataclass
class Block:
    kind: str                     # heading|para|list|table|quote|code|mermaid|image|rule
    text: str = ""
    level: int = 0
    items: Optional[List] = None  # list items / table rows
    ordered: bool = False


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_ULIST_RE = re.compile(r"^(\s*)[-*•]\s+(.*)$")
_OLIST_RE = re.compile(r"^(\s*)(\d+)[.)]\s+(.*)$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?[\s:-]*[-]{2,}[\s:|-]*\|?\s*$")
_IMG_RE = re.compile(r"^\s*\[\[IMG:([^\]]+)\]\]\s*$")
_INLINE_IMG_RE = re.compile(r"\[\[IMG:([^\]]+)\]\]")
# Tags the old prompt asked for; harmless leftovers if a model still emits one.
_LEGACY_IMAGE_TAG = re.compile(r"\[IMAGE_SEARCH:.*?\]", re.S)


def _split_row(line: str) -> List[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def _hoist_image_markers(md: str) -> str:
    """Move a figure marker that landed mid-sentence onto its own line.

    The model is asked to put `[[FIGURE: 6.2]]` on its own line and sometimes
    writes it inside a bullet instead, where it was printed to the page as
    literal "[[IMG:s0fig0]]". Markers are lifted out and re-emitted after the
    block they appeared in, which is where the figure belongs anyway.
    """
    out: List[str] = []
    pending: List[str] = []
    for line in md.split("\n"):
        if not _IMG_RE.match(line):
            found = _INLINE_IMG_RE.findall(line)
            if found:
                line = _INLINE_IMG_RE.sub("", line).rstrip()
                pending.extend(found)
        out.append(line)
        if not line.strip() and pending:
            out.extend([f"[[IMG:{t}]]" for t in pending] + [""])
            pending.clear()
    if pending:
        out.extend([""] + [f"[[IMG:{t}]]" for t in pending])
    return "\n".join(out)


def parse_markdown(md: str) -> List[Block]:
    md = _LEGACY_IMAGE_TAG.sub("", md or "")
    md = _hoist_image_markers(md)
    lines = md.replace("\r\n", "\n").split("\n")
    blocks: List[Block] = []
    para: List[str] = []
    i = 0

    def flush_para():
        if para:
            blocks.append(Block("para", " ".join(para).strip()))
            para.clear()

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            flush_para()
            i += 1
            continue

        m = _IMG_RE.match(line)
        if m:
            flush_para()
            blocks.append(Block("image", m.group(1).strip()))
            i += 1
            continue

        if stripped.startswith("```"):
            flush_para()
            lang = stripped[3:].strip().lower()
            body, i = [], i + 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                body.append(lines[i])
                i += 1
            i += 1
            kind = "mermaid" if lang.startswith("mermaid") else "code"
            blocks.append(Block(kind, "\n".join(body)))
            continue

        m = _HEADING_RE.match(stripped)
        if m:
            flush_para()
            blocks.append(Block("heading", m.group(2).strip(" #"), level=len(m.group(1))))
            i += 1
            continue

        if re.match(r"^\s*([-*_])\1{2,}\s*$", stripped):
            flush_para()
            blocks.append(Block("rule"))
            i += 1
            continue

        # Table: a header row followed by a |---|---| separator.
        if "|" in stripped and i + 1 < len(lines) and _TABLE_SEP_RE.match(lines[i + 1]):
            flush_para()
            rows = [_split_row(stripped)]
            i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append(_split_row(lines[i]))
                i += 1
            width = max(len(r) for r in rows)
            rows = [r + [""] * (width - len(r)) for r in rows]
            blocks.append(Block("table", items=rows))
            continue

        if stripped.startswith(">"):
            flush_para()
            quote = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote.append(lines[i].strip().lstrip(">").strip())
                i += 1
            blocks.append(Block("quote", " ".join(quote)))
            continue

        if _ULIST_RE.match(line) or _OLIST_RE.match(line):
            flush_para()
            # (indent, text, ordered) — the marker is recorded per line, because
            # NCERT-style notes nest bullets under numbered steps and the two
            # must not be drawn with the same marker.
            items: List[tuple] = []
            ordered = bool(_OLIST_RE.match(line))
            while i < len(lines):
                if not lines[i].strip():
                    # A "loose" list — blank lines between items. Markdown still
                    # calls that one list, and treating each item as its own
                    # restarted the numbering at 1 for every step.
                    j = i + 1
                    while j < len(lines) and not lines[j].strip():
                        j += 1
                    if j < len(lines) and (
                        _ULIST_RE.match(lines[j]) or _OLIST_RE.match(lines[j])
                        or (lines[j].startswith((" ", "\t")) and items)
                    ):
                        i = j
                        continue
                    break
                um, om = _ULIST_RE.match(lines[i]), _OLIST_RE.match(lines[i])
                if um:
                    items.append((len(um.group(1)), um.group(2).strip(), False))
                elif om:
                    items.append((len(om.group(1)), om.group(3).strip(), True))
                elif lines[i].strip() and lines[i].startswith((" ", "\t")) and items:
                    # continuation of the previous bullet
                    indent, text, was_ordered = items[-1]
                    items[-1] = (indent, f"{text} {lines[i].strip()}", was_ordered)
                else:
                    break
                i += 1
            blocks.append(Block("list", items=items, ordered=ordered))
            continue

        para.append(stripped)
        i += 1

    flush_para()
    return blocks


# ── Drawing ───────────────────────────────────────────────────────────────

def _mermaid_png(code: str) -> Optional[bytes]:
    try:
        encoded = base64.urlsafe_b64encode(code.strip().encode("utf-8")).decode("utf-8")
        res = requests.get(f"{MERMAID_API_URL}{encoded}", timeout=12)
        if res.status_code == 200 and res.content:
            return res.content
    except Exception as e:
        print(f"[NOTES] mermaid render failed: {e}")
    return None


def _fit_image(data_or_path, max_width=CONTENT_WIDTH, max_height=MAX_IMAGE_HEIGHT) -> Optional[Image]:
    """Scale to fit, preserving aspect ratio. The old renderer forced 4x3 and
    squashed every portrait diagram."""
    try:
        src = io.BytesIO(data_or_path) if isinstance(data_or_path, bytes) else data_or_path
        img = Image(src)
        iw, ih = img.imageWidth, img.imageHeight
        if not iw or not ih:
            return None
        scale = min(max_width / iw, max_height / ih, 1.0)
        # Small crops (a 200pt-wide ray diagram) still deserve to be readable.
        scale = max(scale, min(max_width / iw, max_height / ih) * 0.55)
        img.drawWidth, img.drawHeight = iw * scale, ih * scale
        img.hAlign = "CENTER"
        return img
    except Exception as e:
        print(f"[NOTES] image could not be placed: {e}")
        return None


_BOOK_CLASS_RE = re.compile(r"^(\d{1,2})(?:th|st|nd|rd)?[_\s-]+(.*)$", re.I)


def pretty_book(subject: str) -> str:
    """'8th_science_english' -> 'Class 8 Science (English)'.

    A plain .title() produced "8Th Science English" in the sources table.
    """
    name = (subject or "").replace("_", " ").strip()
    if not name:
        return "NCERT"
    m = _BOOK_CLASS_RE.match(name)
    if m:
        rest = m.group(2).split()
        medium = ""
        if rest and rest[-1].lower() in ("english", "hindi", "punjabi", "urdu"):
            medium = f" ({rest.pop().capitalize()})"
        return f"Class {m.group(1)} {' '.join(w.capitalize() for w in rest)}{medium}".strip()
    return " ".join(w if w.isupper() else w.capitalize() for w in name.split())


def _source_line(fig, book: str = "") -> str:
    """'NCERT Class 8 Science (English), page 7'.

    Named by book and page only. The chapter's NCERT file code ("HECU105") is
    what the pipeline works in, and it means nothing to a student.
    """
    return f"{book or 'NCERT textbook'}, page {fig.page}"


def _bullet_flowable(items: Sequence[tuple], ordered: bool, styles) -> List:
    """Nested markdown lists, as nested ListFlowables.

    A deeper item belongs *inside* the item above it, so the child list is
    appended to that ListItem's own flowables. Giving the child list a ListItem
    of its own instead printed a stray marker beside it and restarted the
    parent's numbering — "1 … 2 1 … 1" where the notes said 1, 2, 3.
    """
    def build(start: int, indent: int):
        rows: List[tuple] = []           # (paragraph or None, [child lists])
        level_ordered = None
        i = start
        while i < len(items):
            ind, text, is_ordered = items[i]
            if ind < indent:
                break
            if ind > indent:
                child, i = build(i, ind)
                if rows:
                    rows[-1][1].append(child)
                else:
                    rows.append((None, [child]))
                continue
            if level_ordered is None:
                level_ordered = is_ordered
            rows.append((Paragraph(inline(text), styles["Bullet"]), []))
            i += 1

        entries = [
            ListItem([f for f in ([para] + children) if f is not None], leftIndent=14)
            for para, children in rows
        ]
        use_numbers = level_ordered if level_ordered is not None else ordered
        flow = ListFlowable(
            entries,
            bulletType="1" if use_numbers else "bullet",
            bulletFormat="%s." if use_numbers else None,
            start=None if use_numbers else "\u2022",
            bulletFontName="Helvetica-Bold" if use_numbers else "Helvetica",
            bulletFontSize=10,
            bulletColor=PRIMARY if use_numbers else ACCENT,
            leftIndent=18,
            bulletDedent=13,
            spaceBefore=2,
            spaceAfter=6,
        )
        return flow, i

    if not items:
        return []
    flow, _ = build(0, items[0][0])
    return [flow]


def _table_flowable(rows: List[List[str]], styles) -> List:
    header, body = rows[0], rows[1:]
    ncols = len(header)
    if ncols == 0:
        return []
    # A 2-column table reads better with a narrow first column (Term | Meaning).
    if ncols == 2:
        widths = [CONTENT_WIDTH * 0.32, CONTENT_WIDTH * 0.68]
    else:
        widths = [CONTENT_WIDTH / ncols] * ncols

    data = [[Paragraph(inline(c), styles["TableHeader"]) for c in header]]
    for row in body:
        data.append([Paragraph(inline(c), styles["TableCell"]) for c in row])

    table = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), TABLE_HEADER_BG),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.4, CODE_BORDER),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    for r in range(1, len(data)):
        if r % 2 == 0:
            style.append(("BACKGROUND", (0, r), (-1, r), TABLE_ALT_ROW))
    table.setStyle(TableStyle(style))
    return [Spacer(1, 4), table, Spacer(1, 10)]


def _callout(text: str, styles) -> List:
    """A quoted line, drawn as a bar-and-tint callout."""
    inner = Paragraph(inline(text), styles["Quote"])
    box = Table([[inner]], colWidths=[CONTENT_WIDTH])
    box.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FAFAFA")),
        ("LINEBEFORE", (0, 0), (0, -1), 2.2, ACCENT),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    return [Spacer(1, 4), box, Spacer(1, 8)]


def _figure_flowable(fig, styles, book: str = "") -> List:
    img = _fit_image(fig.path)
    if img is None:
        return []
    caption = fig.caption if len(fig.caption) <= 240 else fig.caption[:237] + "…"
    parts = [
        Spacer(1, 6),
        img,
        Spacer(1, 4),
        Paragraph(inline(caption), styles["Caption"]),
        Paragraph(safe_text(_source_line(fig, book)), styles["FigureSource"]),
        Spacer(1, 8),
    ]
    # A figure split across a page break loses its caption; keep them together
    # when they fit, and start a fresh page when they don't.
    return [KeepTogether(parts)]


def build_story(markdown: str, figures_by_token: Dict[str, object], styles,
                heading_keys: List, books_by_chapter: Optional[Dict[str, str]] = None) -> List:
    story: List = []
    blocks = parse_markdown(markdown)
    first_heading = True

    for block in blocks:
        if block.kind == "heading":
            level = min(block.level, 3)
            # Every topic opens a page — except the first, which would leave a
            # blank sheet between the contents page and the notes.
            if level == 1 and not first_heading:
                story.append(PageBreak())
            first_heading = False
            key = f"h{len(heading_keys)}"
            heading_keys.append((level, block.text, key))
            style = styles[f"Heading{level}"]
            para = Paragraph(f'<a name="{key}"/>{inline(block.text)}', style)
            para._bookmarkName = key
            story.append(para)
            if level <= 2:
                story.append(rule(width=CONTENT_WIDTH if level == 1 else CONTENT_WIDTH * 0.45,
                                  thickness=1.2 if level == 1 else 0.8,
                                  space_after=8))
        elif block.kind == "para":
            text = block.text
            style = styles["Answer"] if text.lower().startswith("**answer:") else styles["Body"]
            story.append(Paragraph(inline(text), style))
        elif block.kind == "list":
            story.extend(_bullet_flowable(block.items or [], block.ordered, styles))
        elif block.kind == "table":
            story.extend(_table_flowable(block.items or [], styles))
        elif block.kind == "quote":
            story.extend(_callout(block.text, styles))
        elif block.kind == "code":
            story.append(Paragraph(
                html.escape(block.text, quote=False).replace("\n", "<br/>"), styles["Code"]))
        elif block.kind == "mermaid":
            png = _mermaid_png(block.text)
            img = _fit_image(png) if png else None
            if img is not None:
                story.extend([Spacer(1, 6), img, Spacer(1, 10)])
        elif block.kind == "image":
            fig = figures_by_token.get(block.text)
            if fig is not None:
                book = (books_by_chapter or {}).get(fig.chapter, "")
                story.extend(_figure_flowable(fig, styles, book))
        elif block.kind == "rule":
            story.append(rule(width=CONTENT_WIDTH, thickness=0.6, color=CODE_BORDER))
    return story


def _sources_section(sources: Sequence, styles, heading_keys: List) -> List:
    if not sources:
        return []
    key = f"h{len(heading_keys)}"
    heading_keys.append((1, "Where this came from", key))
    heading = Paragraph(f'<a name="{key}"/>Where this came from', styles["Heading1"])
    heading._bookmarkName = key
    flow = [
        PageBreak(),
        heading,
        rule(width=CONTENT_WIDTH, space_after=8),
        Paragraph(
            "Every explanation above was built from these pages of your NCERT textbooks, "
            "and every figure was taken from the same pages. Check anything that "
            "matters against the book itself.",
            styles["Body"]),
        Spacer(1, 6),
    ]
    # One row per book with the pages used, rather than a row per passage with
    # an NCERT file code in it.
    pages_by_book: Dict[str, List[int]] = {}
    for src in sources[:60]:
        pages_by_book.setdefault(pretty_book(src.subject), []).append(src.page)

    rows = [["Book", "Pages"]]
    for book, pages in pages_by_book.items():
        ordered = sorted(set(pages))
        rows.append([book, ", ".join(str(p) for p in ordered)])

    data = [[Paragraph(inline(c), styles["TableHeader"]) for c in rows[0]]]
    for r in rows[1:]:
        data.append([Paragraph(safe_text(c), styles["TableCell"]) for c in r])
    table = Table(data, colWidths=[CONTENT_WIDTH * 0.58, CONTENT_WIDTH * 0.42],
                  repeatRows=1, hAlign="LEFT")
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), TABLE_HEADER_BG),
        ("GRID", (0, 0), (-1, -1), 0.4, CODE_BORDER),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    for r in range(1, len(data)):
        if r % 2 == 0:
            style.append(("BACKGROUND", (0, r), (-1, r), TABLE_ALT_ROW))
    table.setStyle(TableStyle(style))
    flow.append(table)
    return flow


class _NotesDoc(SimpleDocTemplate):
    """Feeds headings to the table of contents and the PDF outline.

    The hook this replaces was `def afterFlowable(self, flowable): pass`, so
    the TOC page pdf_theme styles exist for was never populated.
    """

    def beforeDocument(self):
        # multiBuild lays the document out more than once; the outline has to
        # start from scratch on every pass.
        self._outline_level = -1

    def afterFlowable(self, flowable):
        if not isinstance(flowable, Paragraph):
            return
        name = getattr(flowable.style, "name", "")
        if name not in ("Heading1", "Heading2"):
            return
        text = re.sub(r"<[^>]+>", "", flowable.getPlainText() or "").strip()
        if not text:
            return
        # A PDF outline cannot skip a level, and a document whose first heading
        # is an H2 would do exactly that.
        level = min(0 if name == "Heading1" else 1,
                    getattr(self, "_outline_level", -1) + 1)
        self._outline_level = level
        key = getattr(flowable, "_bookmarkName", None) or f"toc{id(flowable)}"
        self.canv.bookmarkPage(key)
        self.canv.addOutlineEntry(text[:80], key, level=level, closed=(level > 0))
        self.notify("TOCEntry", (level, text, self.page, key))


def _toc(styles) -> TableOfContents:
    toc = TableOfContents()
    toc.levelStyles = [styles["TOC1"], styles["TOC2"]]
    toc.dotsMinLevel = 0
    return toc


def _build_once(doc, target, total_pages: Optional[int]) -> int:
    """One full rendering pass. Returns the page count it produced."""
    styles = build_styles()
    pdf = _NotesDoc(
        target, pagesize=PAGE_SIZE,
        leftMargin=LEFT_MARGIN, rightMargin=RIGHT_MARGIN,
        topMargin=TOP_MARGIN, bottomMargin=BOTTOM_MARGIN,
        title=doc.title, author="RAGNOUS", subject=doc.subtitle,
    )

    subtitle_bits = [b for b in [doc.subtitle,
                                 f"Class {doc.student_class}" if doc.student_class else ""] if b]
    story = build_cover_page(styles, safe_text(doc.title),
                             safe_text(" · ".join(subtitle_bits)))
    story.append(PageBreak())
    story.append(Paragraph("Contents", styles["TOCHeading"]))
    story.append(rule(width=CONTENT_WIDTH, space_after=10))
    story.append(_toc(styles))
    story.append(PageBreak())

    heading_keys: List = []
    books_by_chapter = {s.chapter: pretty_book(s.subject) for s in doc.sources}
    story.extend(build_story(doc.markdown, doc.figures, styles, heading_keys,
                             books_by_chapter))
    story.extend(_sources_section(doc.sources, styles, heading_keys))

    chrome = page_chrome(doc.title, total_pages)
    pdf.multiBuild(story, onFirstPage=chrome, onLaterPages=chrome)
    return pdf.page


def render_notes_pdf(doc, output_path: str) -> str:
    """Render a NotesDocument (app/services/notes_service.py) to `output_path`.

    Built twice: the footer says "Page 3 of 11", and the total is only known
    once the document has been laid out. The first pass is thrown away — its
    flowables are consumed by it, so the story is rebuilt rather than reused.
    """
    total = _build_once(doc, io.BytesIO(), None)
    _build_once(doc, output_path, total)
    return output_path


@dataclass
class _PlainDoc:
    title: str
    subtitle: str
    markdown: str
    figures: Dict[str, object]
    sources: List
    student_class: str = ""


def generate_study_notes_pdf(llm_markdown_output: str, output_filepath: str,
                             title: str = "RAGNOUS Study Notes",
                             subtitle: str = "Synthesized from your chat session") -> str:
    """Render bare markdown — kept for callers that have no NotesDocument."""
    return render_notes_pdf(
        _PlainDoc(title=title, subtitle=subtitle, markdown=llm_markdown_output,
                  figures={}, sources=[]),
        output_filepath,
    )
