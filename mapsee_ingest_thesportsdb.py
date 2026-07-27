#!/usr/bin/env python3
"""
mapsee_ingest_thesportsdb.py — import upcoming fixtures for chosen leagues from
TheSportsDB (a free, open sports database) into the Mapsee store, for the sports
layer. Free JSON API; the shared test key "3" works for light use, a $9/mo key
raises limits (set THESPORTSDB_KEY).

Fixtures carry a venue NAME but no coordinates, so venues are geocoded once via
Photon (OSM) and cached in geocode_cache.json — the same pattern (and cache file)
as the ICS / open-data adapters.

Config (thesportsdb_leagues.json):
    { "api_key": "3",
      "leagues": [ {"id": "4387", "name": "NBA"}, {"id": "4346", "name": "MLS"} ] }

    python mapsee_ingest_thesportsdb.py --config thesportsdb_leagues.json --store mapsee_events.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from mapsee_geo_budget import geocode_allowed
from typing import Any, Dict, Optional, Tuple

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

from mapsee_ingest import NormalizedEvent, EventStore, make_fingerprint

# ---- persistent geocode cache (shared with the ICS/open-data adapters) -------
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


def _geocode(session, venue: str) -> Tuple[Optional[float], Optional[float]]:
    key = f"{venue}|tsdb".strip().lower()
    if key in _CACHE:
        return tuple(_CACHE[key])
    if not geocode_allowed():                              # over this run's budget → retry next run
        return (None, None)
    out: Tuple[Optional[float], Optional[float]] = (None, None)
    try:
        time.sleep(1.1)                                    # Photon fair-use pacing
        r = session.get("https://photon.komoot.io/api/", params={"q": venue, "limit": 1}, timeout=20)
        f = (r.json().get("features") or [None])[0]
        if f:
            c = f["geometry"]["coordinates"]
            out = (c[1], c[0])
    except Exception:
        pass
    _CACHE[key] = list(out)
    return out


def _iso_utc(ts: Optional[str]) -> Optional[str]:
    if not ts:
        return None
    return ts if (ts.endswith("Z") or "+" in ts[10:]) else ts + "Z"


def to_event(session, ev: Dict[str, Any], league_name: str, category: str) -> Optional[NormalizedEvent]:
    title = (ev.get("strEvent") or "").strip()
    date = ev.get("dateEvent")
    venue = (ev.get("strVenue") or "").strip()
    if not title or not date or not venue:
        return None
    lat, lon = _geocode(session, venue)
    if lat is None or lon is None:
        return None
    ts = ev.get("strTimestamp")
    tstr = ev.get("strTime")
    start_local = ts or (f"{date}T{tstr}" if tstr else str(date))
    home, away = ev.get("strHomeTeam", ""), ev.get("strAwayTeam", "")
    matchup = f"{home} vs {away}".strip(" vs")
    nev = NormalizedEvent(
        source="thesportsdb:" + league_name.lower().replace(" ", "-"),
        source_id=str(ev.get("idEvent") or make_fingerprint(title, str(date), venue)),
        name=title,
        description=f"{league_name}: {matchup}".strip(": ") or None,
        start_local=start_local,
        start_utc=_iso_utc(ts),
        venue_name=venue,
        latitude=lat, longitude=lon,
        category=category or "sports",
        ticket_url=(f"https://www.thesportsdb.com/event/{ev.get('idEvent')}" if ev.get("idEvent") else None),
    )
    nev.fingerprint = make_fingerprint(title, str(date)[:10], venue)
    return nev


def ingest_league(store: EventStore, session, api_key: str, league: Dict[str, Any], default_cat: str) -> int:
    lid = str(league.get("id"))
    name = league.get("name") or lid
    r = session.get(f"https://www.thesportsdb.com/api/v1/json/{api_key}/eventsnextleague.php",
                    params={"id": lid}, timeout=25)
    if r.status_code != 200:
        print(f"[thesportsdb] {name} HTTP {r.status_code}")
        return 0
    events = (r.json() or {}).get("events") or []
    kept = 0
    for ev in events:
        nev = to_event(session, ev, name, league.get("category") or default_cat)
        if nev:
            store.upsert(nev)
            kept += 1
    print(f"[thesportsdb] {name}: kept {kept} of {len(events)}")
    return kept


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import TheSportsDB league fixtures into the Mapsee store.")
    ap.add_argument("--config", required=True)
    ap.add_argument("--store", default="mapsee_events.json")
    a = ap.parse_args(argv)
    cfg = json.loads(open(a.config, encoding="utf-8").read())
    api_key = os.environ.get("THESPORTSDB_KEY") or cfg.get("api_key") or "3"
    session = requests.Session()
    session.headers.update({"User-Agent": "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)"})
    store = EventStore(a.store)
    total = 0
    for lg in cfg.get("leagues", []):
        try:
            total += ingest_league(store, session, api_key, lg, cfg.get("category", "sports"))
        except Exception as exc:
            print(f"[thesportsdb] {lg.get('name', '?')} FAILED: {exc}")
    store.save()
    _save_cache(_CACHE)
    print(f"[thesportsdb] done: +{total}; store now holds {len(store.records)} unique events.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
