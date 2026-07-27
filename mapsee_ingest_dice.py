#!/usr/bin/env python3
"""
mapsee_ingest_dice.py - import live events from the DICE Partner API into the
Mapsee store, normalized + deduped alongside Ticketmaster / SeatGeek / the feeds.

DICE powers a huge slice of small + mid music clubs (Songbyrd, DC9, and hundreds
more nationwide), so this widens the music / theater layers with inventory the
big APIs miss. DICE's events carry a venue WITH coordinates, so no geocoding is
needed.

    export DICE_API_KEY=...                  # from a DICE partner agreement
    python mapsee_ingest_dice.py --city "Washington" --within-days 90 \
        --store mapsee_events.json
    # or sweep by point (server-side radius filter):
    python mapsee_ingest_dice.py --latlong 38.9072,-77.0369 --radius 40 --store ...

GATED: with no DICE_API_KEY set the script prints a notice and exits 0 (imports
nothing), exactly like the other optional adapters - so the pipeline runs fine
until you have a key. The endpoint + auth header are DICE's documented Partner
API; the event field mapping matches DICE's event object (name, date, timezone,
venues[].location, lineup, tags, ticket link). Tune API_BASE / the params below
to your partner account's documented filters if they differ.
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

API_BASE = os.environ.get("DICE_API_BASE", "https://partners-endpoint.dice.fm/api/v2/events")

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
        print(f"[dice] 429 rate-limited; backing off {wait:.0f}s ({attempt}/5)")
        time.sleep(wait)
    return r


# DICE tags lean overwhelmingly music/nightlife; only clear stage/screen tags
# move an event onto the theater layer (the sync also reclassifies comedy titles).
_THEATER_TAGS = {"comedy", "theatre", "theater", "spoken word", "film", "cinema"}


def _category(ev: Dict[str, Any]) -> str:
    tags = ev.get("tags") or ev.get("tags_types") or []
    names = {str(t.get("name") if isinstance(t, dict) else t).lower() for t in tags}
    if names & _THEATER_TAGS:
        return "theater"
    return "music"


def _start(ev: Dict[str, Any]):
    """(start_utc, start_local). DICE gives an ISO 'date' (partner API) and/or a
    'date_unix' epoch (UTC). Prefer an absolute instant so we never store a naive
    local time."""
    ts = ev.get("date_unix") or (ev.get("dates") or {}).get("event_start_date_unix")
    if isinstance(ts, (int, float)) and ts > 0:
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), None
    d = ev.get("date") or (ev.get("dates") or {}).get("event_start_date")
    if isinstance(d, str) and len(d) >= 10:
        return (d if (d.endswith("Z") or "+" in d[10:]) else None), d
    return None, None


def _image(ev: Dict[str, Any]) -> Optional[str]:
    imgs = ev.get("event_images") or ev.get("images") or {}
    if isinstance(imgs, dict):
        for k in ("landscape", "square", "portrait", "brand"):
            if imgs.get(k):
                return imgs[k]
    prev = ev.get("previews") or []
    if isinstance(prev, list) and prev and isinstance(prev[0], dict):
        return prev[0].get("src") or prev[0].get("url")
    return None


def to_event(ev: Dict[str, Any]) -> Optional[NormalizedEvent]:
    name = (ev.get("name") or "").strip()
    start_utc, start_local = _start(ev)
    if not name or not (start_utc or start_local):
        return None
    venues = ev.get("venues") or []
    v = venues[0] if venues else (ev.get("venue") or {})
    loc = v.get("location") or {}
    lat, lon = loc.get("lat"), loc.get("lng") if "lng" in loc else loc.get("lon")
    city = v.get("city")
    if isinstance(city, dict):
        city = city.get("name")
    lineup = [p.get("name") if isinstance(p, dict) else p
              for p in (ev.get("summary_lineup") or ev.get("lineup") or []) if p]
    lineup = [str(x).strip() for x in lineup if str(x or "").strip()]
    perm = ev.get("perm_name") or ev.get("slug")
    ticket_url = ev.get("url") or (f"https://dice.fm/event/{perm}" if perm else None)
    date_key = (start_utc or start_local or "")[:10]
    tz = (ev.get("dates") or {}).get("timezone") or ev.get("timezone")
    nev = NormalizedEvent(
        source="dice",
        source_id=str(ev.get("id") or perm or name),
        name=name,
        description=(ev.get("about") or ev.get("description") or None),
        start_local=start_local,
        start_utc=start_utc,
        timezone=tz,
        venue_name=v.get("name"),
        latitude=float(lat) if lat is not None else None,
        longitude=float(lon) if lon is not None else None,
        address=v.get("address"),
        city=city,
        region=(v.get("state") or v.get("region")),
        country=v.get("country"),
        postal_code=(v.get("postal_code") or v.get("zip")),
        category=_category(ev),
        lineup=lineup,
        poster_image_url=_image(ev),
        ticket_url=ticket_url,
    )
    nev.fingerprint = make_fingerprint(name, date_key, v.get("name"), city)
    return nev


def ingest(store: EventStore, session, within_days: int, city: Optional[str],
           lat: Optional[str], lon: Optional[str], radius_mi: Optional[int]) -> int:
    now = datetime.now(timezone.utc)
    params: Dict[str, Any] = {
        "page[size]": 50, "page[number]": 1,
        "filter[start_date][gte]": now.strftime("%Y-%m-%d"),
        "filter[start_date][lte]": (now + timedelta(days=within_days)).strftime("%Y-%m-%d"),
    }
    if city:
        params["filter[cities][]"] = city
    if lat and lon:
        params["filter[location][lat]"] = lat
        params["filter[location][lng]"] = lon
        if radius_mi:
            params["filter[location][radius]"] = radius_mi
    kept = 0
    for page in range(1, 21):                              # cap ~1000 events/sweep
        params["page[number]"] = page
        r = _get(session, params)
        if r is None or r.status_code != 200:
            code = getattr(r, "status_code", "ERR")
            print(f"[dice] page {page} HTTP {code}"
                  + (" - check DICE_API_KEY / partner filters" if code in (401, 403) else ""))
            break
        body = r.json()
        events = body.get("data") if isinstance(body, dict) else body
        events = events or []
        if not events:
            break
        for ev in events:
            nev = to_event(ev)
            if nev:
                store.upsert(nev)
                kept += 1
        if len(events) < params["page[size]"]:
            break
    print(f"[dice] kept {kept} events ({city or f'{lat},{lon}'})")
    return kept


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import DICE partner events into the Mapsee store.")
    ap.add_argument("--city", help="City filter, e.g. \"Washington\"")
    ap.add_argument("--latlong", help="lat,lon for a radius sweep, e.g. 38.9072,-77.0369")
    ap.add_argument("--radius", type=int, default=40, help="miles (with --latlong)")
    ap.add_argument("--within-days", type=int, default=90)
    ap.add_argument("--store", default="mapsee_events.json")
    a = ap.parse_args(argv)

    key = os.environ.get("DICE_API_KEY", "").strip()
    if not key:
        print("[dice] DICE_API_KEY not set - skipping (no events imported).")
        return 0
    if not a.city and not a.latlong:
        print("[dice] pass --city or --latlong."); return 2

    lat = lon = None
    if a.latlong:
        try:
            lat, lon = [p.strip() for p in a.latlong.split(",", 1)]
        except ValueError:
            print("[dice] --latlong must be 'lat,lon'"); return 2

    session = requests.Session()
    session.headers.update({
        "x-api-key": key,
        "User-Agent": "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)",
        "Accept": "application/json",
    })
    store = EventStore(a.store)
    ingest(store, session, a.within_days, a.city, lat, lon, a.radius)
    store.save()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
