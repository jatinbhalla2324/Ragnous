"""Markdown -> PDF parsing rules that broke in real notes.

Each test here corresponds to something that reached a rendered page and was
wrong: numbering that restarted, a figure marker printed as literal text, a
subscript that swallowed the next letter, a black box where an arrow belonged.
Run with: python -m pytest tests/test_notes_render.py
"""

import re

import pytest

from app.api.v1.notes_render import (
    _hoist_image_markers,
    _source_line,
    inline,
    parse_markdown,
    pretty_book,
    safe_text,
)


def kinds(md):
    return [b.kind for b in parse_markdown(md)]


# ── Lists ─────────────────────────────────────────────────────────────────

def test_blank_lines_between_items_stay_one_list():
    """A "loose" list was split into three lists, so every step was numbered 1."""
    blocks = parse_markdown("1. First\n\n2. Second\n\n3. Third\n")
    lists = [b for b in blocks if b.kind == "list"]
    assert len(lists) == 1
    assert [text for _, text, _ in lists[0].items] == ["First", "Second", "Third"]


def test_nested_bullets_keep_their_own_marker():
    blocks = parse_markdown("1. Step\n   * detail a\n   * detail b\n2. Next\n")
    items = [b for b in blocks if b.kind == "list"][0].items
    assert [ordered for _, _, ordered in items] == [True, False, False, True]
    assert items[1][0] > items[0][0]     # the detail is indented deeper


def test_indented_continuation_joins_the_item_above():
    items = [b for b in parse_markdown(
        "1. Reason:\n   because the spinal cord is closer\n"
    ) if b.kind == "list"][0].items
    assert items[0][1] == "Reason: because the spinal cord is closer"


def test_paragraph_after_a_list_ends_the_list():
    assert kinds("- one\n- two\n\nA new paragraph.\n") == ["list", "para"]


# ── Tables, quotes, headings ──────────────────────────────────────────────

def test_table_with_alignment_row():
    blocks = parse_markdown(
        "| Term | Meaning |\n| :--- | :--- |\n| Stimulus | A change |\n"
    )
    table = [b for b in blocks if b.kind == "table"][0]
    assert table.items[0] == ["Term", "Meaning"]
    assert table.items[1] == ["Stimulus", "A change"]


def test_ragged_table_rows_are_padded():
    table = [b for b in parse_markdown(
        "| A | B | C |\n| --- | --- | --- |\n| 1 | 2 |\n"
    ) if b.kind == "table"][0]
    assert all(len(row) == 3 for row in table.items)


def test_heading_levels():
    blocks = [b for b in parse_markdown("# One\n## Two\n### Three\n") if b.kind == "heading"]
    assert [b.level for b in blocks] == [1, 2, 3]


# ── Figures ───────────────────────────────────────────────────────────────

def test_marker_on_its_own_line_becomes_an_image_block():
    assert kinds("Text.\n\n[[IMG:s0fig1]]\n\nMore.\n") == ["para", "image", "para"]


def test_marker_stuck_inside_a_bullet_is_lifted_out():
    """It used to print as literal '[[IMG:s0fig0]]' in the middle of a sentence."""
    md = _hoist_image_markers("- Steam gives an oxide [[IMG:s0fig0]] for example iron.\n\n")
    assert "[[IMG:s0fig0]]" in md
    assert "oxide  for example iron." in md.replace("[[IMG:s0fig0]]", "").replace("\n", " ")
    blocks = parse_markdown("- Steam gives an oxide [[IMG:s0fig0]] for example.\n\n")
    assert kinds("- Steam gives an oxide [[IMG:s0fig0]] for example.\n\n") == ["list", "image"]
    assert "[[IMG" not in blocks[0].items[0][1]


# ── Inline formatting ─────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("**bold**", "<b>bold</b>"),
    ("*italic*", "<i>italic</i>"),
    ("H_2O", "H<sub>2</sub>O"),           # not H<sub>2O</sub>
    ("x^2", "x<super>2</super>"),
    ("CaCO3", "CaCO<sub>3</sub>"),
    ("ZnSO4", "ZnSO<sub>4</sub>"),
    ("NCERT", "NCERT"),                   # all caps, no digits: not a formula
    ("A4 sheet", "A4 sheet"),             # single element-shaped unit
    ("HECU105", "HECU105"),               # an NCERT chapter code, not a formula
    ("JESC106", "JESC106"),
    ("C12H22O11", "C<sub>12</sub>H<sub>22</sub>O<sub>11</sub>"),
    ("Class 10", "Class 10"),             # a plain number is not a formula
    (r"$v = u + at$", "v = u + at"),
    (r"$\frac{1}{2}mv^2$", "1/2mv<super>2</super>"),
])
def test_inline_conversions(text, expected):
    assert inline(text) == expected


def test_no_unencodable_character_reaches_the_page():
    """Anything Helvetica cannot draw must be inside a Unicode-font span, a
    tag, or an ASCII stand-in — a bare one renders as a black box."""
    rendered = inline("CO₂ + H₂O → glucose; λ ≈ 5×10⁻⁷ m; sub‑layer; ΔH ≥ 0")
    # Whatever is drawn from the Unicode font is safe by construction.
    without_symbols = re.sub(r'<font face="NotesSymbols">.*?</font>', "", rendered)
    bare = re.sub(r"<[^>]+>", "", without_symbols)
    for ch in bare:
        ch.encode("cp1252")               # raises if it would box


def test_markup_characters_in_the_text_are_escaped():
    assert inline("5 < 7 & rising") == "5 &lt; 7 &amp; rising"


def test_safe_text_escapes_and_maps():
    assert "&amp;" in safe_text("Acids & Bases")


@pytest.mark.parametrize("subject,expected", [
    ("8th_science_english", "Class 8 Science (English)"),
    ("8th_social_hindi", "Class 8 Social (Hindi)"),
    ("class 10 science", "Class 10 Science"),
])
def test_book_names_are_readable(subject, expected):
    """.title() gave "8Th Science English" in the sources table."""
    assert pretty_book(subject) == expected


def test_a_figure_is_credited_by_book_and_page_not_by_chapter_code():
    """"HECU105" is the pipeline's own file code and means nothing to a student."""
    fig = type("F", (), {"chapter": "hecu105", "page": 7})()
    line = _source_line(fig, "Class 8 Science (English)")
    assert line == "Class 8 Science (English), page 7"
    assert "hecu" not in line.lower()
