#!/usr/bin/env python3
"""
mapsee_ingest_squarespace.py - import any Squarespace Events collection through
the site's own `?format=json` view.

    python mapsee_ingest_squarespace.py --config squarespace_sources.json \
        --store feeds_events.json

One adapter, many sites, same as tribe/gancio/localist: every Squarespace site
answers `<collection-url>?format=json` with the JSON its own front end renders
from, so adding a source is a config line. Squarespace is where small arts
nonprofits, park conservancies, galleries and neighbourhood groups actually
live - exactly the long tail that has no ticketing API - and `server: Squarespace`
in the response headers is a one-request test for whether a candidate qualifies.

WHY NOT mapsee_ingest_jsonld.py, WHICH ALREADY READS THESE PAGES
----------------------------------------------------------------
Squarespace does emit a schema.org Event block per event page, so the JSON-LD
adapter would find these. It should not be used here, for two reasons measured
on volunteerparktrust.org:

    location: {"name": "", "address": "", "@type": "Place"}

The Event block's location is EMPTY on every event - the JSON-LD carries a
correct start time and nothing about where. So the JSON-LD route cannot place an
event at all, and every row would lean entirely on the config's venue block.
`?format=json` carries the real location where the organiser set one, plus the
excerpt, the image, the tags and the site's own categories, and it costs ONE
request for the whole collection instead of one per event.

THE DEFAULT-COORDINATE TRAP, WHICH IS THE WHOLE REASON THIS IS CAREFUL
-----------------------------------------------------------------------
Squarespace always ships mapLat/mapLng, even for an event whose location was
never filled in. An unset block comes back as:

    {"mapLat": 40.7207559, "mapLng": -74.0007613, "addressLine1": "", ...}

which is lower Manhattan - the editor's default map centre, not a venue. On
Volunteer Park Trust that is 17 of 22 upcoming events, so a reader that trusts
mapLat would drop seventeen Seattle events into New York. They would geocode
cleanly, sync cleanly, and be wrong, which is the worst kind of wrong this
pipeline can produce: silently plausible.

The defence is not a coordinate blocklist, because a real site could sit on that
pixel. It is that Squarespace cannot store a pin without an address behind it,
so A LOCATION WITH NO ADDRESS TEXT IS NOT A LOCATION - its coordinates are
discarded and the config's `venue` block fills the gap, the same contract
mapsee_ingest_jsonld.py uses for single-venue sites. The known default pair is
ALSO rejected outright, as a second line of defence for a site that types an
address but never moves the pin.

An event that ends up with neither real coordinates nor a `venue` fallback is
skipped LOUDLY, because an unplaceable event is worse than no event.

TIME. `startDate` is epoch milliseconds, a true UTC instant, and the collection
JSON carries the site's own zone at `website.timeZone`. Both are recorded, so
the sync never has to guess a zone from coordinates.
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

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

from mapsee_ingest import NormalizedEvent, EventStore, make_fingerprint, norm_categories

UA = "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)"

_TAG = re.compile(r"<[^>]+>")

# The Squarespace editor's default map centre, shipped verbatim whenever an
# event's location was never set. See the module docstring.
_DEFAULT_LATLON = (40.7207559, -74.0007613)
_DESC_MAX = 1200


def _clean(s: Optional[str], limit: Optional[int] = None) -> Optional[str]:
    if not s:
        return None
    s = html_mod.unescape(_TAG.sub(" ", str(s)))
    s = re.sub(r"\s+", " ", s).strip()
    if not s:
        return None
    if limit and len(s) > limit:
        s = s[:limit].rstrip() + "…"
    return s


def _f(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _times(ms: Any, tzname: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """(start_local, start_utc) from Squarespace's epoch-millisecond timestamp."""
    try:
        secs = int(ms) / 1000.0
    except (TypeError, ValueError):
        return None, None
    try:
        utc = datetime.fromtimestamp(secs, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None, None
    utc_s = utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    if tzname and ZoneInfo is not None:
        try:
            return utc.astimezone(ZoneInfo(tzname)).strftime("%Y-%m-%dT%H:%M:%S"), utc_s
        except Exception:
            pass
    # No usable zone: the UTC instant is still exact, so record it as both and
    # let the sync place it rather than inventing a local wall-clock time.
    return utc_s, utc_s


# "Seattle, WA, 98112" - Squarespace glues city/region/postal into addressLine2.
_ADDR2_RX = re.compile(r"^\s*([^,]+?)\s*,\s*([A-Za-z]{2})\s*(?:,\s*([\dA-Za-z\- ]+))?\s*$")


def _split_line2(line2: Optional[str]) -> Dict[str, Optional[str]]:
    out: Dict[str, Optional[str]] = {"city": None, "region": None, "postal_code": None}
    m = _ADDR2_RX.match(line2 or "")
    if m:
        out["city"] = _clean(m.group(1))
        out["region"] = m.group(2).upper()
        out["postal_code"] = _clean(m.group(3))
    elif line2:
        out["city"] = _clean(line2)
    return out


def has_real_address(loc: Dict[str, Any]) -> bool:
    """True when the organiser actually filled the location block in.

    This is the gate on trusting mapLat/mapLng at all - see the docstring.
    """
    return bool((loc.get("addressTitle") or "").strip()
                or (loc.get("addressLine1") or "").strip())


def _is_default_pin(lat: Optional[float], lon: Optional[float]) -> bool:
    if lat is None or lon is None:
        return False
    return (round(lat, 5), round(lon, 5)) == (round(_DEFAULT_LATLON[0], 5),
                                              round(_DEFAULT_LATLON[1], 5))


def resolve_place(loc: Dict[str, Any], venue_default: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """A trustworthy location for one event, or None if it cannot be placed."""
    loc = loc or {}
    real = has_real_address(loc)
    lat = lon = None
    parts: Dict[str, Optional[str]] = {"city": None, "region": None, "postal_code": None}
    venue = address = None
    if real:
        lat, lon = _f(loc.get("mapLat")), _f(loc.get("mapLng"))
        if _is_default_pin(lat, lon):
            lat = lon = None                     # address typed, pin never moved
        venue = _clean(loc.get("addressTitle"))
        address = _clean(loc.get("addressLine1"))
        parts = _split_line2(loc.get("addressLine2"))
    vd = venue_default or {}
    venue = venue or _clean(vd.get("name"))
    address = address or _clean(vd.get("address"))
    for k in ("city", "region", "postal_code"):
        if not parts.get(k) and vd.get(k):
            parts[k] = vd[k]
    country = _clean(loc.get("addressCountry")) or vd.get("country")
    if lat is None and vd.get("lat") is not None:
        lat, lon = _f(vd.get("lat")), _f(vd.get("lon"))
    # The sync geocodes street+city+region and DROPS anything with no street, so
    # coordinates or a real street line are the only two ways to survive it.
    if lat is None and not (address and parts.get("city")):
        return None
    return {"venue": venue, "address": address, "country": country,
            "lat": lat, "lon": lon, **parts}


def to_event(item: Dict[str, Any], site: Dict[str, Any], tzname: Optional[str],
             base: str) -> Optional[NormalizedEvent]:
    name = _clean(item.get("title"))
    start_local, start_utc = _times(item.get("startDate"), tzname)
    if not name or not start_utc:
        return None
    date_key = (start_local or start_utc)[:10]
    if date_key < datetime.now(timezone.utc).strftime("%Y-%m-%d"):
        return None                               # past - the sync would drop it anyway
    place = resolve_place(item.get("location") or {}, site.get("venue"))
    if not place:
        print(f"[squarespace] {site.get('name','?')}: unplaceable, skipped: {name!r} - "
              f"the event sets no address and the config has no `venue` fallback")
        return None
    end_local, end_utc = _times(item.get("endDate"), tzname)
    url = item.get("fullUrl") or ""
    if url and not url.startswith("http"):
        url = base.rstrip("/") + "/" + url.lstrip("/")
    primary = site.get("category", "community")
    # The site's own taxonomy, folded in where it happens to match mapsee's
    # vocabulary. norm_categories drops everything else, so a Squarespace tag
    # can never reach the database as a category nobody filters on.
    extras = norm_categories(primary, item.get("categories") or [], item.get("tags") or [])
    nev = NormalizedEvent(
        source="squarespace",
        source_id=str(item.get("id") or item.get("urlId") or url or name),
        name=name,
        description=_clean(item.get("excerpt") or item.get("body"), _DESC_MAX),
        start_local=start_local, start_utc=start_utc,
        end_local=end_local, end_utc=end_utc,
        timezone=tzname,
        venue_name=place["venue"],
        latitude=place["lat"], longitude=place["lon"],
        address=place["address"], city=place["city"], region=place["region"],
        country=place["country"], postal_code=place["postal_code"],
        category=primary,
        categories=extras,
        poster_image_url=item.get("assetUrl") or None,
        ticket_url=url or None,
    )
    nev.fingerprint = make_fingerprint(name, date_key, nev.venue_name, nev.city)
    return nev


def ingest_site(store: EventStore, session, site: Dict[str, Any]) -> int:
    name = site.get("name", "?")
    collection = (site.get("collection") or "").rstrip("/")
    if not collection:
        print(f"[squarespace] {name}: no collection URL")
        return 0
    base = re.sub(r"^(https?://[^/]+).*$", r"\1", collection)
    sep = "&" if "?" in collection else "?"
    try:
        r = session.get(f"{collection}{sep}format=json", timeout=45)
    except Exception as exc:
        print(f"[squarespace] {name} failed: {exc}")
        return 0
    if not (200 <= r.status_code < 300):
        print(f"[squarespace] {name} HTTP {r.status_code}")
        return 0
    try:
        doc = r.json()
    except Exception as exc:
        # A Squarespace site that has never had an Events collection at this URL
        # answers with the rendered page instead of JSON.
        print(f"[squarespace] {name}: not a JSON collection view ({exc})")
        return 0
    tzname = site.get("timezone") or ((doc.get("website") or {}).get("timeZone"))
    rows = doc.get("upcoming")
    if not rows:
        # Non-event collections (and some event templates) use `items`; filter
        # those to the ones that actually carry a start time.
        rows = [i for i in (doc.get("items") or []) if i.get("startDate")]
    if not rows:
        print(f"[squarespace] !! {name}: collection view carried no upcoming events "
              f"(type={(doc.get('collection') or {}).get('typeName')!r}). If the site "
              f"has events, the collection URL is probably wrong.")
        return 0
    kept = 0
    for item in rows[: int(site.get("max_events", 200))]:
        nev = to_event(item, site, tzname, base)
        if nev:
            store.upsert(nev)
            kept += 1
    print(f"[squarespace] {name}: kept {kept} of {len(rows)} upcoming (tz={tzname})")
    return kept


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import Squarespace Events collections into the Mapsee store.")
    ap.add_argument("--config", required=True)
    ap.add_argument("--store", default="mapsee_events.json")
    ap.add_argument("--only", help="ingest just this site name (substring match)")
    a = ap.parse_args(argv)

    cfg = json.loads(open(a.config, encoding="utf-8").read())
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept": "application/json"})
    store = EventStore(a.store)
    total = 0
    for site in cfg.get("sites", []):
        if a.only and a.only.lower() not in str(site.get("name", "")).lower():
            continue
        try:                                       # one site failing must not abort the sweep
            total += ingest_site(store, session, site)
        except Exception as exc:
            print(f"[squarespace] {site.get('name','?')} FAILED: {exc}")
        time.sleep(float(site.get("pause", 1.0)))
    store.save()
    print(f"[squarespace] done: +{total} events; store now holds {len(store.records)} unique events.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
