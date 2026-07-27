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

    python mapsee_ingest_markets.py --config market_sources.json --store mapsee_events.json
"""
from __future__ import annotations

import argparse
import json
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

from mapsee_ingest import NormalizedEvent, EventStore, make_fingerprint, _to_float

# ---- persistent geocode cache (shared with the other adapters) --------------
GEO_CACHE_PATH = os.environ.get("GEOCODE_CACHE", "geocode_cache.json")


def _load_cache() -> Dict[str, Any]:
    try:
        return json.loads(open(GEO_CACHE_PATH, encoding="utf-8").read())
    except Exception:
        return {}


def _save_cache(c: Dict[str, Any]) -> None:
    try:
        open(GEO_CACHE_PATH, "w", encoding="utf-8").write(json.dumps(c))
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
    _CACHE[key] = list(out)
    return out


# ---- schedule parsing -------------------------------------------------------
_DAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
         "friday": 4, "saturday": 5, "sunday": 6}


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
    place = mk.get("address") or name
    tz = _tz(src.get("timezone"))
    out: List[NormalizedEvent] = []
    for d in _occurrences(weekdays, src.get("horizon_days", 42),
                          _as_date(mk.get("season_start")), _as_date(mk.get("season_end"))):
        ds = d.isoformat()
        fp = make_fingerprint(name, ds, place)
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
            category="market",
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


def ingest(store: EventStore, session, src: Dict[str, Any]) -> int:
    markets = load_socrata(session, src) if src.get("type") == "socrata" else src.get("markets", [])
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
    a = ap.parse_args(argv)
    sources = json.loads(open(a.config, encoding="utf-8").read())
    session = requests.Session()
    session.headers.update({"User-Agent": "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)"})
    store = EventStore(a.store)
    total = 0
    for src in sources:
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
