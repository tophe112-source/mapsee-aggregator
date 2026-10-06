#!/usr/bin/env python3
"""
mapsee_ingest_lcsd.py - what is on at Hong Kong's community places: the
walk-in sports sessions of the Leisure and Cultural Services Department
(SmartPlay) and the cultural programme at its town halls, civic centres,
theatres and museums.

    python mapsee_ingest_lcsd.py --config lcsd_sources.json \
        --store feeds_events.json [--max-minutes 6]

Both sources are LCSD's own, published on DATA.GOV.HK under its terms of use
(free re-use, commercial included, with attribution):
  - "Community Recreation and Sports Programmes (SmartPlay)": one 12 MB JSON
    file, rebuilt every 10 minutes (data.smartplay.lcsd.gov.hk; robots.txt is
    a 404, which RFC 9309 reads as allow-all);
  - "Cultural Programmes": events.xml + eventDates.xml + venues.xml +
    holiday.xml under www.lcsd.gov.hk/datagovhk/, which robots.txt allows;
  - the venue points come from LCSD's own facility lists, facility-sc.json
    (sports centres) and facility-sg.json (sports grounds), same host.
Seven requests a run plus two robots.txt reads; every host is paced to
>= 1.1 s and its Crawl-delay, under the MapseeAggregator UA.

WHAT IT GIVES (the files as served 2026-10-05 16:57 UTC, a 90-day window):
2,863 rows from 9 requests, 2,859 records once the store folds 4 performances
LCSD lists twice in the same room. The cultural programme 854 rows (379 free
in 0227's reading, 454 ticketed, 21 unpriced); SmartPlay 2,009 walk-in
sessions, all free. ../mapsee's 0227 twin, run on the rows the sync's to_row
writes, agreed with the adapter's free/paid line on every row. Wall time: 59 s
with a warm geocode cache; a cold one has taken 310 s (Photon at ~8 s a
lookup from a sandbox), which is why the cultural programme is read first.

------------------------------------------------------------------------------
SMARTPLAY: A BALLOT CATALOGUE WITH A WALK-IN TIMETABLE INSIDE IT
------------------------------------------------------------------------------

1. ONLY ENROL_METHOD == "WALKIN" IS A SESSION ANYONE CAN TURN UP TO. Read whole
   the file is a registration catalogue: 6,308 programmes on 2026-10-05, and
   of the 6,049 that touch the window 5,274 are BALLOT and 306 first-come
   enrolment (FCFS) - courses you apply for weeks ahead, out and counted by
   method - against 469 WALKIN (16:57 UTC file; 431 at 05:00). Every
   walk-in row measured is FEE 0, but the fee is still read: a FEE above 0
   says its amount and "(not free)", and an EN_NOTES line that names an
   admission fee ("Public swimming pool admission fee is required", Hydro
   Fitness) says the programme is free but the pool's fee applies (not free),
   which 0227's FREE_NEG reads as a veto. Otherwise the row says "Admission:
   free.", on the source's own FEE field.

2. ONE ROW PER SESSION, ON ITS DATE. A programme is a window
   (PGM_START_DATE..PGM_END_DATE), weekdays (EN_DAY "Mon,Wed (Exclude 19 Oct
   2026)") and one clock. It is expanded to its dates inside today..today +
   horizon_days; the "(Exclude d Mon yyyy, ...)" dates are not written. The
   data carries LCSD's own holidays that way: of 45 walk-in sessions that fell
   on 2026-10-19 (the day after Chung Yeung), 44 were excluded by their own
   EN_DAY. An EN_DAY that is not a list of weekdays, an Exclude clause with no
   readable date, and a one-day programme whose weekday contradicts its date
   are refused, not guessed. Sessions of one title at one venue on one day are
   folded when repeated and joined when back to back (toronto_rec's rule), and
   never span a gap: live, 374 back-to-back hours joined ("Badminton" 07-08
   and 08-09 at one centre is one pin), 19 repeats folded, 11 title-days split
   (a pool's 11:00 and 20:00 hydro-fitness hours are two rows, never one
   11-22). 101 dates were left out on their own Exclude clause. Identity is
   ACTIVITY_NO#date of the stretch's first session.

3. THE POINT IS LCSD'S, UNLESS ITS OWN DISTRICT SAYS OTHERWISE. EN_VENUE is
   joined (parentheticals dropped, "&" read as "and", `venue_aliases`) to
   Name_en in the facility lists, whose Latitude/Longitude are DMS strings
   ("22-18-53", "114-9-60" - sixty seconds is a whole minute, and the
   arithmetic handles it). Six of the 141 points contradict their own
   district (2026-10-05): Sham Shui Po Sports Centre's longitude is
   "144-14-90" (3,000 km off), Tai Kiu Market and Yuen Long Jockey Club
   Squash Courts (Yuen Long) sit in Kowloon, Tung Cheong Street Sports Centre
   (Tai Po) in Kowloon, Choi Wing Road Sports Centre (Kwun Tong) in Sha Tin
   and Sir Denys Roberts Squash Courts (Yuen Long) in Fanling. A point is
   refused when strictly more of its district's other facilities lie beyond
   `district_check_km` (8) than within it - Islands district, Lantau to
   Cheung Chau, gets 20 - and that refuses exactly those six. A venue the
   lists do not name is pinned at the one listed facility whose name is its
   name plus more, in its district ("Kowloon Park" -> Kowloon Park Sports
   Centre; 18 programmes): Photon answers "Kowloon Park, Hong Kong" with
   Kowloon BAY Park and "Hong Kong Park, Hong Kong" with Ocean Park. The rest
   (parks, playgrounds, estates' courts) are geocoded the way
   mapsee_ingest_ics geocodes a LOCATION, with ", Hong Kong", and an answer
   is kept only inside the SAR's box and within `geocode_check_km` (3) of
   the nearest facility of the row's own EN_DISTRICT: of 55 geocoded venues
   every right answer was within 2.5 km (Islands' Tai O 9.0, under its 20),
   the two wrong ones 3.7 and 4.9 km. So a SmartPlay geocode is kept only
   when its district HAS facilities to ask: EN_DISTRICT is sometimes the
   venue again ("Tuen Mun Recreation & Sports Centre", 3 sessions, unplaced
   and counted), and when the facility lists cannot be read at all SmartPlay
   is not read that run - with no district to check against, both of Photon's
   wrong parks would pass the SAR's box. Live: 332 programmes on a facility
   point, 18 inside a park, 111 geocoded, 8 (25 sessions) unplaced and
   named in the run's output. Only the facility lists' points are
   coords_exact.

4. CATEGORY FROM THE TITLE. 273 of 338 walk-in programmes measured on
   2026-10-03 were for the Elderly (60+): table tennis, badminton, gateball,
   lawn bowls (sports), tai chi, baduanjin, fitness and dance (fitness), bird
   watching and orienteering (outdoors), carnivals (community). Live, after
   derive_categories: sports 1,211, fitness 664, outdoors 22, community 6,
   learning 1; none reaches the party door. The age band is the second
   paragraph. LCSD pastes the stock note of its ball-games COURSES ("...
   training courses ... during the class") onto walk-in rows (399 live); it
   is dropped, because it describes something the row is not.

------------------------------------------------------------------------------
CULTURAL PROGRAMMES: THREE FILES, AND THE DATES FILE OVERSTATES
------------------------------------------------------------------------------

5. eventDates.xml LISTS EVERY DAY OF A WEEKLY CLASS'S SEASON. "(Buddhism
   Class)" says "13 Jul- 14 Dec 2026 (Every Mon) 1930-2130" and has 155 dates,
   every weekday of the season; "Bee's Drawing Classes" (Every Fri) has 169.
   Every "(Every <day>)" programme measured is a hirer's class season - dance,
   Cantonese opera, guzheng, calligraphy, choir (186 events under inc4sc14 and
   inc4sc8) - so a weekly series is refused as a course (273 live), and so is
   a title that says class, course or training (14), a price that says
   "Admission by enrollment" (5), and a description that says the same in
   other words - "Free admission by registration: <link>", "Online
   registration starts from 31.8", "Successful registrants will be notified"
   (5: masterclasses with a quota you apply for). 18dART's community-arts workshops
   ("15/10/2026-28/01/2027", "Each workshop lasts approximately 1 hour", a
   class for ages 6 to 12) give no clock and no place ("Venues in Eastern
   district", 51 events at a district rather than a venue) and are not
   written.

6. THE CLOCK IS IN predateE, AND IT IS PROSE. "16-18/10/2026 (Fri-Sun) 19:30 /
   17-18/10/2026 (Sat-Sun) 14:30", "14/11/2026 (Sat) 19:30, 15/11/2026 (Sun)
   15:00", "9/10/2026(Fri)-14/10/2026(Wed) 10am-8pm; 15/10/2026(Thu)
   10am-4:30pm". Each date phrase starts a group and the times after it belong
   to it. ONLY THE DATES THE TEXT NAMES ARE WRITTEN: eventDates.xml lists
   all 50 days from the first to the last of "Music at Heart"'s 8 Wednesday
   lectures (187979), and lending the text's 19:30 to every listed day wrote
   42 lectures that do not exist; a day the text does not name is dropped and
   counted (42 live). The grammar reads "24, 29-31/10, 5-7/11/2026" (a list
   across two months), "8/11/ 2026", and dotted "22.9.2026" and
   "10.9-14.10.2026"; misread, they gave Border Town (187116) a 15:00 and a
   20:00 show on each of its 9 nights, and an installation (185799) its two
   exception days' hours on every day. Two date phrases share the clock after
   them only when nothing but a separator stands between them, so "...
   14.10.2026* *Opening hours of the following days: 22.9.2026 19:00-23:00"
   does not lend 22.9's hours to the run. Only a text with no clock after any
   phrase ("19:30 on 14/11/2026") lends its clock to the dates it names, and
   a text that names no readable date lends it to a single listed day only.
   A performance is one row per date per start, ending at progtimee's
   duration when it states one. Opening hours (one span a day, on every day
   that has a clock: 4 hours or more, or any span for an exhibition, whose
   opening day may run 16:00-18:30) over a run of CONSECUTIVE dates is an
   exhibition, a lantern carnival: one multi-day, date-only row per run, hours
   in the text (../mapsee draws a date-only window as two dates, never a
   clock). Runs are cut from the event's whole date list, not the window, so a
   row's identity does not move as days pass. A shorter daily span ("30-31/10
   19:30-21:30") is a show and stays one row a night. An exhibition whose text
   gives dates and no hours is a date-only run that says so; any other event
   with no clock is refused (3 live). "14/11/2026 (Sat) 19:30, 15/11/2026"
   must not read "30, 15/11" as a date list: no date phrase may start after a
   digit, ':' or '/'. Live: 822 dated rows and 32 multi-day runs.

7. PRICE. pricee "Free Activities" / "Free admission ..." -> "Admission: free."
   ; a dollar list -> "Tickets (HK$): ... (not free)."; empty -> "Price not
   stated" (tags nothing). Titles are English (titlee), Chinese when that is
   all there is; 0227 reads no Chinese, so nothing Chinese is relied on.

8. KEEP ALL OF IT, NOT ONLY THE DISTRICT HALLS. Of the 948 rows of the
   05:00 file (before the date fixes above), 392 were at the town halls and
   civic centres - the community places proper, 272 of them free (69%) - and
   556 at LCSD's other venues (City Hall, the Cultural Centre, Ko Shan
   Theatre, East Kowloon Cultural Centre, the Space Museum), 154 free (28%)
   and 400 ticketed. All are public venues of one
   publisher under one licence in one request, every price is stated, and
   Hong Kong had almost nothing else on the map (one HKU calendar). The
   config's `venue_include` regex narrows it to the halls if that changes.
   These rows are a cultural agenda, not a timetable: their prefix,
   lcsd-culture, stays out of the sync's CIVIC_TIMETABLE_SOURCES.

9. PLACE. venues.xml's lat/lon (70 of 96 venues) are LCSD's: coords_exact.
   "Venues in Eastern district" is a district, not a place: unplaced. Named
   venues without a point (Hong Kong Central Library, the Coliseum) are tried
   against the facility lists, then geocoded inside the SAR's box.
   holiday.xml (venue closures) was an empty <holiday /> on 2026-10-05; a run
   that finds entries in it says so loudly rather than guess its schema.

10. THE ROOM IS PART OF THE PLACE. The fingerprint is title + start + date +
   venuee WITH its room: "Cantonese Opera Excerpts" at 14:15 on 1/11 in Ko
   Shan Theatre's Theatre ($50, $20, $10) and in its New Wing Auditorium
   (free, another troupe) are two shows, and a building-only fingerprint
   kept the ticketed one and lost the free one; 7 of the 10 pairs it folded
   were like that. 4 pairs fold now, each one show listed twice in one room
   under two cat2 codes (Musicus Fest at City Hall's Theatre, x3; an opera
   concert at Tai Po Civic Centre). venue_name stays the building.

------------------------------------------------------------------------------
THE ROW
------------------------------------------------------------------------------

The price line is the FIRST paragraph and the description fits the sync's 800
characters (DESCRIPTION_MAX), so _cap_prose never cuts "(not free)"; the
attribution is the last paragraph and under 200 characters (TAIL_KEEP_MAX).
A 401/403/429, a bot challenge or a robots.txt refusal ends that host's
reading for the run and is never retried; a 5xx or a timeout is retried
twice. --max-minutes is a deadline no request starts after AND no transfer
runs past: requests' timeout bounds each read, not a 12 MB body (45 s
measured), so a body is streamed and abandoned at the deadline, and each read
waits no longer than the time left (never under 10 s). The cultural programme
is read first (4 requests, ~6 geocodes), so SmartPlay's transfer and its
geocodes can never starve it. The store is saved after each source, and one
source failing never stops the other.
"""
from __future__ import annotations

import argparse
import html as _html
import json
import math
import os
import re
import sys
import time
import unicodedata
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import urljoin

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    sys.exit("This script needs Python 3.9+ (zoneinfo).")

import robots_txt
from mapsee_ingest import EventStore, NormalizedEvent, make_fingerprint, norm_categories

UA = "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)"
MIN_INTERVAL_S = 1.1
# The SmartPlay file is 12 MB; measured 2026-10-03 at ~30 s from Europe.
REQUEST_TIMEOUT_S = 120
DEFAULT_MAX_MINUTES = 6.0
MAX_REDIRECTS = 3
JOIN_MINUTES = 5
_RETRYABLE = {408, 500, 502, 503, 504}
_REFUSALS = {401, 403, 429}
ATTRIBUTION_MAX = 200
# mapsee_supabase_sync.DESCRIPTION_MAX: past it _cap_prose trims the head from
# its end. Our own lines are written first and the source's text gets the room
# they leave, so the sync never has to cut.
DESCRIPTION_MAX = 800
SOURCE_TEXT_MIN = 60
DISTRICT_CHECK_KM = 8.0
# SmartPlay is read only when the facility lists give accepted points in at
# least this many of Hong Kong's 18 districts (18 on 2026-10-05).
SMARTPLAY_MIN_DISTRICTS = 15
GEOCODE_CHECK_KM = 3.0
SENTINEL_DATE = "20990101"
# A daily span this long is opening hours (an exhibition, a lantern garden),
# and a run of such days is one multi-day row; a shorter one is a show.
OPENING_HOURS_MIN = 240
# An exhibition by its category (cat2 inc4sc4) or its own title.
EXHIBITION_CATS = {"inc4sc4"}
EXHIBITION_RX = re.compile(r"\bexhibitions?\b", re.I)


class Refused(Exception):
    """The publisher said no (401/403/429, a challenge, robots.txt). Never
    retried; the host is asked nothing more this run."""


class OutOfTime(Exception):
    """--max-minutes has passed: no request starts after it."""


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
class Reader:
    """Paced, robots-checked GETs, per origin. Counts its own requests,
    robots.txt reads included."""

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
        self.refused: Dict[str, str] = {}
        self._last: Dict[str, float] = {}

    def _allowed(self, url: str) -> float:
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
            why = (f"robots.txt ({verdict.get('status')}) refuses "
                   f"{robots_txt.request_path(url)}: {verdict.get('rule')}")
            self.refused[origin] = why
            raise Refused(why)
        return max(self.min_interval, float(verdict.get("crawl_delay") or 0))

    def get(self, url: str) -> bytes:
        """The body of a 200. Redirects are followed by hand, each hop
        robots-checked."""
        for _hop in range(MAX_REDIRECTS + 1):
            r, body = self._get_once(url)
            if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                url = urljoin(url, r.headers["location"])
                continue
            return body
        raise RuntimeError(f"more than {MAX_REDIRECTS} redirects from {url}")

    def _timeout(self):
        """(connect, read). requests' read timeout bounds the wait for each
        chunk, not the transfer, so the read wait is also held to the time left
        before the deadline (never under 10 s), and _body() checks the clock
        between chunks: a 12 MB file trickling in cannot carry the run past
        --max-minutes by more than one wait."""
        read = self.timeout
        if self.deadline is not None:
            read = max(10.0, min(read, self.deadline - self.clock()))
        return (min(15.0, read), read)

    def _body(self, r, url: str) -> bytes:
        chunks = getattr(r, "iter_content", None)
        if chunks is None:
            return r.content or b""
        got: List[bytes] = []
        n = 0
        try:
            for chunk in chunks(1 << 16):
                got.append(chunk)
                n += len(chunk)
                if self.deadline is not None and self.clock() >= self.deadline:
                    raise OutOfTime(f"run deadline reached while reading {url} ({n:,} bytes in); "
                                    "the transfer was abandoned")
        finally:
            close = getattr(r, "close", None)
            if close is not None:
                close()
        return b"".join(got)

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
                r = self.session.get(url, timeout=self._timeout(), allow_redirects=False, stream=True)
                if r.status_code < 500:
                    body = self._body(r, url)
                else:
                    body = b""
                    getattr(r, "close", lambda: None)()
            except OutOfTime:
                raise
            except Exception as exc:  # noqa: BLE001 - timeouts, resets
                last = exc
            else:
                if r.status_code in _REFUSALS:
                    self.refused[origin] = f"HTTP {r.status_code} from {origin}"
                    raise Refused(self.refused[origin])
                head = body[:6000].decode("utf-8", "replace")
                if robots_txt.CHALLENGE_RX.search(head):
                    self.refused[origin] = f"a bot challenge or block page from {origin}"
                    raise Refused(self.refused[origin])
                if r.status_code == 200 or 300 <= r.status_code < 400:
                    return r, body
                if r.status_code not in _RETRYABLE:
                    raise RuntimeError(f"HTTP {r.status_code} from {url}")
                last = RuntimeError(f"HTTP {r.status_code}")
            finally:
                self._last[origin] = self.clock()
            if attempt + 1 < self.tries:
                self.sleep(2 * (attempt + 1))
        raise last


def _json(body: bytes) -> Any:
    return json.loads(body.decode("utf-8-sig"))


# ---------------------------------------------------------------------------
# small readers
# ---------------------------------------------------------------------------
def _s(v: Any) -> str:
    if v is None:
        return ""
    return re.sub(r"\s+", " ", _html.unescape(str(v)).replace("\xa0", " ")).strip()


def _text(v: Any) -> str:
    """HTML-free, entity-free, one-line text."""
    return _s(re.sub(r"<[^>]+>", " ", _html.unescape(str(v or ""))))


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", (s or "").replace("’", "'").replace("‘", "'"))
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", _fold(s))


def _bump(stats: Dict[str, int], key: str, n: int = 1) -> None:
    stats[key] = stats.get(key, 0) + n


def _today(tz) -> date:
    """MAPSEE_TODAY=YYYYMMDD fixes "today" for the tests; else today in Hong Kong."""
    fixed = os.environ.get("MAPSEE_TODAY")
    if fixed:
        return datetime.strptime(fixed, "%Y%m%d").date()
    return datetime.now(tz).date()


def _stamp(d: date, minutes: int, tz) -> Tuple[str, str]:
    local = datetime(d.year, d.month, d.day) + timedelta(minutes=int(minutes))
    utc = local.replace(tzinfo=tz).astimezone(timezone.utc)
    return local.strftime("%Y-%m-%dT%H:%M:00"), utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def hm(minutes: int) -> str:
    h, m = divmod(int(minutes), 60)
    return f"{h:02d}:{m:02d}"


def _hhmm(v: Any) -> Optional[int]:
    m = re.match(r"^\s*(\d{1,2}):(\d{2})(?::\d{2})?\s*$", _s(v))
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    return h * 60 + mi if h <= 24 and mi < 60 else None


def _km(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    return 6371.0 * math.hypot((lo2 - lo1) * math.cos((la1 + la2) / 2), la2 - la1)


def in_box(p: Optional[Tuple[float, float]], box: Optional[List[float]]) -> bool:
    if p is None:
        return False
    if not box:
        return True
    s, w, n, e = box
    return s <= p[0] <= n and w <= p[1] <= e


def dms(v: Any) -> Optional[float]:
    """'22-18-53' -> 22.31472. Degrees-minutes-seconds as LCSD writes them,
    sixty seconds included ('114-9-60' is 114-10-00); a plain decimal passes."""
    s = _s(v)
    if re.fullmatch(r"-?\d+(?:\.\d+)?", s):
        return float(s)
    parts = re.findall(r"\d+(?:\.\d+)?", s)
    if len(parts) != 3:
        return None
    d, m, sec = (float(x) for x in parts)
    if m > 60 or sec > 60:
        return None
    return round(d + m / 60.0 + sec / 3600.0, 6)


def venue_key(name: Any) -> str:
    """'Kowloon Park (Sculpture Walk)' -> 'kowloon park'; '&' reads as 'and'."""
    s = _fold(_s(name)).replace("&", " and ")
    s = re.sub(r"\([^)]*\)", " ", s)
    s = re.sub(r"[\"'“”‘’]", "", s)
    return re.sub(r"\s+", " ", s).strip(" ,.")


def district_key(name: Any) -> str:
    s = venue_key(name)
    return re.sub(r"\s+district$", "", s)


def geocode_query(venue: str) -> str:
    """The landmark a SmartPlay venue string names, for the geocoder:
    parentheticals and quotes dropped, 'Piazza outside X' / 'Open Space near X'
    -> X, an estate's long court description cut after 'Estate'."""
    s = re.sub(r"\([^)]*\)", " ", _s(venue))
    s = re.sub(r"[\"“”]", "", s)
    m = re.search(r"\b(?:outside|near|next to|beside)\s+(.+)$", s, re.I)
    if m:
        s = m.group(1)
    m = re.match(r"^(.*?\bEstate)\b", s)
    if m:
        s = m.group(1)
    return re.sub(r"\s+", " ", s).strip(" ,.")


# ---------------------------------------------------------------------------
# places: LCSD's facility lists, then the geocoder
# ---------------------------------------------------------------------------
class Places:
    """Facility points by venue_key, each checked against its own district;
    a geocoder for the rest, checked the same way."""

    def __init__(self, facilities: Iterable[Dict[str, Any]], cfg: Dict[str, Any],
                 geocode: Optional[Callable[[str], Tuple[Any, Any]]] = None,
                 stop: Optional[Callable[[], bool]] = None) -> None:
        self.cfg = cfg
        self.stop = stop
        self.box = cfg.get("bbox")
        self.radius = float(cfg.get("district_check_km") or DISTRICT_CHECK_KM)
        self.geocode_radius = float(cfg.get("geocode_check_km") or GEOCODE_CHECK_KM)
        self.radius_by = {district_key(k): float(v) for k, v in (cfg.get("district_radius_km") or {}).items()}
        self.aliases = {venue_key(k): venue_key(v) for k, v in (cfg.get("venue_aliases") or {}).items()}
        self.geocode = geocode
        self.book: Dict[str, Dict[str, Any]] = {}
        self.refused: Dict[str, str] = {}
        self._geo: Dict[Tuple[str, str], Optional[Tuple[float, float]]] = {}
        self.notes: Dict[str, int] = {}
        raw: List[Dict[str, Any]] = []
        for f in facilities:
            name = _s(f.get("Name_en"))
            lat, lon = dms(f.get("Latitude")), dms(f.get("Longitude"))
            raw.append({"name": name, "key": venue_key(name), "district": _s(f.get("District_en")),
                        "address": _text(f.get("Address_en")) or None, "phone": _s(f.get("Phone")) or None,
                        "point": (lat, lon) if lat is not None and lon is not None else None})
        boxed = [r for r in raw if in_box(r["point"], self.box)]
        self.by_district: Dict[str, List[Tuple[float, float]]] = {}
        for r in boxed:
            self.by_district.setdefault(district_key(r["district"]), []).append(r["point"])
        for r in raw:
            why = None
            if r["point"] is None:
                why = "no readable point"
            elif not in_box(r["point"], self.box):
                why = "point outside Hong Kong"
            else:
                peers = [p for p in self.by_district.get(district_key(r["district"]), []) if p != r["point"]]
                if self.contradicted(r["point"], r["district"], peers):
                    why = f"point contradicts its district ({r['district']})"
            if why:
                self.refused[r["key"]] = why
                r = dict(r, point=None)
            self.book.setdefault(r["key"], r)
        # Only accepted points vouch for a district from here on.
        self.by_district = {}
        for r in self.book.values():
            if r["point"] is not None:
                self.by_district.setdefault(district_key(r["district"]), []).append(r["point"])

    def contradicted(self, p: Tuple[float, float], district: str,
                     peers: Optional[List[Tuple[float, float]]] = None) -> Optional[bool]:
        """True when strictly more of the district's facilities lie beyond the
        radius than within it; None when the district has nobody to ask."""
        if peers is None:
            peers = self.by_district.get(district_key(district), [])
        if not peers:
            return None
        r = self.radius_by.get(district_key(district), self.radius)
        far = sum(1 for q in peers if _km(p, q) > r)
        return far * 2 > len(peers)

    def facility(self, venue: str) -> Optional[Dict[str, Any]]:
        k = venue_key(venue)
        k = self.aliases.get(k, k)
        if k in self.book:
            return self.book[k]
        q = venue_key(geocode_query(venue))
        q = self.aliases.get(q, q)
        return self.book.get(q)

    def inside(self, venue: str, district: Optional[str]) -> Optional[Dict[str, Any]]:
        """The one facility whose name is the venue's name plus more ('Kowloon
        Park' -> 'Kowloon Park Sports Centre'), in the same district: the
        session is in the park that building stands in. Photon answers
        'Kowloon Park, Hong Kong' with Kowloon BAY Park, 3.7 km off, and 'Hong
        Kong Park, Hong Kong' with Ocean Park (2026-10-05)."""
        q = venue_key(geocode_query(venue))
        if not q or not district:
            return None
        hits = [f for k, f in self.book.items() if k.startswith(q + " ") and f["point"] is not None
                and district_key(f["district"]) == district_key(district)]
        return hits[0] if len(hits) == 1 else None

    def far_from_district(self, p: Tuple[float, float], district: str) -> Optional[bool]:
        """A GEOCODED point must lie within geocode_check_km of the nearest of its
        district's facilities (None: the district has none to ask). Measured
        2026-10-05 on 55 geocoded venues: every right answer within 2.5 km
        (Islands' Tai O 9.0 km), the two wrong ones 3.7 and 4.9 km."""
        peers = self.by_district.get(district_key(district), [])
        if not peers:
            return None
        r = self.radius_by.get(district_key(district), self.geocode_radius)
        return min(_km(p, q) for q in peers) > r

    def geocoded(self, query: str, district: Optional[str], stats: Dict[str, int],
                 checked: bool = False) -> Optional[Tuple[float, float]]:
        """`checked`: the answer must be vouched for by its district's own
        facilities, and is refused when the district has none to ask (a
        SmartPlay venue: Photon's Kowloon BAY Park for Kowloon Park passes
        the SAR's box and fails only this)."""
        key = (query, district_key(district or ""))
        if key in self._geo:
            return self._geo[key]
        if checked and not self.by_district.get(district_key(district or "")):
            _bump(stats, "venues not geocoded: their district has no facility point to check an answer against")
            self._geo[key] = None
            return None
        out: Optional[Tuple[float, float]] = None
        if self.stop is not None and self.stop():
            # Not cached: the next run asks again.
            _bump(stats, "venues not geocoded: the run deadline passed")
            return None
        if self.geocode is not None and query:
            try:
                lat, lon = self.geocode(query)
            except Exception:  # noqa: BLE001 - a geocoder error leaves it unplaced
                lat, lon = None, None
            if lat is None or lon is None:
                _bump(stats, "venues the geocoder did not find")
            else:
                p = (round(float(lat), 6), round(float(lon), 6))
                if not in_box(p, self.box):
                    _bump(stats, "venues geocoded outside Hong Kong (refused)")
                elif district and self.far_from_district(p, district):
                    _bump(stats, "venues geocoded away from their district's facilities (refused)")
                else:
                    if not district or self.far_from_district(p, district) is None:
                        _bump(stats, "venues geocoded with no district to check against")
                    out = p
        self._geo[key] = out
        return out

    def place(self, venue: str, district: Optional[str], stats: Dict[str, int],
              checked: bool = False) -> Optional[Dict[str, Any]]:
        """{name, lat, lon, exact, address, phone} or None. `checked`: see
        geocoded()."""
        fac = self.facility(venue)
        if fac is not None and fac["point"] is not None:
            if district and district_key(fac["district"]) != district_key(district):
                _bump(stats, "venue's district differs between SmartPlay and the facility list (facility kept)")
            return {"lat": fac["point"][0], "lon": fac["point"][1], "exact": True,
                    "address": fac["address"], "phone": fac["phone"], "how": "facility list"}
        if fac is None:
            host = self.inside(venue, district)
            if host is not None:
                return {"lat": host["point"][0], "lon": host["point"][1], "exact": False,
                        "address": None, "phone": host["phone"],
                        "how": "facility list (a building inside it)"}
        query = fac["name"] if fac is not None else geocode_query(venue)
        p = self.geocoded(query, (fac or {}).get("district") or district, stats, checked)
        if p is None:
            return None
        return {"lat": p[0], "lon": p[1], "exact": False, "address": (fac or {}).get("address"),
                "phone": (fac or {}).get("phone"),
                "how": "geocoded (facility point refused)" if fac is not None else "geocoded"}


def load_facilities(reader: Reader, urls: Iterable[str]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for url in urls:
        rows = _json(reader.get(url))
        if not isinstance(rows, list):
            raise RuntimeError(f"{url} is not a list")
        out.extend(rows)
    return out


# ---------------------------------------------------------------------------
# the row's text
# ---------------------------------------------------------------------------
def compose(head: List[Optional[str]], source_text: str, tail: List[Optional[str]],
            stats: Dict[str, int]) -> str:
    """Our own paragraphs first (the price line is head[0]), the source's text
    in the room they leave under the sync's cap, the attribution last."""
    head_p = [p for p in head if p]
    tail_p = [p for p in tail if p]
    room = DESCRIPTION_MAX - len("\n\n".join(head_p + tail_p)) - 2
    src = _s(source_text)
    if src and len(src) > room:
        if room >= SOURCE_TEXT_MIN:
            src = src[:max(room - 2, 0)].rsplit(" ", 1)[0] + " …"
            _bump(stats, "source text shortened to fit the sync's cap")
        else:
            src = ""
            _bump(stats, "source text dropped: no room under the sync's cap")
    return "\n\n".join(head_p + ([src] if src else []) + tail_p)


def stretches(times: Iterable[Tuple[int, int]], join: int = JOIN_MINUTES
              ) -> List[Tuple[int, int, List[Tuple[int, int]]]]:
    out: List[List[Any]] = []
    for a, b in sorted(set(times)):
        if out and a <= out[-1][1] + join:
            out[-1][1] = max(out[-1][1], b)
            out[-1][2].append((a, b))
        else:
            out.append([a, b, [(a, b)]])
    return [(a, b, parts) for a, b, parts in out]


def _category(title: str, rules: List[List[str]], by_type: Dict[str, str], typ: str,
              default: str) -> str:
    t = _fold(title)
    for pattern, key in rules or []:
        if re.search(pattern, t):
            return key
    return (by_type or {}).get(typ) or default


# ---------------------------------------------------------------------------
# SmartPlay
# ---------------------------------------------------------------------------
WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8,
          "sep": 9, "oct": 10, "nov": 11, "dec": 12}
_DAY_TOKEN = re.compile(r"^(mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?(?:\s*-\s*(mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?)?$")
_EXCL_DATE = re.compile(r"\b(\d{1,2})\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?(?:\s+(\d{4}))?\b")


def parse_days(en_day: Any, lo: date, hi: date) -> Tuple[Optional[Set[int]], Set[date], Optional[str]]:
    """'Mon,Wed (Exclude 19 Oct 2026)' -> ({0, 2}, {2026-10-19}, None);
    (None, set(), why) when the pattern cannot be read whole."""
    text = _fold(_s(en_day))
    if not text:
        return None, set(), "no EN_DAY"
    main, _, rest = text.partition("(")
    days: Set[int] = set()
    for tok in [t.strip() for t in main.split(",") if t.strip()]:
        if tok in ("daily", "everyday", "every day"):
            days |= set(range(7))
            continue
        m = _DAY_TOKEN.match(tok)
        if not m:
            return None, set(), f"EN_DAY token not a weekday: {tok!r}"
        a = WEEKDAYS[m.group(1)]
        b = WEEKDAYS[m.group(2)] if m.group(2) else a
        d = a
        while True:
            days.add(d)
            if d == b:
                break
            d = (d + 1) % 7
    if not days:
        return None, set(), "EN_DAY names no weekday"
    excluded: Set[date] = set()
    if rest:
        if "exclud" not in rest:
            return None, set(), f"EN_DAY has an unread clause: ({rest.strip()[:40]}"
        found = list(_EXCL_DATE.finditer(rest))
        if not found:
            return None, set(), "an Exclude clause with no readable date"
        for m in found:
            month, day = MONTHS[m.group(2)], int(m.group(1))
            years = [int(m.group(3))] if m.group(3) else sorted({lo.year, hi.year})
            for y in years:
                try:
                    excluded.add(date(y, month, day))
                except ValueError:
                    return None, set(), f"an Exclude date that is not a date: {m.group(0)}"
    return days, excluded, None


def smartplay_dates(row: Dict[str, Any], today: date, horizon: date, stats: Dict[str, int]) -> List[date]:
    try:
        lo = date.fromisoformat(_s(row.get("PGM_START_DATE"))[:10])
        hi = date.fromisoformat(_s(row.get("PGM_END_DATE"))[:10])
    except ValueError:
        _bump(stats, "refused: unreadable programme dates")
        return []
    days, excluded, why = parse_days(row.get("EN_DAY"), lo, hi)
    if days is None:
        _bump(stats, f"refused: {why}")
        return []
    if lo == hi and lo.weekday() not in days:
        _bump(stats, "refused: a one-day programme whose weekday contradicts its date")
        return []
    out: List[date] = []
    d = max(lo, today)
    while d <= min(hi, horizon):
        if d.weekday() in days:
            if d in excluded:
                _bump(stats, "dates not written: excluded by the programme's own EN_DAY")
            else:
                out.append(d)
        d += timedelta(days=1)
    return out


# LCSD's stock note for its ball-games COURSES, pasted onto 76 of the walk-in
# programmes (2026-10-03): "Participants attending ball games training courses
# ... during the class". On a walk-in it describes something the row is not.
_COURSE_NOTE = re.compile(r"training courses?|during the class", re.I)


def _notes(row: Dict[str, Any], stats: Optional[Dict[str, int]] = None) -> List[str]:
    v = row.get("EN_NOTES")
    items = v if isinstance(v, list) else [v]
    out: List[str] = []
    for x in items:
        t = _text(x)
        if t and _COURSE_NOTE.search(t):
            if stats is not None:
                _bump(stats, "notes dropped: LCSD's stock note for its courses")
            continue
        if t and t not in out:
            out.append(t)
    return out


_FEE_NOTE = re.compile(r"admission fee|fee (?:is )?required|\bcharges?\b|\bfees? (?:apply|applies)", re.I)


def smartplay_price(row: Dict[str, Any]) -> Tuple[str, str]:
    """(tier, sentence). Free only on the source's FEE field, and never when a
    note names a fee the session still costs."""
    try:
        fee = float(row.get("FEE") or 0)
    except (TypeError, ValueError):
        return "unknown", "Fee not stated in the data."
    if fee > 0:
        amount = f"{fee:.2f}".rstrip("0").rstrip(".")
        return "fee", f"Fee: HK${amount} per session (not free)."
    said = next((n for n in _notes(row) if _FEE_NOTE.search(n)), None)
    if said:
        return "fee", f'No programme fee, but its note says "{said.rstrip(".")}" (not free).'

    return "free", "Admission: free."


def age_label(row: Dict[str, Any]) -> Optional[str]:
    try:
        lo = int(row.get("MIN_AGE")) if row.get("MIN_AGE") is not None else None
        hi = int(row.get("MAX_AGE")) if row.get("MAX_AGE") is not None else None
    except (TypeError, ValueError):
        return None
    if lo is not None and lo <= 0:
        lo = None
    if hi is not None and hi >= 99:
        hi = None
    if lo is None and hi is None:
        return None
    if hi is None:
        return f"ages {lo}+"
    if lo is None:
        return f"ages up to {hi}"
    return f"ages {lo}-{hi}"


_KIDS_WORDS = re.compile(r"\b(?:family|families|kids?|child(?:ren)?|parent-child|toddlers?)\b")


def smartplay_events(rows: List[Dict[str, Any]], src: Dict[str, Any], cfg: Dict[str, Any],
                     places: Places, tz, today: date, stats: Dict[str, int]) -> List[NormalizedEvent]:
    horizon = today + timedelta(days=int(src.get("horizon_days", 90)))
    groups: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = {}
    for row in rows:
        lo, hi = _s(row.get("PGM_START_DATE"))[:10], _s(row.get("PGM_END_DATE"))[:10]
        if hi < today.isoformat() or lo > horizon.isoformat():
            _bump(stats, "programmes outside the window")
            continue
        method = _s(row.get("ENROL_METHOD")).upper()
        if method != "WALKIN":
            _bump(stats, f"refused: enrolment by {method or 'an unstated method'} (a course you apply for)")
            continue
        _bump(stats, "walk-in programmes in the window")
        title = _s(row.get("EN_PGM_NAME")) or _s(row.get("TC_PGM_NAME"))
        venue = _s(row.get("EN_VENUE")) or _s(row.get("TC_VENUE"))
        a, b = _hhmm(row.get("PGM_START_TIME")), _hhmm(row.get("PGM_END_TIME"))
        if not title or not venue:
            _bump(stats, "refused: no title or venue")
            continue
        if a is None or b is None or b <= a:
            _bump(stats, "refused: no usable clock")
            continue
        days = smartplay_dates(row, today, horizon, stats)
        if not days:
            continue
        where = places.place(venue, _s(row.get("EN_DISTRICT")) or None, stats, checked=True)
        if where is None:
            _bump(stats, "unplaceable programmes (venue not in the facility lists, not geocoded)")
            _bump(stats, "unplaceable sessions", len(days))
            _bump(stats, f"  unplaced: {venue} ({_s(row.get('EN_DISTRICT'))})", len(days))
            continue
        _bump(stats, f"programmes placed from the {where['how']}")
        r = dict(row, _start=a, _end=b, _title=title, _venue=venue, _where=where)
        for d in days:
            groups.setdefault((venue_key(venue), _norm(title), d.isoformat()), []).append(r)

    out: List[NormalizedEvent] = []
    attribution = cfg["attribution"]
    for (_vk, _tk, day_s), sessions in sorted(groups.items()):
        sessions.sort(key=lambda s: (s["_start"], s["_end"], _s(s.get("ACTIVITY_NO"))))
        times = sorted({(s["_start"], s["_end"]) for s in sessions})
        _bump(stats, "repeat sessions folded", len(sessions) - len(times))
        runs = stretches(times)
        if len(runs) > 1:
            _bump(stats, "title-days split at a gap")
        day = date.fromisoformat(day_s)
        for a, b, parts in runs:
            inside = [s for s in sessions if a <= s["_start"] and s["_end"] <= b]
            lead = inside[0]
            if len(parts) > 1:
                _bump(stats, "back-to-back sessions joined into one row", len(parts) - 1)
            title, venue, where = lead["_title"], lead["_venue"], lead["_where"]
            tiers = [smartplay_price(s) for s in inside]
            tier, price = next((t for t in tiers if t[0] == "fee"), tiers[0])
            _bump(stats, f"price: {tier}")
            ages = age_label(lead)
            group = _s(lead.get("EN_TARGET_GRP"))
            who = ", ".join(x for x in [group if group and group.lower() != "general" else None, ages] if x)
            try:
                quota = int(lead.get("QUOTA") or 0)
            except (TypeError, ValueError):
                quota = 0
            head = [
                price + " Walk-in session: no enrolment, turn up on the day"
                + (f" ({quota} places, first come first served)." if quota else "."),
                (f"For: {who}." if who else None),
                (f"Sessions: {', '.join(hm(x) + '-' + hm(y) for x, y in parts)}." if len(parts) > 1 else None),
                # EN_DISTRICT is sometimes the venue again ("Tuen Mun Recreation
                # & Sports Centre", 3 rows 2026-10-05): named only when it is a
                # district the facility lists know.
                f"At {venue}" + (f", {_s(lead.get('EN_DISTRICT'))} District"
                                 if district_key(lead.get("EN_DISTRICT")) in places.by_district else "")
                + (f". Enquiries: {where['phone']}" if where.get("phone") else "") + ".",
            ]
            notes = " ".join(n if n.endswith((".", "。")) else n + "." for n in _notes(lead, stats))
            desc = compose(head, notes, [attribution], stats)
            sl, su = _stamp(day, a, tz)
            el, eu = _stamp(day, b, tz)
            typ = _s(lead.get("EN_ACT_TYPE_NAME"))
            primary = _category(title, src.get("category_by_title"), src.get("category_by_type"), typ,
                                src.get("category_default", "fitness"))
            extras = ["kids"] if _KIDS_WORDS.search(_fold(f"{title} {group}")) else []
            ev = NormalizedEvent(
                source=src.get("source", "lcsd-smartplay"),
                source_id=f"{_s(lead.get('ACTIVITY_NO'))}#{day_s}",
                name=title,
                description=desc,
                start_local=sl, start_utc=su, end_local=el, end_utc=eu,
                timezone=cfg.get("timezone"),
                venue_name=venue,
                latitude=where["lat"], longitude=where["lon"], coords_exact=bool(where["exact"]),
                address=where.get("address"),
                city=cfg.get("city"), country=cfg.get("country"),
                region=(_s(lead.get("EN_DISTRICT")) if district_key(lead.get("EN_DISTRICT")) in places.by_district
                        else None),
                category=primary, categories=norm_categories(primary, extras),
                promoter="Leisure and Cultural Services Department",
                ticket_url=_s(lead.get("EN_URL")) or src.get("dataset"),
            )
            ev.fingerprint = make_fingerprint(f"{title} {hm(a)}", day_s, venue)
            out.append(ev)
    return out


# ---------------------------------------------------------------------------
# cultural programmes
# ---------------------------------------------------------------------------
def xml_records(body: bytes, tag: str) -> Dict[str, Dict[str, Any]]:
    """{id: {child tag: text}} - repeated children (indate) become lists."""
    root = ET.fromstring(body)
    out: Dict[str, Dict[str, Any]] = {}
    for el in root.iter(tag):
        rec: Dict[str, Any] = out.setdefault(el.get("id") or "", {})
        for c in el:
            v = (c.text or "").strip()
            if c.tag in rec:
                prev = rec[c.tag]
                rec[c.tag] = (prev if isinstance(prev, list) else [prev]) + [v]
            else:
                rec[c.tag] = v
    return out


def holiday_entries(body: bytes) -> int:
    root = ET.fromstring(body)
    return sum(1 for _ in root)


# Date phrases in predateE, longest first at each position. None may start
# right after a digit, ':' or '/': "19:30, 15/11/2026" must not read "30, 15".
_LB = r"(?<![\d:/.])"
_DATE_RX = re.compile(
    rf"{_LB}(?P<a>(?P<ad1>\d{{1,2}})/(?P<am1>\d{{1,2}})/(?P<ay1>\d{{4}})\s*(?:\(\s*[A-Za-z]{{3}}\s*\))?\s*-\s*"
    rf"(?P<ad2>\d{{1,2}})/(?P<am2>\d{{1,2}})/(?P<ay2>\d{{4}}))"
    rf"|{_LB}(?P<b>(?P<bd1>\d{{1,2}})/(?P<bm1>\d{{1,2}})\s*-\s*(?P<bd2>\d{{1,2}})/(?P<bm2>\d{{1,2}})/(?P<by>\d{{4}}))"
    rf"|{_LB}(?P<l>(?P<ll>(?:\d{{1,2}}(?:\s*-\s*\d{{1,2}})?(?:/\d{{1,2}})?\s*(?:,|&|\band\b)\s*)+"
    rf"\d{{1,2}}(?:\s*-\s*\d{{1,2}})?)/(?P<lm>\d{{1,2}})/(?P<ly>\d{{4}}))"
    rf"|{_LB}(?P<c>(?P<cd1>\d{{1,2}})\s*-\s*(?P<cd2>\d{{1,2}})/(?P<cm>\d{{1,2}})/(?P<cy>\d{{4}}))"
    rf"|{_LB}(?P<d>(?P<dd>\d{{1,2}})/(?P<dm>\d{{1,2}})/(?P<dy>\d{{4}}))")
_EXCEPT_RX = re.compile(r"\(?\s*except\b([^)\n;]*)\)?", re.I)
_EXCEPT_DM = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{4}))?\b")
_AMPM = r"(?:am|pm|a\.m\.|p\.m\.)"
_SPAN_RX = re.compile(
    rf"(?<![\d/:.])(\d{{1,2}})(?:[:.](\d{{2}})|(\d{{2}}))?\s*({_AMPM})?\s*(?:-|–|to)\s*"
    rf"(\d{{1,2}})(?:[:.](\d{{2}})|(\d{{2}}))?\s*({_AMPM})?(?![\d/])", re.I)
_TIME_RX = re.compile(rf"(?<![\d/:.])(\d{{1,2}})(?::(\d{{2}})\s*({_AMPM})?|\s*({_AMPM}))(?![\d/])", re.I)


# What may stand between two date phrases that share the times after them.
_JOINER_RX = re.compile(r"(?:[\s,;&/*#]|\band\b|\(\s*[A-Za-z]{3}[a-z]*\.?(?:\s*[-,&]\s*[A-Za-z]{3}[a-z]*\.?)*\s*\))*",
                        re.I)


def _predate_text(text: Any) -> str:
    """predateE as the date grammar reads it: "8/11/ 2026" -> "8/11/2026",
    "22.9.2026" -> "22/9/2026", "10.9-14.10.2026" -> "10/9-14/10/2026", an
    en dash a hyphen. Clocks ("19.30", "7:30pm") are left alone: a dotted
    date needs its four-digit year, or a slashed date right after its dash."""
    raw = _html.unescape(_s(text)).replace("\xa0", " ").replace("\u2013", "-")
    raw = re.sub(r"(?<=\d)/\s+(?=\d{4}(?!\d))", "/", raw)
    raw = re.sub(r"(?<![\d:/.])(\d{1,2})\.(\d{1,2})\.(\d{4})(?!\d)", r"\1/\2/\3", raw)
    raw = re.sub(r"(?<![\d:/.])(\d{1,2})\.(\d{1,2})(?=\s*-\s*\d{1,2}/\d{1,2}/\d{4}(?!\d))", r"\1/\2", raw)
    return raw


def _clock(h: str, m: Optional[str], ampm: Optional[str]) -> Optional[int]:
    hh, mm = int(h), int(m or 0)
    if ampm:
        p = ampm.lower().replace(".", "")
        if hh > 12:
            return None
        hh = (hh % 12) + (12 if p == "pm" else 0)
    if hh > 24 or mm > 59:
        return None
    return hh * 60 + mm


def _span(m: "re.Match[str]") -> Optional[Tuple[int, int]]:
    h1, m1a, m1b, ap1, h2, m2a, m2b, ap2 = m.groups()
    m1, m2 = m1a or m1b, m2a or m2b
    # Each side must look like a clock: minutes, or am/pm. "24-27.9" is not.
    if not ((m1 or ap1) and (m2 or ap2)):
        return None
    if ap2 and not ap1 and int(h1) <= 12:
        ap1 = ap2 if int(h1) <= int(h2) or ap2.lower().startswith("a") else "am"
    a, b = _clock(h1, m1, ap1), _clock(h2, m2, ap2)
    if a is None or b is None:
        return None
    if b <= a:
        b += 24 * 60      # "6:30pm - 1am" crosses midnight
    return a, b


def _days(lo: date, hi: date) -> List[date]:
    if hi < lo or (hi - lo).days > 400:
        return []
    return [lo + timedelta(days=i) for i in range((hi - lo).days + 1)]


def _dates_of(m: "re.Match[str]") -> List[date]:
    g = {k: (int(v) if v and v.isdigit() else v) for k, v in m.groupdict().items()}
    try:
        if g["a"]:
            return _days(date(g["ay1"], g["am1"], g["ad1"]), date(g["ay2"], g["am2"], g["ad2"]))
        if g["b"]:
            y = g["by"]
            return _days(date(y if g["bm1"] <= g["bm2"] else y - 1, g["bm1"], g["bd1"]), date(y, g["bm2"], g["bd2"]))
        if g["l"]:
            # "24, 29-31/10, 5-7/11/2026": an item without a month takes the
            # month of the next item that names one; the year is the list's,
            # a year earlier for a month after the last ("28-31/12, 2/1/2027").
            out: List[date] = []
            month = g["lm"]
            for part in reversed(re.split(r"\s*(?:,|&|\band\b)\s*", g["ll"])):
                pm = re.fullmatch(r"(\d{1,2})(?:\s*-\s*(\d{1,2}))?(?:/(\d{1,2}))?", part.strip())
                if not pm:
                    return []
                month = int(pm.group(3)) if pm.group(3) else month
                y = g["ly"] if month <= g["lm"] else g["ly"] - 1
                out += _days(date(y, month, int(pm.group(1))), date(y, month, int(pm.group(2) or pm.group(1))))
            return out
        if g["c"]:
            return _days(date(g["cy"], g["cm"], g["cd1"]), date(g["cy"], g["cm"], g["cd2"]))
        return [date(g["dy"], g["dm"], g["dd"])]
    except ValueError:
        return []


def parse_predate(text: str, year_hint: int) -> Tuple[List[Tuple[Set[date], List[Tuple[int, Optional[int]]]]],
                                                       Set[date], List[Tuple[int, Optional[int]]]]:
    """(groups, excluded dates, every time in the text). A group is (dates,
    [(start, end or None)]): a date phrase and the times written after it,
    up to the next date phrase."""
    raw = _predate_text(text)
    excluded: Set[date] = set()
    masked = raw
    for m in _EXCEPT_RX.finditer(raw):
        clause = m.group(1)
        for d in _EXCEPT_DM.finditer(clause):
            try:
                excluded.add(date(int(d.group(3) or year_hint), int(d.group(2)), int(d.group(1))))
            except ValueError:
                pass
        for d in _EXCL_DATE.finditer(_fold(clause)):
            try:
                excluded.add(date(int(d.group(3) or year_hint), MONTHS[d.group(2)], int(d.group(1))))
            except ValueError:
                pass
        masked = masked[:m.start()] + "#" * (m.end() - m.start()) + masked[m.end():]
    tokens: List[Tuple[int, str, Any]] = []
    ends: Dict[int, int] = {}
    for m in _DATE_RX.finditer(masked):
        ds = _dates_of(m)
        if ds:
            tokens.append((m.start(), "date", set(ds)))
            ends[m.start()] = m.end()
        masked = masked[:m.start()] + "#" * (m.end() - m.start()) + masked[m.end():]
    for m in _SPAN_RX.finditer(masked):
        sp = _span(m)
        if sp:
            tokens.append((m.start(), "time", sp))
            masked = masked[:m.start()] + "#" * (m.end() - m.start()) + masked[m.end():]
    for m in _TIME_RX.finditer(masked):
        t = _clock(m.group(1), m.group(2), m.group(3) or m.group(4))
        if t is not None:
            tokens.append((m.start(), "time", (t, None)))
    tokens.sort(key=lambda x: x[0])
    groups: List[Tuple[Set[date], List[Tuple[int, Optional[int]]]]] = []
    every: List[Tuple[int, Optional[int]]] = []
    last_end = None
    for pos, kind, val in tokens:
        if kind == "date":
            # "14/11/2026 (Sat), 15/11/2026 (Sun) 19:30" share the time; but
            # "10.9-14.10.2026* (Thu-Wed) *Opening hours of the following
            # days: 22.9.2026 (Tue) 19:00-23:00" does not lend 22.9's hours to
            # the whole run - only a list separator may stand between them.
            if (groups and not groups[-1][1] and last_end is not None
                    and _JOINER_RX.fullmatch(masked[last_end:pos])):
                groups[-1][0].update(val)
            else:
                groups.append((set(val), []))
            last_end = ends[pos]
        else:
            if val not in every:
                every.append(val)
            if groups:
                if val not in groups[-1][1]:
                    groups[-1][1].append(val)
    return groups, excluded, every


def times_for(day: date, groups, every) -> List[Tuple[int, Optional[int]]]:
    """The clocks the text gives `day`. When the text names dates, a day it
    does not name has none: eventDates.xml lists all 50 days from the first
    to the last of a weekly lecture's 8 Wednesdays (187979, 2026-10-05), and
    the text's one 19:30 must not be lent to the other 42. A text that names
    no date at all lends `every` - which culture_events passes only when
    eventDates lists a single day."""
    if not groups:
        return sorted(every)
    named = [ts for ds, ts in groups if day in ds]
    if not named:
        return []
    got: List[Tuple[int, Optional[int]]] = []
    for ts in named:
        for t in ts:
            if t not in got:
                got.append(t)
    if got:
        return sorted(got)
    # Named by a phrase with no clock after it. Only when NO phrase has a
    # clock after it ("19:30 on 14/11/2026") does the text's clock apply: an
    # installation's one exception day ("22.9.2026 (Tue) 19:00-23:00") must
    # not lend its hours to the run it is an exception to (185799).
    if every and not any(ts for _ds, ts in groups):
        return sorted(every)
    return []


_DUR_H = re.compile(r"(\d+(?:\.\d+)?)\s*(?:hrs?|hours?)(?![a-z])(?:\s*(?:and\s*)?(\d{1,2})\s*(?:mins?|minutes?)(?![a-z]))?", re.I)
_DUR_M = re.compile(r"(\d{1,3})\s*(?:mins?|minutes?)\b(?!\s*(?:intermission|interval|break))", re.I)


def duration_minutes(text: str) -> Optional[int]:
    """'Approximately 1 hr 30 mins' -> 90; '2 hours with a 15 min intermission' -> 120."""
    t = _s(text)
    m = _DUR_H.search(t)
    if m:
        total = int(round(float(m.group(1)) * 60)) + int(m.group(2) or 0)
        return total if 10 <= total <= 12 * 60 else None
    m = _DUR_M.search(t)
    if m:
        total = int(m.group(1))
        return total if 10 <= total <= 12 * 60 else None
    return None


# A free event you register for ahead, in the event's own description:
# "Free admission by registration: https://www.art-mate.net/doc/99829"
# (188241, 188335), "Online registration starts from 31.8 evening" (187105,
# 187106), "Successful registrants will be notified via email" (188652) - the
# five of 2026-10-05, the same thing pricee's "Admission by enrollment" says.
_REG_DESC_RX = re.compile(
    r"admission\s+by\s+(?:registration|enrol(?:l)?ment)|\bonline\s+registration\b"
    r"|(?<!\bno\s)(?<!\bwithout\s)\b(?:pre-?)?registration\s+(?:is\s+)?(?:required|needed)"
    r"|\bsuccessful\s+(?:registrants|applicants)\b", re.I)
_CLASS_RX = re.compile(r"\bclass(?:es)?\b|\bcourses?\b|\btraining\b|\bworkshop series\b", re.I)
_ENROL_RX = re.compile(r"\benrol(?:l)?(?:ment|ing)?\b|\bregistration\b|\bapplication\b|\bapply\b", re.I)
_EVERY_RX = re.compile(r"\bevery\s+(?:mon|tue|wed|thu|fri|sat|sun|day|week)", re.I)
_DISTRICT_ONLY = re.compile(r"^venues?\s+in\s+.+\s+district$", re.I)


def culture_verdict(ev: Dict[str, Any]) -> Optional[str]:
    """None when kept, else the reason it is refused."""
    title = _s(ev.get("titlee")) or _s(ev.get("titlec"))
    price = _s(ev.get("pricee"))
    when = _s(ev.get("predateE"))
    if _ENROL_RX.search(price):
        return "admission by enrolment (its price line)"
    if _REG_DESC_RX.search(_text(ev.get("desce"))):
        return "admission by advance registration (its description)"
    if _EVERY_RX.search(when):
        return "a weekly series ('Every <day>'): a hirer's class season"
    if _CLASS_RX.search(title):
        return "a class or course (its title)"
    return None


def culture_price(ev: Dict[str, Any]) -> Tuple[str, str]:
    p = _s(ev.get("pricee"))
    f = _fold(p)
    if re.search(r"\bfree\b", f) and not re.search(r"\$\s*\d", p):
        extra = []
        if "ticket" in f:
            extra.append("free tickets are required")
        if "first-come" in f or "first come" in f:
            extra.append("first come, first served")
        return "free", "Admission: free" + (f" ({'; '.join(extra)})." if extra else ".")
    if re.search(r"\$\s*\d|\d\s*(?:hkd|dollars)", p, re.I):
        return "fee", f"Tickets (HK$): {p} (not free)."
    if p and re.search(r"[A-Za-z]", p) and not re.search(r"[㐀-鿿]", p):
        return "unknown", f"Price: {p} - not stated as free; see the programme page."
    return "unknown", "Price not stated in the data; see the programme page."


def _room(venuee: str) -> Tuple[str, Optional[str]]:
    m = re.match(r"^(.*?)\s*\((.*)\)\s*$", venuee)
    if m:
        return m.group(1).strip(), m.group(2).strip() or None
    return venuee.strip(), None


def culture_events(events: Dict[str, Dict[str, Any]], dates: Dict[str, Dict[str, Any]],
                   venues: Dict[str, Dict[str, Any]], src: Dict[str, Any], cfg: Dict[str, Any],
                   places: Places, tz, today: date, stats: Dict[str, int]) -> List[NormalizedEvent]:
    horizon = today + timedelta(days=int(src.get("horizon_days", 90)))
    include = re.compile(src["venue_include"], re.I) if src.get("venue_include") else None
    attribution = cfg["attribution"]
    out: List[NormalizedEvent] = []
    placed: Dict[str, Optional[Dict[str, Any]]] = {}
    for eid in sorted(events, key=lambda x: (len(x), x)):
        ev = events[eid]
        raw = (dates.get(eid) or {}).get("indate") or []
        raw = raw if isinstance(raw, list) else [raw]
        all_days: Set[date] = set()
        for d in raw:
            if d and d != SENTINEL_DATE:
                try:
                    all_days.add(datetime.strptime(d, "%Y%m%d").date())
                except ValueError:
                    pass
        if not any(today <= d <= horizon for d in all_days):
            _bump(stats, "events with no date in the window")
            continue
        _bump(stats, "events in the window")
        why = culture_verdict(ev)
        if why:
            _bump(stats, f"refused: {why}")
            continue
        vrec = venues.get(_s(ev.get("venueid"))) or {}
        venuee = _s(vrec.get("venuee")) or _s(vrec.get("venuec"))
        if not venuee:
            _bump(stats, "refused: no venue")
            continue
        building, room = _room(venuee)
        if include and not include.search(venuee):
            _bump(stats, "not kept: a venue outside venue_include")
            continue
        if _DISTRICT_ONLY.match(building):
            _bump(stats, "unplaceable: the venue is a whole district ('Venues in ... district')")
            continue
        vid = _s(ev.get("venueid"))
        if vid not in placed:
            lat, lon = dms(vrec.get("latitude")), dms(vrec.get("longitude"))
            if lat is not None and lon is not None and in_box((lat, lon), cfg.get("bbox")):
                placed[vid] = {"lat": lat, "lon": lon, "exact": True, "how": "venues.xml"}
            else:
                if lat is not None and lon is not None:
                    _bump(stats, "venue points outside Hong Kong (refused)")
                placed[vid] = places.place(building, None, stats)
            if placed[vid] is not None:
                _bump(stats, f"venues placed from {placed[vid]['how']}")
        where = placed[vid]
        if where is None:
            _bump(stats, "unplaceable: venue without a point the geocoder could find")
            _bump(stats, f"  unplaced: {building}")
            continue
        title = _s(ev.get("titlee")) or _s(ev.get("titlec"))
        if not _s(ev.get("titlee")):
            _bump(stats, "titles in Chinese only")
        when_text = _s(ev.get("predateE")) or _s(ev.get("predateC"))
        groups, excluded, every = parse_predate(ev.get("predateE") or ev.get("predateC") or "",
                                                min(all_days).year)
        days = sorted(all_days - excluded)
        _bump(stats, "dates not written: an 'Except' date", len(all_days & excluded))
        dur = duration_minutes(_s(ev.get("progtimee")))
        tier, price = culture_price(ev)
        primary = _category(title, src.get("category_by_title"), src.get("category_by_cat2"),
                            _s(ev.get("cat2")), src.get("category_default", "arts"))
        presenter = _s(ev.get("presenterorge")) or _s(ev.get("presenterorgc"))
        age = _s(ev.get("agelimite"))
        phone = _s(ev.get("enquiry"))
        link = _s(ev.get("urle")) or _s(ev.get("urlc")) or src.get("dataset")
        desc_src = _text(ev.get("desce"))
        if re.fullmatch(r"(?i)for programme (?:information|details),? please refer to the chinese version\.?",
                        desc_src):
            desc_src = ""

        def row(day0: date, day1: date, start: Optional[int], end: Optional[int], sid: str,
                note: Optional[str]) -> NormalizedEvent:
            about = [f"At {building}" + (f", {room}" if room else ""), presenter, age,
                     f"Enquiries: {phone}" if phone else ""]
            head = [
                price,
                f"Dates and times (LCSD): {when_text}" if when_text else None,
                note,
                " ".join(x if x.endswith((".", "。", ")")) else x + "." for x in about if x),
            ]
            desc = compose(head, desc_src, [attribution], stats)
            if start is None:
                sl, el, su, eu = day0.isoformat(), day1.isoformat(), None, None
            else:
                sl, su = _stamp(day0, start, tz)
                el, eu = _stamp(day0, end, tz) if end is not None else (None, None)
            evn = NormalizedEvent(
                source=src.get("source", "lcsd-culture"),
                source_id=sid,
                name=title,
                description=desc,
                start_local=sl, start_utc=su, end_local=el, end_utc=eu,
                timezone=cfg.get("timezone"),
                venue_name=building,
                latitude=where["lat"], longitude=where["lon"], coords_exact=bool(where["exact"]),
                address=where.get("address"),
                city=cfg.get("city"), country=cfg.get("country"),
                category=primary, categories=[],
                promoter=re.sub(r"^(?:presented|organi[sz]ed)\s+by\s+(?:the\s+)?", "", presenter, flags=re.I)[:120] or None,
                ticket_url=link,
            )
            tag = hm(start) if start is not None else ""
            # The ROOM is part of the place: two "Cantonese Opera Excerpts" at
            # 14:15 in Ko Shan Theatre's Theatre and its New Wing Auditorium
            # are two shows by two troupes (187008, 187017), one ticketed and
            # one free (188367, 188392). The same show listed twice in one
            # room (188313/188338, two cat2 codes) still folds.
            evn.fingerprint = make_fingerprint(f"{title} {tag}".strip(), day0.isoformat(), venuee)
            _bump(stats, f"price: {tier}")
            return evn

        # A text that names no date we can read lends its clock to a single
        # listed day only: eventDates.xml overstates (see times_for).
        lend = every if (groups or len(days) == 1) else []
        if not groups and len(days) > 1:
            _bump(stats, "events whose predateE names no readable date (eventDates alone gives no clock)")
        per_day = {d: times_for(d, groups, lend) for d in days}
        exhibition = _s(ev.get("cat2")) in EXHIBITION_CATS or bool(EXHIBITION_RX.search(title))
        # Only days the text names: "07/10/2026 19:30 ... 25/11/2026 19:30"
        # is 8 lectures, not eventDates' 50 days from the first to the last.
        if groups:
            named = set().union(*(ds for ds, _ts in groups))
            unnamed = [d for d in days if d not in named]
            if unnamed:
                _bump(stats, "dates not written: in eventDates.xml, not in the event's own text",
                      sum(1 for d in unnamed if today <= d <= horizon))
                days = [d for d in days if d in named]
        # An exhibition whose text gives dates and no hours is still open on
        # those dates; a film series or an expo of shows with no clock is not
        # something to turn up to at an unknown hour.
        timed_days = [d for d in days if per_day[d]]
        undated_show = not any(today <= d <= horizon for d in timed_days)
        if undated_show and not exhibition:
            _bump(stats, "refused: no clock time in predateE for any date in the window")
            continue
        # Opening hours, not a show: one span on each day that has a clock,
        # 4 hours or more unless the event is an exhibition (an exhibition's
        # 16:00-18:30 opening day is still its opening hours: 186936).
        # "30-31/10/2026 19:30-21:30" is two performances, not a run.
        hours_only = undated_show or all(
            len(per_day[d]) == 1 and per_day[d][0][1] is not None
            and (exhibition or per_day[d][0][1] - per_day[d][0][0] >= OPENING_HOURS_MIN) for d in timed_days)
        # Runs of consecutive dates over the event's WHOLE list, so a run's
        # identity is the same today and next week.
        runs: List[List[date]] = []
        for d in days:
            if runs and (d - runs[-1][-1]).days == 1:
                runs[-1].append(d)
            else:
                runs.append([d])
        for run in runs:
            if run[-1] < today or run[0] > horizon:
                continue
            if hours_only and (len(run) > 1 or undated_show):
                hours = sorted({per_day[d][0] for d in run if per_day[d]})
                every_day = all(per_day[d] for d in run)
                note = ("Open " + ", ".join(f"{hm(a)}-{hm(b % 1440)}" for a, b in hours) + " on the days listed."
                        if hours and every_day and len(hours) <= 3 else
                        "Opening hours are not in the data." if not hours else None)
                out.append(row(run[0], run[-1], None, None, f"{eid}#{run[0].isoformat()}..{run[-1].isoformat()}",
                               note))
                _bump(stats, "multi-day rows (a run of consecutive dates with opening hours)")
                continue
            for d in run:
                if not (today <= d <= horizon):
                    continue
                ts = per_day[d]
                if not ts:
                    _bump(stats, "dates refused: no clock time for the date in predateE")
                    continue
                for start, end in ts:
                    if end is None and dur:
                        end = start + dur
                    out.append(row(d, d, start, end, f"{eid}#{d.isoformat()}#{hm(start)}", None))
                    _bump(stats, "dated rows (one per performance)")
    return out


# ---------------------------------------------------------------------------
# runs
# ---------------------------------------------------------------------------
def ingest_smartplay(store: EventStore, reader: Reader, src: Dict[str, Any], cfg: Dict[str, Any],
                     places: Places, tz) -> Dict[str, int]:
    stats: Dict[str, int] = {}
    rows = _json(reader.get(src["url"]))
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("the SmartPlay file is empty or not a list")
    stats["programmes read"] = len(rows)
    evs = smartplay_events(rows, src, cfg, places, tz, _today(tz), stats)
    for ev in evs:
        store.upsert(ev)
    stats["rows written"] = len(evs)
    return stats


def ingest_culture(store: EventStore, reader: Reader, src: Dict[str, Any], cfg: Dict[str, Any],
                   places: Places, tz) -> Dict[str, int]:
    stats: Dict[str, int] = {}
    events = xml_records(reader.get(src["events_url"]), "event")
    dates = xml_records(reader.get(src["dates_url"]), "event")
    venues = xml_records(reader.get(src["venues_url"]), "venue")
    if not events:
        raise RuntimeError("events.xml carries no events")
    stats["events read"] = len(events)
    if src.get("holiday_url"):
        n = holiday_entries(reader.get(src["holiday_url"]))
        if n:
            print(f"[lcsd] WARNING: holiday.xml lists {n} venue closure(s); this adapter does not yet "
                  "apply them (the file was empty when it was written). Read it and teach the adapter.")
            stats["holiday.xml entries NOT applied"] = n
    evs = culture_events(events, dates, venues, src, cfg, places, tz, _today(tz), stats)
    for ev in evs:
        store.upsert(ev)
    stats["rows written"] = len(evs)
    return stats


def load_config(path: str) -> Dict[str, Any]:
    cfg = json.loads(open(path, encoding="utf-8").read())
    attribution = (cfg.get("attribution") or "").strip()
    if not attribution or len(attribution) > ATTRIBUTION_MAX:
        raise ValueError(f"attribution must be 1-{ATTRIBUTION_MAX} characters "
                         "(the sync keeps a final paragraph only that short)")
    if not cfg.get("timezone"):
        raise ValueError("config needs a timezone")
    return cfg


def make_geocoder(session, cfg: Dict[str, Any]):
    """mapsee_ingest_ics's geocoder: its committed cache, its per-run budget,
    its attempts."""
    from mapsee_ingest_ics import make_location_geocoder
    return make_location_geocoder(session, cfg.get("geocode_suffix") or ", Hong Kong")


def _budget_spent() -> bool:
    """True when this run's cap on NEW Photon lookups (mapsee_geo_budget) is
    used up: a venue the geocoder then "did not find" was never asked."""
    try:
        import mapsee_geo_budget as gb
        if gb._MAX <= 0:
            return False
        with open(gb._FILE, encoding="utf-8") as fh:
            return int(json.load(fh).get("n", 0)) >= gb._MAX
    except Exception:  # noqa: BLE001 - no file yet: nothing spent
        return False


DEFAULT_SOURCE = {"smartplay": "lcsd-smartplay", "culture": "lcsd-culture"}


def save_geocoder() -> None:
    try:
        from mapsee_ingest_ics import _GEO_CACHE, _save_geo_cache
        _save_geo_cache(_GEO_CACHE)
    except Exception as exc:  # noqa: BLE001
        print(f"[lcsd] geocode cache not saved: {type(exc).__name__}: {exc}", flush=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import Hong Kong LCSD walk-in sessions and cultural programmes.")
    ap.add_argument("--config", required=True)
    ap.add_argument("--store", default="mapsee_events.json")
    ap.add_argument("--max-minutes", type=float, default=DEFAULT_MAX_MINUTES,
                    help="whole-run deadline; no request starts after it (0 = none)")
    a = ap.parse_args(argv)

    started = time.monotonic()
    cfg = load_config(a.config)
    tz = ZoneInfo(cfg["timezone"])
    session = requests.Session()
    session.headers.update({"User-Agent": UA})
    reader = Reader(session, deadline=started + a.max_minutes * 60 if a.max_minutes else None)
    store = EventStore(a.store)
    total = 0
    geocode = None
    places: Optional[Places] = None
    facilities_read = False
    try:
        facilities = load_facilities(reader, cfg.get("facility_urls") or [])
        facilities_read = True
    except (Refused, OutOfTime, Exception) as exc:  # noqa: BLE001 - places can still be geocoded
        print(f"[lcsd] facility lists NOT read: {type(exc).__name__}: {exc}")
        facilities = []
    # A venue the geocoder could not be ASKED (this run's budget spent) is not
    # a venue it could not find: counted, so the read is not called complete.
    unasked = [0]
    try:
        bare = make_geocoder(session, cfg)

        def geocode(query: str, _g=bare):
            got = _g(query)
            if (got[0] is None or got[1] is None) and _budget_spent():
                unasked[0] += 1
            return got
    except Exception as exc:  # noqa: BLE001
        print(f"[lcsd] no geocoder: {type(exc).__name__}: {exc}")
    places = Places(facilities, cfg, geocode,
                    stop=(lambda: reader.deadline is not None and reader.clock() >= reader.deadline))
    print(f"[lcsd] facility lists: {len(facilities)} facilities, {len(places.refused)} points refused"
          + "".join(f"\n[lcsd]     {k}: {v}" for k, v in sorted(places.refused.items())))
    stopped = False
    for src in cfg.get("sources", []):
        label = src.get("name", src.get("kind"))
        if stopped:
            print(f"[lcsd] {label}: NOT READ - the run deadline passed")
            continue
        before = reader.requests
        today = _today(tz)
        try:
            if src.get("kind") == "smartplay":
                need = int(cfg.get("smartplay_min_districts") or SMARTPLAY_MIN_DISTRICTS)
                if len(places.by_district) < need:
                    # Without the facility lists no district can vouch for a
                    # geocode, and Photon's Kowloon BAY Park and Ocean Park
                    # would pass for Kowloon Park and Hong Kong Park.
                    print(f"[lcsd] {label}: NOT READ - the facility lists vouch for "
                          f"{len(places.by_district)} districts (fewer than {need}), so no SmartPlay "
                          "venue could be placed safely; the cultural programme does not need them")
                    continue
                stats = ingest_smartplay(store, reader, src, cfg, places, tz)
            elif src.get("kind") == "culture":
                stats = ingest_culture(store, reader, src, cfg, places, tz)
            else:
                print(f"[lcsd] {label}: unknown kind {src.get('kind')!r}, skipped")
                continue
        except Refused as exc:
            print(f"[lcsd] {label} REFUSED: {exc} - not retried, and the host is asked nothing more")
            continue
        except OutOfTime as exc:
            print(f"[lcsd] {label} STOPPED: {exc} (--max-minutes {a.max_minutes:g})")
            stopped = True
            continue
        except Exception as exc:  # noqa: BLE001 - one source never stops another
            print(f"[lcsd] {label} FAILED: {type(exc).__name__}: {exc}")
            continue
        finally:
            store.save()
        total += stats.get("rows written", 0)
        # THE SOURCE WAS READ WHOLE: its files answered in full (a body cut by
        # the deadline raises OutOfTime above), the facility lists that place
        # its venues were read, and no venue went unplaced for want of time or
        # geocoding budget. Then a session the last complete read wrote and
        # this one did not is gone from LCSD's own data (SmartPlay is rebuilt
        # every 10 minutes), and mapsee_supabase_sync --retire-absent may
        # cancel it. Neither file carries a per-session cancellation of its
        # own: absence is the signal (the one "postponed" in 3,137 rows on
        # 2026-10-05 was a typhoon contingency, not a postponement).
        lost = [why for why, bad in (
            ("the facility lists were not read", not facilities_read),
            ("there was no geocoder", geocode is None),
            ("venues not geocoded: the run deadline passed", stats.get("venues not geocoded: the run deadline passed")),
            (f"{unasked[0]} venue(s) not geocoded: the budget was spent", unasked[0])) if bad]
        if lost:
            stats["read NOT complete: " + "; ".join(lost)] = 1
        else:
            store.mark_complete(src.get("source") or DEFAULT_SOURCE[src["kind"]], today,
                                today + timedelta(days=int(src.get("horizon_days", 90))))
            stats["read complete (absence may cancel)"] = 1
            store.save()
        print(f"[lcsd] {label}: {stats.get('rows written', 0)} rows in {reader.requests - before} requests")
        for k, v in sorted(stats.items()):
            if k != "rows written":
                print(f"[lcsd]     {k}: {v}")
    if geocode is not None:
        save_geocoder()
    st = store.stats
    print(f"[lcsd] done in {(time.monotonic() - started):.0f} s: {total} rows written "
          f"(added {st.get('added', 0)}, updated {st.get('updated', 0)}, merged {st.get('merged', 0)}, "
          f"rekeyed {st.get('rekeyed', 0)}, rejected {st.get('rejected', 0)}) in {reader.requests} "
          f"requests; store now holds {len(store.records)} unique events.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
