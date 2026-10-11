#!/usr/bin/env python3
"""
mapsee_ingest_ava.py - American Volkssport Association traditional events
(volksmarches): free, non-competitive 5 and 10 km walks run by local clubs,
plus the few bike rides and swims the same calendar carries.

    python mapsee_ingest_ava.py --config ava_sources.json --store feeds_events.json \
        [--max-minutes 10] [--force] [--dry-run]

WHAT IT READS. my.ava.org publishes no feed: no iCal, no JSON, no schema.org
Event on any page (checked 2026-10-11). Two pages carry everything:

  * regional-te-list.php?time=year&rsel=all - ONE GET lists every traditional
    event of the next 365 days: 166 on 2026-10-11, 122 of them in the next 120
    days (Walk 112, Swim 6, Bike 3, untyped 1) across 26 states. Each row gives
    the serial number (sn), the day, the type, the trail rating, the town and
    the club - but no place and no clock.
  * event-view.php?sn=<sn> - one page per event with the start-table window,
    the start place as the club typed it for map apps, a map pin, distances,
    restrooms/pets/strollers/wheelchairs, and the club's own words. On the
    live pass of 2026-10-11 all 122 in-window pages had a pin, 117 a start time
    this reads and 103 a start place: 120 rows (two pairs of listings share a
    title, a day and a start) in 27 states, 123 requests, 2.0 minutes.

So a run is one list read and one detail read per event in the window, at the
config's pause (1 s): ~2 minutes for ~120 events. Events outside the window are
never fetched. The ~2,084 seasonal and year-round self-guided routes sit behind
a stateful POST search and are deliberately NOT read: a standing route is not a
dated event, and taking them is a separate decision.

HTTP: the production User-Agent, robots.txt read before the first request to a
host (my.ava.org answers 404, so RFC 9309 says no rules), a 401/403/429 or a
bot challenge ends the run, and no request starts after --max-minutes.
https://my.ava.org/legal.php is a software warranty disclaimer, with no clause
on automated access.

WHY THESE RULES:

1. THE PIN IS THE PLACE. The start place is free text for map apps ("South Hill
   Park, Puyallup, WA", "Legacy Park Cottleville MO") and is usually a park, not
   a street; Census, the sync's only geocoder, cannot read it. The page's map pin
   (google.maps.LatLng) is what the club placed, so the row carries it as
   coords_exact. A page with no pin is not written. The page itself warns the
   pin "may not accurately indicate the start box"; the start place and the
   club's driving directions are therefore kept in the description.

2. THE START TABLE IS A WINDOW, NOT AN EVENT TIME. "Start table open: 9 am-9:30
   am off trail by 11:30" means walkers may set off between 9 and 9:30 and must
   be back by 11:30. The row starts at the window's opening and ends at the
   "finish by" / "off trail by" time when the club states one. A clock written
   without am/pm ("3:30 - 6:30", "10:00 to noon") takes the first am/pm in the
   window, and with none at all an hour of 7-11 is morning and 12-6 afternoon:
   nobody opens a start table at 3:30 in the morning. No clock at all is an
   all-day row, never a guess.

3. A MULTI-DAY EVENT IS ONE ALL-DAY SPAN. "Starting Fri, Oct 16th, 2026 for 3
   days" is the same route open each day, with the hours per day in the
   description; one timed row from Friday 10:00 to Sunday night would read as a
   walk that never stops.

4. THE NAME SAYS WHAT IT IS. A club names its event after the place or the
   occasion ("Falling Leaves", "St. Nicholas Catholic Church Fall Bazaar"). A
   title that names no activity gets "(volkssport walk)" appended, so the map
   says it is a walk and the market classifier is not left reading "Bazaar".

5. FREE IS THE CLUB'S WORD, AND MOSTLY IT IS NOT FREE. No membership is
   required ("No membership is required, only the motivation to walk!",
   ava.org/about), but of the 122 event pages read on 2026-10-11, 30 state a
   walker fee ($4 on 28 of them: "The fee for Non-IVV & IVV credit is $4.00",
   "All walkers will be assessed the $4 walker's fee") and 1 says everyone
   walks free (Omaha's, whose sponsors pay). The other 91 say nothing. So a
   row says "Free to walk." only where its own text says so for everyone
   ("Children under 12 walk for free" is not that), states the fee where the
   club does, and otherwise says nothing about price.

Category: community, with fitness and outdoors (a swim: fitness only - the
2026 swims are indoor pools at a YMCA and at motels).
"""
from __future__ import annotations

import argparse
import html as htmllib
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from mapsee_ingest import (EventStore, NormalizedEvent, USER_AGENT, looks_online_only,
                           make_fingerprint, norm_categories)

SOURCE = "ava"
LIST_URL = "https://my.ava.org/regional-te-list.php?time=year&rsel=all"
DETAIL_URL = "https://my.ava.org/event-view.php?sn={sn}"
DEFAULT_WITHIN_DAYS = 120
DEFAULT_MAX_MINUTES = 10.0
SAVE_EVERY = 25
DESCRIPTION_ROOM = 780        # under the sync's DESCRIPTION_MAX of 800

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], 1)}
_MON3 = {k[:3]: v for k, v in MONTHS.items()}

# What the activity is called on the map, by the list's type glyph.
KINDS = {
    "walk": ("volkssport walk", ["fitness", "outdoors"]),
    "bike": ("volkssport bike ride", ["fitness", "outdoors"]),
    "swim": ("volkssport swim", ["fitness"]),
    "ski": ("volkssport ski", ["fitness", "outdoors"]),
    "snowshoe": ("volkssport snowshoe", ["fitness", "outdoors"]),
}
# A title already naming its activity is left alone (rule 4).
# "Trail" is not on it: "South Hill Park and Chapman Trail" says where, not what.
_ACTIVITY_RX = re.compile(
    r"\b(walk|walks|walking|walkers?|hike|hikes|hiking|stroll|ramble|trek|"
    r"volks\w*|bike|biking|bicycle|ride|cycl\w*|swim\w*|ski|skiing|snowshoe\w*|paddle)\b",
    re.I)
# PRICE IS READ SENTENCE BY SENTENCE, FROM THE CLUB'S OWN TEXT (rule 5). Of the
# 122 event pages of 2026-10-11, 30 state a walker fee - "All walkers will be
# assessed the $4 walker's fee", "The fee for Non-IVV & IVV credit is $4.00",
# "Exact change is appreciated ($4)" - and 1 says everyone walks free. A sentence
# about a hotel room, parking or an optional award is not the walk's price, and
# "Children under 12 walk for free" is not free for everyone.
_FREE_RX = re.compile(
    r"\b(?:(?:everyone|anyone|all)\s+(?:walks?|can\s+walk)\s+(?:for\s+)?free|free\s+to\s+(?:walk|participate)|"
    r"no\s+(?:charge|cost|fee)\s+(?:to|for)\s+(?:walk|the\s+walk|walkers|participants)|"
    r"walking\s+is\s+free|(?:the\s+)?walks?\s+(?:is|are)\s+free)\b", re.I)
_FEE_WORD_RX = re.compile(
    r"\b(fees?|pay|paid|contribut\w*|assessed|registration|exact\s+change|correct\s+change|"
    r"cost\s+(?:is|per)|per\s+(?:walker|person|participant))\b", re.I)
_NOT_THE_WALK_RX = re.compile(
    r"\b(rooms?|hotel|motel|nights?|parking|lot|tax|museum|awards?|patch|pins?|packages?|shirts?|"
    r"lunch|dinner|breakfast|meals?|bus|ferry|train|tickets?|admission\s+to|entrance\s+fee|entry\s+fee|"
    r"book|books|prizes?|raffle|sale|purchase)\b", re.I)
_SOME_ONLY_RX = re.compile(
    r"\b(child|children|kids?|under\s+\d+|\d+\s+(?:and|&)\s+under|ages?\s+\d+|youth|students?|members?|scouts?)\b",
    re.I)
_AMOUNT_RX = re.compile(r"\$\s?(\d{1,3}(?:\.\d{2})?)")
_CANCELLED_RX = re.compile(r"\b(cancel+ed|cancel+ation|postponed)\b", re.I)


def walk_price(text: str) -> Tuple[Optional[str], bool]:
    """(the walker fee the club states, e.g. "$4", or None; everyone walks free).

    A fee wins over a free phrase: a page that says both is a page where
    somebody pays."""
    fee = None
    free = False
    for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z(])|\n+", text or ""):
        # A clause, not the sentence: "Registration Fee is $4.00 and (optional)
        # "A" Award will be available (Only $5.00 ...)" is a walk fee beside an
        # award, while "a $2.00 fee for the train station parking lot" is not.
        for clause in re.split(r";|\s+and\s+", sentence):
            amount = _AMOUNT_RX.search(clause)
            if (fee is None and amount and _FEE_WORD_RX.search(clause)
                    and not _NOT_THE_WALK_RX.search(clause)):
                fee = "$" + re.sub(r"\.00$", "", amount.group(1))
        if _FREE_RX.search(sentence) and not _SOME_ONLY_RX.search(sentence):
            free = True
    return fee, (free and fee is None)


# THE START PLACE IS TYPED FOR A MAP APP, AND IT SHOWS. Read live 2026-10-11:
# "Get directions to Comfort Suites Dayton Wright Patterson.", "Siri Directions
# to Marion Township Park in Maria Stein Ohio.", "Go to Flour Garden Bakery at
# 109 Neal St. Grass Valley California.", a bare "38.25546", a street with no
# name before it ("374 Trevor Lane") and a name with its street glued on
# ("United Methodist Church of Savage 9050 Baltimore St. Savage Maryland").
_DIRECTIONS_RX = re.compile(r"^\s*(?:siri\s+)?(?:get\s+)?(?:directions\s+to|go\s+to)\s+(?:the\s+)?", re.I)
_STREET_RX = re.compile(
    r"\b\d{1,6}[A-Za-z]?\s+(?:[NSEW]\.?\s+|North\s+|South\s+|East\s+|West\s+)?[\w.' -]*?"
    r"\b(?:St|Street|Ave|Avenue|Rd|Road|Dr|Drive|Blvd|Boulevard|Ln|Lane|Way|Hwy|Highway|Pkwy|Parkway|"
    r"Ct|Court|Pl|Place|Cir|Circle|Ter|Terrace|Pike|Trail|Loop|Plaza|Square|Row)\b\.?.*$", re.I)
US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
    "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia",
    "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
    "PR": "Puerto Rico", "VI": "Virgin Islands", "GU": "Guam",
}


# A town cell holding a place rather than a town: "Gemini Springs Park", "Holiday
# Inn Express & Suites", "NeighborGood Pantry", "Patuxent Research Refuge".
_VENUE_WORD_RX = re.compile(
    r"\b(park|preserve|prsv|refuge|inn|suites|hotel|motel|resort|pantry|center|centre|church|"
    r"school|college|university|museum|mall|plaza|trailhead|campground|lodge|marina)\b", re.I)


def _strip_town(s: str, city: Optional[str], region: Optional[str]) -> str:
    """Drop a trailing ZIP, state and town an address repeats: the sync appends
    the town and state itself."""
    s = re.sub(r"[\s,]+\d{5}(?:-\d{4})?\.?\s*$", "", s.strip())
    for n in (US_STATES.get((region or "").upper()), region):
        if n:
            s = re.sub(r"[\s,]+" + re.escape(n) + r"\.?\s*$", "", s, flags=re.I)
    if city:
        s = re.sub(r"[\s,]+" + re.escape(city) + r"\.?\s*$", "", s, flags=re.I)
    return re.sub(r"(?i)[\s,]+(?:in|at|of|@)$", "", s.strip(" ,")).strip(" ,")


def split_place(place: Optional[str], city: Optional[str] = None,
                region: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    """(venue name, street address) from the club's start place, either None."""
    p = _DIRECTIONS_RX.sub("", place or "").strip().rstrip(".").strip()
    if not p or re.fullmatch(r"[-\d.,\s]+", p) or p.lower().startswith("the address"):
        return None, None
    paren = re.search(r"\(([^)]+)\)", p)
    inner = paren.group(1).strip() if paren else None
    p = _strip_town(re.sub(r"\s*\([^)]*\)", "", p), city, region)
    if not p:
        return inner, None
    street = _STREET_RX.search(p)
    if street and street.start() == 0:
        return inner, p
    if street:
        venue = _strip_town(p[:street.start()], None, None) or inner
        return venue or None, street.group(0).strip(" ,") or None
    return (p.split(",")[0].strip() or inner or None), None


def clean_city(city: Optional[str], region: Optional[str],
               place: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    """(town, venue) from the list's "City & State" cell, which is free text too:
    "Bristow, Oklahoma", "St. Louis, MO", "Puyallup / Grayland Park", "HOUSTON",
    and sometimes the venue where the town should be. A cell is taken for a
    venue only when it names a kind of place AND the start place begins with
    it: "Clifton Park" is a town in New York, "Gemini Springs Park" beside a
    start place of "Gemini Springs Park DeBary Florida" is not."""
    c = _strip_town((city or "").strip(), None, region)
    if not c:
        return None, None
    c = re.split(r"\s*/\s*", c)[0].strip()
    if c.isupper() and len(c) > 3:
        c = c.title()
    head, _, tail = c.partition(",")
    if tail and _VENUE_WORD_RX.search(head):
        return tail.strip() or None, head.strip()       # "Robinson Preserve, Bradenton"
    norm = lambda x: re.sub(r"[^a-z0-9]+", " ", (x or "").lower().replace("&", "and")).strip()
    if _VENUE_WORD_RX.search(c) and place and norm(_DIRECTIONS_RX.sub("", place)).startswith(norm(c)):
        return None, c
    return c or None, None


def _text(fragment: str) -> str:
    """Visible text of an HTML fragment, one line."""
    t = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", fragment or "")
    t = re.sub(r"(?i)<br\s*/?>", " ", t)
    t = htmllib.unescape(re.sub(r"<[^>]+>", " ", t))
    return re.sub(r"\s+", " ", t).strip()


# --------------------------------------------------------------------------- #
# The list page

_MONTH_ROW_RX = re.compile(r"(\d{4})\s*&nbsp;-&nbsp;\s*([A-Za-z]+)|(\d{4})\s+-\s+([A-Za-z]+)")


def parse_list(page: str) -> List[Dict[str, Any]]:
    """Every event row of the year list, in page order.

    The table carries a month heading row ("2026 - October", colspan 9) above
    the days of that month, then one row per event: View, Add, day ("11" or
    "16 - 18"), a type glyph (alt='Walk'), trail rating, "Town,<br>ST",
    "<sn> -<br>Name", club. A row that follows with a single noborder cell is
    that event's one-line blurb.
    """
    out: List[Dict[str, Any]] = []
    year = month = None
    for tr in re.findall(r"(?is)<tr\b[^>]*>(.*?)</tr>", page):
        if "colspan" in tr and "event-view.php" not in tr:
            m = _MONTH_ROW_RX.search(tr)
            if m and "noborder" not in tr:
                y, mon = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
                if mon.lower() in MONTHS:
                    year, month = int(y), MONTHS[mon.lower()]
                continue
            if out and "noborder" in tr and not out[-1].get("blurb"):
                out[-1]["blurb"] = _text(tr)
            continue
        sn = re.search(r"event-view\.php\?sn=(\d+)", tr)
        if not sn or year is None:
            continue
        cells = re.findall(r"(?is)<td\b[^>]*>(.*?)</td>", tr)
        texts = [_text(c) for c in cells]
        kind = re.search(r"""alt=['"]([^'"]+)['"]""", tr)
        # The day cell is the third; "16 - 18" is a span within the month and
        # "30 - 1" one that runs into the next.
        days = re.findall(r"\d{1,2}", texts[2]) if len(texts) > 2 else []
        if not days:
            continue
        try:
            first = date(year, month, int(days[0]))
        except ValueError:
            continue
        last = first
        if len(days) > 1:
            d2 = int(days[-1])
            y2, m2 = (year, month) if d2 >= first.day else ((year + 1, 1) if month == 12 else (year, month + 1))
            try:
                last = date(y2, m2, d2)
            except ValueError:
                last = first
        town = texts[-3] if len(texts) >= 8 else ""
        city, _, region = town.rpartition(",")
        name = re.sub(r"^\s*\d+\s*-\s*", "", texts[-2] if len(texts) >= 8 else "").strip()
        out.append({
            "sn": sn.group(1), "first": first, "last": last,
            "type": (kind.group(1).strip() if kind else ""),
            "rating": (texts[-4] if len(texts) >= 8 else "").strip(),
            "city": city.strip() or town.strip(), "region": region.strip(),
            "name": name, "club": (texts[-1] if len(texts) >= 8 else "").strip(),
        })
    return out


# --------------------------------------------------------------------------- #
# The event page

_STARTING_RX = re.compile(
    r"Starting\s+\w+,?\s+([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})"
    r"(?:\s+for\s+(\d+)\s+days?)?", re.I)
_LATLNG_RX = re.compile(r"google\.maps\.LatLng\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)")


def _paragraphs(page: str) -> List[Tuple[str, str]]:
    """(class, text) of every <p>, in order: the event page is a column of them."""
    out = []
    for m in re.finditer(r"(?is)<p\b([^>]*)>(.*?)</p>", page):
        cls = re.search(r"""class=['"]([^'"]*)['"]""", m.group(1))
        out.append(((cls.group(1) if cls else ""), _text(m.group(2))))
    return out


def _after(paras: List[Tuple[str, str]], label: str) -> Optional[str]:
    """The paragraph after a heading paragraph that reads `label`."""
    for i, (_c, t) in enumerate(paras):
        if t.lower() == label.lower() and i + 1 < len(paras):
            nxt = paras[i + 1][1]
            return nxt or None
    return None


def parse_detail(page: str) -> Dict[str, Any]:
    """The facts of one event-view.php page. Missing facts are None."""
    d: Dict[str, Any] = {}
    what = page.find("evnt-what")
    paras = _paragraphs(page[what:] if what >= 0 else page)
    texts = [t for _c, t in paras]
    d["title"] = next((t for c, t in paras if "large note text" in c and t), None)
    d["kind"] = next((t for c, t in paras if "large notice text" in c and re.search(
        r"\b(walk|bike|swim|ski|snowshoe)\b", t, re.I) and not t.startswith("SN:")), None)
    d["status"] = next((t for c, t in paras if "alert" in c and "large" in c), None)
    when = next((_STARTING_RX.search(t) for t in texts if _STARTING_RX.search(t)), None)
    d["first"] = d["days"] = None
    if when:
        mon = _MON3.get(when.group(1).lower())
        try:
            d["first"] = date(int(when.group(3)), mon, int(when.group(2))) if mon else None
        except ValueError:
            d["first"] = None
        d["days"] = int(when.group(4)) if when.group(4) else 1
    win = next((t for t in texts if t.lower().startswith("start table open")), None)
    d["window"] = re.sub(r"(?i)^start table open:?\s*", "", win).strip() if win else None
    # The start place, as the club typed it for map apps. It follows the line
    # "... for mapping services such as Google or Siri is as follows:".
    d["place"] = None
    for i, t in enumerate(texts):
        if "mapping services" in t.lower() and i + 1 < len(texts):
            cand = texts[i + 1]
            if not cand.lower().startswith(("warning", "special programs")):
                d["place"] = cand or None
            break
    ll = _LATLNG_RX.search(page)
    d["lat"] = d["lon"] = None
    if ll:
        lat, lon = float(ll.group(1)), float(ll.group(2))
        if -90 <= lat <= 90 and -180 <= lon <= 180 and not (abs(lat) < 0.5 and abs(lon) < 0.5):
            d["lat"], d["lon"] = lat, lon
    d["comments"] = _after(paras, "Event comments")
    d["information"] = _after(paras, "Event information")
    d["driving"] = _after(paras, "Driving to the start")
    rd = next((t for t in texts if t.startswith("Rating:")), None)
    d["rating"] = d["distance"] = None
    if rd:
        m = re.search(r"Rating:\s*([0-9][A-E]?\+?)", rd)
        d["rating"] = m.group(1) if m else None
        m = re.search(r"Distance:\s*(.+)$", rd)
        d["distance"] = m.group(1).strip() if m else None
    d["facilities"] = [t for t in texts if re.match(
        r"^(Restrooms|Pets|Strollers|Wheelchairs):\s*\S", t)]
    d["awards"] = next((re.sub(r"^Awards:\s*", "", t) for t in texts if t.startswith("Awards:")), None)
    sponsor = page.find("evnt-sponsor")
    d["club"] = d["website"] = None
    if sponsor >= 0:
        sp = _paragraphs(page[sponsor:sponsor + 3000])
        if sp:
            d["club"] = re.sub(r"\s*\(AVA-\d+\)\s*$", "", sp[0][1]).strip() or None
        w = re.search(r"""Event website:.*?href=['"]([^'"]+)['"]""", page[sponsor:sponsor + 3000], re.S)
        if w and re.match(r"https?://", w.group(1)):
            d["website"] = w.group(1)
    return d


# --------------------------------------------------------------------------- #
# The clock (rule 2)

# A NUMBER IS A CLOCK ONLY WHEN NOTHING SAYS IT IS SOMETHING ELSE. Measured over
# the 122 windows of 2026-10-11: "10K will start at 9:00am" read the 10 of 10K
# as 10 o'clock, "Saturday 42 km start 7-9 am" stopped at 42, "5 & 10 km -
# registration opens at 10 am" made 5 into 5 pm, "Daylight hours, 7 days per
# week" made 7 am, and "Oct. 17: Start Table 9:00 AM" made 17 into 5 am. So a
# distance, a count of days or hours, an ordinal and a month's day are not
# clocks, and the first number that survives is.
_NOT_A_CLOCK = (r"(?!\d)(?!\s*(?:k\b|km|kms\b|kilomet|mi\b|miles?\b|days?\b|weeks?\b|hours?\b|hrs?\b|"
                r"min|\+|%|th\b|st\b|nd\b|rd\b|"
                r"(?:&|and|or|,|/|-)\s*\d+(?:\.\d+)?\s*(?:k|km)\b))")
_MARKER = r"(a\.?m\.?|p\.?m\.?|a\b|p\b|noon)"
_CLOCK_RX = re.compile(r"(?<![\d$.])\b(\d{1,2})" + _NOT_A_CLOCK + r"(?::(\d{2}))?\s*" + _MARKER + "?", re.I)
_MONTH_DAY_RX = re.compile(
    r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?\b:?", re.I)
# The END of the walk, when the club states one. "Closes" is deliberately not
# here: "9am-11am; closes at 11am" is the start table closing, not the walk.
_FINISH_RX = re.compile(
    r"(?:finish(?:ed)?(?:\s+(?:the\s+)?walk)?|off\s+(?:the\s+)?trails?|done|back|ends?|end\s+by|"
    r"(?:be\s+)?completed?)\s*(?:by|at|:|no\s+later\s+than|nlt)?\s*"
    r"(?:(\d{1,2})" + _NOT_A_CLOCK + r"(?::(\d{2}))?\s*" + _MARKER + r"?|(noon))", re.I)


def _hour24(hour: int, minute: int, marker: Optional[str]) -> Optional[Tuple[int, int]]:
    if not 0 <= minute <= 59:
        return None
    m = (marker or "").lower().replace(".", "")
    if m == "noon":
        return (12, 0)
    if m.startswith(("a", "p")):
        if not 1 <= hour <= 12:
            return None              # "17 am" is a day of the month, not a time
        return (hour % 12 + (12 if m.startswith("p") else 0), minute)
    if 13 <= hour <= 23:
        return (hour, minute)        # a 24-hour clock
    if 1 <= hour <= 6:
        return (hour + 12, minute)   # nobody opens a start table at 3:30 in the morning
    if 7 <= hour <= 12:
        return (hour, minute)
    return None


def parse_window(text: Optional[str]) -> Tuple[Optional[Tuple[int, int]], Optional[Tuple[int, int]]]:
    """(opens, finish-by) as (hour, minute) pairs, either None when unstated."""
    if not text:
        return None, None
    t = _MONTH_DAY_RX.sub(" ", text.replace("\u2013", "-").replace("\u2014", "-"))
    finish = None
    f = _FINISH_RX.search(t)
    if f:
        finish = (12, 0) if f.group(4) else _hour24(int(f.group(1)), int(f.group(2) or 0), f.group(3))
        t_open = t[:f.start()]
    else:
        t_open = t
    if re.match(r"(?i)\s*noon\b", t_open):
        return (12, 0), finish
    opens = None
    for m in _CLOCK_RX.finditer(t_open):
        marker = m.group(3)
        if not marker:
            # "8-11 am", "9:45 - 10:00 am": the window's own marker covers its
            # start when the start is not later than the end.
            rest = _CLOCK_RX.search(t_open, m.end())
            if rest and rest.group(3) and rest.group(3).lower() != "noon":
                if int(m.group(1)) % 12 <= int(rest.group(1)) % 12:
                    marker = rest.group(3)
        opens = _hour24(int(m.group(1)), int(m.group(2) or 0), marker)
        if opens:
            break
    if opens and finish and finish <= opens:
        finish = None                # a finish before the start is a misreading
    return opens, finish


# --------------------------------------------------------------------------- #
# One row

def _kind_key(list_type: str, detail_kind: Optional[str]) -> str:
    for hay in (list_type or "", detail_kind or ""):
        m = re.search(r"\b(walk|bike|swim|ski|snowshoe)\b", hay, re.I)
        if m:
            return m.group(1).lower()
    return "walk"


def _cap(s: Optional[str], n: int) -> Optional[str]:
    if not s:
        return None
    s = s.strip()
    if len(s) <= n:
        return s
    cut = s[:n].rsplit(" ", 1)[0].rstrip(",;:")
    return cut + "..."


def to_event(row: Dict[str, Any], det: Dict[str, Any], today: date,
             within_days: int) -> Tuple[Optional[NormalizedEvent], str]:
    """(event, "") or (None, why-not). The detail page's facts win over the
    list's, which has no place and no clock."""
    first = det.get("first") or row.get("first")
    if not first:
        return None, "no date"
    days = det.get("days") or ((row["last"] - row["first"]).days + 1 if row.get("last") else 1)
    last = first + timedelta(days=max(1, days) - 1)
    if last < today:
        return None, "past"
    if first > today + timedelta(days=within_days):
        return None, "beyond the window"
    if det.get("lat") is None:
        return None, "no map pin"
    key = _kind_key(row.get("type", ""), det.get("kind"))
    noun, extras = KINDS.get(key, KINDS["walk"])
    title = (det.get("title") or row.get("name") or "").strip()
    if not title:
        title = f"{row.get('club') or 'AVA club'} {noun}"
    name = title if _ACTIVITY_RX.search(title) else f"{title} ({noun})"
    body = " ".join(x for x in (det.get("comments"), det.get("information"), row.get("blurb")) if x)
    if looks_online_only(name, body):
        return None, "online"

    opens, finish = parse_window(det.get("window"))
    if days > 1 or not opens:
        start_local, end_local = first.isoformat(), (last.isoformat() if days > 1 else None)
    else:
        start_local = f"{first.isoformat()}T{opens[0]:02d}:{opens[1]:02d}:00"
        end_local = f"{first.isoformat()}T{finish[0]:02d}:{finish[1]:02d}:00" if finish else None

    region = row.get("region") or None
    city, cell_venue = clean_city(row.get("city"), region, det.get("place"))
    venue, street = split_place(det.get("place"), city, region)
    venue = venue or cell_venue
    club = det.get("club") or row.get("club") or None
    url = DETAIL_URL.format(sn=row["sn"])

    lines: List[str] = []
    fee, free = walk_price(" ".join(x for x in (det.get("comments"), det.get("information"),
                                                row.get("blurb"), det.get("window")) if x))
    if free:
        lines.append("Free to walk.")
    elif fee:
        lines.append(f"Walker fee {fee}, as the club lists it.")
    facts = [f"{det.get('kind') or noun.capitalize()} by {club}" if club else (det.get("kind") or noun.capitalize())]
    if det.get("distance"):
        facts.append(f"distance {det['distance']}")
    rating = det.get("rating") or row.get("rating")
    if rating:
        facts.append(f"AVA trail rating {rating}")
    lines.append(", ".join(facts) + ".")
    if det.get("window"):
        if days > 1:
            lines.append(f"{days} days, start table open: {det['window'].rstrip('.')}.")
        else:
            lines.append(f"Start table open: {det['window'].rstrip('.')}.")
    if det.get("place") and (venue or street):
        lines.append(f"Start: {_DIRECTIONS_RX.sub('', det['place']).strip().rstrip('.')}.")
    if det.get("facilities"):
        lines.append(" ".join(f.rstrip(".") + "." for f in det["facilities"]))
    # The sync keeps 800 characters (DESCRIPTION_MAX) plus a short last line, so
    # the facts above go first, then how to find the start, then the club's own
    # words in whatever room is left.
    if det.get("driving"):
        lines.append("Getting there: " + _cap(det["driving"], 260))
    closing = "Listed by the American Volkssport Association (my.ava.org); no membership needed."
    room = DESCRIPTION_ROOM - sum(len(x) + 2 for x in lines) - len(closing)
    prose = det.get("comments") or row.get("blurb") or det.get("information")
    if prose and room >= 80:
        lines.append(_cap(prose, room))
    lines.append(closing)

    nev = NormalizedEvent(
        source=SOURCE,
        source_id=str(row["sn"]),
        name=name,
        description="\n\n".join(lines),
        start_local=start_local,
        end_local=end_local,
        venue_name=venue,
        address=street,
        latitude=det["lat"], longitude=det["lon"],
        city=city,
        region=region,
        country="US",
        category="community",
        categories=norm_categories("community", extras),
        promoter=club,
        ticket_url=url,
        coords_exact=True,
    )
    nev.fingerprint = make_fingerprint(nev.name, first.isoformat(), venue or street, nev.city)
    return nev, ""


# --------------------------------------------------------------------------- #
# HTTP

class Refused(Exception):
    """The host said no: 401/403/429, a bot challenge or robots.txt."""


class Stop(Exception):
    """The run's deadline passed; no request starts after it."""


class Client:
    """One host, paced, robots-checked and deadline-aware. get() returns the body
    of a 200, None for any other answer worth skipping (a network error costs
    one page, not the run), and raises Refused or Stop."""

    def __init__(self, session, pause: float, deadline: Optional[float], robots=None,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.session, self.pause, self.deadline = session, pause, deadline
        self.robots, self.clock, self.sleep = robots, clock, sleep
        self.requests = 0
        self._last: Optional[float] = None

    def get(self, url: str) -> Optional[str]:
        from robots_txt import CHALLENGE_RX
        if self.deadline is not None and self.clock() >= self.deadline:
            raise Stop("run deadline reached")
        if self.robots is not None:
            v = self.robots.check(url)
            if v.get("allowed") is not True:
                raise Refused(f"robots.txt ({v.get('status')}): {v.get('rule')} for {url}")
            if v.get("crawl_delay") and v["crawl_delay"] > self.pause:
                self.pause = float(v["crawl_delay"])
        if self._last is not None:
            wait = self.pause - (self.clock() - self._last)
            if wait > 0:
                self.sleep(wait)
        self._last = self.clock()
        self.requests += 1
        try:
            r = self.session.get(url, timeout=40, allow_redirects=True)
        except OSError as exc:            # requests.RequestException is an OSError
            print(f"[{SOURCE}] network error, page skipped: {type(exc).__name__} - {url}", flush=True)
            return None
        if r.status_code in (401, 403, 429):
            raise Refused(f"HTTP {r.status_code} for {url}")
        if urlsplit(str(getattr(r, "url", url))).netloc not in ("", urlsplit(url).netloc):
            print(f"[{SOURCE}] redirected off the site, not read: {url} -> {r.url}", flush=True)
            return None
        text = r.text or ""
        if CHALLENGE_RX.search(text[:6000]):
            raise Refused(f"bot challenge for {url}")
        if r.status_code != 200:
            print(f"[{SOURCE}] HTTP {r.status_code}: {url}", flush=True)
            return None
        return text


# --------------------------------------------------------------------------- #

def ingest(cfg: Dict[str, Any], client: Client, today: date,
           emit: Callable[[NormalizedEvent], None],
           cancel: Callable[[NormalizedEvent, str], None]) -> Dict[str, Any]:
    """Read the list, then each in-window event's page. Returns counts."""
    within = int(cfg.get("within_days", DEFAULT_WITHIN_DAYS))
    rep: Dict[str, Any] = {"listed": 0, "in_window": 0, "kept": 0, "cancelled": 0,
                           "skipped": {}, "kinds": {}, "stopped": None}
    page = client.get(cfg.get("list_url") or LIST_URL)
    if page is None:
        rep["stopped"] = "the list page was not read"
        return rep
    rows = parse_list(page)
    rep["listed"] = len(rows)
    horizon = today + timedelta(days=within)
    seen = set()
    for row in rows:
        if row["sn"] in seen:
            continue
        seen.add(row["sn"])
        if row["last"] < today or row["first"] > horizon:
            continue
        rep["in_window"] += 1
        try:
            body = client.get((cfg.get("detail_url") or DETAIL_URL).format(sn=row["sn"]))
        except Stop as exc:
            rep["stopped"] = f"{exc} after {rep['in_window'] - 1} event page(s)"
            break
        if body is None:
            rep["skipped"]["page not read"] = rep["skipped"].get("page not read", 0) + 1
            continue
        det = parse_detail(body)
        ev, why = to_event(row, det, today, within)
        if ev is None:
            rep["skipped"][why] = rep["skipped"].get(why, 0) + 1
            continue
        if _CANCELLED_RX.search(det.get("status") or "") or _CANCELLED_RX.search(det.get("title") or ""):
            cancel(ev, "the AVA calendar marks it cancelled")
            rep["cancelled"] += 1
            continue
        emit(ev)
        rep["kept"] += 1
        k = _kind_key(row.get("type", ""), det.get("kind"))
        rep["kinds"][k] = rep["kinds"].get(k, 0) + 1
    return rep


def _today() -> date:
    fixed = os.environ.get("MAPSEE_TODAY")
    if fixed:
        return datetime.strptime(fixed, "%Y%m%d").date()
    return date.today()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import American Volkssport Association traditional events.")
    ap.add_argument("--config", default="ava_sources.json")
    ap.add_argument("--store", default="feeds_events.json")
    ap.add_argument("--max-minutes", type=float, default=DEFAULT_MAX_MINUTES,
                    help="whole-run deadline; no request starts after it (0 = none)")
    ap.add_argument("--force", action="store_true", help="run regardless of the config's run_weekdays")
    ap.add_argument("--dry-run", action="store_true", help="read and report, write nothing")
    a = ap.parse_args(argv)

    cfg = json.loads(open(a.config, encoding="utf-8").read())
    today = _today()
    wd = cfg.get("run_weekdays")
    if wd and not a.force and today.weekday() not in wd:
        print(f"[{SOURCE}] skipped (runs on weekdays {wd}; today is {today.weekday()})")
        return 0
    try:
        import requests
    except ImportError:  # pragma: no cover
        sys.exit("This script needs 'requests'.  Install it with:  pip install requests")
    from robots_txt import Robots

    session = requests.Session()
    # Only the User-Agent: robots.txt is read with this session, and a
    # session-wide Accept header can change its answer (see
    # mapsee_ingest_rallybound, where it turned a rule file into a 406).
    session.headers.update({"User-Agent": USER_AGENT})
    started = time.monotonic()
    deadline = started + a.max_minutes * 60 if a.max_minutes else None
    client = Client(session, float(cfg.get("pause", 1.0)), deadline, robots=Robots(session))
    store = None if a.dry_run else EventStore(a.store)
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
            store.cancel(ev, reason)

    try:
        rep = ingest(cfg, client, today, emit, cancel)
    except Refused as exc:
        print(f"[{SOURCE}] REFUSED - {exc}; stopping (a refusal is not retried)", flush=True)
        rep = None
    finally:
        if store is not None:
            store.save()
    took = (time.monotonic() - started) / 60
    if rep is not None:
        skipped = ", ".join(f"{k} {v}" for k, v in sorted(rep["skipped"].items())) or "none"
        kinds = ", ".join(f"{k} {v}" for k, v in sorted(rep["kinds"].items())) or "none"
        print(f"[{SOURCE}] listed {rep['listed']}, {rep['in_window']} in the "
              f"{cfg.get('within_days', DEFAULT_WITHIN_DAYS)}-day window, kept {rep['kept']} "
              f"({kinds}), cancelled {rep['cancelled']}; skipped: {skipped}; "
              f"{client.requests} request(s) in {took:.1f} min", flush=True)
        if rep.get("stopped"):
            print(f"[{SOURCE}] STOPPED - {rep['stopped']}", flush=True)
    if store is not None:
        print(f"[{SOURCE}] store now holds {len(store.records)} unique events.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
