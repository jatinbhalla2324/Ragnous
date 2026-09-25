"""
pdf_theme.py
------------
All visual design decisions for the RAGNOUS study-notes PDF live here:
color palette, paragraph styles, page chrome (branded header + "Page X of Y"
footer), and the cover page builder. Keeping this separate from parsing
logic means the look can be restyled without touching markdown-parsing code.
"""

from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

# ---------------------------------------------------------
# COLOR PALETTE
# ---------------------------------------------------------
PRIMARY = colors.HexColor("#1B2A4A")        # deep navy   - brand / H1
SECONDARY = colors.HexColor("#2F5D8C")      # steel blue  - H2
ACCENT = colors.HexColor("#E08E45")         # warm amber  - rules / accents
TEXT_DARK = colors.HexColor("#1F2933")
TEXT_MUTED = colors.HexColor("#5B6472")
CODE_BG = colors.HexColor("#F4F5F7")
CODE_BORDER = colors.HexColor("#D8DCE2")
TABLE_HEADER_BG = colors.HexColor("#1B2A4A")
TABLE_ALT_ROW = colors.HexColor("#F7F8FA")
QUOTE_BAR = ACCENT
QUOTE_BG = colors.HexColor("#FAFAFA")
PAGE_SIZE = A4

BRAND_NAME = "RAGNOUS  ·  AI Study Notes"


# ---------------------------------------------------------
# PARAGRAPH STYLES
# ---------------------------------------------------------
def build_styles() -> dict:
    """Returns a fresh dict of ParagraphStyles. Call once per PDF build.

    NOTE: style names 'Heading1' / 'Heading2' are load-bearing —
    notes_render._NotesDoc.afterFlowable() matches on style.name to populate
    the table of contents and the PDF outline. Don't rename without updating
    that hook too.
    """
    base = getSampleStyleSheet()
    styles = {}

    styles["CoverTitle"] = ParagraphStyle(
        "CoverTitle", parent=base["Title"], fontName="Helvetica-Bold",
        fontSize=28, leading=34, textColor=PRIMARY, alignment=TA_CENTER,
        spaceAfter=14,
    )
    styles["CoverSubtitle"] = ParagraphStyle(
        "CoverSubtitle", parent=base["Normal"], fontName="Helvetica",
        fontSize=13, leading=18, textColor=TEXT_MUTED, alignment=TA_CENTER,
        spaceAfter=6,
    )
    styles["CoverMeta"] = ParagraphStyle(
        "CoverMeta", parent=base["Normal"], fontName="Helvetica-Oblique",
        fontSize=10, leading=14, textColor=TEXT_MUTED, alignment=TA_CENTER,
    )
    styles["Heading1"] = ParagraphStyle(
        "Heading1", parent=base["Heading1"], fontName="Helvetica-Bold",
        fontSize=19, leading=24, textColor=PRIMARY, spaceBefore=18,
        spaceAfter=10,
    )
    styles["Heading2"] = ParagraphStyle(
        "Heading2", parent=base["Heading2"], fontName="Helvetica-Bold",
        fontSize=15, leading=19, textColor=SECONDARY, spaceBefore=14,
        spaceAfter=8,
    )
    styles["Heading3"] = ParagraphStyle(
        "Heading3", parent=base["Heading3"], fontName="Helvetica-Bold",
        fontSize=12.5, leading=16, textColor=TEXT_DARK, spaceBefore=10,
        spaceAfter=6,
    )
    styles["Body"] = ParagraphStyle(
        "Body", parent=base["Normal"], fontName="Helvetica", fontSize=10.3,
        leading=15, textColor=TEXT_DARK, alignment=TA_JUSTIFY, spaceAfter=8,
    )
    styles["Bullet"] = ParagraphStyle(
        "Bullet", parent=styles["Body"], leftIndent=14, spaceAfter=4,
        alignment=0,
    )
    styles["Numbered"] = ParagraphStyle(
        "Numbered", parent=styles["Body"], leftIndent=14, spaceAfter=4,
        alignment=0,
    )
    styles["Quote"] = ParagraphStyle(
        "Quote", parent=styles["Body"], fontName="Helvetica-Oblique",
        textColor=TEXT_MUTED, leftIndent=12, spaceBefore=6, spaceAfter=6,
        alignment=0,
    )
    styles["Code"] = ParagraphStyle(
        "Code", parent=base["Code"], fontName="Courier", fontSize=8.6,
        leading=11.5, textColor=TEXT_DARK, backColor=CODE_BG,
        borderColor=CODE_BORDER, borderWidth=0.6, borderPadding=8,
        spaceBefore=4, spaceAfter=10,
    )
    styles["Caption"] = ParagraphStyle(
        "Caption", parent=base["Normal"], fontName="Helvetica-Oblique",
        fontSize=8.8, leading=11, textColor=TEXT_MUTED, alignment=TA_CENTER,
        spaceAfter=12,
    )
    styles["TableHeader"] = ParagraphStyle(
        "TableHeader", parent=base["Normal"], fontName="Helvetica-Bold",
        fontSize=9.4, leading=12.5, textColor=colors.white,
    )
    styles["TableCell"] = ParagraphStyle(
        "TableCell", parent=base["Normal"], fontName="Helvetica", fontSize=9.4,
        leading=12.5, textColor=TEXT_DARK,
    )
    styles["Answer"] = ParagraphStyle(
        "Answer", parent=styles["Body"], leftIndent=12, textColor=TEXT_MUTED,
        alignment=0, spaceBefore=1, spaceAfter=10,
    )
    styles["FigureSource"] = ParagraphStyle(
        "FigureSource", parent=base["Normal"], fontName="Helvetica",
        fontSize=7.6, leading=10, textColor=TEXT_MUTED, alignment=TA_CENTER,
        spaceAfter=2,
    )
    styles["TOCHeading"] = ParagraphStyle(
        "TOCHeading", parent=base["Heading1"], fontName="Helvetica-Bold",
        fontSize=18, textColor=PRIMARY, spaceAfter=14,
    )
    styles["TOC1"] = ParagraphStyle(
        "TOC1", fontName="Helvetica-Bold", fontSize=11, leading=16,
        textColor=PRIMARY, leftIndent=0,
    )
    styles["TOC2"] = ParagraphStyle(
        "TOC2", fontName="Helvetica", fontSize=9.5, leading=14,
        textColor=TEXT_DARK, leftIndent=14,
    )
    return styles


def rule(width=460, color=ACCENT, thickness=1.4, space_before=4, space_after=10):
    """A thin horizontal divider, used under H1s and as an hr."""
    t = Table([[""]], colWidths=[width], rowHeights=[thickness])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), color),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    t.spaceBefore = space_before
    t.spaceAfter = space_after
    return t


def build_cover_page(styles: dict, module_title: str, topic_summary: str = "") -> list:
    flow = []
    flow.append(Spacer(1, 2.2 * inch))
    flow.append(Paragraph(BRAND_NAME.upper(), styles["CoverSubtitle"]))
    flow.append(rule(width=200, space_before=8, space_after=18))
    flow.append(Paragraph(module_title, styles["CoverTitle"]))
    if topic_summary:
        flow.append(Spacer(1, 6))
        flow.append(Paragraph(topic_summary, styles["CoverSubtitle"]))
    flow.append(Spacer(1, 1.4 * inch))
    flow.append(Paragraph(f"Generated on {datetime.now().strftime('%d %B %Y')}", styles["CoverMeta"]))
    flow.append(Paragraph("Personalized study module synthesized from your RAGNOUS session", styles["CoverMeta"]))
    return flow


# ---------------------------------------------------------
# PAGE CHROME — branded header rule + "Page X of Y" footer
# ---------------------------------------------------------
def page_chrome(module_title: str = "", total_pages=None):
    """An onPage callback that stamps the header and footer.

    `total_pages` is the count from a previous rendering pass, so "Page X of Y"
    can be printed at all — the renderer builds the document twice for exactly
    this reason (see notes_render.render_notes_pdf). The cover is left bare.

    This replaces a NumberedCanvas that buffered every page and emitted it in
    save(): with pages deferred that way, every bookmark and every table of
    contents entry resolved to page 1, and each page was written out twice.
    """
    def draw(canvas, doc):
        page_num = canvas.getPageNumber()
        if page_num <= 1:
            return
        w, h = PAGE_SIZE
        canvas.saveState()
        canvas.setStrokeColor(CODE_BORDER)
        canvas.setLineWidth(0.6)
        canvas.line(0.7 * inch, h - 0.65 * inch, w - 0.7 * inch, h - 0.65 * inch)
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(TEXT_MUTED)
        canvas.drawString(0.7 * inch, h - 0.55 * inch, BRAND_NAME)
        canvas.drawRightString(w - 0.7 * inch, h - 0.55 * inch, (module_title or "")[:70])
        # The cover is not counted, so the first numbered page reads "Page 1".
        label = f"Page {page_num - 1}"
        if total_pages:
            label += f" of {max(total_pages - 1, 1)}"
        canvas.drawCentredString(w / 2, 0.45 * inch, label)
        canvas.restoreState()

    return draw
