#!/usr/bin/env python3
"""
mapsee_ingest_programs.py - curated RECURRING community programs into dated map
events (e.g. Seattle's Free Summer Meals: free kids' lunches at parks all summer).

Some high-value civic programs run daily or weekly at fixed locations for a
season but publish NO machine-readable feed - only a flyer or a live map widget.
This adapter turns a hand-curated JSON (locations + weekday schedule + season
window) into one dated event per site per operating day within a rolling
horizon, idempotently (fingerprint = title | date | address). The daily cleanup
prunes past ones and re-runs regenerate the window in place. Every event carries
the program's source URL, so the app's "More on this event" link points at the
official page.

Config (program_sources.json): a list of programs, each:
  { "name": "Seattle Free Summer Meals", "category": "community",
    "url": "https://www.hungerfreewa.org/freesummerfood",
    "timezone": "America/Los_Angeles",
    "season_start": "2026-06-29", "season_end": "2026-08-20",
    "days": "Monday Tuesday Wednesday Thursday Friday", "horizon_days": 42,
    "title_prefix": "Free Summer Meals",
    "blurb": "Free lunch, games and activities for kids and teens ages 0-18. No registration, just show up.",
    "sites": [ { "name": "North Acres Park",
                 "address": "12718 1st Ave NE, Seattle, WA 98125",
                 "start": "11:00", "end": "14:30",
                 "meals": "Lunch 11am-12pm, Snack 1pm-2pm" }, ... ] }

    python mapsee_ingest_programs.py --config program_sources.json --store mapsee_events.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from mapsee_geo_budget import geocode_allowed
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

from mapsee_ingest import NormalizedEvent, EventStore, make_fingerprint

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
    key = f"{query}|program".strip().lower()
    if key in _CACHE:
        return tuple(_CACHE[key])
    if not geocode_allowed():                        # over this run's budget → retry next run
        return (None, None)
    out: Tuple[Optional[float], Optional[float]] = (None, None)
    try:
        time.sleep(1.1)                              # Photon fair-use pacing
        r = session.get("https://photon.komoot.io/api/", params={"q": query, "limit": 1}, timeout=20)
        f = (r.json().get("features") or [None])[0]
        if f:
            c = f["geometry"]["coordinates"]
            out = (c[1], c[0])
    except Exception:
        pass
    _CACHE[key] = list(out)
    return out


_DAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
         "friday": 4, "saturday": 5, "sunday": 6}


def _weekdays(s: Optional[str]) -> List[int]:
    s = (s or "").lower()
    return sorted({v for k, v in _DAYS.items() if k in s})


def _tz(name: Optional[str]):
    if not name:
        return None
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        return None


def _localize(ds: str, t: Optional[str], tz) -> Tuple[str, Optional[str]]:
    """(start_local, start_utc) for a date + 'HH:MM' in tz. Without tz -> naive
    local (start_utc None); with tz -> a real UTC instant so the app shows the
    program at its true local time."""
    if not t:
        return ds, None
    if tz is not None:
        dt = datetime.fromisoformat(f"{ds}T{t}:00").replace(tzinfo=tz)
        return dt.isoformat(), dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"{ds}T{t}:00", None


def _as_date(s: Optional[str]):
    if not s:
        return None
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _occurrences(weekdays: List[int], horizon_days: int, s_start, s_end) -> List:
    """Every matching weekday from today through the horizon, clamped to the
    program's season window."""
    today = datetime.now().date()
    out = []
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


def program_events(prog: Dict[str, Any], session) -> List[NormalizedEvent]:
    weekdays = _weekdays(prog.get("days")) or [0, 1, 2, 3, 4]   # Mon-Fri default
    tz = _tz(prog.get("timezone"))
    s_start, s_end = _as_date(prog.get("season_start")), _as_date(prog.get("season_end"))
    horizon = int(prog.get("horizon_days", 42))
    category = prog.get("category", "community")
    prefix = prog.get("title_prefix") or prog.get("name") or "Community program"
    blurb = (prog.get("blurb") or "").strip()
    url = prog.get("url")
    label = "program:" + str(prog.get("name", "program")).lower().replace(" ", "-")
    out: List[NormalizedEvent] = []
    for site in prog.get("sites", []):
        sname = (site.get("name") or "").strip()
        if not sname:
            continue
        addr = site.get("address")
        lat, lon = site.get("lat"), site.get("lon")
        if lat is None or lon is None:
            lat, lon = _geocode(session, addr or f"{sname}, Seattle, WA")
        if lat is None or lon is None:
            continue                                 # nowhere to pin it (yet - retried next run)
        title = f"{prefix} - {sname}"
        meals = (site.get("meals") or "").strip()
        desc = " · ".join(x for x in (blurb, meals) if x) or None
        start_t, end_t = site.get("start"), site.get("end")
        for d in _occurrences(weekdays, horizon, s_start, s_end):
            ds = d.isoformat()
            fp = make_fingerprint(title, ds, addr or sname)
            sl, su = _localize(ds, start_t, tz)
            el, eu = _localize(ds, end_t, tz)
            ev = NormalizedEvent(
                source=label, source_id=fp, name=title, description=desc,
                start_local=sl, start_utc=su, end_local=el, end_utc=eu,
                venue_name=sname, latitude=lat, longitude=lon, address=addr,
                category=category, ticket_url=url,
            )
            ev.fingerprint = fp
            out.append(ev)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import curated recurring community programs into the Mapsee store.")
    ap.add_argument("--config", required=True)
    ap.add_argument("--store", default="mapsee_events.json")
    a = ap.parse_args(argv)
    programs = json.loads(open(a.config, encoding="utf-8").read())
    session = requests.Session()
    session.headers.update({"User-Agent": "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)"})
    store = EventStore(a.store)
    total = 0
    for prog in programs:
        try:
            evs = program_events(prog, session)
            for ev in evs:
                store.upsert(ev)
            total += len(evs)
            print(f"[programs] {prog.get('name', '?')}: +{len(evs)} events across {len(prog.get('sites', []))} sites")
        except Exception as exc:
            print(f"[programs] {prog.get('name', '?')} FAILED: {exc}")
    store.save()
    _save_cache(_CACHE)
    print(f"[programs] done: +{total}; store now holds {len(store.records)} unique events.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
