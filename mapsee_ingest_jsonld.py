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
  • Modern Events Calendar (measured 2026-10-04; see the notes above
    _mec_unset_price and _mec_wall_clock). Its "price": "0" is an EMPTY cost
    field, not a free event, so on a page carrying MEC's markup a bare zero is
    dropped before the admission reader sees it. Its timed startDate/endDate is
    the local wall clock printed as if it were UTC, so it is read back as that
    wall clock and the sync localises it from the venue.
  • One url, several dates: when a page gives the same url to more than one
    start, each occurrence is keyed url#date (see _shared_urls). Otherwise the
    store folds every occurrence of a weekly session into one row.
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
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

from mapsee_ingest import NormalizedEvent, EventStore, make_fingerprint
from mapsee_admission import admission_description, normalize_admission_facts

UA = "Mozilla/5.0 (compatible; MapseeAggregator/1.0; +https://mapsee.me; events@mapsee.me)"
CURSOR_PATH = os.environ.get("JSONLD_CURSOR", "jsonld_cursor.json")


class BudgetExpired(Exception):
    def __init__(self, offset, inline_offset=0):
        self.offset = offset
        self.inline_offset = inline_offset


def _site_key(site):
    return json.dumps([site.get("name"), site.get("listing", [])], sort_keys=True)


def _load_cursor(path):
    try:
        cursor = json.load(open(path, encoding="utf-8"))
        if isinstance(cursor, dict) and isinstance(cursor.get("source"), str) \
                and isinstance(cursor.get("offset", 0), int) and cursor.get("offset", 0) >= 0 \
                and isinstance(cursor.get("inline_offset", 0), int) and cursor.get("inline_offset", 0) >= 0:
            return cursor
    except (OSError, ValueError):
        pass
    return {}


def _save_cursor(path, source, offset=0, inline_offset=0):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"source": source, "offset": offset, "inline_offset": inline_offset}, fh)
    os.replace(tmp, path)

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
    """Parse one JSON-LD block, repairing the two ways real CMSes break it.

    A RAW CONTROL CHARACTER inside a string is the common one and it is
    expensive: a CMS drops the description straight into the block with its
    literal newlines, and strict JSON refuses the whole document. The Royal
    Lyceum's programme is 40 well-formed Event blocks behind exactly that, and
    every one was being discarded — while a REGEX looking for the same blocks
    saw them fine, which is how discovery came to propose pages the ingester
    could not read. `strict=False` accepts them; nothing else about the parse
    changes.
    """
    s = block.strip()
    for attempt in (s, re.sub(r"\\'", "'", s)):          # repair the invalid \' escape
        for strict in (True, False):                      # ...and raw control chars
            try:
                return json.loads(attempt, strict=strict)
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


# MODERN EVENTS CALENDAR WRITES "price": "0" FOR A COST NOBODY ENTERED. Its schema
# block copies the event's cost field into offers.price. The Lite source
# (features/schema.php) writes "" for an empty field, which already reads as
# unknown; the Pro build (7.36 at The Florrie and Whitehorn) writes "0". Measured
# 2026-10-04 on 26 MEC calendars, 20 drawn at random from the 107 configured plus
# 6 proposed: 39 of 39 event pages whose block says "0" print NO cost on the page,
# while all 8 with a non-zero or text price print MEC's "Cost" line ("£5.00"
# beside "5", "kostenfrei" beside "kostenfrei"). On the 20 configured listing
# pages, 188 of 388 upcoming events (224 of 455 listed; 11 of the 19 sites with
# Event blocks) carry the "0", and normalize_admission_facts read every one as an
# explicit free offer: "Free to attend." led the description, 0227's offer
# tagger reads that as offer:free (the /c/<city>/free page), and source_details
# {free: true, offer.price "0"} becomes isAccessibleForFree on /e/<id>. Among
# the 6 proposed sites it was 110 of 131 rows in 90 days, Whitehorn's "$10
# drop-ins" yoga included.
#
# So on a page with MEC's markup a bare numeric zero is removed and the event is
# left saying nothing about price, as source_details {} rather than None: that
# is what makes the sync clear the {free: true} an earlier run stored (to_event
# says how). A paid cost the organiser typed ("5") stays a paid offer. TYPED TEXT stays exactly as written, and the admission reader reads
# it as UNKNOWN, not free: normalize_admission_facts takes only a number from an
# Offer, so "Free", "kostenfrei" and "Gratis" give no facts (checked 2026-10-04,
# before and after this change). A description that says "free" / "gratuit" /
# "kostenlos" still reaches 0227's text tagger untouched (50 of the 224 listed
# carry such a word).
#
# MEC's ONE explicit free marker is kept. Its single-event template prints the
# Cost line (<dd class="mec-events-event-cost">) only `if($cost)`, which PHP
# makes false for "" and "0", and renders a numeric cost through render_price(),
# which returns its translated "Free" for a zero. So a Cost line with no non-zero
# digit beside a zero price is the organiser typing a zero ("0.00") and MEC
# calling it free on the page: on an event page holding exactly one Event, that
# zero is kept (_mec_cost_says_free). 0 of the 60 zero-price event pages sampled
# (39, then 21 in review) show it, so today this keeps nothing; it is here so a
# real one is not dropped. The page fingerprint is the one
# catalog_discover_osm.py proposed these sites with.
_MEC_PAGE_RX = re.compile(r"modern-events-calendar|mec-event", re.I)
_ZERO_PRICE_RX = re.compile(r"0+(?:\.0+)?")
_MEC_COST_RX = re.compile(
    r"<dd[^>]*\bclass=[\"'][^\"']*\bmec-events-event-cost\b[^\"']*[\"'][^>]*>(.*?)</dd>",
    re.S | re.I)


def _mec_cost_says_free(page: str) -> bool:
    """MEC printed its own Cost line, and it holds no price above zero."""
    m = _MEC_COST_RX.search(page or "")
    label = _clean(m.group(1)) if m else None
    return bool(label) and not re.search(r"[1-9]", label)


def _mec_unset_price(item: Dict[str, Any]) -> Dict[str, Any]:
    """The same Event with any Offer whose price is a bare zero stripped of it."""
    def unset(offer):
        price = offer.get("price") if isinstance(offer, dict) else None
        return (isinstance(price, (int, float, str)) and not isinstance(price, bool)
                and _ZERO_PRICE_RX.fullmatch(str(price).strip()) is not None)

    def without(offer):
        return {k: v for k, v in offer.items() if k != "price"} if unset(offer) else offer

    offers = item.get("offers")
    if isinstance(offers, dict) and unset(offers):
        return dict(item, offers=without(offers))
    if isinstance(offers, list) and any(unset(o) for o in offers):
        return dict(item, offers=[without(o) for o in offers])
    return item


# MODERN EVENTS CALENDAR PRINTS THE WALL CLOCK AS IF IT WERE UTC. MEC keeps an
# occurrence as a timestamp that is the venue's wall clock read as UTC; its own
# links show it (Haus Steinstraße's "?occurrence=2026-10-09&time=1791576000" is
# 2026-10-09 20:00Z for a 20:00 show), and the schema block prints that instant
# in the site's offset: "2026-10-09T22:00:00+02:00". Read as written, every timed
# MEC row is late by the venue's UTC offset, which is 1-2 h in Europe and 6 h in
# Calgary. Measured 2026-10-04 on 15 event pages on 9 configured or proposed MEC
# sites, page time against block: 14 off by exactly the site's offset (Whitehorn
# prints "6:45 pm" beside "12:45:00-06:00"; Ateliertheater prints 19:30 beside
# 21:30+02:00 on 21 Oct and 20:30+01:00 on 3 Nov, so the error follows DST;
# Basler Papiermühle prints 14:00 beside 14:00+00:00, a site set to UTC, so
# Basel saw it 2 h late), and the 15th agreed only because the UK was at +00:00.
# Timed starts are 80 of the 388 upcoming events on the 20 sampled listings; the
# rest are MEC Lite's bare dates, which pass through untouched.
#
# So on a page with MEC's markup an offset-bearing stamp is turned into that UTC
# wall clock and handed on NAIVE, and the sync localises it from the venue's
# coordinates (_to_utc_if_naive), exactly as for the WP Event Manager stamps in
# this file's test. It is done before to_event, because the date can change and
# the date is part of the fingerprint: a Berlin Dark (Barcelona) club night whose
# url says "?occurrence=2026-10-23" is "2026-10-24T00:00:00+02:00" in its block,
# and only the wall clock, 2026-10-23 22:00, puts it on its own night. If MEC
# ever prints honest offsets this shifts every timed MEC row by the offset; the
# 15 pairs above are the check.
_STAMP_RX = re.compile(
    r"(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})(?::(\d{2}))?(?:\.\d+)?\s*(Z|[+-]\d{2}:?\d{2})", re.I)


def _mec_wall_clock(stamp: Any) -> Any:
    """MEC's offset-bearing stamp as the naive wall clock it stands for."""
    m = _STAMP_RX.fullmatch(stamp.strip()) if isinstance(stamp, str) else None
    if not m:
        return stamp                                   # a bare date, a naive stamp, "" or junk
    try:
        wall = datetime.fromisoformat(f"{m[1]}T{m[2]}:{m[3] or '00'}")
    except ValueError:
        return stamp
    off = m[4].upper().replace(":", "")
    minutes = 0 if off == "Z" else (int(off[1:3]) * 60 + int(off[3:5])) * (-1 if off[0] == "-" else 1)
    return (wall - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%S")


def _from_mec(item: Dict[str, Any], keep_zero: bool = False) -> Tuple[Dict[str, Any], bool]:
    """A MEC Event as its organiser meant it, and whether a price was withdrawn."""
    priced = item if keep_zero else _mec_unset_price(item)
    times = {k: _mec_wall_clock(priced.get(k)) for k in ("startDate", "endDate")}
    moved = {k: v for k, v in times.items() if v != priced.get(k)}
    return (dict(priced, **moved) if moved else priced), priced is not item


# ONE URL, SEVERAL DATES. EventStore.upsert looks a row up by (source,
# source_id) BEFORE the fingerprint, and source_id is the Event's url. Some
# calendars give every occurrence of a weekly session the same url and no
# ?occurrence= (The Florrie: /events/yoga/ for every Tuesday), and an Event with
# no url at all takes the page's. Each later date then found the first row,
# re-keyed it to its own fingerprint and kept the FIRST date's start_local, so
# the row's id and its time disagreed and moved as the horizon rolled on. Live
# 2026-10-04: The Florrie printed "kept 91 events" into a store that held 21;
# 127 of the 417 upcoming inline Event blocks on the 20 sampled MEC listings (9
# sites) share an id with another date.
# Such an id gets "#<date>" appended, but only when the SAME page shows it with
# more than one date, so every other row keeps the id it had. The fingerprint,
# which is the database's external_id, does not involve source_id at all.
def _shared_urls(items: List[Dict[str, Any]], page_url: str) -> set:
    dates: Dict[str, set] = {}
    for item in items:
        start, uid = item.get("startDate"), item.get("url") or page_url
        if isinstance(start, str) and len(start.strip()) >= 10 and isinstance(uid, str):
            dates.setdefault(uid, set()).add(start.strip()[:10])
    return {u for u, d in dates.items() if len(d) > 1}


def _page_events(page: str) -> List[Dict[str, Any]]:
    """Every schema.org Event on one page, in document order."""
    out = []
    for block in _LD_RX.findall(page or ""):
        doc = _parse_ld(block)
        if doc is not None:
            out.extend(item for item in _iter_items(doc) if _is_event(item))
    return out


def _prepared(page: str, page_url: str, detail: bool):
    """(item, to_event keyword arguments) for each Event on a fetched page. A
    keyword is passed only when it is set, so an ordinary page calls to_event
    exactly as it always did."""
    items = _page_events(page)
    flags = [False] * len(items)
    if _MEC_PAGE_RX.search(page or ""):
        # MEC's own "Free" is believable only where it sits beside the one event
        # it describes: an event page, not a listing of dozens.
        keep_zero = detail and len(items) == 1 and _mec_cost_says_free(page)
        pairs = [_from_mec(item, keep_zero) for item in items]
        items, flags = [p[0] for p in pairs], [p[1] for p in pairs]
    shared = _shared_urls(items, page_url)
    out = []
    for item, withdrawn in zip(items, flags):
        uid = item.get("url") or page_url
        opts = {"occurrence_key": isinstance(uid, str) and uid in shared,
                "price_withdrawn": withdrawn}
        out.append((item, {k: v for k, v in opts.items() if v}))
    return out


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
             skip_rx: Optional[re.Pattern] = None, *, occurrence_key: bool = False,
             price_withdrawn: bool = False) -> Optional[NormalizedEvent]:
    """occurrence_key: this url carries other dates on the same page (_shared_urls).
    price_withdrawn: a price the page printed was set aside (_mec_unset_price), so an
    empty result must still be sent as {} - the sync then CLEARS facts an earlier
    run stored from that price instead of leaving them on the row."""
    if "OnlineEventAttendanceMode" in str(item.get("eventAttendanceMode") or ""):
        return None
    # schema.org/eventStatus is the publisher saying the show is off, in the same
    # markup we are already reading for the date and the venue — free to honour,
    # and mapsee_ingest_festivals.py has honoured it since it was written. This
    # adapter did not, so a venue that correctly flipped its own page to
    # EventCancelled still had the gig re-imported on the next run.
    # Postponed counts too: the date in this markup is the one that is NOT
    # happening, so importing it puts a wrong date on the map rather than a
    # missing one.
    if any(s in str(item.get("eventStatus") or "")
           for s in ("EventCancelled", "EventPostponed")):
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
    raw_offers = item.get("offers")
    first_offer = (raw_offers[0] if isinstance(raw_offers, list) and raw_offers
                   else raw_offers if isinstance(raw_offers, dict) else {})
    ticket_url = ((first_offer.get("url") if isinstance(first_offer, dict) else None)
                  or item.get("url") or page_url)
    description = _clean(item.get("description"))
    admission = normalize_admission_facts(
        raw_offers, url=ticket_url, context=f"{name} {description or ''}")
    description = admission_description(description, admission)
    # Retain named participants already published in this downloaded Event.
    # CMS type placeholders and unrelated book authors are not participants.
    details = dict(admission or {})
    named_performers = []
    raw_performers = _ld_get(item, "performer")
    if isinstance(raw_performers, dict):
        raw_performers = [raw_performers]
    if isinstance(raw_performers, list):
        for person in raw_performers[:10]:
            if not isinstance(person, dict) or not isinstance(person.get("name"), str):
                continue
            person_name = _meaningful(person["name"])
            kind = person.get("@type")
            if (kind in ("Person", "Organization", "PerformingGroup") and person_name
                    and len(person_name) <= 200
                    and person_name.casefold() not in ("person", "organization", "performer", "host")):
                named_performers.append({"type": kind, "name": person_name})
    if named_performers:
        details["performers"] = named_performers
    organizer = _ld_get(item, "organizer")
    if isinstance(organizer, dict) and isinstance(organizer.get("name"), str):
        organizer_name = _meaningful(organizer["name"])
        organizer_url = organizer.get("url")
        if (organizer.get("@type") in ("Person", "Organization") and organizer_name
                and len(organizer_name) <= 200 and isinstance(organizer_url, str) and len(organizer_url) <= 2048
                and organizer_name.casefold() not in ("person", "organization", "organizer", "host", "unknown")):
            try:
                parsed = urlparse(organizer_url)
                if parsed.scheme in ("http", "https") and parsed.hostname and not parsed.username and not parsed.password:
                    details["organizer"] = {"type": organizer["@type"], "name": organizer_name, "url": organizer_url}
            except ValueError:
                pass
    ev = NormalizedEvent(
        source="jsonld",
        source_id=(item.get("url") or page_url) + ("#" + date_key if occurrence_key else ""),
        name=name,
        description=description,
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
        ticket_url=ticket_url,
        # {} and not None after a withdrawn price: to_row writes NULL for {}, and
        # needs_detail_sync sends the row even under --only-new, so a row stored
        # as {free: true, offer.price "0"} before the fix is put right on the next
        # run instead of serving isAccessibleForFree until the event has passed.
        source_details=details or ({} if price_withdrawn else None),
        admission_checked=True,
    )
    ev.fingerprint = make_fingerprint(name, date_key, venue)
    return ev


def ingest_site(store: EventStore, session, site: Dict[str, Any], *, start_offset=0,
                start_inline=0, deadline=None) -> int:
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
    inline_seen = 0
    for listing in site.get("listing", []):
        if deadline is not None and time.monotonic() >= deadline:
            raise BudgetExpired(start_offset, max(inline_seen, start_inline))
        try:
            r = session.get(listing, timeout=20)
            r.raise_for_status()
        except Exception as exc:
            print(f"[jsonld] {name} listing {listing} failed: {exc}")
            continue
        # harvest Event blocks embedded in the listing page itself
        for item, opts in _prepared(r.text, listing, detail=False):
            if inline_seen < start_inline:
                inline_seen += 1
                continue
            if deadline is not None and time.monotonic() >= deadline:
                raise BudgetExpired(start_offset, inline_seen)
            ev = to_event(item, listing, category, session, venue_default, skip_rx, **opts)
            if ev:
                store.upsert(ev)
                kept += 1
            inline_seen += 1
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
    for offset in range(start_offset, min(len(urls), cap)):
        if deadline is not None and time.monotonic() >= deadline:
            raise BudgetExpired(offset, inline_seen)
        u = urls[offset]
        try:
            r = session.get(u, timeout=20)
            r.raise_for_status()
        except Exception as exc:
            print(f"[jsonld] {name} {u} failed: {exc}")
            continue
        for item, opts in _prepared(r.text, u, detail=True):
            ev = to_event(item, u, category, session, venue_default, skip_rx, **opts)
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
    ap.add_argument("--max-minutes", type=float, default=0,
                    help="Finish before the Actions step cap, then sync the checkpointed store.")
    ap.add_argument("--cursor", default=CURSOR_PATH)
    a = ap.parse_args(argv)
    if a.max_minutes < 0:
        ap.error("--max-minutes must be non-negative")

    cfg = json.loads(open(a.config, encoding="utf-8").read())
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.8"})
    store = EventStore(a.store)
    sites = cfg.get("sites", [])
    deadline = time.monotonic() + a.max_minutes * 60 if a.max_minutes else None
    cursor = _load_cursor(a.cursor) if deadline is not None else {}
    start = next((i for i, site in enumerate(sites) if _site_key(site) == cursor.get("source")), None)
    if start is None:
        start, cursor = 0, {}  # a removed site must not lend its offset to another
    total = 0
    attempted = 0
    for step in range(len(sites)):
        index = (start + step) % len(sites)
        site = sites[index]
        if deadline is not None and time.monotonic() >= deadline:
            print(f"[jsonld] budget reached after {attempted} sites; next: {site.get('name', '?')}")
            break
        offset = cursor.get("offset", 0) if step == 0 and index == start else 0
        inline_offset = cursor.get("inline_offset", 0) if step == 0 and index == start else 0
        complete = False
        partial_offset = None
        try:                                              # one site failing must not abort the sweep
            total += ingest_site(store, session, site, start_offset=offset,
                                 start_inline=inline_offset, deadline=deadline)
            complete = True
        except BudgetExpired as exc:
            partial_offset = exc.offset
            partial_inline = exc.inline_offset
        except Exception as exc:
            print(f"[jsonld] {site.get('name','?')} FAILED: {exc}")
            complete = True
        finally:
            store.save()
            _save_geo_cache()
        if partial_offset is not None:
            if deadline is not None:
                _save_cursor(a.cursor, _site_key(site), partial_offset, partial_inline)
            print(f"[jsonld] budget reached inside {site.get('name', '?')} at inline event {partial_inline}, detail page {partial_offset}; resuming there next run")
            break
        if complete:
            attempted += 1
            if deadline is not None:
                _save_cursor(a.cursor, _site_key(sites[(index + 1) % len(sites)]))
    store.save()
    _save_geo_cache()
    print(f"[jsonld] done: +{total} events processed; store now holds {len(store.records)} unique events.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
