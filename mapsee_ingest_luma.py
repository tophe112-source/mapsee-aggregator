#!/usr/bin/env python3
"""
mapsee_ingest_luma.py - import public Luma (luma.com, formerly lu.ma) events,
either a whole calendar or a whole city's Discover feed.

    python mapsee_ingest_luma.py --config luma_sources.json --store feeds_events.json

Luma is where the tech/AI/startup/creative-meetup layer of a city organises, and
almost none of it reaches Eventbrite or Meetup. It carries real coordinates and a
parsed address on every listing, so events land on the map without a geocoder
round trip - the only adapter here besides tribe that can say that.

TWO WAYS IN, ONE EVENT SHAPE
----------------------------
    calendars: one organiser's calendar        /calendar/get-items
    places:    a whole city's Discover feed    /discover/get-paginated-events

Both answer {entries:[{event:{...}}], has_more, next_cursor} with the same event
object, so `to_event` is shared. `url` in the config can be any luma.com slug -
a calendar, a city, or even a single event page - and the adapter resolves it
from the page's __NEXT_DATA__:

    kind = "calendar"       -> data.calendar.api_id       (that calendar)
    kind = "event"          -> data.event.calendar_api_id (the calendar it is ON)
    kind = "discover-place" -> data.place.api_id          (that city)

An event URL therefore pulls in the whole calendar behind it, which is what you
almost always want: one ClawCon event page is a pointer to the ClawCon calendar.
Set `calendar_api_id` / `discover_place_api_id` explicitly to skip that lookup.

THE PARAMETER NAME IS A TRAP AND IT FAILS SILENTLY
---------------------------------------------------
Discover is `discover_place_api_id`. The obvious `place_api_id` - which is what
the id is called everywhere else in Luma's own payloads, including the field on
the place object - is ACCEPTED, IGNORED, and answered with a 200 and a full page
of events for whatever city the caller's IP is in. Asking for Seattle from a
runner in AWS us-east-2 returns Columbus, Ohio, with no error and no clue in the
response. That would quietly file another city's events under the one you
configured, so `place_api_id` must never be sent, and `verify_place` below
re-reads the place from luma.com and checks the name it got back.

POLITENESS. api.lu.ma/robots.txt disallows only /insights/; the calendar, ICS
and discover routes are not restricted. These are undocumented internal
endpoints all the same, so this pages in modest batches with a delay between
requests and a hard page cap, and treats any non-200 as "stop, not retry".
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

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
# The web pages 403 a non-browser agent; the API routes do not care. Reading a
# public page to learn a public id is the minimum needed to resolve a slug.
_BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
API = "https://api.lu.ma"
WEB = "https://luma.com"

_NEXT_DATA = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)
# "2205 7th Ave, Seattle, WA 98121, USA" -> 98121. Anchored on the state code
# because a bare five-digit search finds the STREET NUMBER first: "15600 NE 8th
# St, Bellevue, WA 98007" yielded 15600, a postal code in Pennsylvania.
_POSTAL_RX = re.compile(r"\b[A-Z]{2}\s+(\d{5})(?:-\d{4})?\b")


def _clean(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    s = re.sub(r"\s+", " ", str(s)).strip()
    return s or None


def _local(iso_utc: Optional[str], tzname: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """(local, utc) from Luma's '2026-08-12T00:30:00.000Z' plus the event's zone."""
    if not iso_utc:
        return None, None
    try:
        dt = datetime.fromisoformat(str(iso_utc).replace("Z", "+00:00"))
    except ValueError:
        return None, None
    utc = dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if tzname and ZoneInfo is not None:
        try:
            return dt.astimezone(ZoneInfo(tzname)).strftime("%Y-%m-%dT%H:%M:%S"), utc
        except Exception:
            pass
    return utc, utc


def resolve(session, slug_or_url: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """(kind, api_id, name) for any luma.com slug - calendar, event or city."""
    slug = str(slug_or_url or "").strip().rstrip("/")
    slug = re.sub(r"^https?://(?:www\.)?(?:luma\.com|lu\.ma)/", "", slug)
    if not slug:
        return None, None, None
    r = session.get(f"{WEB}/{slug}", timeout=30,
                    headers={"Accept": "text/html", "User-Agent": _BROWSER_UA})
    if r.status_code != 200:
        print(f"[luma] resolve {slug}: HTTP {r.status_code}")
        return None, None, None
    m = _NEXT_DATA.search(r.text)
    if not m:
        print(f"[luma] resolve {slug}: no __NEXT_DATA__ on the page")
        return None, None, None
    try:
        init = json.loads(m.group(1))["props"]["pageProps"]["initialData"]
    except Exception as exc:
        print(f"[luma] resolve {slug}: unexpected page shape ({exc})")
        return None, None, None
    kind, data = init.get("kind"), (init.get("data") or {})
    if kind == "calendar":
        cal = data.get("calendar") or {}
        return "calendar", cal.get("api_id"), cal.get("name")
    if kind == "event":
        # An event page points at the calendar that owns it; ingesting that
        # calendar is the useful reading of "add this Luma link".
        ev = data.get("event") or {}
        cal = data.get("calendar") or {}
        return "calendar", ev.get("calendar_api_id") or cal.get("api_id"), cal.get("name") or ev.get("name")
    if kind == "discover-place":
        p = data.get("place") or {}
        return "place", p.get("api_id"), p.get("name")
    print(f"[luma] resolve {slug}: unsupported page kind {kind!r}")
    return None, None, None


def _page(session, path: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        r = session.get(f"{API}/{path}", params=params, timeout=40)
    except Exception as exc:
        print(f"[luma] {path} failed: {exc}")
        return None
    if r.status_code != 200:
        print(f"[luma] {path} HTTP {r.status_code}")
        return None
    try:
        return r.json()
    except Exception as exc:
        print(f"[luma] {path} bad JSON: {exc}")
        return None


def iter_entries(session, kind: str, api_id: str, limit: int, max_pages: int,
                 pause: float) -> Iterable[Dict[str, Any]]:
    """Walk either feed, yielding raw event dicts."""
    if kind == "calendar":
        path, key = "calendar/get-items", "calendar_api_id"
        base = {key: api_id, "period": "future"}
    else:
        # NOT place_api_id - see the module docstring.
        path, key = "discover/get-paginated-events", "discover_place_api_id"
        base = {key: api_id}
    cursor = None
    for _ in range(max_pages):
        params = dict(base, pagination_limit=limit)
        if cursor:
            params["pagination_cursor"] = cursor
        body = _page(session, path, params)
        if not body:
            return
        entries = body.get("entries") or []
        if not entries:
            return
        for e in entries:
            ev = e.get("event") if isinstance(e, dict) else None
            if isinstance(ev, dict):
                yield ev
        if not body.get("has_more"):
            return
        cursor = body.get("next_cursor")
        if not cursor:
            return
        time.sleep(pause)


def to_event(ev: Dict[str, Any], src: Dict[str, Any]) -> Optional[NormalizedEvent]:
    if ev.get("visibility") not in (None, "public"):
        return None
    if str(ev.get("location_type") or "").lower() == "online":
        return None                                # nothing to put on a map
    name = _clean(ev.get("name"))
    tzname = ev.get("timezone") or None
    start_local, start_utc = _local(ev.get("start_at"), tzname)
    if not name or not start_utc:
        return None
    date_key = (start_local or start_utc)[:10]
    if date_key < datetime.now(timezone.utc).strftime("%Y-%m-%d"):
        return None
    coord = ev.get("coordinate") or {}
    lat, lon = coord.get("latitude"), coord.get("longitude")
    g = ev.get("geo_address_info") or {}
    if lat is None or lon is None:
        # Hosts who hide the address until you register still publish an
        # approximate coordinate; one with neither cannot be placed.
        print(f"[luma] {src.get('name','?')}: no coordinates, skipped: {name!r}")
        return None
    # `address` is whichever of the two the host typed - "1275 Kinnear Rd" or
    # "Dragonfly Bookshop". A leading digit is the only reliable tell, and
    # getting it wrong costs a label, not a location, since the coordinates are
    # already exact. geo_address_info.description is a free-text note from the
    # host ("This is an outdoor, open-air event!"), never a venue name.
    raw_addr = _clean(g.get("address"))
    street = raw_addr if (raw_addr and re.match(r"^\d", raw_addr)) else None
    venue = None if street else raw_addr
    full = g.get("full_address") or ""
    pm = _POSTAL_RX.search(full)
    slug = _clean(ev.get("url"))
    end_local, end_utc = _local(ev.get("end_at"), tzname)
    primary = src.get("category", "community")
    nev = NormalizedEvent(
        source="luma",
        source_id=str(ev.get("api_id") or slug or name),
        name=name,
        start_local=start_local, start_utc=start_utc,
        end_local=end_local, end_utc=end_utc,
        timezone=tzname,
        venue_name=venue,
        latitude=float(lat), longitude=float(lon),
        address=street,
        city=_clean(g.get("city")) or src.get("default_city"),
        region=_clean(g.get("region_short") or g.get("region")) or src.get("default_region"),
        country=_clean(g.get("country_code") or g.get("country")),
        postal_code=pm.group(1) if pm else None,
        category=primary,
        categories=norm_categories(primary, src.get("categories") or []),
        poster_image_url=ev.get("cover_url") or ev.get("social_image_url") or None,
        ticket_url=f"{WEB}/{slug}" if slug else None,
    )
    nev.fingerprint = make_fingerprint(name, date_key, venue, nev.city)
    return nev


def verify_place(session, api_id: str, expected: Optional[str]) -> bool:
    """Guard against the silent geo-IP fallback described in the docstring."""
    if not expected:
        return True
    body = _page(session, "discover/get-paginated-events",
                 {"discover_place_api_id": api_id, "pagination_limit": 1})
    if not body:
        return False
    entries = body.get("entries") or []
    if not entries:
        return True                                # empty city, nothing to check
    g = (entries[0].get("event") or {}).get("geo_address_info") or {}
    got_region = (g.get("region_short") or g.get("region") or "").strip()
    want_region = str(expected).strip()
    if want_region and got_region and got_region.lower() != want_region.lower():
        print(f"[luma] !! place {api_id} returned events in {got_region!r} but the config "
              f"expects {want_region!r}. Refusing to ingest - this is the geo-IP fallback, "
              f"NOT an empty city. Check discover_place_api_id.")
        return False
    return True


def ingest(store: EventStore, session, src: Dict[str, Any], kind_hint: str) -> int:
    name = src.get("name", "?")
    api_id = src.get("calendar_api_id") or src.get("discover_place_api_id")
    kind = kind_hint
    if not api_id:
        kind, api_id, resolved = resolve(session, src.get("url") or src.get("slug") or "")
        if not api_id:
            print(f"[luma] {name}: could not resolve a Luma id")
            return 0
        print(f"[luma] {name}: resolved to {kind} {api_id} ({resolved})")
    if kind == "place" and not verify_place(session, api_id, src.get("expect_region")):
        return 0
    kept = 0
    cap = int(src.get("max_events", 300))
    for ev in iter_entries(session, kind, api_id,
                           limit=int(src.get("per_page", 50)),
                           max_pages=int(src.get("max_pages", 10)),
                           pause=float(src.get("pause", 1.0))):
        if kept >= cap:
            break
        nev = to_event(ev, src)
        if nev:
            store.upsert(nev)
            kept += 1
    print(f"[luma] {name}: kept {kept} events")
    return kept


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import public Luma calendars and city feeds into the Mapsee store.")
    ap.add_argument("--config", required=True)
    ap.add_argument("--store", default="mapsee_events.json")
    ap.add_argument("--only", help="ingest just this source name (substring match)")
    a = ap.parse_args(argv)

    cfg = json.loads(open(a.config, encoding="utf-8").read())
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept": "application/json"})
    store = EventStore(a.store)
    total = 0
    for kind, key in (("calendar", "calendars"), ("place", "places")):
        for src in cfg.get(key) or []:
            if a.only and a.only.lower() not in str(src.get("name", "")).lower():
                continue
            try:                                   # one source failing must not abort the sweep
                total += ingest(store, session, src, kind)
            except Exception as exc:
                print(f"[luma] {src.get('name','?')} FAILED: {exc}")
            time.sleep(float(src.get("pause", 1.0)))
    store.save()
    print(f"[luma] done: +{total} events; store now holds {len(store.records)} unique events.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
