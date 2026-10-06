#!/usr/bin/env python3
"""
mapsee_ingest_glenecho.py - import Glen Echo Park's events calendar
(https://glenechopark.org/events-calendar) into the Mapsee store.

    python mapsee_ingest_glenecho.py --config glenecho_sources.json \
        --store feeds_events.json [--max-minutes 12] [--dry-run]

WHAT THE PLACE IS
-----------------
A National Park Service site in Glen Echo, Montgomery County MD, run by the
Glen Echo Park Partnership for Arts and Culture: the 1933 Spanish Ballroom, the
Bumper Car Pavilion, two children's theatres (the Puppet Co. and Adventure
Theatre MTC) and a ring of resident studios and galleries. It is the DC area's
community dance hall - a contra, tango, folk, blues, swing, waltz or ballroom
dance most nights of the week, each with a lesson first and a fee at the door.
That is exactly the "what is on at the local community place" supply the owner
asked for (docs/agents/community-centres.md), and nobody else carries it.

WHY THIS IS A SCRAPER
---------------------
The site publishes no feed. Checked 2026-10-04: robots.txt allows every path
used here (only /admin/, /search/, /user/... and /core/ are disallowed); no
iCal link or endpoint, no schema.org JSON-LD on listing or detail pages, and
the Drupal 8 view renders server-side. So: the month listing, then one detail
page per event - like mapsee_ingest_seattlecenter.py and pioneersquare.

THE YEAR COMES FROM OUR OWN REQUEST, NEVER FROM THE PAGE
--------------------------------------------------------
Neither page states a year. The card says "October 4", the detail page's
<time> says "Oct 04, 2:45 pm" and its datetime attribute is a broken "00Z" on
every page sampled. The month listing is requested by URL as
/events-calendar/YYYYMM, so the year and month are the ones WE asked for; a
card whose month name is not that month is refused, and a detail page whose
month/day disagrees with its card is refused - loudly, both - rather than
patched from today's date (a guessed year is the well-formed, plausible, wrong
value that survives every later check). The view shows today onward: the
October page began at October 4 on 2026-10-04.

EVERY OCCURRENCE IS ITS OWN NODE, AND THE NODE ID IS THE IDENTITY
-----------------------------------------------------------------
The weekly tango on four Sundays is four detail pages (7618, 7619, 7621,
7622), each with its own Location and Admission. So nothing is inherited from
another date's page; source_id is the node id, and the fingerprint is widened
by the clock (as seattlecenter's is) so two same-titled sessions on one day in
one room stay two rows.

GALLERY AND STUDIO OPEN HOURS ARE NOT EVENTS
--------------------------------------------
240 of the 430 cards from 2026-10-04 to 12-31 (55.8%). They are the opening
hours of nine resident studios and galleries ("SILVERWORKS 11:00am - 5:00pm", "PARK VIEW GALLERY | INHERITED
THREADS 10:00am - 6:00pm" every day of a month), a business's standing hours
rather than an occasion - the shape recurring_days exists for, and the job of
the civic layer, not of a dated pin repeated every day. `open_hours_titles` in
the config names them, they are refused from the LISTING without a detail
request, and the count is printed every run. An artist talk or an opening in
the same gallery has its own title and is kept.

WHAT ELSE IS REFUSED, AND COUNTED
---------------------------------
Lead's policy (docs/agents/community-centres.md): a session anyone can turn up
to is in, with its fee stated; registration-only courses, private bookings,
closures, members-only, cancelled, sold-out and online-only rows are out. The
registration test is the source's own words ("registration required", "must
register", "registration only") - a "Register >>" or "Sign Up >>" link next to
a per-class price is how this site sells a single class, and stays in.

THE FEE IS STATED, AND "FREE" ONLY WHEN THE PARK SAYS SO
--------------------------------------------------------
The Admission field is the source's words, and ../mapsee's 0227 offer tagger
reads the description. "Admission: FREE" becomes "Admission: free." (the form
its strict `free` reads). Anything else - "$15/adult, $5/teen, FREE for ages 12
and under", "See description for specific pricing" - is written with
"(not free)" after it, which 0227's negation vetoes on, so a dance whose blurb
says "free lesson before the dance" can never read as a free event.

TIMES
-----
Wall clock in America/New_York, from the card's TIME LINE - the words the park
wrote ("2:45pm - 6:00pm", "2:45 - 5:30pm", "7:30pm - 12:30am", which ends the
next day, or "2:45pm: Lesson | 3:30pm - 6:00pm: Social Dance", read as one span
only when its parts meet end to start). The detail page's <time> field is used
only where a card has no time line, and its end only when its start agrees:
on 2026-10-04 it disagreed with the time line on 3 of 184 rows. The Junior
Ranger swearing-in (7685, 7686) reads "11:00am" on its card, on its page and
in "the first Saturday of each month at 11:00am", while its <time> says 10:00,
copied from the tour it follows; the Viennese Ball's (7638) 8:00pm is none of
its line's times (7:30 lesson, 8:15 check-in, 8:40 dance). Each disagreement
is printed and counted. A row is one contiguous session; a
schedule with a gap is not read as one. "showtimes vary" (the two children's
theatres, 39 rows) is a DATE with no clock and says so in the description - the
<time> field then holds one of several performances, and a clock would claim
the others do not exist. A card or a <time> pair naming two different days is
refused and counted (0 seen), not guessed into a one-day row.

PLACEMENT
---------
From the VENUE BOOK in the config, keyed on the detail page's own `Location`
string, never from the page. Coordinates are OpenStreetMap's (via Geofabrik
Postpass, 2026-10-04), one building each; `coords_exact` only on buildings. A
Location not in the book is skipped LOUDLY; that log line is how the book
grows. A page with no Location at all gets the park's own pin, inexact.

MEASURED 2026-10-04 (live, Oct 4 - Jan 2): 4 month pages and 185 detail
pages, 190 requests with robots.txt, 190 s at one per second. 430 cards; 240
open hours, 5 "CANCELLED TODAY-" dances and 1 "Virtual" artist talk refused;
184 rows kept, every one placed (176 on a building, 8 on the park pin), 0
duplicate fingerprints. 28 free by the park's own "Admission: FREE" - and
../mapsee's 0227 Python twin (tools/measure_deals.py) tags exactly those 28 and
no fee row. 39 date-only (showtimes vary). Primary after derive_categories:
community 129, kids 41, fitness 7, arts 6, music 1; music is a layer on 47.
One pair shares title, presenter and start in two rooms (9581, 9658: Ballroom
Time, 2026-12-27) and is printed every run - possibly one dance posted twice.

HTTP: our own User-Agent, robots.txt checked per host before the first
request, one request per `pause` seconds (Crawl-delay honoured if larger), a
redirect followed only to a host whose robots.txt was also checked, and a
401/403/429 or a bot challenge ends the site's run - it is a refusal. A
network error or a 5xx costs that one page (printed, counted); five failures
in a row (`max_failures_in_row`) end the run, since the host is not answering.
"""
from __future__ import annotations

import argparse
import hashlib
import html as html_mod
import json
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlsplit

from mapsee_ingest import NormalizedEvent, EventStore, make_fingerprint

SOURCE = "glenecho"
TZ_NAME = "America/New_York"
USER_AGENT = "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)"
DEFAULT_MAX_MINUTES = 12.0
DEFAULT_WITHIN_DAYS = 90
SAVE_EVERY = 40          # rows between store saves, so a cancelled step keeps its work

_MONTHS = ["january", "february", "march", "april", "may", "june", "july",
           "august", "september", "october", "november", "december"]
_MON = {m: i for i, m in enumerate(_MONTHS, start=1)}
_MON.update({m[:3]: i for i, m in enumerate(_MONTHS, start=1)})

_TAG = re.compile(r"<[^>]+>")

# A listing card: the title anchor, then the card's date, then the time line.
_CARD_SPLIT = re.compile(r'<div class="calendar-box flex-container">')
_CARD_HEAD = re.compile(
    r'(?is)href="/events-calendar/event-detail/(\d+)"[^>]*>(.*?)</a>\s*<br\s*/?>\s*'
    r'<time[^>]*>(.*?)</time>')
# "December 27 - December 27": a card's head can carry a second <time>. Every
# one seen on 2026-10-04 (10) named the same day; one that names ANOTHER day is
# refused and counted, so a multi-day card is measured before it is guessed.
_CARD_HEAD_END = re.compile(r"(?is)\s*(?:-|–|—|to)\s*<time[^>]*>(.*?)</time>")
# The time line is the card's first <p>, bare or with attributes - the Ballroom
# Time and Viennese Ball cards paste theirs from Google Docs as <p dir="ltr">,
# and a bare-<p> pattern missed both. A card without one goes straight to the
# blurb, which is a NESTED <p><p>, so that is refused here - otherwise a blurb
# would become "Times: Experience a real Strauss Viennese Ball...".
_CARD_TIME = re.compile(r"(?is)</strong>\s*</div>\s*<p(?:\s[^>]*)?>(?!\s*<p)(.{0,400}?)</p>")

# The detail page's own content block, and inside it the <time>, the
# "Label: value" rows, the description and the links.
_CONTENT = re.compile(r'(?is)<div class="views-element-container">(.*?)<!-- /#content -->')
_DETAIL_TIME = re.compile(r"(?is)<time[^>]*>\s*([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s*([^<]*)</time>")
# "<time>Dec 27, 2:45 pm</time> - <time>Dec 27, 6:00 pm</time>" (9658): the end.
_DETAIL_END = re.compile(r"(?is)\s*(?:-|–|—|to)\s*<time[^>]*>\s*([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s*([^<]*)</time>")
_FIELD = re.compile(r"(?is)<div>\s*<strong>\s*([^<:]{2,40}?)\s*</strong>\s*:\s*(.*?)</div>")
_DESC = re.compile(r"(?is)<strong>\s*Description\s*</strong>\s*</p>(.*?)(?:<div>\s*<strong>|$)")
_ANCHOR = re.compile(r'(?is)<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>')
_IMG = re.compile(r'(?is)<img[^>]+src="(/sites/default/files/[^"]+)"')
_LINK_LINE = re.compile(r"(?is)<p[^>]*>(?:(?!</p>).)*Park\s+Map(?:(?!</p>).)*</p>")

# --------------------------------------------------------------------------- #
# Refusals. Each returns a reason string; the run prints every count.
_CANCELLED = re.compile(r"\b(?:cancel+ed|postponed|rescheduled)\b", re.I)
_SOLD_OUT = re.compile(r"\bsold[\s-]*out\b", re.I)
_CLOSURE = re.compile(r"\b(?:closed|closure|no\s+dance\s+tonight)\b", re.I)
_PRIVATE = re.compile(r"\bprivate\s+(?:event|party|rental|function|booking)\b|\brental\b", re.I)
_MEMBERS = re.compile(r"\bmembers?\s+only\b|\bfor\s+members\s+only\b", re.I)
_REG_ONLY = re.compile(
    r"\b(?:pre-?registration|advance\s+registration|registration)\s+(?:is\s+)?"
    r"(?:required|mandatory|necessary)\b|\bmust\s+(?:pre-?)?register\b|"
    r"\bby\s+registration\s+only\b|\bregistration\s+only\b|"
    r"\bsign[\s-]?up\s+(?:is\s+)?required\b|\bregistered\s+students\s+only\b", re.I)
# NOT a bare "online only": the Bumper Car Squares' Admission reads "$12/adult
# advance online only" - the TICKET is online-only, the dance is in the room.
# "No registration required." - the NPS civil-rights walking tour, 2026-10-04,
# refused as registration-only by the first cut of _REG_ONLY. Struck first.
_REG_NOT = re.compile(
    r"\b(?:no|not|without)\s+(?:any\s+)?(?:pre-?|advance\s+)?(?:registration|sign[\s-]?up)"
    r"(?:\s+(?:is|was))?(?:\s+(?:required|necessary|needed|mandatory))?\b|"
    r"\b(?:registration|sign[\s-]?up)\s+(?:is\s+)?(?:not|never)\s+(?:required|necessary|needed)\b|"
    r"\b(?:preferred|encouraged|appreciated|recommended),?\s+(?:but\s+)?not\s+required\b", re.I)
_ONLINE = re.compile(r"\b(?:on|via)\s+zoom\b|\bzoom\s+(?:link|meeting)\b|"
                     r"\b(?:held|takes\s+place|offered|meets)\s+(?:entirely\s+|only\s+)?(?:online|virtually)\b|"
                     r"\bvirtual\s+(?:event|class|session|program)\b|"
                     r"\bonline[\s-]only\s+(?:event|class|session|program)\b", re.I)
# A Location that is not a place: "Virtual" (an artist talk, 2026-10-04).
_ONLINE_PLACE = re.compile(r"^\s*(?:virtual|online|zoom|web(?:inar)?|live\s*stream)\b", re.I)
_MEETING = re.compile(r"\b(?:board|annual|general|public)\s+meeting\b|\bpublic\s+hearing\b", re.I)


def refusal(title: str, text: str) -> Optional[str]:
    """Why a row is not a session anyone can turn up to, or None. `text` is the
    description and Admission together."""
    t = title or ""
    if _CANCELLED.search(t):
        return "cancelled"
    if _SOLD_OUT.search(t):
        return "sold out"
    if _CLOSURE.search(t):
        return "closure"
    if _PRIVATE.search(t):
        return "private booking"
    if _MEETING.search(t):
        return "governance meeting"
    if _MEMBERS.search(t) or _MEMBERS.search(text or ""):
        return "members only"
    if _REG_ONLY.search(_REG_NOT.sub(" ", text or "")):
        return "registration required"
    if _ONLINE.search(t) or _ONLINE.search(text or ""):
        return "online only"
    return None


# THE CARD THAT CALLS A DANCE OFF NAMES THE DANCE. A dance the park retitles
# "CANCELLED TODAY- <its title>" keeps its date, time line and room, so the
# row we wrote from it is the same card under its own title. called_off()
# gives that title back and the run tombstones the fingerprint the live row
# had (EventStore.cancel) instead of leaving it on the map. "Postponed" says
# the same of that date. NOT "rescheduled": that card may sit on the NEW date,
# where the dance is on. notice_reason() in mapsee_ingest does not read
# "CANCELLED TODAY-" (the word is followed by TODAY, not by a separator).
# MEASURED 2026-10-05: the 5 such cards in the window (nodes 8845-8852, Nov
# 20 - Dec 31) were notices posted on their own, titled "CAPITAL BLUES DANCE"
# and "FRIDAY NIGHT CONTRA DANCE", while the weekly series are "BLUES DANCE"
# (nodes 7569-7578) and "CONTRA DANCE" (9627-9635) - contiguous ids in date
# order, no gap where a deleted node would sit: as far as the listing shows,
# those holiday dates were never listed live, and the 5 tombstones match no
# row (the sync ignores a tombstone it cannot find).
# The 5 requests are paid for the dance retitled in place; a series node that
# is DELETED instead is absence, and mapsee_supabase_sync --retire-absent's.
_CALLED_OFF_HEAD = re.compile(r"^\W*(?:cancel+ed|postponed)(?:\s+(?:today|tonight))?\s*[-\u2013\u2014:|!*]+\s*", re.I)
_CALLED_OFF_TAIL = re.compile(r"\s*[-\u2013\u2014:|(\[*]+\s*(?:cancel+ed|postponed)(?:\s+(?:today|tonight))?\W*$", re.I)


def called_off(title: Optional[str]) -> Optional[str]:
    """The dance's own title inside a "CANCELLED TODAY- <title>" card, or None
    when the card names no event (a closure, "Rescheduled", the word alone)."""
    t = (title or "").strip()
    for rx in (_CALLED_OFF_HEAD, _CALLED_OFF_TAIL):
        cut = rx.sub("", t, count=1).strip()
        if cut != t:
            if re.search(r"\w", cut) and refusal(cut, "") is None:
                return cut
            return None
    return None


def is_open_hours(title: str, names: List[str]) -> bool:
    """A resident studio's or gallery's opening hours: the title IS the house's
    name ("SILVERWORKS"), or the name and an exhibition ("POPCORN GALLERY |
    THE DECONSTRUCTED CATHEDRAL"). An artist talk in that gallery has its own
    title and is not matched."""
    t = re.sub(r"\s+", " ", (title or "").strip()).lower()
    for n in names or ():
        n = n.strip().lower()
        if t == n or t.startswith(n + " |") or t.startswith(n + ":"):
            return True
    return False


# --------------------------------------------------------------------------- #
# Text

def _clean(s: Optional[str]) -> Optional[str]:
    if s is None:
        return None
    s = html_mod.unescape(_TAG.sub(" ", s)).replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip() or None


def _paragraphs(s: str) -> str:
    """HTML -> text, keeping paragraph and list breaks as newlines."""
    s = re.sub(r"(?is)<br\s*/?>|</p>|</li>|</ul>|</div>", "\n", s)
    s = re.sub(r"(?is)<li[^>]*>", "- ", s)
    s = html_mod.unescape(_TAG.sub(" ", s)).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in s.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def _trim(s: str, n: int) -> str:
    if len(s) <= n:
        return s
    cut = s[:n].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return cut + "…"


# --------------------------------------------------------------------------- #
# Clocks

_CLOCK = r"(\d{1,2})(?::(\d{2}))?\s*(a\.?\s?m\.?|p\.?\s?m\.?)?"
_RANGE = re.compile(rf"^\s*{_CLOCK}\s*(?:-|–|—|to)\s*{_CLOCK}\s*$", re.I)
_SINGLE = re.compile(rf"^\s*{_CLOCK}\s*$", re.I)


def _hm(h: str, m: Optional[str], ap: Optional[str]) -> Optional[Tuple[int, int]]:
    hh, mm = int(h), int(m or 0)
    if ap:
        ap = ap.lower()[0]
        if not 1 <= hh <= 12:
            return None
        if ap == "p" and hh != 12:
            hh += 12
        if ap == "a" and hh == 12:
            hh = 0
    if hh > 23 or mm > 59:
        return None
    return hh, mm


# One segment of a schedule line: "2:45pm: Lesson", "7:30- 8:15 pm Lesson",
# "3:30pm - 6:00pm: Social Dance". The clock (or range) must LEAD the segment.
_SEGMENT = re.compile(rf"^\s*{_CLOCK}\s*(?:(?:-|–|—|to)\s*{_CLOCK})?\s*(?::|\s|$)", re.I)


def _span(sh, sm, sap, eh, em, eap) -> Optional[Tuple[Tuple[int, int], Tuple[int, int]]]:
    """A start and an end, at least one with am/pm. A start written without
    am/pm takes the end's when that keeps it before the end ("2:45 - 5:30pm" is
    14:45), and the other one otherwise ("11 - 1pm" is 11:00)."""
    end = _hm(eh, em, eap)
    if end is None or not (sap or eap):
        return None
    if not sap:
        cand = _hm(sh, sm, eap)
        other = _hm(sh, sm, "am" if eap.lower()[0] == "p" else "pm")
        start = cand if cand and cand <= end else other
    else:
        start = _hm(sh, sm, sap)
    return (start, end) if start is not None else None


def _schedule(low: str) -> Optional[Tuple[Tuple[int, int], Optional[Tuple[int, int]]]]:
    """'2:45pm: Lesson | 3:30pm - 6:00pm: Social Dance' -> (14:45, 18:00): the
    lesson-then-social that a plain range card writes as '2:45pm - 6:00pm'.
    Every segment must lead with a clock, start after the one before, and begin
    where that one ended (an open segment, '2:45pm: Lesson', runs to the next),
    or this is None - a schedule with a gap is two sessions, and one row never
    spans a gap. A last segment with no end leaves the end unknown (None)."""
    segs = [p for p in low.split("|") if p.strip()]
    if len(segs) < 2:
        return None
    first = prev_st = prev_end = None
    for seg in segs:
        m = _SEGMENT.match(seg)
        if not m:
            return None
        sh, sm, sap, eh, em, eap = m.groups()
        if eh is None:
            st = _hm(sh, sm, sap) if sap else None
            en = None
        else:
            sp = _span(sh, sm, sap, eh, em, eap)
            st, en = sp if sp else (None, None)
        if st is None:
            return None
        if prev_st is not None and st <= prev_st:
            return None
        if prev_end is not None and st != prev_end:
            return None
        first = first or st
        prev_st, prev_end = st, en
    return first, prev_end


def parse_time_line(s: Optional[str]) -> Dict[str, Any]:
    """The card's time line -> {"kind", "start", "end"}, start/end (h, m).

    kind: "range" ("2:45pm - 6:00pm", "2:45 - 5:30pm"), "start" ("9:30am"),
    "schedule" ("2:45pm: Lesson | 3:30pm - 6:00pm: Social Dance", one
    contiguous span), "varies" ("showtimes vary"), "none" (no line) or
    "other" (anything else, which is reported, not guessed)."""
    raw = _clean(s) or ""
    low = raw.lower()
    if not low:
        return {"kind": "none", "start": None, "end": None, "raw": raw}
    if "vary" in low or "varies" in low or "various" in low:
        return {"kind": "varies", "start": None, "end": None, "raw": raw}
    low = low.replace("noon", "12:00pm")
    m = _RANGE.match(low)
    if m:
        sp = _span(*m.groups())
        if sp is None:
            return {"kind": "other", "start": None, "end": None, "raw": raw}
        return {"kind": "range", "start": sp[0], "end": sp[1], "raw": raw}
    m = _SINGLE.match(low)
    if m and m.group(3):
        st = _hm(*m.groups())
        if st:
            return {"kind": "start", "start": st, "end": None, "raw": raw}
    sp = _schedule(low)
    if sp:
        return {"kind": "schedule" if sp[1] else "start", "start": sp[0], "end": sp[1], "raw": raw}
    return {"kind": "other", "start": None, "end": None, "raw": raw}


def parse_detail_clock(s: Optional[str]) -> Optional[Tuple[int, int]]:
    """'2:45 pm' (the tail of the detail <time>) -> (14, 45)."""
    m = re.search(_CLOCK, (s or "").lower().replace("noon", "12:00pm"))
    if not m or not m.group(3):
        return None
    return _hm(*m.groups())


def _tz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(TZ_NAME)
    except Exception:      # pragma: no cover - zoneinfo ships with 3.9+
        return None


def localize(d: date, hm: Optional[Tuple[int, int]]) -> Tuple[str, Optional[str]]:
    """(local ISO, UTC ISO). No clock -> a bare date and no instant, which is
    what the source said; never midnight."""
    if hm is None:
        return d.isoformat(), None
    naive = datetime(d.year, d.month, d.day, hm[0], hm[1])
    tz = _tz()
    if tz is None:
        return naive.isoformat(), None
    dt = naive.replace(tzinfo=tz)
    return dt.isoformat(), dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# Pages

def parse_listing(page: str, year: int, month: int) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """One month page -> (cards, notes). The year and month are the ones the
    URL asked for; a card naming another month is refused, not re-dated."""
    cards: List[Dict[str, Any]] = []
    notes: Dict[str, int] = {}
    seen = set()
    for chunk in _CARD_SPLIT.split(page)[1:]:
        m = _CARD_HEAD.search(chunk)
        if not m:
            notes["card without a link"] = notes.get("card without a link", 0) + 1
            continue
        nid, title, when = m.group(1), _clean(m.group(2)) or "", _clean(m.group(3)) or ""
        if nid in seen:
            continue
        seen.add(nid)
        dm = re.match(r"([A-Za-z]+)\.?\s+(\d{1,2})$", when)
        mon = _MON.get(dm.group(1).lower()) if dm else None
        if not dm or mon != month:
            notes["card month is not the page's month"] = notes.get("card month is not the page's month", 0) + 1
            continue
        try:
            d = date(year, month, int(dm.group(2)))
        except ValueError:
            notes["impossible card date"] = notes.get("impossible card date", 0) + 1
            continue
        em = _CARD_HEAD_END.match(chunk, m.end())
        if em:
            to = re.match(r"([A-Za-z]+)\.?\s+(\d{1,2})$", _clean(em.group(1)) or "")
            if not to or (_MON.get(to.group(1).lower()), int(to.group(2))) != (d.month, d.day):
                key = f"card spans days ({when} - {_clean(em.group(1))}), not guessed"
                notes[key] = notes.get(key, 0) + 1
                continue
        tm = _CARD_TIME.search(chunk[m.end():])
        cards.append({"id": nid, "title": title, "date": d,
                      "time_line": _clean(tm.group(1)) if tm else None})
    return cards, notes


def parse_detail(page: str) -> Dict[str, Any]:
    """A detail page -> its <time> month/day/clock, Label: value fields,
    description text, links and image. Reads the content block only, so the
    site's header and footer links cannot leak in."""
    cm = _CONTENT.search(page)
    body = cm.group(1) if cm else page
    out: Dict[str, Any] = {"month": None, "day": None, "clock": None,
                           "end_month": None, "end_day": None, "end_clock": None,
                           "fields": {}, "description": "", "links": [], "image": None}
    tm = _DETAIL_TIME.search(body)
    if tm:
        out["month"] = _MON.get(tm.group(1).lower()[:3])
        out["day"] = int(tm.group(2))
        out["clock"] = parse_detail_clock(tm.group(3))
        te = _DETAIL_END.match(body, tm.end())
        if te:
            out["end_month"] = _MON.get(te.group(1).lower()[:3])
            out["end_day"] = int(te.group(2))
            out["end_clock"] = parse_detail_clock(te.group(3))
    for k, v in _FIELD.findall(body):
        key = _clean(k)
        if key and key not in out["fields"]:
            out["fields"][key] = _clean(v)
    dm = _DESC.search(body)
    if dm:
        out["description"] = _paragraphs(_LINK_LINE.sub(" ", dm.group(1)))
    out["links"] = [(href, _clean(txt) or "") for href, txt in _ANCHOR.findall(body)]
    im = _IMG.search(body)
    if im:
        out["image"] = im.group(1)
    return out


_BOOK_LABEL = re.compile(r"\b(?:purchase\s+)?tickets?\b|\bregister\b|\bsign\s*up\b|\brsvp\b", re.I)


def booking_link(links: List[Tuple[str, str]], base: str) -> Optional[str]:
    """The anchor that books or registers, by its TEXT ("Purchase Tickets >>",
    "Register >>", "Sign Up >>"); else the presenter's "Learn More" site."""
    learn = None
    for href, text in links:
        if href.startswith("mailto:") or "parkmap" in href:
            continue
        url = urljoin(base, href)
        if _BOOK_LABEL.search(text):
            return url
        if learn is None and re.search(r"learn\s+more|more\s+details|website", text, re.I):
            learn = url
    return learn


# --------------------------------------------------------------------------- #
# Admission

_AMOUNT = re.compile(r"\$\s?\d")
_FREE_LEAD = re.compile(r"^\s*free\b[\s.!]*", re.I)
_FOR_SOME = re.compile(r"\b(?:for|under|over|ages?|members?|students?|seniors?|children|kids|"
                       r"with|before|after|until|youth|teens?)\b", re.I)


def admission_line(text: Optional[str]) -> Tuple[Optional[str], bool]:
    """(the description's fee line, is it free). The wording is a contract with
    ../mapsee's offer tagger (0227): FREE only when the park writes FREE and
    nothing after it narrows it to some people; every other stated admission
    carries "(not free)", which 0227's negation reads as a veto."""
    t = _clean(text)
    if not t:
        return None, False
    t = t.rstrip(" .;")
    fm = _FREE_LEAD.match(t)
    if fm and not _AMOUNT.search(t):
        rest = t[fm.end():].strip(" ;,-–()")
        if not rest:
            return "🎟 Admission: free.", True
        if not _FOR_SOME.search(rest):
            return f"🎟 Admission: free ({rest}).", True
        return f"🎟 Admission: {t} (not free for everyone).", False
    return f"🎟 Admission: {t} (not free).", False


# --------------------------------------------------------------------------- #
# Places

def _norm_place(s: str) -> str:
    """'the Puppet Co. Playhouse ' and 'Puppet Co Playhouse' are one room."""
    s = (s or "").lower()
    s = re.sub(r"\bat glen echo park\b", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    s = re.sub(r"^the\s+", "", s)
    return re.sub(r"\s+", " ", s).strip()


def place(name: Optional[str], site: Dict[str, Any]) -> Optional[Tuple[str, Dict[str, Any]]]:
    """(the venue name to write, the book's entry) for a Location string, or
    None. A key or one of its `aliases` matches after _norm_place and the row
    takes the BOOK's name, so "the Puppet Co. Playhouse " and "Puppet Co.
    Playhouse" fingerprint as one room. Failing that, the longest multi-word
    key the Location STARTS with ("Arcade Building Classrooms 202 & 203" ->
    "Arcade Building"), and the row keeps the page's more specific words. A
    one-word key never prefix-matches: "Park" would swallow "Park View Gallery"."""
    book: Dict[str, Tuple[str, Dict[str, Any]]] = {}
    for k, v in (site.get("places") or {}).items():
        if k.startswith("_") or not isinstance(v, dict):
            continue
        book[_norm_place(k)] = (k, v)
        for a in v.get("aliases") or ():
            book.setdefault(_norm_place(a), (k, v))
    # "Adventure Theatre | 7300 MacArthur Blvd / Glen Echo, MD 20812": the
    # room is before the bar, the park's street address after it.
    name = (name or "").split("|")[0]
    key = _norm_place(name)
    hit = book.get(key)
    if hit is None and key:
        for k in sorted(book, key=len, reverse=True):
            if " " in k and key.startswith(k + " "):
                hit = (re.sub(r"\s+", " ", (name or "").strip()), book[k][1])
                break
    if hit is None or hit[1].get("lat") is None or hit[1].get("lon") is None:
        return None
    return hit


# --------------------------------------------------------------------------- #
# Rows

_LIVE_MUSIC = re.compile(r"\blive\s+(?:music|band)\b|\bband\b|\borchestra\b|\bfiddle\b|"
                         r"\bmusicians?\b|\bconcert\b", re.I)


_CONCERT_TITLE = re.compile(r"\bconcerts?\b", re.I)


def occurrence_fingerprint(name: str, start_local: str, spot: str) -> str:
    """make_fingerprint widened by the clock, as in mapsee_ingest_seattlecenter:
    two same-titled sessions in one room on one day are two rows. A date-only
    row hashes to exactly make_fingerprint's value."""
    base = make_fingerprint(name, start_local, spot)
    clock = start_local[11:16] if len(start_local) >= 16 else ""
    if not clock:
        return base
    return hashlib.sha1(f"{base}|{clock}".encode("utf-8")).hexdigest()


def _note(notes: Optional[Dict[str, int]], key: str) -> None:
    if notes is not None:
        notes[key] = notes.get(key, 0) + 1


CLOCK_DISAGREES = "card time line and the page's <time> disagree (the time line is used)"


def build_event(card: Dict[str, Any], detail: Dict[str, Any], site: Dict[str, Any],
                url: str, notes: Optional[Dict[str, int]] = None
                ) -> Tuple[Optional[NormalizedEvent], Optional[str]]:
    """(row, None) or (None, why not). Never raises on a page's content.
    `notes` counts what was kept but worth a look (a clock disagreement)."""
    title = card.get("title") or ""
    d: date = card["date"]
    if detail.get("month") is None:
        return None, "detail page has no date"
    if (detail["month"], detail["day"]) != (d.month, d.day):
        return None, "detail date disagrees with its listing card"
    if detail.get("end_month") is not None and \
            (detail["end_month"], detail["end_day"]) != (d.month, d.day):
        return None, "detail page's <time> spans days, not guessed"
    f = detail.get("fields") or {}
    desc = detail.get("description") or ""
    admission = f.get("Admission")
    why = refusal(title, f"{desc}\n{admission or ''}")
    if why:
        return None, why

    loc_name = f.get("Location")
    if loc_name and _ONLINE_PLACE.match(loc_name):
        return None, "online only"
    if loc_name:
        hit = place(loc_name, site)
        if hit is None:
            return None, f"unplaceable location: {loc_name}"
    else:
        hit = place(site.get("park_place", "Glen Echo Park"), site)
        if hit is None:
            return None, "no location and no park pin in the book"
    venue, spot = hit

    # THE TIME LINE THE PARK WROTE WINS over the <time> field. The Junior
    # Ranger swearing-in (7685, 7686) reads "11:00am" on its card and on its
    # page, and "the first Saturday of each month at 11:00am" in its text; its
    # <time> says 10:00, copied from the tour (7674) it follows. The <time> is
    # used only when there is no time line at all, and its end only when its
    # start is the start we use.
    tl = parse_time_line(card.get("time_line"))
    start_hm = end_hm = None
    note = None
    clock = detail.get("clock")
    if tl["kind"] == "varies":
        note = "Showtimes vary: see the presenter's page for this date's performances."
    elif tl["kind"] == "other":
        note = f"Times: {tl['raw']}."
    elif tl["kind"] == "none":
        start_hm = clock
    else:
        start_hm, end_hm = tl["start"], tl["end"]
        if clock is not None and clock != start_hm:
            _note(notes, CLOCK_DISAGREES)
            print(f"[{SOURCE}] {CLOCK_DISAGREES}: {tl['raw']!r} vs <time> "
                  f"{clock[0]:02d}:{clock[1]:02d} - {title} {url}")
        if "|" in tl["raw"]:            # a schedule: its parts are worth quoting
            note = f"Times: {tl['raw']}."
    if end_hm is None and start_hm is not None and clock == start_hm and \
            detail.get("end_clock") and detail["end_clock"] > start_hm:
        end_hm = detail["end_clock"]      # same day, so it cannot end before it starts
    start_local, start_utc = localize(d, start_hm)
    end_local = end_utc = None
    if end_hm is not None:
        ed = d + timedelta(days=1) if end_hm <= start_hm else d
        end_local, end_utc = localize(ed, end_hm)

    fee, _free = admission_line(admission)
    parts: List[str] = []
    if fee:
        parts.append(fee)
    if note:
        parts.append(note)
    ages = f.get("Recommended Ages") or f.get("Ages")
    if ages:
        parts.append(f"Ages: {ages}.")
    if desc:
        parts.append(_trim(desc, int(site.get("description_chars", 900))))
    base = site.get("base", "https://glenechopark.org")

    category = spot.get("category") or site.get("category", "community")
    if category == "community" and _CONCERT_TITLE.search(title):
        category = "music"      # "4P'S DC REUNION CONCERT ..." (9564) was community
    extras = [c for c in (spot.get("categories") or []) if c != category]
    if category == "community" and "music" not in extras and _LIVE_MUSIC.search(desc):
        extras.append("music")

    nev = NormalizedEvent(
        source=SOURCE,
        source_id=card["id"],
        name=title,
        description="\n\n".join(parts) or None,
        start_local=start_local, start_utc=start_utc,
        end_local=end_local, end_utc=end_utc,
        timezone=TZ_NAME,
        venue_name=venue,
        address=spot.get("address") or site.get("default_address"),
        city=spot.get("city") or site.get("default_city"),
        region=spot.get("region") or site.get("default_region"),
        country=spot.get("country") or site.get("default_country"),
        postal_code=spot.get("postal_code") or site.get("default_postal_code"),
        latitude=float(spot["lat"]), longitude=float(spot["lon"]),
        coords_exact=bool(spot.get("coords_exact")),
        category=category, categories=extras,
        promoter=f.get("Presenter"),
        poster_image_url=urljoin(base, detail["image"]) if detail.get("image") else None,
        ticket_url=booking_link(detail.get("links") or [], base) or url,
    )
    nev.fingerprint = occurrence_fingerprint(title, start_local, nev.venue_name or "")
    return nev, None


def is_free(ev: NormalizedEvent) -> bool:
    """Free by the park's own word: the row's fee line says so."""
    return (ev.description or "").startswith("🎟 Admission: free")


# --------------------------------------------------------------------------- #
# HTTP

class Refused(Exception):
    """The host said no (401/403/429, a challenge, robots.txt). Ends the site."""


class Client:
    """One host, paced, robots-checked, deadline-aware. `get` returns the body
    of a 2xx, None for any other answer worth skipping - a network error
    included, so one timeout costs one page and not the run (seattlecenter's
    rule) - and raises Refused for a refusal and Stop when the deadline has
    passed or `max_failures` requests in a row got no answer or a 5xx (a host
    that is down is not read 185 times at one per second)."""

    def __init__(self, session, pause: float, deadline: Optional[float],
                 robots=None, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep, max_failures: int = 5):
        self.session = session
        self.pause = pause
        self.deadline = deadline
        self.clock, self.sleep = clock, sleep
        self.robots = robots
        self.requests = 0
        self.errors = 0           # network errors, all run
        self.max_failures = max_failures
        self._in_row = 0          # network errors and 5xx since the last answer
        self._last = None

    def _failed(self, why: str) -> None:
        self._in_row += 1
        if self._in_row >= self.max_failures:
            raise Stop(f"{self._in_row} requests in a row failed (last: {why}) - the host is not answering")

    def _allowed(self, url: str) -> None:
        if self.robots is None:
            return
        v = self.robots.check(url)
        if v.get("allowed") is not True:
            raise Refused(f"robots.txt ({v.get('status')}): {v.get('rule')} for {url}")
        cd = v.get("crawl_delay")
        if cd and cd > self.pause:
            self.pause = float(cd)

    def get(self, url: str) -> Optional[str]:
        from robots_txt import CHALLENGE_RX
        host = urlsplit(url).netloc
        for _hop in range(4):
            if self.deadline is not None and self.clock() >= self.deadline:
                raise Stop("run deadline reached")
            self._allowed(url)
            if self._last is not None:
                wait = self.pause - (self.clock() - self._last)
                if wait > 0:
                    self.sleep(wait)
            self._last = self.clock()
            self.requests += 1
            try:
                r = self.session.get(url, timeout=40, allow_redirects=False)
            except OSError as exc:    # requests.RequestException is an OSError
                self.errors += 1
                print(f"[{SOURCE}] network error, page skipped: {type(exc).__name__}: {exc} - {url}")
                self._failed(type(exc).__name__)
                return None
            code = r.status_code
            if code in (301, 302, 303, 307, 308):
                nxt = urljoin(url, r.headers.get("Location") or "")
                nhost = urlsplit(nxt).netloc
                if nhost.removeprefix("www.") != host.removeprefix("www."):
                    print(f"[{SOURCE}] redirect off the site, not followed: {url} -> {nxt}")
                    return None
                url = nxt
                continue
            if code in (401, 403, 429):
                raise Refused(f"HTTP {code} for {url}")
            text = r.text or ""
            if CHALLENGE_RX.search(text[:6000]):
                raise Refused(f"bot challenge for {url}")
            if not 200 <= code < 300:
                print(f"[{SOURCE}] HTTP {code}: {url}")
                if code >= 500:
                    self._failed(f"HTTP {code}")
                else:
                    self._in_row = 0
                return None
            self._in_row = 0
            return text
        print(f"[{SOURCE}] too many redirects: {url}")
        return None


class Stop(Exception):
    """The run deadline passed; no request starts after it."""


def months_between(start: date, end: date) -> List[Tuple[int, int]]:
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


# Notes that mean a card the park lists was NOT read this run: the read is then
# not complete, and no row may be called absent from it (mark_complete).
INCOMPLETE_NOTES = ("month page not read", "month page matched no cards", "detail page not read",
                    "over max_events, not read", "network error, page skipped")


def read_site(site: Dict[str, Any], client: Client, today: date,
              emit: Callable[[NormalizedEvent], None],
              cancel: Optional[Callable[[NormalizedEvent, str], Any]] = None) -> Dict[str, Any]:
    """Walk the months, refuse what is refused, fetch a detail page per kept
    card, and emit rows. A card that calls a dance off (called_off) is read
    like the dance and handed to `cancel` with the dance's own title, so the
    tombstone carries the fingerprint the live row had. Returns the report,
    whose "complete" says every card in [today, horizon] was read; never
    raises on content."""
    base = site.get("base", "https://glenechopark.org").rstrip("/")
    horizon = today + timedelta(days=int(site.get("within_days", DEFAULT_WITHIN_DAYS)))
    max_events = int(site.get("max_events", 600))
    rep: Dict[str, Any] = {"listed": 0, "kept": 0, "free": 0, "cancelled": 0, "refused": {}, "notes": {},
                           "unplaceable": {}, "stopped": None, "complete": False,
                           "window": (today, horizon)}

    def count(bucket: str, key: str) -> None:
        rep[bucket][key] = rep[bucket].get(key, 0) + 1

    cards: List[Dict[str, Any]] = []
    seen = set()
    twins: Dict[Tuple[str, str, str], str] = {}
    try:
        for y, m in months_between(today, horizon):
            page = client.get(f"{base}/events-calendar/{y}{m:02d}")
            if page is None:
                count("notes", "month page not read")
                continue
            got, notes = parse_listing(page, y, m)
            for k, v in notes.items():
                rep["notes"][k] = rep["notes"].get(k, 0) + v
            if not got:
                # Nine studios' daily hours alone put ~80 cards in every month,
                # so an empty month is a markup change, not a quiet month.
                count("notes", "month page matched no cards")
                print(f"[{SOURCE}] !! {y}-{m:02d} matched no cards - the markup has "
                      f"probably changed. Check _CARD_HEAD.")
            for c in got:
                if c["id"] in seen:
                    continue
                seen.add(c["id"])
                if today <= c["date"] <= horizon:
                    cards.append(c)
        rep["listed"] = len(cards)
        names = site.get("open_hours_titles") or []
        todo, offs = [], []
        for c in cards:
            # Both decided from the TITLE, so neither costs a detail request:
            # 240 open-hours cards and 5 "CANCELLED TODAY-" dances, Oct 4 - Jan 2.
            if is_open_hours(c["title"], names):
                count("refused", "gallery/studio open hours")
                continue
            why = refusal(c["title"], "")
            if why:
                count("refused", why)
                live = called_off(c["title"]) if why == "cancelled" and cancel is not None else None
                if live:
                    # One detail request each (5 in 90 days on 2026-10-04): the
                    # room, and so the fingerprint, is on the page only.
                    offs.append(dict(c, title=live, notice=c["title"]))
                continue
            todo.append(c)
        if len(todo) > max_events:
            rep["notes"]["over max_events, not read"] = len(todo) - max_events
            todo = todo[:max_events]
        for c in todo:
            url = f"{base}/events-calendar/event-detail/{c['id']}"
            page = client.get(url)
            if page is None:
                count("notes", "detail page not read")
                continue
            nev, why = build_event(c, parse_detail(page), site, url, rep["notes"])
            if nev is None:
                if why.startswith("unplaceable location: "):
                    count("unplaceable", why.split(": ", 1)[1])
                    print(f"[{SOURCE}] unplaceable, skipped: {why.split(': ', 1)[1]!r} "
                          f"({c['title']}) - add it to `places` in the config")
                elif why.startswith("detail") or why.startswith("no location"):
                    count("notes", why)
                    print(f"[{SOURCE}] {why}: {url}")
                else:
                    count("refused", why)
                continue
            # Same title, presenter and start in two rooms: kept (the rooms are
            # different venue names, so two fingerprints), but NAMED - 9581 and
            # 9658, Ballroom Time on 2026-12-27, may be one dance posted twice.
            twin = (nev.name.lower(), nev.start_local, (nev.promoter or "").lower())
            if twin in twins:
                count("notes", "same title, presenter and start as another node (a double posting?)")
                print(f"[{SOURCE}] same title, presenter and start: nodes {twins[twin]} and "
                      f"{nev.source_id} ({nev.name}, {nev.start_local}) - kept both")
            else:
                twins[twin] = nev.source_id
            rep["kept"] += 1
            rep["free"] += 1 if is_free(nev) else 0
            emit(nev)
        # After the live rows, so a deadline costs a tombstone before a row.
        for c in offs:
            url = f"{base}/events-calendar/event-detail/{c['id']}"
            page = client.get(url)
            if page is None:
                count("notes", "detail page not read")
                continue
            nev, why = build_event(c, parse_detail(page), site, url)
            if nev is None:
                count("notes", f"called off, but its dance would not have been a row ({why.split(':')[0]})")
                continue
            cancel(nev, c["notice"][:80])
            rep["cancelled"] += 1
    except Stop as exc:
        rep["stopped"] = str(exc)
    except Refused as exc:
        rep["stopped"] = f"REFUSED - {exc}"
    if client.errors:
        rep["notes"]["network error, page skipped"] = client.errors
    rep["requests"] = client.requests
    rep["complete"] = rep["stopped"] is None and not any(rep["notes"].get(k) for k in INCOMPLETE_NOTES)
    return rep


def _report(name: str, rep: Dict[str, Any]) -> None:
    refused = sum(rep["refused"].values())
    detail = ", ".join(f"{n} {w}" for w, n in sorted(rep["refused"].items(), key=lambda kv: -kv[1]))
    print(f"[{SOURCE}] {name}: kept {rep['kept']} of {rep['listed']} listed "
          f"({rep['free']} free by the park's own word); refused {refused}"
          + (f" ({detail})" if detail else "") + f"; cancelled {rep.get('cancelled', 0)} "
          f"called-off dance(s); read {'COMPLETE' if rep.get('complete') else 'NOT complete'}; "
          f"{rep['requests']} request(s)", flush=True)
    for w, n in sorted(rep["unplaceable"].items()):
        print(f"[{SOURCE}] {name}: unplaceable location {w!r} x{n}", flush=True)
    for w, n in sorted(rep["notes"].items()):
        print(f"[{SOURCE}] {name}: {w}: {n}", flush=True)
    if rep.get("stopped"):
        print(f"[{SOURCE}] {name}: STOPPED - {rep['stopped']}", flush=True)


def _today_local() -> date:
    import os
    fixed = os.environ.get("MAPSEE_TODAY")
    if fixed:
        return datetime.strptime(fixed, "%Y%m%d").date()
    tz = _tz()
    return datetime.now(tz).date() if tz else date.today()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import Glen Echo Park's events calendar.")
    ap.add_argument("--config", default="glenecho_sources.json")
    ap.add_argument("--store", default="feeds_events.json")
    ap.add_argument("--max-minutes", type=float, default=DEFAULT_MAX_MINUTES,
                    help="whole-run deadline; no request starts after it (0 = none)")
    ap.add_argument("--dry-run", action="store_true", help="read and report, write nothing")
    a = ap.parse_args(argv)

    try:
        import requests
    except ImportError:  # pragma: no cover
        sys.exit("This script needs 'requests'.  Install it with:  pip install requests")
    from robots_txt import Robots

    cfg = json.loads(open(a.config, encoding="utf-8").read())
    started = time.monotonic()
    deadline = started + a.max_minutes * 60 if a.max_minutes else None
    store = None if a.dry_run else EventStore(a.store)
    today = _today_local()
    total = 0
    reads: List[Optional[Tuple[date, date]]] = []
    for site in cfg.get("sites", []):
        if site.get("skip"):
            continue
        name = site.get("name", "?")
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT,
                                "Accept": "text/html,application/xhtml+xml",
                                "Accept-Language": "en-US,en;q=0.9"})
        client = Client(session, float(site.get("pause", 1.0)), deadline, robots=Robots(session),
                        max_failures=int(site.get("max_failures_in_row", 5)))
        pending = [0]

        def emit(ev: NormalizedEvent) -> None:
            if store is None:
                return
            store.upsert(ev)
            pending[0] += 1
            if pending[0] >= SAVE_EVERY:
                store.save()
                pending[0] = 0

        def cancel(ev: NormalizedEvent, reason: str) -> None:
            if store is not None:
                store.cancel(ev, reason, notice=True)  # a called-off title

        try:
            rep = read_site(site, client, today, emit, cancel)
        except Exception as exc:                          # noqa: BLE001
            # One source must never cost the others.
            print(f"[{SOURCE}] {name} FAILED: {type(exc).__name__}: {exc}", flush=True)
            reads.append(None)
            continue
        finally:
            if store is not None:
                store.save()
        _report(name, rep)
        reads.append(rep["window"] if rep["complete"] else None)
        total += rep["kept"]
    # Every card in the window was read, so a dance the last complete read
    # wrote and this one did not is gone from the calendar (mapsee_supabase_sync
    # --retire-absent). The rows of every site share SOURCE, so it is one unit:
    # complete only when every site was, over the window they all covered.
    if store is not None and reads and all(reads):
        store.mark_complete(SOURCE, max(w[0] for w in reads), min(w[1] for w in reads))
        store.save()
    took = (time.monotonic() - started) / 60
    if store is not None:
        print(f"[{SOURCE}] done: +{total} events in {took:.1f} min; "
              f"store now holds {len(store.records)} unique events.", flush=True)
    else:
        print(f"[{SOURCE}] dry run - {total} event(s) would be written; {took:.1f} min", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
