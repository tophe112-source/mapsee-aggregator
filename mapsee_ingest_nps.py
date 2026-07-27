#!/usr/bin/env python3
"""
mapsee_ingest_nps.py — import National Park Service events (ranger programs, guided
tours, talks, family + volunteer programs) into the Mapsee store.

Official, free API — get a key at https://www.nps.gov/subjects/developer/get-started.htm
(1,000 req/hr). Great for the outdoors / kids / learning / volunteer layers near
metros with national parks, monuments, seashores and historic sites. Events carry
their own lat/lon, so no geocoding is needed.

    export NPS_API_KEY=...
    python mapsee_ingest_nps.py --states WA,OR,CA --within-days 90 --store mapsee_events.json
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

from mapsee_ingest import NormalizedEvent, EventStore, make_fingerprint

API = "https://developer.nps.gov/api/v1/events"


def _category(ev: Dict[str, Any]) -> str:
    txt = " ".join(str(ev.get(k) or "") for k in ("category", "title", "description")).lower()
    for kw, key in (("volunteer", "volunteer"), ("cleanup", "volunteer"), ("stewardship", "volunteer"),
                    ("family", "kids"), ("junior ranger", "kids"), ("kid", "kids"),
                    ("art", "arts"), ("music", "music"), ("concert", "music"),
                    ("guided tour", "learning"), ("tour", "learning"), ("talk", "learning"),
                    ("workshop", "learning"), ("lecture", "learning"), ("demonstration", "learning"),
                    ("hike", "outdoors"), ("walk", "outdoors"), ("bird", "outdoors")):
        if kw in txt:
            return key
    return "outdoors"


def _hm(t):
    """Parse an NPS clock string to 'HH:MM:00' — accepts 12-hour ('10:00 AM',
    '2:30 PM') AND 24-hour ('14:30', '09:00:00')."""
    if not t or not isinstance(t, str):
        return None
    t = t.strip()
    m = re.match(r"(\d{1,2}):(\d{2})\s*([ap])\.?\s*m", t.lower())          # 12-hour
    if m:
        h, mi, ap = int(m.group(1)), m.group(2), m.group(3)
        if ap == "p" and h != 12:
            h += 12
        if ap == "a" and h == 12:
            h = 0
        return f"{h:02d}:{mi}:00" if 0 <= h <= 23 else None
    m = re.match(r"(\d{1,2}):(\d{2})(?::\d{2})?$", t)                      # 24-hour
    if m:
        h, mi = int(m.group(1)), m.group(2)
        return f"{h:02d}:{mi}:00" if 0 <= h <= 23 else None
    return None


def _f(ev, *keys):
    """First non-empty value across field-name variants — the NPS events endpoint
    mixes casing (timeStart vs timestart, infoURL vs infourl)."""
    for k in keys:
        v = ev.get(k)
        if v not in (None, ""):
            return v
    return None


def to_events(ev: Dict[str, Any], within_days: int) -> List[NormalizedEvent]:
    title = (ev.get("title") or "").strip()
    if not title:
        return []
    try:
        lat = float(_f(ev, "latitude", "Latitude"))
        lon = float(_f(ev, "longitude", "Longitude"))
    except (TypeError, ValueError):
        return []
    today = datetime.now(timezone.utc).date()
    horizon = today + timedelta(days=within_days)
    dstart = _f(ev, "dateStart", "datestart")
    dates = ev.get("dates") or ([dstart] if dstart else [])
    times = ev.get("times") or []
    t0 = times[0] if (times and isinstance(times[0], dict)) else {}
    tstart = _hm(_f(t0, "timeStart", "timestart"))
    tend = _hm(_f(t0, "timeEnd", "timeend"))
    site = _f(ev, "parkFullName", "parkfullname", "siteCode", "sitecode")
    eid = _f(ev, "id", "eventID", "eventid")
    # Every NPS event has a public page — use infoURL if given, else build it from
    # the event id, so the listing always links out to the webpage.
    link = _f(ev, "infoURL", "infourl", "url", "regResURL") \
        or (f"https://www.nps.gov/planyourvisit/event-details.htm?id={eid}" if eid else None)
    desc = (ev.get("description") or "").strip() or None
    if desc:
        desc = " ".join(desc.split())
    cat = _category(ev)
    out: List[NormalizedEvent] = []
    seen = set()
    for d in dates:
        try:
            dd = datetime.strptime(str(d), "%Y-%m-%d").date()
        except (TypeError, ValueError):
            continue
        if dd < today or dd > horizon or d in seen:
            continue
        seen.add(d)
        nev = NormalizedEvent(
            source="nps",
            source_id=f"{eid or title}|{d}",
            name=title,
            description=desc,
            start_local=f"{d}T{tstart}" if tstart else str(d),
            end_local=f"{d}T{tend}" if tend else None,
            venue_name=ev.get("location") or site,
            latitude=lat, longitude=lon,
            category=cat,
            poster_image_url=_f(ev, "imageUrl", "imageURL") or None,
            ticket_url=link,
        )
        nev.fingerprint = make_fingerprint(title, str(d), ev.get("location") or site)
        out.append(nev)
        if len(out) >= 3:                                  # cap recurrences per event
            break
    return out


def ingest_state(store: EventStore, session, api_key: str, state: str, within_days: int) -> int:
    kept = 0
    start = 0
    for _ in range(10):                                    # paginate (pageSize 50)
        r = session.get(API, params={"api_key": api_key, "stateCode": state,
                                     "pageSize": 50, "start": start}, timeout=30)
        if r.status_code != 200:
            print(f"[nps] {state} HTTP {r.status_code}")
            break
        data = r.json()
        rows = data.get("data") or []
        if not rows:
            break
        for ev in rows:
            for nev in to_events(ev, within_days):
                store.upsert(nev)
                kept += 1
        start += len(rows)
        if start >= int(data.get("total") or 0):
            break
    print(f"[nps] {state}: kept {kept}")
    return kept


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import NPS events into the Mapsee store.")
    ap.add_argument("--states", default="WA,OR,CA,NV,AZ,CO,UT,NM,TX,MN,IL,MO,TN,GA,FL,NC,VA,PA,NY,MA,DC,HI,AK",
                    help="comma-separated USPS state codes to sweep")
    ap.add_argument("--within-days", type=int, default=90)
    ap.add_argument("--store", default="mapsee_events.json")
    ap.add_argument("--api-key", default=os.environ.get("NPS_API_KEY"))
    a = ap.parse_args(argv)
    if not a.api_key:
        print("[nps] no NPS_API_KEY set — skipping (free key at nps.gov/subjects/developer)")
        return 0
    session = requests.Session()
    session.headers.update({"User-Agent": "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)"})
    store = EventStore(a.store)
    total = 0
    for st in [s.strip() for s in a.states.split(",") if s.strip()]:
        try:
            total += ingest_state(store, session, a.api_key, st, a.within_days)
        except Exception as exc:
            print(f"[nps] {st} FAILED: {exc}")
    store.save()
    print(f"[nps] done: +{total}; store now holds {len(store.records)} unique events.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
