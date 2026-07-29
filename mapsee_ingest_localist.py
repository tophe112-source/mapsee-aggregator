#!/usr/bin/env python3
"""
mapsee_ingest_localist.py — import Localist (Concept3D) calendars into the Mapsee
store. Localist powers hundreds of university, city and arts-org event calendars,
and its API is READ-ONLY and PUBLIC: GET {base}/api/2/events returns JSON with
coordinates, so no geocoding is needed. Great for arts / learning / community.

Config (localist_sources.json): a list of
    { "name": "Cornell University",
      "base_url": "https://events.cornell.edu",
      "category": "learning",       # optional fixed Mapsee KEY (else derived per-event)
      "days": 90 }                  # optional lookahead window

    python mapsee_ingest_localist.py --config localist_sources.json --store mapsee_events.json
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, Optional

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

from mapsee_ingest import NormalizedEvent, EventStore, make_fingerprint, norm_categories

# Localist event_type filter name -> Mapsee frontend category KEY.
_TYPE_MAP = {
    "concert": "music", "music": "music", "performance": "arts", "theatre": "arts",
    "theater": "arts", "art": "arts", "exhibit": "arts", "exhibition": "arts",
    "dance": "arts", "film": "arts", "lecture": "learning", "workshop": "learning",
    "class": "learning", "seminar": "learning", "conference": "learning",
    "athletics": "sports", "sports": "sports", "game": "sports",
    "volunteer": "volunteer", "community": "community", "festival": "music",
    "food": "food", "market": "market", "kids": "kids", "family": "kids",
    "outdoor": "outdoors", "recreation": "outdoors",
    # movement (wegosie.com). Campus calendars carry these as their own
    # event_types, and every one of them used to land on "community".
    "fitness": "fitness", "wellness": "fitness", "exercise": "fitness",
    "yoga": "fitness", "intramural": "fitness", "run": "running",
    "walk": "fitness", "hike": "outdoors", "bike": "fitness",
}


def _categories(event: Dict[str, Any], default: Optional[str]) -> tuple:
    """(primary, extras). Localist events carry a LIST of event_types — a campus
    "Wellness Walk" is tagged Recreation AND Wellness — and this used to return
    the first match and bin the rest. Keeping the others as secondaries is free
    breadth: that walk now reaches both the outdoors and the movement lens."""
    hits = []
    for t in (((event.get("filters") or {}).get("event_types")) or []):
        m = _TYPE_MAP.get((t.get("name") or "").strip().lower())
        if m and m not in hits:
            hits.append(m)
    if not hits:
        return (default or "community", [])
    return (hits[0], norm_categories(hits[0], hits[1:]))


def to_event(wrap: Dict[str, Any], src: Dict[str, Any]) -> Optional[NormalizedEvent]:
    event = wrap.get("event") or {}
    title = (event.get("title") or "").strip()
    if not title:
        return None
    inst = event.get("event_instances") or []
    inst0 = (inst[0].get("event_instance") or {}) if (inst and isinstance(inst[0], dict)) else {}
    start = inst0.get("start")
    end = inst0.get("end")
    if not start:
        return None
    geo = event.get("geo") or {}
    try:
        lat = float(geo.get("latitude"))
        lon = float(geo.get("longitude"))
    except (TypeError, ValueError):
        return None                                        # no coordinates -> can't map it
    date_key = str(start)[:10]
    desc = (event.get("description_text") or "").strip() or None
    if desc:
        desc = " ".join(desc.split())
    place = event.get("location_name") or geo.get("street")
    nev = NormalizedEvent(
        source="localist:" + src["name"].lower().replace(" ", "-"),
        source_id=str(event.get("id") or make_fingerprint(title, date_key, place)),
        name=title,
        description=desc,
        start_local=start,
        end_local=end,
        venue_name=place,
        latitude=lat, longitude=lon,
        address=geo.get("street"), city=geo.get("city"),
        region=geo.get("state"), country=geo.get("country"), postal_code=geo.get("zip"),
        **dict(zip(("category", "categories"), _categories(event, src.get("category")))),
        poster_image_url=event.get("photo_url") or None,
        ticket_url=event.get("localist_url") or event.get("url") or None,
    )
    nev.fingerprint = make_fingerprint(title, date_key, place)
    return nev


def ingest(store: EventStore, session, src: Dict[str, Any]) -> int:
    base = (src.get("base_url") or "").rstrip("/")
    if not base:
        return 0                                           # allow note-only config entries
    days = src.get("days", 90)
    kept = 0
    for page in range(1, 11):                              # up to ~1000 events/source
        r = session.get(f"{base}/api/2/events",
                        params={"days": days, "pp": 100, "page": page}, timeout=25)
        if r.status_code != 200:
            print(f"[localist] {src.get('name','?')} p{page} HTTP {r.status_code}")
            break
        data = r.json()
        events = data.get("events") or []
        if not events:
            break
        for wrap in events:
            nev = to_event(wrap, src)
            if nev:
                store.upsert(nev)
                kept += 1
        pg = data.get("page") or {}
        if int(pg.get("current") or page) >= int(pg.get("total") or page):
            break
    print(f"[localist] {src.get('name','?')}: kept {kept}")
    return kept


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import Localist calendars into the Mapsee store.")
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
            print(f"[localist] {src.get('name', '?')} FAILED: {exc}")
    store.save()
    print(f"[localist] done: +{total}; store now holds {len(store.records)} unique events.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
