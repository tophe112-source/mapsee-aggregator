"""Strict parser for the Jackson Street Jazz Walk timetable PDF."""

from __future__ import annotations

import hashlib
import io
import re
import unicodedata
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from pypdf import PdfReader
from pypdf.generic import ContentStream


class FestivalPdfError(ValueError):
    """The supplied PDF is not the expected, complete Jackson timetable."""


_VENUES = (
    "Casa Latina Center Conf. Room",
    "Cheeky Café",
    "Wonder Ethiopian Restaurant & Bar",
    "Pratt Fine Arts Center Plaza Mural Stage",
    "Pratt Fine Arts Center Plaza Court Stage",
    "Bell Jackson Apartments Atrium",
    "Bell Jackson Apartments Rooftop",
    "F45 Fitness Center",
    "Jackson St. Apartments Street Stage",
    "Jackson St. Apartments Network Lounge",
    "Jackson St. Apartments Bruce Lee Lounge",
    "Central Area Sr. Center Green Dolphin Rm.",
)
_VENUE_MARKERS = (
    "casa", "cheeky", "wonder", "mural", "court", "atrium", "rooftop",
    "f45", "street", "network", "bruce", "dolphin",
)

# Measured block regions in the publisher's 1008x612 one-page template. Keeping
# these separate from content is intentional: names, dates and times are always
# read from the PDF, while a moved/redesigned grid fails loudly.
_REGIONS = (
    (0, 404, 456), (0, 190, 246),
    (1, 285, 325),
    (2, 345, 456), (2, 169, 246), (2, 54, 120),
    (3, 410, 445), (3, 344, 400), (3, 278, 330), (3, 194, 276),
    (4, 398, 446), (4, 169, 276),
    (5, 249, 330),
    (6, 289, 371), (6, 129, 206),
    (7, 345, 411), (7, 169, 237),
    (8, 387, 451), (8, 169, 237),
    (9, 279, 446),
    (10, 367, 451), (10, 169, 237),
    (11, 330, 411), (11, 169, 246),
)

_DATE_RE = re.compile(
    r"\b(\d{1,2})\s+(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|"
    r"Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sept?\.?|September|Oct(?:ober)?|"
    r"Nov(?:ember)?|Dec(?:ember)?)\s+(20\d{2})\b",
    re.I,
)
_TIME_RE = re.compile(
    r"(?<!\d)(\d{1,2}(?::\d{2})?)\s*(?:-|–|—|\|)\s*(\d{1,2}(?::\d{2})?)|"
    r"(?<!\d)(\d{1,2}:\d{2})(?!\d)"
)


def _clean(value: str) -> str:
    value = value.replace("Caf�", "Café").replace("Yellin�", "Yellin’")
    value = unicodedata.normalize("NFKC", value)
    value = re.sub(r"\s+", " ", value).strip(" ,")
    value = re.sub(r"(?<=\d) (?=\d)", "", value)
    # The source font splits these words into two positioned glyph runs. Keep
    # this repair deliberately narrow so a real artist such as "A Perfect
    # Circle" is not silently rewritten as "APerfect Circle".
    for broken, repaired in {
        "Q uartet": "Quartet", "G uest": "Guest", "T rio": "Trio",
        "B and": "Band", "B lues": "Blues", "F unk": "Funk",
        "S oul": "Soul", "writ er": "writer", "S tage": "Stage",
        "T HE": "THE", "L ISTENING": "LISTENING", "R OOM": "ROOM",
    }.items():
        value = value.replace(broken, repaired)
    value = re.sub(r"\s*:\s*", ":", value)
    return re.sub(r"\s*([–—-])\s*", r"\1", value)


def _parse_date(text: str) -> str:
    match = _DATE_RE.search(text)
    if not match:
        raise FestivalPdfError("festival date is missing from the PDF header")
    month = match.group(2).rstrip(".")
    if month.lower() == "sept":
        month = "Sep"
    try:
        return datetime.strptime(
            f"{match.group(1)} {month} {match.group(3)}", "%d %b %Y"
        ).date().isoformat()
    except ValueError:
        return datetime.strptime(
            f"{match.group(1)} {month} {match.group(3)}", "%d %B %Y"
        ).date().isoformat()


def _clock(value: str, previous_hour: int | None = None) -> tuple[int, int]:
    hour_text, _, minute_text = value.partition(":")
    hour, minute = int(hour_text), int(minute_text or 0)
    # This published schedule is labelled PM and runs from 4:30 through 10:30.
    if hour < 12:
        hour += 12
    if previous_hour is not None and hour < previous_hour:
        hour += 12
    return hour, minute


def _iso(date: str, clock: tuple[int, int], timezone: str) -> str:
    hour, minute = clock
    day = datetime.fromisoformat(date)
    if hour >= 24:
        day += timedelta(days=1)
        hour -= 24
    return day.replace(hour=hour, minute=minute, tzinfo=ZoneInfo(timezone)).isoformat()


def _stable_id(date: str, title: str, place: str, occurrence: int) -> str:
    identity = "\0".join((date, title.casefold(), place.casefold(), str(occurrence)))
    return "jackson-jazz-" + hashlib.sha256(identity.encode()).hexdigest()[:16]


def _page_data(page):
    spans = []

    def visit(text, _cm, tm, _font, size):
        text = _clean(text)
        if text:
            spans.append({"x": float(tm[4]), "y": float(tm[5]), "size": float(size), "text": text})

    page.extract_text(visitor_text=visit)

    # LibreOffice emits each table-cell background as a filled rectangle. Track
    # graphics state so only genuinely coloured, full-width schedule cells remain.
    stream = ContentStream(page.get_contents(), page.pdf)
    colour = (0.0, 0.0, 0.0)
    stack = []
    pending = []
    filled = []
    for args, operator in stream.operations:
        if operator == b"q":
            stack.append(colour)
        elif operator == b"Q":
            colour = stack.pop() if stack else (0.0, 0.0, 0.0)
            pending = []
        elif operator == b"rg":
            colour = tuple(float(v) for v in args)
        elif operator == b"g":
            shade = float(args[0])
            colour = (shade, shade, shade)
        elif operator == b"re":
            pending.append(tuple(float(v) for v in args))
        elif operator in (b"f", b"f*"):
            for rect in pending:
                if (max(colour) < 0.95 or max(colour) - min(colour) > 0.08) and rect[2] > 60 and rect[3] > 15:
                    filled.append((*rect, colour))
            pending = []
        elif operator not in (b"W", b"W*", b"n"):
            pending = []
    return spans, filled


def parse_jackson_pdf(pdf_bytes: bytes, timezone: str = "America/Los_Angeles") -> dict:
    """Return the complete internal agenda encoded by a Jackson timetable PDF."""
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
    except Exception as exc:
        raise FestivalPdfError(f"unreadable PDF: {exc}") from exc
    if len(reader.pages) != 1:
        raise FestivalPdfError(f"expected one timetable page, found {len(reader.pages)}")
    page = reader.pages[0]
    width, height = float(page.mediabox.width), float(page.mediabox.height)
    if not (1000 <= width <= 1015 and 605 <= height <= 620):
        raise FestivalPdfError(f"unexpected page geometry {width:g}x{height:g}")
    spans, rectangles = _page_data(page)
    all_text = " ".join(s["text"] for s in spans)
    if "JACKSON STREET JAZZ WALK" not in all_text.upper():
        raise FestivalPdfError("Jackson Street Jazz Walk header is missing")
    date = _parse_date(all_text)

    bounds = (75.0, 148.7, 223.5, 295.9, 371.8, 453.6, 523.6,
              599.0, 678.4, 758.0, 841.4, 916.1, 992.1)
    for column, marker in enumerate(_VENUE_MARKERS):
        header = _clean(" ".join(s["text"] for s in sorted(spans, key=lambda s: (-s["y"], s["x"]))
                                 if bounds[column] < s["x"] < bounds[column + 1] and s["y"] > 475))
        header_words = set(re.findall(r"[a-z0-9]+", header.casefold().replace("é", "e")))
        marker_words = set(re.findall(r"[a-z0-9]+", marker))
        if not marker_words <= header_words:
            raise FestivalPdfError(f"layout changed: venue column {column + 1} does not match {marker!r}")
        body = _clean(" ".join(s["text"] for s in sorted(spans, key=lambda s: (-s["y"], s["x"]))
                               if bounds[column] < s["x"] < bounds[column + 1] and 35 < s["y"] < 470))
        expected = sum(region[0] == column for region in _REGIONS)
        observed = len(list(_TIME_RE.finditer(body)))
        if observed != expected:
            raise FestivalPdfError(
                f"layout changed: venue column {column + 1} has {observed} published times; expected {expected}"
            )
    items = []
    for column, low, high in _REGIONS:
        inside = [s for s in spans if bounds[column] < s["x"] < bounds[column + 1]
                  and low <= s["y"] <= high]
        text = _clean(" ".join(s["text"] for s in sorted(inside, key=lambda s: (-s["y"], s["x"]))))
        match = _TIME_RE.search(text)
        if not match:
            raise FestivalPdfError(f"layout changed: no published time in column {column + 1}, y={low}:{high}")
        before = _clean(text[:match.start()])
        after = _clean(text[match.end():])
        if re.match(r"5:00\s+WELCOME\b", text, re.I):
            title = "Welcome: " + re.sub(r"^WELCOME\s*", "", after, flags=re.I)
        else:
            title = before
        title = re.sub(r"^THE LISTENING ROOM\s+", "", title, flags=re.I)
        title = title.rstrip(" |").strip()
        if not title:
            raise FestivalPdfError(f"performer title missing in {text!r}")
        start_raw = match.group(1) or match.group(3)
        end_raw = match.group(2)
        start_clock = _clock(start_raw)
        item = {"at": _iso(date, start_clock, timezone), "title": title, "place": _VENUES[column]}
        if end_raw:
            item["until"] = _iso(date, _clock(end_raw, start_clock[0]), timezone)
        items.append(item)

    # Two cells print only a start, but their grid cells end at the next slot
    # on the SAME stage. Preserve that boundary explicitly: Mapsee's generic
    # calendar fallback sees simultaneous other stages and can otherwise extend
    # the 6pm dance all the way to the festival's 10:30pm closing time.
    for item in items:
        if 'until' not in item:
            following = [other['at'] for other in items
                         if other['place'] == item['place'] and other['at'] > item['at']]
            if not following:
                raise FestivalPdfError('start-only grid cell has no following stage boundary')
            item['until'] = min(following)

    counts = {}
    for item in sorted(items, key=lambda item: (item["at"], item["place"], item["title"])):
        key = (item["title"].casefold(), item["place"].casefold())
        counts[key] = counts.get(key, 0) + 1
        item["id"] = _stable_id(date, item["title"], item["place"], counts[key])
    starts = [item["at"] for item in items]
    ends = [item.get("until", item["at"]) for item in items]
    return {
        "date": date,
        "start": min(starts),
        "end": max(ends),
        "agenda": sorted(items, key=lambda item: (item["at"], item["place"])),
        "diagnostics": {
            "page_size": [width, height],
            "venue_columns": len(_VENUES),
            "filled_rectangles_seen": len(rectangles),
            "measured_regions": len(_REGIONS),
            "agenda_items": len(items),
        },
    }
