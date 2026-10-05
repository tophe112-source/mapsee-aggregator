#!/usr/bin/env python3
"""
mapsee_ingest_barcelona.py - what is on at Barcelona's community places: the
centres civics, casals de barri, youth and children's casals, seniors' casals
and municipal libraries, read from the City's own daily agenda.

    python mapsee_ingest_barcelona.py --config barcelona_sources.json \
        --store feeds_events.json [--max-minutes 3]

The source is the Ajuntament de Barcelona open dataset "Agenda d'actes i
activitats de la ciutat" (agenda-diaria, CC BY 4.0), the Guia BCN export, read
through the portal's CKAN datastore API (`/api/3/action/datastore_search`), a
documented public API (catalog_curate.DOCUMENTED_API_TYPES has `ckan`), and so
exempt from robots.txt - which here is no file at all: /robots.txt answers 302
to /challenge, a "Bot Detection" page (2026-10-05; robots_txt.CHALLENGE_RX reads
it as a challenge since that day, which binds the portal's own pages, not this
documented API). The same resource as a JSON download redirects to
/challenge too, and the ASIA XML feeds on w10.bcn.cat are refused by robots.txt
- so the datastore is the only way in, and a 401/403/429, a redirect or a 200
that is not JSON ends the run's reading. Two requests: the agenda, and
the City's list of culture and leisure facilities (equipament-cultura-i-lleure),
which is where a row's venue NAME comes from (below).

WHY THIS SOURCE, AND WHY ONLY PART OF IT. It is a whole city's agenda -
stadium concerts, museum shows, L'Auditori's season, schoolyards open on
Saturdays. The owner's request was "what is going on at my local community
place", so a row is kept only when its address is a community facility in the
City's own register (`venue_kinds`). Measured live 2026-10-05 17:5x UTC (today
2026-10-05, horizon 90 days): 5,741 rows, 4,546 in the window, 2,590 at the
address of one of 351 community facilities. Refused: 128 as a museum's
programme, then 1,256 for registration, 424 as paid centre programmes, 21 as
online, private, a call for entries or a schoolyard's hours, 16 as a room's or
a collection point's opening hours, 10 whose days cannot be read. 735 registers
kept -> 1,237 rows at 131 places (394 timed sessions, 611 with a start only,
227 runs, 5 date-only), 2 requests, 5 s. 1,077 say "Admission: free." (87%), 123 state a fee, 13
pay-what-you-can, 24 no price.

------------------------------------------------------------------------------
1. THE CLOCK IN start_date IS A PLACEHOLDER; THE TIMETABLE HTML IS THE TIME
------------------------------------------------------------------------------
start_date/end_date carry 03:00 (3,802 of 5,957 rows) or 12:00 (1,767). Of the
291 rows with any other clock, 2 agree with their own timetable. So only the
DATE part is read, and the time comes from the `timetable` column: an HTML
table with Periode / Dies / Hores / Preus / Observacions columns, rowspans
included ("a les 18.30 h", "de 09.00 h a 14.00 h i de 16.00 h a 21.30 h",
"d'11.00 h a 13.00 h"). A row whose hours cannot be read ("Per determinar",
"Matinal", "Tot el dia") is written date-only, never at 03:00 or 12:00.

2. A MULTI-DAY ROW IS A TIMETABLE; IT IS WRITTEN ON ITS DATES
------------------------------------------------------------------------------
A row running 2026-09-14..12-18 whose Dies cell says "De dilluns a divendres
excepte 12 octubre" is a weekly drop-in (a youth space, a conversation group,
ping-pong on Tuesdays and Fridays). It is expanded to one row per contiguous
session on each date inside start..end and today..today+horizon: never one row
spanning the gaps (../mapsee pulses a pin "Happening now" from start to end).
The cell's grammar is read whole or not at all: weekdays, "de X a Y", lists,
"excepte <dates>", "(excepte el mes d'agost)", explicit dates ("27 octubre i
24 novembre"), "4rt dimecres del mes", "Ultim dissabte del mes". A Periode
cell ("Del 15 setembre al 20 novembre") binds the weekdays on ITS line only
(Montreal's lesson: pooled, a range wrote dates its own line had ended).
"Cada quinze dies", "un divendres al mes" and anything else not understood is
refused, counted, never guessed. A line whose hours say "Tancat" removes its
dates. Public holidays (`skip_dates`) are not written for a weekly pattern; an
explicit date is the source's own claim and is kept.
A weekday that LABELS a date ("Dissabte 24 d'octubre") is that date, not every
Saturday; a label that is not its date's weekday, or weekdays bounded by a
date range, are refused.
A genuinely continuous RUN is one row: an exhibition or installation (by its
title, "IV Mostra ..." and "... - Exposicio d'obres" included) whose hours are
opening hours. It is written date-only from start_date to end_date, with the
opening hours in its text - unless every line lists its own dates (three
screenings are three rows, never seven weeks 'happening now'). Nothing else is
a run: a title naming a space ("Espai 12/16", "Sala de joc", "Trobada 'Punt
TIC'") open on 4+ weekdays, and anything else open 6 h+ on 4+ weekdays (a
collection point, a month-long swap), are opening hours, and opening hours are
not an event - refused, counted (16 registers that had been 377 rows, 2026-10-05).
Sessions that only touch (16:30-17:30, 17:30-18:30) stay two rows. A one-day
row whose weekday contradicts its own date is refused.

3. VENUE NAME: THE ADDRESS, JOINED TO THE CITY'S FACILITY REGISTER
------------------------------------------------------------------------------
The agenda has no venue name, description or URL column. Its address does
carry the City's road id and street number, and the culture-and-leisure
facility list (resource f3721b17, same portal) carries the same ids for every
centre civic, casal and library. Joined on road id + number (both non-empty:
rows outside Barcelona have neither, and their placeholder point lands on a
facility with no address) and within `join_max_m` of the facility's point.
Measured 2026-10-03: of 2,753 joined rows, 2,672 sit within 1 m. One address
can hold facilities on different points (Vallcivera 3: a library, and a
children's casal 86 m away), so the facility NEAREST the row's own point names
it; the highest `venue_kinds` entry only breaks a tie inside one building
(Centre Civic Sagrada Familia, not the library on its first floor; within
CO_LOCATED_M), and the rest of that building is listed. A line whose notes name
ANOTHER community facility ("CC La Sedeta") is held there and skipped. An
address that also holds a MUSEUM keeps a row only at the facility's own point
and nearer it than the museum (the Palau Blaugrana's basketball at Casal de
l'Avi Barça's number is refused); where the two share one point the row is the
museum's (L'Auditori's season, Disseny Hub's shows) unless a reviewed
`museum_shared_kept` entry says otherwise (Comerç 36, the Convent de Sant
Agusti). The facility's point is the City's register for that building, so
coords_exact is True.

4. WHAT IS KEPT: WHAT ANYONE CAN TURN UP TO
------------------------------------------------------------------------------
"Cal inscripcio previa" ("prior registration required") is on 63% of the rows
at community places, and it is a refusal, as Linked Events' "Maksuton, vaatii
ilmoittautumisen" and Montreal's reserved free skates are: a free talk you must
sign up for is not a drop-in. Each line is read on its own: "No cal inscripcio
previa", "sense reserva previa" and "places per ordre d'arribada" keep a row
(and say so: 49 registers); a line about GROUP bookings ("grups ... amb
reserva previa") is not about the individual. A centre's paid programme is
refused whatever its title: the line "Reduccio i subvencio dels imports als
cursos / tallers" sits on 410 rows - quarterly workshops, but also its 8.75 EUR
talks and 9.50 EUR walks, all sold through the centre's registration - and so
are a monthly or termly fee ("30 EUR mensual"), "N sessions" and a "Curs"
title.
Also refused, each counted: an online or Zoom session, a closure, a call for
entries ("Convocatoria", a "Concurs" whose window is office hours), space
opening hours (an open schoolyard, a study room, a computer room, a youth
space open every weekday), a governance meeting.

5. THE PRICE IS A CONTRACT WITH ../mapsee's 0227
------------------------------------------------------------------------------
0227 (../mapsee/tools/measure_deals.py is its twin) reads French "gratuit" and
Spanish "gratuito/gratis", but NOT Catalan "gratuit/gratuita" with its
diaeresis. So a row whose own Preus cell is a plain free statement ("Entrada
Gratuita", "Entrada lliure") says "Admission: free." in English; a stated
amount says "Price: <the cell> (not free)." - 0227's FREE_NEG vetoes the whole
row; "Taquilla inversa" and "Donatiu voluntari" say "pay what you can" (0227's
pwyw); a free cell for some people
only ("Entrada gratuita per a menors de 3 anys") is not free. The price is the
FIRST line and the description fits the sync's 800 characters, so _cap_prose
never cuts the "(not free)". Source notes that would read as free on a row
that is not free are withheld, not rewritten.

6. LINK
------------------------------------------------------------------------------
guia.barcelona.cat/ca/detall/<slug>_<register_id>.html is the City's public
page for each register id (robots.txt allows /ca/detall/, Crawl-delay 10; the
slug is ignored by the server - a wrong one returned the same 200 page,
2026-10-05). The adapter never fetches it; it only links it. The link becomes
the row's "Tickets / info" line, which mapsee_prune_cancelled.py probes; since
2026-10-05 that walk keeps each host's Crawl-delay (probe_all), so this host is
asked at most once every 10 s and the rest wait for a later run.

CC BY 4.0 asks for attribution: the description's LAST paragraph, under 200
characters, which the sync's _cap_prose keeps.

Every request carries the MapseeAggregator UA and is paced (1 s; the host
states no Crawl-delay). A 401/403/429, any redirect, or a 200 that is not the
API's JSON (a challenge page) is never retried and ends the run's reading; a
5xx or a timeout (45 s) is retried twice. --max-minutes is a deadline no
request starts after, and the store is saved after every source.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
import unicodedata
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

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
DEFAULT_API = "https://opendata-ajuntament.barcelona.cat/data/api/3/action/datastore_search"
PAGE_LIMIT = 10000
MIN_INTERVAL_S = 1.0
REQUEST_TIMEOUT_S = 45
DEFAULT_MAX_MINUTES = 3.0
JOIN_MAX_M = 250.0
# A day open this long is a building's opening hours, not a session.
RUN_DAY_MINUTES = 6 * 60
RUN_WEEKDAYS = 4
# A multi-day row with no readable days at all is a run only this short.
RUN_NO_DAYS_MAX = 14
# mapsee_spam.MAX_SPAN_DAYS: a longer claim loses its end in EventStore.upsert.
MAX_RUN_SPAN_DAYS = 400
_RETRYABLE = {408, 500, 502, 503, 504}
_REFUSALS = {401, 403, 429}
ATTRIBUTION_MAX = 200
DESCRIPTION_MAX = 800   # mapsee_supabase_sync.DESCRIPTION_MAX
NOTES_MIN = 40


class Refused(Exception):
    """The publisher said no (401/403/429). Reported, never retried."""


class OutOfTime(Exception):
    """The run deadline (--max-minutes) has passed: no request starts after it."""


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
class Reader:
    """A paced reader for one CKAN datastore; counts its own requests."""

    def __init__(self, session, api: str = DEFAULT_API, min_interval: float = MIN_INTERVAL_S,
                 tries: int = 3, sleep=time.sleep, clock=time.monotonic,
                 deadline: Optional[float] = None, timeout: float = REQUEST_TIMEOUT_S) -> None:
        self.session = session
        self.api = api
        self.min_interval = min_interval
        self.tries = tries
        self.sleep = sleep
        self.clock = clock
        self.deadline = deadline
        self.timeout = timeout
        self.requests = 0
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
                # No redirects: the portal's downloads redirect to /challenge,
                # a bot check, which is a refusal and never a page to follow.
                r = self.session.get(self.api, params=params, timeout=self.timeout,
                                     allow_redirects=False)
            except Exception as exc:  # noqa: BLE001 - timeouts, resets
                last = exc
            else:
                if r.status_code in _REFUSALS:
                    self.refused = f"HTTP {r.status_code} from {self.api}"
                    raise Refused(self.refused)
                if 300 <= r.status_code < 400:
                    self.refused = (f"HTTP {r.status_code} redirect to "
                                    f"{r.headers.get('location', '?')} from {self.api}")
                    raise Refused(self.refused)
                if r.status_code == 200:
                    # The API answers JSON or nothing. A 200 that is a page
                    # (the portal's /challenge "Bot Detection" page, served
                    # inline) is a refusal: asked again, it would be asked of
                    # a host that has said no.
                    ctype = str((r.headers or {}).get("content-type", "")).lower()
                    try:
                        body = None if "html" in ctype else r.json()
                    except ValueError:
                        body = None
                    if not isinstance(body, dict):
                        self.refused = f"HTTP 200 that is not the API's JSON ({ctype or 'no type'}) from {self.api}"
                        raise Refused(self.refused)
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
        """Every row, paged by offset in `_id` order; restarts once if `total`
        moves mid-read (the table is rebuilt daily)."""
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
def _s(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v)).strip() if v is not None else ""


def _fold(s: str) -> str:
    """Lower case without accents, apostrophes straight: 'inscripció' and
    'inscripcio', 'd’11' and "d'11" are one pattern."""
    s = (s or "").replace("’", "'").replace("‘", "'").replace("´", "'").replace("`", "'")
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", _fold(s))


def _rid(v: Any) -> str:
    # The CSV behind the datastore starts with a BOM, and the first column of
    # every row (both resources) carries it: '\ufeff99400751127'.
    return _s(v).lstrip("\ufeff").strip()


def _today(tz) -> date:
    fixed = os.environ.get("MAPSEE_TODAY")
    if fixed:
        return datetime.strptime(fixed, "%Y%m%d").date()
    return datetime.now(tz).date()


def _bump(stats: Dict[str, int], key: str, n: int = 1) -> None:
    stats[key] = stats.get(key, 0) + n


def _day(v: Any) -> Optional[date]:
    m = re.match(r"^\s*(\d{4})-(\d{2})-(\d{2})", _s(v))
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _m(v: Any) -> float:
    return math.radians(float(v))


def metres(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    la1, lo1, la2, lo2 = _m(a[0]), _m(a[1]), _m(b[0]), _m(b[1])
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 12742000.0 * math.asin(math.sqrt(h))


def _point(row: Dict[str, Any]) -> Optional[Tuple[float, float]]:
    try:
        lat, lon = float(_s(row.get("geo_epgs_4326_lat"))), float(_s(row.get("geo_epgs_4326_lon")))
    except ValueError:
        return None
    if not (-90 < lat < 90 and -180 < lon < 180) or (lat == 0 and lon == 0):
        return None
    return lat, lon


def hm(minutes: int) -> str:
    h, m = divmod(int(minutes) % (24 * 60), 60)
    return f"{h:02d}:{m:02d}"


def _stamp(d: date, minutes: int, tz) -> Tuple[str, str]:
    local = datetime(d.year, d.month, d.day) + timedelta(minutes=int(minutes))
    utc = local.replace(tzinfo=tz).astimezone(timezone.utc)
    return local.strftime("%Y-%m-%dT%H:%M:00"), utc.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# the timetable HTML
# ---------------------------------------------------------------------------
_TR_RX = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.S | re.I)
_CELL_RX = re.compile(r"<(th|td)\b([^>]*)>(.*?)</\1>", re.S | re.I)
_ROWSPAN_RX = re.compile(r"rowspan\s*=\s*[\"']?(\d+)", re.I)
_CLASS_RX = re.compile(r"class\s*=\s*[\"']([^\"']+)", re.I)


def cell_text(inner: str) -> str:
    import html as _html
    s = re.sub(r"<br\s*/?>|</p>|</div>|</li>", "\n", inner or "", flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = _html.unescape(s).replace("\xa0", " ")
    lines = [re.sub(r"[ \t\r]+", " ", ln).strip() for ln in s.split("\n")]
    return "\n".join(ln for ln in lines if ln)


def timetable(html: Any) -> List[Dict[str, str]]:
    """The timetable as rows of {period, weekdays, hours, prices, description},
    rowspans carried down (an exhibition's one price cell spans three lines)."""
    trs = _TR_RX.findall(str(html or ""))
    if not trs:
        return []
    cols: List[str] = []
    for _tag, attrs, _inner in _CELL_RX.findall(trs[0]):
        m = _CLASS_RX.search(attrs)
        cols.append(m.group(1).strip() if m else "?")
    out: List[Dict[str, str]] = []
    carry: Dict[str, List[Any]] = {}
    for tr in trs[1:]:
        cells = _CELL_RX.findall(tr)
        row: Dict[str, str] = {}
        ci = 0
        for col in cols:
            if col in carry and carry[col][1] > 0:
                row[col] = carry[col][0]
                carry[col][1] -= 1
                continue
            if ci < len(cells):
                _tag, attrs, inner = cells[ci]
                ci += 1
                m = _ROWSPAN_RX.search(attrs)
                span = int(m.group(1)) if m else 1
                row[col] = cell_text(inner)
                if span > 1:
                    carry[col] = [row[col], span - 1]
        if any(row.values()):
            out.append(row)
    return out


# ---------------------------------------------------------------------------
# days
# ---------------------------------------------------------------------------
WEEKDAYS = {"dilluns": 0, "dimarts": 1, "dimecres": 2, "dijous": 3, "divendres": 4,
            "dissabte": 5, "dissabtes": 5, "diumenge": 6, "diumenges": 6}
MONTHS = {"gener": 1, "febrer": 2, "marc": 3, "abril": 4, "maig": 5, "juny": 6, "juliol": 7,
          "agost": 8, "setembre": 9, "octubre": 10, "novembre": 11, "desembre": 12}
ORDINALS = {"primer": 1, "1r": 1, "1er": 1, "segon": 2, "2n": 2, "2on": 2, "tercer": 3, "3r": 3,
            "3er": 3, "quart": 4, "4t": 4, "4rt": 4, "cinque": 5, "5e": 5, "ultim": -1, "darrer": -1}
_WD = r"(?:dilluns|dimarts|dimecres|dijous|divendres|dissabtes?|diumenges?)"
_MON = r"(?:gener|febrer|marc|abril|maig|juny|juliol|agost|setembre|octubre|novembre|desembre)"
_ORD = r"(?:primer|1er|1r|segon|2on|2n|tercer|3er|3r|quart|4rt|4t|cinque|5e|ultim|darrer)"
_DATE_RX = re.compile(rf"\b(\d{{1,2}})\s*(?:de\s+|d')?\s*({_MON})\b")
_RANGE_WD_RX = re.compile(rf"\bde\s+({_WD})\s+a\s+({_WD})\b")
_ORDINAL_RX = re.compile(rf"\b((?:{_ORD})(?:\s*(?:,|i)\s*(?:{_ORD}))*)\s+({_WD})\s+(?:del?|de\s+cada)\s+mes\b")
_IMPRECISE_RX = re.compile(r"cada\s+quinze|quinzenal|alterns|\bun[a]?\s+(?:" + _WD[3:-1] + r")\s+al\s+mes|"
                           r"no\s+d\w*finits|a\s+convenir|consulte|per\s+determinar|pendent|"
                           r"\bun\s+cop\s+al\s+mes|\bcada\s+mes\b|\bmensual")
_EXCEPT_MONTH_RX = re.compile(rf"\(?\s*excepte\s+(?:el\s+)?mes\s+(?:de\s+|d')({_MON})\s*\)?")
_FESTIUS_RX = re.compile(rf"\b(?:i\s+)?(?:{_WD}\s+)?festius\b")
# "Dissabte 24 d'octubre": the weekday is the date's LABEL, not a weekly day.
# Read as both, the row was written on every Saturday from the 24th (Teatre
# Musical "De 9 a 5", 2026-10-05: 4 performances the source never lists).
_WD_LABEL_RX = re.compile(rf"\b({_WD})\s*,?\s*(?=(\d{{1,2}})\s*(?:de\s+|d')?\s*({_MON})\b)")
# "Dissabtes del 3 d'octubre al 19 de desembre": weekdays BOUNDED by a range.
# Spelled out, the range is every day; 0 such cells on 2026-10-05, refused.
_WD_IN_RANGE_RX = re.compile(rf"{_WD}\s*,?\s*(?:del|de\s+l'|de)\s*\d{{1,2}}\s*(?:(?:de\s+|d')?\s*{_MON})?\s+"
                             rf"(?:al|a\s+l'|a)\s*\d")


def _place(day: int, month: int, lo: date, hi: date) -> Optional[date]:
    """The date with that day and month inside lo..hi (the row's own range)."""
    for y in range(lo.year, hi.year + 1):
        try:
            d = date(y, month, day)
        except ValueError:
            continue
        if lo <= d <= hi:
            return d
    return None


_DAYLIST_RX = re.compile(rf"\b(\d{{1,2}}(?:\s*(?:,|\bi\b)\s*\d{{1,2}})+)\s*(?:de\s+|d')?\s*({_MON})\b")


def _expand_day_lists(text: str) -> str:
    """'1 i 6 de gener' -> '1 gener 6 gener': every day keeps its month."""
    return _DAYLIST_RX.sub(lambda m: " ".join(f"{d} {m.group(2)}" for d in re.findall(r"\d{1,2}", m.group(1))),
                           text)


_MONTH_NAMES = {v: k for k, v in MONTHS.items()}
# "del 5 al 9 d'octubre", "de l'1 al 15 de novembre", "del 29 de juliol al 2
# d'agost": every day between. Read as two dates, the bare first day was
# dropped ("del 5" is residue) and only the 9th was written; inside "Tancat
# ... del 7 al 14 de gener" only the 14th was closed.
_DATE_RANGE_RX = re.compile(rf"\b(?:del|de\s+l'|de)\s*(\d{{1,2}})\s*(?:(?:de\s+|d')?\s*({_MON}))?\s+"
                            rf"(?:al|a\s+l'|a)\s*(\d{{1,2}})\s*(?:de\s+|d')?\s*({_MON})\b")
DATE_RANGE_MAX_DAYS = 92


def _expand_date_ranges(text: str) -> Tuple[str, bool]:
    """(text with each 'del D1 [m1] al D2 m2' spelled out as 'D month' days,
    False when a range could not be read: a backwards or impossible one, or
    one longer than DATE_RANGE_MAX_DAYS, which is a period, not a list of days)."""
    ok = True

    def one(m: "re.Match[str]") -> str:
        nonlocal ok
        d1, m1 = int(m.group(1)), MONTHS[m.group(2) or m.group(4)]
        d2, m2 = int(m.group(3)), MONTHS[m.group(4)]
        try:
            a = date(2000, m1, d1)                      # a leap year: 29 febrer reads
            b = date(2000 if (m2, d2) >= (m1, d1) else 2001, m2, d2)
        except ValueError:
            ok = False
            return " "
        if (b - a).days > DATE_RANGE_MAX_DAYS:
            ok = False
            return " "
        out = []
        while a <= b:
            out.append(f"{a.day} {_MONTH_NAMES[a.month]}")
            a += timedelta(days=1)
        return " " + " ".join(out) + " "
    return _DATE_RANGE_RX.sub(one, text), ok


def _dates_in(text: str, lo: date, hi: date) -> Tuple[Set[date], int]:
    """Explicit 'D month' dates in folded text; (dates inside lo..hi, outside)."""
    got: Set[date] = set()
    outside = 0
    text = _expand_day_lists(text)
    for m in _DATE_RX.finditer(text):
        d = _place(int(m.group(1)), MONTHS[m.group(2)], lo, hi)
        if d:
            got.add(d)
        else:
            outside += 1
    return got, outside


def _nth_weekday(y: int, m: int, wd: int, n: int) -> Optional[date]:
    if n > 0:
        first = date(y, m, 1)
        d = first + timedelta(days=(wd - first.weekday()) % 7 + 7 * (n - 1))
        return d if d.month == m else None
    nxt = date(y + (m == 12), m % 12 + 1, 1)
    last = nxt - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - wd) % 7)


class DaySpec:
    """What a Dies cell says: weekdays, explicit dates, nth-weekday-of-month,
    exceptions. `ok` is False when anything in the cell was not understood."""

    def __init__(self) -> None:
        self.weekdays: Set[int] = set()
        self.dates: Set[date] = set()
        self.ordinals: List[Tuple[int, int]] = []     # (n, weekday)
        self.except_dates: Set[date] = set()
        self.except_months: Set[int] = set()
        self.ok = True
        self.why = ""
        self.festius = False
        self.on_holidays = False      # "Diumenge i festius": holidays too
        self.ranged = False           # some dates came from "del D1 al D2"
        self.outside = 0

    @property
    def empty(self) -> bool:
        return not (self.weekdays or self.dates or self.ordinals)

    @property
    def pattern(self) -> bool:
        """A weekly or monthly pattern (holidays apply), not a list of dates."""
        return bool(self.weekdays or self.ordinals)

    def covers(self, d: date) -> bool:
        if d in self.except_dates or d.month in self.except_months:
            return False
        if d in self.dates or d.weekday() in self.weekdays:
            return True
        return any(_nth_weekday(d.year, d.month, wd, n) == d for n, wd in self.ordinals)


def parse_days(cell: str, lo: date, hi: date) -> DaySpec:
    spec = DaySpec()
    t = _fold(cell or "").replace("\n", " ")
    t = re.sub(r"\s+", " ", t).strip(" .")
    if not t:
        return spec
    if _IMPRECISE_RX.search(t):
        spec.ok, spec.why = False, "imprecise"
        return spec
    if _WD_IN_RANGE_RX.search(t):
        spec.ok, spec.why = False, "weekdays inside a date range"
        return spec
    expanded, ranges_ok = _expand_date_ranges(t)
    spec.ranged = expanded != t
    t = expanded
    if not ranges_ok:
        spec.ok, spec.why = False, "unread date range"
        return spec
    # Day lists spelled out here too, so the dates removed below are every
    # date read: a day number left behind is a day not understood.
    t = _expand_day_lists(t)
    # "Diumenges / Tancat 25 i 26 de desembre, 1 i 6 de gener": closed dates
    # are exceptions; "excepte els dies 1 i 25 de maig" likewise.
    t = re.sub(r"\b(?:tancat|tancada|tancats|excepte\s+(?:els\s+)?dies?)\b", "excepte", t)
    m = _EXCEPT_MONTH_RX.search(t)
    if m:
        spec.except_months.add(MONTHS[m.group(1)])
        t = (t[:m.start()] + " " + t[m.end():]).strip()
    head, sep, exc = t.partition("excepte")
    exc = exc.replace("excepte", " ")
    except_wd: Set[int] = set()
    if sep:
        exc_dates, _outside = _dates_in(exc, lo, hi)
        spec.except_dates |= exc_dates
        rest = _DATE_RX.sub(" ", exc)
        if re.search(r"\bfestius\b", rest):
            spec.festius = True          # skip_dates already keeps a weekly row off them
            rest = re.sub(r"\bfestius\b", " ", rest)
        except_wd = {WEEKDAYS[w] for w in re.findall(_WD, rest)}
        rest = re.sub(_WD, " ", rest)
        if re.search(r"[a-z]{3,}", re.sub(r"\b(?:i|el|la|els|les|dia|dies|dels?)\b", " ", rest)):
            spec.ok, spec.why = False, "unread exception"
            return spec
    t = head
    if re.search(r"\b(?:tots\s+els\s+dies|cada\s+dia|diariament)\b", t):
        spec.weekdays |= set(range(7))
        t = re.sub(r"\b(?:tots\s+els\s+dies|cada\s+dia|diariament)\b", " ", t)
    elif not t.strip() and except_wd:
        spec.weekdays |= set(range(7))   # "excepte dilluns i 12 octubre": every other day
    if _FESTIUS_RX.search(t):
        # Outside an "excepte", "Diumenge i festius" says the holidays are ON.
        spec.festius = spec.on_holidays = True
        t = _FESTIUS_RX.sub(" ", t)
    for m in _ORDINAL_RX.finditer(t):
        wd = WEEKDAYS[m.group(2)]
        for tok in re.split(r"\s*(?:,|\bi\b)\s*", m.group(1)):
            if tok.strip() in ORDINALS:
                spec.ordinals.append((ORDINALS[tok.strip()], wd))
    t = _ORDINAL_RX.sub(" ", t)
    for m in _RANGE_WD_RX.finditer(t):
        a, b = WEEKDAYS[m.group(1)], WEEKDAYS[m.group(2)]
        k = a
        while True:
            spec.weekdays.add(k)
            if k == b:
                break
            k = (k + 1) % 7
    t = _RANGE_WD_RX.sub(" ", t)
    for m in _WD_LABEL_RX.finditer(t):
        d = _place(int(m.group(2)), MONTHS[m.group(3)], lo, hi)
        if d is not None and d.weekday() != WEEKDAYS[m.group(1)]:
            spec.ok, spec.why = False, "a weekday that is not its date's"
            return spec
    t = _WD_LABEL_RX.sub(" ", t)
    dates, out = _dates_in(t, lo, hi)
    spec.dates |= dates
    spec.outside += out
    t = _DATE_RX.sub(" ", t)
    for w in re.findall(_WD, t):
        spec.weekdays.add(WEEKDAYS[w])
    t = re.sub(_WD, " ", t)
    spec.weekdays -= except_wd
    residue = re.sub(r"\b(?:i|de|del|dels|a|al|els|les|el|la|cada|tots|totes|dia|dies|setmana|"
                     r"y|o|també|tambe)\b|[,;:/()\-.']", " ", t)
    if re.search(r"[a-z]{2,}", residue):
        spec.ok, spec.why = False, "unread words"
    elif re.search(r"\d", residue):
        spec.ok, spec.why = False, "unread day number"
    elif spec.empty and spec.outside:
        spec.ok, spec.why = False, "dates outside the row's own range"
    return spec


_MONTH_RANGE_RX = re.compile(rf"\b(?:de\s+|d')({_MON})\s+(?:a|al|fins\s+a)\s+(?:l')?({_MON})\b")


def _month_end(y: int, m: int) -> int:
    return ((date(y + (m == 12), m % 12 + 1, 1)) - timedelta(days=1)).day


def parse_period(cell: str, lo: date, hi: date) -> List[Tuple[date, date]]:
    """The Periode cell as ranges inside lo..hi: 'Del 15 setembre al 20
    novembre', 'De setembre a juny', a bare 'Juliol'. The period is placed in
    every year the row touches and CLIPPED to the row's own range; a period
    wholly outside it gives [] - that line writes nothing. (Falling back to the
    whole row when a date lay outside it wrote a summer line "De l'1 abril al 30
    setembre" on every Saturday of an October-March row.) A cell with no date
    and no month ('Curs escolar', a label) leaves lo..hi as it is."""
    t = re.sub(r"\s+", " ", _fold(cell or "").replace("\n", " ")).strip()
    found = list(_DATE_RX.finditer(t))
    mr = _MONTH_RANGE_RX.search(t)
    if len(found) >= 2:
        (d1, m1), (d2, m2) = [(int(x.group(1)), MONTHS[x.group(2)]) for x in found[:2]]
    elif mr and not found:
        m1, m2 = MONTHS[mr.group(1)], MONTHS[mr.group(2)]
        d1, d2 = 1, 31
    else:
        months = [MONTHS[x] for x in re.findall(_MON, t)]
        rest = re.sub(rf"{_MON}|\b(?:i|de|d'|del|mes|mesos|el|y)\b|[^a-z0-9]", " ", t).strip()
        if months and not found and not rest:
            # "Juliol", "Juliol i agost": those whole months.
            out: List[Tuple[date, date]] = []
            for m in sorted(set(months)):
                for y in range(lo.year, hi.year + 1):
                    a, b = max(date(y, m, 1), lo), min(date(y, m, _month_end(y, m)), hi)
                    if a <= b:
                        out.append((a, b))
            return out
        return [(lo, hi)]
    out = []
    for y in range(lo.year - 1, hi.year + 1):
        y2 = y if (m2, d2) >= (m1, d1) else y + 1
        try:
            a = date(y, m1, min(d1, _month_end(y, m1)))
            b = date(y2, m2, min(d2, _month_end(y2, m2)))
        except ValueError:
            continue
        a, b = max(a, lo), min(b, hi)
        if a <= b:
            out.append((a, b))
    return out


# ---------------------------------------------------------------------------
# hours
# ---------------------------------------------------------------------------
_T = r"(\d{1,2})(?:\s*[.:h]\s*(\d{2}))?\s*h?"
_RANGE_RX = re.compile(rf"\b(?:de|d')\s*(?:les\s+)?{_T}\s*(?:a|fins\s+a)\s+(?:les\s+)?{_T}")
_START_RX = re.compile(rf"\b(?:a|a\s+partir\s+de)\s+les\s+{_T}")
_CLOSED_RX = re.compile(r"^\s*tancat\b")


def _clock(h: str, m: Optional[str]) -> Optional[int]:
    hh, mm = int(h), int(m or 0)
    if hh > 24 or mm > 59:
        return None
    return hh * 60 + mm


class Hours:
    def __init__(self) -> None:
        self.ranges: List[Tuple[int, int]] = []
        self.starts: List[int] = []
        self.closed = False


def parse_hours(cell: str) -> Hours:
    h = Hours()
    t = _fold(cell or "").replace("\n", " ")
    if _CLOSED_RX.search(t):
        h.closed = True
        return h
    for m in _RANGE_RX.finditer(t):
        a, b = _clock(m.group(1), m.group(2)), _clock(m.group(3), m.group(4))
        if a is None or b is None:
            continue
        if b <= a:
            b += 24 * 60          # past midnight: a sarau 18:30 -> 01:00
        h.ranges.append((a, b))
    t = _RANGE_RX.sub(" ", t)
    for m in _START_RX.finditer(t):
        a = _clock(m.group(1), m.group(2))
        if a is not None:
            h.starts.append(a)
    h.ranges = sorted(set(h.ranges))
    h.starts = sorted(set(h.starts))
    return h


def stretches(times: Iterable[Tuple[int, int]]) -> List[Tuple[int, int, List[Tuple[int, int]]]]:
    """OVERLAPPING sessions joined; a gap ends a stretch, and so does a session
    that starts as the last one ends: two guided tours 16:30-17:30 and
    17:30-18:30 are two tours, not one from 16:30 to 18:30."""
    out: List[List[Any]] = []
    for a, b in sorted(set(times)):
        if out and a < out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
            out[-1][2].append((a, b))
        else:
            out.append([a, b, [(a, b)]])
    return [(a, b, parts) for a, b, parts in out]


def day_minutes(h: Hours) -> int:
    return sum(b - a for a, b, _ in stretches(h.ranges))


# ---------------------------------------------------------------------------
# what is kept
# ---------------------------------------------------------------------------
# Each LINE is judged on its own. Registration words, as the City writes them
# (2026-10-05, all 5,957 rows): "Cal inscripcio previa" 2,046, "Cal reserva"
# 338, "Cal fer reserva", "Caldra inscripcio", "Imprescindible inscripcio",
# "Inscripcio obligatoria", "Reserva previa", "Inscripcions a / per / obertes",
# "inscripcio presencial al casal", "15 dies abans s'obrira la reserva
# d'entrada".
_REG_RX = re.compile(
    r"\b(?:cal|caldra|es\s+necessari[ao]?|imprescindible|requereix|cal\s+fer)\s+(?:fer\s+)?(?:la\s+|una\s+|l')?"
    r"(?:inscripcio|inscriure|inscriure's|reserva|reservar|cita\s+previa)"
    r"|\bamb\s+(?:inscripcio|(?:inscripcio|reserva)\s+previa|cita\s+previa)"
    r"|\b(?:inscripcio|reserva)\s+(?:previa|obligatoria|necessaria|imprescindible|presencial)"
    r"|\bprevia\s+(?:inscripcio|reserva)"
    r"|\binscripcions?\s*(?::|a\s|al\s|per\s|en\s|obertes|presencials)"
    r"|\breserva\s+d'entrad|\bobrira\s+(?:la\s+)?(?:reserva|inscripcio)|\bdia\s+de\s+la\s+inscripcio"
    r"|\breserv(?:a|eu)\s+(?:la\s+)?(?:vostra\s+)?(?:entrada|placa|plaça)"
    r"|\bfes\s+(?:la\s+teva\s+)?(?:reserva|inscripcio)\b|\bfeu\s+(?:la\s+vostra\s+)?(?:reserva|inscripcio)\b"
    # An appointment is a registration (2026-10-05: "Cal demanar cita previa",
    # "Cal demana hora a l'EAMP", "Cal concertar hora", "Cal sol·licitar cita
    # previa" - 36 rows at 3 places were written as free drop-ins before), and
    # so is collecting a ticket in advance ("Recull la teva entrada ... 15 dies
    # abans de la sessio").
    r"|\bcal\s+(?:demanar?|concertar|sol·licitar|sollicitar|fer)\s+(?:la\s+|una\s+)?(?:cita|hora|inscripcio|reserva)"
    r"|\b(?:demanar|concertar)\s+hora\b|\bhora\s+concertada\b"
    r"|\brecull\w*\s+(?:la\s+teva\s+|la\s+vostra\s+|les\s+)?entrad\w*")
_NOREG_RX = re.compile(
    r"\b(?:no\s+cal|no\s+es\s+necessari[ao]?|sense|no\s+requereix|no\s+fa\s+falta|no\s+hi\s+ha)\s+"
    r"(?:fer\s+)?(?:la\s+|cap\s+|l')?(?:inscripcio|inscriure|reserva|reservar|cita)"
    r"|\bper\s+ordre\s+d'arribada\b|\bfins\s+a\s+(?:completar|esgotar)\s+(?:l'|el\s+)?aforament\b"
    r"|\bentrada\s+lliure\b|\bacces\s+lliure\b")
_GROUP_RX = re.compile(r"\bgrups?\b")
# A course, whatever its price says.
_COURSE_RX = re.compile(
    r"\bimports?\s+(?:als|dels)\s+(?:cursos|tallers)|\bsubvencio\s+dels\s+imports\b"
    r"|€\s*(?:/\s*)?(?:mensual|trimestral|al\s+mes|al\s+trimestre|per\s+trimestre|/\s*mes|/\s*trimestre)"
    r"|\b(?:quota|preu)\s+(?:mensual|trimestral)\b|\bpreu\s+per\s+torn\b"
    r"|\b\d+\s+sessions\b|\bcurs\s+(?:de|d')|\bcurs\s+\d|\bcursos\b|\bmatricula\b|\btrimestre\b"
    r"|\bcasal\s+d'estiu\b|\bcolonies\b|\binclou\s+dinar\b|\btorns?\s+setmanals?\b")
_COURSE_TITLE_RX = re.compile(r"^\W*(?:curs|cursos|cursets?|taller\s+monografic|taller\s+trimestral)\b"
                              r"|\btaller\s+de\s+\d+\s+sessions\b|\bnivell\s+\d\b")
_ONLINE_RX = re.compile(r"\bonline\b|\ben\s+linia\b|\bvirtual\b|\bper\s+zoom\b|\bvia\s+zoom\b|\bzoom\b"
                        r"|\bteams\b|\bstreaming\b|\btelematic")
_CLOSURE_RX = re.compile(r"^\W*(?:tancament|tancat|centre\s+tancat|casal\s+tancat|biblioteca\s+tancada)\b"
                         r"|\bdies?\s+de\s+tancament\b")
_GOVERNANCE_RX = re.compile(r"\b(?:consell\s+(?:de\s+barri|de\s+districte|plenari|escolar|municipal)|"
                            r"audiencia\s+publica|plenari|ple\s+(?:del|de)\s+districte|"
                            r"comissio\s+de\s+seguiment|assemblea\s+general|junta\s+(?:de|directiva))\b")
# "Convocatoria ...", "27e Concurs ...", "XIII Concurs ...": a call for entries.
_CALL_RX = re.compile(r"^\W*(?:\d+\s*[a-z]{0,2}\.?\s+|[ivxlc]+\s+)?(?:edicio\s+(?:del?\s+)?)?"
                      r"(?:convocatoria|convocatories|beques|premi\s+de|concurs)\b")
_SPACE_HOURS_RX = re.compile(r"\bpatis?\s+escolars?\s+oberts?\b|^\W*sala\s+d'estudi\b|\bsales\s+d'estudi\b"
                             r"|^\W*espai\s+'?sala\s+d'informatica")
_PRIVATE_RX = re.compile(r"\blloguer\s+d'espais\b|\bcessio\s+d'espais\b|\bactivitat\s+privada\b"
                         r"|\bnomes\s+(?:per\s+a\s+)?(?:socis|soci[ae]s|usuaris|inscrits|alumnes)\b"
                         r"|\b(?:families|persones|infants|nens|nenes|joves|usuaris)\s+inscrit(?:e?s|a)?\s+(?:al|a\s+la|a\s+l')"
                         r"|\bexclusiu\s+(?:per\s+a\s+)?(?:socis|usuaris|alumnes)\b")
_SCHOOL_LINE_RX = re.compile(r"\b(?:visites|activitat|sessions?|horari)\s+(?:per\s+a|per\s+als|adrecad[ae]s?\s+a)"
                             r"\s+(?:les\s+|els\s+)?(?:escoles|grups|centres\s+educatius|instituts)\b")
_EXHIBITION_RX = re.compile(r"^\W*(?:inaugur\w*\s+(?:de\s+)?(?:l'|la\s+)?)?(?:exposicio|exposicions|exposicion|instal·lacio|instal·lacions|"
                            r"installacio|installacions|instal\.lacio|mostra\b|exhibicio)")


def sentences(cell: str) -> List[str]:
    """A cell's lines, a wrapped sentence joined back: the City breaks notes
    with <br> mid-sentence ("Cal concertar hora / per a les visites /
    comentades i de / grup (+ de 15 persones)."), and a line that starts in
    lower case continues the one before. Judged line by line, "Cal concertar
    hora" read as a requirement for everyone; the sentence says groups."""
    out: List[str] = []
    for ln in (cell or "").split("\n"):
        ln = _s(ln)
        if not ln:
            continue
        if out and re.match(r"^[\W\d]*[a-zà-ÿ]", ln) and not re.search(r"[.!?:;]$", out[-1]):
            out[-1] = f"{out[-1]} {ln}"
        else:
            out.append(ln)
    return out


def lines_of(rows: List[Dict[str, str]]) -> List[str]:
    out: List[str] = []
    for r in rows:
        for col in ("prices", "description", "period", "weekdays", "hours"):
            out.extend(sentences(r.get(col) or ""))
    return out


def registration(lines: Iterable[str]) -> Tuple[Optional[bool], str]:
    """(True, line) when a line requires registration for the individual,
    (False, line) when one says none is needed, (None, '') when silent. A
    requirement anywhere wins over a "no" elsewhere only on a line of its own."""
    says_no = ""
    for ln in lines:
        f = _fold(ln)
        if _NOREG_RX.search(f):
            says_no = says_no or ln
            rest = _NOREG_RX.sub(" ", f)
            if _REG_RX.search(rest) and not _GROUP_RX.search(f):
                return True, ln
            continue
        if _REG_RX.search(f) and not _GROUP_RX.search(f):
            return True, ln
    if says_no:
        return False, says_no
    return None, ""


def verdict(title: str, rows: List[Dict[str, str]], multi_day: bool) -> Tuple[bool, str]:
    """(kept, reason). The order is the order of the policy: what a row IS
    (online, a closure, a meeting, a call for entries, a room's hours, a
    course) before what it ASKS (registration)."""
    ft = _fold(title)
    lines = lines_of(rows)
    body = "\n".join(_fold(x) for x in lines)
    if _ONLINE_RX.search(ft) or re.search(r"\bper\s+zoom\b|\bvia\s+zoom\b|\bonline\b|\bstreaming\b", body):
        return False, "online"
    if _CLOSURE_RX.search(ft) or (rows and all(parse_hours(r.get("hours", "")).closed for r in rows)):
        return False, "a closure"
    if _GOVERNANCE_RX.search(ft):
        return False, "a governance meeting"
    if (_CALL_RX.search(ft) and multi_day and not _EXHIBITION_RX.search(re.sub(r"^.*?-\s*", "", ft))
            and "exposicio" not in ft and not any(parse_hours(r.get("hours", "")).starts for r in rows)):
        return False, "a call for entries (its window is office hours)"
    if _SPACE_HOURS_RX.search(ft):
        return False, "a space's opening hours (schoolyard, study room)"
    if _PRIVATE_RX.search(ft + "\n" + body):
        return False, "private or members only"
    if _COURSE_TITLE_RX.search(ft) or _COURSE_RX.search(body):
        return False, "a paid centre programme or course (sold by registration)"
    reg, _line = registration(lines)
    if reg:
        return False, "registration required (cal inscripcio / reserva previa)"
    if reg is False:
        return True, "says no registration is needed"
    return True, "no registration asked"


# ---------------------------------------------------------------------------
# price
# ---------------------------------------------------------------------------
_MONEY_RX = re.compile(r"\d+(?:[.,]\d+)?\s*(?:€|eur\b|euros?\b)|€\s*\d")
_FREE_CELL_RX = re.compile(r"^\W*(?:entrada\s+)?(?:gratuita|gratuit|lliure|gratis|gratuito)\b"
                           r"|^\W*activitat\s+gratuita\b|^\W*acces\s+(?:gratuit|lliure)\b")
# Free for SOME people: the session's own price is then unknown.
_FREE_SOME_RX = re.compile(r"(?:gratuit[ae]?s?|gratis|lliure)\s+(?:per\s+a|per\s+als|pels|amb|fins\s+als?|"
                           r"als?\s+(?:menors|socis|majors))\b|\b(?:menors|socis|majors|infants|jubilats|"
                           r"residents|titulars)\b[^\n]{0,40}\bgratu")
# "Donatiu voluntari en benefici de la companyia" is a donation: pay what
# you can, not "Price not stated" (Teatre Musical "De 9 a 5", 2026-10-05).
_PWYW_RX = re.compile(r"\btaquilla\s+inversa\b|\bla\s+voluntat\b|\bpreu\s+lliure\b|\baportacio\s+voluntaria\b"
                      r"|\bdonatius?\b")
# What ../mapsee 0227 would read as free in a source note on a row that is NOT
# free: a subset of measure_deals.FREE that this source's notes can hit.
_TAGGED_FREE = re.compile(
    r"\bgratuit(?:e|s|es)?\b|\bgratuit[ao]s?\b|\bgratis\b|\b(?:entrada|acceso|ingreso)\s+(?:libre|gratuit[ao]|gratis)\b|"
    r"\bentree\s+(?:libre|gratuite)\b|(?<![-\w/])free\s+(?:admission|entry|entrance|event|of\s+charge|to\s+"
    r"(?:attend|join|enter|participate|the\s+public|all|everyone)|for\s+(?:all|everyone|kids|children|"
    r"the\s+public|families))\b|\bis\s+free\b|\bfree\s*!", re.I)


# Never "Price: free ...": 0227 reads "price: free" as free. "not free" is its
# FREE_NEG, a veto on the whole row - the session is not free for everyone.
CONDITIONAL = "Price: not free for everyone; the City's listing names who goes free."


def price_line(rows: List[Dict[str, str]]) -> Tuple[str, str]:
    """(tier, sentence) from the Preus cells: free | fee | pwyw | conditional | unknown."""
    cells = [r.get("prices") or "" for r in rows if (r.get("prices") or "").strip()]
    if not cells:
        return "unknown", "Price not stated."
    joined = " ".join(_s(c) for c in cells)
    folded = [_fold(c) for c in cells]
    if any(_MONEY_RX.search(f) and not re.search(r"^\W*(?:entrada\s+general\s*:?\s*)?0\s*€\W*$", f)
           for f in folded):
        first = _s(cells[0])
        if len(first) > 150:
            first = first[:147].rsplit(" ", 1)[0] + "..."
        return "fee", f"Price: {first.rstrip('.')} (not free)."
    for f in folded:
        m = _PWYW_RX.search(f)
        if m:
            return "pwyw", f"Price: {m.group(0)} - pay what you can."
    if all(_FREE_CELL_RX.search(f) for f in folded):
        if any(_FREE_SOME_RX.search(f) for f in folded):
            return "conditional", CONDITIONAL
        return "free", "Admission: free."
    if any(_FREE_SOME_RX.search(f) for f in folded) or any(re.search(r"gratu|gratis|lliure", f) for f in folded):
        return "conditional", CONDITIONAL
    return "unknown", "Price not stated."


# ---------------------------------------------------------------------------
# facilities
# ---------------------------------------------------------------------------
GENERIC_NAMES = {"biblioteca", "casal", "centrecivic", "auditori", "sala"}
# Facilities this close to each other are one building, and the kinds' rank
# names it. Measured 2026-10-05 on the 39 addresses with two or more community
# facilities: 0-4.8 m apart, then Pati Llimona's centre civic and casal
# infantil at 18.7 m (one building: the centre civic's exhibitions sit on the
# casal's point), then the Besòs library 30.6 m from its centre civic (its own
# door; its storytimes sit on it), 45.9, 60.3, 75.3, 86.0 and 482.7 m.
CO_LOCATED_M = 20.0
# A row this close to a facility's point is AT that facility; past it the
# point says nothing and the kinds' rank names the building.
AT_FACILITY_M = 25.0


def _clean_name(v: Any) -> str:
    # "Casal de Gent Gran d'Horta *Feliu i Codina": the register's asterisk.
    return re.sub(r"\s+", " ", _s(v).replace("*", " ")).strip()


def facility_index(rows: List[Dict[str, Any]], kinds: List[str], museum_kinds: List[str]
                   ) -> Tuple[Dict[Tuple[str, str], List[Dict[str, Any]]],
                              Dict[Tuple[str, str], List[Tuple[str, Tuple[float, float]]]]]:
    """{(road_id, number): [facility, best kind first]} for community kinds,
    and {(road_id, number): [(museum name, point)]} for addresses that also
    hold a museum."""
    rank = {k: i for i, k in enumerate(kinds)}
    fac: Dict[str, Dict[str, Any]] = {}
    museums: Dict[Tuple[str, str], List[Tuple[str, Tuple[float, float]]]] = {}
    for r in rows:
        road, num = _s(r.get("addresses_road_id")), _s(r.get("addresses_start_street_number"))
        if not road or not num:
            continue
        kind = _s(r.get("secondary_filters_name"))
        pt = _point(r)
        if kind in museum_kinds and pt is not None:
            got = museums.setdefault((road, num), [])
            if _clean_name(r.get("name")) not in [n for n, _p in got]:
                got.append((_clean_name(r.get("name")), pt))
        if kind not in rank:
            continue
        if pt is None:
            continue
        if _norm(r.get("name")) in GENERIC_NAMES:
            # "Biblioteca" and nothing else: the library of a museum or a school
            # (Museu Picasso's, the Conservatori's, Escola Massana's), filed as
            # municipal. Its building's programme is the institution's.
            continue
        rid = _rid(r.get("register_id"))
        f = fac.setdefault(rid, {"id": rid, "name": _clean_name(r.get("name")), "kinds": set(), "point": pt,
                                 "road": road, "num": num, "rank": len(kinds)})
        f["kinds"].add(kind)
        f["rank"] = min(f["rank"], rank[kind])
    idx: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for f in fac.values():
        idx.setdefault((f["road"], f["num"]), []).append(f)
    for v in idx.values():
        v.sort(key=lambda f: (f["rank"], f["name"]))
    return idx, museums


def choose_venue(here: List[Dict[str, Any]], pt: Tuple[float, float]
                 ) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """(the facility the row is at, the others in its building). One address
    can hold facilities on different points: Carrer de Vallcivera 3 is a
    library and, 86 m away, a children's casal. The facility nearest the row's
    own point names it; the kinds' rank only breaks a tie between co-located
    ones (a centre civic over the library on its first floor), and decides
    alone only when no facility is within AT_FACILITY_M of the row (a building
    geocoded twice). Ranked first regardless, 15 rows in the window on
    2026-10-05 were named for a building other than the one at their point - a
    library's seniors' talk on the kids door as 'Casal Infantil Ciutat
    Meridiana' (14), a library storytime as the Besòs centre civic (1)."""
    dist = [(metres(pt, f["point"]), f) for f in here]
    near = min(d for d, _f in dist)
    if near <= AT_FACILITY_M:
        pool = [f for d, f in dist if d <= near + CO_LOCATED_M]
    else:
        pool = list(here)
    venue = min(pool, key=lambda f: (f["rank"], f["name"]))
    same = [f for f in here if f is not venue and metres(f["point"], venue["point"]) <= AT_FACILITY_M]
    return venue, same


# A place named in a line's notes ("CC La Sedeta", "Casal de Joves del Coll"):
# the kind words the City writes before a facility's own name.
_KIND_ALIASES = ("centre civic", "centre cultural", "casal de barri", "casal de joves", "casal jove",
                 "casal infantil", "casal de gent gran", "espai de gent gran", "espai jove", "espai d'adolescents",
                 "biblioteca", "ateneu", "ludoteca", "sala jove", "casal", "cc")
_ARTICLES = r"(?:(?:la|el|les|els|l|del|de|d)\s+)?"


def _words(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _fold(s)).strip()


def place_matcher(idx: Dict[Tuple[str, str], List[Dict[str, Any]]]) -> List[Tuple[str, "re.Pattern[str]"]]:
    """[(facility id, pattern)]: a kind word, an article, then the facility's
    own name ("centre civic la sedeta" -> 'sedeta' after any kind word), read
    on _words() text. Without a kind word in front a name is not a place: "Sant
    Antoni" is a festival before it is a library."""
    aliases = "|".join(re.escape(_words(a)) for a in sorted(_KIND_ALIASES, key=len, reverse=True))
    out: List[Tuple[str, "re.Pattern[str]"]] = []
    for v in idx.values():
        for f in v:
            name = _words(f["name"].split(" - ")[0].split(" – ")[0])
            core = re.sub(rf"^(?:{aliases})\s+", "", name)
            core = re.sub(r"^(?:(?:la|el|les|els|l|del|de|d)\s+)+", "", core).strip()
            if core == name or len(core) < 4:
                # No kind word to strip, or nothing distinctive left ("Casal
                # de Barri", "Sala Jove"): the whole name, as written.
                if len(name.split()) < 2:
                    continue
                out.append((f["id"], re.compile(rf"\b{re.escape(name)}\b")))
                continue
            out.append((f["id"], re.compile(rf"\b(?:{aliases})\s+{_ARTICLES}{re.escape(core)}\b")))
    return out


def places_named(text: str, matcher: List[Tuple[str, "re.Pattern[str]"]]) -> Set[str]:
    w = _words(text)
    return {fid for fid, rx in matcher if rx.search(w)} if w else set()


def slug(s: str) -> str:
    return re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9]+", "-", _fold(s))).strip("-")[:80] or "activitat"


# ---------------------------------------------------------------------------
# category
# ---------------------------------------------------------------------------
# "Petits" alone is a NAME as often as an audience: the perinatal-grief
# association Petits amb Llum put a pregnancy-loss commemoration and a bereaved
# parents' mutual-aid group on the kids door (integration review, 2026-10-05).
# So the little ones count only as an audience: "per als petits", "els més
# petits", "petita infància".
_KIDS_WORDS_RX = re.compile(r"\b(?:infantil|infantils|familiar|families|en\s+familia|nens|nenes|nadons|"
                            r"(?:per\s+als?|pels|als|els\s+mes)\s+(?:mes\s+)?(?:petits|petites)|"
                            r"petita\s+infancia|canalla|contes?|contacontes|titelles|ludoteca|"
                            r"de\s+\d\s+a\s+\d{1,2}\s+anys)\b")


def category(title: str, venue: Dict[str, Any], cfg: Dict[str, Any]) -> Tuple[str, List[str]]:
    ft = _fold(title)
    primary = None
    for word, key in (cfg.get("category_by_title_word") or {}).items():
        if re.search(rf"^\W*{re.escape(_fold(word))}", ft):
            primary = key
            break
    primary = primary or cfg.get("category_default", "community")
    extras: List[str] = []
    # A children's casal's programme is for children - when the row is AT the
    # casal (choose_venue): a library's seniors' talk at the same address
    # carried the kids tag while the casal named it.
    if _KIDS_WORDS_RX.search(ft) or venue.get("kinds") == {"Casals infantils"}:
        extras.append("kids")
    return primary, norm_categories(primary, extras)


# ---------------------------------------------------------------------------
# rows -> events
# ---------------------------------------------------------------------------
def schedule(rows: List[Dict[str, str]], lo: date, hi: date, today: date, horizon: date,
             skip: Set[date], single: bool, stats: Dict[str, int],
             elsewhere: Optional[Any] = None) -> Tuple[Optional[str], Dict[date, Hours]]:
    """(why refused or None, {date: Hours}) for one register. For a one-day row
    the date is start_date and the Dies cell must agree with it. `elsewhere`
    says whether a line's notes name ANOTHER community facility; such a line is
    held there, not at the row's address, and is skipped. A skipped line is
    marked `_skipped`, so its notes are not printed either."""
    per_day: Dict[date, Hours] = {}
    closed: Set[date] = set()
    a, b = max(lo, today), min(hi, horizon)
    covered = contradicted = 0
    for r in rows:
        if _SCHOOL_LINE_RX.search(_fold(f"{r.get('description', '')} {r.get('prices', '')}")):
            # "Visites per a escoles ... 2 hores per a grups i escoles": that
            # line's 09:30-11:30 is a school's booking, not the public's tour.
            _bump(stats, "timetable lines skipped: for schools or groups only")
            r["_skipped"] = "schools"
            continue
        if elsewhere is not None and elsewhere(r.get("description", "")):
            # "Concert Districte Musical Jove DMJ Gràcia": one register, a line
            # per date, each naming its own hall ("CC La Sedeta", "Casal de
            # Joves del Coll"). Pinned at the row's address, 2 of its 3
            # concerts were on the wrong building.
            _bump(stats, "timetable lines skipped: their notes name another community facility")
            r["_skipped"] = "elsewhere"
            continue
        spec = parse_days(r.get("weekdays", ""), lo, hi)
        h = parse_hours(r.get("hours", ""))
        if single:
            # A one-day row's DATE is the claim; its Dies cell must not deny it.
            # A line naming another weekday is another line's, and is skipped;
            # when no line names this date's weekday, the row is refused.
            if spec.ok and not spec.empty and not spec.covers(lo):
                contradicted += 1
                continue
            covered += 1
            if h.closed:
                closed.add(lo)
                continue
            got = per_day.setdefault(lo, Hours())
            got.ranges += h.ranges
            got.starts += h.starts
            continue
        if not spec.ok and spec.why == "dates outside the row's own range":
            _bump(stats, "timetable lines skipped: their dates fall outside the row's own range")
            continue
        if not spec.ok:
            return f"days not stated precisely ({spec.why})", {}
        if spec.empty:
            continue
        periods = parse_period(r.get("period", ""), lo, hi)
        if not periods:
            _bump(stats, "timetable lines skipped: their period falls outside the row's own range")
            continue
        for p_lo, p_hi in periods:
            d = max(a, p_lo)
            end = min(b, p_hi)
            while d <= end:
                holiday_on = (spec.on_holidays and d in skip and d not in spec.except_dates
                              and d.month not in spec.except_months)
                if spec.covers(d) or holiday_on:
                    if spec.pattern and d not in spec.dates and d in skip and not holiday_on:
                        _bump(stats, "weekly dates not written: a public holiday")
                    elif h.closed:
                        closed.add(d)
                    else:
                        got = per_day.setdefault(d, Hours())
                        got.ranges += h.ranges
                        got.starts += h.starts
                d += timedelta(days=1)
    if single and contradicted and not covered:
        return "its weekday contradicts its own date", {}
    if single and not rows:
        per_day[lo] = Hours()
    for d in closed:
        per_day.pop(d, None)
    return None, per_day


def is_exhibition(title: str) -> bool:
    """An exhibition or installation by its title: "Exposició ...", but also
    "IV Mostra de vídeo-performances" and "19è Concurs d'Art Jove - Exposició
    d'obres seleccionades"."""
    ft = _fold(title)
    return any(_EXHIBITION_RX.search(x) for x in (
        ft, re.sub(r"^\W*(?:\d+\s*[a-z]{0,2}\.?|[ivxlc]+)\s+", "", ft), ft.split(" - ", 1)[-1]))


def is_run(title: str, rows: List[Dict[str, str]], lo: date, hi: date) -> bool:
    """One date-only row from start to end: an exhibition or installation whose
    hours are opening hours. Nothing else is a run (a building's hours are not a
    session - see opening_hours_only). Not an exhibition either when every line
    lists its own dates: "Mostra de cinema emergent", Dies "28 octubre, 25
    novembre i 16 desembre", 19:00-20:00, is three screenings, and as a run it
    was 'happening now' for the seven weeks between them."""
    if not is_exhibition(title):
        return False
    if any(parse_hours(r.get("hours", "")).starts for r in rows):
        return False
    specs = [parse_days(r.get("weekdays", ""), lo, hi) for r in rows if (r.get("weekdays") or "").strip()]
    if specs and all(sp.ok and sp.dates and not sp.pattern and not sp.ranged for sp in specs):
        return False
    return True


# A title naming a SPACE: "Espai 12/16", "Sala de joc", "Trobada 'Punt TIC'",
# "Trobada 'Casal Persones Grans'", "Espai 'Sala d'Informàtica'".
_SPACE_TITLE_RX = re.compile(r"^\W*(?:trobada\s+\W?\s*)?(?:espai|espais|sala|punt|casal)\b")


def opening_hours_only(title: str, rows: List[Dict[str, str]], lo: date, hi: date) -> Optional[str]:
    """Why a multi-day row is a place's opening hours and not a session, or
    None. Two shapes, both refused (opening hours are not an event; the
    exhibition is the one exception, because its opening hours ARE it):
      - a title naming a space, open on RUN_WEEKDAYS or more weekdays: a
        computer room, a youth space, a seniors' room. Decided by the title and
        the days alone: on 2026-10-05 "Espai 'Sala d'Informàtica'" was refused
        while "Trobada 'Punt TIC'" (the same kind of room, Mon-Fri 10-13 and
        16-18) was 106 rows, and "Trobada 'Espai Familiar'" (7 h) a three-month
        run 'happening' every night. 7 registers, 368 rows.
      - anything else open RUN_DAY_MINUTES or more on RUN_WEEKDAYS or more
        weekdays: a collection point or a swap open the building's hours for
        weeks ("Campanya solidària 'Cap infant sense joguina'", Mon-Sat
        09:30-21:30 for a month)."""
    if is_exhibition(title):
        return None
    weekly: Set[int] = set()
    long_days: Set[int] = set()
    for r in rows:
        sp = parse_days(r.get("weekdays", ""), lo, hi)
        weekly |= sp.weekdays
        if day_minutes(parse_hours(r.get("hours", ""))) >= RUN_DAY_MINUTES:
            long_days |= sp.weekdays
    if _SPACE_TITLE_RX.search(_fold(title)) and len(weekly - {5, 6}) >= RUN_WEEKDAYS:
        return "a space's opening hours (a room or space open on weekdays)"
    if len(long_days) >= RUN_WEEKDAYS:
        return f"opening hours, not a session ({RUN_DAY_MINUTES // 60} h+ a day on {RUN_WEEKDAYS}+ weekdays)"
    return None


def opening_text(rows: List[Dict[str, str]]) -> str:
    parts = []
    for r in rows:
        bits = [x for x in (_s(r.get("period")), _s(r.get("weekdays")), _s(r.get("hours"))) if x]
        if bits:
            parts.append(" ".join(bits))
    return "; ".join(dict.fromkeys(parts))


def notes_text(rows: List[Dict[str, str]]) -> str:
    """The Observacions of the lines written, as sentences: a <br> mid-sentence
    is a space, not a full stop ("durada d'1 hora i. un aforament" before), and
    a line skipped (for schools, held elsewhere) says nothing."""
    seen: List[str] = []
    for r in rows:
        if r.get("_skipped"):
            continue
        for ln in sentences(r.get("description") or ""):
            ln = ln if re.search(r"[.!?…]$", ln) else ln.rstrip(",;: ") + "."
            if ln not in seen:
                seen.append(ln)
    return " ".join(seen)


def _fmt_day(d: date) -> str:
    return f"{d.day} {d.strftime('%b')} {d.year}"


def events(agenda: List[Dict[str, Any]], facilities: List[Dict[str, Any]], src: Dict[str, Any],
           cfg: Dict[str, Any], tz, today: date, stats: Dict[str, int]) -> List[NormalizedEvent]:
    horizon = today + timedelta(days=int(src.get("horizon_days", 90)))
    skip = {date.fromisoformat(x) for x in cfg.get("skip_dates") or []}
    if skip and max(skip) < horizon:
        print(f"[barcelona] WARNING: skip_dates end {max(skip)}, before the horizon {horizon}; "
              "add the next public holidays to the config")
    kinds = list(cfg.get("venue_kinds") or [])
    idx, museums = facility_index(facilities, kinds, list(cfg.get("museum_kinds") or []))
    stats["community facilities in the register"] = len({f["id"] for v in idx.values() for f in v})
    box = cfg.get("bbox")
    join_max = float(cfg.get("join_max_m") or JOIN_MAX_M)
    shared_ok = {(_s(x.get("road_id")), _s(x.get("number"))) for x in cfg.get("museum_shared_kept") or []}
    matcher = place_matcher(idx)

    groups: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = {}
    runs: List[Dict[str, Any]] = []
    for row in agenda:
        lo = _day(row.get("start_date"))
        hi = _day(row.get("end_date")) or lo
        if lo is None:
            _bump(stats, "refused: no start date")
            continue
        if hi < lo:
            hi = lo
        if hi < today or lo > horizon:
            _bump(stats, "outside the window (ended, or starts after the horizon)")
            continue
        _bump(stats, "agenda rows in the window")
        pt = _point(row)
        if pt is None:
            _bump(stats, "unplaceable (no point)")
            continue
        if box and not (box[0] <= pt[0] <= box[2] and box[1] <= pt[1] <= box[3]):
            _bump(stats, "outside Barcelona's box")
            continue
        addr = (_s(row.get("addresses_road_id")), _s(row.get("addresses_start_street_number")))
        here = idx.get(addr) if all(addr) else None
        if not here:
            _bump(stats, "not at a community place (no facility at its address)")
            continue
        venue, same = choose_venue(here, pt)
        gap = metres(pt, venue["point"])
        shown_museums: List[str] = []
        if museums.get(addr):
            # A museum at the address. The row's point says whose programme it
            # is: kept only AT the community facility (within AT_FACILITY_M)
            # and nearer it than the museum. Casal de l'Avi Barça shares a
            # number with the Barça museum, and the Palau Blaugrana's
            # basketball sits 102 m from the casal: 13 matches, refused. On
            # ONE point (L'Auditori, Disseny Hub, the Museu de la Xocolata, La
            # Fabra) the point cannot tell, and the row is refused unless a
            # reviewed `museum_shared_kept` entry says the address is the
            # centre's own.
            to_museum = min(metres(pt, mp) for _n, mp in museums[addr])
            one_point = any(metres(venue["point"], mp) <= CO_LOCATED_M for _n, mp in museums[addr])
            if gap > AT_FACILITY_M or to_museum < gap or (one_point and addr not in shared_ok):
                _bump(stats, "refused: the building is also a museum (its programme, not the centre's)")
                continue
            shown_museums = [n for n, mp in museums[addr] if metres(venue["point"], mp) <= AT_FACILITY_M]
            _bump(stats, "kept at a museum's address: a reviewed centre's own (museum_shared_kept)")
        if gap > join_max:
            _bump(stats, f"refused: address matches a facility but the point is over {join_max:.0f} m away")
            continue
        if gap > 25:
            _bump(stats, "address matched; the row's own point was 25 m+ off, the facility's is used")
        _bump(stats, "at a community place")
        title = _s(row.get("name"))
        rows = timetable(row.get("timetable"))
        single = lo == hi
        kept, why = verdict(title, rows, not single)
        if not kept:
            _bump(stats, f"refused: {why}")
            continue
        _bump(stats, f"kept: {why}")
        rec = dict(row, _rows=rows, _venue=venue, _also=same, _museums=shown_museums, _lo=lo, _hi=hi,
                   _title=title, _noreg=(why == "says no registration is needed"))
        if not single:
            hours_only = opening_hours_only(title, rows, lo, hi)
            if hours_only:
                _bump(stats, f"refused: {hours_only}")
                continue
        run = not single and is_run(title, rows, lo, hi)
        if not single and (run or not rows or all(not r.get("weekdays") for r in rows)):
            if not run and (hi - lo).days > RUN_NO_DAYS_MAX:
                _bump(stats, "refused: a long window with no days or hours")
                continue
            _bump(stats, "runs (an exhibition, or a short window with no timetable): one date-only row")
            runs.append(rec)
            continue
        here_ids = {venue["id"]} | {f["id"] for f in same}

        def elsewhere(note: str, _ids: Set[str] = here_ids) -> bool:
            named = places_named(note, matcher)
            return bool(named) and not (named & _ids)
        why_not, per_day = schedule(rows, lo, hi, today, horizon, skip, single, stats, elsewhere)
        if why_not:
            _bump(stats, f"refused: {why_not}")
            continue
        if not per_day:
            _bump(stats, "no date left in the window (exceptions, holidays, closed days)")
            continue
        for d, h in per_day.items():
            groups.setdefault((venue["id"], _norm(title), d.isoformat()), []).append(dict(rec, _hours=h))

    attribution = cfg["attribution"]
    out: List[NormalizedEvent] = []

    def build(rec: Dict[str, Any], title: str, sl: str, su: Optional[str], el: Optional[str],
              eu: Optional[str], source_id: str, fp_day: str, fp_key: str, schedule_line: Optional[str],
              stats_key: str) -> NormalizedEvent:
        venue = rec["_venue"]
        # The price of the lines written: a line held at another facility or
        # for schools only does not set this row's price.
        tier, price = price_line([r for r in rec["_rows"] if not r.get("_skipped")] or rec["_rows"])
        _bump(stats, f"price: {tier}")
        access = " No registration needed, says the City's listing." if rec["_noreg"] else ""
        district = _s(rec.get("addresses_district_name"))
        head = f"{price}{access} At {venue['name']}" + (f" ({district})." if district else ".")
        also = [f["name"] for f in rec["_also"] if _norm(f["name"]) != _norm(venue["name"])]
        also += rec.get("_museums") or []
        tail = [
            schedule_line,
            (f"Same building: {', '.join(also[:3])}." if also else None),
            # The phone's label is the centre's ("Inscripcions" sits on free
            # exhibitions too), so it is kept in brackets, not as a claim.
            (f"Tel. {_s(rec.get('values_value'))}"
             + (f" ({_s(rec.get('values_attribute_name'))})." if _s(rec.get('values_attribute_name')) else ".")
             if _s(rec.get("values_category")) == "Telèfons" and _s(rec.get("values_value")) else None),
            attribution,
        ]
        notes = notes_text(rec["_rows"])
        room = DESCRIPTION_MAX - len("\n\n".join([head] + [p for p in tail if p])) - 2
        if len(notes) > room:
            notes = notes[:max(room - 2, 0)].rsplit(" ", 1)[0] + " …" if room >= NOTES_MIN else ""
            _bump(stats, "source notes shortened to fit the sync's cap" if notes
                  else "source notes dropped: no room under the sync's cap")
        if notes and tier != "free" and _TAGGED_FREE.search(notes):
            _bump(stats, "source notes withheld: they read as free on a row that is not")
            notes = ""
        paras = [head, notes or None] + tail
        primary, extras = category(title, venue, src)
        road = _s(rec.get("addresses_road_name"))
        num = _s(rec.get("addresses_start_street_number"))
        zipc = _s(rec.get("addresses_zip_code"))
        rid = _rid(rec.get("register_id"))
        ev = NormalizedEvent(
            source=src.get("source", "barcelona-agenda"),
            source_id=source_id,
            name=title,
            description="\n\n".join(p for p in paras if p),
            start_local=sl, start_utc=su, end_local=el, end_utc=eu,
            timezone=cfg.get("timezone"),
            venue_name=venue["name"],
            latitude=venue["point"][0], longitude=venue["point"][1],
            coords_exact=True,
            address=f"{road} {num}".strip() or None,
            city=cfg.get("city"), region=cfg.get("region"), country=cfg.get("country"),
            postal_code=(zipc.zfill(5) if zipc.isdigit() else zipc) or None,
            category=primary, categories=extras,
            ticket_url=(cfg["link_template"].format(slug=slug(title), id=rid)
                        if cfg.get("link_template") and rid else src.get("dataset")),
        )
        ev.fingerprint = make_fingerprint(fp_key, fp_day, f"{venue['name']} {road} {num}".strip())
        _bump(stats, stats_key)
        return ev

    for rec in runs:
        lo, hi = rec["_lo"], rec["_hi"]
        opening = opening_text(rec["_rows"])
        line = (f"Open {_fmt_day(lo)} to {_fmt_day(hi)}" + (f": {opening}." if opening else "."))
        first = lo
        if (hi - lo).days > MAX_RUN_SPAN_DAYS:
            # Past mapsee_spam.MAX_SPAN_DAYS the store drops the END, and a run
            # that opened last year would then be one day, in the past. From
            # today it is what is still open; the text keeps the real opening.
            first = max(lo, today)
            _bump(stats, "runs longer than 400 days: written from today")
        # Identity is the register and its OWN opening day, never the clamped
        # start: fingerprinted on `first`, a run longer than 400 days took a new
        # fingerprint (the database's external_id) every day, and the store
        # rekeyed it - one more pin a day until the exhibition closed.
        out.append(build(rec, rec["_title"], first.isoformat(), None, hi.isoformat(), None,
                         f"{rec['_venue']['id']}|{_rid(rec.get('register_id'))}|run",
                         lo.isoformat(), rec["_title"], line, "rows written: runs (date-only, start to end)"))

    for (vid, _key, day_s), items in sorted(groups.items()):
        items.sort(key=lambda r: _rid(r.get("register_id")))
        lead = items[0]
        title = lead["_title"]
        day = date.fromisoformat(day_s)
        ranges = sorted({x for r in items for x in r["_hours"].ranges})
        starts = sorted({x for r in items for x in r["_hours"].starts})
        _bump(stats, "repeat registers folded", len(items) - 1)
        spans = stretches(ranges)
        if len(spans) > 1:
            _bump(stats, "days split at a gap")
        # A start inside a stretch is the same session, not another.
        starts = [s for s in starts if not any(a <= s < b for a, b, _ in spans)]
        if not spans and not starts:
            out.append(build(lead, title, day_s, None, None, None,
                             f"{vid}|{_norm(title)}|{day_s}", day_s, title,
                             None, "rows written: date-only (hours not readable)"))
            continue
        others_all = [f"{hm(a)}-{hm(b)}" for a, b, _ in spans] + [hm(s) for s in starts]
        for a, b, parts in spans:
            sl, su = _stamp(day, a, tz)
            el, eu = _stamp(day, b, tz)
            others = [x for x in others_all if x != f"{hm(a)}-{hm(b)}"]
            line = " ".join(x for x in (
                (f"Sessions: {', '.join(f'{hm(x)}-{hm(y)}' for x, y in parts)}." if len(parts) > 1 else ""),
                (f"Also this day: {', '.join(others)}." if others else "")) if x) or None
            out.append(build(lead, title, sl, su, el, eu, f"{vid}|{_norm(title)}|{day_s}|{hm(a)}",
                             day_s, f"{title} {hm(a)}", line, "rows written: timed sessions"))
        for s in starts:
            sl, su = _stamp(day, s, tz)
            others = [x for x in others_all if x != hm(s)]
            line = f"Also this day: {', '.join(others)}." if others else None
            out.append(build(lead, title, sl, su, None, None, f"{vid}|{_norm(title)}|{day_s}|{hm(s)}",
                             day_s, f"{title} {hm(s)}", line, "rows written: timed sessions (start only)"))
    return out


def ingest(store: EventStore, reader: Reader, src: Dict[str, Any], cfg: Dict[str, Any], tz) -> Dict[str, int]:
    stats: Dict[str, int] = {}
    facilities = reader.resource(src["facilities_resource_id"], fields=src.get("facility_fields"))
    if not facilities:
        raise RuntimeError("the facility register returned no rows")
    agenda = reader.resource(src["resource_id"], fields=src.get("fields"))
    stats["agenda rows read"] = len(agenda)
    stats["facility rows read"] = len(facilities)
    if not agenda:
        raise RuntimeError("the datastore returned no agenda rows")
    evs = events(agenda, facilities, src, cfg, tz, _today(tz), stats)
    for ev in evs:
        store.upsert(ev)
    stats["rows written"] = len(evs)
    return stats


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def load_config(path: str) -> Dict[str, Any]:
    cfg = json.loads(open(path, encoding="utf-8").read())
    attribution = (cfg.get("attribution") or "").strip()
    if not attribution or len(attribution) > ATTRIBUTION_MAX:
        raise ValueError(f"attribution must be 1-{ATTRIBUTION_MAX} characters "
                         "(the sync keeps a final paragraph only that short)")
    if not cfg.get("timezone"):
        raise ValueError("config needs a timezone")
    if not cfg.get("venue_kinds"):
        raise ValueError("config needs venue_kinds: without them every row is 'not a community place'")
    return cfg


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import what is on at Barcelona's community places into the Mapsee store.")
    ap.add_argument("--config", required=True)
    ap.add_argument("--store", default="mapsee_events.json")
    ap.add_argument("--max-minutes", type=float, default=DEFAULT_MAX_MINUTES,
                    help="whole-run deadline; no request starts after it (0 = none)")
    a = ap.parse_args(argv)

    started = time.monotonic()
    cfg = load_config(a.config)
    tz = ZoneInfo(cfg["timezone"])
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept": "application/json"})
    reader = Reader(session, cfg.get("api") or DEFAULT_API,
                    min_interval=float(cfg.get("crawl_delay") or MIN_INTERVAL_S),
                    deadline=started + a.max_minutes * 60 if a.max_minutes else None)
    store = EventStore(a.store)
    total = 0
    failed = 0
    unsaved, saved = False, False
    for src in cfg.get("sources", []):
        label = src.get("name", "barcelona-agenda")
        if reader.refused:
            print(f"[barcelona] {label}: NOT READ - {reader.refused} earlier this run (one host)")
            continue
        before = reader.requests
        unsaved = True
        try:
            stats = ingest(store, reader, src, cfg, tz)
        except Refused as exc:
            print(f"[barcelona] {label} REFUSED: {exc} - not retried, and the host is asked nothing more")
            failed += 1
            continue
        except OutOfTime as exc:
            print(f"[barcelona] {label} STOPPED: {exc} (--max-minutes {a.max_minutes:g})")
            break
        except Exception as exc:  # noqa: BLE001 - one source never stops another
            print(f"[barcelona] {label} FAILED: {exc}")
            failed += 1
            continue
        total += stats.get("rows written", 0)
        print(f"[barcelona] {label}: {stats.get('rows written', 0)} rows "
              f"in {reader.requests - before} requests")
        for k, v in sorted(stats.items()):
            if k != "rows written":
                print(f"[barcelona]     {k}: {v}")
        store.save()
        unsaved, saved = False, True
    if unsaved or not saved:
        store.save()
    st = store.stats
    print(f"[barcelona] done in {(time.monotonic() - started):.0f} s: {total} rows written "
          f"(added {st.get('added', 0)}, updated {st.get('updated', 0)}, merged {st.get('merged', 0)}, "
          f"rekeyed {st.get('rekeyed', 0)}, rejected {st.get('rejected', 0)}) in {reader.requests} "
          f"requests; store now holds {len(store.records)} unique events.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
