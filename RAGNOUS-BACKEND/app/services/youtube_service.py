"""YouTube lesson lookup for the tutor chat.

A student who says "I don't get it" is usually better served by watching someone
teach the topic than by a third rewrite of the same bullet points. This module
finds a short, on-topic, embeddable lesson video and returns it in a shape the
chat UI can render inline.

Two search paths, in order of preference:

  1. YouTube Data API v3 (set YOUTUBE_API_KEY). This is the only path that can
     tell us whether a video is actually embeddable and how long it is, so the
     player never lands on "Video unavailable" and we never hand a student a
     three-hour livestream.
  2. A scrape of the public results page, used when no key is configured. It
     yields id/title/channel/duration but nothing about embeddability, so those
     results are marked `verified: False` and the UI keeps a "watch on YouTube"
     escape hatch.

Every candidate is scored for teaching value rather than popularity — the raw
top hit for "photosynthesis" is frequently a rhyming song for six-year-olds or
a coaching-centre advertisement.
"""

import json
import os
import re
import urllib.parse
import urllib.request
from typing import Dict, List, Optional

_API_SEARCH = "https://www.googleapis.com/youtube/v3/search"
_API_VIDEOS = "https://www.googleapis.com/youtube/v3/videos"
_SCRAPE_URL = "https://www.youtube.com/results"

# Desktop UA — the mobile/bot response omits the JSON blob we parse.
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)

_HTTP_TIMEOUT = 8

# A lesson shorter than this is a teaser or a Short; longer than this is a
# full coaching class the student will not sit through mid-chat.
_MIN_SECONDS = 90
_MAX_SECONDS = 30 * 60

# Channels that teach the Indian school syllabus. A match is a strong signal
# that the video is aimed at exactly this student.
_TRUSTED_CHANNELS = {
    "khan academy", "khan academy india", "byju", "byju's", "vedantu",
    "physics wallah", "physicswallah", "unacademy", "magnet brains",
    "learnohub", "exam fear", "examfear", "ncert official", "swayam",
    "doubtnut", "toppr", "shobhit nirwan", "dear sir", "manocha academy",
    "cbse", "infinity learn", "extramarks", "digital teacher",
    "amoeba sisters", "crash course", "veritasium", "sci show", "scishow",
    "ted-ed", "ted ed", "bozeman science", "professor dave explains",
    "the organic chemistry tutor", "3blue1brown", "kurzgesagt",
}

_GOOD_TITLE_WORDS = {
    "explained", "explanation", "lesson", "lecture", "class", "chapter",
    "concept", "concepts", "tutorial", "animation", "animated", "ncert",
    "cbse", "basics", "introduction", "example", "examples", "solved",
    "derivation", "numericals", "revision", "one shot", "full chapter",
}

# Content that is technically about the topic but is not a lesson.
_BAD_TITLE_WORDS = {
    "shorts", "#shorts", "song", "rhyme", "rap", "meme", "memes", "funny",
    "prank", "vlog", "reaction", "unboxing", "giveaway", "admission",
    "scholarship", "fees", "batch launch", "demo class", "join now",
    "motivational", "motivation", "topper interview", "study with me",
    "asmr", "live class", "trailer", "teaser", "promo", "webinar",
}

_QUERY_STOPWORDS = {
    "a", "an", "the", "of", "for", "and", "in", "on", "with", "to", "is",
    "are", "what", "how", "why", "explain", "show", "me", "video", "please",
    "class", "ncert", "cbse", "understand", "understanding",
}


def _http_json(url: str) -> Optional[dict]:
    try:
        request = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8", "replace"))
    except Exception as e:
        print(f"[YT] request failed for {url.split('?')[0]}: {e}")
        return None


def _http_text(url: str) -> Optional[str]:
    try:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": _UA,
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT) as response:
            return response.read().decode("utf-8", "replace")
    except Exception as e:
        print(f"[YT] scrape failed: {e}")
        return None


def _tokens(text: str) -> set:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {w for w in words if w not in _QUERY_STOPWORDS and len(w) > 2}


def _parse_iso8601_duration(value: str) -> int:
    """'PT8M14S' -> 494. Returns 0 when the format is unexpected."""
    m = re.match(r"^P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$", value or "")
    if not m:
        return 0
    days, hours, minutes, seconds = (int(g or 0) for g in m.groups())
    return ((days * 24 + hours) * 60 + minutes) * 60 + seconds


def _parse_clock_duration(value: str) -> int:
    """'8:14' / '1:02:03' -> seconds. Returns 0 when unparseable."""
    parts = [p for p in (value or "").strip().split(":") if p.isdigit()]
    if not 1 <= len(parts) <= 3:
        return 0
    total = 0
    for part in parts:
        total = total * 60 + int(part)
    return total


def _format_duration(seconds: int) -> str:
    if seconds <= 0:
        return ""
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


# "Class 10", "class-10", "10th class", "std 12", "grade 9"
_CLASS_IN_TITLE_RE = re.compile(
    r"(?:\b(?:class|std|grade)\s*[-–]?\s*(\d{1,2})\b)"
    r"|(?:\b(\d{1,2})\s*(?:st|nd|rd|th)\s*(?:class|std|grade)\b)",
    re.IGNORECASE,
)


def _classes_in_title(title: str) -> set:
    found = set()
    for a, b in _CLASS_IN_TITLE_RE.findall(title or ""):
        value = a or b
        if value and 1 <= int(value) <= 12:
            found.add(int(value))
    return found


def _class_ok(title: str, student_class: int) -> bool:
    """False only when the title clearly targets a different class."""
    named = _classes_in_title(title)
    return not named or student_class in named


def _score_video(video: dict, topic: str, student_class: Optional[int]) -> float:
    """Teaching value of one candidate for this student. Higher is better."""
    title = (video.get("title") or "").lower()
    channel = (video.get("channel") or "").lower()
    description = (video.get("description") or "").lower()
    duration = video.get("durationSeconds") or 0

    topic_tokens = _tokens(topic)
    title_tokens = _tokens(title)

    score = 0.0

    # Does the title actually cover what the student asked about?
    if topic_tokens:
        score += 4.0 * len(topic_tokens & title_tokens) / len(topic_tokens)
        if topic_tokens & _tokens(description):
            score += 0.5

    # A channel that teaches this syllabus for a living.
    if any(name in channel for name in _TRUSTED_CHANNELS):
        score += 3.0

    if title_tokens & _GOOD_TITLE_WORDS:
        score += 1.0
    if _tokens(title) & _BAD_TITLE_WORDS or "#shorts" in title:
        score -= 3.5

    # The student's own class named in the title is a near-perfect match.
    if student_class and re.search(rf"class\s*{student_class}\b", title):
        score += 2.0
    elif student_class:
        other = re.search(r"class\s*(\d{1,2})\b", title)
        if other and int(other.group(1)) != student_class:
            # A Class 12 derivation shown to a Class 8 student is noise.
            score -= 1.5 if abs(int(other.group(1)) - student_class) > 1 else 0.0

    # Length: reward the 3-15 minute explainer band, penalise the extremes.
    if duration:
        if _MIN_SECONDS <= duration <= 15 * 60:
            score += 1.5
        elif duration > _MAX_SECONDS or duration < _MIN_SECONDS:
            score -= 3.0

    # Mild popularity tiebreak between otherwise equal lessons.
    views = video.get("viewCount") or 0
    if views > 1_000_000:
        score += 0.6
    elif views > 100_000:
        score += 0.4
    elif views > 10_000:
        score += 0.2

    return score


# Below this, nothing found is convincingly a lesson on the asked topic, and a
# wrong video wastes more of the student's time than no video at all.
_MIN_SCORE = 2.0


def _build_query(topic: str, subject_area: str, student_class: Optional[int]) -> str:
    parts = [topic.strip()]
    if subject_area and subject_area.lower() != "other":
        parts.append(subject_area.strip())
    if student_class:
        parts.append(f"class {student_class}")
    parts.append("NCERT explained")
    return " ".join(p for p in parts if p)


# ── Path 1: YouTube Data API v3 ───────────────────────────────────────────

def _search_via_api(api_key: str, query: str, limit: int) -> List[dict]:
    search_url = _API_SEARCH + "?" + urllib.parse.urlencode({
        "key": api_key,
        "part": "snippet",
        "q": query,
        "type": "video",
        "maxResults": max(limit * 4, 12),
        "safeSearch": "strict",
        "relevanceLanguage": "en",
        "regionCode": "IN",
        # Only videos the embedded player is allowed to load at all.
        "videoEmbeddable": "true",
        "videoSyndicated": "true",
    })
    payload = _http_json(search_url)
    if not payload:
        return []

    candidates = {}
    for item in payload.get("items") or []:
        video_id = ((item.get("id") or {}).get("videoId") or "").strip()
        snippet = item.get("snippet") or {}
        if not video_id:
            continue
        candidates[video_id] = {
            "id": video_id,
            "title": snippet.get("title") or "",
            "channel": snippet.get("channelTitle") or "",
            "description": (snippet.get("description") or "")[:300],
            "thumbnail": (
                ((snippet.get("thumbnails") or {}).get("medium") or {}).get("url")
                or f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg"
            ),
            "verified": True,
        }

    if not candidates:
        return []

    # Second call: duration, embeddability and view count are only on videos.list.
    details_url = _API_VIDEOS + "?" + urllib.parse.urlencode({
        "key": api_key,
        "part": "contentDetails,status,statistics",
        "id": ",".join(candidates),
    })
    details = _http_json(details_url)
    for item in (details or {}).get("items") or []:
        video = candidates.get(item.get("id"))
        if not video:
            continue
        status = item.get("status") or {}
        if not status.get("embeddable", True) or status.get("privacyStatus") == "private":
            candidates.pop(item["id"], None)
            continue
        seconds = _parse_iso8601_duration(
            (item.get("contentDetails") or {}).get("duration") or ""
        )
        video["durationSeconds"] = seconds
        video["duration"] = _format_duration(seconds)
        try:
            video["viewCount"] = int((item.get("statistics") or {}).get("viewCount") or 0)
        except (TypeError, ValueError):
            video["viewCount"] = 0

    return list(candidates.values())


# ── Path 2: public results page ───────────────────────────────────────────

def _search_via_scrape(query: str) -> List[dict]:
    url = _SCRAPE_URL + "?" + urllib.parse.urlencode({
        "search_query": query,
        # EgIQAQ%3D%3D — filter the result set to videos only.
        "sp": "EgIQAQ%3D%3D",
    })
    html = _http_text(url)
    if not html:
        return []

    # The page ships its results as one big JSON blob. Splitting on the
    # videoRenderer key is far more robust than trying to balance the braces of
    # a structure whose schema changes every few months.
    videos: List[dict] = []
    seen = set()
    for blob in html.split('"videoRenderer":')[1:]:
        chunk = blob[:2500]

        id_match = re.search(r'"videoId":"([\w-]{11})"', chunk)
        if not id_match or id_match.group(1) in seen:
            continue
        video_id = id_match.group(1)

        title_match = re.search(r'"title":\{"runs":\[\{"text":"(.*?)"\}', chunk)
        channel_match = re.search(
            r'"ownerText":\{"runs":\[\{"text":"(.*?)"', chunk
        ) or re.search(r'"longBylineText":\{"runs":\[\{"text":"(.*?)"', chunk)
        # lengthText nests an accessibility object before simpleText, so this
        # cannot be a `[^}]*` scan — it would stop at the inner closing brace.
        duration_match = re.search(
            r'"lengthText":\{.*?"simpleText":"([\d:]+)"', chunk
        )
        views_match = re.search(r'"viewCountText":\{"simpleText":"([\d,]+)', chunk)

        if not title_match:
            continue

        def _unescape(text: str) -> str:
            try:
                return json.loads(f'"{text}"')
            except json.JSONDecodeError:
                return text

        seconds = _parse_clock_duration(duration_match.group(1) if duration_match else "")
        seen.add(video_id)
        videos.append({
            "id": video_id,
            "title": _unescape(title_match.group(1)),
            "channel": _unescape(channel_match.group(1)) if channel_match else "",
            "description": "",
            "thumbnail": f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg",
            "duration": _format_duration(seconds),
            "durationSeconds": seconds,
            "viewCount": int(views_match.group(1).replace(",", "")) if views_match else 0,
            # No embeddability signal on this path.
            "verified": False,
        })
        if len(videos) >= 20:
            break

    return videos


# ── Public entry point ────────────────────────────────────────────────────

def find_lesson_videos(
    topic: str,
    subject_area: str = "",
    student_class: Optional[int] = None,
    limit: int = 3,
) -> List[Dict]:
    """Best teaching videos for `topic`, best first. May return [].

    Blocking (urllib); call it with `asyncio.to_thread` from async code.
    """
    if not (topic or "").strip():
        return []

    query = _build_query(topic, subject_area, student_class)
    api_key = os.getenv("YOUTUBE_API_KEY")

    candidates = _search_via_api(api_key, query, limit) if api_key else []
    if not candidates:
        if api_key:
            print("[YT] Data API returned nothing usable; falling back to scrape")
        candidates = _search_via_scrape(query)

    if not candidates:
        print(f"[YT] no candidates for {query!r}")
        return []

    # Drop the unwatchable extremes before ranking so a 4-hour livestream can
    # never win on channel reputation alone.
    usable = [
        v for v in candidates
        if not v.get("durationSeconds") or _MIN_SECONDS <= v["durationSeconds"] <= _MAX_SECONDS
    ] or candidates

    # Hard class filter. A title that names a class is making a promise about
    # its syllabus level; if that promise is for a different class, the video
    # is dropped rather than merely ranked lower. Titles that name no class are
    # kept — most good explainers do not mention one.
    if student_class:
        on_level = [v for v in usable if _class_ok(v.get("title"), student_class)]
        dropped = len(usable) - len(on_level)
        if dropped:
            print(f"[YT] dropped {dropped} result(s) aimed at another class")
        if on_level:
            usable = on_level
        else:
            print("[YT] every result was aimed at another class")
            return []

    ranked = sorted(
        ((_score_video(v, topic, student_class), v) for v in usable),
        key=lambda pair: pair[0],
        reverse=True,
    )

    best_score = ranked[0][0]
    print(
        f"[YT] query={query!r} best={ranked[0][1]['title']!r} "
        f"score={best_score:.2f} (of {len(candidates)} candidates)"
    )
    if best_score < _MIN_SCORE:
        print("[YT] nothing cleared the relevance bar; no video attached")
        return []

    picked = []
    for score, video in ranked[:limit]:
        if score < _MIN_SCORE / 2:
            break
        picked.append({
            "id": video["id"],
            "title": video["title"][:140],
            "channel": video["channel"][:60],
            "thumbnail": video["thumbnail"],
            "duration": video.get("duration") or "",
            "url": f"https://www.youtube.com/watch?v={video['id']}",
            "verified": bool(video.get("verified")),
        })
    return picked
