#!/usr/bin/env python3
"""
mapsee_ingest_luminate.py - charity walks on Blackbaud Luminate Online
TeamRaiser: the American Cancer Society's Making Strides Against Breast Cancer
and Relay For Life, the Alzheimer's Association's Walk to End Alzheimer's, and
Susan G. Komen's MORE THAN PINK Walk / Race for the Cure.

    python mapsee_ingest_luminate.py --config luminate_sources.json --store feeds_events.json

PARKED. The config ships as luminate_sources.json.pending-permission and the
workflow step is guarded `[ -f luminate_sources.json ]`, so nothing runs until
the owner renames it. The api_key each request carries is the charity's own,
published in its own event finder (ACS 'ACSAPI', Alzheimer's 'ALZADARDA' in an
<input type=hidden name=api_key>, Komen 'pink' in a plain-text API URL on its
page). Blackbaud's help says of that key: "This is not a secure password. You
can include this key in a hidden field of a web form." robots.txt on all three
hosts has no rule touching /site/CRTeamraiserAPI (2026-10-11). Using somebody
else's key is still the owner's decision, not this file's: see the config.

WHAT IT READS. CRTeamraiserAPI method=getTeamraisersByInfo with name=%%% (the
wildcard), the programme's event_type and list_page_size=500 - the client-side
listing the charities' own finders call. Seven requests read every programme:
on 2026-10-11, 175 Making Strides, 1,055 Relay, 624 Walk 2026 and 59 Komen
records. Each record has a date, a venue, a street, city, state and ZIP, and the
entry page (greeting_url).

WHY THESE RULES, each one a record from the 2026-10-11 pull:

1. THE DATE IS A DAY, THE CLOCK IS NOISE. San Diego's Making Strides reads
   2026-10-18T00:00:00.568-05:00 and its page says 8:00 AM; Providence's walk
   reads 12:59:08.959 and its page says "Registration 8:30 AM ... Walk 10:15 AM".
   The time is when somebody saved the record. So a row is all-day and says
   the start time is on the event page.

2. DECEMBER 31 IS A FISCAL YEAR-END, NOT A WALK. 189 of the 1,055 Relay records
   and 36 of the 175 Making Strides ones sit on 12-31 - test events, "Venue
   Name", "NO EVENT THIS YEAR." and real chapters whose date is not set yet
   (Making Strides of Atlanta, Relay at Canyon Ridge High School). A day listed
   in `placeholder_days` is never written.

3. ONLY AN ACTIVE PUBLIC EVENT. status 2 is a live event; 1 is unpublished
   ("BB TEST - RFTC 2027 Blueprint") and 3 is closed (Making Strides of Lansing,
   2026-10-24, registrations off). Names with Test, Blueprint, Template,
   Virtual, "Registration Only", "Donations Only" or Second Life are not walks
   anybody can go to, and nor are "Students Only" school relays or the one held
   inside Limon Correctional Facility. A name that is not the programme's own
   (name_must_match) is another programme on the same type and is skipped.

4. A PLACE IS A STREET. The sync geocodes street + city + state through Census
   and drops what it cannot place, so a record whose location is "Venue Name",
   "location name", "street address", "Cityname(3.a.3)", "Location TBD", "Where
   you Are" or blank is not written, and a street carrying its own town ("601
   Clark Ave, St. Louis, MO 63102") or a lot name ("Pinnacle Lot - 241 20th
   Street NW") is cut to the numbered street.

5. THE ALZHEIMER'S ASSOCIATION FILLS `area` WITH ITS PIN. 621 of its 624
   records carry "35.2847...,-86.3705..." there (Tullahoma-Lynchburg's sits on
   the Jack Daniel's visitor centre it names). With `coords_from_area` the row
   carries that point, and the sync's Census pass still replaces it when the
   street matches: it is what places a walk whose state is mistyped ("Paducah
   AK 42001") or whose street is a park road. A typed state whose box the pin
   is outside is dropped rather than published (Paducah, 1 of 238 pins).

6. FREE ONLY WHERE THE CHARITY SAYS SO. "There is no registration fee for Walk"
   (act.alz.org's event FAQ) and "There is no fee to register for the MORE THAN
   PINK Walk" (secure.info-komen.org mtpw_faq.html): those rows say "Free to
   join: there is no registration fee." - worded so that ../mapsee migration
   0227's free predicate reads it ("free to join"); the first draft's "Free to
   register." matched none of its patterns, and 0 of the 253 rows were tagged
   free. ACS loads its fees by script and one test event is named "$25 and $0
   P-type", so Making Strides and Relay say nothing about price, nor does a
   Komen Race for the Cure.

7. THE RUN ENDS ITSELF. No request starts after --max-minutes (7 in the
   workflow, under the step's 10): one run is 8 API requests at up to 90 s each
   plus 3 robots.txt reads, ~13 minutes at worst. Each page's rows are written
   before the next request and the store is saved after each programme and on
   the way out, so a deadline or a cancelled step keeps what was read. A
   401/403/429 or a challenge ends that host's reads for the run, ACS's second
   programme included.

Category: community, with fitness and outdoors for the walks (Relay is often
an indoor track or gym: fitness only).
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
from urllib.parse import quote, urlencode, urlsplit

from mapsee_ingest import (EventStore, NormalizedEvent, USER_AGENT, looks_online_only,
                           make_fingerprint, norm_categories)

SOURCE = "luminate"
DEFAULT_WITHIN_DAYS = 180
DEFAULT_MAX_MINUTES = 7.0
PAGE_SIZE = 500
MAX_PAGES = 6
FREE_LINE = "Free to join: there is no registration fee."

# Rule 3: not an event anybody can turn up to.
_NOT_PUBLIC_RX = re.compile(
    r"\b(test|tests|testing|blueprint|template|virtual|registration\s+only|donations?\s+only|"
    r"do\s+not\s+use|sustaining\s+gifts?|national\s+team|second\s+life|"
    r"students?\s+only|employees?\s+only|staff\s+only|members?\s+only|"
    r"correctional|prison|penitentiary|detention)\b", re.I)
# Rule 4: a location field that names no place.
_PLACEHOLDER_RX = re.compile(
    r"^\s*(?:venue\s+name|location\s+name|street\s+address|cityname\b.*|location\s+tbd|tbd|tba|"
    r"to\s+be\s+(?:determined|announced)|none|n/?a|virtual(?:\s+event)?|online|where\s+you\s+are|"
    r"digital\s+experience|no\s+physical\s+event.*|no\s+event\s+this\s+year.*|"
    r"see\s+event\s+announcements.*|\d{1,6})\s*\.?\s*$", re.I)
# A location that says there is no single place to go: a campaign, not an event
# ("Community Fundraising Only", "Various Locations In Lansing", "Virtual
# Campaign CCF" at a PO Box, "Digital Experience", Second Life's track).
_NO_PLACE_RX = re.compile(
    r"no\s+physical\s+event|no\s+event\s+this\s+year|virtual|second\s+life|digital\s+experience|"
    r"fundraising\s+(?:event\s+)?only|various\s+locations|\bp\.?\s?o\.?\s+box\b|general\s+delivery", re.I)
_STREET_RX = re.compile(r"\b\d{1,6}[A-Za-z]?(?:-\d{1,6})?\s+[A-Za-z0-9]")
_AREA_RX = re.compile(r"^\s*(-?\d{1,3}\.\d+)\s*°?\s*([NS])?\s*,\s*(-?\d{1,3}\.\d+)\s*°?\s*([EW])?\s*$", re.I)
US_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "district of columbia": "DC",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID", "illinois": "IL",
    "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA",
    "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC", "south dakota": "SD",
    "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA",
    "washington": "WA", "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "puerto rico": "PR",
}


# Each state's bounding box (lat_min, lat_max, lon_min, lon_max), to catch a
# mistyped state beside the charity's own pin: "Paducah AK 42001" carries a pin
# at 37.085,-88.618, in Kentucky, and was published as Alaska. The only pin of
# the 238 placed Alzheimer's rows outside its state's box (2026-10-11). A box
# is wider than its state, so a pin outside it is certainly elsewhere; inside
# proves nothing and changes nothing.
_STATE_BOX = {
    "AL": (30.14, 35.01, -88.47, -84.89), "AK": (51.2, 71.4, -180.0, -129.9), "AZ": (31.33, 37.0, -114.82, -109.04),
    "AR": (33.0, 36.5, -94.62, -89.64), "CA": (32.53, 42.01, -124.41, -114.13), "CO": (36.99, 41.0, -109.06, -102.04),
    "CT": (40.98, 42.05, -73.73, -71.79), "DE": (38.45, 39.84, -75.79, -75.05), "DC": (38.79, 39.0, -77.12, -76.91),
    "FL": (24.4, 31.0, -87.63, -80.03), "GA": (30.36, 35.0, -85.61, -80.84), "HI": (18.91, 22.24, -160.25, -154.81),
    "ID": (41.99, 49.0, -117.24, -111.04), "IL": (36.97, 42.51, -91.51, -87.02), "IN": (37.77, 41.76, -88.1, -84.78),
    "IA": (40.37, 43.5, -96.64, -90.14), "KS": (36.99, 40.0, -102.05, -94.59), "KY": (36.5, 39.15, -89.57, -81.96),
    "LA": (28.93, 33.02, -94.04, -88.82), "ME": (43.06, 47.46, -71.08, -66.95), "MD": (37.91, 39.72, -79.49, -75.05),
    "MA": (41.24, 42.89, -73.51, -69.93), "MI": (41.7, 48.31, -90.42, -82.12), "MN": (43.5, 49.38, -97.24, -89.49),
    "MS": (30.17, 35.01, -91.66, -88.1), "MO": (35.99, 40.61, -95.77, -89.1), "MT": (44.36, 49.0, -116.05, -104.04),
    "NE": (40.0, 43.0, -104.05, -95.31), "NV": (35.0, 42.0, -120.01, -114.04), "NH": (42.7, 45.31, -72.56, -70.61),
    "NJ": (38.93, 41.36, -75.56, -73.89), "NM": (31.33, 37.0, -109.05, -103.0), "NY": (40.5, 45.02, -79.76, -71.86),
    "NC": (33.84, 36.59, -84.32, -75.46), "ND": (45.94, 49.0, -104.05, -96.55), "OH": (38.4, 41.98, -84.82, -80.52),
    "OK": (33.62, 37.0, -103.0, -94.43), "OR": (41.99, 46.29, -124.57, -116.46), "PA": (39.72, 42.27, -80.52, -74.69),
    "RI": (41.15, 42.02, -71.86, -71.12), "SC": (32.03, 35.22, -83.35, -78.54), "SD": (42.48, 45.95, -104.06, -96.44),
    "TN": (34.98, 36.68, -90.31, -81.65), "TX": (25.84, 36.5, -106.65, -93.51), "UT": (37.0, 42.0, -114.05, -109.04),
    "VT": (42.73, 45.02, -73.44, -71.46), "VA": (36.54, 39.47, -83.68, -75.24), "WA": (45.54, 49.0, -124.85, -116.92),
    "WV": (37.2, 40.64, -82.64, -77.72), "WI": (42.49, 47.31, -92.89, -86.25), "WY": (40.99, 45.01, -111.06, -104.05),
    "PR": (17.88, 18.52, -67.95, -65.22),
}


def outside_state(point: Tuple[float, float], region: Optional[str], margin: float = 0.3) -> bool:
    """True when the pin is certainly not in that state (outside its box)."""
    box = _STATE_BOX.get(region or "")
    if not box:
        return False
    lat, lon = point
    return not (box[0] - margin <= lat <= box[1] + margin and box[2] - margin <= lon <= box[3] + margin)


def _clean(v: Any) -> Optional[str]:
    if v is None or isinstance(v, (dict, list)):
        return None
    s = re.sub(r"\s+", " ", htmllib.unescape(str(v))).strip()
    return None if not s or _PLACEHOLDER_RX.match(s) else s


def _region(v: Any) -> Optional[str]:
    s = _clean(v)
    if not s:
        return None
    if re.fullmatch(r"[A-Za-z]{2}", s):
        return s.upper()
    return US_STATES.get(s.lower())


def clean_street(street: Optional[str], city: Optional[str]) -> Optional[str]:
    """Rule 4: the numbered street, without a lot name before it or the town,
    state and ZIP the record repeats after it."""
    s = _clean(street)
    if not s:
        return None
    s = re.sub(r"\s*\([^)]*\)", "", s).strip(" ,.")
    if city:
        i = s.lower().find(", " + city.lower())
        if i > 0:
            s = s[:i]
    s = re.sub(r",?\s+[A-Z]{2}\s+\d{5}(?:-\d{4})?\.?$", "", s).strip(" ,")
    m = _STREET_RX.search(s)
    if not m:
        return None          # "Maple Street", "Baxter Blvd": a road, not an address
    return s[m.start():].strip(" ,") or None


def area_point(area: Any) -> Optional[Tuple[float, float]]:
    """Rule 5: a "lat,lon" pair in `area`, inside the US and its territories."""
    if not isinstance(area, str):
        return None
    m = _AREA_RX.match(area)
    if not m:
        return None
    lat, lon = float(m.group(1)), float(m.group(3))
    if (m.group(2) or "").upper() == "S":
        lat = -abs(lat)
    if (m.group(4) or "").upper() == "W":
        lon = -abs(lon)
    if not (13.0 <= lat <= 72.0 and -180.0 <= lon <= -64.0):
        return None
    return lat, lon


def _types(src: Dict[str, Any], today: date) -> List[str]:
    out = []
    for t in src.get("event_types") or []:
        t = t.replace("{year}", str(today.year)).replace("{next_year}", str(today.year + 1))
        if t not in out:
            out.append(t)
    return out


def request_url(src: Dict[str, Any], event_type: str, offset: int) -> str:
    params = [("method", "getTeamraisersByInfo"), ("api_key", src["api_key"]), ("v", "1.0"),
              ("response_format", "json"), ("list_page_size", str(PAGE_SIZE)),
              ("list_page_offset", str(offset)), ("name", "%%%"), ("event_type", event_type)]
    return src["api"] + "?" + urlencode(params, quote_via=quote)


def to_event(rec: Dict[str, Any], src: Dict[str, Any], today: date, within_days: int,
             placeholder_days: List[str]) -> Tuple[Optional[NormalizedEvent], str]:
    """(event, "") or (None, why-not)."""
    if str(rec.get("status")) != "2":
        return None, "not active"
    name = _clean(rec.get("name")) or ""
    if src.get("strip_name"):
        name = re.sub(src["strip_name"], "", name, flags=re.I).strip()
    if not name:
        return None, "no name"
    if src.get("name_must_match") and not re.search(src["name_must_match"], name, re.I):
        return None, "another programme"
    if _NOT_PUBLIC_RX.search(name):
        return None, "test, virtual or not public"
    day = str(rec.get("event_date") or "")[:10]
    try:
        d = date.fromisoformat(day)
    except ValueError:
        return None, "no date"
    if day[5:] in placeholder_days:
        return None, "placeholder date"
    if d < today:
        return None, "past"
    if d > today + timedelta(days=within_days):
        return None, "beyond the window"
    raw_loc = " ".join(str(rec.get(k) or "") for k in ("location_name", "street_address", "city"))
    if _NO_PLACE_RX.search(raw_loc) or looks_online_only(name, raw_loc):
        return None, "test, virtual or not public"
    # "Location TBD" with a street under it (St Louis's Making Strides at "601
    # Clark Ave", Toledo's relay at "2800 E Rocket Dr") is a venue nobody has
    # chosen yet: the street may be an office's. Not written until it is set.
    if re.search(r"\b(?:tbd|tba|to\s+be\s+(?:determined|announced))\b", str(rec.get("location_name") or ""), re.I):
        return None, "location TBD"
    city = _clean(rec.get("city"))
    region = _region(rec.get("state"))
    street = clean_street(rec.get("street_address"), city)
    point = area_point(rec.get("area")) if src.get("coords_from_area") else None
    if point and outside_state(point, region):
        region = None                # the pin is the charity's own; the typed state is not
    if not (city and region) and not point:
        return None, "no town"
    if not street and not point:
        return None, "no street address"
    venue = _clean(rec.get("location_name"))
    if venue and street and venue.lower().startswith(street.lower()):
        venue = None
    zip5 = (re.match(r"\d{5}", str(rec.get("zip") or "")) or [None])[0]
    url = str(rec.get("greeting_url") or rec.get("customized_url") or "").strip()
    url = re.sub(r"^http://", "https://", url) if url.startswith("http") else None

    lines = []
    free_rx = src.get("free_to_register")
    if free_rx and re.search(free_rx, name, re.I):
        # Worded for ../mapsee migration 0227, whose free predicate reads "free
        # to join" and did not read the earlier "Free to register.".
        lines.append(FREE_LINE)
    if src.get("about"):
        lines.append(src["about"])
    lines.append("Start time, route and registration on the event page.")
    nev = NormalizedEvent(
        source=SOURCE,
        source_id=f"{urlsplit(src['api']).netloc}:{rec.get('id')}",
        name=name,
        description="\n\n".join(lines),
        start_local=d.isoformat(),
        venue_name=venue,
        address=street,
        latitude=point[0] if point else None,
        longitude=point[1] if point else None,
        city=city, region=region, postal_code=zip5,
        country="US",
        category="community",
        categories=norm_categories("community", src.get("categories") or ["fitness"]),
        promoter=src.get("org"),
        ticket_url=url,
    )
    nev.fingerprint = make_fingerprint(name, d.isoformat(), venue or street, city)
    return nev, ""


class Refused(Exception):
    """The host said no: 401/403/429, a bot challenge or robots.txt."""


class Stop(Exception):
    """The run's deadline passed; no request starts after it."""


def fetch(session, url: str, robots=None, deadline: Optional[float] = None,
          clock: Optional[Callable[[], float]] = None) -> Any:
    from robots_txt import CHALLENGE_RX
    if deadline is not None and (clock or time.monotonic)() >= deadline:
        raise Stop("run deadline reached")
    if robots is not None:
        v = robots.check(url)
        if v.get("allowed") is not True:
            raise Refused(f"robots.txt ({v.get('status')}): {v.get('rule')} for {url.split('?')[0]}")
    r = session.get(url, timeout=90, allow_redirects=True, headers={"Accept": "application/json"})
    if r.status_code in (401, 403, 429):
        raise Refused(f"HTTP {r.status_code} for {url.split('?')[0]}")
    text = r.text or ""
    if CHALLENGE_RX.search(text[:6000]):
        raise Refused(f"bot challenge for {url.split('?')[0]}")
    if r.status_code != 200:
        raise ValueError(f"HTTP {r.status_code}")
    return json.loads(text)


class ApiError(Exception):
    """TeamRaiser answered with an errorResponse."""


def iter_pages(src: Dict[str, Any], event_type: str, get_json: Callable[[str], Any],
               pause: Callable[[], None]):
    """Yield (records, totalNumberResults) for one event type, page by page, so
    a caller writes each page before the next request (a deadline or a refusal
    on page 2 keeps page 1). Raises ApiError on an errorResponse."""
    n = 0
    for page in range(MAX_PAGES):
        if page:
            pause()
        data = get_json(request_url(src, event_type, page))
        if isinstance(data, dict) and "errorResponse" in data:
            err = data["errorResponse"] or {}
            raise ApiError(f"errorResponse {err.get('code')}: {err.get('message')}")
        resp = (data or {}).get("getTeamraisersResponse") or {}
        try:
            total = int(resp.get("totalNumberResults") or 0)
        except (TypeError, ValueError):
            total = 0
        got = resp.get("teamraiser") or []
        if isinstance(got, dict):
            got = [got]
        got = [r for r in got if isinstance(r, dict)]
        n += len(got)
        yield got, total
        if not got or n >= total:
            break


def read_type(src: Dict[str, Any], event_type: str, get_json: Callable[[str], Any],
              pause: Callable[[], None]) -> Tuple[List[Dict[str, Any]], Optional[int], Optional[str]]:
    """(records, totalNumberResults, error) for one event type, every page."""
    rows: List[Dict[str, Any]] = []
    total = None
    try:
        for got, total in iter_pages(src, event_type, get_json, pause):
            rows.extend(got)
    except ApiError as exc:
        return rows, total, str(exc)
    return rows, total, None


def ingest_source(src: Dict[str, Any], get_json: Callable[[str], Any], pause: Callable[[], None],
                  today: date, within_days: int, placeholder_days: List[str],
                  emit: Callable[[NormalizedEvent], None]) -> Dict[str, Any]:
    """Every type of one programme. A refusal or the deadline (Stop) ends the
    whole run and is raised after what was read has been emitted; any other
    error costs that type only."""
    rep: Dict[str, Any] = {"read": 0, "total": 0, "kept": 0, "placed_by_area": 0, "skipped": {}, "errors": []}
    seen = set()
    for i, t in enumerate(_types(src, today)):
        if i:
            pause()
        type_total = 0
        try:
            for rows, type_total in iter_pages(src, t, get_json, pause):
                rep["read"] += len(rows)
                for rec in rows:
                    if rec.get("id") in seen:
                        continue
                    seen.add(rec.get("id"))
                    ev, why = to_event(rec, src, today, within_days, placeholder_days)
                    if ev is None:
                        rep["skipped"][why] = rep["skipped"].get(why, 0) + 1
                        continue
                    emit(ev)
                    rep["kept"] += 1
                    rep["placed_by_area"] += ev.latitude is not None
        except (Refused, Stop):
            raise
        except ApiError as exc:
            rep["errors"].append(f"{t}: {exc}")
        except Exception as exc:                          # noqa: BLE001
            rep["errors"].append(f"{t}: {type(exc).__name__}: {exc}")
        finally:
            rep["total"] += type_total or 0
    return rep


def _today() -> date:
    fixed = os.environ.get("MAPSEE_TODAY")
    if fixed:
        return datetime.strptime(fixed, "%Y%m%d").date()
    return date.today()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import charity walks from Blackbaud Luminate TeamRaiser.")
    ap.add_argument("--config", default="luminate_sources.json")
    ap.add_argument("--store", default="feeds_events.json")
    ap.add_argument("--max-minutes", type=float, default=DEFAULT_MAX_MINUTES,
                    help="whole-run deadline; no request starts after it (0 = none)")
    ap.add_argument("--dry-run", action="store_true", help="read and report, write nothing")
    a = ap.parse_args(argv)

    cfg = json.loads(open(a.config, encoding="utf-8").read())
    try:
        import requests
    except ImportError:  # pragma: no cover
        sys.exit("This script needs 'requests'.  Install it with:  pip install requests")
    from robots_txt import Robots

    # Only the User-Agent on the session: robots.txt is read with it, and a
    # session-wide Accept header can change that answer (mapsee_ingest_rallybound).
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    robots = Robots(session)
    store = None if a.dry_run else EventStore(a.store)
    today = _today()
    within = int(cfg.get("within_days", DEFAULT_WITHIN_DAYS))
    placeholder_days = list(cfg.get("placeholder_days") or [])
    gap = float(cfg.get("pause", 2.0))
    deadline = time.monotonic() + a.max_minutes * 60 if a.max_minutes else None
    written = [0]

    def emit(ev: NormalizedEvent) -> None:
        written[0] += 1
        if store is not None:
            store.upsert(ev)

    stopped = None
    refused_hosts = set()
    for i, src in enumerate(s for s in cfg.get("sources", []) if not s.get("skip")):
        name = src.get("name") or src.get("api")
        host = urlsplit(src["api"]).netloc
        if host in refused_hosts:
            # ACS's two programmes share one host: its refusal of the first is
            # its answer to the second too.
            print(f"[{SOURCE}] {name}: skipped - {host} refused this run", flush=True)
            continue
        if i:
            time.sleep(gap)
        rep = None
        try:
            rep = ingest_source(src, lambda u: fetch(session, u, robots, deadline), lambda: time.sleep(gap), today,
                                within, placeholder_days, emit)
        except Refused as exc:
            refused_hosts.add(host)
            print(f"[{SOURCE}] {name}: REFUSED - {exc}; not retried", flush=True)
            continue
        except Stop as exc:
            stopped = f"{exc} during {name}"
        except Exception as exc:                          # noqa: BLE001
            print(f"[{SOURCE}] {name} FAILED: {type(exc).__name__}: {exc}", flush=True)
            continue
        finally:
            # After each programme, and on the way out of the deadline or a
            # refusal: a step cancelled by its timeout-minutes keeps the rows it
            # read (docs/agents/ci-and-jobs.md).
            if store is not None:
                store.save()
        if stopped:
            break
        skipped = ", ".join(f"{k} {v}" for k, v in sorted(rep["skipped"].items())) or "none"
        print(f"[{SOURCE}] {name}: read {rep['read']} of {rep['total']}, kept {rep['kept']} within "
              f"{within} days ({rep['placed_by_area']} placed by the charity's own pin); skipped: {skipped}",
              flush=True)
        for e in rep["errors"]:
            print(f"[{SOURCE}] {name}: {e}", flush=True)
    if stopped:
        print(f"[{SOURCE}] STOPPED - {stopped}; what was read is saved", flush=True)
    if store is not None:
        print(f"[{SOURCE}] done: +{written[0]} events; store now holds {len(store.records)} unique events.",
              flush=True)
    else:
        print(f"[{SOURCE}] dry run - {written[0]} event(s) would be written", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
