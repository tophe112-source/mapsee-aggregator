#!/usr/bin/env python3
"""
mapsee_ingest_axs.py - import live events from the AXS Partner API into the
Mapsee store, normalized + deduped alongside the other adapters.

AXS ticket-sells for a lot of the venues + festivals the big two miss -
renaissance faires, 9:30 Club / The Anthem / Merriweather-style rooms. AXS
BLOCKS non-browser clients on the web (HTTP 403) and gates its structured data
behind a partner API, so there is NO scrape path: this adapter targets the
official AXS Partner API and needs partner credentials.

    export AXS_API_KEY=...                    # from an AXS partner agreement
    export AXS_API_BASE=...                    # the events endpoint AXS gives you
    python mapsee_ingest_axs.py --latlong 38.9072,-77.0369 --radius 40 \
        --within-days 120 --store mapsee_events.json

GATED: with no AXS_API_KEY set the script prints a notice and exits 0 (imports
nothing), like the other optional adapters - the pipeline runs fine until you
have a key.

⚠️ PROVISIONAL MAPPING: unlike DICE (whose event shape we verified against live
data), AXS blocks all inspection, so `to_event` below maps a *reasonable* event
object using tolerant field lookups. When you receive AXS partner API docs (or a
sample response), confirm the field names in `to_event` / `ingest` - the fetch,
gating, pagination, and store wiring are correct regardless; only the JSON field
mapping may need renaming. Every unknown field is read defensively so a mismatch
drops the event rather than crashing the sweep.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

from mapsee_ingest import NormalizedEvent, EventStore, make_fingerprint

# AXS gives partners an events endpoint; set it via env so no code change is
# needed when you onboard. Left blank on purpose - the script no-ops without it.
API_BASE = os.environ.get("AXS_API_BASE", "").strip()

_MIN_INTERVAL_S = 0.4
_last_req = [0.0]


def _get(session, params) -> Optional[requests.Response]:
    gap = time.monotonic() - _last_req[0]
    if gap < _MIN_INTERVAL_S:
        time.sleep(_MIN_INTERVAL_S - gap)
    for attempt in range(1, 6):
        r = session.get(API_BASE, params=params, timeout=30)
        _last_req[0] = time.monotonic()
        if r.status_code != 429:
            return r
        ra = r.headers.get("Retry-After")
        wait = float(ra) if (ra and ra.replace(".", "", 1).isdigit()) else min(2 ** attempt, 30)
        print(f"[axs] 429 rate-limited; backing off {wait:.0f}s ({attempt}/5)")
        time.sleep(wait)
    return r


def _first(d: Dict[str, Any], *keys):
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return None


def _iso_utc(s):
    if not isinstance(s, str) or len(s) < 10:
        return None
    return s if (s.endswith("Z") or "+" in s[10:]) else s + "Z"


def to_event(ev: Dict[str, Any]) -> Optional[NormalizedEvent]:
    """PROVISIONAL - confirm field names against AXS partner docs. Reads every
    field defensively so an unexpected shape drops the event, never crashes."""
    name = str(_first(ev, "title", "name", "eventName") or "").strip()
    start = _first(ev, "eventDateTime", "startDateTime", "start", "date")
    if not name or not start:
        return None
    venue = ev.get("venue") or ev.get("location") or {}
    if not isinstance(venue, dict):
        venue = {}
    geo = venue.get("geo") or venue.get("location") or venue
    lat = _first(geo, "lat", "latitude")
    lon = _first(geo, "lon", "lng", "longitude")
    start_utc = _iso_utc(str(start))
    date_key = str(start)[:10]
    lineup = ev.get("performers") or ev.get("attractions") or []
    lineup = [p.get("name") if isinstance(p, dict) else p for p in lineup if p]
    lineup = [str(x).strip() for x in lineup if str(x or "").strip()]
    nev = NormalizedEvent(
        source="axs",
        source_id=str(_first(ev, "id", "eventId", "eventID") or name),
        name=name,
        description=_first(ev, "description", "info", "about"),
        start_local=None if start_utc else str(start),
        start_utc=start_utc,
        venue_name=_first(venue, "name", "venueName"),
        latitude=float(lat) if lat is not None else None,
        longitude=float(lon) if lon is not None else None,
        address=_first(venue, "address", "street", "addressLine1"),
        city=_first(venue, "city"),
        region=_first(venue, "state", "region", "stateCode"),
        country=_first(venue, "country", "countryCode"),
        postal_code=_first(venue, "postalCode", "zip", "zipCode"),
        # AXS is broad (music, comedy, theater, faires) - leave it generic and let
        # the sync's category keyword pass sort stage/screen from music.
        category=_first(ev, "category", "genre") or "other",
        lineup=lineup,
        poster_image_url=_first(ev, "image", "imageUrl", "eventImage"),
        ticket_url=_first(ev, "url", "ticketUrl", "eventUrl"),
    )
    nev.fingerprint = make_fingerprint(name, date_key, nev.venue_name, nev.city)
    return nev


def ingest(store: EventStore, session, within_days: int,
           lat: Optional[str], lon: Optional[str], radius_mi: Optional[int]) -> int:
    now = datetime.now(timezone.utc)
    # PROVISIONAL param names - align with AXS's documented query params.
    params: Dict[str, Any] = {
        "pageSize": 100, "page": 1,
        "startDate": now.strftime("%Y-%m-%d"),
        "endDate": (now + timedelta(days=within_days)).strftime("%Y-%m-%d"),
    }
    if lat and lon:
        params["lat"], params["lon"] = lat, lon
        if radius_mi:
            params["radius"] = radius_mi
    kept = 0
    for page in range(1, 16):
        params["page"] = page
        r = _get(session, params)
        if r is None or r.status_code != 200:
            code = getattr(r, "status_code", "ERR")
            print(f"[axs] page {page} HTTP {code}"
                  + (" - check AXS_API_KEY / AXS_API_BASE" if code in (401, 403) else ""))
            break
        body = r.json()
        events = body.get("events") or body.get("data") or (body if isinstance(body, list) else [])
        if not events:
            break
        for ev in events:
            nev = to_event(ev)
            if nev:
                store.upsert(nev)
                kept += 1
        if len(events) < params["pageSize"]:
            break
    print(f"[axs] kept {kept} events ({lat},{lon})")
    return kept


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import AXS partner events into the Mapsee store.")
    ap.add_argument("--latlong", help="lat,lon e.g. 38.9072,-77.0369")
    ap.add_argument("--radius", type=int, default=40, help="miles")
    ap.add_argument("--within-days", type=int, default=120)
    ap.add_argument("--store", default="mapsee_events.json")
    a = ap.parse_args(argv)

    key = os.environ.get("AXS_API_KEY", "").strip()
    if not key:
        print("[axs] AXS_API_KEY not set - skipping (no events imported).")
        return 0
    if not API_BASE:
        print("[axs] AXS_API_BASE not set - set it to the events endpoint AXS gives you.")
        return 0

    lat = lon = None
    if a.latlong:
        try:
            lat, lon = [p.strip() for p in a.latlong.split(",", 1)]
        except ValueError:
            print("[axs] --latlong must be 'lat,lon'"); return 2

    session = requests.Session()
    session.headers.update({
        # AXS partner auth is typically a bearer token or x-api-key - set whichever
        # your account uses (both sent here is harmless; the server reads one).
        "Authorization": f"Bearer {key}",
        "x-api-key": key,
        "User-Agent": "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)",
        "Accept": "application/json",
    })
    store = EventStore(a.store)
    ingest(store, session, a.within_days, lat, lon, a.radius)
    store.save()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
