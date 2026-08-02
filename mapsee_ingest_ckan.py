#!/usr/bin/env python3
"""
mapsee_ingest_ckan.py — import public events from CKAN open-data portals.

WHY THIS ADAPTER. The catalog had three ways into a government open-data portal
— Socrata, OpenDataSoft and plain iCal — and CKAN was not one of them, which is
most of the reason `catalog_curate.py coverage` kept reporting Finland, Portugal,
Denmark and Czechia at zero. Those countries do publish; they publish through
CKAN. So do data.gov.uk, open.canada.ca, data.gov.ie, govdata.de, data.gov.au
and opendata.swiss.

Rows come from the DataStore extension (`/api/3/action/datastore_search`), which
returns JSON records with named fields — the same shape the Socrata adapter
already knows how to turn into events, so `row_to_event` is reused wholesale and
this file is only the fetch plus the date filter.

THE DATE FILTER IS CLIENT-SIDE, on purpose. datastore_search has no range
operator (`filters` is exact-match only) and datastore_search_sql is disabled on
most portals, so a future-events WHERE clause is not available the way it is on
Socrata. Instead rows are pulled sorted by start DESCENDING — future events
first — and anything already past is dropped here. That is why `limit` wants to
be comfortably larger than the number of upcoming events, not tuned tight.

Config (ckan_sources.json): a list of entries
  { "name": "...", "url": "https://data.gov.ie/api/3/action/datastore_search?resource_id=abc",
    "category": "community", "limit": 2000, "timezone": "Europe/Dublin",
    "geocode_venue": true, "geocode_suffix": ", Dublin, Ireland",
    "map": {"id":"...","title":"...","start":"...","venue":"...","lat":"...","lon":"..."} }

    python mapsee_ingest_ckan.py --config ckan_sources.json --store events.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from typing import Any, Dict, List

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

from mapsee_ingest import EventStore
import mapsee_ingest_opendata as od
from mapsee_ingest_opendata import _get, _iso_parts, make_venue_geocoder, row_to_event

UA = "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)"


def load_records(session, src: Dict[str, Any]) -> List[Dict[str, Any]]:
    """datastore_search rows, newest-dated first so the future is at the top."""
    params: Dict[str, Any] = {"limit": src.get("limit", 2000)}
    start = (src.get("map") or {}).get("start")
    if start and src.get("sort", True):
        params["sort"] = f"{start} desc"
    r = session.get(src["url"], params=params, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"http {r.status_code}")
    payload = r.json() or {}
    if not payload.get("success"):
        raise RuntimeError(str(payload.get("error"))[:160])
    return (payload.get("result") or {}).get("records") or []


def ingest(store: EventStore, session, src: Dict[str, Any]) -> int:
    rows = load_records(session, src)
    geocoder = make_venue_geocoder(session, src)
    start_col = (src.get("map") or {}).get("start")
    today = datetime.now().date().isoformat()
    kept = past = unusable = 0
    for row in rows:
        _sl, _su, date_key = _iso_parts(str(_get(row, start_col) or ""))
        if not date_key:
            unusable += 1
            continue
        if date_key < today:
            past += 1
            continue
        ev = row_to_event(row, src, geocoder)
        if not ev:
            unusable += 1
            continue
        store.upsert(ev)
        kept += 1
    print(f"[ckan] {src.get('name', '?')}: kept {kept} of {len(rows)} rows "
          f"({past} past, {unusable} unusable)")
    return kept


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import public events from CKAN portals.")
    ap.add_argument("--config", required=True)
    ap.add_argument("--store", default="mapsee_events.json")
    a = ap.parse_args(argv)
    if not os.path.exists(a.config):
        print(f"[ckan] no {a.config} — nothing to do")
        return 0
    sources = json.loads(open(a.config, encoding="utf-8").read())
    sources = sources.get("sources", []) if isinstance(sources, dict) else sources
    session = requests.Session()
    session.headers.update({"User-Agent": UA})
    store = EventStore(a.store)
    total = 0
    for src in sources:
        try:
            total += ingest(store, session, src)
        except Exception as exc:  # noqa: BLE001
            print(f"[ckan] {src.get('name', '?')} FAILED: {exc}")
    store.save()
    od._save_geo_cache(od._GEO_CACHE)      # shared across adapters; persist what we warmed
    print(f"[ckan] done: +{total} events; store now holds {len(store.records)} unique events.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
