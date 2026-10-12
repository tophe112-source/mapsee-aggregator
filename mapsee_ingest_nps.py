#!/usr/bin/env python3
"""
mapsee_ingest_nps.py — import National Park Service events (ranger programs, guided
tours, talks, family + volunteer programs) into the Mapsee store.

Official, free API — get a key at https://www.nps.gov/subjects/developer/get-started.htm
(1,000 req/hr). Great for the outdoors / kids / learning / volunteer layers near
metros with national parks, monuments, seashores and historic sites. Most events
carry their own lat/lon; the rest are placed from their park or their street
address (item 5).

    export NPS_API_KEY=...
    python mapsee_ingest_nps.py --states WA,OR,CA --within-days 90 --store mapsee_events.json

WHAT EACH PART BELOW IS FOR. Measured against the live API on 2026-10-11, and
against the 2026-10-08 Actions log (run 37788997741, the Mon/Thu extra_sources job):

1. PAGING IS pageNumber, NOT start. /events has no `start` parameter (the API's
   own swagger lists pageSize and pageNumber only), so the old loop re-read page
   1 on every pass: start=0 and start=50 for Virginia came back byte-identical,
   and only the first 50 of Virginia's 254 events were ever read. The log hid
   it: "VA 903" was page 1's 129 occurrences upserted 7 times over (replayed
   on the 2026-10-11 page: "kept 762" for 102 rows). The log now counts rows
   by fingerprint, and a page that repeats the one before it ends the state,
   loudly, instead of looping.

2. THE PAGE IS 50, WHATEVER WE ASK FOR. pageSize=500 for Virginia returned 50
   rows and echoed "pagesize": "50". So the short-page test reads the size the
   API echoes, not the one we sent: comparing 50 rows with a requested 500
   would end every state after its first page, the old bug by another road.

3. THE WINDOW IS THE API'S. dateStart/dateEnd keep the read to the horizon
   (Virginia: 254 listed unwindowed, 253 inside 90 days), and expandRecurring
   stays false: one record per event, whose `dates` are expanded here.

4. EVERY STATE, DC AND THE TERRITORIES. The old default swept 23; 28 states
   and every territory were never asked. A park in several states (the Blue
   Ridge Parkway is VA and NC) is listed under each, so an event id is read
   once per run, and a state's count is the events new to the run.

5. AN EVENT WITHOUT COORDINATES IS PLACED FROM ITS PARK, OR NOT AT ALL. 30 of
   Virginia's first 100 events had no lat/lon, and all 30 named a park
   (`sitecode`). 23 of the 30 were Blue Ridge Parkway programmes, and the
   Parkway's own coordinated events in the same pages lay up to 366 km apart
   (its 14 visitor centres, 470 km): one park point would pin a hike at
   Craggy Gardens (NC) a couple of hundred km from its trailhead. So:
     - a visitor centre of that park whose name the title or location names
       is the place ("(Craggy Gardens Visitor Center - Milepost 364)"; 21 of
       the 23 Parkway events name one of its centres);
     - otherwise a street address in the location ("555 Newcomb St SE /
       Washington, DC 20032") goes to the sync as address, city and state,
       with no point, for its Census geocoder to place or to drop. 12 of the
       47 distinct locations in those 100 events read as one (no false
       reads); the sync's batch_geocode matched 11, and the 9 whose events
       carry NPS coordinates too landed 11 to 387 m from them (723 Second St,
       Annapolis did not match, and an unmatched row without a point is
       dropped, not guessed);
     - otherwise the park's own point, but only when the location says
       nothing (Booker T. Washington's two) or names the park itself
       ("Arsenal Square in historic Lower Town, Harpers Ferry"; "Great Falls
       Park"), AND everything else we know is in that park (its visitor
       centres, and the coordinates its other events carried this run) lies
       within FALLBACK_KM of that point. In the same pages a compact park's
       events spread 1.7 km (Cumberland Gap), 3.5 (Petersburg) and 3.6 (Cedar
       Creek); a scattered one's 16 km (Fredericksburg & Spotsylvania) and
       120 km (Chesapeake Bay). The spread is only a LOWER bound on a park's
       size: Civil War Defenses of Washington's four coordinated events all
       sat within 9 m of Battleground National Cemetery, so a park point
       anywhere within 5 km of it passed, and its clean-up 14.1 km away at
       Shepherd Parkway took that point. Hence the location test before it;
     - otherwise the event is counted as unplaced and left out. A park with
       nothing else we know of (George Washington Birthplace: no centre, no
       coordinated event) is left out rather than guessed at.
   /parks and /visitorcenters both take a comma list of park codes, so the
   lookups are batched PARK_BATCH parks to a request and cached for the run.

6. THE KEY HAS 1,000 REQUESTS AN HOUR. Every request is counted; the run ends
   cleanly (saving what it read) at --max-requests, when the API's own
   X-RateLimit-Remaining falls to MIN_REMAINING, or on the first 429 /
   OVER_RATE_LIMIT, 401 or 403, which are refusals and are not retried. The
   key travels in the X-Api-Key header, so no URL, log line or exception text
   carries it. Requests are at least a second apart.

7. `dates` STILL LISTS THE DAYS THE PARK SAYS ARE OFF. In Virginia's first 100
   events, 13 of 13 future EXDATEs in `recurrencerule` and 5 of 6 entries in
   `cancelleddatelist` were also in `dates`, and 13 of the 200 occurrences
   the old adapter wrote were such days. They are no longer written, and
   inside the next RECUR_DAYS days they are tombstoned under the fingerprint
   the live row has (EventStore.cancel), so a row already written comes off
   the map too: 10 tombstones from those 100 events (see occurrences()).

8. A REPEATING PROGRAMME IS WRITTEN FOR THE NEXT WEEK, AND AT LEAST THREE TIMES.
   The old cap (the next 3 dates) left a hole: this runs Mon/Thu, so a daily
   talk was written Mon-Wed and Thu-Sat and never on a Sunday (11 of 100
   Virginia events ran on 4+ days of their first week). Every occurrence in
   the next RECUR_DAYS days, and never fewer than MIN_RECUR, is 212
   occurrences for those 100 events where the cap wrote 200 (uncapped: 820).
   mapsee_link_series chains the rest of a programme after the sync.

9. THE DOOR COMES FROM THE API'S OWN `types` AND THE TITLE, NOT FROM
   SUBSTRINGS OF THE HTML. The old rule looked for "art", "kid", "volunteer"
   anywhere in title + raw description, first hit wins, and the sync keeps an
   arts, kids or volunteer primary as it finds it. Over Virginia's first 100
   events it said arts 16 (12 of them "earth", "heart", "artillery",
   "depart", "smart", "start"), kids 12 (4 of them "the Wood family", "the
   Cone family" twice, and "font-family: 'Calibri'" in a span's style) and
   volunteer 6 (5 of them two bird walks "Volunteer Guided", a butterfly
   walk "alongside a Park Ranger, volunteers", and two open houses where
   "rangers and volunteers are on site"). 14 of the 23 hike and walk
   titles reached a door other than outdoors. `types` already says Hike 14,
   Walk 18, Talk 66, Guided Tour 31, Children's Program 2, Volunteer Event 3,
   Performance 2. Now: a hike or a walk (by type or by the title's own word)
   is outdoors, with learning beside it when it is also a tour or a talk;
   Children's Program or Junior Ranger is kids; a Volunteer Event is
   volunteer only when the title names the work or the sign-up is
   volunteer.gov (Shepherd Parkway's clean-up; the bird walks are
   volunteer-LED); music needs a concert in the title, or Performance and a
   music word; arts needs an art word in the title, read without its "(Folk
   Art Center - Milepost 382)"; a talk, tour, living history or
   demonstration is learning. On those 100: outdoors 27 (all 23 hike and
   walk titles), learning 66, arts 2, kids 2, volunteer 1, music 1,
   community 1, and the sync's derive_categories keeps every one of those
   primaries.
"""
from __future__ import annotations

import argparse
import html
import math
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timezone, timedelta
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

from mapsee_ingest import NormalizedEvent, EventStore, USER_AGENT, make_fingerprint

API_ROOT = "https://developer.nps.gov/api/v1"
API = f"{API_ROOT}/events"
PARKS_API = f"{API_ROOT}/parks"
CENTERS_API = f"{API_ROOT}/visitorcenters"

# The 50 states, DC, and the five inhabited territories (USPS codes), each of
# which has at least one NPS unit (San Juan, Virgin Islands, War in the
# Pacific, American Samoa, American Memorial Park).
ALL_STATES = ("AL,AK,AZ,AR,CA,CO,CT,DE,DC,FL,GA,HI,ID,IL,IN,IA,KS,KY,LA,ME,MD,MA,MI,MN,MS,"
              "MO,MT,NE,NV,NH,NJ,NM,NY,NC,ND,OH,OK,OR,PA,RI,SC,SD,TN,TX,UT,VT,VA,WA,WV,WI,WY,"
              "PR,VI,GU,AS,MP")

PAGE_SIZE = 50            # the API's own ceiling (item 2)
MAX_PAGES = 60            # per state: 3,000 events (Virginia is 6 pages). A ceiling, not a plan.
PARK_BATCH = 50           # park codes per /parks or /visitorcenters request
MAX_REQUESTS = 900        # of the key's 1,000 an hour, leaving room for a manual run
MIN_REMAINING = 20        # stop when the API says this few are left this hour
MIN_INTERVAL_S = 1.0      # at most one request a second
FALLBACK_KM = 5.0         # how far a park's known places may lie from its point (item 5)
RECUR_DAYS = 7            # write every occurrence this many days ahead (item 8)...
MIN_RECUR = 3             # ...and never fewer than this many
SAVE_EVERY_S = 120        # a sweep cut short by the job's timeout keeps what it read

Address = Tuple[str, str, str, Optional[str]]          # street, city, state, ZIP (item 5)


def _today() -> date:
    """MAPSEE_TODAY=YYYYMMDD fixes "today" for the tests; otherwise today in UTC."""
    fixed = os.environ.get("MAPSEE_TODAY")
    if fixed:
        return datetime.strptime(fixed, "%Y%m%d").date()
    return datetime.now(timezone.utc).date()


# --------------------------------------------------------------------------- #
# Which door an event is for (item 9)
# --------------------------------------------------------------------------- #
_TAG_RX = re.compile(r"<[^>]+>")
_PAREN_RX = re.compile(r"\([^)]*\)")
_LEARNING_TYPES = {"talk", "guided tour", "living history", "cultural craft demonstration"}
_HIKE_RX = re.compile(r"\b(hik(?:e|es|ing)|walk(?:s|ing)?|rambles?|treks?|trekking)\b", re.I)
_VOLUNTEER_WORK_RX = re.compile(
    r"\b(volunteers?|volunteering|clean[\s-]?ups?|stewardship|work\s?days?|trail\s+work|"
    r"restoration|invasives?|litter|tree\s+planting)\b", re.I)
_JUNIOR_RANGER_RX = re.compile(r"\bjunior\s+rangers?\b", re.I)
_CONCERT_RX = re.compile(r"\b(concerts?|live\s+music)\b", re.I)
_MUSIC_RX = re.compile(
    r"\b(music|musicians?|concerts?|bands?|bluegrass|fiddl(?:e|es|ers?|ing)|banjos?|singers?|singing|choirs?)\b",
    re.I)
_ART_RX = re.compile(
    r"\b(art|arts|artists?|painting|paint|studios?|sketch(?:es|ing)?|drawing|pottery|printmaking|"
    r"watercolou?rs?)\b", re.I)
_TALK_RX = re.compile(r"\b(talks?|tours?|lectures?|presentations?|demonstrations?)\b", re.I)


def _plain(value: Any) -> str:
    """The words a reader sees: tags out, entities decoded, one space."""
    return " ".join(html.unescape(_TAG_RX.sub(" ", str(value or ""))).split())


def _categories(ev: Dict[str, Any]) -> Tuple[str, List[str]]:
    """(primary, extras) from the API's own `types` and the title (item 9).

    The description is read only when the event has no types, and then as
    text with whole words: the old rule matched substrings of the raw HTML."""
    types = {_norm(t) for t in ev.get("types") or []}
    title = _plain(_PAREN_RX.sub(" ", str(ev.get("title") or "")))     # "(Folk Art Center - Milepost 382)"
    learning = ["learning"] if types & _LEARNING_TYPES else []
    # "Volunteer Event" also marks a programme volunteers LEAD (Great Falls'
    # bird walks, "Volunteer Guided"), so the work has to be named, or the
    # sign-up has to be volunteer.gov.
    signup = f"{ev.get('regresurl') or ''} {ev.get('description') or ''}".lower()
    if "volunteer event" in types and (_VOLUNTEER_WORK_RX.search(title) or "volunteer.gov" in signup):
        return "volunteer", []
    if "children s program" in types or _JUNIOR_RANGER_RX.search(title):
        return "kids", learning
    if types & {"hike", "walk"} or _HIKE_RX.search(title):
        return "outdoors", learning
    if _CONCERT_RX.search(title) \
            or ("performance" in types and _MUSIC_RX.search(f"{title} {_plain(ev.get('description'))}")):
        return "music", []
    if _ART_RX.search(title):
        return "arts", []
    if learning:
        return "learning", []
    if "festival" in types:
        return "community", []
    if not types:
        desc = _plain(ev.get("description"))
        if _HIKE_RX.search(desc):
            return "outdoors", []
        if _TALK_RX.search(desc):
            return "learning", []
    return "outdoors", []


def _category(ev: Dict[str, Any]) -> str:
    return _categories(ev)[0]


def _hm(t):
    """Parse an NPS clock string to 'HH:MM:00' — accepts 12-hour ('10:00 AM',
    '2:30 PM') AND 24-hour ('14:30', '09:00:00')."""
    if not t or not isinstance(t, str):
        return None
    t = t.strip()
    m = re.match(r"(\d{1,2}):(\d{2})\s*([ap])\.?\s*m", t.lower())          # 12-hour
    if m:
        h, mi, ap = int(m.group(1)), m.group(2), m.group(3)
        if ap == "p" and h != 12:
            h += 12
        if ap == "a" and h == 12:
            h = 0
        return f"{h:02d}:{mi}:00" if 0 <= h <= 23 else None
    m = re.match(r"(\d{1,2}):(\d{2})(?::\d{2})?$", t)                      # 24-hour
    if m:
        h, mi = int(m.group(1)), m.group(2)
        return f"{h:02d}:{mi}:00" if 0 <= h <= 23 else None
    return None


def _f(ev, *keys):
    """First non-empty value across field-name variants — the NPS events endpoint
    mixes casing (timeStart vs timestart, infoURL vs infourl)."""
    for k in keys:
        v = ev.get(k)
        if v not in (None, ""):
            return v
    return None


def _norm(s: Any) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(s or "").lower()))


def _int(v) -> Optional[int]:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def _point(lat_raw, lon_raw) -> Optional[Tuple[float, float]]:
    """A usable (lat, lon), or None: missing, unparseable, out of range or 0,0."""
    try:
        lat, lon = float(lat_raw), float(lon_raw)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(lat) and math.isfinite(lon)) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    if abs(lat) < 1e-6 and abs(lon) < 1e-6:
        return None
    return lat, lon


def own_point(ev: Dict[str, Any]) -> Optional[Tuple[float, float]]:
    return _point(_f(ev, "latitude", "Latitude"), _f(ev, "longitude", "Longitude"))


def event_id(ev: Dict[str, Any]) -> Optional[str]:
    v = _f(ev, "id", "eventID", "eventid")
    return str(v) if v is not None else None


def park_code(ev: Dict[str, Any]) -> Optional[str]:
    """The park an event belongs to. `sitecode` names an organisation or a
    portal when `sitetype` says so, and those have no point of their own."""
    if str(_f(ev, "sitetype", "siteType") or "park").strip().lower() != "park":
        return None
    code = str(_f(ev, "sitecode", "siteCode") or "").strip().lower()
    return code or None


_DAY_RX = re.compile(r"(\d{4})-?(\d{2})-?(\d{2})")


def _day(value: Any) -> Optional[date]:
    """'2026-10-18', '20261018' or '10/18/2026' -> a date, else None."""
    s = str(value or "").strip()
    m = _DAY_RX.match(s)
    try:
        if m:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return datetime.strptime(s[:10], "%m/%d/%Y").date()
    except ValueError:
        return None


def off_days(ev: Dict[str, Any]) -> Set[date]:
    """The days the park says this event does NOT happen (item 7): the rule's
    EXDATEs and the cancelled-date list. /events still lists them in `dates`."""
    off: Set[date] = set()
    m = re.search(r"EXDATE=([^|;]*)", str(ev.get("recurrencerule") or ""))
    if m:
        off.update(d for d in (_day(x) for x in m.group(1).split(",")) if d)
    off.update(d for d in (_day(x) for x in ev.get("cancelleddatelist") or []) if d)
    return off


def occurrences(ev: Dict[str, Any], today: date, within_days: int) -> Tuple[List[date], List[date]]:
    """(days to write, days to tombstone), both inside [today, today + within_days].

    Every listed day the park has not called off is an occurrence, and the
    write keeps the next RECUR_DAYS days of them and at least MIN_RECUR (item
    8).

    Called-off days are tombstoned only inside those next RECUR_DAYS days,
    because two NPS events can share a fingerprint (same title, same place)
    and disagree: Mabry Mill's "Historic Mill Open House" is published twice,
    and one copy EXDATEs every remaining weekend day the other lists live. A
    tombstone beside a live listing from the same source under another id is
    a relisting and loses (EventStore._relist), but only when both are read in
    the same run; inside the next RECUR_DAYS days every live occurrence is
    written, so the live copy is always there to win. A called-off day further
    out is tombstoned by a later run, and the runs are at most 4 days apart."""
    horizon = today + timedelta(days=within_days)
    dstart = _f(ev, "dateStart", "datestart")
    raw = ev.get("dates") or ([dstart] if dstart else [])
    days = sorted({d for d in (_day(x) for x in raw) if d and today <= d <= horizon})
    off = off_days(ev)
    live = [d for d in days if d not in off]
    near = today + timedelta(days=RECUR_DAYS - 1)
    live = [d for i, d in enumerate(live) if i < MIN_RECUR or d <= near]
    return live, [d for d in days if d in off and d <= near]


def _build(ev: Dict[str, Any], day: date, point: Optional[Tuple[float, float]],
           address: Optional[Address] = None) -> NormalizedEvent:
    """The one builder for a live row and for its tombstone, so both carry the
    same fingerprint, source and source_id (docs/agents/cancelled-events.md).
    `address` only ever comes without a point: the sync's Census pass would
    otherwise replace the park's own coordinates with its geocode."""
    title = (ev.get("title") or "").strip()
    times = ev.get("times") or []
    t0 = times[0] if (times and isinstance(times[0], dict)) else {}
    tstart = _hm(_f(t0, "timeStart", "timestart"))
    tend = _hm(_f(t0, "timeEnd", "timeend"))
    site = _f(ev, "parkFullName", "parkfullname", "siteCode", "sitecode")
    eid = event_id(ev)
    # Every NPS event has a public page — use infoURL if given, else build it from
    # the event id, so the listing always links out to the webpage.
    link = _f(ev, "infoURL", "infourl", "url", "regResURL") \
        or (f"https://www.nps.gov/planyourvisit/event-details.htm?id={eid}" if eid else None)
    desc = (ev.get("description") or "").strip() or None
    if desc:
        desc = " ".join(desc.split())
    d = day.isoformat()
    primary, extras = _categories(ev)
    street, city, region, postal = address if (address and not point) else (None, None, None, None)
    nev = NormalizedEvent(
        source="nps",
        source_id=f"{eid or title}|{d}",
        name=title,
        description=desc,
        start_local=f"{d}T{tstart}" if tstart else d,
        end_local=f"{d}T{tend}" if tend else None,
        venue_name=ev.get("location") or site,
        latitude=point[0] if point else None,
        longitude=point[1] if point else None,
        address=street,
        city=city,
        region=region,
        postal_code=postal,
        category=primary,
        categories=extras,
        poster_image_url=_f(ev, "imageUrl", "imageURL") or None,
        ticket_url=link,
    )
    nev.fingerprint = make_fingerprint(title, d, ev.get("location") or site)
    return nev


def to_events(ev: Dict[str, Any], within_days: int,
              point: Optional[Tuple[float, float]] = None,
              today: Optional[date] = None,
              address: Optional[Address] = None) -> List[NormalizedEvent]:
    """The rows to write for one NPS event. Its own coordinates win; `point`,
    or else `address`, is the place ParkPlaces found for one that has none
    (item 5)."""
    if not (ev.get("title") or "").strip():
        return []
    where = own_point(ev) or point
    if where is None and address is None:
        return []
    live, _ = occurrences(ev, today or _today(), within_days)
    return [_build(ev, d, where, address) for d in live]


def called_off(ev: Dict[str, Any], within_days: int, today: Optional[date] = None) -> List[NormalizedEvent]:
    """Tombstones for the days the park called off (item 7). A tombstone needs
    no place: the fingerprint reads none."""
    if not (ev.get("title") or "").strip():
        return []
    _, off = occurrences(ev, today or _today(), within_days)
    return [_build(ev, d, own_point(ev)) for d in off]


# --------------------------------------------------------------------------- #
# The key's budget (item 6)
# --------------------------------------------------------------------------- #
class Stop(Exception):
    """The key's budget is spent or the API refused us: end the run, keep what was read."""


class NpsClient:
    def __init__(self, session, api_key: str, max_requests: int = MAX_REQUESTS,
                 min_interval: float = MIN_INTERVAL_S,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.session, self.api_key = session, api_key
        self.max_requests, self.min_interval = max_requests, min_interval
        self.sleep, self.clock = sleep, clock
        self.requests = 0
        self.remaining: Optional[int] = None
        self.stopped: Optional[str] = None
        self._last: Optional[float] = None

    def _stop(self, why: str) -> None:
        self.stopped = why
        raise Stop(why)

    def get(self, url: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """The JSON body; None for a request that failed and may be retried;
        Stop when the run must end."""
        if self.stopped:
            raise Stop(self.stopped)
        if self.requests >= self.max_requests:
            self._stop(f"request budget spent ({self.requests} of {self.max_requests})")
        if self.remaining is not None and self.remaining <= MIN_REMAINING:
            self._stop(f"the key has {self.remaining} requests left this hour")
        if self._last is not None:
            wait = self.min_interval - (self.clock() - self._last)
            if wait > 0:
                self.sleep(wait)
        self._last = self.clock()
        self.requests += 1
        endpoint = url.rsplit("/", 1)[-1]
        try:
            r = self.session.get(url, params=params, headers={"X-Api-Key": self.api_key}, timeout=60)
        except requests.RequestException as exc:
            print(f"[nps] {endpoint}: {type(exc).__name__}")
            return None
        rem = _int(r.headers.get("X-RateLimit-Remaining"))
        if rem is not None:
            self.remaining = rem
        try:
            body = r.json()
        except ValueError:
            body = None
        err = body.get("error") if isinstance(body, dict) else None
        code = err.get("code") if isinstance(err, dict) else None
        if r.status_code == 429 or code == "OVER_RATE_LIMIT":
            self._stop(f"{endpoint}: HTTP {r.status_code} {code or ''}, rate limited; not retrying".replace("  ", " "))
        if r.status_code in (401, 403):
            self._stop(f"{endpoint}: HTTP {r.status_code} {code or ''}, the key was refused".replace("  ", " "))
        if r.status_code != 200 or not isinstance(body, dict):
            print(f"[nps] {endpoint}: HTTP {r.status_code}")
            return None
        return body

    def get_retry(self, url: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """One retry for a 5xx or a timeout; a refusal is never retried (Stop)."""
        data = self.get(url, params)
        return data if data is not None else self.get(url, params)


# --------------------------------------------------------------------------- #
# Placing an event that has no coordinates (item 5)
# --------------------------------------------------------------------------- #
_GENERIC_WORDS = {"visitor", "visitors", "center", "centre", "contact", "station", "information",
                  "info", "the", "and", "of", "at", "park", "museum", "headquarters"}


def _center_key(name: Any) -> Optional[str]:
    """A centre's name as an event would write it: '(Milepost 384)' dropped,
    and a name that says only 'Visitor Center' is no evidence of anything."""
    key = _norm(re.sub(r"\([^)]*\)", " ", str(name or "")))
    return key if set(key.split()) - _GENERIC_WORDS else None


# A park's name as a location writes it: "Harpers Ferry National Historical
# Park" is "Harpers Ferry", "Great Falls Park" is "Great Falls".
_DESIGNATION_RX = re.compile(
    r"(?:\s+(?:national|state|international|historical|historic|memorial|military|battlefields?|"
    r"monuments?|parks?|parkway|sites?|preserve|reserve|recreation|area|seashore|lakeshore|scenic|"
    r"trails?|heritage|corridor|riverways?|of|and|the))+$")

_US_STATES = frozenset(ALL_STATES.split(","))
_STREET_RX = re.compile(r"(?:^|\s)(\d{1,6}[a-z]?(?:\s+[\w.'\u2019#&-]+){1,7})\s*$", re.I)
_CITY_RX = re.compile(r"^([A-Za-z][A-Za-z.'\u2019 -]{1,40}?)(?:\s+([A-Z]{2})(?:\s+(\d{5})(?:-\d{4})?)?)?\.?$")
_STATE_RX = re.compile(r"^([A-Z]{2})(?:\s+(\d{5})(?:-\d{4})?)?\b")


def street_address(text: Any) -> Optional[Address]:
    """(street, city, state, ZIP) when a location carries a US postal address:
    a numbered street, then a city, then a state code (a ZIP may follow).
    "555 Newcomb St SE / Washington, DC 20032", "Cold Harbor Battlefield
    Visitor Center, 5515 Anderson-Wright Drive, Mechanicsville, VA 23111.",
    "... Peaks of Otter Lodge (85554 Blue Ridge Parkway, Bedford, VA 24523)".
    A street and a state with no city between them is not one: the sync's
    geocoder asks for all three."""
    segs = [x.strip() for x in re.split(r"[,\n()]", str(text or ""))]
    for i, seg in enumerate(segs[:-1]):
        street = _STREET_RX.search(seg)
        town = _CITY_RX.match(segs[i + 1])
        if not (street and town):
            continue
        state, zip5 = town.group(2), town.group(3)
        if not state and i + 2 < len(segs):
            tail = _STATE_RX.match(segs[i + 2])
            if tail:
                state, zip5 = tail.group(1), tail.group(2)
        if state in _US_STATES:
            return street.group(1).strip().rstrip("."), town.group(1).strip(), state, zip5
    return None


def _names_only_the_park(ev: Dict[str, Any]) -> bool:
    """True when the location says nothing, or names the event's own park: the
    only two cases where the park's point is not a guess at a finer place."""
    loc = _norm(ev.get("location"))
    if not loc:
        return True
    name = _DESIGNATION_RX.sub("", _norm(_f(ev, "parkFullName", "parkfullname"))).strip()
    return bool(name) and f" {name} " in f" {loc} "


def _km(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371.0 * 2 * math.asin(min(1.0, math.sqrt(h)))


class ParkPlaces:
    """The places a park publishes, fetched once per park per run."""

    def __init__(self, client: NpsClient) -> None:
        self.client = client
        self.parks: Dict[str, Tuple[float, float]] = {}
        self.centers: Dict[str, List[Tuple[str, Tuple[float, float]]]] = defaultdict(list)
        self.seen: Dict[str, List[Tuple[float, float]]] = defaultdict(list)
        self.asked: Set[str] = set()

    def note(self, code: Optional[str], point: Tuple[float, float]) -> None:
        """A coordinate one of the park's own events carried this run."""
        if code:
            self.seen[code].append(point)

    def fetch(self, codes: Iterable[Optional[str]]) -> None:
        todo = sorted({c for c in codes if c and c not in self.asked})
        for i in range(0, len(todo), PARK_BATCH):
            batch = todo[i:i + PARK_BATCH]
            self._read(PARKS_API, batch, self._add_park)
            self._read(CENTERS_API, batch, self._add_center)
            self.asked.update(batch)

    def _read(self, url: str, batch: List[str], add: Callable[[Dict[str, Any]], None]) -> None:
        start, read, prev = 0, 0, None
        for _ in range(10):
            data = self.client.get_retry(url, {"parkCode": ",".join(batch), "limit": 200, "start": start})
            if data is None:
                return
            rows = [r for r in data.get("data") or [] if isinstance(r, dict)]
            ids = {str(r.get("id")) for r in rows}
            if not rows or ids == prev:                    # a paging parameter ignored (item 1)
                return
            prev = ids
            for r in rows:
                add(r)
            read += len(rows)
            total = _int(data.get("total"))
            if total is None or read >= total:
                return
            start += len(rows)

    def _add_park(self, p: Dict[str, Any]) -> None:
        code, where = str(p.get("parkCode") or "").lower(), _point(p.get("latitude"), p.get("longitude"))
        if code and where:
            self.parks[code] = where

    def _add_center(self, c: Dict[str, Any]) -> None:
        code, where = str(c.get("parkCode") or "").lower(), _point(c.get("latitude"), c.get("longitude"))
        key = _center_key(c.get("name"))
        if code and where and key:
            self.centers[code].append((key, where))

    def place(self, ev: Dict[str, Any]) -> Tuple[Optional[Tuple[float, float]], Optional[Address], str]:
        """(point, address, how) for an event without coordinates, in item 5's
        order. Both None says why not."""
        code = park_code(ev)
        text = f" {_norm(ev.get('title'))} {_norm(ev.get('location'))} "
        named = [(k, w) for k, w in self.centers.get(code or "", []) if f" {k} " in text]
        if named:
            return max(named, key=lambda kw: len(kw[0]))[1], None, "visitor centre"
        addr = street_address(ev.get("location"))
        if addr:
            return None, addr, "street address"
        if not code:
            return None, None, "no park"
        if not _names_only_the_park(ev):
            return None, None, "location names another place"
        park = self.parks.get(code)
        if park is None:
            return None, None, "park has no point"
        known = [w for _, w in self.centers.get(code, [])] + self.seen.get(code, [])
        if not known:
            return None, None, "park size unknown"
        far = max(_km(park, w) for w in known)
        if far > FALLBACK_KM:
            return None, None, "park too large"
        return park, None, "park"


# --------------------------------------------------------------------------- #
# The sweep
# --------------------------------------------------------------------------- #
class Sweep:
    def __init__(self, store: EventStore, client: NpsClient, within_days: int,
                 today: Optional[date] = None) -> None:
        self.store, self.client, self.within_days = store, client, within_days
        self.today = today or _today()
        self.places = ParkPlaces(client)
        self.seen_ids: Set[str] = set()
        self.pending: List[Dict[str, Any]] = []
        self.stats: Counter = Counter()
        self.unplaced: Counter = Counter()
        self.placed_by: Counter = Counter()
        self.incomplete: List[str] = []
        # The rows this run wrote, by fingerprint: the count the log prints, so
        # it can no longer say 903 for Virginia's 129 (item 1). Two NPS events
        # sharing a fingerprint are one row here, as they are in the store.
        self.rows: Set[str] = set()

    def _params(self, state: str, page: int) -> Dict[str, Any]:
        return {"stateCode": state, "pageSize": PAGE_SIZE, "pageNumber": page,
                "dateStart": self.today.isoformat(),
                "dateEnd": (self.today + timedelta(days=self.within_days)).isoformat(),
                "expandRecurring": "false"}

    def _write(self, ev: Dict[str, Any], point: Optional[Tuple[float, float]],
               address: Optional[Address] = None) -> None:
        for nev in to_events(ev, self.within_days, point, self.today, address):
            if self.store.upsert(nev) in ("added", "updated", "merged"):
                self.rows.add(nev.fingerprint)

    def _take(self, ev: Dict[str, Any], rep: Counter) -> None:
        eid = event_id(ev)
        if not eid or not (ev.get("title") or "").strip():
            rep["no_id"] += 1
            return
        if eid in self.seen_ids:                          # another state's page listed it (item 4)
            rep["repeat"] += 1
            return
        self.seen_ids.add(eid)
        rep["new"] += 1
        if str(ev.get("isfree") or "").strip().lower() == "true":
            rep["free"] += 1
        for nev in called_off(ev, self.within_days, self.today):
            if self.store.cancel(nev, "nps: the park lists this day as cancelled or excluded") == "cancelled":
                rep["tombstoned"] += 1
        where = own_point(ev)
        if where is None:
            self.pending.append(ev)
            rep["pending"] += 1
            return
        self.places.note(park_code(ev), where)
        self._write(ev, None)

    def state(self, state: str) -> Counter:
        rep: Counter = Counter()
        listed, prev_ids, done = 0, None, False
        for page in range(1, MAX_PAGES + 1):
            data = self.client.get_retry(API, self._params(state, page))
            if data is None:
                break
            rows = [r for r in data.get("data") or [] if isinstance(r, dict)]
            rep["pages"] += 1
            ids = {event_id(r) for r in rows}
            if rows and ids == prev_ids:
                print(f"[nps] {state}: page {page} repeats page {page - 1}; the API ignored pageNumber")
                break
            prev_ids = ids
            for ev in rows:
                self._take(ev, rep)
            listed += len(rows)
            total = _int(data.get("total"))
            size = _int(data.get("pagesize")) or PAGE_SIZE     # the API's page, not ours (item 2)
            if not rows or len(rows) < size or (total is not None and listed >= total):
                done = True
                break
        rep["listed"] = listed
        if not done:
            self.incomplete.append(state)
        return rep

    def flush(self) -> None:
        """Place and write the events that had no coordinates of their own."""
        if not self.pending:
            return
        self.places.fetch(park_code(ev) for ev in self.pending)
        for ev in self.pending:
            where, addr, how = self.places.place(ev)
            if where is None and addr is None:
                self.unplaced[how] += 1
                continue
            self.placed_by[how] += 1
            self._write(ev, where, addr)
        self.pending = []

    def pending_parks(self) -> int:
        return len({park_code(ev) for ev in self.pending} - self.places.asked - {None})

    def run(self, states: List[str], save: Callable[[], None] = lambda: None,
            clock: Callable[[], float] = time.monotonic) -> None:
        last_save = clock()
        todo = list(states)
        try:
            while todo:
                st = todo[0]
                before = len(self.rows)
                rep = self.state(st)
                todo.pop(0)
                self.stats.update(rep)
                print(f"[nps] {st}: listed {rep['listed']} in {rep['pages']} page(s), "
                      f"{rep['new']} new to this run ({rep['free']} free), "
                      f"{len(self.rows) - before} row(s) written, {rep['pending']} waiting for "
                      f"their park's place, {rep['tombstoned']} called-off day(s)")
                if self.pending_parks() >= PARK_BATCH:
                    self.flush()
                if clock() - last_save >= SAVE_EVERY_S:
                    save()
                    last_save = clock()
            self.flush()
        except Stop as exc:
            # The state being read when the budget ran out, and every one after
            # it, was not swept. Events already read but still waiting for
            # their park stay unwritten: the client refuses every request now.
            print(f"[nps] STOPPED: {exc}")
            self.incomplete += [s for s in todo if s not in self.incomplete]
            self.unplaced["budget spent before their park was read"] += len(self.pending)
            self.pending = []

    def summary(self) -> str:
        s = self.stats
        placed = sum(self.placed_by.values())
        free_share = f"{100 * s['free'] / s['new']:.0f}%" if s["new"] else "n/a"
        left = self.client.remaining if self.client.remaining is not None else "?"
        return (f"[nps] done: {s['new']} events ({free_share} free) in {s['pages']} page(s), "
                f"{len(self.rows)} row(s) written, {s['tombstoned']} called-off day(s) tombstoned; "
                f"{s['pending']} without coordinates: {placed} placed "
                f"({', '.join(f'{k} {v}' for k, v in self.placed_by.most_common()) or 'none'}), "
                f"{sum(self.unplaced.values())} unplaced "
                f"({', '.join(f'{k} {v}' for k, v in self.unplaced.most_common()) or 'none'}); "
                f"{self.client.requests} request(s), {left} left this hour"
                + (f"; INCOMPLETE: {','.join(self.incomplete)}" if self.incomplete else ""))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import NPS events into the Mapsee store.")
    ap.add_argument("--states", default=ALL_STATES,
                    help="comma-separated USPS state codes to sweep (default: all 50, DC and the territories)")
    ap.add_argument("--within-days", type=int, default=90)
    ap.add_argument("--store", default="mapsee_events.json")
    ap.add_argument("--api-key", default=os.environ.get("NPS_API_KEY"))
    ap.add_argument("--max-requests", type=int, default=MAX_REQUESTS,
                    help=f"stop after this many API requests (the key allows 1,000/hour; default {MAX_REQUESTS})")
    a = ap.parse_args(argv)
    if not a.api_key:
        print("[nps] no NPS_API_KEY set — skipping (free key at nps.gov/subjects/developer)")
        return 0
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    client = NpsClient(session, a.api_key, max_requests=a.max_requests, min_interval=MIN_INTERVAL_S)
    store = EventStore(a.store)
    sweep = Sweep(store, client, a.within_days)
    states = [s.strip().upper() for s in a.states.split(",") if s.strip()]
    sweep.run(states, save=store.save)
    store.save()
    print(sweep.summary())
    print(f"[nps] store now holds {len(store.records)} unique events.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
