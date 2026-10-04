#!/usr/bin/env python3
"""
mapsee_ingest_toronto_rec.py - what is on at Toronto's community centres this
week: the City's drop-in programme schedule, and the EarlyON family drop-ins.

    python mapsee_ingest_toronto_rec.py --config toronto_rec_sources.json \
        --store feeds_events.json [--only dropin|earlyon] [--max-minutes 10]

Both come from the City of Toronto's open data portal, read ONLY through the
CKAN datastore API (`/api/3/action/datastore_search`). The host's robots.txt
says `Allow: /api/` and `Disallow: /dataset/*/resource/*/download/*`, so the
CSV/JSON downloads that look like the obvious thing to fetch are the refused
half. Open Government Licence - Toronto; the attribution sentence is the last
paragraph of every row (see THE LICENCE LINE below).

WHY THIS SOURCE. A community centre is the place most people can walk to, and
what it runs - lane swim, pickleball, a youth drop-in, a toddler gym - is the
thing no ticketing feed lists. Measured 2026-10-03: the Drop-in resource of
"Registered Programs and Drop In Courses Offering" holds 33,457 sessions,
2026-10-01..11-12, at 177 locations (141 community recreation centres, 17
schools), every one a drop-in by the City's own definition ("a program ...
where registration is not required"). It is refreshed weekly. EarlyON lists 231
child-and-family centres, 209 of them with weekly drop-in hours, refreshed daily.

------------------------------------------------------------------------------
DROP-IN: A THREE-RESOURCE JOIN
------------------------------------------------------------------------------

1. THE SESSION ROW KNOWS NO PLACE. It carries `Location ID`, a course title, a
   section, an age band, a date and four INTEGER clock columns (Start Hour,
   Start Minute, End Hour, End Min) in America/Toronto. The venue name and the
   street come from the Locations resource (1,884 rows); the point comes from a
   THIRD dataset, parks-and-recreation-facilities, keyed by `LOCATIONID` - a
   string there, an integer here. 175 of the 177 locations join (31,611 of
   31,734 upcoming rows, 99.6%); Riverdale Farm and Native Scarborough Family
   Centre have no point and are counted as unplaceable rather than guessed.
   Locations writes a missing value as the literal string 'None' - 1,846 of
   1,884 `Street No Suffix` values are that word - so it is read as null.

2. PAGE IT, DO NOT SORT IT. 33,457 rows are four requests at limit=10000. The
   generic CKAN adapter reads one request sorted by start descending at 2,000
   rows, which on this table returns 11-10..11-12 and drops THIS week - the
   only week anybody is planning.

3. A WEIGHT ROOM IS A ROOM, NOT A PROGRAMME. 3,630 upcoming rows are
   facility-use slots - "Weight/Cardio Room", "Walking/Running Track", "Open
   Fitness Studio" - and York's track alone publishes eleven a day. Same
   refusal as OpenActive's FacilityUse/Slot ("a bookable badminton court at
   19:00 is an empty room"). The prefixes are in the config.
   ...and the same room comes back under other titles as a SHAPE: Table
   Tennis 9:00-20:00, Squash 7:00-20:00, Snooker 9:00-16:00, FitnessTO
   "Walking" 11:00-19:00 (the refused track by another name). Of the sessions
   kept on 2026-10-03, 713 ran six hours or more; `facility_use_shapes`
   refuses those titles from six hours on, read on the joined STRETCH so six
   back-to-back hours count too: 563 more sessions. Youth Zone, Club: Social,
   a caregiver play morning and a long lane swim are staffed programmes and
   stay (177 kept rows run 6 h+), which is why the cut is not bare duration.

4. ONE ROW PER CONTIGUOUS STRETCH of a title, age band, place and day. The
   shared fingerprint is title|date|venue (make_fingerprint), so Lane Swim at
   07:30, 11:45 and 20:00 at one pool would silently merge into whichever came
   first and keep one time. The first version folded the whole day into one
   row from the first start to the last end, and 2,506 of its 23,232 rows then
   spanned hours when nothing runs - 11,346 gap-hours, "Lane Swim" at Antibes
   11:30-21:00 for three short swims - and ../mapsee pulses a pin "Happening
   now" from starts_at to its end, straight through every gap. So exact
   repeats fold (359), back-to-back or overlapping sessions join into one
   stretch (a start within JOIN_MINUTES of the running end; 0 such joins
   needed the tolerance on 2026-10-03), and a GAP ENDS THE ROW: 2,416
   title-days split, and 0 written rows span a gap. Each row names the day's
   other stretches ("Also on this day: ..."). Identity is the stretch's
   FIRST clock, in source_id and in the fingerprint's basis, exactly as
   bibliocommons and perfectmind key a storytime or a session; a shuffled
   re-read writes identical rows and a second run adds 0 and rekeys 0. A
   start that MOVES orphans the old stretch until its date passes (at most
   the ~6 weeks the City publishes) - the same trade those adapters make.
   ...and the AGE BAND IS IN THE TITLE, because 1,235 title/place/days carry two
   bands: Pickleball for 19+ and Pickleball for 60+ are different sessions for
   different people, and on one key 1,421 audiences fold into somebody else's
   listing.

5. "RESERVE A SPOT" IS NOT A DROP-IN, AND IT IS MOSTLY A DUPLICATE. 690 of the
   33,457 rows need an advance booking. 616 of them have a drop-in twin at the same place,
   title, day and minute - the same class published both ways - so refusing
   them costs 74 sessions and keeps the "anyone can turn up" promise.

6. THE PRICE IS NOT IN THE DATA, AND THE WORDING IS A CONTRACT. ../mapsee's
   migration 0227 tags `offer:free` from the row's own text, so a row may say
   "Free" only where toronto.ca says so (read 2026-10-03): leisure swim ("free
   at all indoor and outdoor pools"), every skating drop-in ("All drop-in
   programs are free"), older-adult lane swim (the fee table says Free), the
   Enhanced Youth Spaces' arts, hobbies and interest drop-ins ("Free programs
   include photography, barbering ... DJing and music recording" - the page
   names no sport, so EYS - Sports falls through: 84 rows that said free on
   2026-10-03 now say fees may apply), and EVERY City drop-in at the 38 Free
   Centres ("All City-delivered, registered and drop-in programs for all age
   groups"). Lane swim, aquatic fitness and FitnessTO classes say "Drop-in
   fee". Everything else says fees may apply, which is what the City's own
   sports page says. No amounts: they change every January. The rules are
   `price_rules` in the config, first match wins, each with its evidence URL.
   A Free Centre is matched on its Location ID, never its address: on number
   + street, 9 PARKS at a centre's address matched too (FreeCentres).

7. DATED ROWS, NEVER STANDING ROWS. The City publishes ~6 weeks ahead and drops
   holidays itself: 0 sessions on Thanksgiving Monday 2026-10-12 against 835-988
   on every other Monday. A weekly roller would invent that Monday back.

------------------------------------------------------------------------------
EARLYON: WEEKLY HOURS AS TEXT, WRITTEN AS DATED ROWS
------------------------------------------------------------------------------

`dropinHours` is text: "Monday: 9:00 a.m. - noon ; 1:00 p.m. - 3:30 p.m. |
Tuesday: ...". Days split on '|', ranges on ';', times are "h[:mm] a.m./p.m."
or "noon". Over the 209 centres: 888 centre-days a week, 1,086 ranges, 0
fragments unparsed. 22 centres write the literal word 'None', read as no hours.

WHY NOT ONE STANDING ROW PER CENTRE with recurring_days, which is what the
field is for: `recurring_days` holds ONE [open, close] pair per weekday, and
154 of the 888 centre-days carry two or more ranges - 139 of them with a gap
between (a centre open 9-noon and 4-6 is CLOSED at 2). One pair would publish
those gaps as open hours or drop a session. And a standing row never dies
(docs/agents/openactive-and-standing-rows.md): 94 of the 209 are run by the two
school boards and follow a school calendar the data does not carry.

So: one dated row per centre per open STRETCH, `horizon_days` (14) ahead,
re-read daily - 251 centre-days split at a gap in the 2026-10-03 read, 1,886
rows. A changed timetable is wrong for at most the horizon, not for ever.
The `website` field is typed by hand and once held a staff member's email:
`_website` refuses anything that is not a web address (see there).
`skip_dates` (Ontario's public holidays) are not written - the City's own
drop-in table is empty on Thanksgiving, and listing a locked door is worse than
missing an open one. The adapter warns when that list runs out.

------------------------------------------------------------------------------
THE LICENCE LINE
------------------------------------------------------------------------------

The Open Government Licence - Toronto asks for an attribution statement. It is
the description's LAST paragraph and under 200 characters, so the sync's
_cap_prose keeps it when it trims (TAIL_KEEP_MAX), exactly as OpenActive's
CC-BY line survives.

Every HTTP request carries the MapseeAggregator UA and is paced >= 1.1 s on the
host. A 401/403/429 is never retried, and because both sources live on one
host it ends the run's reading: the second source is not asked. A 5xx or a
timeout (30 s) is retried twice; any other failure of one source never stops
the other. --max-minutes (default 10) is a deadline no request starts after,
and the store is saved after EVERY source, so a step cancelled by its
timeout-minutes keeps the drop-ins even if EarlyON was cut off.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlsplit

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    sys.exit("This script needs Python 3.9+ (zoneinfo).")

from mapsee_ingest import EventStore, NormalizedEvent, make_fingerprint, norm_categories

UA = "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)"
DEFAULT_API = "https://ckan0.cf.opendata.inter.prod-toronto.ca/api/3/action/datastore_search"
# The datastore answers 10,000 rows a request on this portal (measured: four
# pages for the 33,457-row drop-in table). Fewer pages means fewer requests on a
# host we pace at one a second.
PAGE_LIMIT = 10000
MIN_INTERVAL_S = 1.1
# A page of 10,000 rows answers in about 2 s (7 requests, 14.6 s with pacing,
# 2026-10-03), so 30 s is generous. With 3 tries and 2 + 4 s of backoff the
# worst call is 96 s, and the run deadline below stops any request from
# STARTING after --max-minutes: 10 minutes + one 30 s read + the save sits
# inside a 15-minute step. At timeout=60 the worst case was 7 x 186 s = 21.7 min.
REQUEST_TIMEOUT_S = 30
DEFAULT_MAX_MINUTES = 10.0
# Two sessions this close are one stretch: a 5-minute changeover between two
# blocks of one programme is not a gap anybody plans around (the same tolerance
# as mapsee_ingest_perfectmind's GRID_JOIN_MINUTES). On 2026-10-03 every join
# was exactly back to back or overlapping; 0 needed the tolerance.
JOIN_MINUTES = 5
# A transient failure is retried; a refusal never is (see Refused).
_RETRYABLE = {408, 500, 502, 503, 504}
_REFUSALS = {401, 403, 429}
# The licence line must stay under the sync's TAIL_KEEP_MAX (200) to survive a
# trim. Checked at load, not trusted.
ATTRIBUTION_MAX = 200


class Refused(Exception):
    """The publisher said no (401/403/429). Reported, never retried or worked
    around - and the reader asks that host nothing more this run."""


class OutOfTime(Exception):
    """The run deadline (--max-minutes) has passed: no request starts after it.
    Whatever was written before it is saved."""


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
class Reader:
    """A paced reader for one CKAN datastore. Counts its own requests, because
    the request budget is part of what a dry run has to report."""

    def __init__(self, session, api: str = DEFAULT_API, min_interval: float = MIN_INTERVAL_S,
                 tries: int = 3, sleep=time.sleep, clock=time.monotonic,
                 deadline: Optional[float] = None, timeout: float = REQUEST_TIMEOUT_S) -> None:
        self.session = session
        self.api = api
        self.min_interval = min_interval
        self.tries = tries
        self.sleep = sleep
        self.clock = clock
        # On `clock`'s scale (time.monotonic by default); None = no deadline.
        self.deadline = deadline
        self.timeout = timeout
        self.requests = 0
        # Set by the first 401/403/429. Both sources live on one host, so a
        # refusal of one is a refusal of the next: a 429 above all means stop.
        self.refused: Optional[str] = None
        self._last: Optional[float] = None

    def _pace(self) -> None:
        if self._last is not None:
            wait = self.min_interval - (self.clock() - self._last)
            if wait > 0:
                self.sleep(wait)

    def call(self, params: Dict[str, Any]) -> Dict[str, Any]:
        last: Exception = RuntimeError("no attempt made")
        for attempt in range(self.tries):
            if self.refused:
                raise Refused(f"{self.refused} earlier this run; nothing more is asked of this host")
            self._pace()
            if self.deadline is not None and self.clock() >= self.deadline:
                raise OutOfTime("run deadline reached; no request starts after it")
            try:
                self.requests += 1
                r = self.session.get(self.api, params=params, timeout=self.timeout)
            except Exception as exc:  # noqa: BLE001 - timeouts, resets
                last = exc
            else:
                if r.status_code in _REFUSALS:
                    self.refused = f"HTTP {r.status_code} from {self.api}"
                    raise Refused(self.refused)
                if r.status_code == 200:
                    try:
                        body = r.json()
                    except ValueError as exc:
                        last = exc
                    else:
                        if body.get("success") is True and isinstance(body.get("result"), dict):
                            return body["result"]
                        raise RuntimeError(f"CKAN said no: {str(body.get('error'))[:200]}")
                elif r.status_code not in _RETRYABLE:
                    raise RuntimeError(f"HTTP {r.status_code} from {self.api}")
                else:
                    last = RuntimeError(f"HTTP {r.status_code}")
            finally:
                self._last = self.clock()
            if attempt + 1 < self.tries:
                self.sleep(2 * (attempt + 1))
        raise last

    def resource(self, resource_id: str, fields: Optional[Iterable[str]] = None,
                 limit: int = PAGE_LIMIT) -> List[Dict[str, Any]]:
        """Every row of one datastore resource, paged by offset in `_id` order.

        The `total` is read on every page. If it moves, the table was replaced
        mid-read (the drop-in table is rebuilt weekly) and the offsets no longer
        mean the same rows, so the walk restarts once rather than returning a
        seam with rows missing or doubled."""
        for _ in range(2):
            rows: List[Dict[str, Any]] = []
            total: Optional[int] = None
            moved = False
            while True:
                params: Dict[str, Any] = {"resource_id": resource_id, "limit": limit,
                                          "offset": len(rows), "sort": "_id asc"}
                if fields:
                    params["fields"] = ",".join(fields)
                res = self.call(params)
                page = res.get("records") or []
                t = res.get("total")
                if total is None:
                    total = t
                elif t != total:
                    moved = True
                    break
                rows.extend(page)
                if not page or len(page) < limit or (total is not None and len(rows) >= total):
                    break
            if not moved:
                return rows
        raise RuntimeError(f"resource {resource_id} changed size twice while being read")


# ---------------------------------------------------------------------------
# small readers
# ---------------------------------------------------------------------------
def _none(v: Any) -> Optional[str]:
    """The Locations resource writes a missing value as the string 'None'."""
    if v is None:
        return None
    s = str(v).strip()
    return None if (not s or s.lower() in ("none", "null", "nan")) else s


def _int(v: Any) -> Optional[int]:
    s = _none(v)
    if s is None:
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def _norm(s: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower().replace("’", "'"))


def _today(tz) -> date:
    """MAPSEE_TODAY=YYYYMMDD fixes "today" for the tests; otherwise it is today
    IN TORONTO - the runner is on UTC, and at 21:00 local it is already tomorrow
    there."""
    fixed = os.environ.get("MAPSEE_TODAY")
    if fixed:
        return datetime.strptime(fixed, "%Y%m%d").date()
    return datetime.now(tz).date()


def clock(minutes: int) -> str:
    """600 -> '10:00 a.m.', 720 -> 'noon', 1290 -> '9:30 p.m.'."""
    h, m = divmod(int(minutes), 60)
    if h == 12 and m == 0:
        return "noon"
    suffix = "a.m." if h < 12 or h == 24 else "p.m."
    return f"{(h % 12) or 12}:{m:02d} {suffix}"


def span(a: int, b: int) -> str:
    """'9:00-11:30 a.m.' when both ends share a half of the day, else both said."""
    ca, cb = clock(a), clock(b)
    if ca[-4:] == cb[-4:] and "noon" not in (ca, cb):
        return f"{ca[:-5]}-{cb}"
    return f"{ca}-{cb}"


def _join(parts: List[str]) -> str:
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def _sentence(s: str) -> str:
    """End with one full stop: a list of times already ends in "a.m."."""
    return s if s.endswith(".") else s + "."


def _stamp(d: date, minutes: int, tz) -> Tuple[str, str]:
    """(naive local ISO, UTC ISO) for a day and a minute-of-day in tz."""
    h, m = divmod(int(minutes), 60)
    local = datetime(d.year, d.month, d.day, h, m)
    utc = local.replace(tzinfo=tz).astimezone(timezone.utc)
    return local.strftime("%Y-%m-%dT%H:%M:00"), utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def _in_box(lat: float, lon: float, box: Optional[List[float]]) -> bool:
    if not box:
        return True
    s, w, n, e = box
    return s <= lat <= n and w <= lon <= e


def _description(paragraphs: List[Optional[str]], attribution: str) -> str:
    body = [p.strip() for p in paragraphs if p and p.strip()]
    return "\n\n".join(body + [attribution.strip()])


# ---------------------------------------------------------------------------
# DROP-IN
# ---------------------------------------------------------------------------
def age_label(amin: Any, amax: Any) -> str:
    """'ages 7+', 'ages 13-17', 'age 12', 'all ages'. The table is in YEARS
    (the Registered table uses months - 156 - and is not read here). An upper
    bound of 90 or more is "no upper bound" written as a number (0-98 on an
    adapted family skate)."""
    lo, hi = _int(amin), _int(amax)
    if hi is not None and hi >= 90:
        hi = None
    lo = lo or 0
    if hi is None:
        return "all ages" if lo <= 0 else f"ages {lo}+"
    if lo == hi:
        return f"age {lo}"
    return f"ages {lo}-{hi}"


def street_line(loc: Dict[str, Any]) -> Optional[str]:
    """'1369 St. Clair Ave W' from the Locations columns, 'None' read as null.
    Line 1 only: the sync adds city, province and postal code itself."""
    no = _none(loc.get("Street No"))
    name = _none(loc.get("Street Name"))
    if not (no and name):
        return name
    parts = [no + (_none(loc.get("Street No Suffix")) or ""), name,
             (_none(loc.get("Street Type")) or "").rstrip("."),
             _none(loc.get("Street Direction")) or ""]
    return " ".join(p for p in parts if p)


def location_points(rows: List[Dict[str, Any]], box: Optional[List[float]] = None
                    ) -> Dict[str, Tuple[float, float, Optional[str]]]:
    """LOCATIONID -> (lat, lon, toronto.ca page) from parks-and-recreation-facilities.

    Two LOCATIONIDs appear twice (a centre and the park it stands in, metres
    apart); the Community Centre row wins because that is the building the
    programme is in."""
    out: Dict[str, Tuple[float, float, Optional[str]]] = {}
    rank: Dict[str, int] = {}
    for r in rows:
        lid = _none(r.get("LOCATIONID"))
        if not lid:
            continue
        try:
            geom = r.get("geometry")
            geom = json.loads(geom) if isinstance(geom, str) else geom
            lon, lat = float(geom["coordinates"][0]), float(geom["coordinates"][1])
        except (TypeError, ValueError, KeyError, IndexError):
            continue
        if not _in_box(lat, lon, box):
            continue
        r_rank = 0 if "centre" in (r.get("TYPE") or "").lower() else 1
        if lid in out and rank[lid] <= r_rank:
            continue
        url = _none(r.get("URL"))
        out[lid] = (lat, lon, _website(url) if url and url.startswith("http") else None)
        rank[lid] = r_rank
    return out


class FreeCentres:
    """Toronto's Free Centres, matched on the City's own LOCATION ID - never on
    the address. On street number + name, 47 of the 1,884 locations matched the
    page's 38 centres on 2026-10-04: the other 9 are PARKS at a centre's address
    (Moss Park at 150 Sherbourne, Cedarbrook Park at 91 Eastpark, Jimmie Simpson
    Park ...), whose winter rinks and summer courts would have been called free.
    Each of the 38 has exactly one non-park location; its id is in the config
    beside the page's name and street, which stay as provenance. A centre the
    City re-numbers falls back to "fees may apply" - the safe direction."""

    def __init__(self, entries: List[Dict[str, Any]]) -> None:
        self.ids = {i for i in (_int(e.get("location_id")) for e in entries) if i is not None}

    def __contains__(self, loc: Any) -> bool:
        lid = _int(loc.get("Location ID")) if isinstance(loc, dict) else _int(loc)
        return lid is not None and lid in self.ids


def _starts(value: str, prefixes: Iterable[str]) -> bool:
    low = value.lower()
    return any(low.startswith(p.lower()) for p in prefixes)


def _has(value: str, needles: Iterable[str]) -> bool:
    low = value.lower()
    return any(n.lower() in low for n in needles)


def exclusion(row: Dict[str, Any], rules: Dict[str, Any]) -> Optional[str]:
    """Why this session is not a drop-in anyone can turn up to, or None."""
    title = (row.get("Course Title") or "").strip()
    section = (row.get("Section") or "").strip()
    if _starts(section, rules.get("reservation_sections_prefix") or ()):
        return "reservation required (Reserve a Spot)"
    if _starts(title, rules.get("facility_use_titles_prefix") or ()):
        return "facility use, not a programme"
    if title.lower() in {t.lower() for t in (rules.get("restricted_titles") or ())}:
        return "restricted audience"
    return None


def facility_shape(row: Dict[str, Any], minutes: int, rules: List[Dict[str, Any]]) -> bool:
    """A table, a court or a track left open all day is a room, not a
    programme (lesson 3), whatever its title. Read on the STRETCH, so six
    back-to-back hours of table tennis are caught as surely as one six-hour
    session. Each rule names title prefixes, optionally a section, and the
    length from which the shape is a facility."""
    title = row.get("Course Title") or ""
    section = row.get("Section") or ""
    for rule in rules:
        if minutes < float(rule.get("min_hours", 6)) * 60:
            continue
        if rule.get("section_prefix") and not _starts(section, rule["section_prefix"]):
            continue
        if _starts(title, rule.get("title_prefix") or ()):
            return True
    return False


def stretches(times: Iterable[Tuple[int, int]], join: int = JOIN_MINUTES
              ) -> List[Tuple[int, int, List[Tuple[int, int]]]]:
    """Distinct (start, end) sessions -> [(start, end, sessions)] per CONTIGUOUS
    stretch. A session that starts within `join` minutes of the running end
    (back to back, or overlapping it) continues the stretch; anything later is
    a gap, and a gap is where the row ends."""
    out: List[List[Any]] = []
    for a, b in sorted(set(times)):
        if out and a <= out[-1][1] + join:
            out[-1][1] = max(out[-1][1], b)
            out[-1][2].append((a, b))
        else:
            out.append([a, b, [(a, b)]])
    return [(a, b, parts) for a, b, parts in out]


def _hm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _bump(stats: Dict[str, int], key: str, n: int = 1) -> None:
    stats[key] = stats.get(key, 0) + n


def price(row: Dict[str, Any], free_centre: bool, rules: List[Dict[str, Any]]) -> Tuple[str, str]:
    """(tier, sentence) from the first matching price rule. Tiers: free, fee, may."""
    title = row.get("Course Title") or ""
    section = row.get("Section") or ""
    for rule in rules:
        if rule.get("free_centre") and not free_centre:
            continue
        if rule.get("section_prefix") and not _starts(section, rule["section_prefix"]):
            continue
        if rule.get("title_prefix") and not _starts(title, rule["title_prefix"]):
            continue
        if rule.get("title_contains") and not _has(title, rule["title_contains"]):
            continue
        return rule["tier"], rule["text"]
    return "may", "Drop-in fees may apply; the centre's page has the details."


def category(row: Dict[str, Any], cfg: Dict[str, Any]) -> Tuple[str, List[str]]:
    """(primary, extras). The SECTION decides, a title prefix may override it,
    and children's sessions add `kids` as a secondary so the family door finds a
    toddler gym without taking it off the movement door."""
    title = row.get("Course Title") or ""
    section = row.get("Section") or ""
    primary = None
    for prefix, key in sorted((cfg.get("category_by_title_prefix") or {}).items(),
                              key=lambda kv: -len(kv[0])):
        if title.lower().startswith(prefix.lower()):
            primary = key
            break
    if primary is None:
        for prefix, key in sorted((cfg.get("category_by_section_prefix") or {}).items(),
                                  key=lambda kv: -len(kv[0])):
            if section.lower().startswith(prefix.lower()):
                primary = key
                break
    primary = primary or cfg.get("category_default", "community")
    extras: List[str] = []
    hi = _int(row.get("Age Max"))
    if (hi is not None and hi <= int(cfg.get("kids_age_max", 12))) \
            or _has(title, cfg.get("kids_title_words") or ()):
        extras.append("kids")
    # Doors a title belongs on, said outright, so the sync's keyword pass has
    # nothing left to guess: see `extras_by_title_prefix` in the config.
    for prefix, keys in (cfg.get("extras_by_title_prefix") or {}).items():
        if title.lower().startswith(prefix.lower()):
            extras.extend(keys)
    return primary, norm_categories(primary, extras)


def dropin_events(rows: List[Dict[str, Any]], locations: Dict[int, Dict[str, Any]],
                  points: Dict[str, Tuple[float, float, Optional[str]]],
                  src: Dict[str, Any], common: Dict[str, Any], tz, today: date,
                  stats: Dict[str, int]) -> List[NormalizedEvent]:
    """Session rows -> one dated row per (location, title + age band, day)."""
    horizon = today + timedelta(days=int(src.get("horizon_days", 90)))
    rules = src.get("exclude") or {}
    free = FreeCentres(src.get("free_centres") or [])
    groups: Dict[Tuple[int, str, str], List[Dict[str, Any]]] = {}
    for row in rows:
        day_s = str(row.get("First Date") or "")[:10]
        try:
            day = date.fromisoformat(day_s)
        except ValueError:
            stats["bad date"] = stats.get("bad date", 0) + 1
            continue
        if str(row.get("Last Date") or "")[:10] != day_s:
            # Never seen (0 of 33,457 on 2026-10-03), and a range row would need
            # its weekday expanded - refused rather than read as one day.
            stats["multi-day row"] = stats.get("multi-day row", 0) + 1
            continue
        if day < today:
            stats["past"] = stats.get("past", 0) + 1
            continue
        if day > horizon:
            stats["beyond horizon"] = stats.get("beyond horizon", 0) + 1
            continue
        why = exclusion(row, rules)
        if why:
            stats[why] = stats.get(why, 0) + 1
            continue
        a = (_int(row.get("Start Hour")), _int(row.get("Start Minute")) or 0)
        b = (_int(row.get("End Hour")), _int(row.get("End Min")) or 0)
        if a[0] is None or b[0] is None or b[0] * 60 + b[1] <= a[0] * 60 + a[1]:
            stats["no usable time"] = stats.get("no usable time", 0) + 1
            continue
        lid = _int(row.get("Location ID"))
        title = re.sub(r"\s+", " ", (row.get("Course Title") or "").strip())
        if lid is None or not title:
            stats["no title or place"] = stats.get("no title or place", 0) + 1
            continue
        shown = f"{title}, {age_label(row.get('Age Min'), row.get('Age Max'))}"
        row = dict(row, _start=a[0] * 60 + a[1], _end=b[0] * 60 + b[1])
        groups.setdefault((lid, shown, day_s), []).append(row)

    out: List[NormalizedEvent] = []
    attribution = common["attribution"]
    shapes = rules.get("facility_use_shapes") or []
    for (lid, shown, day_s), sessions in sorted(groups.items()):
        loc = locations.get(lid)
        pt = points.get(str(lid))
        if not loc or not pt:
            _bump(stats, "unplaceable (no point)", len(sessions))
            continue
        venue = re.sub(r"\s+", " ", (loc.get("Location Name") or "").strip())
        # The input order is a weekly rebuild's row order, not a fact: sort, so
        # the session that speaks for the group is the same on every read.
        sessions.sort(key=lambda s: (s["_start"], s["_end"], s.get("Section") or ""))
        times = sorted({(s["_start"], s["_end"]) for s in sessions})
        # A session published twice (two sections, or one row twice) is one
        # session: 359 exact repeats among the sessions kept on 2026-10-03.
        _bump(stats, "repeat sessions folded", len(sessions) - len(times))
        first = sessions[0]
        runs = []
        for a, b, parts in stretches(times):
            if facility_shape(first, b - a, shapes):
                _bump(stats, "facility use, not a programme",
                      sum(1 for s in sessions if a <= s["_start"] and s["_end"] <= b))
                continue
            runs.append((a, b, parts))
        if not runs:
            continue
        primary, extras = category(first, src)
        for s in sessions[1:]:
            p2, e2 = category(s, src)
            extras = norm_categories(primary, extras, [p2], e2)
        tier, price_text = price(first, loc in free, src.get("price_rules") or [])
        day = date.fromisoformat(day_s)
        age = age_label(first.get("Age Min"), first.get("Age Max"))
        if len(runs) > 1:
            _bump(stats, "title-days split at a gap")
        for a, b, parts in runs:
            _bump(stats, f"price: {tier}")
            sl, su = _stamp(day, a, tz)
            el, eu = _stamp(day, b, tz)
            # ONE ROW PER STRETCH (lesson 4): the row's own clock is the truth,
            # and the day's other stretches are said, not spanned.
            others = [span(x, y) for x, y, _ in runs if (x, y) != (a, b)]
            desc = _description([
                f"City of Toronto drop-in program, {age}. No registration needed.",
                _sentence(f"Sessions: {_join([span(x, y) for x, y in parts])}") if len(parts) > 1 else None,
                _sentence(f"Also on this day: {_join(others)}") if others else None,
                price_text,
                src.get("notice"),
            ], attribution)
            hm = _hm(a)
            ev = NormalizedEvent(
                source=src.get("source", "toronto-rec"),
                # STABLE ACROSS RUNS: the City's `_id` is a row number in this
                # week's rebuild, and Course_ID names a whole series (3,297 of
                # them for 33,457 sessions), so neither is one stretch's
                # identity. The place, the title as shown, the day and the
                # stretch's first clock are; a later end updates the same row.
                source_id=f"{lid}|{_norm(shown)}|{day_s}|{hm}",
                name=shown,
                description=desc,
                start_local=sl, start_utc=su, end_local=el, end_utc=eu,
                timezone=common.get("timezone"),
                venue_name=venue,
                latitude=pt[0], longitude=pt[1],
                # The City's own asset point for its own building.
                coords_exact=True,
                address=street_line(loc),
                city=common.get("city"), region=common.get("region"),
                country=common.get("country"),
                postal_code=_none(loc.get("Postal Code")),
                category=primary, categories=extras,
                promoter=src.get("promoter"),
                ticket_url=pt[2] or src.get("dataset"),
            )
            # The clock joins the key's BASIS (never the shown name), as
            # mapsee_ingest_bibliocommons and _perfectmind do: on title|day|
            # venue alone the 07:30 and the 20:00 lane swim are one row. The
            # street joins the place: a name is not unique on its own (two
            # EarlyON centres are both "Eastview ...").
            ev.fingerprint = make_fingerprint(f"{shown} {hm}", day_s,
                                              f"{venue} {ev.address or ''}".strip())
            out.append(ev)
    return out


def ingest_dropin(store: EventStore, reader: Reader, src: Dict[str, Any],
                  common: Dict[str, Any], tz) -> Dict[str, int]:
    res = src["resources"]
    stats: Dict[str, int] = {}
    sessions = reader.resource(res["sessions"])
    stats["sessions read"] = len(sessions)
    locs = reader.resource(res["locations"], fields=src.get("location_fields"))
    pts = reader.resource(res["points"], fields=src.get("point_fields"))
    locations = {}
    for l in locs:
        lid = _int(l.get("Location ID"))
        if lid is not None:
            locations[lid] = l
    points = location_points(pts, common.get("bbox"))
    if not points:
        # Without the point table nothing can be placed, and a coordless
        # Canadian row is dropped by the sync anyway (it geocodes with US
        # Census). Say so here, where the reason is known.
        raise RuntimeError("the facility point table came back empty; nothing placed")
    evs = dropin_events(sessions, locations, points, src, common, tz, _today(tz), stats)
    for ev in evs:
        store.upsert(ev)
    stats["rows written"] = len(evs)
    return stats


# ---------------------------------------------------------------------------
# EARLYON
# ---------------------------------------------------------------------------
_DAY_NAMES = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_TIME = r"(?:noon|midnight|\d{1,2}(?::\d{2})?\s*[ap]\.?\s*m\.?)"
_RANGE = re.compile(rf"^\s*({_TIME})\s*[-–—]\s*({_TIME})\s*$", re.I)


def _minutes(s: str) -> int:
    s = s.strip().lower()
    if s == "noon":
        return 720
    if s == "midnight":
        return 0
    m = re.match(r"(\d{1,2})(?::(\d{2}))?\s*([ap])", s)
    h = int(m.group(1)) % 12 + (12 if m.group(3) == "p" else 0)
    return h * 60 + int(m.group(2) or 0)


def parse_hours(text: Optional[str]) -> Tuple[Dict[int, List[Tuple[int, int]]], List[str]]:
    """'Monday: 9:00 a.m. - noon ; 1:00 p.m. - 3:30 p.m. | Tuesday: ...' ->
    ({0: [(540, 720), (780, 930)], 1: [...]}, unparsed fragments).

    Ranges are sorted and exact repeats folded; OVERLAPS are kept as written
    (27 centre-days list "10:00 - 11:00" inside "10:00 - noon" - two rooms or
    two programmes - and the description says both)."""
    week: Dict[int, List[Tuple[int, int]]] = {}
    bad: List[str] = []
    for part in (text or "").split("|"):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^([A-Za-z]+)\s*:\s*(.*)$", part)
        if not m or m.group(1).lower() not in _DAY_NAMES:
            bad.append(part)
            continue
        wd = _DAY_NAMES.index(m.group(1).lower())
        for r in m.group(2).split(";"):
            r = r.strip()
            if not r:
                continue
            mm = _RANGE.match(r)
            if not mm:
                bad.append(r)
                continue
            a, b = _minutes(mm.group(1)), _minutes(mm.group(2))
            if b <= a:
                bad.append(r)
                continue
            week.setdefault(wd, []).append((a, b))
    return {d: sorted(set(v)) for d, v in week.items()}, bad


def _postal(full_address: Optional[str]) -> Optional[str]:
    m = re.search(r"\b([A-Z]\d[A-Z])\s?(\d[A-Z]\d)\b", full_address or "")
    return f"{m.group(1)} {m.group(2)}" if m else None


_HOST = re.compile(r"^(?=.{4,253}$)[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+$")


def _website(url: Optional[str]) -> Optional[str]:
    """A centre's own web page, or None - and NEVER an email address.

    The field is typed by hand. On 2026-10-04 one of the 231 EarlyON centres
    held a staff member's email there, and "https://" + it is a URL whose
    userinfo is a person: it reached 11 rows' "Tickets / info" line. Anything
    with an '@', userinfo, whitespace or no dotted host name is refused, and
    the caller falls back to the City's finder page."""
    u = _none(url)
    if not u:
        return None
    u = u.strip()
    if "@" in u or re.search(r"\s", u):
        return None
    if not re.match(r"^https?://", u, re.I):
        u = "https://" + u.lstrip("/")
    try:
        parts = urlsplit(u)
        host = (parts.hostname or "").lower()
        bad = parts.username is not None or parts.password is not None or parts.port is not None
    except ValueError:
        return None
    return u if (not bad and _HOST.match(host)) else None


def earlyon_events(rows: List[Dict[str, Any]], src: Dict[str, Any], common: Dict[str, Any],
                   tz, today: date, stats: Dict[str, int]) -> List[NormalizedEvent]:
    horizon = int(src.get("horizon_days", 14))
    skip = {str(d)[:10] for d in (src.get("skip_dates") or ())}
    attribution = common["attribution"]
    title = src.get("title", "EarlyON family drop-in")
    out: List[NormalizedEvent] = []
    for c in rows:
        name = re.sub(r"\s+", " ", (_none(c.get("program_name")) or "").strip())
        # 'None' is how the table writes "no hours" (22 centres on 2026-10-04);
        # read raw, the word was counted as 22 unparsed fragments.
        week, bad = parse_hours(_none(c.get("dropinHours")))
        if bad:
            _bump(stats, "unparsed hour fragments", len(bad))
        if not week:
            _bump(stats, "centre with no drop-in hours")
            continue
        try:
            lat, lon = float(c.get("lat")), float(c.get("lng"))
        except (TypeError, ValueError):
            lat = lon = None
        if lat is None or not _in_box(lat, lon, common.get("bbox")) or not name:
            _bump(stats, "centre unplaceable")
            continue
        _bump(stats, "centres listed")
        agency = _none(c.get("agency"))
        building = _none(c.get("buildingName"))
        who = f"Run by {agency}" if agency else None
        # "Run by X, at X" says nothing twice; nor does a building the centre's
        # own name already contains.
        if who and building and _norm(building) not in _norm(name) and _norm(building) != _norm(agency):
            who += f", at {building}"
        extras = []
        langs = _none(c.get("languages"))
        if langs:
            extras.append(f"Languages: {langs.strip().rstrip('.')}.")
        if _none(c.get("french_language_program")) == "Yes":
            extras.append("A French-language program.")
        if _none(c.get("indigenous_program")) == "Yes":
            extras.append("An Indigenous program.")
        site = _none(c.get("website"))
        url = _website(site)
        if site and not url:
            _bump(stats, "website refused (not a web address)")
        url = url or src.get("finder_url")
        for i in range(horizon):
            day = today + timedelta(days=i)
            ranges = week.get(day.weekday())
            if not ranges:
                continue
            if day.isoformat() in skip:
                _bump(stats, "holiday skipped")
                continue
            # ONE ROW PER STRETCH, as for the drop-ins: a centre open 9-noon and
            # 4-6 is closed at 2, and a row spanning 9-6 pulses "happening now"
            # through the gap. Overlapping ranges (27 centre-days list 10-11
            # inside 10-noon: two rooms or two programmes) are one stretch.
            runs = stretches(ranges)
            if len(runs) > 1:
                _bump(stats, "centre-days split at a gap")
            hours = _join([span(a, b) for a, b, _ in runs])
            day_line = _sentence(f"Drop-in hours this day: {hours}") + (
                " It is closed in between." if len(runs) > 1 else "")
            for a, b, _parts in runs:
                sl, su = _stamp(day, a, tz)
                el, eu = _stamp(day, b, tz)
                desc = _description([
                    src.get("blurb"),
                    day_line,
                    " ".join(x for x in [(who + ".") if who else None] + extras if x) or None,
                    src.get("notice"),
                ], attribution)
                hm = _hm(a)
                ev = NormalizedEvent(
                    source=src.get("source", "toronto-earlyon"),
                    source_id=f"{_none(c.get('loc_id')) or _norm(name)}|{day.isoformat()}|{hm}",
                    name=title,
                    description=desc,
                    start_local=sl, start_utc=su, end_local=el, end_utc=eu,
                    timezone=common.get("timezone"),
                    venue_name=name,
                    latitude=lat, longitude=lon,
                    # The City's point for a centre it funds and lists.
                    coords_exact=True,
                    address=_none(c.get("address")),
                    city=common.get("city"), region=common.get("region"),
                    country=common.get("country"),
                    postal_code=_postal(c.get("full_address")),
                    category=src.get("category", "kids"),
                    categories=norm_categories(src.get("category", "kids"), src.get("categories")),
                    promoter=agency,
                    ticket_url=url,
                )
                # Name AND street: "Eastview EarlyON Child and Family Centre" is
                # two centres (20 Waldock St and 86 Blake St), and on the name
                # alone the second merged into the first on every day both were
                # open. The clock joins the basis so a split day is two rows.
                ev.fingerprint = make_fingerprint(f"{title} {hm}", day.isoformat(),
                                                  f"{name} {ev.address or ''}".strip())
                out.append(ev)
    if skip and today.isoformat() > max(skip):
        print(f"[toronto-rec] WARNING: skip_dates ends {max(skip)}; add next year's holidays")
    return out


def ingest_earlyon(store: EventStore, reader: Reader, src: Dict[str, Any],
                   common: Dict[str, Any], tz) -> Dict[str, int]:
    stats: Dict[str, int] = {}
    rows = reader.resource(src["resource"], fields=src.get("fields"))
    stats["centres read"] = len(rows)
    evs = earlyon_events(rows, src, common, tz, _today(tz), stats)
    for ev in evs:
        store.upsert(ev)
    stats["rows written"] = len(evs)
    return stats


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
KINDS = {"dropin": ingest_dropin, "earlyon": ingest_earlyon}


def load_config(path: str) -> Dict[str, Any]:
    cfg = json.loads(open(path, encoding="utf-8").read())
    attribution = (cfg.get("attribution") or "").strip()
    if not attribution or len(attribution) > ATTRIBUTION_MAX:
        raise ValueError(f"attribution must be 1-{ATTRIBUTION_MAX} characters "
                         "(the sync keeps a final paragraph only that short)")
    if not cfg.get("timezone"):
        raise ValueError("config needs a timezone")
    return cfg


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import Toronto drop-in and EarlyON schedules into the Mapsee store.")
    ap.add_argument("--config", required=True)
    ap.add_argument("--store", default="mapsee_events.json")
    ap.add_argument("--only", choices=sorted(KINDS), help="read just one kind of source")
    ap.add_argument("--max-minutes", type=float, default=DEFAULT_MAX_MINUTES,
                    help="whole-run deadline; no request starts after it, and the store is "
                         "saved after every source (0 = none)")
    a = ap.parse_args(argv)

    started = time.monotonic()
    cfg = load_config(a.config)
    tz = ZoneInfo(cfg["timezone"])
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept": "application/json"})
    reader = Reader(session, cfg.get("api") or DEFAULT_API,
                    deadline=started + a.max_minutes * 60 if a.max_minutes else None)
    store = EventStore(a.store)
    total = 0
    # A source that raised after an upsert would leave work unsaved; a run that
    # wrote nothing still writes the file, which the sync step expects.
    unsaved, saved = False, False
    for src in cfg.get("sources", []):
        kind = src.get("kind")
        if kind not in KINDS or (a.only and kind != a.only):
            continue
        label = src.get("name", kind)
        if reader.refused:
            print(f"[toronto-rec] {label}: NOT READ - {reader.refused} earlier this run (one host)")
            continue
        before = reader.requests
        unsaved = True
        try:
            stats = KINDS[kind](store, reader, src, cfg, tz)
        except Refused as exc:
            print(f"[toronto-rec] {label} REFUSED: {exc} - not retried, and the host is asked nothing more")
            continue
        except OutOfTime as exc:
            print(f"[toronto-rec] {label} STOPPED: {exc} (--max-minutes {a.max_minutes:g})")
            break
        except Exception as exc:  # noqa: BLE001 - one source never stops the other
            print(f"[toronto-rec] {label} FAILED: {exc}")
            continue
        total += stats.get("rows written", 0)
        print(f"[toronto-rec] {label}: {stats.get('rows written', 0)} rows "
              f"in {reader.requests - before} requests")
        for k, v in sorted(stats.items()):
            if k != "rows written":
                print(f"[toronto-rec]     {k}: {v}")
        # After EVERY source, so a step cancelled by its timeout-minutes keeps
        # what was read (docs/agents/ci-and-jobs.md): two saves, not one.
        store.save()
        unsaved, saved = False, True
    if unsaved or not saved:
        store.save()
    st = store.stats
    print(f"[toronto-rec] done in {(time.monotonic() - started):.0f} s: {total} rows written "
          f"(added {st.get('added', 0)}, updated {st.get('updated', 0)}, merged {st.get('merged', 0)}, "
          f"rekeyed {st.get('rekeyed', 0)}, rejected {st.get('rejected', 0)}) in {reader.requests} "
          f"requests; store now holds {len(store.records)} unique events.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
