#!/usr/bin/env python3
"""
mapsee_ingest_jsonld.py — generic importer for venue / local-ticketing sites via
schema.org Event JSON-LD.

Most venue sites (Wix, Squarespace, many WordPress themes) and small ticketing
platforms embed a schema.org Event JSON-LD block on each event page for SEO —
name, startDate, location/address, image, offers. That makes "bring site X into
mapsee" a CONFIG entry, not a new adapter: point this at a listing page, give it
a regex for its event links, and every page with an Event block imports.

    python mapsee_ingest_jsonld.py --config jsonld_sources.json --store feeds_events.json

Config (jsonld_sources.json):
    { "sites": [
        { "name": "Sea Monster Lounge",
          "listing": ["https://www.seamonsterlounge.com/buy-tickets-in-advance"],
          "link_pattern": "\\"slug\\":\\"([a-zA-Z0-9-]+)\\"",
          "url_template": "/event-info/{}",
          "category": "music",
          "max_events": 100 },
        ... ] }
    link_pattern matches either literal hrefs (no url_template) or captures a
    fragment that url_template turns into a page URL — Wix pages, for example,
    embed the FULL event list as {"slug": ...} JSON while only rendering the
    first screenful as anchors. Non-event URLs a loose pattern sweeps up just
    404 or carry no Event block and are skipped.

Notes:
  • Tolerant JSON: some sites (Ticket Tomato) emit invalid \\' escapes in their
    JSON-LD — those are repaired before parsing.
  • Coordinates: location.geo when present; otherwise a street address is left
    for the sync's Census batch geocoder, and address-less venues fall back to
    one cached Photon lookup (same geocode_cache.json as the other feed adapters).
  • Politeness: identified UA, ~1s between event-page fetches, per-site cap.
"""
from __future__ import annotations

import argparse
import html as html_mod
import json
import os
import re
import sys
import time
from mapsee_geo_budget import geocode_allowed
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

from mapsee_ingest import NormalizedEvent, EventStore, make_fingerprint

UA = "Mozilla/5.0 (compatible; MapseeAggregator/1.0; +https://mapsee.me; events@mapsee.me)"

# ---- persistent geocode cache (shared with the other feed adapters) ----------
GEO_CACHE_PATH = os.environ.get("GEOCODE_CACHE", "geocode_cache.json")
try:
    _geo_cache: Dict[str, Any] = json.load(open(GEO_CACHE_PATH, encoding="utf-8"))
except Exception:
    _geo_cache = {}


def _save_geo_cache():
    try:
        json.dump(_geo_cache, open(GEO_CACHE_PATH, "w", encoding="utf-8"))
    except Exception:
        pass


def _geocode(session, query: str) -> Tuple[Optional[float], Optional[float]]:
    """One polite Photon lookup per unique query, cached forever."""
    key = "q:" + query.lower()
    if key in _geo_cache:
        v = _geo_cache[key]
        return (v[0], v[1]) if v else (None, None)
    if not geocode_allowed():                        # over this run's budget → retry next run
        return (None, None)
    try:
        r = session.get("https://photon.komoot.io/api/", params={"q": query, "limit": 1}, timeout=20)
        feats = (r.json() or {}).get("features") or []
        if feats:
            lon, lat = feats[0]["geometry"]["coordinates"][:2]
            _geo_cache[key] = [lat, lon]
            time.sleep(1.1)
            return lat, lon
    except Exception:
        pass
    _geo_cache[key] = None
    time.sleep(1.1)
    return None, None


# ---- JSON-LD extraction -------------------------------------------------------
_LD_RX = re.compile(r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", re.S | re.I)


def _parse_ld(block: str) -> Optional[Any]:
    s = block.strip()
    for attempt in (s, re.sub(r"\\'", "'", s)):          # repair the invalid \' escape
        try:
            return json.loads(attempt)
        except Exception:
            continue
    return None


def _iter_items(doc: Any):
    if isinstance(doc, list):
        for d in doc:
            yield from _iter_items(d)
    elif isinstance(doc, dict):
        if "@graph" in doc and isinstance(doc["@graph"], list):
            yield from _iter_items(doc["@graph"])
        else:
            yield doc


# Most schema.org Event subtypes are spelled "...Event" (MusicEvent, TheaterEvent,
# SportsEvent), so a suffix test caught them — but not all of them are, and the
# exceptions are exactly the listings worth having. A destination site marks its
# summer blowout up as `Festival`, and this dropped every one of them on the
# floor without a word. Same for the others below.
_EVENT_TYPES = {"festival", "hackathon", "courseinstance", "eventseries"}


def _is_event(item: Dict[str, Any]) -> bool:
    t = item.get("@type")
    types = t if isinstance(t, list) else [t]
    return any(isinstance(x, str)
               and (x.endswith("Event") or x.strip().lower() in _EVENT_TYPES)
               for x in types)


def _clean(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    s = html_mod.unescape(str(s))
    s = re.sub(r"<[^>]+>", " ", s)                        # descriptions sometimes carry HTML
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def _image_url(img: Any) -> Optional[str]:
    if isinstance(img, list):
        img = img[0] if img else None
    if isinstance(img, dict):
        return img.get("url")
    return img if isinstance(img, str) else None


# "2202 N 45th St, Seattle, WA 98103, USA" -> (street, city, region, zip)
_ADDR_RX = re.compile(r"^(.*?),\s*([^,]+?),\s*([A-Z]{2})\s*(\d{5})?(?:,\s*[^,]+)?$")


def _address_parts(loc: Dict[str, Any]) -> Dict[str, Optional[str]]:
    addr = loc.get("address")
    out = {"address": None, "city": None, "region": None, "postal_code": None, "country": None}
    if isinstance(addr, dict):
        out["address"] = _clean(addr.get("streetAddress"))
        out["city"] = _clean(addr.get("addressLocality"))
        out["region"] = _clean(addr.get("addressRegion"))
        out["postal_code"] = _clean(addr.get("postalCode"))
        c = addr.get("addressCountry")
        out["country"] = _clean(c.get("name") if isinstance(c, dict) else c)
    elif isinstance(addr, str):
        m = _ADDR_RX.match(addr.strip())
        if m:
            out["address"], out["city"], out["region"], out["postal_code"] = \
                _clean(m.group(1)), _clean(m.group(2)), m.group(3), m.group(4)
        else:
            out["address"] = _clean(addr)
    return out


def to_event(item: Dict[str, Any], page_url: str, category: str, session,
             venue_default: Optional[Dict[str, Any]] = None) -> Optional[NormalizedEvent]:
    if "OnlineEventAttendanceMode" in str(item.get("eventAttendanceMode") or ""):
        return None
    name = _clean(item.get("name"))
    start = (item.get("startDate") or "").strip()
    if not name or len(start) < 10:
        return None
    date_key = start[:10]
    if date_key < datetime.now(timezone.utc).strftime("%Y-%m-%d"):
        return None                                        # past — the sync would drop it anyway
    loc = item.get("location") or {}
    if isinstance(loc, list):
        loc = next((l for l in loc if isinstance(l, dict) and l.get("@type") != "VirtualLocation"), {})
    venue = _clean(loc.get("name"))
    parts = _address_parts(loc)
    geo = loc.get("geo") or {}
    lat = geo.get("latitude")
    lon = geo.get("longitude")
    # Single-venue sites (a music club's own calendar) routinely ship Events with
    # no location, or a bare street line with no city/state, or a non-standard
    # location key we can't read — every show is at the same address anyway. The
    # config's "venue" block FILLS those gaps (never overrides real data), so the
    # sync's Census pass can place them and the naive-time→UTC conversion knows
    # the timezone. Provide lat/lon there to skip geocoding entirely.
    if venue_default:
        venue = venue or _clean(venue_default.get("name"))
        for k in ("address", "city", "region", "postal_code", "country"):
            if not parts.get(k) and venue_default.get(k):
                parts[k] = venue_default[k]
        if lat is None and venue_default.get("lat") is not None:
            lat, lon = venue_default.get("lat"), venue_default.get("lon")
    if lat is None and not (parts["address"] and parts["city"]):
        # no coords and not enough address for the sync's Census pass → one cached Photon try
        q = ", ".join(x for x in (venue or parts["address"], parts["city"], parts["region"]) if x)
        if q:
            lat, lon = _geocode(session, q)
        if lat is None:
            return None                                    # nowhere to pin it
    performers = item.get("performer") or []
    if isinstance(performers, dict):
        performers = [performers]
    lineup = [p.get("name") for p in performers if isinstance(p, dict) and p.get("name")]
    offers = item.get("offers") or {}
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    ev = NormalizedEvent(
        source="jsonld",
        source_id=item.get("url") or page_url,
        name=name,
        description=_clean(item.get("description")),
        start_local=start,
        end_local=(item.get("endDate") or "").strip() or None,
        venue_name=venue,
        latitude=float(lat) if lat is not None else None,
        longitude=float(lon) if lon is not None else None,
        address=parts["address"], city=parts["city"], region=parts["region"],
        country=parts["country"], postal_code=parts["postal_code"],
        category=category,
        lineup=[_clean(x) for x in lineup if x],
        poster_image_url=_image_url(item.get("image")),
        ticket_url=(offers.get("url") if isinstance(offers, dict) else None) or item.get("url") or page_url,
    )
    ev.fingerprint = make_fingerprint(name, date_key, venue)
    return ev


def ingest_site(store: EventStore, session, site: Dict[str, Any]) -> int:
    name = site.get("name", "?")
    # link_pattern is optional: single-page venue sites (Wix etc.) embed every
    # Event block on the LISTING page itself, and their detail links often go
    # to bot-blocked ticketers (Tixr 403s non-browsers) - so we harvest the
    # listing's own JSON-LD below and only follow links when a pattern is set.
    pattern = re.compile(site["link_pattern"]) if site.get("link_pattern") else None
    tmpl = site.get("url_template")
    cap = int(site.get("max_events", 60))
    category = site.get("category", "community")
    venue_default = site.get("venue")   # fixed venue name/address/coords for single-venue sites
    urls: List[str] = []
    seen = set()
    kept = 0
    for listing in site.get("listing", []):
        try:
            r = session.get(listing, timeout=20)
            r.raise_for_status()
        except Exception as exc:
            print(f"[jsonld] {name} listing {listing} failed: {exc}")
            continue
        # harvest Event blocks embedded in the listing page itself
        for block in _LD_RX.findall(r.text):
            doc = _parse_ld(block)
            if doc is None:
                continue
            for item in _iter_items(doc):
                if not _is_event(item):
                    continue
                ev = to_event(item, listing, category, session, venue_default)
                if ev:
                    store.upsert(ev)
                    kept += 1
        for m in (pattern.finditer(r.text) if pattern else ()):
            frag = m.group(1) if (tmpl and m.groups()) else m.group(0)
            u = urljoin(listing, tmpl.format(frag) if tmpl else frag)
            if u not in seen:
                seen.add(u)
                urls.append(u)
        time.sleep(1.0)
    for u in urls[:cap]:
        try:
            r = session.get(u, timeout=20)
            r.raise_for_status()
        except Exception as exc:
            print(f"[jsonld] {name} {u} failed: {exc}")
            continue
        for block in _LD_RX.findall(r.text):
            doc = _parse_ld(block)
            if doc is None:
                continue
            for item in _iter_items(doc):
                if not _is_event(item):
                    continue
                ev = to_event(item, u, category, session, venue_default)
                if ev:
                    store.upsert(ev)
                    kept += 1
        time.sleep(1.0)
    print(f"[jsonld] {name}: kept {kept} events from {min(len(urls), cap)} pages")
    return kept


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import schema.org Event JSON-LD pages into the Mapsee store.")
    ap.add_argument("--config", required=True, help="JSON: {sites:[{name, listing:[], link_pattern, category, max_events}]}")
    ap.add_argument("--store", default="feeds_events.json")
    a = ap.parse_args(argv)

    cfg = json.loads(open(a.config, encoding="utf-8").read())
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.8"})
    store = EventStore(a.store)
    total = 0
    for site in cfg.get("sites", []):
        try:                                              # one site failing must not abort the sweep
            total += ingest_site(store, session, site)
        except Exception as exc:
            print(f"[jsonld] {site.get('name','?')} FAILED: {exc}")
    store.save()
    _save_geo_cache()
    print(f"[jsonld] done: +{total} events processed; store now holds {len(store.records)} unique events.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
