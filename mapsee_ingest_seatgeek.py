#!/usr/bin/env python3
"""
mapsee_ingest_seatgeek.py — import live events from the SeatGeek Platform API into
the Mapsee store, normalized + deduped alongside Ticketmaster and the feeds.

SeatGeek's Platform API is free: register at https://seatgeek.com/build for a
client_id. It covers concerts, sports, theater and comedy — largely DIFFERENT
inventory from Ticketmaster, so it widens the music / sports / arts layers. Per
SeatGeek's terms you link users back to SeatGeek (this ingester keeps each
event's SeatGeek url) and must not display other ticket sellers' listings.

    export SEATGEEK_CLIENT_ID=...            # from seatgeek.com/build
    python mapsee_ingest_seatgeek.py --latlong 47.6062,-122.3321 --radius 25 \
        --within-days 90 --store mapsee_events.json

Sweep many metros by calling once per metro (see aggregate-events.yml). Events
already carry lat/lon from SeatGeek, so no geocoding is needed.
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

API = "https://api.seatgeek.com/2/events"

_MIN_INTERVAL_S = 0.35        # ~3 req/s — SeatGeek 429s when the metro sweep hits it too fast
_last_req = [0.0]


def _get(session, params):
    """GET with polite spacing + 429 Retry-After/backoff, so the sweep RETRIES on
    a rate-limit instead of dropping the rest of the metro's pages."""
    gap = time.monotonic() - _last_req[0]
    if gap < _MIN_INTERVAL_S:
        time.sleep(_MIN_INTERVAL_S - gap)
    r = None
    for attempt in range(1, 6):
        r = session.get(API, params=params, timeout=30)
        _last_req[0] = time.monotonic()
        if r.status_code != 429:
            return r
        ra = r.headers.get("Retry-After")
        wait = float(ra) if (ra and ra.replace(".", "", 1).isdigit()) else min(2 ** attempt, 30)
        print(f"[seatgeek] 429 rate-limited; backing off {wait:.0f}s (attempt {attempt}/5)")
        time.sleep(wait)
    return r

# SeatGeek's top-level "type" -> Mapsee frontend category KEY (site/js/app.js).
_MUSIC = {"concert", "music_festival"}
# Stage & screen (standup, plays, broadway, dance, classical, film) → 'theater'.
_THEATER = {"theater", "broadway_tickets_national", "comedy", "dance_performance_tour",
            "classical_orchestral_instrumental", "classical_vocal", "cirque_du_soleil", "film"}


def _category(ev: Dict[str, Any]) -> str:
    t = (ev.get("type") or "").lower()
    if t in _MUSIC:
        return "music"
    if t in _THEATER:
        return "theater"
    if t == "family":
        return "kids"
    taxes = {(x.get("name") or "").lower() for x in (ev.get("taxonomies") or [])}
    if taxes & {"concert", "music"}:
        return "music"
    if taxes & {"theater", "comedy", "dance", "classical", "cirque"}:
        return "theater"
    if "family" in taxes:
        return "kids"
    if "sports" in taxes:
        return "sports"
    # SeatGeek's remaining top-level types are overwhelmingly sports leagues.
    return "sports" if t else "other"


def _iso_utc(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    return s if (s.endswith("Z") or "+" in s[10:]) else s + "Z"


def to_event(ev: Dict[str, Any]) -> Optional[NormalizedEvent]:
    title = (ev.get("title") or "").strip()
    dt_local = ev.get("datetime_local")
    if not title or not dt_local:
        return None
    venue = ev.get("venue") or {}
    loc = venue.get("location") or {}
    lat, lon = loc.get("lat"), loc.get("lon")
    if lat is None or lon is None:
        return None                                        # nowhere to pin it
    date_key = dt_local[:10]
    performers = [p.get("name") for p in (ev.get("performers") or []) if p.get("name")]
    price = (ev.get("stats") or {}).get("lowest_price")
    bits: List[str] = []
    if price:
        bits.append(f"Tickets from about ${price}.")
    if len(performers) > 1:
        bits.append("Lineup: " + ", ".join(performers[:8]))
    poster = next((p["image"] for p in (ev.get("performers") or []) if p.get("image")), None)
    nev = NormalizedEvent(
        source="seatgeek",
        source_id=str(ev.get("id")),
        name=title,
        description=" ".join(bits) or None,
        start_local=dt_local,
        start_utc=_iso_utc(ev.get("datetime_utc")),
        venue_name=venue.get("name"),
        latitude=float(lat), longitude=float(lon),
        address=venue.get("address"),
        city=venue.get("city"), region=venue.get("state"),
        country=venue.get("country"), postal_code=venue.get("postal_code"),
        category=_category(ev),
        lineup=performers,
        poster_image_url=poster,
        ticket_url=ev.get("url"),
    )
    nev.fingerprint = make_fingerprint(title, date_key, venue.get("name"), venue.get("city"))
    return nev


def ingest(store: EventStore, session, client_id, client_secret,
           lat: str, lon: str, radius_mi: int, within_days: int) -> int:
    now = datetime.now(timezone.utc)
    params: Dict[str, Any] = {
        "client_id": client_id,
        "lat": lat, "lon": lon, "range": f"{radius_mi}mi",
        "per_page": 100, "page": 1, "sort": "datetime_utc.asc",
        "datetime_utc.gte": now.strftime("%Y-%m-%dT%H:%M:%S"),
        "datetime_utc.lte": (now + timedelta(days=within_days)).strftime("%Y-%m-%dT%H:%M:%S"),
    }
    if client_secret:
        params["client_secret"] = client_secret
    kept = 0
    for page in range(1, 8):                               # cap ~700 events/metro
        params["page"] = page
        r = _get(session, params)
        if r.status_code != 200:
            print(f"[seatgeek] {lat},{lon} p{page} HTTP {r.status_code}")
            break
        data = r.json()
        events = data.get("events") or []
        if not events:
            break
        for ev in events:
            nev = to_event(ev)
            if nev:
                store.upsert(nev)
                kept += 1
        meta = data.get("meta") or {}
        if page * int(meta.get("per_page") or 100) >= int(meta.get("total") or 0):
            break
    print(f"[seatgeek] {lat},{lon}: kept {kept}")
    return kept


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import SeatGeek events near a point into the Mapsee store.")
    ap.add_argument("--latlong", required=True, help="lat,lon  e.g. 47.6062,-122.3321")
    ap.add_argument("--radius", type=int, default=25)
    ap.add_argument("--within-days", type=int, default=90)
    ap.add_argument("--store", default="mapsee_events.json")
    ap.add_argument("--client-id", default=os.environ.get("SEATGEEK_CLIENT_ID"))
    ap.add_argument("--client-secret", default=os.environ.get("SEATGEEK_CLIENT_SECRET"))
    a = ap.parse_args(argv)
    if not a.client_id:
        print("[seatgeek] no SEATGEEK_CLIENT_ID set — skipping (free key at seatgeek.com/build)")
        return 0
    try:
        lat, lon = a.latlong.split(",")
    except ValueError:
        sys.exit("--latlong must look like  47.6062,-122.3321")
    session = requests.Session()
    session.headers.update({"User-Agent": "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)"})
    store = EventStore(a.store)
    try:
        ingest(store, session, a.client_id, a.client_secret, lat.strip(), lon.strip(), a.radius, a.within_days)
    except Exception as exc:                               # one metro must not abort a sweep
        print(f"[seatgeek] {a.latlong} FAILED: {exc}")
    store.save()
    print(f"[seatgeek] store now holds {len(store.records)} unique events.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
