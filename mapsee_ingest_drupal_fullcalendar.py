#!/usr/bin/env python3
"""
mapsee_ingest_drupal_fullcalendar.py - a Drupal site's events calendar, read
from the JSON that the fullcalendar_view module writes into the calendar page,
and each event placed from its own page.

    python mapsee_ingest_drupal_fullcalendar.py --config drupal_fullcalendar_sources.json \
        --store feeds_events.json [--only fairfax-county] [--days 90] [--max-minutes 12]

WHY THIS SOURCE. Fairfax County, VA (1.1 million people, the biggest
jurisdiction around Washington, DC) publishes one events calendar for every
agency at https://www.fairfaxcounty.gov/events/ and no feed: no iCal, no RSS,
no JSON-LD on any event page. What it does publish is the calendar's data.
Drupal's fullcalendar_view module hands the browser its events in
drupalSettings (`<script type="application/json"
data-drupal-selector="drupal-settings-json">`), and that is the one request
this adapter needs for the list. Read 2026-10-04: 382 events, 2026-09-04 to
2026-12-15. 195 of them start in the next 90 days, and 116 of those come from
the Park Authority (paths under /events/parks/): nature centres, the nine rec
centres, golf courses and historic sites. The DC sweep of 2026-10-04 found the
county's drop-in timetables behind ParkTakes (UseDirect, a registration
catalogue), so this calendar is what the county says is ON at its parks and
centres, one occasion at a time.

The module is not Fairfax's: fullcalendar_view is a contributed Drupal module,
and both shapes it has shipped are read. 8.x-2.x (Fairfax) puts the event list
itself in `fullCalendarView`; 5.x puts one entry per view, each carrying
`calendar_options` (a JSON string) with the events inside. Everything
site-specific - which paths to rewrite, which agency is which promoter, which
category, where the map's default centre is, the venue book - is config.

------------------------------------------------------------------------------
LESSONS (measured 2026-10-04)
------------------------------------------------------------------------------

1. THE CALENDAR KNOWS NO PLACE. Each event has title, start, end (ISO with an
   offset), eventtype, url, id and a description - and no location field. The
   park is in the URL path (/events/parks/burke-lake/campfire-fridays/101626),
   and the calendar site's own event page shows the TITLE again where the
   location should be ("Location: Campfire Fridays"). The page to read is the
   agency's copy, which is the same path without /events
   (`detail_rewrite`): on it the location is a Drupal `location` node with the
   geolocation module's point (`data-lat`/`data-lng`) and a street address.

2. THE MAP'S CENTRE IS NOT THE EVENT'S PLACE. The geolocation widget carries
   `data-centre-lat="38.853854" data-centre-lng="-77.356967"` on EVERY page -
   the map's default view, which is the Government Center's own pin. Reading
   the first latitude-shaped attribute on a page would put all 195 events on
   one building in Fairfax. Only `.geolocation-location` (the node's marker)
   is read, and a marker within 100 m of a configured `map_centres` entry is
   believed only when the node's street is that centre's street. Measured: 13
   of the 72 non-park pages carry that point, all 13 the Government Center's
   own node (12000 Government Center Parkway); no other node sits on it.

3. NO POINT, NO PIN. A row is placed by (a) its page's own location point
   (115 of 116 park pages; 35 of 72 others), or (b) a street on its page with
   no point. The sync geocodes such a street with the US Census batch, which
   could not place 3 of the 13 streets on 2026-10-04 (4621 Legato Road, Fire
   Station 40; 3001 Vaden Drive; 6500 Franconia-Springfield Pkwy, which the
   one-line geocoder put on Franconia Rd) - the sync then drops the row, so 5
   of 15 such rows never reached the map. Those streets are in the config's
   STREET BOOK (`street_pins`, OSM's pins read through Geofabrik's Postpass,
   matched on the normalised street and house number) and are coords_exact;
   the other 10 are sent without coordinates for the sync to geocode. The
   sync never geocodes a bare town (_addr_parts returns None). (c) The
   config's venue book, keyed by the URL path, holds the Park Authority's own
   pins for its parks and rec centres (1 row: a Huntley Meadows hike whose
   page names no place). Anything else is dropped and counted
   ("unplaceable": 1, a water-recycling plant open house), never sent with
   the county's name for a place. The page point is coords_exact: Census
   geocodes of the book's streets sit a median 216 m (max 1,074 m) from the
   Park Authority's pins.

4. WHAT IS NOT AN OCCASION ANYONE CAN GO TO IS OUT, AND EVERY REFUSAL IS
   COUNTED. Before any page is fetched: eventtype "Public Meeting" (29 of 195)
   and governance titles (board, committee, commission, hearing, work
   session), closures and cancellations, sold-out events (2), and online
   sessions by title ("Virtual ...", "(Virtual)", "Café Virtual": 12). After
   the page: a training whose registration form is a Zoom meeting and which
   names no place (9, the Community Services Board's), an appointment
   ("Appointments required": 1), members only, a booking for groups only
   (Reading Tails: "open for group registrations only"), a page that shows
   no date at all (the same 2 Reading Tails pages: their hidden startDate,
   18:00, is a placeholder - "check the exact date and time in the
   registration link"), and a registration-only CLASS: instruction (class,
   course, lesson, camp, training, certification, workshop, a sport's clinic)
   in the TITLE, or a blurb that calls itself one ("This class will focus
   on", "Classes held during the same week", "In this hands-on workshop",
   "Students learn how to"), with a registration form or "registration
   required". 15 refused: 6 "Cider Making (Class)" by title, 9 more by the
   blurb or a "Workshop" title (Little Hands on the Farm, a weekly toddler
   class; Foraging for Wild Edibles; Teacup Topiary Workshop ...). A one-off
   public event that takes registration (a $12 campfire night on ParkTakes:
   98 of 123 kept rows) is a ticketed event and stays, with "Registration
   required." and its price said - or "Tickets online or at the door." when
   the blurb sells at the door (Haunted Mini Golf, 2). A vaccine clinic is a
   service, not a class.

5. A PRICE IS SAID, FIRST, AND "FREE" ONLY IN THE SOURCE'S WORDS. The page's
   `event_cost` value is copied: "$12.00" is written "Price: $12.00 (not
   free)", the phrase ../mapsee 0227's FREE_NEG vetoes on, so a fee row whose
   blurb says "Free!" (a golf clinic's sponsor line) cannot be tagged free.
   "Free", "$0.00" and Animal Services' "$.00 - Free" are written "Admission:
   free." (0227's strict pattern). No price field, no price line. The price
   and registration lines come FIRST and the blurb is cut so the whole fits
   the sync's 800: the sync's _cap_prose cuts the END of long prose, and with
   the price line last 9 of 133 rows lost it in the database (a 1,022-
   character Halloween party among them), "(not free)" veto included. Of 123
   rows written on 2026-10-04 (replayed): 92 fee, 0 free, 31 unpriced; 0227's
   twin, run on the sync's to_row output, tags 7 of the 122 stored free,
   every one from the source's own words ("Free Adoption Event", "event is
   FREE and open to the public"), and 0 fee rows; 0 price lines are cut.

6. ONE ROW PER OCCASION, NEVER A ROW ACROSS A GAP. Every occasion is its own
   Drupal node (Goblin Golf on five dates is five ids), so identity is the
   node id and its day. The one timed event that ran from 10-07 13:30 to 10-21
   15:30 ("Botanical Journaling", a weekly series written as one row) would
   light a pin "happening now" for two weeks; a timed span of 24 hours or more
   is refused as a series, while an overnight occasion shorter than that (a
   Family Campout, 14:30 to 11:30 the next day) is one row with its end. A
   same-day span over 12 hours is an am/pm slip (4 in the window: Little Hands
   09:45-22:30, a hike 10:00-23:00 whose page says "10:00 am to 10:00 pm"),
   and its end is dropped (2 kept rows). A row whose end equals its start has
   no end (41 of 195; 4 kept); the sync synthesizes an end for both. The fingerprint's basis
   carries the start clock, as bibliocommons/perfectmind/toronto_rec do, so a
   second session the same evening is its own row - while E.C. Lawrence's
   "House of Reptiles", posted as two nodes both at 18:00, merges into one.

Every request carries the MapseeAggregator UA, is checked against the host's
robots.txt (robots_txt.Robots, Crawl-delay honoured) and is paced >= 1.1 s on
its host. Redirects are followed by hand, only to an origin whose robots.txt is
also read. A 401/403/429 or a bot challenge is a refusal: never retried, and
that host is asked nothing more this run. A 5xx or a timeout is retried twice;
one page that still fails (or that robots.txt refuses on its own path) is
dropped and counted, and the source goes on - only a refusal from the
calendar's own host and the deadline stop it ("rows written" is kept current,
so a STOPPED line says what was kept). --max-minutes is a deadline no request
starts after, and the store is saved after every source. The JSON-level
refusals are made before any page is asked for, so 151 of the 195 rows need a
page: the live run of 2026-10-04 was 153 requests (robots.txt, the calendar,
151 pages) in 198 s. A slow day (3.6 s a
page was measured the same morning) is about 9 minutes, inside the default
--max-minutes 12.
"""
from __future__ import annotations

import argparse
import html as htmlmod
import json
import math
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlsplit

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    sys.exit("This script needs Python 3.9+ (zoneinfo).")

import robots_txt
from mapsee_ingest import EventStore, NormalizedEvent, looks_online_only, make_fingerprint, strip_notice

UA = "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)"
MIN_INTERVAL_S = 1.1
# Fairfax's pages answered in 2-5 s on 2026-10-04; 30 s is generous, and the
# deadline keeps any slow run inside its step.
REQUEST_TIMEOUT_S = 30
DEFAULT_MAX_MINUTES = 12.0
DEFAULT_DAYS = 90
MAX_REDIRECTS = 3
# A point this close to a map's default centre is the centre, not a venue.
CENTRE_RADIUS_M = 100.0
# The sync's _cap_prose cuts any description over its DESCRIPTION_MAX (800)
# from the END of the head, keeping only a short last paragraph. So the price
# and registration lines go FIRST (as perfectmind's do), and the blurb is cut
# here to what is left, so the sync never has to cut at all (lesson 5).
SYNC_DESCRIPTION_MAX = 800
ATTRIBUTION_MAX = 200
# Lesson 6: a timed span of a day or more is a series written as one row; a
# same-day span over 12 h from a morning start is an am/pm slip.
SERIES_HOURS = 24
LONGEST_SAME_DAY_HOURS = 12
_RETRYABLE = {408, 500, 502, 503, 504}
_REFUSALS = {401, 403, 429}


class Refused(Exception):
    """The publisher said no (401/403/429, a challenge, or robots.txt). Never
    retried or worked around; the host is asked nothing more this run."""


class OutOfTime(Exception):
    """--max-minutes has passed: no request starts after it."""


class NotFound(Exception):
    """HTTP 404/410 - the page is gone (not a refusal)."""


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
class Reader:
    """Paced, robots-checked GETs. Counts its own requests (robots.txt reads
    included), because the request budget is part of what a dry run reports."""

    def __init__(self, session, robots=None, min_interval: float = MIN_INTERVAL_S,
                 tries: int = 3, sleep=time.sleep, clock=time.monotonic,
                 deadline: Optional[float] = None, timeout: float = REQUEST_TIMEOUT_S) -> None:
        self.session = session
        self.robots = robots if robots is not None else robots_txt.Robots(session)
        self.min_interval = min_interval
        self.tries = tries
        self.sleep = sleep
        self.clock = clock
        self.deadline = deadline
        self.timeout = timeout
        self.requests = 0
        self.refused: Dict[str, str] = {}      # origin -> why
        self._last: Dict[str, float] = {}

    def _allowed(self, url: str) -> float:
        """The pause this origin asks for; raises Refused if robots.txt says no."""
        origin = robots_txt.origin_of(url)
        if origin in self.refused:
            raise Refused(f"{self.refused[origin]} earlier this run; nothing more is asked of {origin}")
        if self.deadline is not None and self.clock() >= self.deadline:
            raise OutOfTime("run deadline reached; no request starts after it")
        known = origin in getattr(self.robots, "_files", {})
        verdict = self.robots.check(url)
        if not known:
            self.requests += 1
        if verdict.get("allowed") is not True:
            why = f"robots.txt ({verdict.get('status')}) refuses {robots_txt.request_path(url)}: {verdict.get('rule')}"
            if verdict.get("allowed") is None or verdict.get("status") == "challenge":
                self.refused[origin] = why
            raise Refused(why)
        return max(self.min_interval, float(verdict.get("crawl_delay") or 0))

    def get(self, url: str) -> Tuple[str, str]:
        """(final url, body text) of a 200. Follows up to MAX_REDIRECTS hops by
        hand, each one robots-checked."""
        for _hop in range(MAX_REDIRECTS + 1):
            r = self._get_once(url)
            if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                url = urljoin(url, r.headers["location"])
                continue
            return url, r.text
        raise RuntimeError(f"more than {MAX_REDIRECTS} redirects from {url}")

    def _get_once(self, url: str):
        origin = robots_txt.origin_of(url)
        last: Exception = RuntimeError("no attempt made")
        for attempt in range(self.tries):
            interval = self._allowed(url)
            prev = self._last.get(origin)
            if prev is not None:
                wait = interval - (self.clock() - prev)
                if wait > 0:
                    self.sleep(wait)
            if self.deadline is not None and self.clock() >= self.deadline:
                raise OutOfTime("run deadline reached; no request starts after it")
            try:
                self.requests += 1
                r = self.session.get(url, timeout=self.timeout, allow_redirects=False)
            except Exception as exc:  # noqa: BLE001 - timeouts, resets
                last = exc
            else:
                if r.status_code in _REFUSALS:
                    self.refused[origin] = f"HTTP {r.status_code} from {origin}"
                    raise Refused(self.refused[origin])
                body_head = (r.text or "")[:6000] if r.status_code < 500 else ""
                if robots_txt.CHALLENGE_RX.search(body_head):
                    self.refused[origin] = f"a bot challenge or block page from {origin}"
                    raise Refused(self.refused[origin])
                if r.status_code in (404, 410):
                    raise NotFound(f"HTTP {r.status_code} {url}")
                if r.status_code == 200 or 300 <= r.status_code < 400:
                    return r
                if r.status_code not in _RETRYABLE:
                    raise RuntimeError(f"HTTP {r.status_code} from {url}")
                last = RuntimeError(f"HTTP {r.status_code}")
            finally:
                self._last[origin] = self.clock()
            if attempt + 1 < self.tries:
                self.sleep(2 * (attempt + 1))
        raise last


# ---------------------------------------------------------------------------
# the calendar's JSON
# ---------------------------------------------------------------------------
_SETTINGS_RX = re.compile(
    r'<script[^>]+data-drupal-selector="drupal-settings-json"[^>]*>(.*?)</script>', re.S | re.I)


def calendar_events(page: str) -> List[Dict[str, Any]]:
    """Every event the page's fullcalendar_view hands the browser.

    8.x-2.x: drupalSettings.fullCalendarView IS the list of events.
    5.x: drupalSettings.fullCalendarView is one entry per view (a list or a dict
    keyed by view index), each with `calendar_options`, a JSON STRING whose
    `events` is the list. Both are read; anything else is an error, because a
    page that stopped carrying its events must not read as an empty calendar."""
    m = _SETTINGS_RX.search(page)
    if not m:
        raise RuntimeError("no drupal-settings-json block on the calendar page")
    settings = json.loads(m.group(1))
    fcv = settings.get("fullCalendarView")
    if fcv is None:
        raise RuntimeError("drupalSettings has no fullCalendarView")
    views = list(fcv.values()) if isinstance(fcv, dict) else list(fcv)
    if views and all(isinstance(v, dict) and "start" in v for v in views):
        return views                                                  # 8.x-2.x
    events: List[Dict[str, Any]] = []
    for v in views:
        opts = v.get("calendar_options") if isinstance(v, dict) else None
        if isinstance(opts, str):
            opts = json.loads(opts)
        if isinstance(opts, dict) and isinstance(opts.get("events"), list):
            events.extend(e for e in opts["events"] if isinstance(e, dict))
    if views and not events:
        raise RuntimeError("fullCalendarView carries no events in a shape this adapter reads")
    return events


def _parse_iso(s: Any) -> Optional[datetime]:
    if not s or not isinstance(s, str):
        return None
    s = s.strip()
    try:
        if len(s) == 10:
            return datetime.strptime(s, "%Y-%m-%d")
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _local(dt: datetime, tz) -> datetime:
    """The wall clock in the site's zone (a naive value is already local)."""
    return dt.astimezone(tz).replace(tzinfo=None) if dt.tzinfo else dt


def _utc(local: datetime, tz) -> str:
    return local.replace(tzinfo=tz).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _today(tz) -> date:
    """MAPSEE_TODAY=YYYYMMDD fixes "today" for the tests; otherwise today in the
    site's zone (the runner is on UTC)."""
    fixed = os.environ.get("MAPSEE_TODAY")
    if fixed:
        return datetime.strptime(fixed, "%Y%m%d").date()
    return datetime.now(tz).date()


# ---------------------------------------------------------------------------
# the event's page
# ---------------------------------------------------------------------------
_BLOCK_RX = re.compile(r"(?i)<br\s*/?>|</?(?:p|div|li|ul|ol|h[1-6]|tr|section|article)\b[^>]*>")
_TAG_RX = re.compile(r"<[^>]+>")
_ATTR_RX = re.compile(r'([\w:-]+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\')')


def _attrs(tag: str) -> Dict[str, str]:
    return {m.group(1).lower(): htmlmod.unescape(m.group(2) if m.group(2) is not None else m.group(3))
            for m in _ATTR_RX.finditer(tag)}


def _element(page: str, classes: List[str], start: int = 0) -> Optional[Tuple[int, str]]:
    """(offset, inner HTML) of the first element whose class carries ALL of
    `classes`. The close is found by counting that tag name's opens and closes,
    so an unclosed <p> or <br> inside cannot end the element early or late (a
    depth-tracking parser drifted on Fairfax's `<p><article>` nesting)."""
    for m in re.finditer(r"<([a-zA-Z][\w-]*)\b[^>]*\bclass\s*=\s*[\"']([^\"']*)[\"'][^>]*>", page[start:]):
        cls = m.group(2).split()
        if not all(c in cls for c in classes):
            continue
        name = m.group(1).lower()
        if name in ("br", "img", "hr", "meta", "input"):
            continue
        open_end = start + m.end()
        depth, pos = 1, open_end
        tag_rx = re.compile(rf"<(/?){name}\b[^>]*>", re.I)
        for t in tag_rx.finditer(page, open_end):
            if t.group(0).endswith("/>"):
                continue
            depth += -1 if t.group(1) else 1
            if depth == 0:
                return start + m.start(), page[open_end:t.start()]
        return start + m.start(), page[open_end:]
    return None


def _text_lines(fragment: Optional[str]) -> List[str]:
    if not fragment:
        return []
    s = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", fragment)
    s = _BLOCK_RX.sub("\n", s)
    s = htmlmod.unescape(_TAG_RX.sub(" ", s)).replace("\xa0", " ")
    return _lines(s)


def _lines(text: str) -> List[str]:
    out = []
    for ln in text.split("\n"):
        ln = re.sub(r"\s+", " ", ln).strip()
        if ln and ln != ",":
            out.append(ln)
    return out


DEFAULT_PAGE = {
    "date": ["event_date", "value"],
    "location": ["event_location_address"],
    "price": ["event_cost", "value"],
    "description": ["event_description_content_inner"],
    "registration": ["registration_link"],
}


def parse_event_page(page: str, selectors: Optional[Dict[str, List[str]]] = None) -> Dict[str, Any]:
    """What an event page says about where, how much and how to get in.

    `points` are the geolocation module's markers (`.geolocation-location`
    data-lat/data-lng) - NEVER `data-centre-lat`, the map's default view, which
    is the same on every page (lesson 2). The location node (`<article
    class="location ...">`) gives the venue's title and street."""
    sel = dict(DEFAULT_PAGE)
    sel.update(selectors or {})
    found: Dict[str, Optional[str]] = {}
    for key, classes in sel.items():
        if key == "date":
            continue
        got = _element(page, classes)
        found[key] = got[1] if got else None
    # The date the publisher SHOWS. Fairfax's template also carries an empty
    # modal twin ("related_modal_event_cost event_date value"), so the first
    # element with a date in it is the one read.
    visible_date = None
    pos = 0
    while sel.get("date"):
        got = _element(page, sel["date"], pos)
        if not got:
            break
        text = " ".join(_text_lines(got[1]))
        if re.search(r"\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2}|\b\d{1,2},?\s+\d{4}\b", text):
            visible_date = text
            break
        pos = got[0] + 1
    points: List[Tuple[float, float]] = []
    for m in re.finditer(r"<[^>]*\bclass\s*=\s*[\"'][^\"']*\bgeolocation-location\b[^>]*>", page):
        a = _attrs(m.group(0))
        try:
            points.append((float(a["data-lat"]), float(a["data-lng"])))
        except (KeyError, ValueError):
            pass
    node = node_title = None
    node_lines: List[str] = []
    art = re.search(r"<article\b[^>]*\bclass\s*=\s*[\"'](?:[^\"']*\s)?location(?:\s[^\"']*)?[\"'][^>]*>(.*?)</article>",
                    page, re.S | re.I)
    if art:
        node = _attrs(art.group(0)[:art.group(0).index(">") + 1]).get("about")
        inner = art.group(1)
        h3 = re.search(r"(?is)<h3\b[^>]*>(.*?)</h3>(.*)", inner)
        if h3:
            node_title = " ".join(_text_lines(h3.group(1))) or None
            rest = re.split(r"(?i)<a\b[^>]*>\s*Link to Google Maps", h3.group(2))[0]
            node_lines = _text_lines(rest)
    reg_html = found.get("registration")
    reg = None
    if reg_html:
        a = re.search(r"<a\b[^>]*>", reg_html, re.I)
        reg = _attrs(a.group(0)).get("href") if a else None
    return {
        "visible_date": visible_date,
        "points": sorted(set(points)),
        "location_lines": _text_lines(found.get("location")),
        "price": " ".join(_text_lines(found.get("price"))).strip() or None,
        "description": "\n".join(_text_lines(found.get("description"))).strip() or None,
        "registration": reg,
        "location_node": node,
        "location_title": node_title,
        "location_address": node_lines,
    }


# ---------------------------------------------------------------------------
# placing a row
# ---------------------------------------------------------------------------
_STREET_RX = re.compile(r"^\d+[A-Za-z]?(?:-\d+)?\s+\S")


def split_address(lines: List[str]) -> Dict[str, Optional[str]]:
    """{venue, address, city, region, postal} from address-block lines, e.g.
    ["Herndon Senior Center", "873 Grace Street", "Herndon, VA 20170"].

    The city line comes in every shape Fairfax's templates print: one line
    ("Herndon, VA 20170"), one part per line ("Fairfax Station,", "VA,",
    "22039"), with the state twice ("Alexandria, VA ,", "VA,", "22310") or with
    no ZIP ("Alexandria,", "VA"). So the part after the street is read as text:
    the town is what precedes the first comma, the state the first two-letter
    word after it, the ZIP the last five digits. No street line, no address."""
    out: Dict[str, Optional[str]] = {"venue": None, "address": None, "city": None,
                                     "region": None, "postal": None}
    street = next((i for i, ln in enumerate(lines) if _STREET_RX.match(ln)), None)
    if street is None:
        out["venue"] = lines[0] if lines else None
        return out
    before = [ln for ln in lines[:street] if ln.strip(" ,")]
    out["venue"] = before[0] if before else None
    addr = lines[street].strip().rstrip(",")
    # "7611 Little River Turnpike, Room 348E": the room is where to go once
    # inside, and the Census geocoder is asked the street alone.
    m = re.match(r"(.+?),\s*((?:room|suite|ste\.?|unit|floor|fl\.?|#)\s*\S.*)$", addr, re.I)
    if m:
        addr = m.group(1)
        out["venue"] = f"{out['venue']}, {m.group(2)}" if out["venue"] else None
    out["address"] = addr
    tail = " ".join(lines[street + 1:])
    if "," in tail:
        town, rest = tail.split(",", 1)
        town = town.strip()
        if re.fullmatch(r"[A-Za-z .'-]{2,40}", town):
            out["city"] = town
        st = re.search(r"\b([A-Z]{2})\b", rest)
        out["region"] = st.group(1) if st else None
    z = re.findall(r"\b(\d{5})(?:-\d{4})?\b", tail)
    out["postal"] = z[-1] if z else None
    return out


def _metres(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    lat = math.radians((a[0] + b[0]) / 2)
    dy = (a[0] - b[0]) * 111_320
    dx = (a[1] - b[1]) * 111_320 * math.cos(lat)
    return math.hypot(dx, dy)


def _in_box(p: Tuple[float, float], box: Optional[List[float]]) -> bool:
    if not box:
        return True
    s, w, n, e = box
    return s <= p[0] <= n and w <= p[1] <= e


def page_point(info: Dict[str, Any], street: Optional[str], src: Dict[str, Any],
               stats: Dict[str, int]) -> Optional[Tuple[float, float]]:
    """The page's own location point, or None. Lesson 2: a point on a
    configured map centre is believed only when the location node's own street
    is that centre's street (the Government Center's own events)."""
    pts = [p for p in info.get("points") or [] if not (p[0] == 0 and p[1] == 0)]
    good = []
    for p in pts:
        centre = next((c for c in src.get("map_centres") or []
                       if _metres(p, (c["lat"], c["lon"])) <= CENTRE_RADIUS_M), None)
        if centre and not (street and _norm_street(street).startswith(_norm_street(centre.get("street", "")))
                           and centre.get("street")):
            _bump(stats, "page point refused: the map's default centre under another street")
        elif not _in_box(p, src.get("bbox")):
            _bump(stats, "page point refused: outside bbox")
        else:
            good.append(p)
    if len(good) > 1:
        far = max(_metres(good[0], q) for q in good[1:])
        if far > 250:
            _bump(stats, "page point refused: several locations on one page")
            return None
    return good[0] if good else None


_STREET_ABBR = {"parkway": "pkwy", "road": "rd", "drive": "dr", "street": "st", "avenue": "ave",
                "boulevard": "blvd", "lane": "ln", "turnpike": "tpke", "court": "ct", "place": "pl",
                "highway": "hwy", "circle": "cir", "terrace": "ter"}


def _norm_street(s: str) -> str:
    s = re.sub(r"[^a-z0-9 ]", " ", (s or "").lower())
    s = re.sub(r"\b(" + "|".join(_STREET_ABBR) + r")\b", lambda m: _STREET_ABBR[m.group(1)], s)
    return re.sub(r"\s+", " ", s).strip()


def street_pin(street: Optional[str], book: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The street book's surveyed pin for a street the page names without a
    point (lesson 3): matched on the normalised street, house number included."""
    key = _norm_street(street or "")
    if not key:
        return None
    return next((b for b in book or [] if _norm_street(b.get("street", "")) == key), None)


def book_entry(path: str, book: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The venue book's entry for a calendar path: the longest matching prefix."""
    best = None
    for v in book:
        pre = v.get("path")
        if pre and path.startswith(pre) and (best is None or len(pre) > len(best["path"])):
            best = v
    return best


# ---------------------------------------------------------------------------
# what is in, what is out
# ---------------------------------------------------------------------------
GOVERNANCE_RX = re.compile(
    r"\b(?:board|committee|commission|council|authority|panel|task\s+force|advisory)\b[^|]*\bmeeting\b|"
    r"\bpublic\s+hearing\b|\bwork\s+session\b|\bbudget\s+(?:hearing|town\s+hall)\b", re.I)
CLOSURE_RX = re.compile(r"\b(?:closed|closures?|cancel+ed|cancel+ation|postponed)\b", re.I)
SOLD_OUT_RX = re.compile(r"\bsold[\s-]+out\b", re.I)
ONLINE_TITLE_RX = re.compile(
    r"^\s*virtual\b|\(\s*virtual\s*\)|\bwebinar\b|\bonline\s+(?:session|class|workshop|training)\b|"
    r"\bvirtual\s+(?:parent|caf[eé]|training|workshop|session|class|info(?:rmation)?\s+session|meeting)\b|"
    r"\bcaf[eé]\s+virtual\b", re.I)
ONLINE_PLACE_RX = re.compile(r"^\s*(?:virtual|online|zoom|microsoft\s+teams|teams|webinar|web\s*ex)\b", re.I)
APPOINTMENT_RX = re.compile(r"\bappointments?\s+(?:is\s+|are\s+)?required\b|\bby\s+appointment\b", re.I)
MEMBERS_RX = re.compile(r"\b(?:members?|membership|pass[\s-]?holders?)\s+only\b|\bmembership\s+(?:is\s+)?required\b", re.I)
CLASS_RX = re.compile(
    # "clinic" only with a sport in front: a Vaccine and Microchip Clinic
    # (Animal Services, 6 in the window) is a service anyone can bring a pet to.
    r"\b(?:class(?:es)?|course|lessons?|camps?|training|certification|instruction|"
    r"(?:golf|tennis|swim(?:ming)?|skat(?:e|ing)|soccer|basketball|pickleball|archery)\s+clinics?|"
    r"workshops?|series\s+of\s+\d+|\d+\s*-?\s*(?:week|session)s?)\b", re.I)
# The closing \b is load-bearing: without it "camps?" read "Campfire Fridays" (4
# rows) and "Lorton Campus" (2 Reading Tails rows) as courses on 2026-10-04.
# The blurb calls it a class, guarded the way linkedevents guards its phrases:
# "This class will focus on", "Classes held during the same week", "Bring the
# medium of your choice to the class", "After class, view the night sky",
# "Students learn how to roll", "In this hands-on workshop". 9 of the 134 rows
# kept on 2026-10-04 said so in the blurb or the title, all behind a ParkTakes
# form; "a resource fair, workshops, and lunch" (a celebration's programme, no
# form) is a list, not a determiner, and stays.
CLASS_BODY_RX = re.compile(
    r"\b(?:this|the|a|each|after|at|to\s+the|our|per)\s+class\b|\bclasses\b|"
    r"\bstudents\s+(?:will\s+)?(?:learn|make|create|practi[cs]e|bring|need|should|must|explore)\b|"
    r"\b(?:this|the|a|an|our|each)\s+(?:[\w-]+\s+){0,2}workshop\b", re.I)
# A booking for a group the public cannot join: Animal Services' Reading Tails
# ("currently open for group registrations only", "Groups must include 8 to 20
# total attendees": 2 rows on 2026-10-04).
PRIVATE_GROUP_RX = re.compile(
    r"\bgroup\s+(?:registrations?|bookings?|reservations?)\s+only\b|\bgroups\s+only\b|"
    r"\bgroups\s+must\s+(?:include|have|consist)\b|\bfor\s+(?:private|organi[sz]ed)\s+groups\s+only\b", re.I)
# Tickets sold at the door too: a registration link is then a way to buy ahead,
# not a requirement ("Online tickets: $12 / Tickets at the Door: $15").
DOOR_RX = re.compile(
    r"\b(?:tickets?|admission)\s+(?:(?:is|are)\s+)?(?:also\s+)?(?:available\s+|sold\s+)?at\s+the\s+door\b|"
    r"\bwalk-?ins?\s+(?:are\s+)?(?:welcome|accepted)\b", re.I)
# "Registration required" written in the blurb, for pages with no form link.
REGISTRATION_RX = re.compile(r"\bregistration\s+(?:is\s+)?required\b|\bmust\s+register\b|\bpre-?registration\b", re.I)
NO_REGISTRATION_RX = re.compile(r"\bno\s+(?:pre-?)?registration\b|\bregistration\s+(?:is\s+)?not\s+required\b", re.I)


# A TITLE THAT CALLS AN OCCASION OFF NAMES IT: "CANCELED: Campfire Fridays",
# "Goblin Golf - Postponed". Each occasion is its own node, retitled in place,
# so the row we wrote under the occasion's own title stays on the map unless
# the run tombstones THAT row's fingerprint (EventStore.cancel). called_off()
# gives the title back and ingest_source builds the row from the node's page
# exactly as it would a live one. A closure ("Park Closed", "Closures") names
# no occasion and gives None (mapsee_ingest.strip_notice reads the forms; a
# "CANCELLED TODAY- X" is read here). 0 such titles in 190 window rows on
# 2026-10-05: the path costs one page request per called-off occasion.
_CALLED_OFF_TODAY = re.compile(r"^\W*(?:cancel+ed|postponed)\s+(?:today|tonight)\s*[-\u2013\u2014:|!*]+\s*", re.I)


def called_off(title: Optional[str]) -> Optional[str]:
    """The occasion's own title inside a cancelled or postponed title, or None."""
    t = (title or "").strip()
    cut = _CALLED_OFF_TODAY.sub("", t, count=1).strip()
    cut = cut if cut != t else (strip_notice(t) or "")
    if cut and re.search(r"\w", cut) and not CLOSURE_RX.search(cut):
        return cut
    return None


# What a page lost this run means: the calendar lists an occasion whose row
# was not built, so the read is not complete and nothing may read as gone.
INCOMPLETE_STATS = ("not read: over max_pages", "dropped: page refused (robots.txt or a redirect's host)",
                    "dropped: page failed (5xx, timeout or redirects)", "dropped: page gone (404)")


def json_exclusion(ev: Dict[str, Any], src: Dict[str, Any]) -> Optional[str]:
    """Refusals made from the calendar's JSON alone, before any page is asked for."""
    title = _clean(ev.get("title"))
    etype = (ev.get("eventtype") or "").strip()
    if etype and etype in (src.get("exclude_eventtypes") or []):
        return f"eventtype {etype}"
    if GOVERNANCE_RX.search(title):
        return "governance meeting"
    if CLOSURE_RX.search(title):
        return "closure or cancellation"
    if SOLD_OUT_RX.search(title):
        return "sold out"
    if ONLINE_TITLE_RX.search(title) or looks_online_only(title, None):
        return "online"
    for rx in src.get("exclude_title_rx") or []:
        if re.search(rx, title, re.I):
            return "configured title refusal"
    return None


ONLINE_HOST_RX = re.compile(r"(?:^|\.)(?:zoom\.us|zoomgov\.com|teams\.microsoft\.com|teams\.live\.com|"
                            r"webex\.com|meet\.google\.com|gotomeeting\.com|gotowebinar\.com)$", re.I)


def page_exclusion(title: str, body: str, info: Dict[str, Any],
                   src: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """Refusals that need the page: where it is, when, how you get in."""
    src = src or {}
    # A page that shows no date: its hidden startDate is a placeholder (Reading
    # Tails: "check the exact date and time in the registration link"; 2 of 151
    # pages on 2026-10-04, stored at a made-up 18:00 before this rule).
    if src.get("require_visible_date") and not info.get("visible_date"):
        return "no date shown on the page (the clock is a placeholder)"
    place = " ".join(info.get("location_lines") or [])
    reg_host = urlsplit(info.get("registration") or "").hostname or ""
    if ONLINE_PLACE_RX.search(place) or looks_online_only(title, body):
        return "online"
    # The Community Services Board's trainings register on Zoom and name no
    # place (12 of 12 in the 2026-10-04 window): the form IS the venue.
    if ONLINE_HOST_RX.search(reg_host) and not info.get("points") and not split_address(
            info.get("location_lines") or [])["address"]:
        return "online"
    if APPOINTMENT_RX.search(body) or APPOINTMENT_RX.search(title):
        return "private appointment"
    if MEMBERS_RX.search(body):
        return "members only"
    if PRIVATE_GROUP_RX.search(body) or PRIVATE_GROUP_RX.search(title):
        return "private group booking"
    registration = bool(info.get("registration")) or (
        REGISTRATION_RX.search(body) and not NO_REGISTRATION_RX.search(body))
    if registration and (CLASS_RX.search(title) or CLASS_BODY_RX.search(body)):
        return "registration-only class"
    return None


# ---------------------------------------------------------------------------
# price
# ---------------------------------------------------------------------------
_AMOUNT_RX = re.compile(r"\$\s*(\d[\d,]*(?:\.\d+)?|\.\d+)")
# What is left of a price field once its amounts and the words that say "no
# charge" are taken out; nothing left means the field says only that.
_ONLY_FREE_RX = re.compile(r"\$\s*(?:\d[\d,]*(?:\.\d+)?|\.\d+)|\bfree\b|\bno\s+(?:cost|charge|fee)\b|[-\u2013\u2014.,:;/()\s]", re.I)


def price_line(price: Optional[str]) -> Tuple[Optional[str], str]:
    """(the description's price line or None, tier). Lesson 5: free only when the
    price field says so - "Free", "$0.00", or Animal Services' "$.00 - Free"
    (2 rows on 2026-10-04) - and any amount over zero says "not free" (0227's
    veto), whatever else the field or the blurb says."""
    text = re.sub(r"\s+", " ", price or "").strip().rstrip(".")
    if not text:
        return None, "no price stated"
    amounts = [float(a.replace(",", "")) for a in _AMOUNT_RX.findall(text)]
    positive = [a for a in amounts if a > 0]
    if positive:
        tail = "not free for everyone" if len(positive) < len(amounts) else "not free"
        return f"🎟 Price: {text} ({tail}).", "fee"
    if not _ONLY_FREE_RX.sub("", text) and (amounts or re.search(r"(?i)\bfree\b|\bno\s+(?:cost|charge|fee)\b", text)):
        return "🎟 Admission: free.", "free"
    # Words and no amount ("Varies", "See website"): said as written, never free.
    return f"🎟 Price: {text}.", "unclear"


# ---------------------------------------------------------------------------
# rows
# ---------------------------------------------------------------------------
def _clean(s: Any, limit: int = 0) -> str:
    if not s:
        return ""
    s = re.sub(r"<\?xml[^>]*\?>", " ", str(s))
    s = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</div>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = htmlmod.unescape(s).replace("\xa0", " ")
    s = "\n".join(re.sub(r"[ \t]+", " ", ln).strip() for ln in s.split("\n"))
    s = re.sub(r"\n{2,}", "\n", s).strip()
    if limit and len(s) > limit:
        cut = s[:limit].rsplit(" ", 1)[0]
        s = cut.rstrip(",;:") + " ..."
    return s


def _bump(stats: Dict[str, int], key: str, n: int = 1) -> None:
    stats[key] = stats.get(key, 0) + n


def _by_path(path: str, rules: List[Dict[str, Any]], field: str, default: Any) -> Any:
    best, n = default, -1
    for r in rules or []:
        pre = r.get("path", "")
        if path.startswith(pre) and len(pre) > n:
            best, n = r.get(field, default), len(pre)
    return best


def detail_url(base: str, path: str, rewrites: List[List[str]]) -> str:
    for pat, rep in rewrites or []:
        new = re.sub(pat, rep, path, count=1)
        if new != path:
            return urljoin(base, new)
    return urljoin(base, path)


def build_event(ev: Dict[str, Any], info: Dict[str, Any], page_url: str, src: Dict[str, Any],
                tz, stats: Dict[str, int]) -> Optional[NormalizedEvent]:
    """One row, or None (counted in stats) when the page refuses it or it cannot
    be placed."""
    title = _clean(ev.get("title")).replace("\n", " ")
    path = ev.get("url") or ""
    body = _clean(info.get("description") or ev.get("fulldescription") or ev.get("description"))
    why = page_exclusion(title, body, info, src)
    if why:
        _bump(stats, f"excluded: {why}")
        return None
    start = _parse_iso(ev.get("start"))
    end = _parse_iso(ev.get("end"))
    sl = _local(start, tz)
    el = _local(end, tz) if end else None
    if el is not None and el <= sl:
        el = None                                # lesson 6: no end, not a zero-length one
        _bump(stats, "end equals start: written with no end")
    elif el is not None and el.date() == sl.date() and el - sl > timedelta(hours=LONGEST_SAME_DAY_HOURS):
        # Lesson 6: 09:45-22:30 for a toddler farm class, 10:00-23:00 for a hike
        # whose page says "10:00 am to 10:00 pm" (4 rows on 2026-10-04) - an
        # am/pm slip that would light the pin "happening now" all evening. The
        # end is dropped and the sync synthesizes one.
        el = None
        _bump(stats, "end over 12 h after a same-day start (an am/pm slip): written with no end")

    # PLACE (lesson 3) ---------------------------------------------------
    # The location node's street, else the location block's.
    node = split_address(info.get("location_address") or [])
    block = split_address(info.get("location_lines") or [])
    if not node["address"] and block["address"]:
        node = dict(block, venue=node["venue"] or block["venue"])
    point = page_point(info, node["address"], src, stats)
    lat = lon = None
    exact = False
    venue = address = city = region = postal = None
    entry = book_entry(path, src.get("venues") or [])
    pinned = None if point else street_pin(node["address"] or block["address"], src.get("street_pins") or [])
    if point:
        lat, lon = point
        exact = True
        venue = info.get("location_title") or node["venue"] or block["venue"]
        address, city, region, postal = node["address"], node["city"], node["region"], node["postal"]
        _bump(stats, "placed: the page's own location point")
    elif pinned:
        # A street the page names without a point, which the US Census batch
        # the sync geocodes with cannot place (lesson 3): its surveyed pin.
        lat, lon = pinned["lat"], pinned["lon"]
        exact = True
        got = node if node["address"] else block
        venue = info.get("location_title") or got["venue"] or pinned.get("name")
        address = got["address"]
        city, region = got["city"] or pinned.get("city"), got["region"] or pinned.get("region")
        postal = got["postal"] or pinned.get("postal")
        _bump(stats, "placed: the street book (a surveyed pin for a street the Census misses)")
    elif block["address"] and block["city"]:
        venue = block["venue"] or block["address"]
        address, city, region, postal = block["address"], block["city"], block["region"], block["postal"]
        _bump(stats, "placed: the page's street address (the sync geocodes it)")
    elif entry:
        lat, lon = entry["lat"], entry["lon"]
        exact = True
        venue, address = entry.get("name"), entry.get("address")
        city, region, postal = entry.get("city"), entry.get("region"), entry.get("postal")
        _bump(stats, "placed: the venue book")
    else:
        _bump(stats, "dropped: unplaceable (no point, no street, no venue-book entry)")
        return None
    if venue and src.get("venue_strip_rx"):
        venue = re.sub(src["venue_strip_rx"], "", venue).strip() or venue
    venue = venue or title

    # TEXT ----------------------------------------------------------------
    # Lesson 5: the price and how to get in come FIRST, the blurb after them,
    # cut so the whole stays inside the sync's 800: the sync cuts the END of
    # long prose, and a price line there was lost on 9 rows (the "(not free)"
    # veto with it) before this order.
    p_line, tier = price_line(info.get("price"))
    _bump(stats, f"price: {tier}")
    reg = info.get("registration")
    reg_url = urljoin(page_url, reg) if reg else None
    head: List[str] = [p_line] if p_line else []
    if reg_url and DOOR_RX.search(body):
        head.append("Tickets online or at the door.")
        _bump(stats, "registration link, tickets also at the door")
    elif reg_url:
        head.append("Registration required.")
        _bump(stats, "registration required (kept: a one-off public event)")
    attribution = src["attribution"]
    fixed = sum(len(x) + 2 for x in head) + len(attribution) + 2
    blurb = _clean(body, max(SYNC_DESCRIPTION_MAX - fixed - 4, 120)) or None   # 4: " ..."
    desc = "\n\n".join(head + ([blurb] if blurb else []) + [attribution])

    promoter = _by_path(path, src.get("promoters"), "promoter", src.get("promoter"))
    category = _by_path(path, src.get("categories"), "category", src.get("category", "community"))
    day = sl.strftime("%Y-%m-%d")
    hm = sl.strftime("%H:%M")
    row = NormalizedEvent(
        source=src.get("source") or f"drupal-fullcalendar:{src['key']}",
        # Lesson 6: each occasion is its own node, so the node id IS the
        # occasion; the date joins it so a node re-dated to another day is a
        # new row, not a silently moved one.
        source_id=f"{src['key']}:{ev.get('id')}:{day}",
        name=title,
        description=desc,
        start_local=sl.strftime("%Y-%m-%dT%H:%M:00"),
        start_utc=_utc(sl, tz),
        end_local=el.strftime("%Y-%m-%dT%H:%M:00") if el else None,
        end_utc=_utc(el, tz) if el else None,
        timezone=src.get("timezone"),
        venue_name=venue,
        latitude=lat, longitude=lon,
        # The publisher's own pin for its own park or centre (or the venue
        # book's surveyed point); a street-only row is geocoded by the sync.
        coords_exact=exact,
        address=address,
        city=city or src.get("city"),
        region=region or src.get("region"),
        country=src.get("country"),
        postal_code=postal,
        category=category,
        promoter=promoter,
        ticket_url=reg_url or page_url,
    )
    # Lesson 6: the clock joins the basis - two "House of Reptiles" on one day.
    row.fingerprint = make_fingerprint(f"{title} {hm}", day, f"{venue} {address or ''}".strip())
    return row


def ingest_source(store: EventStore, reader: Reader, src: Dict[str, Any], days: int,
                  stats: Dict[str, int]) -> int:
    tz = ZoneInfo(src["timezone"])
    today = _today(tz)
    last = today + timedelta(days=days)
    cal_url, page = reader.get(src["calendar_url"])
    events = calendar_events(page)
    stats["calendar events"] = len(events)
    todo = []
    for ev in events:
        start = _parse_iso(ev.get("start"))
        if start is None or not ev.get("url"):
            _bump(stats, "skipped: no start or no url")
            continue
        sl = _local(start, tz)
        if not (today <= sl.date() <= last):
            _bump(stats, "outside the window")
            continue
        _bump(stats, "in the window")
        end = _parse_iso(ev.get("end"))
        # Lesson 6: a day or more of clock time is a series written as one row
        # ("Botanical Journaling" 10-07 13:30 to 10-21 15:30); an overnight
        # occasion shorter than that (a 21-hour Family Campout) is one occasion.
        if (end is not None and len(str(ev.get("end"))) > 10
                and _local(end, tz) - sl >= timedelta(hours=SERIES_HOURS)):
            _bump(stats, "excluded: a timed span of a day or more (a series as one row)")
            continue
        why = json_exclusion(ev, src)
        if why:
            _bump(stats, f"excluded: {why}")
            live = called_off(_clean(ev.get("title")).replace("\n", " ")) if why == "closure or cancellation" else None
            if live and json_exclusion(dict(ev, title=live), src) is None:
                todo.append(dict(ev, title=live, _called_off=_clean(ev.get("title"))))
            continue
        todo.append(ev)
    cap = src.get("max_pages")
    if cap is not None and len(todo) > cap:
        _bump(stats, "not read: over max_pages", len(todo) - cap)
        todo = todo[:cap]
    written = 0
    stats["rows written"] = 0                    # kept current, so a STOPPED log line is true
    cal_origin = robots_txt.origin_of(cal_url)
    for ev in todo:
        path = ev["url"]
        url = detail_url(cal_url, path, src.get("detail_rewrite") or [])
        try:
            got = _read_page(reader, url, urljoin(cal_url, path), stats)
        except OutOfTime:
            raise
        except Refused as exc:
            # The calendar's host said no (a 401/403/429 or a challenge): the
            # run stops asking it. A robots.txt Disallow on one path, or a
            # refusal from a host a redirect led to, costs that page only.
            if cal_origin in reader.refused:
                raise
            _bump(stats, "dropped: page refused (robots.txt or a redirect's host)")
            print(f"[drupal-fullcalendar]     page refused {url}: {exc}")
            continue
        except Exception as exc:  # noqa: BLE001 - a 5xx after retries, a timeout, a redirect loop
            _bump(stats, "dropped: page failed (5xx, timeout or redirects)")
            print(f"[drupal-fullcalendar]     page failed {url}: {type(exc).__name__}: {exc}")
            continue
        if got is None:
            continue
        page_url, body = got
        info = parse_event_page(body, src.get("page_selectors"))
        notice = ev.get("_called_off")
        # A called-off occasion is built like a live one (its counts kept
        # apart, so the live tallies stay true) and tombstoned.
        row = build_event(ev, info, page_url, src, tz, {} if notice else stats)
        if row is None:
            continue
        if notice:
            if store.cancel(row, notice[:80], notice=True) == "cancelled":
                _bump(stats, "rows cancelled (a called-off title: a tombstone for the row we wrote)")
            continue
        store.upsert(row)
        written += 1
        stats["rows written"] = written
    # THE WHOLE CALENDAR WAS READ, and every occasion in the window either built
    # (or tombstoned) from its page or was refused by a rule: an occasion the
    # last complete read wrote and this one did not is gone from the calendar
    # (a node deleted or re-dated), and mapsee_supabase_sync --retire-absent
    # may cancel it. A page lost to a refusal, a failure, a 404 or the
    # max_pages cap leaves the read incomplete; a deadline or a refusal from
    # the calendar's own host raises past this line.
    lost = [k for k in INCOMPLETE_STATS if stats.get(k)]
    if not events:
        # A calendar that lists nothing is a broken page or a moved setting,
        # not a county that called everything off.
        lost.append("the calendar listed nothing")
    if lost:
        _bump(stats, "read NOT complete: " + ", ".join(lost))
    else:
        store.mark_complete(src.get("source") or f"drupal-fullcalendar:{src['key']}", today, last)
        stats["read complete (absence may cancel)"] = 1
    return written


def _read_page(reader: Reader, url: str, own: str, stats: Dict[str, int]) -> Optional[Tuple[str, str]]:
    """The agency's page, else the calendar's own copy when the agency's is
    gone; None (counted) when both are."""
    try:
        return reader.get(url)
    except NotFound:
        if own == url:
            _bump(stats, "dropped: page gone (404)")
            return None
    _bump(stats, "agency page 404: read the calendar's own page")
    try:
        return reader.get(own)
    except NotFound:
        _bump(stats, "dropped: page gone (404)")
        return None


def load_config(path: str) -> Dict[str, Any]:
    cfg = json.loads(open(path, encoding="utf-8").read())
    for src in cfg.get("sources", []):
        for k in ("key", "calendar_url", "timezone", "attribution"):
            if not src.get(k):
                raise ValueError(f"source {src.get('key') or '?'} needs {k}")
        if len(src["attribution"]) > ATTRIBUTION_MAX:
            raise ValueError(f"{src['key']}: attribution must be <= {ATTRIBUTION_MAX} characters "
                             "(the sync keeps a final paragraph only that short)")
        for v in src.get("venues") or []:
            if not (v.get("path") and isinstance(v.get("lat"), (int, float)) and isinstance(v.get("lon"), (int, float))):
                raise ValueError(f"{src['key']}: a venue-book entry needs path, lat and lon: {v}")
            if not _in_box((v["lat"], v["lon"]), src.get("bbox")):
                raise ValueError(f"{src['key']}: venue {v.get('name')} lies outside bbox")
        for v in src.get("street_pins") or []:
            if not (_STREET_RX.match(v.get("street") or "") and isinstance(v.get("lat"), (int, float))
                    and isinstance(v.get("lon"), (int, float))):
                raise ValueError(f"{src['key']}: a street pin needs a numbered street, lat and lon: {v}")
            if not _in_box((v["lat"], v["lon"]), src.get("bbox")) or any(
                    _metres((v["lat"], v["lon"]), (c["lat"], c["lon"])) <= CENTRE_RADIUS_M
                    for c in src.get("map_centres") or []):
                raise ValueError(f"{src['key']}: street pin {v.get('street')} lies outside bbox or on a map centre")
    return cfg


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import Drupal fullcalendar_view event calendars into the Mapsee store.")
    ap.add_argument("--config", required=True)
    ap.add_argument("--store", default="mapsee_events.json")
    ap.add_argument("--only", help="read just the source with this key")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS, help="how far ahead to read (days)")
    ap.add_argument("--max-minutes", type=float, default=DEFAULT_MAX_MINUTES,
                    help="whole-run deadline; no request starts after it, and the store is "
                         "saved after every source (0 = none)")
    a = ap.parse_args(argv)

    started = time.monotonic()
    cfg = load_config(a.config)
    session = requests.Session()
    session.headers.update({"User-Agent": UA})
    reader = Reader(session, deadline=started + a.max_minutes * 60 if a.max_minutes else None)
    store = EventStore(a.store)
    total, failed, ran = 0, 0, 0
    unsaved, saved = False, False
    for src in cfg.get("sources", []):
        if a.only and src["key"] != a.only:
            continue
        if src.get("enabled") is False:
            continue
        ran += 1
        label = src.get("name") or src["key"]
        before = reader.requests
        stats: Dict[str, int] = {}
        unsaved = True
        try:
            ingest_source(store, reader, src, a.days, stats)
        except Refused as exc:
            failed += 1
            print(f"[drupal-fullcalendar] {label} REFUSED: {exc} - not retried")
        except OutOfTime as exc:
            print(f"[drupal-fullcalendar] {label} STOPPED: {exc} (--max-minutes {a.max_minutes:g}); "
                  f"{stats.get('rows written', 0)} rows kept so far")
        except Exception as exc:  # noqa: BLE001 - one source never stops the others
            failed += 1
            print(f"[drupal-fullcalendar] {label} FAILED: {type(exc).__name__}: {exc}")
        n = stats.get("rows written", 0)
        total += n
        print(f"[drupal-fullcalendar] {label}: {n} rows, "
              f"{stats.get('rows cancelled (a called-off title: a tombstone for the row we wrote)', 0)} cancelled, "
              f"in {reader.requests - before} requests")
        for k, v in sorted(stats.items()):
            if k != "rows written":
                print(f"[drupal-fullcalendar]     {k}: {v}")
        store.save()
        unsaved, saved = False, True
        if reader.deadline is not None and reader.clock() >= reader.deadline:
            break
    if unsaved or not saved:
        store.save()
    st = store.stats
    print(f"[drupal-fullcalendar] done in {(time.monotonic() - started):.0f} s: {total} rows written "
          f"(added {st.get('added', 0)}, updated {st.get('updated', 0)}, merged {st.get('merged', 0)}, "
          f"rejected {st.get('rejected', 0)}) in {reader.requests} requests; store now holds "
          f"{len(store.records)} unique events.")
    # Like the siblings (toronto_rec, perfectmind, linkedevents): a failed or
    # refused source is reported in the log, never a red step - the workflow
    # runs every ingest step failure-tolerant and the sync still has to run.
    if ran and failed == ran:
        print("[drupal-fullcalendar] every source failed this run")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
