#!/usr/bin/env python3
"""
mapsee_ingest_mobilizon.py - import events from Mobilizon instances.

    python mapsee_ingest_mobilizon.py --config mobilizon_sources.json --store feeds_events.json

Mobilizon (https://joinmobilizon.org) is federated, AGPL event software - the
fediverse's answer to Facebook Events. Instances are run by collectives, unions,
municipalities and activist networks, mostly across France, Germany, Switzerland,
Belgium and Italy. Public GraphQL, no key, no account.

    POST {base}/api   { "query": "...", "variables": {...} }

THREE THINGS THAT WILL CATCH YOU OUT, all found the hard way:

1. `geom` IS "lon;lat", LONGITUDE FIRST, semicolon-delimited. Lausanne comes back
   as "6.6327025;46.5218269". Read it the other way round and every event in
   Europe lands in the Gulf of Guinea or Somalia.
2. Use `searchEvents(beginsOn:)`, NOT `events`. The latter caps out and happily
   returns records from 2006, so a naive sweep spends its budget on a decade of
   dead listings.
3. `elements` is typed `EventSearchResult`, a union. Every field beyond the
   shared ones must sit inside `... on Event { }` or the whole query errors on
   every instance.

The category enum is the richest of any source here - 30 values that map almost
one-to-one onto mapsee's own vocabulary, so these arrive correctly filed rather
than guessed at from keywords.
"""
from __future__ import annotations

import argparse
import html as html_mod
import json
import re
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

from mapsee_ingest import NormalizedEvent, EventStore, make_fingerprint, norm_categories

UA = "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)"
_TAG = re.compile(r"<[^>]+>")

QUERY = """
query($b:DateTime,$l:Int,$p:Int){
  searchEvents(beginsOn:$b, limit:$l, page:$p){
    total
    elements{
      ... on Event {
        uuid title description beginsOn endsOn url category onlineAddress
        physicalAddress{ description street locality postalCode region country geom }
      }
    }
  }
}"""

# Mobilizon's 30-value EventCategory -> mapsee's 15 keys. The ones with no honest
# home (BUSINESS, NETWORKING, PETS, FASHION_BEAUTY, PHOTOGRAPHY, SPIRITUALITY,
# AUTO_BOAT_AIR) fall through to the instance default rather than being forced
# into a lens where nobody looking for them would find them.
_CATEGORY = {
    "ARTS": "arts", "PERFORMING_VISUAL_ARTS": "arts", "FILM_MEDIA": "arts", "CRAFTS": "arts",
    "THEATRE": "theater", "COMEDY": "theater",
    "MUSIC": "music", "PARTY": "party",
    "FOOD_DRINK": "food",
    "SPORTS": "sports", "HEALTH": "fitness",
    "OUTDOORS_ADVENTURE": "outdoors",
    "LEARNING": "learning", "BOOK_CLUBS": "learning", "SCIENCE_TECH": "learning",
    "LANGUAGE_CULTURE": "learning", "FAMILY_EDUCATION": "kids",
    "CAUSES": "volunteer", "MOVEMENTS_POLITICS": "community", "COMMUNITY": "community",
    "MEETING": "community", "LGBTQ": "community",
    "GAMES": "party",
}


def _clean(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    s = html_mod.unescape(_TAG.sub(" ", str(s)))
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def parse_geom(geom: Optional[str]) -> Tuple[Optional[float], Optional[float]]:
    """'lon;lat' -> (lat, lon). Longitude comes FIRST on the wire; see the header."""
    if not geom or ";" not in str(geom):
        return None, None
    lon_s, _, lat_s = str(geom).partition(";")
    try:
        lon, lat = float(lon_s), float(lat_s)
    except ValueError:
        return None, None
    # 0,0 is the null island the software writes for "no location", and anything
    # outside these ranges is a parse that went wrong rather than a real place.
    if (lat, lon) == (0.0, 0.0) or not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        return None, None
    return lat, lon


def to_event(ev: Dict[str, Any], site: Dict[str, Any]) -> Optional[NormalizedEvent]:
    name = _clean(ev.get("title"))
    begins = (ev.get("beginsOn") or "").strip()
    if not name or len(begins) < 10:
        return None
    pa = ev.get("physicalAddress") or {}
    lat, lon = parse_geom(pa.get("geom"))
    primary = _CATEGORY.get(str(ev.get("category") or "").upper(), site.get("category", "community"))
    extras = norm_categories(primary, [])
    street = _clean(pa.get("street"))
    nev = NormalizedEvent(
        source="mobilizon",
        source_id=str(ev.get("uuid") or ev.get("url") or make_fingerprint(name, begins[:10], pa.get("description"))),
        name=name,
        description=_clean(ev.get("description")),
        # beginsOn is ISO-8601 UTC, so it is an instant and belongs in start_utc.
        start_utc=begins.replace("+00:00", "Z"),
        end_utc=((ev.get("endsOn") or "").strip().replace("+00:00", "Z") or None),
        venue_name=_clean(pa.get("description")),
        latitude=lat, longitude=lon,
        address=street,
        city=_clean(pa.get("locality")) or site.get("default_city"),
        region=_clean(pa.get("region")) or site.get("default_region"),
        country=_clean(pa.get("country")) or site.get("default_country"),
        postal_code=_clean(pa.get("postalCode")),
        category=primary,
        categories=extras,
        ticket_url=ev.get("onlineAddress") or ev.get("url"),
    )
    nev.fingerprint = make_fingerprint(name, nev.start_utc[:10], nev.venue_name, nev.city)
    return nev


def ingest_site(store: EventStore, session, site: Dict[str, Any]) -> int:
    base = (site.get("base_url") or "").rstrip("/")
    if not base:
        print(f"[mobilizon] {site.get('name','?')}: no base_url"); return 0
    api = f"{base}/api"
    limit = int(site.get("limit", 100))
    max_pages = int(site.get("max_pages", 5))
    delay = float(site.get("crawl_delay", 1))
    begins = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    kept = 0
    for page in range(1, max_pages + 1):
        payload = {"query": QUERY, "variables": {"b": begins, "l": limit, "p": page}}
        try:
            r = session.post(api, data=json.dumps(payload), timeout=45)
        except Exception as exc:
            print(f"[mobilizon] {site.get('name')} p{page} failed: {exc}"); break
        if r.status_code != 200:
            print(f"[mobilizon] {site.get('name')} p{page} HTTP {r.status_code}"); break
        try:
            body = r.json()
        except Exception as exc:
            print(f"[mobilizon] {site.get('name')} p{page} bad JSON: {exc}"); break
        if body.get("errors"):
            print(f"[mobilizon] {site.get('name')} query errors: {str(body['errors'])[:180]}"); break
        node = (body.get("data") or {}).get("searchEvents") or {}
        rows = [e for e in (node.get("elements") or []) if e]
        if not rows:
            break
        for ev in rows:
            nev = to_event(ev, site)
            if nev:
                store.upsert(nev)
                kept += 1
        if page * limit >= int(node.get("total") or 0):
            break
        if delay:
            time.sleep(delay)
    print(f"[mobilizon] {site.get('name')}: kept {kept} events")
    return kept


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import Mobilizon instances into the Mapsee store.")
    ap.add_argument("--config", required=True)
    ap.add_argument("--store", default="mapsee_events.json")
    ap.add_argument("--only", help="ingest just this site name (substring match)")
    a = ap.parse_args(argv)

    cfg = json.loads(open(a.config, encoding="utf-8").read())
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Content-Type": "application/json", "Accept": "application/json"})
    store = EventStore(a.store)
    total = 0
    for site in cfg.get("sites", []):
        if a.only and a.only.lower() not in str(site.get("name", "")).lower():
            continue
        try:
            total += ingest_site(store, session, site)
        except Exception as exc:
            print(f"[mobilizon] {site.get('name','?')} FAILED: {exc}")
    store.save()
    print(f"[mobilizon] done: +{total} events; store now holds {len(store.records)} unique events.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
