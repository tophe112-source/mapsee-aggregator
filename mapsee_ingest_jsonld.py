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
    Optional per site:
      venue       fixed name/address/city/region/postal_code/country/lat/lon for a
                  single-venue calendar. FILLS gaps, never overrides real data —
                  and on a site whose Event blocks carry only a placeholder
                  location it is the only thing that places the event at all.
      skip_title  regex (case-insensitive) matched against the event NAME, for the
                  non-events a venue posts to its own calendar: "CLOSED FOR
                  MAINTENANCE", "Closed for Private Event". Without it those reach
                  the map as ordinary listings.
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


# The LAST GATE before a committed file. Every _geocode in this repo now caches
# only a hit, but this is what makes that true regardless of which call site ran:
# a null in geocode_cache.json is permanent, and a coordless event is dropped at
# the sync, so one Photon timeout can retire a venue from the map for ever with
# nothing in any log to say so. Cheap, and self-healing for anything a previous
# version already wrote.
def _drop_nulls(cache):
    return {k: v for k, v in cache.items()
            if v is not None and isinstance(v, (list, tuple)) and len(v) >= 2 and v[0] is not None}


def _save_geo_cache():
    try:
        json.dump(_drop_nulls(_geo_cache), open(GEO_CACHE_PATH, "w", encoding="utf-8"))
    except Exception:
        pass


def _geocode(session, query: str) -> Tuple[Optional[float], Optional[float]]:
    """One polite Photon lookup per unique query. ONLY A HIT IS CACHED.

    geocode_cache.json is shared and COMMITTED, so a null written here on a
    transient Photon failure is permanent and no later run ever looks that venue
    up again — and an event with no coordinates is dropped at the sync, so the
    row vanishes with nothing saying why. Same rule as
    mapsee_ingest_markets._geocode, which got here first.
    """
    key = "q:" + query.lower()
    if key in _geo_cache:
        v = _geo_cache[key]
        return (v[0], v[1]) if v else (None, None)
    if not geocode_allowed():                        # over this run's budget → retry next run
        return (None, None)
    try:
        r = session.get("https://photon.komoot.io/api/", params={"q": query, "limit": 1}, timeout=20)
        r.raise_for_status()                         # a 502 is not an empty result
        feats = (r.json() or {}).get("features") or []
        if feats:
            lon, lat = feats[0]["geometry"]["coordinates"][:2]
            _geo_cache[key] = [lat, lon]
            time.sleep(1.1)
            return lat, lon
    except Exception:                                # noqa: BLE001
        pass
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


def _ld_get(item: Dict[str, Any], name: str) -> Any:
    """schema.org property names are lower-camelCase, and real emitters capitalise
    them anyway: WP Event Manager ships `Location` and `Organizer` on every event
    page it renders. `.get("location")` then quietly returns None, and the event is
    placed by the config's `venue` block INSTEAD of by what the page actually said.
    That happens to be the right pin — for exactly as long as the page keeps saying
    nothing. Read the key case-insensitively so we see what is there, and let the
    placeholder rule below decide whether it was worth anything."""
    if name in item:
        return item[name]
    want = name.lower()
    for k, v in item.items():
        if isinstance(k, str) and k.lower() == want:
            return v
    return None


# A CMS with an unfilled location does not omit it — it renders the placeholder its
# template uses. WP Event Manager writes {"name": "-", "address": "-"} into the
# JSON-LD of every event on a single-venue site, and "-" is TRUTHY: it survives
# `if not parts.get(k)` below, so the config's venue block never fills the gap, and
# "-" goes to the geocoder as a street. Same rule as the Squarespace default pin —
# a location with no address TEXT is not a location — and the same defence: fall
# back to the venue block rather than believe the field.
# "na" is deliberately NOT in this set: it is Namibia's ISO country code, and
# these values are matched against `country` as well as against street text.
_PLACEHOLDER = {"-", "--", "---", "n/a", "none", "null", "tba", "tbd",
                "to be announced", "to be determined", "unknown"}


def _meaningful(s: Optional[str]) -> Optional[str]:
    """A location string that carries no information, normalised to None."""
    if s is None:
        return None
    return None if str(s).strip().strip(".").lower() in _PLACEHOLDER else s


# "2202 N 45th St, Seattle, WA 98103, USA" -> (street, city, region, zip)
_ADDR_RX = re.compile(r"^(.*?),\s*([^,]+?),\s*([A-Z]{2})\s*(\d{5})?(?:,\s*[^,]+)?$")


def _address_parts(loc: Dict[str, Any]) -> Dict[str, Optional[str]]:
    addr = _ld_get(loc, "address")
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
    return {k: _meaningful(v) for k, v in out.items()}


def to_event(item: Dict[str, Any], page_url: str, category: str, session,
             venue_default: Optional[Dict[str, Any]] = None,
             skip_rx: Optional[re.Pattern] = None) -> Optional[NormalizedEvent]:
    if "OnlineEventAttendanceMode" in str(item.get("eventAttendanceMode") or ""):
        return None
    name = _clean(item.get("name"))
    start = (item.get("startDate") or "").strip()
    if not name or len(start) < 10:
        return None
    # A venue calendar carries entries that are not events: The Royal Room posts
    # "CLOSED FOR MAINTENANCE" and "Closed for Private Event" as event_listing
    # posts, because that is the only way its CMS can put a notice on the calendar.
    # They are well-formed Events with real dates — nothing downstream can tell
    # them from a gig — so they would reach the map as music, telling somebody a
    # shut venue is open. Checked here, before the geocode that would pay for one.
    if skip_rx and skip_rx.search(name):
        return None
    date_key = start[:10]
    if date_key < datetime.now(timezone.utc).strftime("%Y-%m-%d"):
        return None                                        # past — the sync would drop it anyway
    loc = _ld_get(item, "location") or {}
    if isinstance(loc, list):
        loc = next((l for l in loc if isinstance(l, dict) and l.get("@type") != "VirtualLocation"), {})
    venue = _meaningful(_clean(_ld_get(loc, "name")))
    parts = _address_parts(loc)
    geo = _ld_get(loc, "geo") or {}
    lat = geo.get("latitude")
    lon = geo.get("longitude")
    # Single-venue sites (a music club's own calendar) routinely ship Events with
    # no location, or a bare street line with no city/state, or a non-standard
    # location key we can't read — every show is at the same address anyway. The
    # config's "venue" block FILLS those gaps (never overrides real data), so the
    # sync's Census pass can place them and the naive-time→UTC conversion knows
    # the timezone. lat/lon there skips THIS adapter's Photon lookup; the sync's
    # Census pass still refines the pin from the address, as it does for every
    # event feed, so they are a good starting point and not the last word.
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
    # Non-events a venue posts to its own calendar (closure and private-hire
    # notices). Matched against the event NAME, which is what they actually mean;
    # the alternative tell — a 00:00 start — would also throw away a New Year show.
    skip_rx = re.compile(site["skip_title"], re.I) if site.get("skip_title") else None
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
                ev = to_event(item, listing, category, session, venue_default, skip_rx)
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
    if len(urls) > cap:
        print(f"[jsonld] {name}: {len(urls)} event links found but max_events={cap} "
              f"— NOT reading {len(urls) - cap}; raise max_events to cover the calendar")
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
                ev = to_event(item, u, category, session, venue_default, skip_rx)
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
