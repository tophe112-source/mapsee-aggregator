#!/usr/bin/env python3
"""
mapsee_ingest_gancio.py - import events from Gancio instances.

    python mapsee_ingest_gancio.py --config gancio_sources.json --store feeds_events.json

Gancio (https://gancio.org) is self-hosted, AGPL event software used by social
centres, squats, DIY venues, hackerspaces and neighbourhood collectives, mostly
across Europe and Latin America. Every instance serves the same `/api/events`
with no key, and - unlike most of the long tail - almost every event carries
`place.latitude`/`place.longitude`, so these land on the map without a geocoder
round trip at all.

This is the grassroots layer no ticketing API has: a benefit gig in a Bologna
social centre, a repair cafe in Nijmegen, a squat's assembly in Berlin. It is
also genuinely international, which is where mapsee's curated coverage is
thinnest.

    GET {base}/api/events?start=<unix>&end=<unix>&max=<n>

start/end are UNIX SECONDS, not ISO dates, and so are `start_datetime` and
`end_datetime` on the way back - the one thing about this API that will catch you
out. Only `/api/events` is public; `/api/places`, `/api/tags` and `/api/settings
` are admin-only and 403.

robots.txt is byte-identical across the instances checked and allows everything
(`user-agent: *`, `allow: /`, no Disallow, no crawl-delay); there is no terms
page on any of them. A polite default delay is still applied - these are
volunteer-run servers, several on home connections.
"""
from __future__ import annotations

import argparse
import html as html_mod
import json
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

from mapsee_ingest import NormalizedEvent, EventStore, make_fingerprint, norm_categories

UA = "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)"
_TAG = re.compile(r"<[^>]+>")

# Gancio tags are free text, but a handful recur across instances often enough to
# be worth reading. Anything not here falls back to the instance's own category.
_TAG_CATEGORY = {
    "concerto": "music", "concert": "music", "konzert": "music", "musica": "music",
    "music": "music", "gig": "music", "dj": "party", "festa": "party", "party": "party",
    "teatro": "theater", "theater": "theater", "theatre": "theater",
    "cinema": "arts", "film": "arts", "mostra": "arts", "arte": "arts", "art": "arts",
    "workshop": "learning", "corso": "learning", "seminar": "learning", "talk": "learning",
    "presentazione": "learning", "vortrag": "learning", "lezione": "learning",
    "cena": "food", "pranzo": "food", "food": "food", "vegan": "food", "brunch": "food",
    "mercatino": "market", "mercato": "market", "market": "market", "flohmarkt": "market",
    "assemblea": "community", "assembly": "community", "plenum": "community",
    "sport": "sports", "yoga": "fitness", "trekking": "outdoors", "escursione": "outdoors",
}


def _clean(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    s = html_mod.unescape(_TAG.sub(" ", str(s)))
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def _iso_utc(unix_seconds) -> Optional[str]:
    try:
        n = int(unix_seconds)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    return datetime.fromtimestamp(n, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _f(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f == 0.0 else f


def to_event(ev: Dict[str, Any], site: Dict[str, Any]) -> Optional[NormalizedEvent]:
    name = _clean(ev.get("title"))
    start_utc = _iso_utc(ev.get("start_datetime"))
    if not name or not start_utc:
        return None
    place = ev.get("place") or {}
    lat, lon = _f(place.get("latitude")), _f(place.get("longitude"))
    tags = [str(t).strip().lower() for t in (ev.get("tags") or []) if str(t or "").strip()]
    primary = next((_TAG_CATEGORY[t] for t in tags if t in _TAG_CATEGORY), site.get("category", "community"))
    extras = norm_categories(primary, [_TAG_CATEGORY.get(t) for t in tags])
    base = (site.get("base_url") or "").rstrip("/")
    slug = ev.get("slug") or ev.get("id")
    # `online_locations` is where an instance puts a ticket shop or a stream; the
    # event's own page is the honest fallback and always exists.
    link = next((u for u in (ev.get("online_locations") or []) if str(u).startswith("http")), None)
    nev = NormalizedEvent(
        source="gancio",
        source_id=f"{base}#{ev.get('id')}",
        name=name,
        description=_clean(ev.get("description")),
        # start_utc, NOT start_local: the API hands back an instant, and inventing
        # a local wall-clock from it would be inventing a timezone we were not told.
        start_utc=start_utc,
        end_utc=_iso_utc(ev.get("end_datetime")),
        venue_name=_clean(place.get("name")),
        latitude=lat, longitude=lon,
        address=_clean(place.get("address")),
        city=site.get("default_city"),
        region=site.get("default_region"),
        country=site.get("default_country"),
        category=primary,
        categories=extras,
        ticket_url=link or (f"{base}/event/{slug}" if slug else None),
    )
    nev.fingerprint = make_fingerprint(name, start_utc[:10], nev.venue_name, nev.city)
    return nev


def ingest_site(store: EventStore, session, site: Dict[str, Any]) -> int:
    base = (site.get("base_url") or "").rstrip("/")
    if not base:
        print(f"[gancio] {site.get('name','?')}: no base_url"); return 0
    now = datetime.now(timezone.utc)
    params = {
        "start": int(now.timestamp()),
        "end": int((now + timedelta(days=int(site.get("within_days", 180)))).timestamp()),
        "max": int(site.get("max", 500)),
    }
    try:
        r = session.get(f"{base}/api/events", params=params, timeout=45)
    except Exception as exc:
        print(f"[gancio] {site.get('name')} failed: {exc}"); return 0
    if r.status_code != 200:
        print(f"[gancio] {site.get('name')} HTTP {r.status_code}"); return 0
    try:
        rows = r.json()
    except Exception as exc:
        print(f"[gancio] {site.get('name')} bad JSON: {exc}"); return 0
    if not isinstance(rows, list):
        print(f"[gancio] {site.get('name')}: unexpected payload {type(rows).__name__}"); return 0
    kept = 0
    for ev in rows:
        nev = to_event(ev, site)
        if nev:
            store.upsert(nev)
            kept += 1
    print(f"[gancio] {site.get('name')}: kept {kept}/{len(rows)} events")
    return kept


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import Gancio instances into the Mapsee store.")
    ap.add_argument("--config", required=True)
    ap.add_argument("--store", default="mapsee_events.json")
    ap.add_argument("--only", help="ingest just this site name (substring match)")
    a = ap.parse_args(argv)

    cfg = json.loads(open(a.config, encoding="utf-8").read())
    delay = float(cfg.get("crawl_delay", 2))
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept": "application/json"})
    store = EventStore(a.store)
    total = 0
    sites = [s for s in cfg.get("sites", [])
             if not a.only or a.only.lower() in str(s.get("name", "")).lower()]
    for i, site in enumerate(sites):
        try:
            total += ingest_site(store, session, site)
        except Exception as exc:
            print(f"[gancio] {site.get('name','?')} FAILED: {exc}")
        if delay and i < len(sites) - 1:
            time.sleep(delay)
    store.save()
    print(f"[gancio] done: +{total} events; store now holds {len(store.records)} unique events.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
