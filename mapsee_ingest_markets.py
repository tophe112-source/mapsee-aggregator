#!/usr/bin/env python3
"""
mapsee_ingest_markets.py — turn RECURRING community markets (farmers markets, night
markets, flea markets) into dated map events for the Mapsee 'market' layer.

Market data is published as a weekly schedule ("Saturdays, 9 a.m. - 2 p.m."), not
as individual dated events, so this adapter EXPANDS each market into one occurrence
per matching weekday within a rolling horizon (default 6 weeks). The weekly cleanup
prunes past ones, and re-runs regenerate the window idempotently (fingerprint =
name | date | address).

Config (market_sources.json): a list of sources, each either
  • a Socrata dataset (no key), e.g. NYC's DOHMH Farmers Markets:
      { "name": "NYC Farmers Markets", "type": "socrata",
        "url": "https://data.cityofnewyork.us/resource/8vwk-6iz2.json",
        "map": { "name":"marketname","lat":"latitude","lon":"longitude",
                 "days":"daysoperation","hours":"hoursoperations","address":"streetaddress" } }
  • an inline curated list (geocoded via Photon from the address):
      { "name": "Seattle Farmers Markets", "type": "inline", "city": "Seattle, WA",
        "markets": [ {"name":"Ballard Farmers Market","address":"Ballard Ave NW ...",
                      "days":"Sunday","hours":"9 a.m. - 2 p.m."}, ... ] }
  • OpenStreetMap, via Overpass — amenity=marketplace inside each bbox:
      { "name": "OpenStreetMap Marketplaces", "type": "overpass",
        "run_weekdays": [0], "pause_s": 4,
        "bboxes": [ {"name":"Seattle","city":"Seattle","s":..,"n":..,"w":..,"e":..} ] }
    OSM carries BOTH halves of the problem — coordinates and an opening_hours
    schedule — so these cost nothing from the geocode budget, and one config
    covers every metro at once instead of a curated list per city. Marketplaces
    open more than _OSM_MAX_DAYS days a week are treated as shops, not market
    days, and skipped.
  • USDA Local Food Directories — the national registry, one query per area:
      { "name": "USDA Local Food Directories", "type": "usda",
        "run_weekdays": [2], "radius": 30, "directories": ["farmersmarket"],
        "areas": [ {"name":"Seattle","lat":47.61,"lon":-122.33}, ... ] }
    Needs USDA_LOCALFOOD_API_KEY (free, usdalocalfoodportal.com/fe/datasharing);
    silently skipped when unset, like every other keyed adapter here. Rows carry
    coordinates AND a "Sat: 9:00 AM-2:00 PM;" schedule, so they cost nothing
    from the geocode budget. Areas may use "zip" instead of lat/lon.

"run_weekdays" (any source): only run on these weekdays (0 = Monday). Markets
are weekly schedules expanded over a rolling horizon, so a daily re-run emits
identical rows; --force ignores it.

    python mapsee_ingest_markets.py --config market_sources.json --store mapsee_events.json
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
from mapsee_geo_budget import geocode_allowed
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

from mapsee_ingest import (NormalizedEvent, EventStore, make_fingerprint,
                           geohash_encode, _to_float)

# Geohash precision for a market's cross-source identity: 5 is a ~4.9km cell.
# See market_events for why identity is a cell and not the address.
_IDENT_PRECISION = 5

# ---- persistent geocode cache (shared with the other adapters) --------------
# COMMITTED TO GIT, unlike the other caches in this repo, and this file is the
# reason why. Identity here is a geohash cell (see market_events), so the
# coordinate Photon returns is not merely where the pin lands — it decides
# whether a row is the SAME market as last run or a brand-new one. That makes a
# disposable cache load-bearing: `geocode_cache.json` used to live only in
# actions/cache, which GitHub evicts after 7 days idle and which a timed-out job
# never saves. The 2026-08-04 run came up cold, re-geocoded all 48 inline
# markets, got different answers, and filed every one of them a second time —
# Columbia City twice in Seattle search, 4km apart, on the day of the store
# screenshots. Photon is not stable across index builds and never promised to be.
#
# So: the committed entries are the SOURCE OF TRUTH for `|market` keys. CI still
# restores a warm cache for the other adapters (whose fingerprints key on venue
# TEXT, so a drifting coordinate only nudges a pin) but merges it UNDERNEATH
# this file — see the "Merge the warm geocode cache" step in aggregate-events.yml.
GEO_CACHE_PATH = os.environ.get("GEOCODE_CACHE", "geocode_cache.json")


def _load_cache() -> Dict[str, Any]:
    try:
        return json.loads(open(GEO_CACHE_PATH, encoding="utf-8").read())
    except Exception:
        return {}


def _save_cache(c: Dict[str, Any]) -> None:
    # sort_keys + indent because this file is committed: json.dumps' default
    # single-line, insertion-ordered blob rewrites the whole file on every run
    # and makes the diff unreviewable, which defeats the point of tracking it.
    try:
        open(GEO_CACHE_PATH, "w", encoding="utf-8").write(
            json.dumps(c, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
    except Exception:
        pass


_CACHE = _load_cache()


def _geocode(session, query: str) -> Tuple[Optional[float], Optional[float]]:
    key = f"{query}|market".strip().lower()
    if key in _CACHE:
        return tuple(_CACHE[key])
    if not geocode_allowed():                        # over this run's budget → retry next run
        return (None, None)
    out: Tuple[Optional[float], Optional[float]] = (None, None)
    try:
        time.sleep(1.1)
        r = session.get("https://photon.komoot.io/api/", params={"q": query, "limit": 1}, timeout=20)
        f = (r.json().get("features") or [None])[0]
        if f:
            c = f["geometry"]["coordinates"]
            out = (c[1], c[0])
    except Exception:
        pass
    # Only a HIT is cached. A miss used to be stored as [null, null], which was
    # survivable while the cache was disposable — the next eviction retried it.
    # Now that the file is committed, one Photon 500 would be baked into git
    # permanently, and market_events() drops any row whose coordinates are None:
    # the market would silently disappear from the map and no later run would
    # ever look it up again. An uncached miss just retries next run.
    if out[0] is not None:
        _CACHE[key] = list(out)
    return out


# ---- schedule parsing -------------------------------------------------------
_DAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
         "friday": 4, "saturday": 5, "sunday": 6}
_DAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _weekdays(s: Optional[str]) -> List[int]:
    s = (s or "").lower()
    return sorted({v for k, v in _DAYS.items() if k in s})


def _parse_time(s: Optional[str]) -> Optional[str]:
    """'8 a.m.' / '4p.m.' / '3:00 p.m.' / '10 AM' / 'Noon' -> 'HH:MM:00'."""
    low = (s or "").lower()
    # civic listings very often write "8 a.m. - Noon"; without this the end time
    # is dropped and the market renders as an open-ended event
    if "noon" in low and not re.search(r"\d", low):
        return "12:00:00"
    if "midnight" in low and not re.search(r"\d", low):
        return "00:00:00"
    m = re.search(r"(\d{1,2})(?::(\d{2}))?\s*([ap])\s*\.?\s*m", low)
    if not m:
        # 24-hour clock ("15:00", "09:30") — how OpenStreetMap writes
        # opening_hours. Without this every OSM time parsed as None and the
        # market rendered open-ended. fullmatch so it cannot swallow half of a
        # date or a range that only carried am/pm on its first half.
        m24 = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", low)
        if m24 and int(m24.group(1)) <= 23:
            return f"{int(m24.group(1)):02d}:{m24.group(2)}:00"
        return None
    h, mi, ap = int(m.group(1)), m.group(2) or "00", m.group(3)
    if ap == "p" and h != 12:
        h += 12
    if ap == "a" and h == 12:
        h = 0
    return f"{h:02d}:{mi}:00" if h <= 23 else None


def _parse_hours(s: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """'9 a.m. - 2 p.m.' / '10 AM–2 PM' / '3 p.m. to 7:30 p.m.' -> (start, end)."""
    parts = re.split(r"\s*(?:-|–|—|to)\s*", (s or "").strip(), maxsplit=1)
    if len(parts) == 2:
        return _parse_time(parts[0]), _parse_time(parts[1])
    return _parse_time(s), None


def _as_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _tz(name: Optional[str]):
    """IANA timezone (handles DST) for a source, or None (times stay naive)."""
    if not name:
        return None
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        return None


def _localize(ds: str, t: Optional[str], tz) -> Tuple[str, Optional[str]]:
    """(start_local, start_utc) for a date + 'HH:MM:SS' in tz. Without tz -> naive
    local (start_utc None). WITH tz we emit a real UTC instant so the app shows the
    market at its true local time instead of shifting by the UTC offset."""
    if not t:
        return ds, None
    if tz is not None:
        dt = datetime.fromisoformat(f"{ds}T{t}").replace(tzinfo=tz)
        return dt.isoformat(), dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"{ds}T{t}", None


def _occurrences(weekdays: List[int], horizon_days: int,
                 s_start: Optional[date], s_end: Optional[date]) -> List[date]:
    today = datetime.now().date()
    out: List[date] = []
    for i in range(horizon_days):
        d = today + timedelta(days=i)
        if d.weekday() not in weekdays:
            continue
        if s_start and d < s_start:
            continue
        if s_end and d > s_end:
            continue
        out.append(d)
    return out


def market_events(mk: Dict[str, Any], src: Dict[str, Any], session) -> List[NormalizedEvent]:
    name = (mk.get("name") or "").strip()
    weekdays = _weekdays(mk.get("days"))
    if not name or not weekdays:
        return []
    start_t, end_t = _parse_hours(mk.get("hours") or "")
    lat, lon = _to_float(mk.get("lat")), _to_float(mk.get("lon"))
    if lat is None or lon is None:
        query = mk.get("address") or f"{name}, {src.get('city', '')}"
        lat, lon = _geocode(session, query)
    if lat is None or lon is None:
        return []
    label = "market:" + src["name"].lower().replace(" ", "-")
    # The pin's LABEL. Same rule as mapsee_ingest_runsignup: a street beginning
    # with a house number makes a poor one — "1 Schöneicher Straße" tells a
    # reader nothing, where "Flohmarkt Friedrichshagen" names the thing they are
    # going to. A street that does NOT start with a number is usually a real
    # place ("Ballard Ave NW & Vernon Pl NW") and is kept. The exact address
    # survives either way in `address`, which the sync appends as "📍 …".
    addr = (mk.get("address") or "").strip()
    place = addr if (addr and not addr[0].isdigit()) else name
    # Cross-source identity is the NAME plus a ~5km geohash cell, NOT the address.
    # One market reaches this function as "Ballard Ave NW & Vernon Pl NW" from the
    # curated list, "5345 Ballard Ave NW" from USDA and "5301 Ballard Avenue
    # Northwest" from OpenStreetMap, so an address-keyed fingerprint filed it three
    # times; the coordinates agree to a few hundred metres. The cell has to be a
    # pure function of THIS row — sources run on different weekdays and the store
    # is rebuilt every run, so the duplicates are never in memory together to be
    # compared. A market sitting on a cell boundary still slips through, which is
    # what mapsee_dedupe_events.py sweeps up in the database, the one place where
    # every source's rows do meet. `place` stays the address, for display.
    #
    # "Pure function of THIS row" is the load-bearing part, and for the 48 inline
    # markets that carry an address but no coordinates it is only true because
    # geocode_cache.json is committed — lat/lon above came from a network call,
    # so without a stable cache the cell is a function of whatever Photon
    # happened to answer today. See the cache block at the top of this file.
    ident = "market " + geohash_encode(lat, lon, _IDENT_PRECISION)
    # A nationwide source spans four zones, so the row may carry its own; a
    # city-scoped source declares one for the whole config.
    tz = _tz(mk.get("timezone") or src.get("timezone"))
    out: List[NormalizedEvent] = []
    for d in _occurrences(weekdays, src.get("horizon_days", 42),
                          _as_date(mk.get("season_start")), _as_date(mk.get("season_end"))):
        ds = d.isoformat()
        fp = make_fingerprint(name, ds, ident)
        sl, su = _localize(ds, start_t, tz)
        el, eu = _localize(ds, end_t, tz)
        ev = NormalizedEvent(
            source=label,
            source_id=fp,
            name=name,
            description=(f"Weekly market · {mk.get('hours')}" if mk.get("hours") else "Weekly community market"),
            start_local=sl, start_utc=su,
            end_local=el, end_utc=eu,
            venue_name=place,
            latitude=lat, longitude=lon,
            address=mk.get("address"),
            # Absent on every loader but Overpass, so this is None and nothing
            # changes for the inline, Socrata and USDA sources.
            city=mk.get("city"),
            coords_exact=bool(mk.get("coords_exact")),
            category="market",
            # A farmers/night market IS a food destination — this is the single
            # richest supply oneday.cafe has, and it was reaching only fleabop.
            categories=["food"],
            ticket_url=mk.get("url") or src.get("url_home"),
        )
        ev.fingerprint = fp
        out.append(ev)
    return out


def load_socrata(session, src: Dict[str, Any]) -> List[Dict[str, Any]]:
    m = src.get("map", {})
    r = session.get(src["url"], params={"$limit": src.get("limit", 1000)}, timeout=25)
    r.raise_for_status()
    rows = r.json() if isinstance(r.json(), list) else []
    return [{
        "name": row.get(m.get("name", "")),
        "lat": row.get(m.get("lat", "")),
        "lon": row.get(m.get("lon", "")),
        "days": row.get(m.get("days", "")),
        "hours": row.get(m.get("hours", "")),
        "address": row.get(m.get("address", "")),
    } for row in rows]


# ---- OpenStreetMap marketplaces (Overpass) ---------------------------------
# WHY THIS SOURCE. `market` was the thinnest lens in the database — ~1.4k events
# nationally against 20k+ for music — because every market here had to be
# curated by hand, city by city. OSM already holds `amenity=marketplace`
# worldwide WITH coordinates and an opening_hours schedule: exactly the two
# things this adapter needs, and the two most tedious to gather. No API key, and
# because the coordinates arrive with the row it costs NOTHING from the geocode
# budget — unlike the inline sources, where every market is a Photon lookup.
#
# It feeds oneday.cafe too: market_events tags every occurrence `food` as well.
_OSM_ABBR = {"mo": 0, "tu": 1, "we": 2, "th": 3, "fr": 4, "sa": 5, "su": 6}
_OSM_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
# A marketplace open most of the week is a SHOP — a permanent hall, a corner
# grocer tagged as a marketplace — not a market DAY. Expanding those would bury
# the real thing under a daily repeat of somewhere that is merely open.
_OSM_MAX_DAYS = 3


def _osm_season(text: str) -> Tuple[Optional[str], Optional[str]]:
    """'May-Sep' / 'Jun 1-Oct 7' -> ISO season bounds in the CURRENT year."""
    yr = datetime.now().year
    m = re.search(r"\b([a-z]{3})[a-z]*\.?\s*(\d{0,2})\s*-\s*([a-z]{3})[a-z]*\.?\s*(\d{0,2})", text)
    if not m or m.group(1) not in _OSM_MONTHS or m.group(3) not in _OSM_MONTHS:
        return None, None
    m1, d1, m2, d2 = _OSM_MONTHS[m.group(1)], m.group(2), _OSM_MONTHS[m.group(3)], m.group(4)
    try:
        start = date(yr, m1, int(d1) if d1 else 1)
        if d2:
            end = date(yr, m2, int(d2))
        else:                                     # bare month -> its last day
            end = date(yr + (1 if m2 == 12 else 0), (m2 % 12) + 1, 1) - timedelta(days=1)
        return start.isoformat(), end.isoformat()
    except ValueError:
        return None, None


def parse_opening_hours(oh: str) -> Optional[Dict[str, Any]]:
    """OSM opening_hours -> {days, hours, season_start, season_end}, or None when
    it is not a market day (open too often, or no parseable day + time)."""
    text = (oh or "").lower().strip()
    if not text or text == "off" or "closed" in text:
        return None
    s_start, s_end = _osm_season(text)
    days: set = set()
    times: List[Tuple[str, str]] = []
    # Rules are ';'-separated, each "<days> <from>-<to>". Both halves can repeat
    # ("Mo 16:30-20:00;Th-Sa 11:00-18:00") and the day half may be a range.
    day_range = r"\b(mo|tu|we|th|fr|sa|su)\s*-\s*(mo|tu|we|th|fr|sa|su)\b"
    for rule in text.split(";"):
        got: set = set()
        for a, b in re.findall(day_range, rule):
            i, j = _OSM_ABBR[a], _OSM_ABBR[b]
            got.update((i + k) % 7 for k in range((j - i) % 7 + 1))   # wraps Sa-Mo
        stripped = re.sub(day_range, " ", rule)
        for d in re.findall(r"\b(mo|tu|we|th|fr|sa|su)\b", stripped):
            got.add(_OSM_ABBR[d])
        t = re.search(r"(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})", rule)
        if got and t:
            days |= got
            times.append((t.group(1), t.group(2)))
    if not days or not times or len(days) > _OSM_MAX_DAYS:
        return None
    return {"days": ", ".join(_DAY_NAMES[d] for d in sorted(days)),
            "hours": f"{times[0][0]}-{times[0][1]}",
            "season_start": s_start, "season_end": s_end}


# Backoff base, in seconds: 5s / 15s / 45s across the three retries. A module
# constant rather than a literal so test_ingest_markets.py can set it to 0 — a
# stubbed 504 needs the RETRY LOGIC exercised, not the waiting, and at 65s per
# failing bbox the suite would take minutes and get skipped.
OVERPASS_BACKOFF_S = 5


def _overpass_fetch(session, endpoint, bbox, quiet=False):
    """Elements for one bbox, or None if the endpoint never answered.

    The public endpoint hands out a couple of slots and answers 429 (or 504)
    when they are busy — across 245 metros that is normal traffic, not an error,
    so it is worth waiting out rather than dropping the metro. Honour
    Retry-After when offered; otherwise back off 5s / 15s / 45s.
    """
    area = "{s},{w},{n},{e}".format(**bbox)
    q = ("[out:json][timeout:90];("
         'node["amenity"="marketplace"](' + area + ");"
         'way["amenity"="marketplace"](' + area + ");"
         ");out center tags;")
    for attempt in range(4):
        try:
            r = session.post(endpoint, data=q.encode("utf-8"), timeout=180)
            if r.status_code in (429, 504) and attempt < 3:
                wait = int(r.headers.get("Retry-After") or 0) or (OVERPASS_BACKOFF_S * 3 ** attempt)
                if not quiet:
                    print(f"[markets] overpass {bbox.get('name', '?')}: "
                          f"{r.status_code}, retrying in {wait}s")
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json().get("elements", [])
        except Exception as exc:                      # noqa: BLE001
            if attempt == 3:
                if not quiet:
                    print(f"[markets] overpass {bbox.get('name', '?')} FAILED: {exc}")
            else:
                time.sleep(OVERPASS_BACKOFF_S * 3 ** attempt)
    return None


def load_overpass(session, src: Dict[str, Any]) -> List[Dict[str, Any]]:
    """amenity=marketplace inside each configured bbox, as market dicts.

    A METRO THAT EXHAUSTS ITS RETRIES USED TO BE GONE FOR THE WEEK. The sweep is
    245 bboxes against a free endpoint, so a handful lose their slot on any given
    run — measured on an 8-metro sample, New York and Los Angeles both came back
    empty while the other six answered. The loop simply moved on, and because
    this source runs twice a week the two largest markets in the US could be
    absent for days with one line of log to say so.

    Two things fix that, and they are cheap. Failures are collected and given a
    SECOND PASS at the end, by which point the sweep has been running for twenty
    minutes and the endpoint has usually recovered — a retry that costs nothing
    when there is nothing to retry. And the outcome is COUNTED, so a partial
    sweep reports as a number rather than looking identical to a complete one.
    """
    endpoint = src.get("endpoint", "https://overpass-api.de/api/interpreter")
    out: List[Dict[str, Any]] = []
    seen: set = set()
    bboxes = list(src.get("bboxes", []))
    failed: List[Dict[str, Any]] = []
    pause = src.get("pause_s", 2)

    def absorb(bbox, els):
        hit = 0
        for el in els:
            t = el.get("tags") or {}
            name = (t.get("name") or "").strip()
            if not name:
                continue                          # an unnamed pin is not an event
            sched = parse_opening_hours(t.get("opening_hours") or "")
            if not sched:
                continue                          # no schedule -> nothing to date
            lat = el.get("lat", (el.get("center") or {}).get("lat"))
            lon = el.get("lon", (el.get("center") or {}).get("lon"))
            if lat is None or lon is None:
                continue
            key = (name.lower(), round(float(lat), 4), round(float(lon), 4))
            if key in seen:                       # configured bboxes may overlap
                continue
            seen.add(key)
            street = " ".join(x for x in (t.get("addr:housenumber"), t.get("addr:street")) if x)
            city = t.get("addr:city") or bbox.get("city") or ""
            # THE CITY IS NOT A STREET. This used to glue them into one `address`
            # and set no city at all, so every OSM market reached the database
            # with locality NULL and street_address "Berlin" — and most OSM
            # marketplaces have no addr:street, so "Berlin" was the whole of it.
            #
            # That is not just an ugly row. mapsee_supabase_sync._addr_parts
            # treats whatever is in `address` as a street and hands it to the US
            # Census batch geocoder, so a surveyed OSM point was offered up to be
            # overwritten by a lookup of a bare city name. It survives today only
            # because Census returns nothing for "Berlin"/"Paris"/"Hamburg" — a
            # US city of the same name and it would not. Keeping them apart means
            # _addr_parts sees no street and declines to geocode at all.
            mk = {"name": name, "lat": lat, "lon": lon,
                  "address": street or None,
                  "city": city or None,
                  # OSM hands over a SURVEYED point and derives its address text
                  # from it — the same reason mapsee_ingest_osm_food sets this.
                  # Without it the Census pass is allowed to move the pin, which
                  # is how a Renton restaurant ended up eleven miles away.
                  "coords_exact": True,
                  "url": t.get("website") or t.get("contact:website")}
            mk.update(sched)
            out.append(mk)
            hit += 1
        print(f"[markets] overpass {bbox.get('name', '?')}: {hit} market(s) "
              f"from {len(els)} marketplaces")

    for bbox in bboxes:
        els = _overpass_fetch(session, endpoint, bbox)
        if els is None:
            failed.append(bbox)
        else:
            absorb(bbox, els)
        time.sleep(pause)                         # a free endpoint; do not hammer it

    # SECOND PASS over whatever lost its slot. By now the sweep has been running
    # for a while and the endpoint has usually recovered; when nothing failed
    # this block costs one `if`.
    recovered = 0
    if failed:
        print(f"[markets] overpass: {len(failed)} of {len(bboxes)} metro(s) did not "
              f"answer — second pass: {', '.join(b.get('name', '?') for b in failed[:8])}"
              + (" …" if len(failed) > 8 else ""))
        still: List[Dict[str, Any]] = []
        for bbox in failed:
            els = _overpass_fetch(session, endpoint, bbox, quiet=True)
            if els is None:
                still.append(bbox)
            else:
                absorb(bbox, els)
                recovered += 1
            time.sleep(pause * 2)                 # it is already unhappy; go gentler
        # The number is the point. A sweep that silently covered 243 of 245
        # metros looks exactly like one that covered all of them, and this
        # source only runs twice a week — so a miss costs days, not minutes.
        print(f"[markets] overpass: recovered {recovered}, still missing {len(still)}"
              + (f" ({', '.join(b.get('name', '?') for b in still)})" if still else ""))
    return out


# ---- USDA Local Food Directories -------------------------------------------
# WHY THIS SOURCE. Every US source above is either one city's open-data portal
# or a list somebody typed by hand, so coverage stops at whichever metros have
# been curated. USDA runs the national registry — farmers markets, on-farm
# markets, CSAs, food hubs — and each row carries the two expensive fields
# (coordinates, and a weekly "Sat: 9:00 AM-2:00 PM;" schedule) already filled
# in, so nothing here touches the geocode budget.
#
# The catch is staleness: listings are self-reported and many still carry the
# season they were registered with, years ago. See _usda_season for how a season
# that ended in a past year is rolled forward, and _USDA_STALE_YEARS for the
# cutoff past which a listing is presumed dead rather than merely un-edited.
_USDA_BASE = "https://www.usdalocalfoodportal.com/api"
_USDA_DIRECTORIES = ["farmersmarket", "onfarmmarket"]
_USDA_MAX_SEASONS = 4          # the API exposes season1..season4 per listing
_USDA_STALE_YEARS = 4          # listing untouched this long -> presumed closed
_USDA_ABBR = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
_USDA_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}

# Times are meaningless without a zone once a source is national. State is the
# best signal the API gives; the handful of states split across two zones are
# resolved by longitude (or latitude, for Idaho's panhandle).
_USDA_STATE_TZ = {
    **{s: "America/New_York" for s in
       "CT DC DE FL GA IN KY MA MD ME MI NC NH NJ NY OH PA RI SC TN VA VT WV".split()},
    **{s: "America/Chicago" for s in
       "AL AR IA IL KS LA MN MO MS ND NE OK SD TX WI".split()},
    **{s: "America/Denver" for s in "CO MT NM UT WY".split()},
    **{s: "America/Los_Angeles" for s in "CA NV OR WA".split()},
    "AZ": "America/Phoenix", "ID": "America/Boise", "AK": "America/Anchorage",
    "HI": "Pacific/Honolulu", "PR": "America/Puerto_Rico", "VI": "America/Puerto_Rico",
    "GU": "Pacific/Guam",
}
_USDA_TZ_SPLITS = {
    "FL": lambda lat, lon: "America/Chicago" if lon < -85.0 else None,   # panhandle
    "TX": lambda lat, lon: "America/Denver" if lon < -104.9 else None,   # El Paso
    "TN": lambda lat, lon: "America/Chicago" if lon < -85.4 else None,
    "KY": lambda lat, lon: "America/Chicago" if lon < -85.9 else None,
    "IN": lambda lat, lon: "America/Chicago" if lon < -87.2 else None,
    "MI": lambda lat, lon: "America/Chicago" if lon < -87.0 else None,   # western UP
    "KS": lambda lat, lon: "America/Denver" if lon < -101.0 else None,
    "NE": lambda lat, lon: "America/Denver" if lon < -101.0 else None,
    "ND": lambda lat, lon: "America/Denver" if lon < -101.0 else None,
    "SD": lambda lat, lon: "America/Denver" if lon < -101.0 else None,
    "OR": lambda lat, lon: "America/Boise" if lon > -117.5 else None,    # Malheur
    "ID": lambda lat, lon: "America/Los_Angeles" if lat > 45.5 else None,
}


# A coarse United States outline, (lon, lat), for clipping the national query
# grid. It only has to be good enough to tell land from ocean at a 100-mile
# radius — points near the edge are kept anyway (see usda_grid), so an outline
# this rough costs a few wasted queries, never coverage.
_CONUS_OUTLINE = [
    (-124.7, 48.4), (-123.0, 49.0), (-95.2, 49.0), (-95.2, 49.4), (-89.5, 48.1),
    (-84.5, 46.5), (-82.5, 45.0), (-83.0, 42.0), (-79.0, 43.3), (-76.5, 44.5),
    (-71.5, 45.0), (-69.2, 47.4), (-67.0, 45.2), (-70.0, 43.0), (-70.0, 41.5),
    (-73.9, 40.5), (-75.5, 39.0), (-75.9, 37.0), (-75.5, 35.2), (-80.9, 32.0),
    (-81.5, 30.7), (-80.0, 26.5), (-80.4, 25.1), (-81.8, 24.4), (-82.8, 27.8),
    (-84.0, 30.0),
    (-88.0, 30.2), (-90.0, 29.0), (-93.8, 29.7), (-97.1, 26.0), (-99.2, 26.4),
    (-101.4, 29.8), (-103.0, 28.9), (-106.5, 31.8), (-111.0, 31.3), (-114.8, 32.5),
    (-117.1, 32.5), (-120.6, 34.5), (-122.0, 36.9), (-124.4, 40.4), (-124.6, 46.3),
]
# Islands and Alaska are mostly water inside their bounding box, so they get
# explicit points rather than a clipped grid.
_USDA_REGIONS = {
    "conus": {"outline": _CONUS_OUTLINE, "bbox": (24.0, 49.5, -125.0, -66.5)},
    "ak": {"points": [(61.2, -149.9), (64.8, -147.7), (58.3, -134.4), (60.5, -151.3)]},
    "hi": {"points": [(21.3, -157.8), (20.8, -156.3), (19.6, -155.5), (22.0, -159.5)]},
    "pr": {"points": [(18.4, -66.1)]},
}
_MILES_PER_DEG_LAT = 69.0


def _in_outline(lat: float, lon: float, outline: List[Tuple[float, float]]) -> bool:
    """Ray casting, (lon, lat) vertices."""
    inside = False
    n = len(outline)
    for i in range(n):
        x1, y1 = outline[i]
        x2, y2 = outline[(i + 1) % n]
        if (y1 > lat) != (y2 > lat):
            xi = x1 + (lat - y1) * (x2 - x1) / (y2 - y1)
            if lon < xi:
                inside = not inside
    return inside


def usda_grid(spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Query areas covering whole regions, spaced from the radius they use.

    The metro list every other US source is built on is a ceiling here and
    nowhere else: city portals are municipal and Ticketmaster is venue-based,
    but this directory is national, free, and returns coordinates — and farmers
    markets skew small-town relative to population, which is exactly the ground
    80 metro circles miss. Spacing is 1.3x the radius, so the square cell each
    circle has to cover has a half-diagonal of 0.92r and the country tiles with
    no seams. Changing radius_miles re-spaces the grid automatically; the two
    can never drift apart.
    """
    radius = spec.get("radius_miles", 100)
    step = radius * 1.3
    out: List[Dict[str, Any]] = []
    for region in spec.get("regions", ["conus"]):
        cfg = _USDA_REGIONS.get(region)
        if not cfg:
            continue
        if cfg.get("points"):
            for i, (lat, lon) in enumerate(cfg["points"], 1):
                out.append({"name": f"{region}-{i}", "lat": lat, "lon": lon, "radius": radius})
            continue
        lo_lat, hi_lat, lo_lon, hi_lon = cfg["bbox"]
        d_lat = step / _MILES_PER_DEG_LAT
        lat = lo_lat
        while lat <= hi_lat:
            d_lon = step / (_MILES_PER_DEG_LAT * max(0.2, math.cos(math.radians(lat))))
            lon = lo_lon
            while lon <= hi_lon:
                # Keep a point whose CELL touches land even when its centre sits
                # offshore — dropping those would open a hole along every coast.
                if any(_in_outline(lat + dy, lon + dx, cfg["outline"])
                       for dy, dx in ((0, 0), (d_lat / 2, 0), (-d_lat / 2, 0),
                                      (0, d_lon / 2), (0, -d_lon / 2))):
                    out.append({"name": f"{region} {lat:.1f},{lon:.1f}",
                                "lat": round(lat, 3), "lon": round(lon, 3),
                                "radius": radius})
                lon += d_lon
            lat += d_lat
    return out


def _usda_field(row: Dict[str, Any], *names: str) -> str:
    """First non-empty value among `names`, tolerating the location_/underscore
    spellings the directories are inconsistent about."""
    for n in names:
        for key in (n, f"location_{n}", n.replace("season", "season_")):
            v = row.get(key)
            if v not in (None, "", "null"):
                return str(v).strip()
    return ""


def _usda_state(row: Dict[str, Any]) -> Optional[str]:
    st = _usda_field(row, "state", "listing_state")
    if len(st) == 2 and st.isalpha():
        return st.upper()
    m = re.search(r"\b([A-Z]{2})\b[ ,]*\d{5}(?:-\d{4})?\s*$",
                  _usda_field(row, "address", "listing_address"))
    return m.group(1) if m else None


def _usda_timezone(state: Optional[str], lat: Optional[float], lon: Optional[float]) -> Optional[str]:
    if not state:
        return None
    split = _USDA_TZ_SPLITS.get(state)
    if split and lat is not None and lon is not None:
        alt = split(lat, lon)
        if alt:
            return alt
    return _USDA_STATE_TZ.get(state)


def _usda_hours(text: str) -> Optional[str]:
    """The time half of a schedule chunk, normalized for _parse_hours."""
    low = (text or "").lower()
    clock = r"\d{1,2}(?::\d{2})?\s*[ap]\.?\s*m\.?"
    m = re.search(rf"({clock})\s*(?:-|–|—|to)\s*({clock})", low)
    if m:
        return f"{m.group(1)} - {m.group(2)}"
    m24 = re.search(r"(\d{1,2}:\d{2})\s*(?:-|–|—|to)\s*(\d{1,2}:\d{2})", low)
    if m24:
        return f"{m24.group(1)} - {m24.group(2)}"
    one = re.search(rf"({clock})", low)         # start-only listing; better than dropping it
    return one.group(1) if one else None


def _usda_weekdays(text: str) -> set:
    """'Sat' / 'Mon-Fri' / 'Tues, Thurs' -> weekday indices."""
    low = (text or "").lower()
    tok = r"(mon|tue|wed|thu|fri|sat|sun)"
    rng = tok + r"[a-z]*\.?\s*-\s*" + tok
    out: set = set()
    for a, b in re.findall(rng, low):
        i, j = _USDA_ABBR[a], _USDA_ABBR[b]
        out.update((i + k) % 7 for k in range((j - i) % 7 + 1))
    for d in re.findall(tok, re.sub(rng, " ", low)):
        out.add(_USDA_ABBR[d])
    return out


def _usda_blocks(time_text: str) -> List[Tuple[str, str]]:
    """'Wed: 3:00 PM-7:00 PM;Sat: 9:00 AM-2:00 PM;' -> [(days, hours), ...].

    One block per distinct time window, NOT per day: a market open Saturday and
    Sunday 9-2 is one weekly schedule, while the same market's Wednesday
    afternoon session is a different one and must not inherit the morning hours.
    """
    by_hours: Dict[str, set] = {}
    for chunk in re.split(r"[;\n]+", time_text or ""):
        chunk = chunk.strip()
        if not chunk:
            continue
        # Read days and hours off the WHOLE chunk rather than splitting on the
        # "Sat:" separator — the separator is not always there ("Saturday 8:00
        # AM - 12:00 PM"), and splitting on the first colon eats the start hour.
        # The two patterns cannot collide: one matches day words, the other clocks.
        days = _usda_weekdays(chunk)
        hours = _usda_hours(chunk)
        if days and hours:
            by_hours.setdefault(hours, set()).update(days)
    return [(", ".join(_DAY_NAMES[d] for d in sorted(ds)), h) for h, ds in by_hours.items()]


def _usda_dates(text: str) -> List[date]:
    """Every date in a season string, in order. Handles '05/01/2025',
    '2025-05-01' and 'May 1' (bare month/day defaults to the current year)."""
    low = (text or "").lower()
    found: List[Tuple[int, date]] = []
    for m in re.finditer(r"(\d{4})-(\d{2})-(\d{2})", low):
        try:
            found.append((m.start(), date(int(m.group(1)), int(m.group(2)), int(m.group(3)))))
        except ValueError:
            pass
    for m in re.finditer(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b", low):
        yr = int(m.group(3))
        try:
            found.append((m.start(), date(yr + 2000 if yr < 100 else yr,
                                          int(m.group(1)), int(m.group(2)))))
        except ValueError:
            pass
    for m in re.finditer(r"\b([a-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s*(\d{4}))?", low):
        mo = _USDA_MONTHS.get(m.group(1)[:3])
        if not mo:
            continue
        try:
            found.append((m.start(), date(int(m.group(3)) if m.group(3) else datetime.now().year,
                                          mo, int(m.group(2)))))
        except ValueError:
            pass
    return [d for _, d in sorted(found)]


def _roll_year(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:                            # Feb 29 -> Feb 28
        return d.replace(year=d.year + years, day=28)


def _usda_season(text: str) -> Tuple[Optional[str], Optional[str]]:
    """Season string -> ISO (start, end), rolled into the current year when it
    ended in a past one.

    Listings are self-reported and rarely re-dated: most still carry the season
    they were registered with. Taken literally, every one of those expands to
    zero occurrences and the whole directory yields nothing — so a market whose
    season ran May-October in 2021 is assumed to still run May-October. The
    guard against reviving markets that genuinely closed is _USDA_STALE_YEARS,
    applied to the listing's own update time.
    """
    ds = _usda_dates(text)
    if not ds:
        return None, None
    start, end = ds[0], (ds[1] if len(ds) > 1 else ds[0])
    shift = datetime.now().year - end.year
    if shift > 0:
        start, end = _roll_year(start, shift), _roll_year(end, shift)
    return start.isoformat(), end.isoformat()


def _usda_fresh(row: Dict[str, Any], years: int) -> bool:
    stamp = _usda_field(row, "update_time", "updatetime", "update_date")
    yr = re.search(r"(19|20)\d{2}", stamp)
    if not yr:
        return True                               # no stamp -> give it the benefit
    return datetime.now().year - int(yr.group(0)) <= years


def _usda_query(session, src: Dict[str, Any], key: str, directory: str,
                area: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
    """One directory × one area. None means "stop the whole source" (bad key)."""
    params: Dict[str, Any] = {"apikey": key}
    if area.get("zip"):
        params["zip"] = str(area["zip"])
    else:
        params["x"] = area.get("lon", area.get("x"))
        params["y"] = area.get("lat", area.get("y"))
    params["radius"] = area.get("radius", src.get("radius", 30))
    try:
        r = session.get(f"{_USDA_BASE}/{directory}/", params=params, timeout=45)
    except Exception as exc:
        print(f"[markets] usda {directory} {area.get('name', '?')} FAILED: {exc}")
        return []
    if r.status_code in (401, 403):
        # Every area would fail the same way, so stop rather than spend 150
        # requests confirming it.
        print(f"[markets] usda: {r.status_code} from the API — check "
              f"USDA_LOCALFOOD_API_KEY. Abandoning this source.")
        return None
    if r.status_code != 200:
        print(f"[markets] usda {directory} {area.get('name', '?')}: HTTP {r.status_code}")
        return []
    try:
        payload = r.json()
    except ValueError:
        print(f"[markets] usda {directory} {area.get('name', '?')}: non-JSON response")
        return []
    if isinstance(payload, dict):
        payload = payload.get("data") or payload.get("results") or []
    return payload if isinstance(payload, list) else []


def load_usda(session, src: Dict[str, Any]) -> List[Dict[str, Any]]:
    key = os.environ.get(src.get("api_key_env", "USDA_LOCALFOOD_API_KEY"), "").strip()
    if not key:
        print("[markets] usda: no USDA_LOCALFOOD_API_KEY set — skipping "
              "(free key at usdalocalfoodportal.com/fe/datasharing)")
        return []
    stale_years = src.get("stale_years", _USDA_STALE_YEARS)
    areas = src.get("areas") or usda_grid(src.get("grid") or {})
    out: List[Dict[str, Any]] = []
    seen: set = set()
    rows_seen = skipped_stale = skipped_noschedule = 0
    probed = False
    for directory in src.get("directories", _USDA_DIRECTORIES):
        for area in areas:
            rows = _usda_query(session, src, key, directory, area)
            if rows is None:
                return out
            for row in rows:
                name = _usda_field(row, "listing_name", "name")
                if not name:
                    continue
                lat, lon = _to_float(row.get("location_y")), _to_float(row.get("location_x"))
                ident = _usda_field(row, "listing_id", "id") or name.lower()
                # Configured areas overlap, and radius queries are generous.
                if (directory, ident) in seen:
                    continue
                seen.add((directory, ident))
                rows_seen += 1
                if not _usda_fresh(row, stale_years):
                    skipped_stale += 1
                    continue
                address = _usda_field(row, "listing_address", "address") or None
                tz = _usda_timezone(_usda_state(row), lat, lon)
                url = _usda_field(row, "media_website", "listing_website", "website") or None
                got = 0
                for si in range(1, _USDA_MAX_SEASONS + 1):
                    hours_text = _usda_field(row, f"season{si}_time")
                    if not hours_text:
                        continue
                    s_start, s_end = _usda_season(_usda_field(row, f"season{si}_date"))
                    for days, hours in _usda_blocks(hours_text):
                        out.append({"name": name, "lat": lat, "lon": lon,
                                    "address": address, "url": url, "timezone": tz,
                                    "days": days, "hours": hours,
                                    "season_start": s_start, "season_end": s_end})
                        got += 1
                if not got:
                    skipped_noschedule += 1
                    if not probed:
                        # The directories are not versioned and the field names
                        # are undocumented, so if season1_time is ever spelled
                        # differently this line says so on the first run instead
                        # of the source quietly returning nothing.
                        probed = True
                        print(f"[markets] usda: first listing with no parseable schedule "
                              f"({name}) — fields present: {sorted(row)}")
            time.sleep(src.get("pause_s", 1))
        print(f"[markets] usda {directory}: {len(out)} schedule(s) so far "
              f"from {rows_seen} listing(s)")
    print(f"[markets] usda: {rows_seen} listing(s); skipped {skipped_stale} stale "
          f"(>{stale_years}y) and {skipped_noschedule} without a parseable schedule")
    return out


def ingest(store: EventStore, session, src: Dict[str, Any]) -> int:
    kind = src.get("type")
    if kind == "socrata":
        markets = load_socrata(session, src)
    elif kind == "overpass":
        markets = load_overpass(session, src)
    elif kind == "usda":
        markets = load_usda(session, src)
    else:
        markets = src.get("markets", [])
    kept = 0
    for mk in markets:
        for ev in market_events(mk, src, session):
            store.upsert(ev)
            kept += 1
    print(f"[markets] {src.get('name', '?')}: {kept} occurrences from {len(markets)} markets")
    return kept


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Expand recurring markets into dated Mapsee events.")
    ap.add_argument("--config", required=True)
    ap.add_argument("--store", default="mapsee_events.json")
    # Sources may declare "run_weekdays"; this ignores that and runs them all,
    # for a manual backfill or when testing a new source.
    ap.add_argument("--force", action="store_true",
                    help="run every source regardless of its run_weekdays")
    # SPLITTING THIS FILE ACROSS TWO JOBS IS THE POINT OF THESE.
    #
    # Every source here used to run in one step inside the `feeds` job, and the
    # OSM sweep is 245 Overpass calls against a free endpoint — measured, that
    # is over three hours on its own. Everything else in the file finishes in
    # about a minute. Live consequence on Monday 2026-08-17: the feeds job hit
    # its 240-minute ceiling INSIDE the markets step, and because the checkpoint
    # sync sits after it, that run delivered nothing at all — not the markets,
    # not the Socrata and ICS work that had already succeeded, and not the
    # twenty-odd sources queued behind it (Squarespace, MyListing, Luma, Tribe,
    # Mobilizon, Moshtix…), every one of them skipped.
    #
    # The same job on the Saturday and Sunday either side took 52 and 55
    # minutes, because run_weekdays keeps the OSM sweep to Mondays. So the sweep
    # does not merely get LOST to the timeout, it CAUSES it and takes the rest
    # of the job with it.
    #
    # So the slow source now runs as its own workflow job with its own store and
    # its own sync, exactly like RunSignup does, and these flags are how the two
    # jobs read one config file.
    ap.add_argument("--type", dest="only_type",
                    help="run only sources of this type (e.g. overpass)")
    ap.add_argument("--skip-type", dest="skip_type",
                    help="run everything EXCEPT sources of this type")
    a = ap.parse_args(argv)
    sources = json.loads(open(a.config, encoding="utf-8").read())
    session = requests.Session()
    session.headers.update({"User-Agent": "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)"})
    store = EventStore(a.store)
    total = 0
    for src in sources:
        kind = src.get("type")
        if a.only_type and kind != a.only_type:
            continue
        if a.skip_type and kind == a.skip_type:
            # Named, not silent: a step that quietly ingests nothing looks
            # exactly like one whose sources have all gone quiet.
            print(f"[markets] {src.get('name', '?')}: skipped (--skip-type {a.skip_type})")
            continue
        # A source can pin itself to certain weekdays. Markets are WEEKLY
        # schedules expanded over a 42-day horizon: re-deriving them every day
        # produces byte-identical rows, and the OSM sweep is 245 network calls,
        # so daily would spend half an hour to change nothing.
        wd = src.get("run_weekdays")
        if wd and not a.force and datetime.now().weekday() not in wd:
            print(f"[markets] {src.get('name', '?')}: skipped (runs on weekdays {wd})")
            continue
        try:
            total += ingest(store, session, src)
        except Exception as exc:
            print(f"[markets] {src.get('name', '?')} FAILED: {exc}")
    store.save()
    _save_cache(_CACHE)
    print(f"[markets] done: +{total} occurrences; store now holds {len(store.records)} unique events.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
