#!/usr/bin/env python3
"""
mapsee_ingest_mylisting.py - import a MyListing (27collective) directory's event
listings from the theme's own explore endpoint.

    python mapsee_ingest_mylisting.py --config mylisting_sources.json \
        --store feeds_events.json

MyListing is the WordPress directory theme that chambers of commerce, tourism
boards and "what's on in <town>" sites are built on - bainbridgeisland.com is
one, and the shape below is the theme's, not that site's, so the same adapter
takes the next one. `wp-content/themes/my-listing/assets/dist/explore.js` in a
page's markup is the one-request test for whether a candidate qualifies.

WHY THE EXPLORE ENDPOINT AND NOT THE PAGE
------------------------------------------
The /events/ page renders nothing: it boots a Vue app that immediately asks the
theme for its results. The endpoint is the theme's own, it is named by the page
itself in `CASE27.mylisting_ajax_url`, and it is what a browser loading the page
fetches - so this reads exactly what the site publishes to anyone who opens it.

Measured on bainbridgeisland.com/robots.txt: the disallowed paths are
`/wp-admin/` (with `/wp-admin/admin-ajax.php` explicitly re-Allowed),
`/wp-content/uploads/`, `/wp-content/plugins/`, `/readme.html`, `/refer/` and
`*.pdf$`. The theme's ajax url is `/?mylisting-ajax=1`, on the site root, and
`CASE27.ajax_url` - the other form some installs use - is the admin-ajax.php
that the same file re-Allows by name. Both are permitted. This adapter uses
whichever URL THE PAGE names and never invents one; if a site's robots.txt ever
disallows it, the honest answer is to drop the site, not to reach for the other.

WHAT ONE "RESULT" IS, AND THE TRAP IN IT
-----------------------------------------
It is one OCCURRENCE, not one listing. Measured on bainbridgeisland.com:
643 results across 9 pages, from only 92 distinct listings. A Thursday music
night is 50 of those 643.

Each result carries TWO dates and they are usually different:

    <div data-date="2026-08-28T18:00:00-07:00:::2026-08-28T20:00:00-07:00" …>   <- THIS occurrence
        …
        <span data-date="2026-08-21T18:00:00-07:00:::…" class="codicts-mlsre-date-manager">

The inner one is the listing's NEXT upcoming date, rendered identically into
every card the listing produces, so reading it gives you the same Friday
eighteen times and loses every other night of the run. 55 of the 80 cards on
one page disagreed between the two. The wrapper is the occurrence; take the
wrapper, and fall back to the inner span only when a site has no wrapper at all
(the recurring-dates plugin is not universal) - which yields one dated row per
listing rather than a plausible, wrong set.

Both are a local wall clock with a real UTC offset glued on, which is the site's
own arithmetic and unambiguous. The IANA zone NAME is not in the payload, so
`timezone` in the config supplies it; without one the offset still fixes the
instant exactly and only the zone label is missing.

THE HORIZON IS NOT OPTIONAL
----------------------------
An annual event with a recurrence rule and no end projects forever. Live counts
from bainbridgeisland.com: 423 occurrences in 2026, 184 in 2027, and then a
thin tail of one or two a year - "Grand Old Fourth of July", "Hometown
Halloween", "New Year's Day Polar Bear Plunge" - all the way to **2050**.
Ingesting those is not coverage, it is 25 years of pin litter that no cleanup
job will ever remove, because none of it is past. `horizon_days` (default 400)
is the guard, and the count it drops is printed rather than swallowed.

Long runs are a different thing and are kept: one show had 100 occurrences over
three months, which is a real theatre run. `mapsee_link_series.py` chains those
into one series after the sync and the product collapses a series to its next
occurrence, so the map shows one pin either way.

PLACEMENT
---------
Every card ships `data-locations` - a JSON array with the full address string
and Google's coordinates for it. All 643 carried one location with real address
text; none carried the theme's default map centre (47.624146,-122.518659 here),
so the Squarespace default-pin failure does not arise. `_split_address` still
refuses to guess: the address is Google's own comma-separated form, so the tail
(country, "Region ZIP", city) is read positionally and only the HEAD is treated
as street - with a leading non-numeric run split off as a venue name only when a
numbered street follows it. "Bainbridge Island Museum of Art, 550 Winslow Way E"
gives both; "Fort Ward Hill Road Northeast & Belfair Avenue East" is left whole,
because an intersection is a street even without a number.

The region arrives spelled out ("Washington"), and `_addr_parts` in
mapsee_supabase_sync feeds it to the US Census batch geocoder, which wants the
two-letter code - so `_STATE_CODES` maps it. A source coordinate is passed
through but NOT marked `coords_exact`: it is Google's geocode of the same
address string the sync will geocode itself, not a surveyed point, so the sync
stays authoritative exactly as it is for every other event feed.
"""
from __future__ import annotations

import argparse
import html as html_mod
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

from mapsee_ingest import NormalizedEvent, EventStore, make_fingerprint, norm_categories

UA = "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)"

_TAG = re.compile(r"<[^>]+>")
_DESC_MAX = 1200
DEFAULT_HORIZON_DAYS = 400
DEFAULT_PER_PAGE_GUARD = 40          # pages, not results — a runaway-loop backstop


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


# --- the page: where the endpoint and its nonce are named -------------------
_AJAX_URL_RX = re.compile(r'"mylisting_ajax_url"\s*:\s*"([^"]+)"')
_AJAX_FALLBACK_RX = re.compile(r'"ajax_url"\s*:\s*"([^"]+)"')
_NONCE_RX = re.compile(r'"ajax_nonce"\s*:\s*"([0-9a-zA-Z]+)"')
_THEME_RX = re.compile(r"/themes/my-listing/")


def read_bootstrap(html_text: str, page_url: str) -> Dict[str, Optional[str]]:
    """{'ajax_url', 'nonce'} from the explore page's own CASE27 config.

    Never guessed: a site that does not name an ajax url is not a MyListing
    explore page we understand, and inventing `/?mylisting-ajax=1` for it would
    be requesting a path nobody told us about.
    """
    base = re.sub(r"^(https?://[^/]+).*$", r"\1", page_url)
    raw = _AJAX_URL_RX.search(html_text) or _AJAX_FALLBACK_RX.search(html_text)
    url = None
    if raw:
        url = html_mod.unescape(raw.group(1)).replace("\\/", "/")
        if url.startswith("/"):
            url = base + url
    nonce = _NONCE_RX.search(html_text)
    return {"ajax_url": url, "nonce": nonce.group(1) if nonce else None,
            "is_mylisting": bool(_THEME_RX.search(html_text))}


# --- the results ------------------------------------------------------------
# One occurrence per wrapper. See the module docstring for why the wrapper's
# date is the one to read and the inner span's is not.
_CARD_SPLIT = re.compile(r'<div\s+data-date="(?P<d>[^"]*)"[^>]*class="[^"]*codicts-mlsre-date-wrap')
_ITEM_SPLIT = re.compile(r'<div\s+class="lf-item-container[^"]*"')
_INNER_DATE = re.compile(r'<span\s+data-date="([^"]*)"[^>]*codicts-mlsre-date-manager')
_LISTING_ID = re.compile(r'data-id="listing-id-(\d+)"')
_LOCATIONS = re.compile(r'data-locations="([^"]*)"')
_TITLE = re.compile(r'listing-preview-title"[^>]*>(.*?)</h4>', re.S)
_HREF = re.compile(r'<a\s+href="(https?://[^"#]+)"')
_IMG = re.compile(r"background-image:\s*url\('([^']+)'\)")

# "2026-08-28T18:00:00-07:00:::2026-08-28T20:00:00-07:00" — start:::end, either
# side an ISO local time with a real offset. The end half is absent on 31 of the
# 643 measured, so it is optional.
_STAMP = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})([+-]\d{2}:\d{2}|Z)?$")


def _parse_stamp(stamp: str) -> Tuple[Optional[str], Optional[str]]:
    """One half of a data-date -> (local ISO without offset, UTC ISO)."""
    m = _STAMP.match((stamp or "").strip())
    if not m:
        return None, None
    local, off = m.group(1), m.group(2)
    if not off:
        # No offset: the wall clock is all the site gave us. Recording it as both
        # is a lie about the instant, so leave the UTC side empty and let the
        # caller decide — to_event refuses an occurrence with no UTC.
        return local, None
    iso = local + ("+00:00" if off == "Z" else off)
    try:
        utc = datetime.fromisoformat(iso).astimezone(timezone.utc)
    except ValueError:
        return local, None
    return local, utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_data_date(value: str) -> Dict[str, Optional[str]]:
    parts = (value or "").split(":::")
    sl, su = _parse_stamp(parts[0] if parts else "")
    el, eu = _parse_stamp(parts[1]) if len(parts) > 1 else (None, None)
    return {"start_local": sl, "start_utc": su, "end_local": el, "end_utc": eu}


def parse_cards(html_text: str) -> List[Dict[str, Any]]:
    """Every occurrence in one get_listings response, newest markup first.

    Splits on the recurring-dates WRAPPER when the site has one (each wrapper is
    one occurrence) and on the listing card otherwise (one dated row per
    listing, from the card's own next-date span).
    """
    spans: List[Tuple[Optional[str], str]] = []
    hits = list(_CARD_SPLIT.finditer(html_text))
    if hits:
        for i, m in enumerate(hits):
            end = hits[i + 1].start() if i + 1 < len(hits) else len(html_text)
            spans.append((m.group("d"), html_text[m.end():end]))
    else:
        items = list(_ITEM_SPLIT.finditer(html_text))
        for i, m in enumerate(items):
            end = items[i + 1].start() if i + 1 < len(items) else len(html_text)
            chunk = html_text[m.start():end]
            inner = _INNER_DATE.search(chunk)
            spans.append((inner.group(1) if inner else None, chunk))

    out: List[Dict[str, Any]] = []
    for raw_date, chunk in spans:
        if raw_date is None:
            inner = _INNER_DATE.search(chunk)
            raw_date = inner.group(1) if inner else None
        if not raw_date:
            continue
        title = _clean((_TITLE.search(chunk).group(1) if _TITLE.search(chunk) else None))
        href = _HREF.search(chunk)
        lid = _LISTING_ID.search(chunk)
        img = _IMG.search(chunk)
        locs: List[Dict[str, Any]] = []
        lm = _LOCATIONS.search(chunk)
        if lm:
            try:
                parsed = json.loads(html_mod.unescape(lm.group(1)) or "[]")
                locs = [x for x in parsed if isinstance(x, dict)]
            except (json.JSONDecodeError, TypeError):
                locs = []
        if not title:
            continue
        row = {"title": title, "url": href.group(1) if href else None,
               "listing_id": lid.group(1) if lid else None,
               "image": img.group(1) if img else None,
               "location": locs[0] if locs else {}}
        row.update(parse_data_date(raw_date))
        out.append(row)
    return out


# --- the address ------------------------------------------------------------
_STATE_CODES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "district of columbia": "DC", "florida": "FL", "georgia": "GA", "hawaii": "HI",
    "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI",
    "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX",
    "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}
_HAS_NUMBER = re.compile(r"\d")
_COUNTRY_WORDS = {
    "united states", "usa", "us", "canada", "united kingdom", "uk", "ireland",
    "australia", "new zealand", "mexico", "france", "germany", "netherlands",
    "spain", "italy", "portugal", "japan", "singapore", "india", "south africa",
}


def _region_tail(part: str) -> Optional[Dict[str, Optional[str]]]:
    """'Washington 98110' / 'WA 98110' / 'Washington' -> {region, postal}, else None.

    The region is the leading run of word tokens carrying no digit and the
    postal is whatever follows. A part qualifies only when it HAS a postal or
    its region is a state we know, which is the whole point: "United States"
    and "Bainbridge Island" are both digitless single parts and neither is a
    region. Reading the tail by POSITION instead — pop the country, then pop the
    region — is what put `city="Washington 98110"` and `region="United States"`
    on six live events, because it assumed a street was always present.
    """
    toks = (part or "").split()
    if not toks:
        return None
    cut = next((i for i, t in enumerate(toks) if _HAS_NUMBER.search(t)), len(toks))
    region, postal = " ".join(toks[:cut]).strip(), " ".join(toks[cut:]).strip()
    if not region:
        return None
    known = region.lower() in _STATE_CODES or (len(region) == 2 and region.isalpha())
    if not (postal or known):
        return None
    return {"region": _region_code(region), "postal": postal or None}


def _region_code(region: Optional[str]) -> Optional[str]:
    """'Washington' -> 'WA'. The Census geocoder the sync uses wants the code."""
    r = (region or "").strip()
    if not r:
        return None
    if len(r) == 2 and r.isalpha():
        return r.upper()
    return _STATE_CODES.get(r.lower(), r)


def _split_address(raw: Optional[str]) -> Dict[str, Optional[str]]:
    """Google's comma-separated address string -> the fields the sync wants.

    Anchored on SHAPE, not on position. The "Region ZIP" part is located by
    `_region_tail` scanning from the right; everything after it is the country
    and everything before it is the head. Counting commas instead assumes a
    street is always present, and "Bainbridge Island, Washington 98110, United
    States" — a real address on six live events, with a city and no street —
    then lands as city="Washington 98110", region="United States".

    In the head, the last part is the city and the rest is the street. A head of
    exactly ONE part is genuinely ambiguous (the source dropped either the street
    or the city), and it is read as a CITY unless it carries a digit: a city name
    written into `address` would be geocoded as a street and pinned somewhere
    real and wrong, while a missing street just leaves the coordinates to place
    the event, which they already do.

    A street is split into venue + street ONLY when a leading digitless run is
    followed by a part carrying a digit. Otherwise it is one street written with
    a comma in it, and splitting it would invent a venue.
    """
    out: Dict[str, Optional[str]] = {"venue": None, "address": None, "city": None,
                                     "region": None, "postal_code": None, "country": None}
    parts = [p.strip() for p in (raw or "").split(",") if p.strip()]
    if not parts:
        return out
    tail_at = next((i for i in range(len(parts) - 1, -1, -1) if _region_tail(parts[i])), None)
    if tail_at is not None:
        tail = _region_tail(parts[tail_at]) or {}
        out["region"], out["postal_code"] = tail.get("region"), tail.get("postal")
        out["country"] = ", ".join(parts[tail_at + 1:]) or None
        head = parts[:tail_at]
    else:
        head = parts[:]
        if head and head[-1].lower() in _COUNTRY_WORDS:
            out["country"] = head.pop()
    if not head:
        return out
    if len(head) == 1:
        if _HAS_NUMBER.search(head[0]):
            out["address"] = head[0]
        else:
            out["city"] = head[0]
        return out
    out["city"] = head.pop()
    street_at = next((i for i, p in enumerate(head) if _HAS_NUMBER.search(p)), None)
    if street_at is None or street_at == 0:
        out["address"] = ", ".join(head)
    else:
        out["venue"] = ", ".join(head[:street_at])
        out["address"] = ", ".join(head[street_at:])
    return out


def resolve_place(loc: Dict[str, Any], site: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """A trustworthy location for one occurrence, or None if it cannot be placed."""
    parsed = _split_address((loc or {}).get("address"))
    for key, cfg in (("city", "default_city"), ("region", "default_region"),
                     ("country", "default_country")):
        if not parsed.get(key) and site.get(cfg):
            parsed[key] = _region_code(site[cfg]) if key == "region" else site[cfg]
    lat, lon = _f((loc or {}).get("lat")), _f((loc or {}).get("lng"))
    # The sync geocodes street+city+region and drops a row with no street, so a
    # coordinate or a real street line are the only two ways to survive it.
    if lat is None and not (parsed.get("address") and parsed.get("city")):
        return None
    parsed["lat"], parsed["lon"] = lat, lon
    return parsed


# --- the detail page: description only --------------------------------------
_LDJSON = re.compile(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', re.S)


def event_description(html_text: str) -> Optional[str]:
    """The listing's own description, from the Event node the theme emits.

    That node carries name/description/address/geo but NO startDate — the dates
    live in the recurrence UI — so this is the only thing worth a second
    request. It is what `derive_categories` in the sync reads to decide which
    lens the event reaches, which is why an adapter with no description files
    everything under the config's default.
    """
    for block in _LDJSON.findall(html_text or ""):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        nodes = data.get("@graph", [data]) if isinstance(data, dict) else data
        for node in nodes if isinstance(nodes, list) else [nodes]:
            if isinstance(node, dict) and node.get("@type") == "Event" and node.get("description"):
                return _clean(node["description"], _DESC_MAX)
    return None


# --- one occurrence ---------------------------------------------------------
def to_event(row: Dict[str, Any], site: Dict[str, Any], today: str,
             horizon: str, description: Optional[str] = None) -> Optional[NormalizedEvent]:
    name = _clean(row.get("title"))
    start_local, start_utc = row.get("start_local"), row.get("start_utc")
    if not name or not start_utc or not start_local:
        return None
    date_key = start_local[:10]
    # An event that STARTED before today but has not ended is still on. A
    # months-long registration window is the live example; judging it by its
    # start alone would drop it while it was still open.
    last_day = (row.get("end_local") or start_local)[:10]
    if last_day < today:
        return None
    if date_key > horizon:
        return None
    place = resolve_place(row.get("location") or {}, site)
    if not place:
        print(f"[mylisting] {site.get('name','?')}: unplaceable, skipped: {name!r} — "
              f"no address text and no default_city/default_region in the config")
        return None
    primary = site.get("category", "community")
    host = re.sub(r"^https?://", "", (site.get("explore_url") or "")).split("/")[0]
    nev = NormalizedEvent(
        source="mylisting",
        source_id=f"{host}|{row.get('listing_id') or name}|{start_local}",
        name=name,
        description=description,
        start_local=start_local, start_utc=start_utc,
        end_local=row.get("end_local"), end_utc=row.get("end_utc"),
        timezone=site.get("timezone"),
        venue_name=place.get("venue") or _clean(site.get("default_venue")),
        latitude=place.get("lat"), longitude=place.get("lon"),
        address=place.get("address"), city=place.get("city"),
        region=place.get("region"), country=place.get("country"),
        postal_code=place.get("postal_code"),
        category=primary,
        categories=norm_categories(primary, site.get("categories") or []),
        poster_image_url=row.get("image") or None,
        ticket_url=row.get("url") or None,
    )
    nev.fingerprint = make_fingerprint(name, date_key, nev.venue_name, nev.city)
    return nev


# --- one site ---------------------------------------------------------------
def fetch_page(session, ajax_url: str, nonce: Optional[str], listing_type: str,
               page: int, sort: str, timeout: float = 45.0) -> Dict[str, Any]:
    params = {
        "action": "get_listings",
        "listing_type": listing_type,
        "form_data[page]": str(page),          # 0-based; the site's own ?pg= is 1-based
        "form_data[preserve_page]": "true",
        "form_data[sort]": sort,
    }
    if nonce:
        params["security"] = nonce
    r = session.get(ajax_url, params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


def ingest_site(store: EventStore, session, site: Dict[str, Any]) -> int:
    name = site.get("name", "?")
    explore_url = (site.get("explore_url") or "").strip()
    if not explore_url:
        print(f"[mylisting] {name}: no explore_url")
        return 0
    try:
        r = session.get(explore_url, timeout=45)
        r.raise_for_status()
    except Exception as exc:
        print(f"[mylisting] {name} failed to load {explore_url}: {exc}")
        return 0
    boot = read_bootstrap(r.text, explore_url)
    if not boot["ajax_url"]:
        print(f"[mylisting] !! {name}: {explore_url} names no MyListing ajax url "
              f"(theme markup {'present' if boot['is_mylisting'] else 'absent'}). "
              f"Not a MyListing explore page — nothing requested.")
        return 0

    listing_type = site.get("listing_type", "event")
    sort = site.get("sort", "date")
    horizon_days = int(site.get("horizon_days", DEFAULT_HORIZON_DAYS))
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    horizon = (now + timedelta(days=horizon_days)).strftime("%Y-%m-%d")
    pause = float(site.get("pause", 1.0))

    rows: List[Dict[str, Any]] = []
    page, max_pages, found = 0, 1, None
    while page < min(max_pages, DEFAULT_PER_PAGE_GUARD):
        try:
            payload = fetch_page(session, boot["ajax_url"], boot["nonce"], listing_type, page, sort)
        except Exception as exc:
            print(f"[mylisting] {name} page {page} failed: {exc}")
            break
        if found is None:
            found = payload.get("found_posts")
            max_pages = int(payload.get("max_num_pages") or 1)
        rows.extend(parse_cards(payload.get("html") or ""))
        page += 1
        if page < max_pages:
            time.sleep(pause)

    if not rows:
        print(f"[mylisting] !! {name}: 0 occurrences parsed from {page} page(s) "
              f"(the endpoint reported {found} results). If the site has events, the "
              f"card markup has moved — check parse_cards against a live response.")
        return 0

    # Beyond the horizon is the loud one: it is the count that would otherwise
    # have put a 2050 pin on the map. See the module docstring.
    beyond = sum(1 for row in rows
                 if (row.get("start_local") or "")[:10] > horizon)
    descriptions: Dict[str, Optional[str]] = {}
    if site.get("fetch_details", True):
        wanted: List[str] = []
        for row in rows:
            url = row.get("url")
            if (url and url not in wanted
                    and today <= (row.get("start_local") or "")[:10] <= horizon):
                wanted.append(url)
        cap = int(site.get("max_details", 250))
        for url in wanted[:cap]:
            try:
                d = session.get(url, timeout=45)
                descriptions[url] = event_description(d.text) if d.ok else None
            except Exception:
                descriptions[url] = None     # a listing without prose is still an event
            time.sleep(pause)
        if len(wanted) > cap:
            print(f"[mylisting] {name}: {len(wanted) - cap} listing(s) over max_details={cap} "
                  f"keep their events but get no description (so no keyword promotion)")

    kept = 0
    for row in rows[: int(site.get("max_events", 2000))]:
        nev = to_event(row, site, today, horizon, descriptions.get(row.get("url")))
        if nev:
            store.upsert(nev)
            kept += 1
    print(f"[mylisting] {name}: kept {kept} of {len(rows)} occurrence(s) from "
          f"{len(set(r.get('listing_id') for r in rows))} listing(s) over {page} page(s); "
          f"dropped {beyond} beyond +{horizon_days}d ({horizon}); "
          f"{len([v for v in descriptions.values() if v])} description(s) read")
    return kept


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Import MyListing (27collective) directory events into the Mapsee store.")
    ap.add_argument("--config", required=True)
    ap.add_argument("--store", default="mapsee_events.json")
    ap.add_argument("--only", help="ingest just this site name (substring match)")
    a = ap.parse_args(argv)

    cfg = json.loads(open(a.config, encoding="utf-8").read())
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept": "text/html,application/json"})
    store = EventStore(a.store)
    total = 0
    for site in cfg.get("sites", []):
        if a.only and a.only.lower() not in str(site.get("name", "")).lower():
            continue
        try:                                   # one site failing must not abort the sweep
            total += ingest_site(store, session, site)
        except Exception as exc:
            print(f"[mylisting] {site.get('name','?')} FAILED: {exc}")
        time.sleep(float(site.get("pause", 1.0)))
    store.save()
    print(f"[mylisting] done: +{total} events; store now holds {len(store.records)} unique events.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
