"""Behaviour of the notes pipeline that is not about layout.

Every case here is something that went wrong on a real run: a fallback that
never fired, a Class 8 chat answered out of a Hindi book, dot-leader exercise
pages used as source material, a heading that read like a shopping list.

Run with: python -m pytest tests/test_notes_service.py
"""

import asyncio

import pytest

from app.services import notes_service as ns
from app.services.search_service import medium_of


class _Stub:
    """Minimal stand-in for a chat model."""

    def __init__(self, reply=None, error=None, delay=0.0):
        self.reply, self.error, self.delay = reply, error, delay
        self.calls = 0

    async def ainvoke(self, messages):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return type("R", (), {"content": self.reply})()


RATE_LIMIT = Exception(
    "Error code: 429 - {'error': {'message': 'Rate limit reached ... "
    "Please try again in 8.52s', 'code': 'rate_limit_exceeded'}}"
)


@pytest.fixture
def providers(monkeypatch):
    """Silence the real clients; each test installs the ones it needs."""
    for attr in ("notes_llm", "notes_gemini", "notes_gemini_backup", "gemini_llm"):
        monkeypatch.setattr(ns, attr, None)
    return monkeypatch


# ── The fallback chain ────────────────────────────────────────────────────

def test_gemini_answers_when_groq_is_rate_limited(providers):
    """The failure this was written for: Groq 429s and the notes came back
    without that section instead of being written by Gemini."""
    groq = _Stub(error=RATE_LIMIT)
    gemini = _Stub(reply="## Volcanoes\n\nDetailed section.")
    providers.setattr(ns, "notes_llm", groq)
    providers.setattr(ns, "notes_gemini", gemini)

    out = asyncio.run(ns._invoke([]))

    assert out.startswith("## Volcanoes")
    assert groq.calls == 1 and gemini.calls == 1


def test_a_rate_limit_is_not_waited_out_while_gemini_is_available(providers):
    """Sleeping for Groq's retry-after before trying Gemini just added the
    wait to the download."""
    providers.setattr(ns, "notes_llm", _Stub(error=RATE_LIMIT))
    providers.setattr(ns, "notes_gemini", _Stub(reply="written"))

    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    providers.setattr(ns.asyncio, "sleep", fake_sleep)
    assert asyncio.run(ns._invoke([])) == "written"
    assert slept == []


def test_groq_is_retried_after_the_wait_when_everything_else_failed(providers):
    groq = _Stub(error=RATE_LIMIT)
    providers.setattr(ns, "notes_llm", groq)
    providers.setattr(ns, "notes_gemini", _Stub(error=Exception("503 high demand")))

    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)
        groq.error = None            # the limit has passed
        groq.reply = "late but written"

    providers.setattr(ns.asyncio, "sleep", fake_sleep)
    assert asyncio.run(ns._invoke([])) == "late but written"
    assert slept and slept[0] == pytest.approx(9.52, abs=0.01)


def test_a_dead_provider_moves_on_to_the_next(providers):
    providers.setattr(ns, "notes_gemini", _Stub(error=Exception("503 UNAVAILABLE")))
    providers.setattr(ns, "notes_gemini_backup", _Stub(reply="from 3.6"))
    assert asyncio.run(ns._invoke([])) == "from 3.6"


def test_an_empty_reply_counts_as_a_failure(providers):
    providers.setattr(ns, "notes_gemini", _Stub(reply="   "))
    providers.setattr(ns, "notes_gemini_backup", _Stub(reply="real text"))
    assert asyncio.run(ns._invoke([])) == "real text"


def test_every_provider_failing_raises_with_all_the_reasons(providers):
    providers.setattr(ns, "notes_gemini", _Stub(error=Exception("503")))
    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(ns._invoke([]))
    assert "503" in str(excinfo.value)


def test_gemini_returns_content_as_a_list_of_parts(providers):
    """Gemini answers with [{'type': 'text', 'text': ...}], not a string."""
    providers.setattr(ns, "notes_gemini",
                      _Stub(reply=[{"type": "text", "text": "part one "},
                                   {"type": "text", "text": "part two"}]))
    assert asyncio.run(ns._invoke([])) == "part one part two"


# ── Source material ───────────────────────────────────────────────────────

@pytest.mark.parametrize("name,expected", [
    ("8th_science_english", "en"),
    ("8th_science_hindi", "hi"),
    ("8th_science_punjabi", "pa"),
    ("hhcu109", "hi"),          # the Class 8 Hindi chapters from the bad run
    ("hecu108", "en"),
    ("jesc106", "en"),
    ("class 10 science", "en"),
])
def test_book_medium_is_read_from_either_naming_scheme(name, expected):
    assert medium_of(name) == expected


def test_dot_leader_exercise_pages_are_not_used_as_source():
    assert not ns._usable_passage("." * 400)
    assert not ns._usable_passage("Fill in the blanks: " + ". " * 200)
    assert not ns._usable_passage("Too short to teach anything.")


def test_real_textbook_prose_is_kept():
    passage = (
        "A reflex action is a sudden and involuntary response to a stimulus. "
        "Receptors in the skin detect the heat and a sensory neuron carries the "
        "impulse to the spinal cord, where relay neurons pass it to a motor neuron."
    )
    assert ns._usable_passage(passage)


# ── Topics ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("topic,heading", [
    ("Metal reactions with acids, oxygen, reactivity", "Metal reactions with acids"),
    ("Reflex action, reflex arc, spinal cord", "Reflex action"),
    ("Acids, Bases and Salts", "Acids, Bases and Salts"),   # a real chapter name
    ("Photosynthesis", "Photosynthesis"),
])
def test_headings_read_like_a_contents_page(topic, heading):
    assert ns.display_title(topic) == heading


def test_near_identical_topics_are_not_written_twice():
    assert ns._duplicate_topic("Reflex arc", ["Reflex action"])
    assert not ns._duplicate_topic("Human brain", ["Reflex action"])
