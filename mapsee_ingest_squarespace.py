#!/usr/bin/env python3
"""
mapsee_ingest_squarespace.py - import any Squarespace Events collection from the
collection page itself.

    python mapsee_ingest_squarespace.py --config squarespace_sources.json \
        --store feeds_events.json

One adapter, many sites, same as tribe/gancio/localist. Squarespace is where
small arts nonprofits, park conservancies, galleries and neighbourhood groups
actually live - exactly the long tail that has no ticketing API - and
`server: Squarespace` in the response headers is a one-request test for whether
a candidate qualifies.

WHY NOT `?format=json`, WHICH IS WHAT THIS USED TO READ
-------------------------------------------------------
Because every Squarespace site's robots.txt forbids it. The stock file - byte
for byte the same on volunteerparktrust.org and sfmamarkets.com - carries, under
the `User-agent: *` group that applies to us:

    Disallow:/*?format=json
    Disallow:/*&format=json

along with `?format=ical`, `?format=json-pretty` and the rest. It is not a
per-site choice a friendly organiser could waive; it ships with the platform, so
it governs every Squarespace source this adapter will ever have. The bare
collection page carries no such rule - only `/config`, `/search`, `/account`,
`/api/`, `/static/` and the query-string views are disallowed - so THAT is what
this reads, and it reads it once per site.

This is the same line the DICE adapter walks: the venue's public page yes, the
endpoint robots.txt names no. Getting the data a second way does not make the
first way allowed.

WHAT THE PAGE CARRIES, AND WHAT IT DOES NOT
--------------------------------------------
Measured on volunteerparktrust.org/events: 46 event articles, 16 of them
upcoming. Every one carries a Google Calendar export link with the exact instant
in UTC -

    dates=20260814T133000Z/20260814T143000Z

- which is better than the old route's epoch milliseconds needed to be, because
it is already unambiguous and it is the site's own arithmetic rather than ours.
Title, link, description and image are all in the markup.

What is NOT there is any coordinate. `mapLat` does not appear on the page at
all. So the default-coordinate trap that gave this adapter its original shape -
Squarespace shipping 40.7207559,-74.0007613, lower Manhattan, for every event
whose location was never filled in, which was 17 of 22 events here - is now
structurally impossible rather than merely defended against. `_is_default_pin`
and the rule below stay, because they cost nothing and the contract they encode
is the one the config still depends on.

A LOCATION WITH NO ADDRESS TEXT IS NOT A LOCATION. Six of the 46 articles carry
an address, as a Google Maps link whose query string holds the whole thing glued
together:

    ?q=1247 15th Avenue East Seattle, WA, 98112 United States

Note there is no comma between the street and the city: Squarespace joins
addressLine1, addressLine2 and the country with spaces, and the split is not
recoverable in general - "1247 15th Avenue East Seattle" could be a street in
Seattle or a street called "1247 15th Avenue" in "East Seattle". `_split_maplink`
therefore parses only the part that IS unambiguous (the `, ST, ZIP` tail) and
recovers the street only when the config's own `city` matches the end of the
head. When it cannot, it says so by returning no street at all, and the config's
`venue` block places the event - which is what happened for 40 of these 46
anyway, and what the block is for.

So a `venue` block is effectively REQUIRED per site now, not a fallback of last
resort. An event that ends up with neither is skipped LOUDLY, because an
unplaceable event is worse than no event.

PAGINATION. Squarespace paginates event lists with `?month=`, which robots.txt
also disallows, so this reads the single page it is given. That page leads with
the upcoming events, which is the half we want; a site with more upcoming events
than fit on one page will be short, and will say so in its kept/listed count.
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


# --- the page --------------------------------------------------------------
# One <article class="eventlist-event ..."> per event. `--upcoming` / `--past`
# is the template's own classification and is cheaper to trust than re-deriving
# it, but the date is re-checked anyway in to_event.
_ARTICLE = re.compile(r'<article[^>]*class="[^"]*eventlist-event[^"]*"[^>]*>.*?</article>', re.S)
_UPCOMING = re.compile(r'class="[^"]*eventlist-event--upcoming')
_TITLE = re.compile(r'class="eventlist-title-link"[^>]*>(.*?)</a>', re.S)
_HREF = re.compile(r'<a\s+href="([^"]+)"[^>]*class="eventlist-title-link"')
# The site's own UTC arithmetic, in the calendar-export link it renders for
# humans. Timed events give a full stamp; all-day events give bare dates.
_GCAL = re.compile(r'[?&]dates=(\d{8}T\d{6}Z)/(\d{8}T\d{6}Z)')
_GCAL_ALLDAY = re.compile(r'[?&]dates=(\d{8})/(\d{8})')
_ADDR_LI = re.compile(r'<li[^>]*eventlist-meta-address[^>]*>(.*?)</li>', re.S)
_MAPLINK = re.compile(r'maps\.google\.com\?q=([^"]+)')
_DESC = re.compile(r'<div class="eventlist-description">(.*)', re.S)
_IMG = re.compile(r'data-image="([^"]+)"')
_TZ = re.compile(r'"timeZone"\s*:\s*"([^"]+)"')

# "…, WA, 98112 United States" — the only part of a Squarespace map link that
# can be read without guessing. Two-letter region, then a postal code, then an
# optional country name.
_MAPLINK_TAIL = re.compile(
    r"^(?P<head>.+?),\s*(?P<region>[A-Za-z]{2}),\s*"
    r"(?P<postal>[0-9][0-9A-Za-z\- ]*?)"
    r"(?:\s+(?P<country>[A-Za-z][A-Za-z .]*))?$")


def _gcal_ms(article: str) -> Tuple[Optional[int], Optional[int]]:
    """(start, end) as epoch milliseconds from the calendar-export link.

    Kept in the same units the collection JSON used, so `_times` and everything
    downstream of it are unchanged by the move off `?format=json`.
    """
    m = _GCAL.search(article)
    if m:
        out = []
        for stamp in m.groups():
            dt = datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            out.append(int(dt.timestamp() * 1000))
        return out[0], out[1]
    m = _GCAL_ALLDAY.search(article)
    if m:
        # An all-day event: midnight UTC is a lie about the wall clock, but it is
        # the right DAY, which is all an all-day event claims. The sync shows the
        # date, not the minute.
        dt = datetime.strptime(m.group(1), "%Y%m%d").replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000), None
    return None, None


def _split_maplink(q: str, venue_default: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """A Squarespace map-link query string -> the location dict shape.

    Returns the same keys the collection JSON used, so `resolve_place` did not
    have to change. Emits `addressLine1` ONLY when the street can be separated
    from the city without guessing — see the module docstring. Everything else
    is left for the config's `venue` block, which is the honest answer when the
    source has glued two fields together.
    """
    out: Dict[str, Any] = {}
    q = _clean(q) or ""
    if not q:
        return out
    m = _MAPLINK_TAIL.match(q)
    if not m:
        # No parseable tail: could be an overseas address, could be a place name
        # somebody typed into the location field. Either way, not a street.
        return out
    head, region, postal = m.group("head").strip(), m.group("region"), m.group("postal")
    out["addressCountry"] = _clean(m.group("country"))
    city = _clean((venue_default or {}).get("city"))
    if city and head.lower().endswith(city.lower()):
        street = head[: -len(city)].strip().rstrip(",").strip()
        if street:
            out["addressLine1"] = street
            out["addressLine2"] = f"{city}, {region}, {postal}"
    return out


def parse_articles(html_text: str, venue_default: Optional[Dict[str, Any]],
                   upcoming_only: bool = True) -> List[Dict[str, Any]]:
    """Every event on the collection page, in the shape `to_event` expects."""
    items: List[Dict[str, Any]] = []
    for art in _ARTICLE.findall(html_text):
        if upcoming_only and not _UPCOMING.search(art):
            continue
        title = _clean((_TITLE.search(art) or [None, ""])[1] if _TITLE.search(art) else "")
        start_ms, end_ms = _gcal_ms(art)
        if not title or start_ms is None:
            continue
        loc: Dict[str, Any] = {}
        li = _ADDR_LI.search(art)
        if li:
            block = li.group(1)
            link = _MAPLINK.search(block)
            # The venue name is the text of the <li> with the "(map)" link
            # removed — Squarespace renders `addressTitle` and nothing else there.
            title_text = _clean(re.sub(r'<a\b.*?</a>', " ", block, flags=re.S))
            if title_text:
                loc["addressTitle"] = title_text
            if link:
                loc.update(_split_maplink(html_mod.unescape(link.group(1)), venue_default))
        href = _HREF.search(art)
        img = _IMG.search(art)
        desc = _DESC.search(art)
        items.append({"title": title, "startDate": start_ms, "endDate": end_ms,
                      "location": loc, "fullUrl": href.group(1) if href else None,
                      "assetUrl": img.group(1) if img else None,
                      "body": desc.group(1) if desc else None})
    return items


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
    # The bare collection page. NEVER add a query string here — `?format=json`,
    # `?format=ical` and `?month=` are all disallowed by the stock Squarespace
    # robots.txt. See the module docstring.
    try:
        r = session.get(collection, timeout=45)
    except Exception as exc:
        print(f"[squarespace] {name} failed: {exc}")
        return 0
    if not (200 <= r.status_code < 300):
        print(f"[squarespace] {name} HTTP {r.status_code}")
        return 0
    body = r.text
    tz_m = _TZ.search(body)
    tzname = site.get("timezone") or (tz_m.group(1) if tz_m else None)
    rows = parse_articles(body, site.get("venue"))
    if not rows:
        n_any = len(_ARTICLE.findall(body))
        print(f"[squarespace] !! {name}: no upcoming events parsed from the page "
              f"({n_any} event articles found in {len(body)} bytes). If the site has "
              f"events, either they are all past or this is not a collection page.")
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
    session.headers.update({"User-Agent": UA, "Accept": "text/html"})
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
