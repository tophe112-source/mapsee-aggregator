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
from typing import Any, Dict, List, Optional

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

from mapsee_ingest import NormalizedEvent, EventStore, make_fingerprint, norm_categories

# Localist event_type filter name -> Mapsee frontend category KEY.
_TYPE_MAP = {
    "concert": "music", "music": "music", "performance": "arts",
    # A campus calendar's own "Theatre" event_type is the least ambiguous signal
    # any adapter gets, and it used to land on 'arts' — so a production showed a
    # 🎨 pin labelled Arts, and nothing downstream ever corrected it:
    # _PROMOTABLE_TO_THEATER in mapsee_supabase_sync.py is {"music", "other"},
    # which does not include 'arts'. 'performance' stays 'arts' on purpose — a
    # dance or a recital is tagged that way as often as a play is.
    "theatre": "theater", "theater": "theater",
    "art": "arts", "exhibit": "arts", "exhibition": "arts",
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


def _instance(event: Dict[str, Any]) -> Dict[str, Any]:
    """The single occurrence Localist attached to THIS wrap.

    /api/2/events returns one wrap PER OCCURRENCE, not per event: a three-month
    exhibition comes back once for every day it is open, same event id each
    time, each wrap carrying exactly one instance on a different date. Measured
    on UT Dallas 2026-08-12: 774 wraps for 357 distinct events, one exhibition
    accounting for 59 of them on its own.
    """
    inst = event.get("event_instances") or []
    return (inst[0].get("event_instance") or {}) if (inst and isinstance(inst[0], dict)) else {}


def _instance_start(event: Dict[str, Any]) -> str:
    return str(_instance(event).get("start") or "")


def to_event(wrap: Dict[str, Any], src: Dict[str, Any]) -> Optional[NormalizedEvent]:
    event = wrap.get("event") or {}
    title = (event.get("title") or "").strip()
    if not title:
        return None
    inst0 = _instance(event)
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
    # IDENTITY IS THE EVENT'S OWN FIRST DATE, not the occurrence this wrap
    # happens to carry. That distinction is the whole bug.
    #
    # `days=90` is a ROLLING window, so which occurrences come back depends on
    # the day the job runs. Every wrap of one event shares a source_id (the
    # Localist id) but produced a DIFFERENT fingerprint here, so EventStore
    # re-keyed the record once per wrap and the survivor was whichever occurrence
    # happened to be processed last. Move the window and that survivor changes,
    # so the fingerprint changed on nearly every run — a new external_id, a new
    # database row, and the old one left behind because the sync upserts on
    # (external_source, external_id). Meanwhile _fill_missing kept the FIRST
    # start_local it saw, which is why the duplicates all carried an identical
    # starts_at and different ids: the visible field was stable and the identity
    # underneath it was not.
    #
    # first_date is a property of the event, so it does not move with the window.
    # Measured on UT Dallas: 311 of 315 single-occurrence events already have
    # first_date == their instance date, so their identity is UNCHANGED and the
    # catalog is not mass re-keyed. The 4 that differ are recurring events whose
    # earlier dates have passed - exactly the unstable ones this is fixing.
    date_key = str(event.get("first_date") or start)[:10]
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
    # One wrap per OCCURRENCE arrives (see _instance), and they span pages, so
    # collapse by event id across the whole fetch before anything is upserted.
    # Keeping the EARLIEST occurrence makes start_local the next upcoming date
    # rather than whichever wrap happened to land first, which is what the
    # listing should show and what _fill_missing was silently deciding before.
    #
    # Upserting each wrap also meant EventStore.upsert ran once per occurrence
    # for the same (source, source_id), popping and re-adding the record every
    # time. Sixty writes to arrive where one belongs.
    best: Dict[Any, Dict[str, Any]] = {}
    order: List[Any] = []
    seen_wraps = 0
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
            seen_wraps += 1
            event = wrap.get("event") or {}
            # No id to group on -> keep the wrap on its own key rather than
            # letting every id-less event collapse into one.
            key = event.get("id")
            if key is None:
                key = ("_noid", seen_wraps)
            prev = best.get(key)
            if prev is None:
                best[key] = wrap
                order.append(key)
            elif _instance_start(event) < _instance_start(prev.get("event") or {}):
                best[key] = wrap
        pg = data.get("page") or {}
        if int(pg.get("current") or page) >= int(pg.get("total") or page):
            break
    kept = 0
    for key in order:
        nev = to_event(best[key], src)
        if nev:
            store.upsert(nev)
            kept += 1
    collapsed = seen_wraps - len(order)
    print(f"[localist] {src.get('name','?')}: kept {kept}"
          + (f" ({collapsed} duplicate occurrence wrap(s) collapsed)" if collapsed else ""))
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
