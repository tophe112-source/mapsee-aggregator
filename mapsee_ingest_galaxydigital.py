#!/usr/bin/env python3
"""
mapsee_ingest_galaxydigital.py - import the dated volunteer shifts that city
and county park departments post on Galaxy Digital "Get Connected" portals
into the Mapsee store.

    python mapsee_ingest_galaxydigital.py --config galaxydigital_sources.json \
        --store feeds_events.json [--max-minutes 8] [--only <slug>] [--dry-run]

WHAT THE SOURCE IS
------------------
Galaxy Digital (Get Connected) is the volunteer-management platform behind
hundreds of US portals: a city's parks department, a United Way, a county's
volunteer centre, a university's community-service office. Every portal has
the same template on its own hostname (volunteer.portland.gov,
volunteer.olympiawa.gov, ...): /need/ lists the "needs" (opportunities),
/need/detail/?need_id=N is one need, with its description, its Location
(name, street, city and ZIP in a table), and either a Shifts table (one row
per dated shift: begins, ends, open spots) or a single "Happens On" date with
a free-text clock. The sign-up ("Respond", a free account) is on that page,
so the page is the row's link. Nothing in the catalog carried these: Rose
City Bluff Restoration meets every Sunday at 9:30 (13 dated shifts inside
90 days on 2026-10-25), Olympia's park stewards every Friday and Saturday.

WHY THIS PLATFORM (the volunteer door's three candidates, gated 2026-10-25)
------------------------------------------------------------------------
Humanitix is refused by its Website Terms of Use, 2(d): you will not "Use any
software, automated device, robot, spider, scraper, or other process or means
to access, retrieve, scrape or index the Websites or any content on the
Websites without our express written consent". Better Impact passes
(robots.txt and a SaaS agreement with its customer), but its public activity
pages give the meeting place in prose and sign up through "Fill in an
application". Galaxy gives a Location table, a Shifts table with open spots
and a per-shift sign-up, the same on every portal.

CONDUCT (checked 2026-10-25 with our own User-Agent)
---------------------------------------------------
robots.txt is the same 1,151-byte file on all four portals read (and on
api2.galaxydigital.com): Disallow: / for 27 named SEO crawlers, Crawl-delay 10
for bingbot, and "User-agent: * / Disallow:" - everything allowed, no
Crawl-delay - for everyone else. Galaxy's Terms and Conditions
(galaxydigital.com/terms-and-conditions) are its licence with the CUSTOMER
("as long as You meet any applicable payment obligations", "Your Principal
Organization"); they forbid the customer to "Reproduce, modify, copy,
deconstruct, sell, trade, or resell the Services" (the software) and say
nothing of reading the public pages. Each portal's "Terms and Conditions"
link is its privacy policy (/privacy), about volunteers' data only. The
operators' own sites: portland.gov's "Terms and policies" are ADA, captioning
and privacy; Olympia's notice says its information "is considered public
information and may be distributed or copied" and that lists of individuals
"may not be used for commercial purposes"; Mecklenburg's restricts only the
County Seal. No terms forbid automated reading or republishing, so the public
pages are read at one request per 1.1 s per host (robots.txt included),
robots.txt asked before the first, and a 401/403/429 or a bot challenge ends
that portal's read. Galaxy's API (api2.galaxydigital.com) is keyed per
customer; the pages carry no iCal, RSS or Event JSON-LD, so this reads HTML.
A portal whose operator's terms cannot be read is not configured (WWU's,
galaxydigital_sources.json _not_included).
NO PERSON IS COPIED: the "Site Supervisor" block (a named member of staff,
an e-mail address, sometimes a phone number) and the Details rows ("working
with park horticulturist <name>") are never copied, and a description
sentence that gives an e-mail address, a phone number or a named contact is
dropped, with a "Questions?" in front of it: 72 sentences on 59 of the 80
pages read on 2026-10-25 ("please request one by email to
<name>@portlandoregon.gov", "please reach out to <name> by phone or email").

WHAT IS READ
------------
The listing, page by page (/need/, /need/index/12, ... 12 needs a page, two
layouts: a table on Portland and a card grid on the others; both carry the
need id and its date text). The date text decides whether the page is worth
a request: "Is Ongoing", "Flexible", "Runs Until <d>" and "<d> through <d>"
are standing roles, refused unread (measured: 0 of the 7 "Flexible" pages
read - Olympia 3, Mecklenburg 4 - and 0 of WWU's 5 ranged ones had a dated
shift), and a "Happens On <d>" outside the window is skipped unread. Every
other need's page is read on every run - shifts fill, and a full one is not
written again - which on 2026-10-25 was 48 + 13 + 19 need pages and 5 + 2 + 2
listing pages: 92 requests with robots.txt, 1.7 minutes. No cache: the pages
are the open spots, and they change between runs.

REFUSED, AND COUNTED
--------------------
A need:
  * that requires a background check (12 of Portland's 48 pages: SUN
    Community Schools clubs and a school food pantry, the same weekly slot for
    months) - a vetted role, not a shift anybody can join;
  * of a programme in the portal's `skip_programs` (Portland's SUN Community
    Schools: the 13th club, which asks no check but a club leader);
  * with no Location section (7 of Portland's: "Find Your Flare", trail days
    that say where to meet only in prose) or a virtual one;
  * whose meeting place is not a street address and has no pin in the config:
    an intersection ("N Lombard St and N Bruce Ave"), a trail ("Holman ln") or
    an address range ("6001-6399 SE Lincoln St"). The only geocoder here is US
    Census, which answers neither, and Photon answered "NE Sacramento St and
    NE 66th Ave, Portland" with a restaurant on NE 49th. So the config's
    `location_overrides` pin those by their exact Location text (8 on
    Portland's portal; see PLACEMENT); anything else is refused, named and
    counted (2 on 2026-10-25);
  * whose place is outside its portal's state, or whose ZIP is outside its
    `zip3` list, or whose override pin is more than `max_km` from `center`
    (0 of 2026-10-25's needs; they catch a portal listing another city's).
A shift:
  * that is full ("0 of 5" open spots, or "0 open spots" on a one-off; 5);
  * that starts at midnight: 6 of the 723 shift rows read (4 inside the
    window) read "12:00am to 3:00am", with registration closing at midnight
    the day before, on stewardship days whose prose names no time - a
    placeholder, not a clock;
  * whose begins text or one-off clock cannot be read, or whose weekday
    disagrees with its date (0 seen): nothing is re-dated.
Every refused shift inside the window is `mark_seen`, so the rec sync's
absence check never reads "full" or "unplaceable" as "gone".

PLACEMENT
---------
A street address (a house number, then a street; ", City, ST ZIP" cut off the
end when an organiser repeats it) is handed to the sync's US Census batch with
its city, state and ZIP: no coordinates are made up here. On 2026-10-25 Census
matched 39 of the 40 addresses; the 40th ("2600 East Bay Dr. NE, Olympia",
Squaxin Park) answered no match and the sync drops its one row. A location
with a pin in `location_overrides` (keyed on the Location lines joined with
", ", case and spacing ignored) is written with that pin and coords_exact:
34 of the 98 rows. Each override records how it was measured: a Census answer
for the grid address of the corner ("6600 NE Sacramento St" is NE 66th Ave on
Portland's grid) checked against the park's OpenStreetMap extent, or the
park's own OSM point when the Location's first line names the park.

TIMES AND IDENTITY
------------------
"Tue Oct 27, 2026 @ 9:00am to 11:00am" (an end on another day is read too),
or "Happens On Oct 30, 2026" with "10 a.m. to 1 p.m." / "4- 7pm" / "9:30am to
12pm" / "9:00am - 12:15pm, 11:45am - 3:00pm or 9am - 3pm" (several windows:
the earliest start to the latest end). Wall clock in the portal's `timezone`.
A one-off with no clock row is a date-only row. Two shift rows with the same
begins and ends are one occasion (Portland's Yard Tree Giveaway: "5 of 12" and
"0 of 1" at the same 8:00-12:00) - open if either is. source_id is
"<slug>:<need id>:<day>:<HHMM>"; the fingerprint is
make_fingerprint("<title> HH:MM", day, venue), the recipe EventStore's notice
rule can prove, so "CANCELLED: <title>" tombstones the shift it names. Two
needs posted for one occasion share a fingerprint and the store merges them.

CATEGORIES
----------
volunteer, with outdoors for "Is Outdoors" and kids for "Is Family Friendly"
- but only when the need's minimum age is 12 or under: 2 of Portland's 30
family-friendly needs say "14 and older" (crews for children's festivals),
and their rows neither carry kids nor say "Family friendly." (the sync's own
kids rule reads that line).

CANCELLATIONS
-------------
Only on the source's own evidence: a cancelled title (EventStore's notice
rule), or absence from a COMPLETE read - every listing page read to the last,
every page that had to be read read - which `mark_complete` records per portal
(id_prefix "<slug>:") for mapsee_supabase_sync --retire-absent and its 10%
breaker. Prose ("events will be cancelled due to inclement weather") cancels
nothing.

MEASURED 2026-10-25 (live, scratch store): two runs nine minutes apart each
made 92 requests in 1.7 minutes, read all three portals COMPLETE and kept the
same 98 fingerprints: Portland 64 (34 pinned from the config, 30 left to the
Census), Olympia 13, Mecklenburg 21. Refused: 11 standing roles unread; of
the 80 needs read, 12 for a background check, 7 with no Location, 2 at a
place with no pin and 1 skipped programme; and 9 shifts (5 full, 4 midnight).
The sync's build_rows wrote 97 (34 exact, 63 placed by the Census, 1 not);
derive_categories: 98 volunteer, 64 with outdoors and kids, 28 outdoors, 6
neither.

HTTP: our own User-Agent, robots.txt checked per host before the first
request, one request per `pause` seconds (Crawl-delay honoured if larger), a
redirect followed only on the same host, and a 401/403/429 or a bot challenge
ends that portal's read - a refusal. A network error or a 5xx costs one page;
five failures in a row end the portal's read. No request starts after
--max-minutes, the pause before it included.
"""
from __future__ import annotations

import argparse
import html as html_mod
import json
import math
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlsplit

from mapsee_ingest import NormalizedEvent, EventStore, make_fingerprint

SOURCE = "galaxydigital"
USER_AGENT = "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)"
DEFAULT_MAX_MINUTES = 8.0
DEFAULT_WITHIN_DAYS = 90
SAVE_EVERY = 40           # rows between store saves, so a cancelled step keeps its work
DESCRIPTION_MAX = 800     # mapsee_supabase_sync's cap; our lines lead so they survive it
KIDS_MAX_MIN_AGE = 12     # a family-friendly need with a higher minimum age is not for children

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
_DOW = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

_TAG = re.compile(r"<[^>]+>")
_NEED_ID = re.compile(r"need/detail/\?need_id=(\d+)")

# Listing markup (read 2026-10-25). The table layout (Portland) and the card
# grid (Olympia, Mecklenburg, WWU) carry the same three facts per need.
_LIST_START = re.compile(r'<tr class="need[ "]|<div class="need[ "]')
_LIST_DATE = (re.compile(r'(?s)<td class="date-time">(.*?)</td>'),
              re.compile(r'(?s)<div class="date">(.*?)</div>'))
_LIST_TITLE = re.compile(r'(?s)class="title[^"]*"[^>]*>(.*?)</(?:a|div)>')
_NEXT = re.compile(r"""<li class="next"[^>]*>\s*<a\s+href=['"]([^'"]+)['"]""")
_PAGER = re.compile(r'<nav class="pagination-wrap">')

# A listing date text that names no dated shift. Measured 2026-10-25: 44 of
# WWU's first 60 needs say "Is Ongoing"; 0 of the 7 "Flexible" pages read
# (Olympia 3, Mecklenburg 4) and 0 of WWU's 5 ranged ones ("Runs Until Feb 28,
# 2027", "Nov 9, 2026 through Nov 10, 2026") had a dated shift.
_STANDING = re.compile(r"^\s*(?:is\s+ongoing|flexible|runs\s+until\b|.*\bthrough\b)", re.I)
_HAPPENS = re.compile(r"Happens\s+On\s+([A-Z][a-z]{2,8})\.?\s+(\d{1,2}),\s+(\d{4})")
_MULTIPLE = re.compile(r"multiple\s+shifts", re.I)

# Detail markup: ONLY these blocks are read. The "Site Supervisor" section
# (need_contact: a person's name, e-mail, phone) is never touched.
_H1 = re.compile(r'(?s)<h1 class="panel-title[^"]*">(.*?)</h1>')
_DESC = re.compile(r'(?s)<div class="section-content jodit-content">(.*?)</div>\s*</div>\s*</section>')
_REQS = re.compile(r'(?s)<section class="requirements">(.*?)</section>')
_REQ_CELL = re.compile(r'(?s)<td(?: class="text")?>(.*?)</td>')
_LOCATION = re.compile(r'(?s)<section class="location">(.*?)</section>')
_ADDRESS = re.compile(r'(?s)<tr class="address">.*?<td class="text">(.*?)</td>')
_ADDRESS_TIME = re.compile(r'(?s)<section class="address-time[^"]*">(.*?)</section>')
_TIME_CELL = re.compile(r'(?s)icon-time.*?<td class="text">(.*?)</td>')
_SPOTS_ONCE = re.compile(r'(?s)<span class="spots-remaining__count">(.*?)</span>')
_SHIFTS = re.compile(r'(?s)<table id="shifts-table"[^>]*>(.*?)</table>')
_TH = re.compile(r'(?s)<th[^>]*>(.*?)</th>')
_TD = re.compile(r'(?s)<td[^>]*>(.*?)</td>')
# "Program" on a city's portal (a programme of the department that runs it:
# Portland's "Nature Patches", "SUN Community Schools"), "Agency" on a
# volunteer centre's (the organisation that posted the need).
_AGENCY = re.compile(r'(?s)<h2 class="section-header">\s*(Program|Agency)\s*</h2>.*?<div class="title">(.*?)</div>')

# "Tue Oct 27, 2026 @ 9:00am to 11:00am", or an end on another day:
# "Fri Dec 4, 2026 @ 9:00pm to Sat Dec 5, 2026 @ 1:00am".
_STAMP = r"([A-Z][a-z]{2})\s+([A-Z][a-z]{2})\s+(\d{1,2}),\s+(\d{4})\s+@\s+(\d{1,2}):(\d{2})\s*([ap]m)"
_BEGINS = re.compile(rf"^\s*{_STAMP}\s+to\s+(?:([A-Z][a-z]{{2}})\s+([A-Z][a-z]{{2}})\s+(\d{{1,2}}),\s+(\d{{4}})\s+@\s+)?"
                     r"(\d{1,2}):(\d{2})\s*([ap]m)\s*$", re.I)
_SPOTS = re.compile(r"(\d+)\s+of\s+(\d+)")
# A free-text clock window: "10 a.m. to 1 p.m.", "4- 7pm", "9:30am to 12pm".
_WINDOW = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*-\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?")

_STREET = re.compile(r"^\d+[A-Za-z]?\s+\S")
_JOINED = re.compile(r"\s(?:&|and|at)\s|/", re.I)   # two streets: an intersection, not an address
_CITY_ST = re.compile(r"^([^,]+),\s*([A-Z]{2})\.?$")
_ZIP = re.compile(r"^(\d{5})(?:-\d{4})?$")
_TAIL = re.compile(r",\s*[^,]+,\s*[A-Z]{2}\.?(?:\s+\d{5}(?:-\d{4})?)?\s*$")
_VIRTUAL = re.compile(r"\b(?:virtual|online|remote(?:ly)?|from\s+home|zoom)\b", re.I)
_BACKGROUND = re.compile(r"background\s+check", re.I)
_MIN_AGE = re.compile(r"^(\d{1,2})\s+and\s+older$", re.I)

# People. A sentence that gives an e-mail address or a phone number, or names
# somebody to contact, is not the row's to carry. A phone is 10-11 digits with
# spaces, dots, dashes or brackets ("503-823-5121", "(503) 278-9764"); a date
# ("2026-11-07", 8 digits) or a clock ("9:00am - 12:00pm") is not.
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"(?<![\w/])\(?\+?\d[\d\s().\-]{7,}\d(?![\w/])")
_CONTACT_LABEL = re.compile(r"^\W*(?:contact(?:\s+person)?|coordinator|site\s+supervisor|questions)\s*:\s*\S", re.I)
# A verb and a capitalised name, then "at/on/via/by", a bracket or the end:
# "please reach out to <First> <Last> by phone or email" (a SUN club page) has
# neither an address nor a number, and is a person all the same.
_NAMED_CONTACT = re.compile(
    r"\b(?:contact|e-?mail|call|text|ask\s+for|reach\s+out\s+to)\s+[A-Z][\w'.-]*(?:\s+[A-Z][\w'.-]*){0,2}"
    r"\s*(?:\b(?:at|on|via|by)\b|\(|[.!]?\s*$)")
_LEAD_IN = re.compile(r"^\W*(?:\w+\s+){0,2}\w+\s*[?:]\s*$")
# A lead-in on a line of its own goes with the contact under it only when it
# is about contacting: 28 lone "Meeting Location:", "Parking:" and "Project
# Details:" headings on the 2026-10-25 pages stay whatever follows them.
_CONTACT_LEAD = re.compile(r"^\W*(?:questions|contact(?:\s+us)?|(?:for\s+)?more\s+info(?:rmation)?)\s*[?:.!]?\s*$", re.I)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(“‘])")


# --------------------------------------------------------------------------- #
# Small helpers

def _clean(s: Optional[str]) -> Optional[str]:
    if s is None:
        return None
    s = html_mod.unescape(_TAG.sub(" ", s)).replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip() or None


def _lines(s: str) -> List[str]:
    """An HTML cell -> its non-empty lines (<br> and newlines both break)."""
    s = re.sub(r"(?i)<br\s*/?>", "\n", s or "")
    out = []
    for part in s.split("\n"):
        c = _clean(part)
        if c:
            out.append(c)
    return out


def _paragraphs(s: str) -> str:
    """HTML -> text with one line per paragraph or list item; headings dropped
    (they are the organisers' "Site Information and Accessibility"). A newline
    in the SOURCE is a space: Olympia's editor wraps "please call\\n360-..."
    inside one <p>, and splitting there left "please call" behind once the
    number's line went. <style> and <script> inside a description are dropped
    (2 of the 2026-10-25 pages pasted a style sheet into theirs)."""
    s = re.sub(r"(?is)<(style|script)\b[^>]*>.*?</\1\s*>", " ", s or "")
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"(?is)<h[1-6][^>]*>.*?</h[1-6]>", "\n", s)
    s = re.sub(r"(?is)<br\s*/?>|</p>|</li>|</ul>|</ol>|</div>", "\n", s)
    s = re.sub(r"(?is)<li[^>]*>", "- ", s)
    s = html_mod.unescape(_TAG.sub(" ", s)).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in s.splitlines()]
    return "\n".join(ln for ln in lines if ln and ln not in ("-",))


def _trim(s: str, n: int) -> str:
    if n <= 0:
        return ""
    if len(s) <= n:
        return s
    cut = s[:n].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return cut + "…"


def _digits(s: str) -> int:
    return sum(c.isdigit() for c in s)


def gives_contact(sentence: str) -> bool:
    """Does this sentence hand out a person's (or anyone's) direct contact?"""
    if _EMAIL.search(sentence) or _CONTACT_LABEL.search(sentence) or _NAMED_CONTACT.search(sentence):
        return True
    return any(10 <= _digits(m.group(0)) <= 11 for m in _PHONE.finditer(sentence))


def without_contacts(text: str) -> Tuple[str, int]:
    """(text without any sentence that gives contact details, how many went)."""
    kept_lines: List[str] = []
    gone = 0
    for line in (text or "").split("\n"):
        kept = []
        for s in _SENTENCE_END.split(line.strip()):
            if not s:
                continue
            if gives_contact(s):
                gone += 1
                if kept and _LEAD_IN.match(kept[-1]):      # "Questions?" left in front of it
                    kept.pop()
                elif not kept and kept_lines and _CONTACT_LEAD.match(kept_lines[-1]):
                    kept_lines.pop()                        # ... or as a paragraph of its own
            else:
                kept.append(s)
        if kept:
            kept_lines.append(" ".join(kept).strip())
    return "\n".join(kept_lines), gone


def _km(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(min(1.0, h)))


def location_key(lines: List[str]) -> str:
    """The key a `location_overrides` entry names: the Location lines joined
    with ", ", case and spacing ignored."""
    return re.sub(r"\s+", " ", ", ".join(lines)).strip().lower()


# --------------------------------------------------------------------------- #
# Dates and clocks

def _month(name: str) -> Optional[int]:
    return _MONTHS.get((name or "").lower()[:3])


def _h24(h: int, m: int, mer: str) -> Optional[Tuple[int, int]]:
    if not (1 <= h <= 12 and 0 <= m < 60):
        return None
    h = h % 12 + (12 if mer.lower() == "pm" else 0)
    return h, m


def parse_listing_date(text: Optional[str]) -> Tuple[str, Optional[date]]:
    """A listing card's date text -> (kind, date). Kinds: "once" (Happens On),
    "shifts" (Multiple Shifts Available), "standing" (ongoing, flexible or a
    range: refused unread) and "unknown" (read the page to find out)."""
    t = _clean(text) or ""
    m = _HAPPENS.search(t)
    if m:
        mon = _month(m.group(1))
        try:
            return "once", date(int(m.group(3)), mon, int(m.group(2))) if mon else None
        except ValueError:
            return "once", None
    if _MULTIPLE.search(t):
        return "shifts", None
    if _STANDING.match(t):
        return "standing", None
    return "unknown", None


def parse_begins(text: Optional[str]):
    """("Tue Oct 27, 2026 @ 9:00am to 11:00am") -> (day, (h, m), end_day, (h, m)),
    or None. The weekday must agree with the date, and the end must come after
    the start and within a day of it."""
    m = _BEGINS.match(_clean(text) or "")
    if not m:
        return None
    g = m.groups()
    dow, mon, dd, yy, sh, sm, smer = g[0:7]
    edow, emon, edd, eyy, eh, em, emer = g[7:14]
    try:
        day = date(int(yy), _month(mon) or 0, int(dd))
    except ValueError:
        return None
    if _DOW[day.weekday()] != dow.lower()[:3]:
        return None
    end_day = day
    if emon:
        try:
            end_day = date(int(eyy), _month(emon) or 0, int(edd))
        except ValueError:
            return None
        if _DOW[end_day.weekday()] != edow.lower()[:3]:
            return None
    start, end = _h24(int(sh), int(sm), smer), _h24(int(eh), int(em), emer)
    if start is None or end is None:
        return None
    a = datetime(day.year, day.month, day.day, *start)
    b = datetime(end_day.year, end_day.month, end_day.day, *end)
    if not (a < b <= a + timedelta(hours=24)):
        return None
    return day, start, end_day, end


def parse_clock_text(text: Optional[str]) -> Optional[Tuple[Tuple[int, int], Tuple[int, int]]]:
    """A one-off's free-text clock -> ((h, m), (h, m)), or None. US wording:
    "10 a.m. to 1 p.m.", "4- 7pm" (the start takes the end's meridiem unless
    that would put it after the end: "9:30-12pm" is 9:30am), "noon". Several
    windows ("9:00am - 12:15pm, 11:45am - 3:00pm or 9am - 3pm") give the
    earliest start and the latest end."""
    t = (text or "").lower()
    t = re.sub(r"\ba\.\s*m\.?", "am", t)
    t = re.sub(r"\bp\.\s*m\.?", "pm", t)
    t = re.sub(r"\bnoon\b", "12pm", t)
    t = re.sub(r"\s+(?:to|until|till|–|—)\s+|[–—]", "-", t)
    windows = []
    for m in _WINDOW.finditer(t):
        sh, sm, smer, eh, em, emer = m.groups()
        sh, eh = int(sh), int(eh)
        sm, em = int(sm or 0), int(em or 0)
        if not smer and not emer:
            return None                       # "9-12": no meridiem anywhere, not guessed
        if not emer:
            emer = smer
            if _h24(eh, em, emer) is not None and _h24(sh, sm, smer) is not None \
                    and _h24(eh, em, emer) <= _h24(sh, sm, smer):
                emer = "pm"
        if not smer:
            smer = emer
            if _h24(sh, sm, smer) is not None and _h24(eh, em, emer) is not None \
                    and _h24(sh, sm, smer) > _h24(eh, em, emer):
                smer = "am"
        a, b = _h24(sh, sm, smer), _h24(eh, em, emer)
        if a is None or b is None or b <= a:
            return None
        windows.append((a, b))
    if not windows:
        return None
    return min(w[0] for w in windows), max(w[1] for w in windows)


def _zone(name: str):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:      # pragma: no cover - zoneinfo ships with 3.9+
        return None


def localize(d: date, hm: Optional[Tuple[int, int]], tz_name: str) -> Tuple[str, Optional[str]]:
    """(local ISO, UTC ISO). No clock -> a bare date and no instant."""
    if hm is None:
        return d.isoformat(), None
    naive = datetime(d.year, d.month, d.day, hm[0], hm[1])
    tz = _zone(tz_name)
    if tz is None:
        return naive.isoformat(), None
    dt = naive.replace(tzinfo=tz)
    return dt.isoformat(), dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# Pages

def parse_listing(page: str, base: str) -> Dict[str, Any]:
    """One listing page -> {"needs": [{"need_id", "title", "date_text"}],
    "next": the next page's URL or None, "pager": whether a pager was seen}."""
    i = page.find('<section class="needs-list"')
    if i < 0:
        i = page.find('class="needs-grid')
    body = page[i:] if i >= 0 else ""
    starts = [m.start() for m in _LIST_START.finditer(body)] + [len(body)]
    needs: List[Dict[str, Any]] = []
    have = set()
    for a, b in zip(starts, starts[1:]):
        blk = body[a:b]
        m = _NEED_ID.search(blk)
        if not m or m.group(1) in have:
            continue
        have.add(m.group(1))
        dt = _LIST_DATE[0].search(blk) or _LIST_DATE[1].search(blk)
        ti = _LIST_TITLE.search(blk)
        needs.append({"need_id": m.group(1), "title": _clean(ti.group(1)) if ti else None,
                      "date_text": _clean(dt.group(1)) if dt else None})
    nxt = _NEXT.search(page)
    return {"needs": needs, "next": urljoin(base + "/", nxt.group(1)) if nxt else None,
            "pager": bool(_PAGER.search(page))}


def parse_location(lines: List[str]) -> Dict[str, Any]:
    """The Location cell's lines -> {"name", "street", "extra", "city", "region",
    "postal", "lines"}. A street is a line that starts with a house number and
    does not join two streets; the organiser's ", City, ST ZIP" repeated on the
    street line is cut off it ("2020 Cornwall Ave, Bellingham, WA 98225")."""
    rest = list(lines)
    out: Dict[str, Any] = {"lines": list(lines), "name": None, "street": None, "extra": None,
                           "city": None, "region": None, "postal": None}
    if rest and _ZIP.match(rest[-1]):
        out["postal"] = _ZIP.match(rest.pop()).group(1)
    for k in range(len(rest) - 1, -1, -1):
        m = _CITY_ST.match(rest[k])
        if m:
            out["city"], out["region"] = m.group(1).strip(), m.group(2).upper()
            rest.pop(k)
            break
    si = next((k for k, ln in enumerate(rest) if _STREET.match(ln)), None)
    if si is not None:
        street = _TAIL.sub("", rest[si]).strip().rstrip(",")
        if not _JOINED.search(street):
            out["street"] = street
        out["extra"] = ", ".join(rest[si + 1:]) or None
        out["name"] = ", ".join(rest[:si]) or None
    else:
        out["name"] = rest[0] if rest else None
    return out


def parse_detail(page: Optional[str]) -> Optional[Dict[str, Any]]:
    """A need's page -> its facts, or None for a page that carries no need.
    Reads the title, description, Details (requirements), Location, the
    one-off date and clock, the Shifts table and the Program/Agency name -
    never the Site Supervisor."""
    if not page:
        return None
    h1 = _H1.search(page)
    title = _clean(h1.group(1)) if h1 else None
    if not title:
        return None
    d: Dict[str, Any] = {"title": title}
    m = _DESC.search(page)
    text, gone = without_contacts(_paragraphs(m.group(1))) if m else ("", 0)
    d["text"], d["dropped"] = text, gone
    reqs: List[str] = []
    m = _REQS.search(page)
    if m:
        reqs = [c for c in (_clean(x) for x in _REQ_CELL.findall(m.group(1))) if c]
    d["requirements"] = reqs
    d["background_check"] = any(_BACKGROUND.search(r) for r in reqs)
    d["family_friendly"] = any(r.lower() == "is family friendly" for r in reqs)
    d["outdoors"] = any(r.lower() == "is outdoors" for r in reqs)
    ages = [int(_MIN_AGE.match(r).group(1)) for r in reqs if _MIN_AGE.match(r)]
    d["min_age"] = min(ages) if ages else None
    m = _LOCATION.search(page)
    a = _ADDRESS.search(m.group(1)) if m else None
    d["location"] = _lines(a.group(1)) if a else []
    m = _AGENCY.search(page)
    d["program"] = _clean(m.group(2)) if m and m.group(1) == "Program" else None
    d["agency"] = _clean(m.group(2)) if m and m.group(1) == "Agency" else None
    # A one-off: "Happens On <date>" and the clock row under it.
    d["once"] = None
    m = _ADDRESS_TIME.search(page)
    if m:
        hm = _HAPPENS.search(_clean(m.group(1)) or "")
        tm = _TIME_CELL.search(m.group(1))
        if hm:
            mon = _month(hm.group(1))
            try:
                when = date(int(hm.group(3)), mon, int(hm.group(2))) if mon else None
            except ValueError:
                when = None
            sp = _SPOTS_ONCE.search(page)
            spots = _clean(sp.group(1)) if sp else None
            open_n = None
            if spots:
                n = re.match(r"(\d+)", spots)
                open_n = int(n.group(1)) if n else (0 if re.search(r"\bfull\b|\bno\b", spots, re.I) else None)
            d["once"] = {"date": when.isoformat() if when else None, "date_text": hm.group(0),
                         "clock_text": _clean(tm.group(1)) if tm else None, "open": open_n}
    # The Shifts table, by its own column headings.
    d["shifts"] = []
    m = _SHIFTS.search(page)
    if m:
        heads = [(_clean(h) or "").lower() for h in _TH.findall(m.group(1))]
        body = m.group(1).split("<tbody", 1)[-1]
        col = {name: heads.index(name) for name in ("begins", "open spots") if name in heads}
        for row in re.split(r"(?i)</tr>", body):
            cells = _TD.findall(row)
            if "begins" not in col or len(cells) <= col["begins"]:
                continue
            begins = _clean(cells[col["begins"]].replace("<br>", " ").replace("<br/>", " "))
            spots = _clean(cells[col["open spots"]]) if "open spots" in col and len(cells) > col["open spots"] else None
            sm = _SPOTS.search(spots or "")
            d["shifts"].append({"begins": begins,
                                "open": int(sm.group(1)) if sm else (0 if re.search(r"\bfull\b", spots or "", re.I) else None),
                                "total": int(sm.group(2)) if sm else None})
    return d


# --------------------------------------------------------------------------- #
# Placement and rows

def place(detail: Dict[str, Any], portal: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """(where, None) or (None, why not). `where` carries either a pin from the
    config (lat, lon) or a street address for the sync's Census batch."""
    lines = detail.get("location") or []
    if not lines:
        return None, "no meeting place on the page"
    loc = parse_location(lines)
    joined = " ".join(lines)
    if _VIRTUAL.search(joined) or _VIRTUAL.search(detail.get("title") or "") and not loc["street"]:
        return None, "virtual"
    states = [s.upper() for s in portal.get("states") or []]
    zip3 = [str(z) for z in portal.get("zip3") or []]
    if loc["region"] and states and loc["region"] not in states:
        return None, "meeting place outside the portal's state"
    if loc["postal"] and zip3 and loc["postal"][:3] not in zip3:
        return None, "meeting place outside the portal's area (ZIP)"
    key = location_key(lines)
    for rule in portal.get("location_overrides") or []:
        if not isinstance(rule, dict) or location_key([rule.get("location") or ""]) != key:
            continue
        lat, lon = rule.get("lat"), rule.get("lon")
        if lat is None or lon is None:
            continue
        center = portal.get("center")
        if center and _km((float(lat), float(lon)), (center[0], center[1])) > float(portal.get("max_km", 60)):
            return None, "override pin far from the portal's area"
        return {"venue": rule.get("venue") or loc["name"] or loc["street"] or lines[0],
                "lat": float(lat), "lon": float(lon), "loc": loc, "pinned": True}, None
    if not loc["street"]:
        return None, "meeting place is not a street address (no pin in the config)"
    if not (loc["city"] and loc["region"]):
        return None, "street address without a city and state"
    return {"venue": loc["name"] or loc["street"], "loc": loc, "pinned": False}, None


def meeting_text(loc: Dict[str, Any]) -> str:
    parts = [p for p in (loc.get("name"), loc.get("street") or None, loc.get("extra")) if p]
    if not loc.get("street"):
        parts = [ln for ln in loc.get("lines") or [] if not _ZIP.match(ln) and not _CITY_ST.match(ln)]
    return ", ".join(parts)


def fingerprint_for(title: str, d: date, start: Optional[Tuple[int, int]], venue: Optional[str]) -> str:
    """make_fingerprint("<title> HH:MM", day, venue) for a timed shift, the plain
    recipe for a date-only one: both provable by EventStore's notice_fingerprint."""
    if start:
        return make_fingerprint(f"{title} {start[0]:02d}:{start[1]:02d}", d.isoformat(), venue)
    return make_fingerprint(title, d.isoformat(), venue)


def build_event(portal: Dict[str, Any], need_id: str, url: str, detail: Dict[str, Any],
                where: Dict[str, Any], shift: Dict[str, Any]) -> NormalizedEvent:
    tz_name = portal["timezone"]
    d, start, end_day, end = shift["day"], shift["start"], shift.get("end_day"), shift["end"]
    start_local, start_utc = localize(d, start, tz_name)
    end_local, end_utc = localize(end_day or d, end, tz_name) if end else (None, None)
    loc = where["loc"]
    head: List[str] = []
    # Who runs it: the posting agency on a volunteer centre's portal, else the
    # portal's own department, with its programme in brackets ("Volunteer
    # shift with Portland Parks & Recreation (Nature Patches).").
    promoter = detail.get("agency") or portal.get("promoter") or portal.get("name")
    program = detail.get("program")
    if program and re.sub(r"\W+", "", program.lower()) == re.sub(r"\W+", "", (promoter or "").lower()):
        program = None              # Olympia's programme is the department: "Parks, Arts, & Recreation"
    if promoter:
        head.append(f"Volunteer shift with {promoter}" + (f" ({program})." if program else "."))
    else:
        head.append("Volunteer shift.")
    meet = meeting_text(loc)
    if meet:
        head.append(f"Meeting place: {_trim(meet, 200)}" + ("" if meet.endswith((".", "!", "?")) else "."))
    if detail.get("min_age"):
        head.append(f"Ages {detail['min_age']} and older.")
    # "Is Family Friendly" is the kids door's only when a child may come: 2 of
    # Portland's 30 family-friendly needs on 2026-10-25 also say "14 and older"
    # (the Harvest Festival and Boo Bash crews) - teen volunteers, not children.
    # The line is printed only then too: the sync's kids rule reads "Family
    # friendly." in a description, and put kids back on both of those rows.
    for_children = bool(detail.get("family_friendly")) and (detail.get("min_age") or 0) <= KIDS_MAX_MIN_AGE
    if for_children:
        head.append("Family friendly.")
    head.append(portal.get("signup_line") or "Sign up on its page (Respond).")
    head_text = "\n".join(head)
    body = detail.get("text") or ""
    room = min(int(portal.get("description_chars", 600)), DESCRIPTION_MAX - len(head_text) - 2)
    body = _trim(body, room) if body else ""
    extras: List[str] = []
    if detail.get("outdoors"):
        extras.append("outdoors")
    if for_children:
        extras.append("kids")
    venue = where["venue"]
    hm = f"{start[0]:02d}{start[1]:02d}" if start else "day"
    nev = NormalizedEvent(
        source=SOURCE,
        source_id=f"{portal['slug']}:{need_id}:{d.isoformat()}:{hm}"[:200],
        name=detail["title"],
        description=head_text + ("\n\n" + body if body else ""),
        start_local=start_local, start_utc=start_utc,
        end_local=end_local, end_utc=end_utc,
        timezone=tz_name,
        venue_name=venue,
        address=None if where.get("pinned") else loc.get("street"),
        city=loc.get("city") or portal.get("city"),
        region=loc.get("region") or (portal.get("states") or [None])[0],
        postal_code=loc.get("postal"),
        country="US",
        latitude=where.get("lat"), longitude=where.get("lon"),
        coords_exact=bool(where.get("pinned")),
        category=portal.get("category", "volunteer"),
        categories=extras[:2],
        promoter=promoter,
        ticket_url=url,
    )
    nev.fingerprint = fingerprint_for(detail["title"], d, start, venue)
    return nev


def shifts_of(detail: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """The need's dated shifts -> ([{day, start, end_day, end, open}], refused).
    Rows with the same begins and ends are one occasion, open if either is."""
    out: Dict[Tuple, Dict[str, Any]] = {}
    refused: Dict[str, int] = {}

    def no(why: str) -> None:
        refused[why] = refused.get(why, 0) + 1

    for s in detail.get("shifts") or []:
        got = parse_begins(s.get("begins"))
        if got is None:
            no("unreadable shift time")
            continue
        day, start, end_day, end = got
        k = (day, start, end_day, end)
        cur = out.setdefault(k, {"day": day, "start": start, "end_day": end_day, "end": end, "open": None})
        if s.get("open") is not None:
            cur["open"] = (cur["open"] or 0) + s["open"]
    once = detail.get("once")
    if once and not detail.get("shifts"):
        if not once.get("date"):
            no("unreadable date")
        else:
            day = date.fromisoformat(once["date"])
            clock = parse_clock_text(once.get("clock_text")) if once.get("clock_text") else None
            if once.get("clock_text") and clock is None:
                no("unreadable clock")
            else:
                start, end = clock if clock else (None, None)
                out[(day, start, day, end)] = {"day": day, "start": start, "end_day": day if end else None,
                                               "end": end, "open": once.get("open")}
    return list(out.values()), refused


# --------------------------------------------------------------------------- #
# HTTP

class Refused(Exception):
    """The host said no (401/403/429, a challenge, robots.txt)."""


class Stop(Exception):
    """The run deadline passed, or the host stopped answering."""


class Client:
    """One host, paced, robots-checked, deadline-aware (mapsee_ingest_glenecho's
    contract). `get` returns the body of a 2xx and None for any other answer
    worth skipping, a network error included; raises Refused for a refusal and
    Stop at the deadline or after `max_failures` failures in a row."""

    def __init__(self, session, pause: float, deadline: Optional[float],
                 robots=None, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep, max_failures: int = 5):
        self.session = session
        self.pause = pause
        self.deadline = deadline
        self.clock, self.sleep = clock, sleep
        self.robots = robots
        self.requests = 0
        self.errors = 0
        self.max_failures = max_failures
        self._in_row = 0
        self._last = None

    def _failed(self, why: str) -> None:
        self._in_row += 1
        if self._in_row >= self.max_failures:
            raise Stop(f"{self._in_row} requests in a row failed (last: {why}) - the host is not answering")

    def _allowed(self, url: str) -> None:
        if self.robots is None:
            return
        from robots_txt import origin_of
        fresh = origin_of(url) not in getattr(self.robots, "_files", {})
        v = self.robots.check(url)
        if fresh:                  # robots.txt was just read: it is a request to this host too
            self._last = self.clock()
            self.requests += 1
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
            wait = self.pause - (self.clock() - self._last) if self._last is not None else 0.0
            if self.deadline is not None and self.clock() + max(0.0, wait) >= self.deadline:
                raise Stop("run deadline reached")      # the pause would carry the request past it
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
                if urlsplit(nxt).netloc != host:
                    print(f"[{SOURCE}] redirect off the portal, not followed: {url} -> {nxt}")
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


# --------------------------------------------------------------------------- #
# The run

def read_portal(portal: Dict[str, Any], client: Client, today: date,
                emit: Callable[[NormalizedEvent], None],
                seen: Callable[[str], None]) -> Dict[str, Any]:
    """Read the listing to its last page, refuse the standing roles unread, read
    every other need's page, and emit one row per open dated shift with a place.
    `seen(fingerprint)` is called for every refused shift in the window.
    Returns the report; "complete" means every listing page and every page that
    had to be read was read, so a shift missing from it has gone."""
    base = portal["base"].rstrip("/")
    horizon = today + timedelta(days=int(portal.get("within_days", DEFAULT_WITHIN_DAYS)))
    max_pages = int(portal.get("max_listing_pages", 40))
    skip_programs = {str(p).strip().lower() for p in portal.get("skip_programs") or []}
    rep: Dict[str, Any] = {"listed": 0, "kept": 0, "refused": {}, "notes": {}, "stopped": None,
                           "complete": False, "window": (today, horizon), "requests": 0,
                           "pages": {"listing": 0, "needs": 0}}

    def count(bucket: str, key: str, n: int = 1) -> None:
        rep[bucket][key] = rep[bucket].get(key, 0) + n

    whole = True
    needs: List[Dict[str, Any]] = []
    have = set()
    url: Optional[str] = base + "/need/"
    try:
        while url:
            if rep["pages"]["listing"] >= max_pages:
                count("notes", f"listing cut at max_listing_pages ({max_pages})")
                whole = False
                break
            page = client.get(url)
            rep["pages"]["listing"] += 1
            if page is None:
                count("notes", "listing page not read")
                whole = False
                break
            parsed = parse_listing(page, base)
            if not parsed["needs"]:
                if rep["pages"]["listing"] == 1:
                    print(f"[{SOURCE}] !! {portal.get('name')}: the listing matched no needs - the markup "
                          f"has probably changed. Check _LIST_START and _NEED_ID.", flush=True)
                    whole = False
                break
            for n in parsed["needs"]:
                if n["need_id"] not in have:
                    have.add(n["need_id"])
                    needs.append(n)
            url = parsed["next"]
    except (Stop, Refused) as exc:
        rep["stopped"] = (f"REFUSED - {exc}" if isinstance(exc, Refused) else str(exc))
        rep["requests"] = client.requests
        return rep
    rep["listed"] = len(needs)

    queue: List[Dict[str, Any]] = []
    for n in needs:
        kind, when = parse_listing_date(n.get("date_text"))
        if kind == "standing":
            count("refused", "need: a standing role (ongoing, flexible or a date range), not read")
            continue
        if kind == "once" and when is not None and (when < today or when > horizon):
            count("notes", "need: one-off outside the window, not read")
            continue
        queue.append(n)

    for i, n in enumerate(queue):
        url = f"{base}/need/detail/?need_id={n['need_id']}"
        try:
            page = client.get(url)
        except (Stop, Refused) as exc:
            rep["stopped"] = (f"REFUSED - {exc}" if isinstance(exc, Refused) else str(exc))
            count("notes", "need pages left for the next run", len(queue) - i)
            whole = False
            break
        rep["pages"]["needs"] += 1
        detail = parse_detail(page)
        if detail is None:
            count("notes", "need page not read" if page is None else "need page carries no need")
            whole = False
            continue
        if detail.get("dropped"):
            count("notes", "contact sentences dropped", detail["dropped"])
        shifts, bad = shifts_of(detail)
        for why, k in bad.items():
            count("refused", f"shift: {why}", k)
        window = [s for s in shifts if today <= s["day"] <= horizon]
        if len(window) < len(shifts):
            count("notes", "shift: outside the window", len(shifts) - len(window))
        if not shifts and not bad:
            count("refused", "need: no dated shift on the page")
            continue
        why = None
        where = None
        if detail.get("background_check"):
            why = "need: requires a background check (a vetted role)"
        elif (detail.get("program") or "").lower() in skip_programs:
            why = "need: a programme the config skips (skip_programs)"
        else:
            where, why = place(detail, portal)
            if why:
                why = "need: " + why
        if why:
            count("refused", why)
            if window:
                count("notes", "shift: refused with its need", len(window))
            if "not a street address" in why or "outside" in why or "far from" in why:
                print(f"[{SOURCE}] {portal.get('name')}: refused ({why[6:]}): {detail['title']} "
                      f"[{' / '.join(detail.get('location') or [])}] {url}")
            venue_guess = (parse_location(detail.get("location") or []).get("name")
                           or parse_location(detail.get("location") or []).get("street"))
            for s in window:
                seen(fingerprint_for(detail["title"], s["day"], s["start"], venue_guess))
            continue
        for s in window:
            if s["start"] == (0, 0):
                count("refused", "shift: starts at midnight (a placeholder clock)")
                seen(fingerprint_for(detail["title"], s["day"], s["start"], where["venue"]))
                continue
            if s.get("open") == 0:
                count("refused", "shift: full")
                seen(fingerprint_for(detail["title"], s["day"], s["start"], where["venue"]))
                continue
            emit(build_event(portal, n["need_id"], url, detail, where, s))
            rep["kept"] += 1
            count("notes", "rows pinned from the config" if where.get("pinned") else "rows left to the Census")
    if client.errors:
        rep["notes"]["network error, page skipped"] = client.errors
    rep["requests"] = client.requests
    rep["complete"] = whole and rep["stopped"] is None
    return rep


def _report(name: str, rep: Dict[str, Any]) -> None:
    refused = sum(rep["refused"].values())
    detail = ", ".join(f"{n} {w}" for w, n in sorted(rep["refused"].items(), key=lambda kv: -kv[1]))
    p = rep["pages"]
    print(f"[{SOURCE}] {name}: kept {rep['kept']} shift(s) from {rep['listed']} listed need(s); refused {refused}"
          + (f" ({detail})" if detail else "")
          + f"; pages: {p['listing']} listing, {p['needs']} need; read "
          f"{'COMPLETE' if rep.get('complete') else 'NOT complete'}; {rep['requests']} request(s)", flush=True)
    for w, n in sorted(rep["notes"].items()):
        print(f"[{SOURCE}] {name}: {w}: {n}", flush=True)
    if rep.get("stopped"):
        print(f"[{SOURCE}] {name}: STOPPED - {rep['stopped']}", flush=True)


def _today_local(tz_name: str) -> date:
    fixed = os.environ.get("MAPSEE_TODAY")
    if fixed:
        return datetime.strptime(fixed, "%Y%m%d").date()
    tz = _zone(tz_name)
    return datetime.now(tz).date() if tz else date.today()


def main(argv=None, session_factory=None, sleep: Callable[[float], None] = time.sleep) -> int:
    ap = argparse.ArgumentParser(description="Import dated volunteer shifts from Galaxy Digital portals.")
    ap.add_argument("--config", default="galaxydigital_sources.json")
    ap.add_argument("--store", default="feeds_events.json")
    ap.add_argument("--max-minutes", type=float, default=DEFAULT_MAX_MINUTES,
                    help="whole-run deadline; no request starts after it (0 = none)")
    ap.add_argument("--only", help="read only the portal with this slug")
    ap.add_argument("--dry-run", action="store_true", help="read and report, write nothing")
    a = ap.parse_args(argv)

    if session_factory is None:
        try:
            import requests
        except ImportError:  # pragma: no cover
            sys.exit("This script needs 'requests'.  Install it with:  pip install requests")
        session_factory = requests.Session
    from robots_txt import Robots

    cfg = json.loads(open(a.config, encoding="utf-8").read())
    started = time.monotonic()
    deadline = started + a.max_minutes * 60 if a.max_minutes else None
    store = None if a.dry_run else EventStore(a.store)
    total = 0
    for portal in cfg.get("portals", []):
        if portal.get("skip") or (a.only and portal.get("slug") != a.only):
            continue
        name = portal.get("name", "?")
        session = session_factory()
        session.headers.update({"User-Agent": USER_AGENT,
                                "Accept": "text/html,application/xhtml+xml",
                                "Accept-Language": "en-US,en;q=0.9"})
        client = Client(session, float(portal.get("pause", 1.1)), deadline, robots=Robots(session),
                        sleep=sleep, max_failures=int(portal.get("max_failures_in_row", 5)))
        pending = [0]

        def emit(ev: NormalizedEvent) -> None:
            if store is None:
                return
            store.upsert(ev)
            pending[0] += 1
            if pending[0] >= SAVE_EVERY:
                store.save()
                pending[0] = 0

        def seen(fp: str) -> None:
            if store is not None:
                store.mark_seen(SOURCE, fp)

        today = _today_local(portal.get("timezone", "America/New_York"))
        try:
            rep = read_portal(portal, client, today, emit, seen)
        except Exception as exc:                          # noqa: BLE001
            print(f"[{SOURCE}] {name} FAILED: {type(exc).__name__}: {exc}", flush=True)
            continue
        finally:
            if store is not None:
                store.save()
        _report(name, rep)
        total += rep["kept"]
        # Every listing page and every need page that had to be read was read,
        # so a shift this portal's last complete read wrote and this one did not
        # has left it (mapsee_supabase_sync --retire-absent, per portal).
        if store is not None and rep["complete"]:
            store.mark_complete(SOURCE, rep["window"][0], rep["window"][1], id_prefix=f"{portal['slug']}:")
            store.save()
    took = (time.monotonic() - started) / 60
    if store is not None:
        print(f"[{SOURCE}] done: +{total} shifts in {took:.1f} min; "
              f"store now holds {len(store.records)} unique events.", flush=True)
    else:
        print(f"[{SOURCE}] dry run - {total} shift(s) would be written; {took:.1f} min", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
