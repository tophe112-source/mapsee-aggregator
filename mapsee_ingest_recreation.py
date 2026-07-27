#!/usr/bin/env python3
"""
mapsee_ingest_recreation.py — import dated events from Recreation.gov's RIDB API
(federal recreation lands: Forest Service, BLM, Army Corps, Reclamation, NPS) into
the Mapsee store for the outdoors layer.

Free API — create an account at https://www.recreation.gov and request an RIDB key
(https://ridb.recreation.gov/docs), then set RECREATION_GOV_KEY (50 req/min).

CAVEAT: RIDB is facilities/campsites/tours-focused; its dated public-events feed is
uneven, and national-park ranger programs are already covered by mapsee_ingest_nps.py.
So this is best-effort: it fetches whatever /events returns, keeps future ones that
have coordinates, and skips the rest. The field names are read defensively (RIDB's
docs GitHub repo is archived), so the first keyed run confirms the real yield.

    export RECREATION_GOV_KEY=...
    python mapsee_ingest_recreation.py --within-days 120 --store mapsee_events.json
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional, Tuple

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

from mapsee_ingest import NormalizedEvent, EventStore, make_fingerprint, _to_float

API = "https://ridb.recreation.gov/api/v1/events"


def _first(d: Dict[str, Any], *keys):
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return None


def _coords(ev: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
    lat = _to_float(_first(ev, "EventLatitude", "Latitude", "FacilityLatitude", "RecAreaLatitude"))
    lon = _to_float(_first(ev, "EventLongitude", "Longitude", "FacilityLongitude", "RecAreaLongitude"))
    if lat is None or lon is None:                         # linked objects when full=true
        for key in ("FACILITY", "RECAREA"):
            obj = ev.get(key)
            if isinstance(obj, list) and obj:
                obj = obj[0]
            if isinstance(obj, dict):
                lat = lat if lat is not None else _to_float(_first(obj, "FacilityLatitude", "RecAreaLatitude", "Latitude"))
                lon = lon if lon is not None else _to_float(_first(obj, "FacilityLongitude", "RecAreaLongitude", "Longitude"))
    return lat, lon


def _iso(s: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if not s or not isinstance(s, str):
        return (None, None)
    s = s.strip()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return (s[:10], s[:10])                        # date-only fallback
    return (dt.isoformat(), dt.date().isoformat())


def to_event(ev: Dict[str, Any]) -> Optional[NormalizedEvent]:
    name = _first(ev, "EventName", "Title", "Name")
    start_raw = _first(ev, "EventStartDate", "StartDate", "EventDate")
    if not name or not start_raw:
        return None
    start_local, date_key = _iso(str(start_raw))
    lat, lon = _coords(ev)
    if lat is None or lon is None:
        return None
    end_raw = _first(ev, "EventEndDate", "EndDate")
    end_local = _iso(str(end_raw))[0] if end_raw else None
    desc = _first(ev, "EventDescription", "Description")
    if desc:
        desc = re.sub(r"<[^>]+>", " ", str(desc))
        desc = re.sub(r"\s+", " ", desc).strip() or None
    venue = _first(ev, "EventLocation", "FacilityName", "RecAreaName")
    if not venue:                                          # pull from linked object (full=true)
        for _k in ("FACILITY", "RECAREA"):
            _obj = ev.get(_k)
            if isinstance(_obj, list) and _obj:
                _obj = _obj[0]
            if isinstance(_obj, dict):
                venue = _first(_obj, "FacilityName", "RecAreaName")
                if venue:
                    break
    nev = NormalizedEvent(
        source="recreation.gov",
        source_id=str(_first(ev, "EventID", "EventUID") or make_fingerprint(str(name), date_key, venue)),
        name=str(name),
        description=desc,
        start_local=start_local,
        end_local=end_local,
        venue_name=venue,
        latitude=lat, longitude=lon,
        category="outdoors",
        ticket_url=_first(ev, "EventURL", "RegistrationURL"),
    )
    nev.fingerprint = make_fingerprint(str(name), date_key, venue)
    return nev


def ingest(store: EventStore, session, key: str, within_days: int) -> int:
    today = datetime.now(timezone.utc).date()
    horizon = today + timedelta(days=within_days)
    kept = seen = offset = 0
    for _ in range(20):                                    # up to 1000 events
        r = session.get(API, headers={"apikey": key},
                        params={"limit": 50, "offset": offset, "full": "true"}, timeout=30)
        if r.status_code != 200:
            print(f"[recreation] HTTP {r.status_code}: {r.text[:150]}")
            break
        data = r.json()
        rows = data.get("RECDATA") or []
        if not rows:
            break
        for ev in rows:
            seen += 1
            nev = to_event(ev)
            if not nev:
                continue
            try:
                d = datetime.strptime((nev.start_local or "")[:10], "%Y-%m-%d").date()
            except ValueError:
                continue
            if today <= d <= horizon:
                store.upsert(nev)
                kept += 1
        offset += len(rows)
        total = ((data.get("METADATA") or {}).get("RESULTS") or {}).get("TOTAL_COUNT")
        if total and offset >= int(total):
            break
    print(f"[recreation] kept {kept} future events of {seen} seen")
    return kept


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import Recreation.gov (RIDB) events into the Mapsee store.")
    ap.add_argument("--within-days", type=int, default=120)
    ap.add_argument("--store", default="mapsee_events.json")
    ap.add_argument("--key", default=os.environ.get("RECREATION_GOV_KEY"))
    a = ap.parse_args(argv)
    if not a.key:
        print("[recreation] no RECREATION_GOV_KEY set — skipping (free key at ridb.recreation.gov/docs)")
        return 0
    session = requests.Session()
    session.headers.update({"User-Agent": "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)"})
    store = EventStore(a.store)
    try:
        ingest(store, session, a.key, a.within_days)
    except Exception as exc:
        print(f"[recreation] FAILED: {exc}")
    store.save()
    print(f"[recreation] store now holds {len(store.records)} unique events.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
