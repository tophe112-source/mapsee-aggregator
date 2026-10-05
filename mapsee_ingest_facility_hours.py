#!/usr/bin/env python3
"""
mapsee_ingest_facility_hours.py - when a city's community centres and older-adult
centres are OPEN, from the city's own open data.

WHAT THIS IS, AND WHAT IT IS NOT. Opening hours are not dated events. Glen Echo's
240 studio opening-hours cards were refused as non-events, and OpenActive's
booking grids taught the repo what forty thousand standing rows cost
(docs/agents/community-centres.md, openactive-and-standing-rows.md). So this
adapter writes the osm_secondhand contract: ONE STANDING ROW PER CENTRE, carrying
its weekly hours in `recurring_days`. ../mapsee 0156/0188 roll its window forward
hourly and 0158's is_standing demotes it in Nearby, so 300 senior centres cannot
crowd tonight's events off the list. The only DATED rows are a real programme:
Seattle's Teen Late Night (Fri/Sat 7 pm - midnight at 3 community centres and 3
Teen Life Centers), one row per evening, skipping federal holidays, projected
only `late_night_days_ahead` (21) days: an upsert cannot remove an evening, and
the open data's LN_HOURS has been wrong - South Park's "Fri 6:30pm-10:30pm and Sat
3:30pm-8:30pm" and Van Asselt's "(no Saturday)" against their own seattle.gov
pages' "7:00pm - Midnight" both nights (2026-10-05). The config records each such
cell (`late_night_disagrees`); while the data still says it, that centre's
evenings are not written, and they return by themselves when the city fixes it.

NEVER TWO PINS ON ONE CENTRE. mapsee_ingest_osm_amenities.py already LISTS every
amenity=community_centre in OSM as a standing row (always_list), mostly with
"opening times are not listed". Measured 2026-10-05 with one Geofabrik Postpass
query per city (Overpass resets every TLS handshake from the cloud sandbox): 25
of Seattle's 28 community centres have a same-name OSM centre 3-87 m away, 14 of
them carrying OSM hours that are often stale (Rainier Beach "Mo-Fr 10:00-21:00"
where the city says Mon-Thu 8:30 AM-9 PM); Cleveland 18 of 25, Phoenix 19 of 34,
Chicago 2 of 21, NYC's 312 older adult centres 5, plus 5 more inside another
centre's building. A second row would be a second listing of the same building.
So each source's config carries an `osm` map, written by `--propose-osm` and
read by a person (77 entries today), and each matched centre is one of:

  * "same"      - the OSM element IS this centre (same kind: general, seniors or
                  teens; the shorter name's distinctive words all in the longer;
                  <= 150 m; one centre per element). This adapter writes the row
                  UNDER THE OSM ROW'S OWN FINGERPRINT, at its coordinate, so the
                  listing already in Supabase is upgraded in place - its id, its
                  share links and its favourites survive - and osm_amenities must
                  skip that element (claimed_osm_refs(); see shared changes). The
                  row keeps the listing's shape: its "<name> — community centre in
                  <town>" first line and its "Public details from OpenStreetMap
                  contributors" line, which is what ../mapsee reads to render the
                  civic card (Run by, Call, "Plan an event here").
  * "colocated" - another centre in the same building (an older adult centre in
                  a settlement house; a Teen Life Center in a community centre):
                  <= 40 m, or the same housenumber + street within 150 m (four
                  NYC centres sat 41-67 m from the box centre of the OSM building
                  at their own address). Its own row, snapped to the building's
                  coordinate, so the map draws ONE dot holding two listings.
  * absent      - nothing in OSM nearby; its own row at the city's coordinate.

../mapsee parseImportedDesc REMOVES every "☎ Phone:" and "🏛 Run by:" line and
renders them back only on an OSM row (measured on the stored Laurelhurst and
Cypress Hills rows: phone parsed, then shown nowhere). So only a taken-over
listing writes those markers; every other row says "Run by X." and "Call N."

HOURS ARE PARSED, NEVER APPROXIMATED. Each format has its traps, all measured on
the live data and pinned in test_ingest_facility_hours.py:
  * Seattle (ArcGIS): two scheduling seasons per centre, labelled
    inconsistently - "Summer" sits in the first set for 16 centres and in the
    second for 4, beside "School Year", "Fall, Winter, Spring", "Year Round",
    "Childcare Only". The season is picked by its LABEL against the date (summer
    ends on Labor Day); a centre with no current season (Northgate: summer only)
    is refused, not given its summer hours. The DAY_ flag contradicts the HOURS_
    cell on one current set (Garfield school-year Sat "No" / "10am-5pm") and the
    city's own Garfield page says Sat 10-5: a cell that parses wins, and the
    overruled flag is counted. Cells come as "9am-2pm",
    "9:00 AM - 7:00 PM", "715am-645pm", "2-9pm", "12:30-8pm",
    "3 PM - 12 AM (Late Night 7 PM - 12 AM)", and once "8am-45pm" (refused).
  * NYC Aging (Socrata): open/close columns in 12-hour time with no am/pm.
    "08:00"-"04:00" is 8 AM-4 PM (619 of 1,531 weekday slots), so a close at or
    before the open is +12 h; "00:00"-"00:00" is closed; "05:00"-"05:00" and
    ":30" are refused; "8 :00" is read.
  * Free text (Chicago, Cleveland, Phoenix): "Mon - Fri 8:30 a.m. to 4:30 p.m.
    Sat - Sun 9:00 a.m. to 4:00 p.m", "M-TH 10-9; FRI 10-6; SA 9-7",
    "M, T, Th 3-7 PM ; W 2-7 PM". Every character must belong to a day+time
    pair or a separator, so "East: M-F 8am-5PM ; West: ..." (two buildings) and
    "M-F 4PM-PM" are refused whole. "Temporarily closed for renovation" and
    "CLOSED" are closures, skipped and counted.

MEASURED LIVE 2026-10-05: 6 sources, 12 requests (+5 robots.txt), 14 s, 455
rows; a second Seattle run added 0. 419 standing: 395 with their weekly hours
(Seattle 23 + 3 Teen Life, NYC 302, Chicago 21, Cleveland 18, Phoenix 28; 2,051
weekly windows), 8 taken-over listings refused today and rewritten with no hours,
16 own rows refused today and rewritten as pins. 36 Late Night evenings in the
next 21 days at 6 sites. Refused hours, counted: 10 closures, 9 with no hours,
1 with only summer hours, 2 NYC centres with an unreadable clock, 2 Phoenix
strings (two buildings; "4PM-PM"); 4 Cleveland non-centres (golf, rink, camp,
greenhouse) are never written. 62 rows take over an OSM listing with hours and 8
without, so all 70 claimed elements have a writer; 12 share a building's dot.
After the sync's derive_categories: community 414, kids 40 (the teen rows),
volunteer 1 (an older adult centre named after its sponsor, Food Bank For New
York City - a shared change in the sync). DC: see the config's _about.

WHO, AND WHAT IT COSTS. Every one of these is a public building anyone may walk
into during its hours; none of these datasets states a price, so no row says
free (0227 must not tag a centre free on our word) and none states a fee.

REFRESH. A standing row changes only when the city's hours do, but --only-new
(the feeds sync's default) never rewrites a stored row, and a season switch must
land within a day. So the workflow step writes its own store and syncs it with
--skip-unchanged and without --only-new, as osm-secondhand.yml does: only rows
whose hours actually changed are written. That holds only once "facility-hours:"
is in mapsee_supabase_sync.CIVIC_TIMETABLE_SOURCES: until then every row gets a
dated "More on this show" Google line and is rewritten daily (measured: to_row
on 10-05 and 10-06 differs in no compared column with it, in the description
without it).

A REFUSED CENTRE IS REWRITTEN, NEVER SKIPPED. An upsert cannot delete and a
standing row never expires, so skipping a centre the city marks closed (or whose
hours stop parsing, or which leaves the data) would keep its old week rolling
for ever, and a skipped taken-over listing would have no writer at all. See
refused_event: a taken-over listing becomes osm_amenities' "opening times are not
listed" listing. An own row that is refused is NOT written (any shape of it would
draw a 24-hour clock in ../mapsee), so an own row written once and refused or
gone later keeps its last week until a retirement exists; none does yet. An
EMPTY answer writes nothing. A source refused whole (data too old, a 403) writes
nothing either, so its rows keep their last state until it reads again: the log
says which.

Env:  none (ArcGIS REST and Socrata SODA are documented public APIs)
Run:  python mapsee_ingest_facility_hours.py --config facility_hours_sources.json \
          --store facility_hours_events.json [--only seattle] [--max-minutes 5]
      python mapsee_ingest_facility_hours.py --config facility_hours_sources.json \
          --propose-osm      # prints `osm` maps for a person to check and paste
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

from mapsee_ingest import EventStore, NormalizedEvent
# Shared with osm_food, osm_secondhand and osm_amenities so a day's windows are
# sorted, touching ones merged and overlapping ones refused by ONE tested
# implementation (test_osm_food.py), and midnight is written the way 0188 reads it.
from mapsee_ingest_osm_food import END_OF_DAY, _tidy_windows
from robots_txt import Robots

UA = "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)"
SOURCE = "facility-hours"
MIN_INTERVAL_S = 1.0
DEFAULT_MAX_MINUTES = 5.0
REQUEST_TIMEOUT_S = 60
_REFUSALS = {401, 403, 429}
_RETRYABLE = {500, 502, 503, 504}
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
LONG_DAYS = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]
POSTPASS = "https://postpass.geofabrik.de/api/0.2/interpreter"


class Refused(Exception):
    """A 401/403/429, a bot challenge, or robots.txt said no. Never retried."""


class OutOfTime(Exception):
    """The run's deadline passed; no request starts after it."""


def today() -> date:
    """MAPSEE_TODAY=YYYYMMDD fixes "today" for the tests."""
    fixed = os.environ.get("MAPSEE_TODAY")
    if fixed:
        return datetime.strptime(fixed, "%Y%m%d").date()
    return date.today()


# ---------------------------------------------------------------------------
# Clock times
# ---------------------------------------------------------------------------
_MER = r"(?:a\.?\s?m\.?|p\.?\s?m\.?)"
def _clock_rx(p: str) -> str:
    return (rf"(?:(?P<{p}h>\d{{1,2}})(?::?(?P<{p}m>\d{{2}}))?\s*(?P<{p}x>{_MER})?"
            rf"|(?P<{p}n>noon|midnight))")


_RANGE = (r"(?<![\d:])" + _clock_rx("a") + r"\s*(?:-|\u2013|\u2014|to)\s*"
          + _clock_rx("b") + r"(?![\d:])")
RANGE_RX = re.compile(_RANGE, re.I)


def _spaces(s: str) -> str:
    return re.sub(r"[   \s]+", " ", s or "").strip()


def _clock(h: Optional[str], m: Optional[str], mer: Optional[str], noon: Optional[str]):
    """(hour, minute, meridiem or None) or None for an impossible clock."""
    if noon:
        return (12, 0, "p") if noon.lower() == "noon" else (12, 0, "a")
    hh, mm = int(h), int(m or 0)
    if mm > 59:
        return None
    x = mer[0].lower() if mer else None
    if x and not 1 <= hh <= 12:
        return None
    if not x and hh > 23:
        return None
    return hh, mm, x


def _to_min(c, x: str) -> int:
    hh, mm, _ = c
    if x == "a":
        return (0 if hh == 12 else hh) * 60 + mm
    return (12 if hh == 12 else hh + 12) * 60 + mm


def parse_range(text: str) -> Optional[Tuple[int, int]]:
    """One "open-close" range -> (open, close) minutes after midnight, or None.

    Meridiem rules, each one bought from a real cell: an end that says PM lends
    it to a start that says nothing ("2-9pm", "12:30-8pm") unless that would put
    the start after the end ("10-2pm" is 10 AM). Two bare numbers are read as a
    facility's day only when the end is the smaller ("10-9" is 10 AM-9 PM); any
    other bare pair is refused. "12 AM" as an end is midnight, the end of the day.
    Refused: an opening before 05:00, a close past midnight, a zero or negative
    span, more than 18 hours.
    """
    m = RANGE_RX.fullmatch(_spaces(text))
    if not m:
        return None
    a = _clock(m.group("ah"), m.group("am"), m.group("ax"), m.group("an"))
    b = _clock(m.group("bh"), m.group("bm"), m.group("bx"), m.group("bn"))
    if not a or not b:
        return None
    ax, bx = a[2], b[2]
    if ax and bx:
        o, c = _to_min(a, ax), _to_min(b, bx)
    elif bx:
        c = _to_min(b, bx)
        o = _to_min(a, bx)
        if o >= c or (bx == "a" and c != 0):
            o = _to_min(a, "a")
    elif ax:
        o = _to_min(a, ax)
        c = _to_min(b, ax)
        if c <= o:
            c = _to_min(b, "p")
    else:
        if a[0] > 12 or b[0] > 12:             # a 24-hour pair: "08:00-16:00"
            o, c = a[0] * 60 + a[1], b[0] * 60 + b[1]
        elif (b[0], b[1]) <= (a[0], a[1]) or a[0] == 12:
            o = _to_min(a, "p" if a[0] == 12 else "a")
            c = _to_min(b, "p")
        else:
            return None                        # "1-5": which half of the day?
    if c == 0 and (bx == "a" or (m.group("bn") or "").lower() == "midnight"):
        c = 24 * 60                            # "12 AM" closes at midnight
    if o < 5 * 60 or c <= o or c > 24 * 60 or c - o > 18 * 60:
        return None
    return o, c


def hhmm(minutes: int) -> str:
    if minutes >= 24 * 60:
        return END_OF_DAY                      # 0188 reads 23:59 as "until midnight"
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def clock_label(minutes: int) -> str:
    if minutes >= 24 * 60:
        return "midnight"
    h, m = divmod(minutes, 60)
    x = "AM" if h < 12 else "PM"
    h = h % 12 or 12
    return f"{h}:{m:02d} {x}" if m else f"{h} {x}"


def nyc_pair(o: Optional[str], c: Optional[str]):
    """NYC Aging's "monhouropen"/"monhourclose": 12-hour clocks with no am/pm.

    -> (open, close) minutes, "closed", or None (unreadable). "00:00"/"00:00" and
    blanks are a closed day. A close at or before the open is afternoon
    ("08:00"-"04:00" is 8 AM-4 PM, 619 weekday slots; "11:00"-"08:00" 11 AM-8 PM).
    An opening between 01:00 and 06:59 is refused: "05:00"-"05:00" (5 slots) is
    not a day anybody can read.
    """
    o = re.sub(r"\s+", "", o or "")
    c = re.sub(r"\s+", "", c or "")
    if o in ("", ":") and c in ("", ":"):
        return "closed"
    if o == "00:00" and c == "00:00":
        return "closed"
    mo = re.fullmatch(r"(\d{1,2}):(\d{2})", o)
    mc = re.fullmatch(r"(\d{1,2}):(\d{2})", c)
    if not mo or not mc:
        return None
    oh, om, ch, cm = int(mo.group(1)), int(mo.group(2)), int(mc.group(1)), int(mc.group(2))
    if oh > 23 or ch > 23 or om > 59 or cm > 59 or 1 <= oh <= 6 or oh == 0:
        return None
    op, cl = oh * 60 + om, ch * 60 + cm
    if cl <= op:
        cl += 12 * 60
    if cl <= op or cl > 24 * 60 or cl - op > 16 * 60:
        return None
    return op, cl


# ---------------------------------------------------------------------------
# Day names and free text
# ---------------------------------------------------------------------------
_DAY_WORDS = [
    (r"monday|mon|mo|m", 0), (r"tuesday|tues|tue|tu|t", 1), (r"wednesday|wed|we|w", 2),
    (r"thursday|thurs|thur|thu|th|r", 3), (r"friday|fri|fr|f", 4),
    (r"saturday|sat|sa", 5), (r"sunday|sun|su", 6),
]
_DAY = r"(?:" + "|".join(w for w, _ in _DAY_WORDS) + r")\.?"
_EVERY = r"(?:every\s*day|everyday|daily|7\s*days(?:\s*a\s*week)?)"
_DAYSPEC = (rf"(?:{_EVERY}|{_DAY}(?:\s*(?:-|–|to|thru|through)\s*{_DAY})?"
            rf"(?:\s*(?:,|&|and|/)\s*{_DAY}(?:\s*(?:-|–|to|thru|through)\s*{_DAY})?)*)")
_TIMES = re.sub(r"\(\?P<\w+>", "(?:", _RANGE)
_SEG = re.compile(rf"\b(?P<days>{_DAYSPEC})\b\s*[:,]?\s*(?P<times>closed|{_TIMES}"
                  rf"(?:\s*(?:,|&|and)\s*{_TIMES})*)", re.I)
_NO_DAY = re.compile(rf"\(\s*no\s+(?P<d>{_DAY})\s*\)", re.I)
_LEFTOVER_OK = re.compile(r"^(?:[\s;,.&|]|and\b)*$", re.I)


def _day_index(word: str) -> Optional[int]:
    w = word.lower().rstrip(".")
    for pat, i in _DAY_WORDS:
        if re.fullmatch(pat, w):
            return i
    return None


def parse_day_spec(spec: str) -> Optional[List[int]]:
    s = _spaces(spec)
    if re.fullmatch(_EVERY, s, re.I):
        return list(range(7))
    out: List[int] = []
    for part in re.split(r"\s*(?:,|&|\band\b|/)\s*", s, flags=re.I):
        if not part:
            continue
        ends = re.split(r"\s*(?:-|–|\bto\b|\bthru\b|\bthrough\b)\s*", part, flags=re.I)
        idx = [_day_index(e) for e in ends if e]
        if not idx or any(i is None for i in idx) or len(idx) > 2:
            return None
        if len(idx) == 1:
            out.append(idx[0])
        else:
            a, b = idx
            if b < a:
                return None                    # "Sat-Mon" is not in any of these files
            out.extend(range(a, b + 1))
    return sorted(set(out)) or None


def parse_week_text(text: str):
    """Free-text weekly hours -> ({weekday: [(open, close)]}, None) or (None, reason).

    Every character must belong to a day+time pair or a separator. That is what
    refuses Phoenix's "East: M-F 8am-5PM ; West: M-TH 9AM-6PM" (two buildings,
    one row) and "M-F 4PM-PM" whole, rather than keeping the parts that parse.
    """
    s = _spaces(text)
    if not s:
        return None, "no hours"
    if re.search(r"\b(?:closed|renovation|temporar(?:y|ily)|until further notice)\b", s, re.I) \
            and not re.search(r"\d", s):
        return None, "closure"
    no_days = set()
    for m in _NO_DAY.finditer(s):
        no_days.add(_day_index(m.group("d")))
    s = _NO_DAY.sub(" ", s)
    days: Dict[int, List[Tuple[int, int]]] = {}
    closed = set()
    pos, leftover = 0, []
    for m in _SEG.finditer(s):
        leftover.append(s[pos:m.start()])
        pos = m.end()
        dl = parse_day_spec(m.group("days"))
        if not dl:
            return None, "unreadable days"
        t = m.group("times")
        if t.strip().lower() == "closed":
            closed.update(dl)
            continue
        for r in re.finditer(_TIMES, t, re.I):
            span = parse_range(r.group(0))
            if not span:
                return None, "unreadable time"
            for d in dl:
                days.setdefault(d, []).append(span)
    leftover.append(s[pos:])
    if any(not _LEFTOVER_OK.match(x) for x in leftover):
        return None, "unreadable text"
    if not days:
        return None, "no open day"
    if (closed | no_days) & set(days):
        return None, "contradictory days"
    return days, None


def to_recurring(days: Dict[int, List[Tuple[int, int]]]) -> Optional[Dict[str, List[List[str]]]]:
    """{weekday: [(open, close) minutes]} -> {"0": [["08:30","16:30"]]} or None."""
    out = {}
    for d in sorted(days):
        tidy = _tidy_windows([(hhmm(o), hhmm(c)) for o, c in days[d]])
        if tidy is None:
            return None                        # overlapping windows: refuse, never pick
        out[str(d)] = [[o, c] for o, c in tidy]
    return out or None


def hours_text(days: Dict[int, List[Tuple[int, int]]]) -> str:
    """"Mon-Fri 8:30 AM-4:30 PM; Sat 9 AM-4 PM", runs of equal days folded."""
    keyed = [(d, tuple(sorted(days[d]))) for d in range(7) if d in days]
    parts, i = [], 0
    while i < len(keyed):
        j = i
        while (j + 1 < len(keyed) and keyed[j + 1][1] == keyed[i][1]
               and keyed[j + 1][0] == keyed[j][0] + 1):
            j += 1
        a, b = keyed[i][0], keyed[j][0]
        label = DAY_NAMES[a] if a == b else (f"{DAY_NAMES[a]}, {DAY_NAMES[b]}" if b == a + 1
                                             else f"{DAY_NAMES[a]}-{DAY_NAMES[b]}")
        spans = ", ".join(f"{clock_label(o)}-{clock_label(c)}" for o, c in keyed[i][1])
        parts.append(f"{label} {spans}")
        i = j + 1
    return "; ".join(parts)


# ---------------------------------------------------------------------------
# Seattle's two-season weekday columns
# ---------------------------------------------------------------------------
_LATE_NIGHT_CELL = re.compile(r"\(\s*late\s*night\s+(?P<r>[^()]+)\)", re.I)


def season_class(label: Optional[str]) -> Optional[str]:
    s = (label or "").strip().lower()
    if not s:
        return None
    if re.search(r"childcare|closed", s):
        return "closed"
    if re.search(r"year[\s-]*round", s):
        return "year"
    if "summer" in s:
        return "summer"
    if re.search(r"school|fall|winter|spring", s):
        return "school"
    return None


def in_summer(d: date, window: List[str]) -> bool:
    """Is `d` inside the ["MM-DD", "MM-DD"] summer window? An end of "labor-day"
    is Labor Day itself: Seattle Parks' Fall season begins the day after it
    ("Fall 2026 (begins Sept 8)" on the South Park, Van Asselt and Laurelhurst
    pages on 2026-10-05; Labor Day 2026 was Sept 7), and a fixed "09-05" gave
    two days of school-year hours early."""
    def md(x: str) -> str:
        return _nth(d.year, 9, 0, 1).strftime("%m-%d") if x == "labor-day" else x
    a, b = window
    return md(a) <= d.strftime("%m-%d") <= md(b)


def seattle_week(attrs: Dict[str, Any], d: date, summer: List[str]):
    """-> (label, {wd: [(o,c)]}, {wd: (o,c)} late night, overruled flags, None) or
    (None, None, None, 0, reason).

    THE HOURS CELL WINS OVER THE DAY_ FLAG. Garfield's school-year Saturday is
    DAY_SATURDAY2 "No" beside HOURS_SATURDAY2 "10am-5pm", and seattle.gov's own
    Garfield page (2026-10-05) says "Sat: 10 a.m. - 5 p.m." with Saturday
    drop-ins; the flag is None beside hours on Laurelhurst Mon, Ballard Mon and
    Garfield Wed too. So a cell that parses is an open day whatever the flag
    says, and each overruled flag is counted."""
    sets = []
    for suffix in ("", "2"):
        cells = {i: attrs.get(f"HOURS_{LONG_DAYS[i]}{suffix}") for i in range(7)}
        if not any(v and str(v).strip() for v in cells.values()):
            continue
        label = attrs.get(f"SCHEDULING_SEASON{suffix}")
        sets.append((season_class(label), (label or "").strip(), suffix, cells))
    if not sets:
        return None, None, None, 0, "no hours"
    if any(c == "closed" for c, *_ in sets):
        return None, None, None, 0, "closure"
    now = "summer" if in_summer(d, summer) else "school"
    pick = [s for s in sets if s[0] == now] or [s for s in sets if s[0] == "year"]
    if len(pick) != 1:
        return None, None, None, 0, ("no hours for the current season" if not pick
                                     else "two sets for one season")
    _, label, suffix, cells = pick[0]
    days: Dict[int, List[Tuple[int, int]]] = {}
    late: Dict[int, Tuple[int, int]] = {}
    overruled = 0
    for i in range(7):
        raw = _spaces(str(cells.get(i) or ""))
        flag = str(attrs.get(f"DAY_{LONG_DAYS[i]}{suffix}") or "").strip().lower()
        if not raw or re.fullmatch(r"closed", raw, re.I):
            continue
        ln = _LATE_NIGHT_CELL.search(raw)
        main = _LATE_NIGHT_CELL.sub("", raw).strip()
        span = parse_range(main)
        if not span:
            return None, None, None, 0, "unreadable time"
        if flag == "no":
            overruled += 1                     # hours in the cell, "No" in the flag
        spans = [span]
        if ln:
            lspan = parse_range(ln.group("r"))
            if not lspan:
                return None, None, None, 0, "unreadable time"
            late[i] = lspan
            spans.append(lspan)
        # The Late Night stretch either sits inside the open hours or carries on
        # from them ("3:00 PM - 7 PM (Late Night 7 PM - 12 AM)"); the building is
        # open through both, so they are joined.
        spans.sort()
        joined = [spans[0]]
        for o, c in spans[1:]:
            if o <= joined[-1][1]:
                joined[-1] = (joined[-1][0], max(joined[-1][1], c))
            else:
                joined.append((o, c))
        days[i] = joined
    if not days:
        return None, None, None, overruled, "no open day"
    return label, days, late, overruled, None


# ---------------------------------------------------------------------------
# Holidays (for the dated programme only)
# ---------------------------------------------------------------------------
def _nth(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _last(year: int, month: int, weekday: int) -> date:
    d = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def us_holidays(year: int, extra: List[str] = ()) -> set:
    """US federal holidays, each on its date AND its observed weekday."""
    fixed = [date(year, 1, 1), date(year, 6, 19), date(year, 7, 4), date(year, 11, 11),
             date(year, 12, 25)]
    out = set(fixed)
    for d in fixed:
        if d.weekday() == 5:
            out.add(d - timedelta(days=1))
        elif d.weekday() == 6:
            out.add(d + timedelta(days=1))
    thanks = _nth(year, 11, 3, 4)
    out |= {_nth(year, 1, 0, 3), _nth(year, 2, 0, 3), _last(year, 5, 0), _nth(year, 9, 0, 1),
            _nth(year, 10, 0, 2), thanks}
    if "thanksgiving+1" in extra:
        out.add(thanks + timedelta(days=1))
    # A Jan 1 on a Saturday is observed on Dec 31 of the year before.
    if date(year + 1, 1, 1).weekday() == 5:
        out.add(date(year, 12, 31))
    return out


# ---------------------------------------------------------------------------
# Names, phones, places
# ---------------------------------------------------------------------------
_VOWEL = re.compile(r"[AEIOU]")
_SMALL = {"of", "and", "the", "at", "for", "in", "on", "de", "la", "y", "by"}
# Street words with no vowel, which the no-vowel rule below would leave shouting
# ("Amico 59TH ST Senior Citizen Center"). Compass points are NOT here: "NE 41st
# St" keeps them in capitals (NE and SE by name, having a vowel), as every
# address in these files writes them.
_STREET_ABBR = {"ST": "St", "RD": "Rd", "PKWY": "Pkwy", "BLVD": "Blvd", "DR": "Dr", "LN": "Ln",
                "CT": "Ct", "PL": "Pl", "HWY": "Hwy", "SQ": "Sq", "TPKE": "Tpke", "CTR": "Ctr"}
_ORDINAL = re.compile(r"\b(\d+)(ST|ND|RD|TH)\b")


def tidy_name(s: str, keep: Any = ()) -> str:
    """Title-case a SHOUTED name. A word stays in capitals when it has no vowel
    ("PSS", "CCNS", "BRC") or the source's config lists it (`keep_upper`:
    "JASA", "ABSW"); "OPEN DOOR" and "PARK SLOPE" are words and are cased.
    An ordinal is written "59th" in any string, shouted or not (Phoenix's
    mixed-case "4530 N 67TH Ave"), and the street abbreviations above are cased."""
    s = _spaces(s)
    shouted = s == s.upper()
    s = _ORDINAL.sub(lambda m: m.group(1) + m.group(2).lower(), s)
    if not s or not shouted:
        return s
    keep = {k.upper() for k in keep}
    words = []
    for i, w in enumerate(s.split(" ")):
        core = re.sub(r"[^A-Za-z]", "", w)
        if i and w.lower() in _SMALL:
            words.append(w.lower())
        elif core.upper() in _STREET_ABBR and core.upper() not in keep and re.fullmatch(r"[A-Za-z]+\.?,?", w):
            words.append(w.replace(core, _STREET_ABBR[core.upper()]))
        elif core and (core.upper() in keep or core.upper() in ("NE", "SE")
                       or not _VOWEL.search(core)):
            words.append(w)
        else:
            words.append(re.sub(r"[A-Za-z][A-Za-z'\u2019]*",
                                lambda m: m.group(0)[0].upper() + m.group(0)[1:].lower(), w))
    return " ".join(words)


def tidy_phone(raw: Any, area: Optional[str] = None) -> Optional[str]:
    digits = re.sub(r"\D", "", str(raw or ""))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) == 7 and area:
        digits = area + digits
    if len(digits) != 10:
        return None
    return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"


def _site(raw: Any) -> Optional[str]:
    s = str(raw or "").strip()
    if not s or "@" in s or " " in s:
        return None
    if not s.startswith("http"):
        s = "https://" + s
    host = urlsplit(s).hostname or ""
    return s if "." in host else None


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _fp(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()


def osm_fingerprint(ref: str) -> str:
    """The fingerprint mapsee_ingest_osm_amenities.to_event gives an OSM element."""
    return _fp("osm-amenity", ref)


def claimed_osm_refs(config_path: str = "facility_hours_sources.json") -> set:
    """OSM refs ("way/53589225") whose listing THIS adapter writes.

    For mapsee_ingest_osm_amenities to skip, so one centre never has two writers
    flipping its row between two sets of hours. A missing or broken config claims
    nothing, which leaves osm_amenities exactly as it was.
    """
    try:
        cfg = json.loads(open(config_path, encoding="utf-8").read())
    except (OSError, ValueError):
        return set()
    out = set()
    for src in cfg.get("sources", []):
        for m in (src.get("osm") or {}).values():
            if isinstance(m, dict) and m.get("mode") == "same" and m.get("ref"):
                out.add(m["ref"])
    return out


# ---------------------------------------------------------------------------
# Reading the publishers
# ---------------------------------------------------------------------------
class Reader:
    """Paced JSON GETs, robots.txt read once per host, refusals final."""

    def __init__(self, session, deadline: Optional[float] = None, sleep=time.sleep,
                 clock=time.monotonic):
        self.session = session
        self.robots = Robots(session)
        self.deadline = deadline
        self.sleep, self.clock = sleep, clock
        self.requests = 0
        self.refused: Dict[str, str] = {}
        self._last: Dict[str, float] = {}

    def get(self, url: str, params: Optional[Dict[str, Any]] = None, tries: int = 3) -> Any:
        host = urlsplit(url).netloc
        if host in self.refused:
            raise Refused(f"{self.refused[host]} earlier this run")
        # ArcGIS REST and Socrata SODA are documented public APIs
        # (catalog_curate.DOCUMENTED_API_TYPES' reasoning), so robots.txt does not
        # decide whether they are read - but its Crawl-delay is honoured, and a
        # bot challenge on it is a refusal like any other.
        verdict = self.robots.check(url)
        if verdict.get("status") == "challenge":
            self.refused[host] = f"bot challenge on {host}/robots.txt"
            raise Refused(self.refused[host])
        gap = max(MIN_INTERVAL_S, float(verdict.get("crawl_delay") or 0))
        last: Exception = RuntimeError("no attempt made")
        for attempt in range(tries):
            if host in self._last:
                wait = gap - (self.clock() - self._last[host])
                if wait > 0:
                    self.sleep(wait)
            if self.deadline is not None and self.clock() >= self.deadline:
                raise OutOfTime("run deadline reached; no request starts after it")
            try:
                self.requests += 1
                r = self.session.get(url, params=params, timeout=REQUEST_TIMEOUT_S)
            except Exception as exc:  # noqa: BLE001 - timeouts, resets
                last = exc
            else:
                if r.status_code in _REFUSALS or re.search(
                        r"(?i)attention required|just a moment|you have been blocked",
                        r.text[:3000] if r.status_code != 200 else ""):
                    self.refused[host] = f"HTTP {r.status_code} from {host}"
                    raise Refused(self.refused[host])
                if r.status_code == 200:
                    try:
                        body = r.json()
                    except ValueError as exc:
                        last = exc
                    else:
                        err = body.get("error") if isinstance(body, dict) else None
                        if isinstance(err, dict):
                            if err.get("code") in (401, 403, 498, 499):
                                self.refused[host] = f"ArcGIS error {err.get('code')} from {host}"
                                raise Refused(self.refused[host])
                            raise RuntimeError(f"ArcGIS error: {str(err)[:200]}")
                        return body
                elif r.status_code not in _RETRYABLE:
                    raise RuntimeError(f"HTTP {r.status_code} from {host}")
                else:
                    last = RuntimeError(f"HTTP {r.status_code} from {host}")
            finally:
                self._last[host] = self.clock()
            if attempt + 1 < tries:
                self.sleep(5 * (attempt + 1))
        raise last


def read_arcgis(reader: Reader, src: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Every feature of a layer as {"attrs": {...}, "lat":, "lon":}, paged."""
    url = src["url"].rstrip("/") + "/query"
    out, offset = [], 0
    while True:
        params = {"where": src.get("where") or "1=1", "outFields": "*", "outSR": 4326, "f": "json"}
        if offset:                       # a MapServer without pagination still answers page one
            params.update(resultOffset=offset, resultRecordCount=1000)
        body = reader.get(url, params)
        feats = body.get("features") or []
        for f in feats:
            g = f.get("geometry") or {}
            if "points" in g and g["points"]:          # Chicago's layer is a multipoint
                g = {"x": g["points"][0][0], "y": g["points"][0][1]}
            out.append({"attrs": f.get("attributes") or {},
                        "lat": g.get("y"), "lon": g.get("x")})
        if not body.get("exceededTransferLimit") or not feats:
            return out
        offset += len(feats)


def read_socrata(reader: Reader, src: Dict[str, Any]) -> List[Dict[str, Any]]:
    params = {"$limit": 5000, "$order": ":id"}
    if src.get("where"):
        params["$where"] = src["where"]
    rows = reader.get(src["url"], params)
    out = []
    for r in rows if isinstance(rows, list) else []:
        lat, lon = r.get(src.get("lat_field", "latitude")), r.get(src.get("lon_field", "longitude"))
        out.append({"attrs": r, "lat": float(lat) if lat else None,
                    "lon": float(lon) if lon else None})
    return out


def data_age_days(reader: Reader, src: Dict[str, Any], now: datetime) -> Optional[float]:
    """Days since the publisher last edited the data, or None when it does not say."""
    if src["type"] == "arcgis":
        meta = reader.get(src["url"].rstrip("/"), {"f": "json"})
        info = meta.get("editingInfo") or {}
        ms = info.get("dataLastEditDate") or info.get("lastEditDate")
        if not ms:
            return None
        return (now.timestamp() - ms / 1000) / 86400
    if src["type"] == "socrata":
        p = urlsplit(src["url"])
        vid = re.search(r"/resource/([a-z0-9]{4}-[a-z0-9]{4})", p.path)
        if not vid:
            return None
        meta = reader.get(f"{p.scheme}://{p.netloc}/api/views/{vid.group(1)}.json")
        ts = meta.get("rowsUpdatedAt") or meta.get("viewLastModified")
        return (now.timestamp() - ts) / 86400 if ts else None
    return None


# ---------------------------------------------------------------------------
# One centre
# ---------------------------------------------------------------------------
def field(attrs: Dict[str, Any], spec: Optional[str]) -> str:
    """A field name, or a "{A} {B}" template over several."""
    if not spec:
        return ""
    if "{" in spec:
        return _spaces(spec.format_map({k: ("" if v is None else v) for k, v in attrs.items()}))
    v = attrs.get(spec)
    return "" if v is None else _spaces(str(v))


def centre_kind(src: Dict[str, Any], attrs: Dict[str, Any]) -> str:
    kinds = src.get("kind_by_field") or {}
    for fname, table in kinds.items():
        v = str(attrs.get(fname) or "").strip().lower()
        for pat, k in table.items():
            if re.search(pat, v, re.I):
                return k
    return src.get("kind", "general")


def read_hours(src: Dict[str, Any], attrs: Dict[str, Any], d: date):
    """-> (label, days, late, contradictions, reason)."""
    fmt = src["format"]
    if fmt == "seattle_seasons":
        return seattle_week(attrs, d, src.get("summer") or ["06-20", "labor-day"])
    if fmt == "open_close_columns":
        days, refused = {}, 0
        for i, short in enumerate(["mon", "tue", "wed", "thu", "fri", "sat", "sun"]):
            got = nyc_pair(attrs.get(f"{short}houropen"), attrs.get(f"{short}hourclose"))
            if got == "closed":
                continue
            if got is None:
                refused += 1
                continue
            days[i] = [got]
        if refused:
            return None, None, None, 0, "unreadable time"
        if not days:
            has_any = any(attrs.get(f"{s}houropen") for s in ["mon", "tue", "wed", "thu", "fri"])
            return None, None, None, 0, ("no open day" if has_any else "no hours")
        return None, days, {}, 0, None
    if fmt == "free_text":
        days, reason = parse_week_text(field(attrs, src["fields"]["hours"]))
        return None, days, {}, 0, reason
    raise ValueError(f"unknown format {fmt!r}")


# The all-week window mapsee_ingest_osm_amenities writes for a listing whose hours
# are unknown: NOT a claim that the centre is open, only what keeps ../mapsee
# 0156's roller carrying the single row. The body says what is true.
ALWAYS = {str(i): [["00:00", END_OF_DAY]] for i in range(7)}
# The glyph osm_amenities draws a community_centre with. The rows taken over
# from it kept the category's 🤝 without it, unlike every other OSM centre.
GLYPH = "🏘"
# The ODbL credit for an OSM point on a row that is NOT the OSM listing itself
# (a dated Late Night evening, a centre sharing a building's dot). Worded
# without "OpenStreetMap contributors" on purpose: mapsee_retire_perday_osm
# selects rows by that phrase plus recurring_hours IS NULL plus a standing row's
# 4dp key, and 148 of the 171 Late Night rows matched all three, so a dispatch
# with apply=true would have hidden them. "© OpenStreetMap" is the OSMF
# attribution guideline's own short form.
MAP_POINT_CREDIT = " Map point © OpenStreetMap (ODbL)."
# The line osm_amenities ends its listings with. On a row that TAKES OVER that
# listing it is still true (the name and the point are OpenStreetMap's), and it
# is what ../mapsee parseImportedDesc reads as `source` (not `business`): the
# civic contact card ("Run by", Call, "Plan an event here") and the ☎ / 🏛
# lines it renders exist only behind it. Without it the product strips those
# two lines from the text and shows them nowhere (app.js parseImportedDesc).
OSM_LISTING_LINE = "Public details from OpenStreetMap contributors (ODbL)."


def _same(c: Dict[str, Any]) -> bool:
    return c.get("osm_mode") == "same"


def _head(src: Dict[str, Any], c: Dict[str, Any]) -> str:
    town = c.get("city") or src.get("city")
    noun = c.get("noun") or src.get("noun", "community centre")
    where = f" in {town}" if town else ""
    if _same(c):
        # osm_amenities' own first line, "<name> — community centre in <town>.",
        # which ../mapsee isCommunityVenue reads to offer "Plan an event here".
        return f"{c['name']} — community centre{where}: {noun}."
    return f"{c['name']} - {noun}{where}."


def _contact_lines(c: Dict[str, Any]) -> List[str]:
    """Run by / phone / website. On a taken-over listing, the marker lines the
    civic card renders; anywhere else, plain sentences, because ../mapsee
    parseImportedDesc removes every "☎"/"Phone:" and "🏛 Run by:" line from the
    text and renders them back only on an OSM row (measured on the stored
    Laurelhurst and Cypress Hills rows: phone parsed, then shown nowhere)."""
    out = []
    if c.get("operator"):
        out.append(f"🏛 Run by: {c['operator']}" if _same(c) else f"Run by {c['operator']}.")
    if c.get("phone"):
        out.append(f"☎ Phone: {c['phone']}" if _same(c) else f"Call {c['phone']}.")
    if c.get("website"):
        out.append(f"🌐 Website: {c['website']}")    # parsed into "Their website" for every row
    return out


def _foot(src: Dict[str, Any], c: Dict[str, Any], first: str) -> str:
    if _same(c):
        return f"{first}\n{OSM_LISTING_LINE}"
    return first + (MAP_POINT_CREDIT if c.get("osm_point") else "")


def _first_window(rec: Dict[str, List[List[str]]], d: date):
    for i in range(8):                           # 8 sees every weekday once
        day = d + timedelta(days=i)
        spans = rec.get(str(day.weekday()))
        if spans:
            return day, spans[0][0], spans[0][1]
    return None


def _place_event(src, c, d, rec, desc, pin_only=False) -> Optional[NormalizedEvent]:
    first = _first_window(rec, d)
    if not first:
        return None
    day, o, cl = first
    return NormalizedEvent(
        source=f"{SOURCE}:{src['key']}",
        source_id=c["source_id"],
        fingerprint=c["fingerprint"],
        name=c["name"],
        description=desc,
        start_local=f"{day.isoformat()}T{o}:00",
        end_local=f"{day.isoformat()}T{cl}:00",
        timezone=src.get("timezone"),
        venue_name=c["name"],
        latitude=c["lat"], longitude=c["lon"],
        address=c.get("address") or None,
        city=c.get("city") or src.get("city"), region=src.get("region"),
        country=src.get("country"),
        postal_code=c.get("postal_code") or None,
        category=src.get("category", "community"),
        categories=list(src.get("categories") or []),
        ticket_url=c.get("website"),
        # The publisher's own point for its own building (or the OSM point of the
        # listing this row takes over, kept so the dot does not move). Never
        # geocode over it.
        coords_exact=True,
        recurring_days=rec,
        pin_only=pin_only,
        icon=GLYPH,
    )


def standing_event(src: Dict[str, Any], c: Dict[str, Any], d: date) -> Optional[NormalizedEvent]:
    days = c["days"]
    rec = to_recurring(days)
    if not rec:
        return None
    season = f" ({c['season']})" if c.get("season") else ""
    lines = [f"🕘 Opening hours{season}: {hours_text(days)}."]
    if c.get("note"):
        lines.append(c["note"])
    lines += _contact_lines(c)
    # THE HOURS LINE IS FIRST, before anything the sync's 800-character
    # _cap_prose could cut (community-centres.md, the Fairfax price line).
    desc = (_head(src, c) + "\n\n" + "\n".join(lines) + "\n\n"
            + _foot(src, c, f"Opening hours from {src['publisher']}. They can change, and centres "
                            f"close on some holidays; check with the centre before travelling."))
    return _place_event(src, c, d, rec, desc)


def refused_event(src: Dict[str, Any], c: Dict[str, Any], d: date) -> NormalizedEvent:
    """The row for a centre whose row this adapter owns but whose hours it will
    not publish today: closed, no hours for the season, hours it cannot read, or
    gone from the city's data.

    A STANDING ROW NEVER EXPIRES and an upsert cannot delete, so SKIPPING such a
    centre would leave whatever was written last rolling forward for ever: Seattle's
    Northgate lists only "Summer" hours and would have kept them all winter, and a
    centre the city marks closed would have kept advertising its old week. And a
    taken-over OSM listing that this adapter skips has no writer at all, because
    osm_amenities skips every element claimed_osm_refs() names (8 of 70 on
    2026-10-05). So the row is REWRITTEN, saying what is true:

      * a taken-over OSM listing keeps being the listing osm_amenities would write
        for a centre with unknown hours: the all-week window, the OSM line, and a
        sentence saying closed or "opening times are not listed".

    A ROW OF OUR OWN IS NOT WRITTEN AT ALL (the caller skips it). The first build
    wrote it as `pin_only` scenery, but ../mapsee opens a sheet for every pin
    (app.js pinTapHandler -> openEventFromMap), and without the OSM line
    isStandingAllDay is false, so the sheet drew the all-week window as
    "12:00 AM - 11:59 PM" for 16 centres the city calls closed or gives no hours
    (review, 2026-10-05). None of those rows exists yet, so skipping leaves
    nothing behind. THE GAP THAT REMAINS: an own row written with hours on one
    day and refused (or gone from the data) on a later one keeps its last week
    until something retires it; nothing does yet.
    """
    kind, detail = c["refused"]
    if kind == "closure":
        status = (f"⚠ The city's data lists this centre as closed: “{detail}”."
                  if detail else "⚠ The city's data lists this centre as closed.")
    elif kind == "gone":
        status = "🕑 Opening times are not listed: the city's data no longer lists this centre."
    else:
        status = "🕑 Opening times are not listed — check before travelling."
    lines = [status] + _contact_lines(c)
    desc = (_head(src, c) + "\n\n" + "\n".join(lines) + "\n\n"
            + _foot(src, c, f"Status from {src['publisher']}; check with the centre before travelling."))
    return _place_event(src, c, d, ALWAYS, desc, pin_only=not _same(c))


def late_night_events(src: Dict[str, Any], c: Dict[str, Any], late: Dict[int, Tuple[int, int]],
                      d: date, horizon: int, holidays: set, title: str) -> List[NormalizedEvent]:
    """One row per evening of the Late Night programme, holidays skipped."""
    out = []
    when = "; ".join(f"{DAY_NAMES[i]} {clock_label(o)}-{clock_label(cl)}"
                     for i, (o, cl) in sorted(late.items()))
    for k in range(horizon):
        day = d + timedelta(days=k)
        span = late.get(day.weekday())
        if not span or day in holidays:
            continue
        o, cl = span
        end_day = day + timedelta(days=1) if cl >= 24 * 60 else day
        end = "00:00" if cl >= 24 * 60 else hhmm(cl)
        desc = (f"{title} at {c['name']}: {src['programme_line']} ({when}).\n\n"
                + (f"Call {c['phone']}.\n" if c.get("phone") else "")
                + (f"🌐 Website: {c['website']}\n" if c.get("website") else "")
                + f"\nFrom {src['publisher']}. No evening is listed on a federal holiday; "
                  f"check with the centre before travelling."
                + (MAP_POINT_CREDIT if c.get("osm_point") else ""))
        out.append(NormalizedEvent(
            source=f"{SOURCE}:late-night",
            source_id=f"{_norm(c['name'])}|{day.isoformat()}",
            fingerprint=_fp(SOURCE, "late-night", _norm(c["name"]), day.isoformat()),
            name=title,
            description=desc,
            start_local=f"{day.isoformat()}T{hhmm(o)}:00",
            end_local=f"{end_day.isoformat()}T{end}:00",
            timezone=src.get("timezone"),
            venue_name=c["name"],
            latitude=c["lat"], longitude=c["lon"],
            address=c.get("address") or None,
            city=c.get("city") or src.get("city"), region=src.get("region"),
            country=src.get("country"),
            category=src.get("category", "community"),
            categories=list(src.get("late_night_categories") or []),
            ticket_url=c.get("website"),
            coords_exact=True,
        ))
    return out


def centre_name(src: Dict[str, Any], attrs: Dict[str, Any]) -> str:
    """The display name: NYC's contract code "(C06)" dropped, capitals tidied,
    the source's abbreviations and truncations ("OAC", "Senior Cen") written out."""
    name = tidy_name(re.sub(r"\s*\([A-Z0-9]{2,4}\)\s*$", "", field(attrs, src["fields"]["name"])),
                     src.get("keep_upper") or ())
    for pat, rep in (src.get("name_rewrites") or {}).items():
        name = re.sub(pat, rep, name, flags=re.I)
    return _spaces(name)


def _closure(src: Dict[str, Any], attrs: Dict[str, Any]) -> Optional[str]:
    """The longest status value saying closed ("Closed - Childcare Site Only"
    over Alki's bare OPEN_ "Closed"), or None."""
    rx = src.get("closure_rx")
    hits = [_spaces(str(attrs.get(f) or "")) for f in (src.get("closure_fields") or [])]
    hits = [v for v in hits if rx and re.search(rx, v, re.I)]
    return max(hits, key=len) if hits else None


def build_centre(src: Dict[str, Any], rec: Dict[str, Any], stats: Dict[str, int],
                 d: date) -> Optional[Dict[str, Any]]:
    """One record -> a centre dict, or None when it is not a centre this adapter
    can place at all. A centre whose HOURS are refused comes back with
    c["refused"] = (reason, detail): refused_event writes its row."""
    attrs = rec["attrs"]
    f = src["fields"]
    keep = src.get("keep_upper") or ()
    name = centre_name(src, attrs)
    if not name:
        stats["excluded: no name"] += 1
        return None
    for fname, rx in (src.get("exclude") or {}).items():
        if re.search(rx, str(attrs.get(fname) or ""), re.I):
            stats[f"excluded: not a centre ({fname})"] += 1
            return None
    sid = field(attrs, f.get("id")) or _norm(name)
    lat, lon = rec.get("lat"), rec.get("lon")
    m = (src.get("osm") or {}).get(sid)
    m = m if isinstance(m, dict) else None
    osm_point = bool(m) and m.get("lat") is not None
    if osm_point:
        lat, lon = m["lat"], m["lon"]
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180) \
            or (abs(lat) < 0.01 and abs(lon) < 0.01):
        stats["excluded: no coordinate"] += 1
        return None
    city = field(attrs, f.get("city")) or src.get("city")
    c = {
        "name": name, "sid": sid, "source_id": f"{src['key']}:{sid}",
        "fingerprint": osm_fingerprint(m["ref"]) if m and m.get("mode") == "same"
                       else _fp(SOURCE, src["key"], sid),
        "osm_mode": m.get("mode") if m else None, "osm_point": osm_point,
        "lat": float(lat), "lon": float(lon),
        "address": tidy_name(field(attrs, f.get("address"))),
        "city": tidy_name(city) if city else None,
        "postal_code": field(attrs, f.get("postal_code")),
        "phone": tidy_phone(field(attrs, f.get("phone")), src.get("default_area_code")),
        "website": _site(field(attrs, f.get("website"))),
        "operator": tidy_name(field(attrs, f.get("operator")), keep),
        "noun": (src.get("noun_by_kind") or {}).get(centre_kind(src, attrs)) or src.get("noun"),
        "season": None, "days": None, "late": {}, "note": "", "refused": None,
    }
    closed = _closure(src, attrs)
    if closed:
        stats["excluded: closure"] += 1
        print(f"[facility-hours]   closed per the status fields ({closed!r}): {name}")
        c["refused"] = ("closure", closed)
        return c
    label, days, late, overruled, reason = read_hours(src, attrs, d)
    stats["days whose DAY_ flag says No beside listed hours (hours kept)"] += overruled
    if reason:
        stats[f"excluded: {reason}"] += 1
        detail = None
        if reason == "closure":
            raw = field(attrs, f.get("hours")) if src["format"] == "free_text" else ""
            detail = raw or None
            print(f"[facility-hours]   closure in the hours field: {name}")
        c["refused"] = (reason, detail)
        return c
    if src.get("late_night_field") and str(attrs.get(src.get("late_night_flag", "LN_LOCATION"))
                                             or "").strip().lower() == "yes":
        ln_days, ln_reason = parse_week_text(field(attrs, src["late_night_field"]))
        if ln_reason or any(len(v) != 1 for v in ln_days.values()):
            stats["Late Night text unreadable (centre kept, programme not)"] += 1
        else:
            late = {**(late or {}), **{k: v[0] for k, v in ln_days.items()}}
    stale = (src.get("late_night_disagrees") or {}).get(sid)
    if late and isinstance(stale, str) and src.get("late_night_field") \
            and _spaces(stale) == field(attrs, src["late_night_field"]):
        # The open data's Late Night times contradict the centre's own page; the
        # config records the text it said, so the programme comes back on its
        # own the day the city corrects it.
        stats["Late Night skipped: open data contradicts the centre's page"] += 1
        late = {}
    if f.get("note"):
        n = field(attrs, f["note"])
        if n and re.search(src.get("note_keep_rx") or r"$^", n, re.I):
            c["note"] = n.rstrip(".") + "."
    c.update(season=label, days=days, late=late or {})
    return c


def gone_centres(src: Dict[str, Any], seen_sids: set) -> List[Dict[str, Any]]:
    """Taken-over OSM listings ("same" in the config) whose centre the city's
    data no longer carries. Their rows have no other writer, so they are written
    from what the config itself knows: OSM's name and point."""
    out = []
    for sid, m in (src.get("osm") or {}).items():
        if sid in seen_sids or not isinstance(m, dict) or m.get("mode") != "same":
            continue
        name = m.get("osm_name") or sid
        out.append({"name": name, "sid": sid, "source_id": f"{src['key']}:{sid}",
                    "fingerprint": osm_fingerprint(m["ref"]), "osm_mode": "same",
                    "osm_point": True, "lat": float(m["lat"]), "lon": float(m["lon"]),
                    "address": None, "city": src.get("city"), "postal_code": None,
                    "phone": None, "website": None, "operator": None,
                    "noun": src.get("noun"), "refused": ("gone", None)})
    return out


def ingest(store, reader: Reader, src: Dict[str, Any], cfg: Dict[str, Any],
           d: date) -> Dict[str, int]:
    from collections import Counter
    stats: Counter = Counter()
    max_age = src.get("max_age_days", cfg.get("max_age_days"))
    if max_age:
        age = data_age_days(reader, src, datetime(d.year, d.month, d.day, 12))
        if age is not None and age > float(max_age):
            stats[f"source refused: data last edited {age:.0f} days ago (> {max_age})"] += 1
            return stats
        stats["data age, days"] = int(age) if age is not None else -1
    recs = read_arcgis(reader, src) if src["type"] == "arcgis" else read_socrata(reader, src)
    stats["records read"] = len(recs)
    if not recs:
        # An empty answer is not "every centre closed": write nothing, so no
        # taken-over listing is rewritten as gone on a publisher's bad day.
        return stats
    # The programme's evenings are projected a short way only: an upsert cannot
    # remove an evening the open data later drops, and its LN_HOURS has been
    # wrong before (South Park, Van Asselt; see the config).
    horizon = int(src.get("late_night_days_ahead", cfg.get("late_night_days_ahead", 21)))
    title = src.get("late_night_title", "Teen Late Night")
    hol = set()
    for y in (d.year, d.year + 1):
        hol |= us_holidays(y, src.get("extra_holidays") or [])
    seen, sids = set(), set()
    centres = []
    for rec in recs:
        c = build_centre(src, rec, stats, d)
        if c:
            sids.add(c["sid"])
            centres.append(c)
    gone = gone_centres(src, sids)
    stats["excluded: gone from the city's data (claimed OSM listing)"] += len(gone)
    for c in centres + gone:
        rows = []
        ev = standing_event(src, c, d) if not c["refused"] else None
        if not c["refused"] and not ev:
            stats["excluded: hours that overlap themselves"] += 1
            c["refused"] = ("unreadable time", None)
        if c["refused"]:
            if not _same(c):
                # See refused_event: an own row has no shape that can say
                # "closed" without ../mapsee drawing a 24-hour clock.
                stats["refused, not written (own row)"] += 1
                continue
            ev = refused_event(src, c, d)
            stats["refused, written as a listing with no hours (taken-over OSM listing)"] += 1
        elif _same(c):
            stats["rows taking over an OSM listing"] += 1
        elif c["osm_mode"] == "colocated":
            stats["rows sharing an OSM building's dot"] += 1
        rows.append(ev)
        if c.get("late") and not c["refused"]:
            rows += late_night_events(src, c, c["late"], d, horizon, hol, title)
        for ev in rows:
            if ev.fingerprint in seen:
                stats["duplicate identity in one read (second dropped)"] += 1
                continue
            seen.add(ev.fingerprint)
            if store is not None:
                store.upsert(ev)
            stats["standing rows" if ev.recurring_days else "dated rows (Late Night)"] += 1
    stats["rows written"] = stats.get("standing rows", 0) + stats.get("dated rows (Late Night)", 0)
    return stats


# ---------------------------------------------------------------------------
# Proposing the OSM map (run by a person; never by CI)
# ---------------------------------------------------------------------------
_GENERIC = {"community", "center", "centre", "centers", "the", "and", "for", "senior", "seniors",
            "recreation", "rec", "teen", "life", "older", "adult", "adults", "oac", "inc",
            "citizens", "citizen", "club", "social", "neighborhood", "ctr", "program",
            "services", "house", "multi", "generational", "satellite", "regional", "hall",
            "activity", "youth", "nsc", "luncheon", "cafe", "street", "avenue"}
_SENIOR_RX = re.compile(r"senior|older\s+adult|aging|elder|golden\s+age", re.I)
_TEEN_RX = re.compile(r"\bteen|youth", re.I)


def _toks(name: str) -> set:
    return {t for t in re.findall(r"[a-z]{3,}", (name or "").lower())} - _GENERIC


def _tok_eq(a: str, b: str) -> bool:
    if a == b or (min(len(a), len(b)) >= 3 and (a.startswith(b) or b.startswith(a))):
        return True
    if len(a) >= 4 and len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1      # Marc / Mark
    return False


def name_cover(a: str, b: str) -> float:
    """Share of the SHORTER name's distinctive words found in the longer one."""
    ta, tb = _toks(a), _toks(b)
    if not ta or not tb:
        return 0.0
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    return sum(1 for t in short if any(_tok_eq(t, u) for u in long_)) / len(short)


def osm_kind(tags: Dict[str, str]) -> str:
    who = " ".join(str(tags.get(k) or "") for k in ("community_centre:for", "social_facility:for"))
    name = tags.get("name") or ""
    if _SENIOR_RX.search(name) or re.search(r"senior|elder", who):
        return "senior"
    if _TEEN_RX.search(name) or re.search(r"youth|juvenile", who):
        return "teen"
    return "general"


_ADDR_WORDS = {"st": "street", "str": "street", "ave": "avenue", "av": "avenue", "pkwy": "parkway",
               "pky": "parkway", "blvd": "boulevard", "rd": "road", "dr": "drive", "pl": "place",
               "ln": "lane", "ct": "court", "hwy": "highway", "sq": "square", "ter": "terrace",
               "e": "east", "w": "west", "n": "north", "s": "south", "ne": "northeast",
               "nw": "northwest", "se": "southeast", "sw": "southwest"}


def addr_key(text: str) -> str:
    """"415 E 93rd St" and "415 East 93rd Street" -> "415 east 93 street"; "" when
    the text does not start with a house number (a street alone is no address)."""
    toks = re.findall(r"\d+(?:-\d+)?[a-z]*|[a-z]+", (text or "").lower())
    if not toks or not toks[0][0].isdigit():
        return ""
    toks = [_ADDR_WORDS.get(t, t) for t in toks]
    return " ".join(re.sub(r"^(\d+)(?:st|nd|rd|th)$", r"\1", t) for t in toks)


def metres(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    return 6371000 * math.hypot((lo2 - lo1) * math.cos((la1 + la2) / 2), la2 - la1)


def propose_osm(centres: List[Dict[str, Any]], elements: List[Dict[str, Any]],
                same_m: float = 150, coloc_m: float = 40) -> Dict[str, Dict[str, Any]]:
    """centres: [{"id", "name", "kind", "lat", "lon", "address"}]; elements: [{"ref",
    "tags", "lat", "lon"}] where lat/lon is the bounding-box centre.
    -> {centre id: {"ref", "mode", "lat", "lon", "metres", "osm_name"}}.

    THE SAME STREET ADDRESS IS THE SAME BUILDING, however far the box centre is.
    40 m alone left four NYC older adult centres beside the OSM centre whose
    building they are in, each on its own dot 41-67 m away: Stanley Isaacs at
    415 E 93rd St, the Y at 54 Nagle Ave, Bensonhurst at 7802 Bay Pkwy and
    Commonpoint Queens at 58-20 Little Neck Pkwy. So within `same_m`, an equal
    housenumber + street is "colocated" too."""
    cands = []
    for c in centres:
        for e in elements:
            # Only community centres: they are what osm_amenities lists that a
            # centre could duplicate or sit inside. A library next door is
            # another building (NYC: 3 older adult centres 31-40 m from one).
            if e["tags"].get("amenity") != "community_centre":
                continue
            dist = metres((c["lat"], c["lon"]), (e["lat"], e["lon"]))
            if dist > same_m:
                continue
            cover = name_cover(c["name"], e["tags"].get("name") or "")
            same = cover == 1.0 and osm_kind(e["tags"]) == c["kind"]
            ea = addr_key(" ".join(str(e["tags"].get(k) or "")
                                   for k in ("addr:housenumber", "addr:street")))
            at = bool(ea) and ea == addr_key(c.get("address") or "")
            cands.append((0 if same else (1 if at else 2), dist, c["id"], e, same, at))
    cands.sort(key=lambda t: (t[0], t[1]))
    out, taken = {}, set()
    for _, dist, cid, e, same, at in cands:
        if cid in out:
            continue
        if same and e["ref"] not in taken:
            taken.add(e["ref"])
            mode = "same"
        elif dist <= coloc_m or at:
            mode = "colocated"
        else:
            continue
        out[cid] = {"ref": e["ref"], "mode": mode, "lat": round(e["lat"], 7),
                    "lon": round(e["lon"], 7), "metres": round(dist),
                    "osm_name": e["tags"].get("name")}
    return out


def _postpass_elements(session, bbox: List[float]) -> List[Dict[str, Any]]:
    w, s, e, n = bbox
    sql = ("SELECT osm_type, osm_id, tags, ST_XMin(geom) x0, ST_XMax(geom) x1, "
           "ST_YMin(geom) y0, ST_YMax(geom) y1 FROM postpass_pointpolygon "
           "WHERE tags->>'amenity' = 'community_centre' "
           f"AND geom && ST_MakeEnvelope({w},{s},{e},{n},4326)")
    r = session.post(POSTPASS, data={"data": sql, "options[geojson]": "false"}, timeout=180)
    r.raise_for_status()
    body = r.json()
    rows = body.get("result") or [f.get("properties") for f in body.get("features", [])]
    kind = {"N": "node", "W": "way", "R": "relation"}
    return [{"ref": f"{kind[x['osm_type']]}/{x['osm_id']}", "tags": x["tags"],
             "lat": (float(x["y0"]) + float(x["y1"])) / 2,
             "lon": (float(x["x0"]) + float(x["x1"])) / 2} for x in rows]


def propose_source(reader, src: Dict[str, Any], fetch_elements) -> Optional[Dict[str, Dict[str, Any]]]:
    """One source's proposed `osm` map, or None when no record has a coordinate
    (no bounding box to ask about; min() of nothing used to crash the run)."""
    recs = read_arcgis(reader, src) if src["type"] == "arcgis" else read_socrata(reader, src)
    cs = []
    for r in recs:
        if r.get("lat") is None or r.get("lon") is None:
            continue
        f = src["fields"]
        nm = centre_name(src, r["attrs"])
        cs.append({"id": field(r["attrs"], f.get("id")) or _norm(nm), "name": nm,
                   "kind": centre_kind(src, r["attrs"]), "lat": r["lat"], "lon": r["lon"],
                   "address": field(r["attrs"], f.get("address"))})
    if not cs:
        return None
    pad = 0.01
    bbox = [min(c["lon"] for c in cs) - pad, min(c["lat"] for c in cs) - pad,
            max(c["lon"] for c in cs) + pad, max(c["lat"] for c in cs) + pad]
    return propose_osm(cs, fetch_elements(bbox))


# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import civic facility opening hours (community "
                                             "and older-adult centres) as standing rows.")
    ap.add_argument("--config", default="facility_hours_sources.json")
    ap.add_argument("--store", default="facility_hours_events.json")
    ap.add_argument("--only", help="sources whose key contains this text")
    ap.add_argument("--max-minutes", type=float, default=DEFAULT_MAX_MINUTES,
                    help="whole-run deadline; no request starts after it (0 = none)")
    ap.add_argument("--dry-run", action="store_true", help="read and count; write no store")
    ap.add_argument("--propose-osm", action="store_true",
                    help="print each source's proposed `osm` map (one Postpass query per "
                         "source) for a person to check and paste into the config")
    a = ap.parse_args(argv)

    started = time.monotonic()
    cfg = json.loads(open(a.config, encoding="utf-8").read())
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept": "application/json"})
    reader = Reader(session, deadline=started + a.max_minutes * 60 if a.max_minutes else None)
    d = today()
    sources = [s for s in cfg.get("sources", []) if not a.only or a.only in s["key"]]

    if a.propose_osm:
        for src in sources:
            got = propose_source(reader, src, lambda bbox: _postpass_elements(session, bbox))
            if got is None:
                print(f"# {src['key']}: no placeable centre - nothing to propose")
                continue
            modes = {}
            for v in got.values():
                modes[v["mode"]] = modes.get(v["mode"], 0) + 1
            print(f"# {src['key']}: {len(got)} centres, {modes}")
            print(json.dumps({"osm": got}, indent=1, ensure_ascii=False))
        return 0

    store = None if a.dry_run else EventStore(a.store)
    total = 0
    for src in sources:
        label = src.get("name") or src["key"]
        before = reader.requests
        try:
            stats = ingest(store, reader, src, cfg, d)
        except Refused as exc:
            print(f"[facility-hours] {label} REFUSED: {exc} - not retried")
            continue
        except OutOfTime as exc:
            print(f"[facility-hours] {label} STOPPED: {exc} (--max-minutes {a.max_minutes:g})")
            break
        except Exception as exc:  # noqa: BLE001 - one source never stops another
            print(f"[facility-hours] {label} FAILED: {type(exc).__name__}: {exc}")
            continue
        total += stats.get("rows written", 0)
        print(f"[facility-hours] {label}: {stats.get('rows written', 0)} rows "
              f"in {reader.requests - before} requests")
        for k, v in sorted(stats.items()):
            if k != "rows written":
                print(f"[facility-hours]     {k}: {v}")
        if store is not None:
            store.save()                         # after every source: a later failure loses nothing
    if store is not None:
        store.save()
        st = store.stats
        print(f"[facility-hours] done in {time.monotonic() - started:.0f} s: {total} rows "
              f"(added {st.get('added', 0)}, updated {st.get('updated', 0)}, "
              f"rejected {st.get('rejected', 0)}) in {reader.requests} requests; "
              f"store holds {len(store.records)}.")
    else:
        print(f"[facility-hours] dry run done in {time.monotonic() - started:.0f} s: {total} rows "
              f"in {reader.requests} requests (nothing written)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
