#!/usr/bin/env python3
"""
mapsee_ingest_ods.py - import public community-event data from Opendatasoft
portals into the Mapsee store.

Opendatasoft (public.opendatasoft.com and hundreds of city/region portals) is the
dominant INTERNATIONAL open-data platform, with a single uniform Explore v2.1
records API and geo-tagged event datasets - the closest international equivalent
to the US .gov/Socrata civic feeds. Flagship: `evenements-publics-openagenda`
(OpenAgenda public community events, ~1.2M rows with {lon,lat} coordinates).

Each dataset uses its own column names, so you describe it in ods_sources.json
(same idea as opendata_sources.json for Socrata). Events carry coordinates, so no
geocoding is needed. Data is open-licensed - we keep the source link.

    python mapsee_ingest_ods.py --config ods_sources.json --store feeds_events.json

Config per source:
    name        provenance label (source = "ods:<name>")
    domain      portal host, e.g. "public.opendatasoft.com"
    dataset     dataset id, e.g. "evenements-publics-openagenda"
    category    fixed Mapsee category key for the dataset (community/…)
    within_days upcoming window (default 90)
    countries   optional list of ISO country codes to keep (location_countrycode)
    max         row cap (default 6000)
    map         column names: id,title,title_alt,description,description_alt,
                start,end,venue,address,city,region,postal,country,url,geo
                (geo = a {lon,lat} object column) OR lat,lon (numeric columns),
                image (or image_alt) for a poster URL.
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

from mapsee_ingest import NormalizedEvent, EventStore, make_fingerprint

UA = "Mozilla/5.0 (compatible; MapseeAggregator/1.0; +https://mapsee.me; events@mapsee.me)"


def _clean(s: Any) -> Optional[str]:
    if not s or not isinstance(s, str):
        return None
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def _coords(rec: Dict[str, Any], m: Dict[str, str]):
    if m.get("geo") and isinstance(rec.get(m["geo"]), dict):
        g = rec[m["geo"]]
        lat, lon = g.get("lat") or g.get("latitude"), g.get("lon") or g.get("longitude")
        if lat is not None and lon is not None:
            return float(lat), float(lon)
    if m.get("lat") and m.get("lon"):
        try:
            return float(rec[m["lat"]]), float(rec[m["lon"]])
        except (TypeError, ValueError, KeyError):
            pass
    return None, None


def to_event(rec: Dict[str, Any], src: Dict[str, Any]) -> Optional[NormalizedEvent]:
    m = src["map"]
    title = _clean(rec.get(m.get("title"))) or _clean(rec.get(m.get("title_alt", "")))
    start = rec.get(m.get("start"))
    if not title or not start:
        return None
    lat, lon = _coords(rec, m)
    if lat is None:
        return None                                        # nowhere to pin it
    desc = _clean(rec.get(m.get("description", ""))) or _clean(rec.get(m.get("description_alt", "")))
    img = None
    for k in (m.get("image"), m.get("image_alt")):
        v = rec.get(k) if k else None
        if isinstance(v, str) and v.startswith("http"):
            img = v; break
    date_key = str(start)[:10]
    venue = _clean(rec.get(m.get("venue", "")))
    nev = NormalizedEvent(
        source="ods:" + src["name"],
        source_id=str(rec.get(m.get("id")) or make_fingerprint(title, date_key, venue)),
        name=title,
        description=desc,
        start_utc=str(start) if str(start).endswith("Z") or "+" in str(start) else None,
        start_local=None if (str(start).endswith("Z") or "+" in str(start)) else str(start),
        end_utc=str(rec.get(m.get("end")) or "") or None if rec.get(m.get("end")) else None,
        venue_name=venue,
        latitude=lat, longitude=lon,
        address=_clean(rec.get(m.get("address", ""))),
        city=_clean(rec.get(m.get("city", ""))),
        region=_clean(rec.get(m.get("region", ""))),
        country=rec.get(m.get("country", "")) or None,
        postal_code=rec.get(m.get("postal", "")) or None,
        category=src.get("category", "community"),
        poster_image_url=img,
        ticket_url=rec.get(m.get("url", "")) or src.get("url_home") or None,
    )
    nev.fingerprint = make_fingerprint(title, date_key, venue)
    return nev


def ingest_source(store: EventStore, session, src: Dict[str, Any]) -> int:
    m = src["map"]
    base = f"https://{src['domain']}/api/explore/v2.1/catalog/datasets/{src['dataset']}/records"
    # events STARTING within the window (not merely still-running) - a recurring
    # event whose first occurrence was years ago would otherwise land with a
    # stale past start date and get hidden by the client. Optional country filter.
    start_col = m.get("start")
    within = int(src.get("within_days", 90))
    conds = [f"{start_col}>=now()", f"{start_col}<=now(days={within})"]
    countries = src.get("countries") or []
    if countries and m.get("country"):
        vals = ",".join(f"'{c}'" for c in countries)
        conds.append(f"{m['country']} in ({vals})")
    where = " and ".join(conds)
    cap = int(src.get("max", 6000))
    kept = offset = 0
    order = m.get("start")
    while offset < min(cap, 10000):                        # ODS caps offset at 10000
        params = {"where": where, "limit": 100, "offset": offset}
        if order:
            params["order_by"] = order
        try:
            r = session.get(base + "?" + urllib.parse.urlencode(params), timeout=45)
            r.raise_for_status()
        except Exception as exc:
            print(f"[ods] {src['name']} offset {offset} failed: {exc}")
            break
        rows = (r.json() or {}).get("results") or []
        if not rows:
            break
        for rec in rows:
            nev = to_event(rec, src)
            if nev:
                store.upsert(nev)
                kept += 1
        offset += len(rows)
        if len(rows) < 100:
            break
        time.sleep(0.3)
    print(f"[ods] {src['name']}: kept {kept} events")
    return kept


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import Opendatasoft public event datasets into the Mapsee store.")
    ap.add_argument("--config", default="ods_sources.json")
    ap.add_argument("--store", default="feeds_events.json")
    a = ap.parse_args(argv)
    cfg = json.loads(open(a.config, encoding="utf-8").read())
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept": "application/json", "Accept-Encoding": "identity"})
    store = EventStore(a.store)
    total = 0
    for src in cfg.get("sources", []):
        try:
            total += ingest_source(store, session, src)
        except Exception as exc:                           # one dataset failing must not abort the rest
            print(f"[ods] {src.get('name','?')} FAILED: {exc}")
    store.save()
    print(f"[ods] done: +{total} events; store now holds {len(store.records)} unique events.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
