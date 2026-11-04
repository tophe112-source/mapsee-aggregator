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
      crawl_delay seconds between two requests to the site's host. Only ever
                  LENGTHENS the pace: the host's own robots.txt Crawl-delay is
                  read every run and the longer of the two wins (see _pause_for).
      naive_is_utc + timezone   the site prints a UTC instant with no offset
                  ("2026-10-24T09:00:00" for a noon start in Tartu); such stamps
                  are turned into `timezone`'s wall clock with its offset.
      description_hours "center_when" opts thecentersd.org into its labelled
                  When:/Time: clock reader, only for matching single-day dates.
                  "dortmund_schedule" reads the verified Stricken mit Ursel
                  Wann:/Wo: range, only on its source's matching Wednesdays.
    link_pattern matches either literal hrefs (no url_template) or captures a
    fragment that url_template turns into a page URL — Wix pages, for example,
    embed the FULL event list as {"slug": ...} JSON while only rendering the
    first screenful as anchors. Non-event URLs a loose pattern sweeps up just
    404 or carry no Event block and are skipped.

Notes:
  • Tolerant JSON: some sites (Ticket Tomato) emit invalid \\' escapes in their
    JSON-LD — those are repaired before parsing.
  • Coordinates: location.geo when present; otherwise a US street address is
    left for the sync's Census batch geocoder, and address-less venues, and
    street addresses outside the US, fall back to one cached Photon lookup (same
    geocode_cache.json as the other feed adapters).
  • Politeness: identified UA, robots.txt read once per host a run and obeyed,
    at least 1 s (or the host's Crawl-delay) between two requests to one host,
    per-site cap. See the pacing section below.
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
from html.parser import HTMLParser
from mapsee_geo_budget import geocode_allowed
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import robots_txt

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

from mapsee_ingest import NormalizedEvent, EventStore, make_fingerprint, notice_reason, strip_notice
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


# ---- pacing: robots.txt, and one host's requests as far apart as it asks ------
# THIS ADAPTER NEVER READ ROBOTS.TXT AND PACED EVERY HOST AT 1 s. Measured
# 2026-10-25 over the 835 configured sites (692 hosts): 31 sites are on a host
# whose robots.txt asks for more (2-3 s: 8, 10 s: 12, 20-30 s: 10, 150 s: 1),
# and every one of them was being read at about 1 s. Curation left the other
# side out: Askim Kulturhus (Crawl-delay 10, 52 program pages) and Ælvespeilet
# (5) were not proposed on 2026-10-23 for exactly this. So each host's
# robots.txt is read once a run (robots_txt.Robots, the parser verify uses) and
# a request starts no sooner than max(MIN_INTERVAL_S, the site's `crawl_delay`,
# the file's Crawl-delay) after the previous answer from the same host, across
# sites, so two configs on one host share its pace. Honouring the 30 delays of
# 30 s or less costs 1,202 s a full pass, over the 206 event pages 12 of those
# listings link today; 12 others carry their Events on the listing itself, and
# 6 did not answer or showed none. The wait is taken before a request now, not
# after every one, so a site's last page no longer sleeps 1 s for a host nobody
# asks next: a second off each of the 835 site visits.
#
# A WAIT LONGER THAN MAX_CRAWL_DELAY_S IS NOT SLEPT: THE REQUEST IS NOT SENT.
# GRAAP asks 150 s, and its 28 event pages would be 70 min of a 45-minute step;
# a sleep carried past --max-minutes would resume on the same page next run and
# sleep again. The first request to a host in a run waits for nothing (robots.txt
# itself is not counted), so such a site is still read from its listing alone,
# once a visit: GRAAP's listing carries 30 Event blocks. 30 s is the cap the OSM
# walk uses (OSM_MAX_CRAWL_DELAY_S in catalog_curate.py).
#
# WHAT ROBOTS.TXT REFUSES IS NOT ASKED: a Disallow on the page's path, a bot
# challenge served on robots.txt, or a file that cannot be read at all (RFC 9309
# section 2.3.1: assume Disallow: /). On 2026-10-25 no configured listing was
# disallowed; 16 sat behind an unreadable or challenged robots.txt. Of their 15
# hosts, 12 failed the listing request too (TLS and connection errors, a 403)
# and 2 answered robots.txt when asked again; one listing, lafontana.org's,
# loaded while its robots.txt did not, and is skipped until that file answers.
#
# A HOST THAT ANSWERS 429 IS ASKED NOTHING MORE THIS RUN. Tartu Kultuuriaken,
# which sets no Crawl-delay, answered "429 Too Many Requests" to the 301st page
# of a second 300-page dry run, about 600 requests in 27 min at this 1 s pace
# (2026-10-25), and the adapter went on asking it at the same pace. Its next
# visit starts again from the listing. Same rule as
# mapsee_ingest_drupal_fullcalendar's 429.
MIN_INTERVAL_S = 1.0
MAX_CRAWL_DELAY_S = 30.0
_ANSWERED: Dict[str, float] = {}        # host origin -> perf_counter() of its last answer
_THROTTLED: set = set()                 # host origins that answered 429 this run


def _site_delay(site: Dict[str, Any]) -> float:
    """The config's own `crawl_delay`, 0 when unset; a junk value is a config error."""
    raw = site.get("crawl_delay")
    if raw is None:
        return 0.0
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw < 0:
        raise ValueError(f"{site.get('name', '?')}: crawl_delay must be a number of seconds, not {raw!r}")
    return float(raw)


def _pause_for(robots, url: str, site_delay: float) -> Tuple[Optional[float], Optional[str]]:
    """(seconds this url's host wants between two requests, None), or (None, why
    robots.txt refuses the url). `robots` None asks nothing: the floor and the
    config's delay only, which is what the offline tests drive."""
    pause = max(MIN_INTERVAL_S, site_delay)
    if robots is None:
        return pause, None
    ans = robots.check(url)
    if ans.get("allowed") is not True:
        return None, (f"robots.txt ({ans.get('status')}) refuses {robots_txt.request_path(url)[:80]}"
                      + (f": {ans['rule']}" if ans.get("rule") else ""))
    return max(pause, float(ans.get("crawl_delay") or 0)), None


def _ask(session, robots, url: str, site_delay: float, now: Optional[float],
         deadline: Optional[float], expired: "BudgetExpired", tally: Dict[str, Any]):
    """GET `url` once its host may be asked again; None when it may not be.

    `now` is the caller's own time.monotonic() read and `expired` the
    BudgetExpired it would raise: test_feed_budgets drives the clock with a
    fixed list of values, one per page, so this reads none of its own. The
    elapsed time since a host's last answer is perf_counter's, as in _took. A
    wait that would end past the deadline is not started."""
    origin = robots_txt.origin_of(url)
    fresh = robots is not None and origin not in getattr(robots, "_files", {})
    pause, why = _pause_for(robots, url, site_delay)
    # THE robots.txt READ IS THE HOST'S FIRST REQUEST, so the first page waits
    # its Crawl-delay after it, as catalog_curate's paced session does. Before,
    # it went out about 1 s later: GRAAP (Crawl-delay 150) got /robots.txt and
    # /agenda/ back to back on every run (2026-10-25 review).
    if fresh and origin not in _ANSWERED:
        _ANSWERED[origin] = time.perf_counter()
    if why:
        tally["refused"] = tally.get("refused", 0) + 1
        tally.setdefault("refusal", why)
        return None
    if origin in _THROTTLED:
        tally["throttled"] = tally.get("throttled", 0) + 1
        return None
    last = _ANSWERED.get(origin)
    wait = 0.0 if last is None else max(0.0, pause - (time.perf_counter() - last))
    if wait > MAX_CRAWL_DELAY_S:
        tally["unasked"] = tally.get("unasked", 0) + 1
        tally["asks"] = max(tally.get("asks", 0), pause)
        return None
    if wait > 0:
        if now is not None and now + wait >= deadline:
            raise expired
        time.sleep(wait)
    try:
        r = session.get(url, timeout=20)
    finally:
        _ANSWERED[origin] = time.perf_counter()
    if getattr(r, "status_code", None) == 429:
        _THROTTLED.add(origin)
    return r

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


# Photon requests this process has sent, and how many found no point. A miss is
# not cached, so it is paid again every run; each site's line prints its own
# share (_took), as mapsee_ingest_ics's lines do.
_PHOTON = {"lookups": 0, "misses": 0}


def _photon_count() -> Tuple[int, int]:
    return _PHOTON["lookups"], _PHOTON["misses"]


def _took(started: float, photon: Tuple[int, int]) -> str:
    """The tail of a site's log line: its seconds, and its Photon requests if any.

    perf_counter, not monotonic: the budget tests drive time.monotonic with a
    list of values, and a clock read here would use one of them up."""
    lookups, misses = _PHOTON["lookups"] - photon[0], _PHOTON["misses"] - photon[1]
    out = f" in {time.perf_counter() - started:.1f}s"
    if lookups:
        out += f"; {lookups} Photon lookup(s), {misses} missed"
    return out


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
    _PHOTON["lookups"] += 1
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
    _PHOTON["misses"] += 1
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


# SOME SITES PRINT A UTC INSTANT WITH NO OFFSET. Tartu Kultuuriaken (Drupal,
# kultuuriaken.tartu.ee) gives "startDate": "2026-10-25T10:00:00" for an event
# its own page shows at "P 25. okt 2026 | 12:00 - 15:00", and 16:00 for "R 23.
# okt 2026 | 19:00", two days earlier in summer time (5 of 5 pages checked on
# 2026-10-25, the first day of EET). A
# naive stamp is otherwise the venue's wall clock (the WP Event Manager note in
# this file's test), which the sync localises from the coordinates, so every
# Tartu row would be 2 h early in winter and 3 h in summer. `naive_is_utc` with
# a `timezone` turns such a stamp into that zone's wall clock WITH its offset:
# the date digits are then the local date the fingerprint and the past-event
# check read, and the sync passes an offset-bearing stamp through untouched.
# Applied after the MEC rule and only to a stamp the page itself printed naive,
# because the MEC rule's own output is a naive wall clock.
_NAIVE_RX = re.compile(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})(?::(\d{2}))?(?:\.\d+)?")


def _site_zone(site: Dict[str, Any]) -> Optional[ZoneInfo]:
    """The zone a `naive_is_utc` site's stamps are turned into; None when unset."""
    if not site.get("naive_is_utc"):
        return None
    try:
        return ZoneInfo(site["timezone"])
    except (KeyError, TypeError, ValueError, ZoneInfoNotFoundError) as exc:
        raise ValueError(f"{site.get('name', '?')}: naive_is_utc needs an IANA `timezone`, "
                         f"not {site.get('timezone')!r}") from exc


def _utc_wall_clock(stamp: Any, zone: ZoneInfo) -> Any:
    """A naive timed stamp read as UTC, as `zone`'s wall clock with its offset."""
    m = _NAIVE_RX.fullmatch(stamp.strip()) if isinstance(stamp, str) else None
    if not m:
        return stamp                                   # a bare date, an offset, "" or junk
    try:
        utc = datetime.fromisoformat(f"{m[1]}T{m[2]}:{m[3] or '00'}").replace(tzinfo=timezone.utc)
    except ValueError:
        return stamp
    return utc.astimezone(zone).isoformat()


def _from_naive_utc(item: Dict[str, Any], printed: Dict[str, Any], zone: ZoneInfo) -> Dict[str, Any]:
    """`item` with each stamp the page printed naive (`printed` is the Event as
    read, before the MEC rule) turned from UTC into `zone`'s wall clock."""
    moved = {k: _utc_wall_clock(item[k], zone) for k in ("startDate", "endDate")
             if isinstance(item.get(k), str) and item.get(k) == printed.get(k)}
    moved = {k: v for k, v in moved.items() if v != item[k]}
    return dict(item, **moved) if moved else item


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


class _PageMetadata(HTMLParser):
    """The page's own metadata tags, kept separate from body and card text."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.og_title = None
        self.description = None
        self.canonical = None

    def handle_starttag(self, tag, attrs):
        attrs = {k.lower(): v for k, v in attrs if k and v is not None}
        if tag.lower() == "meta":
            prop = (attrs.get("property") or "").lower()
            name = (attrs.get("name") or "").lower()
            if prop == "og:title" and self.og_title is None:
                self.og_title = attrs.get("content")
            elif name == "description" and self.description is None:
                self.description = attrs.get("content")
        elif tag.lower() == "link" and "canonical" in (attrs.get("rel") or "").lower().split():
            if self.canonical is None:
                self.canonical = attrs.get("href")


def _title_signature(value: Optional[str]) -> str:
    """Compare publisher and Event titles across entity and punctuation variants."""
    cleaned = _clean(value) or ""
    return re.sub(r"[^\w]+", " ", cleaned.casefold(), flags=re.UNICODE).strip()


def _same_canonical_page(canonical: str, page_url: str) -> bool:
    """A supplied canonical must resolve to this exact page, not another card."""
    try:
        a, b = urlparse(canonical), urlparse(page_url)
        return (a.scheme.lower(), a.netloc.lower(), a.path.rstrip("/"), a.query) == \
               (b.scheme.lower(), b.netloc.lower(), b.path.rstrip("/"), b.query)
    except (AttributeError, ValueError):
        return False


def _detail_meta_description(item: Dict[str, Any], page: str, page_url: str) -> Optional[str]:
    """Use only a matching single-event page's short description meta tag.

    Some publishers omit Event.description and offers from schema.org but publish
    the event's own description in the page head. The opt-in caller limits this
    to a detail page with exactly one Event. Title agreement and canonical URL
    prevent borrowing prose from a related card or a redirected/aggregate page.
    Existing Event descriptions and offers always remain authoritative.
    """
    if _ld_get(item, "description") or _ld_get(item, "offers"):
        return None
    meta = _PageMetadata()
    try:
        meta.feed(page or "")
    except Exception:
        return None
    if not meta.description or not meta.og_title:
        return None
    if _title_signature(_ld_get(item, "name")) != _title_signature(meta.og_title):
        return None
    if meta.canonical and not _same_canonical_page(meta.canonical, page_url):
        return None
    description = _clean(meta.description)
    return description if description and len(description) <= 1000 else None


def _prepared(page: str, page_url: str, detail: bool, meta_description: bool = False,
              utc_zone: Optional[ZoneInfo] = None):
    """(item, to_event keyword arguments) for each Event on a fetched page. A
    keyword is passed only when it is set, so an ordinary page calls to_event
    exactly as it always did. utc_zone: the site's `naive_is_utc` zone (_site_zone)."""
    items = _page_events(page)
    if detail and meta_description and len(items) == 1:
        description = _detail_meta_description(items[0], page, page_url)
        if description:
            items[0] = dict(items[0], description=description)
    printed = list(items)
    flags = [False] * len(items)
    if _MEC_PAGE_RX.search(page or ""):
        # MEC's own "Free" is believable only where it sits beside the one event
        # it describes: an event page, not a listing of dozens.
        keep_zero = detail and len(items) == 1 and _mec_cost_says_free(page)
        pairs = [_from_mec(item, keep_zero) for item in items]
        items, flags = [p[0] for p in pairs], [p[1] for p in pairs]
    if utc_zone is not None:
        items = [_from_naive_utc(item, raw, utc_zone) for item, raw in zip(items, printed)]
    shared = _shared_urls(items, page_url)
    out = []
    for item, withdrawn in zip(items, flags):
        uid = item.get("url") or page_url
        opts = {"occurrence_key": isinstance(uid, str) and uid in shared,
                "price_withdrawn": withdrawn}
        out.append((item, {k: v for k, v in opts.items() if v}))
    return out


def _clean(s: Optional[str]) -> Optional[str]:
    # A Text a CMS printed as a list reads as its lines, not as Python's repr:
    # Tartu Kultuuriaken names a hall ["Elektriteater", "Raekoja saal"] (71 of
    # 299 events on 2026-10-25), which its own listing shows as "Elektriteater,
    # Raekoja saal".
    if isinstance(s, list):
        s = ", ".join(x.strip() for x in s if isinstance(x, str) and x.strip())
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


# A streetAddress printed as a LIST OF LINES (Drupal's schema_metatag) is the
# street and then the town: Tartu Kultuuriaken's are ["Ülikooli tänav 1",
# "Tartu", "Tartu maakond", "Eesti"] and ["Kompanii 2", "Tartu"], with no
# addressLocality beside them. Read whole it was one string, "['Kompanii 2',
# 'Tartu']", that no geocoder can read. Of 299 of its events on 2026-10-25, 296
# print the lines (2 to 5 of them) and none an addressLocality; the second line
# is a town in 281, a county in 8, a postcode and town ("50707 Tartu") in 4 and
# a parish in 3, each a place Photon can read beside the street. So the first
# line is the street, and the second is the town only where the block names none.
def _street_lines(street: Any) -> Tuple[Optional[str], Optional[str]]:
    """(street, town from the second line) of a streetAddress; town None unless a list."""
    if not isinstance(street, list):
        return _clean(street), None
    lines = [line for line in (_clean(x) for x in street if isinstance(x, str)) if line]
    return (lines[0] if lines else None), (lines[1] if len(lines) > 1 else None)


def _address_parts(loc: Dict[str, Any]) -> Dict[str, Optional[str]]:
    addr = _ld_get(loc, "address")
    out = {"address": None, "city": None, "region": None, "postal_code": None, "country": None}
    if isinstance(addr, dict):
        out["address"], town = _street_lines(addr.get("streetAddress"))
        out["city"] = _clean(addr.get("addressLocality")) or town
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


# A STREET AND A TOWN ABROAD ARE NOT GEOCODED HERE, though the sync (Census,
# US only) cannot place them either. A first version asked Photon for
# "<street>, <town>" and took its first answer unchecked; the 2026-10-25 review
# found that answer at another street or another town, and Tartu Kultuuriaken's
# rows include online courses and a city-wide placeholder that would have been
# pinned. Placing them wants the town check the tribe adapter's StreetPlacer
# has (merged 2026-10-26), not a bare first answer.


# schema.org/eventStatus values that mean "not at this place on this date".
# Postponed counts: the date in the markup is the one that is NOT happening.
# MovedOnline counts: the row we stored sends people to a room nobody is in
# (mapsee_ingest_openactive refuses it for the same reason).
DEAD_STATUSES = ("EventCancelled", "EventPostponed", "EventMovedOnline")


def dead_status(item: Dict[str, Any]) -> Optional[str]:
    """The eventStatus that takes this Event off the map, or None."""
    status = str(item.get("eventStatus") or "")
    return next((s for s in DEAD_STATUSES if s in status), None)


def previous_starts(item: Dict[str, Any]) -> List[str]:
    """The dates an EventRescheduled Event says it is NO LONGER on.

    The fingerprint is name|date|venue, so a show moved from the 7th to the 8th
    is a NEW row and the row of the 7th stays on the map, telling people to come
    on the wrong night, until the 7th passes. schema.org's previousStartDate is
    the publisher naming that date. store_item tombstones only a key that is
    not the live one: a new time on the same day is the same row, whose time
    the sync rewrites."""
    if "EventRescheduled" not in str(item.get("eventStatus") or ""):
        return []
    prev = item.get("previousStartDate")
    prev = prev if isinstance(prev, list) else [prev]
    return list(dict.fromkeys(p.strip() for p in prev if isinstance(p, str) and len(p.strip()) >= 10))


_CENTER_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_CENTER_DAY_RX = re.compile(r"\b(" + "|".join(_CENTER_WEEKDAYS) + r")s?\b", re.I)
_CENTER_PERIOD = r"([ap])\.?\s*m\.?"
_CENTER_RANGE_RX = re.compile(
    r"(?<![\d:])(\d{1,2})(?::([0-5]\d))?\s*(?:" + _CENTER_PERIOD + r")?"
    r"\s*(?:[-\u2013\u2014]|to|and)\s*(\d{1,2})(?::([0-5]\d))?\s*" + _CENTER_PERIOD + r"\b", re.I)


def _center_place_url(url):
    parsed = urlparse(str(url or ""))
    if parsed.hostname == "thecentersd.org" and parsed.path.rstrip("/") in (
            "/events/center-library", "/events/cyber-center"):
        return "https://thecentersd.org" + parsed.path.rstrip("/") + "/"
    return None


def _center_minutes(match):
    ah, am, ap, bh, bm, bp = match.groups()
    if not (1 <= int(ah) <= 12 and 1 <= int(bh) <= 12):
        return None
    a = (int(ah) % 12 + (12 if (ap or bp).lower() == "p" else 0)) * 60 + int(am or 0)
    b = (int(bh) % 12 + (12 if bp.lower() == "p" else 0)) * 60 + int(bm or 0)
    return (f"{a // 60:02d}:{a % 60:02d}", f"{b // 60:02d}:{b % 60:02d}") if b > a else None


def _center_place_hours(item, canonical):
    """Only the two reviewed places, from their freshly published week clocks."""
    text = _clean(item.get("description")) or ""
    library = canonical.endswith("/center-library/")
    weekday = re.search(r"(?:Regular Hours:\s*Open\s+)?Monday\s*[-\u2013]\s*Friday,\s*", text, re.I)
    if not weekday or (library and not weekday.group(0).lower().startswith("regular hours")):
        return None
    clock = _CENTER_RANGE_RX.match(text, weekday.end())
    span = _center_minutes(clock) if clock else None
    if not span:
        return None
    days = {str(d): [list(span)] for d in range(5)}
    if not library:
        saturday = re.search(r"Saturdays\s+from\s*", text, re.I)
        clock = _CENTER_RANGE_RX.match(text, saturday.end()) if saturday else None
        weekend = _center_minutes(clock) if clock else None
        if not weekend or not re.search(r"excluding holidays", text, re.I) or not re.search(r"excluding holiday weekends", text, re.I):
            return None
        days["5"] = [list(weekend)]
    return days


def _center_owns_record(rec, canonical=None):
    refs = rec.get("sources") or []
    return bool(refs) and all(isinstance(ref, dict) and ref.get("source") == "jsonld"
        and urlparse(str(ref.get("source_id") or "")).hostname == "thecentersd.org"
        and (canonical is None or _center_place_url(ref.get("source_id")) == canonical) for ref in refs)


def _drop_center_cached_places(store):
    # Retire the old per-date CACHE too: a full sync otherwise recreates dated
    # rows after the one-time database backfill hid them. Preserve mixed owners.
    records = getattr(store, "records", {})
    drop = set()
    for fp, rec in records.items():
        places = {_center_place_url(ref.get("source_id")) for ref in rec.get("sources") or [] if isinstance(ref, dict)}
        if len(places) == 1 and None not in places and _center_owns_record(rec, next(iter(places))) and not rec.get("recurring_days"):
            drop.add(fp)
    for fp in drop:
        records.pop(fp, None)
    if drop:
        store.source_to_fp = {key: fp for key, fp in store.source_to_fp.items() if fp not in drop}
    return len(drop)


def _center_when_hours(item: Dict[str, Any], page_url: str):
    """Recover only this publisher's labelled, weekday-matched session range.

    Its MEC markup names a day while its description says When: Tuesdays,
    3-5pm. Other clocks (reservations, opening hours, an exhibition reception)
    are not the session. No title fallback, overnight inference or date edits.
    """
    if urlparse(page_url).hostname != "thecentersd.org":
        return None
    start, end = str(item.get("startDate") or ""), str(item.get("endDate") or "")
    if len(start) != 10 or (end and end != start):
        return None
    text = _clean(item.get("description")) or ""
    if re.search(r"\b(?:virtual|zoom|online)\b", str(item.get("name") or "") + " " + text, re.I):
        return None
    labels = list(re.finditer(r"\bWhen\s*:\s*(.*?)(?=\b(?:Where|Contact)\s*:|$)", text, re.I))
    if len(labels) != 1:
        return None
    when = labels[0].group(1)
    if re.search(r"\b(?:register|registration|reservations?|deadline|sign.?up|jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b|\d{1,2}/\d{1,2}|\b\d{4}\b", when, re.I):
        return None
    days = list(_CENTER_DAY_RX.finditer(when))
    if not days:
        return None
    try:
        day = datetime.strptime(start, "%Y-%m-%d").date()
    except ValueError:
        return None
    weekdays = [_CENTER_WEEKDAYS.index(m.group(1).lower()) for m in days]
    if len(days) == 2 and re.fullmatch(r"\s*(?:through|to|[-\u2013\u2014])\s*", when[days[0].end():days[1].start()], re.I):
        weekdays = [(weekdays[0] + n) % 7 for n in range((weekdays[1] - weekdays[0]) % 7 + 1)]
    elif len(days) != 1:
        return None
    if day.weekday() not in weekdays:
        return None
    ordinals = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3,
                "fourth": 4, "4th": 4, "fifth": 5, "5th": 5}
    orders = re.findall(r"\b(first|second|third|fourth|fifth|1st|2nd|3rd|4th|5th)\b", when, re.I)
    if re.search(r"\blast\b", when, re.I) or (orders and (day.day - 1) // 7 + 1 not in {ordinals[x.lower()] for x in orders}):
        return None
    ranges = list(_CENTER_RANGE_RX.finditer(when))
    if len(ranges) != 1:
        return None
    remaining = when[:ranges[0].start()] + when[ranges[0].end():]
    if re.search(r"\d\s*(?:[ap]\.?\s*m\.?|:[0-5]\d)", remaining, re.I):
        return None
    span = _center_minutes(ranges[0])
    return (f"{start}T{span[0]}:00", f"{start}T{span[1]}:00") if span else None


def _dortmund_schedule_url(url: Any) -> bool:
    if not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return (parsed.scheme == "https" and parsed.netloc == "muetterzentrum-dortmund.de"
            and parsed.path.rstrip("/") == "/events/stricken-mit-ursel"
            and not parsed.query)


def _dortmund_schedule_hours(item: Dict[str, Any], page_url: str):
    """This one publisher's labelled session range, never its office hours.

    Verified 2026-11-04: the Event dates are bare, while its own description
    says Wann: Mittwochs von 10:00 - 16:00 Uhr. Read the current range, not a
    configured fixed clock. Keep dates and the call-before-coming qualification.
    """
    if (not (_dortmund_schedule_url(page_url)
             or page_url.rstrip("/") == "https://muetterzentrum-dortmund.de/events")
            or not _dortmund_schedule_url(item.get("url") or page_url)
            or _clean(item.get("name")) != "Stricken mit Ursel"):
        return None
    start, end = item.get("startDate"), item.get("endDate")
    if not isinstance(start, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", start) or (end and end != start):
        return None
    try:
        if datetime.strptime(start, "%Y-%m-%d").weekday() != 2:
            return None
    except ValueError:
        return None
    location = _ld_get(item, "location")
    if (not isinstance(location, dict) or location.get("@type") != "Place"
            or _clean(_ld_get(location, "name")) != "Caf\u00e9"
            or _clean(_ld_get(location, "address")) != "Hospitalstra\u00dfe 6, 44149 Dortmund"):
        return None
    text = _clean(item.get("description")) or ""
    if (re.search(r"\b(?:virtual|zoom|online)\b", text, re.I)
            or "OnlineEventAttendanceMode" in str(item.get("eventAttendanceMode") or "")):
        return None
    if len(re.findall(r"\bWann\s*:", text, re.I)) != 1 or len(re.findall(r"\bWo\s*:", text, re.I)) != 1:
        return None
    labelled = re.search(r"\bWann\s*:\s*(.*?)\s*\bWo\s*:", text, re.I)
    match = re.fullmatch(
        r"Mittwochs\s+von\s+([01]\d|2[0-3]):([0-5]\d)\s*[-\u2013\u2014]\s*"
        r"([01]\d|2[0-3]):([0-5]\d)\s+Uhr"
        r"(?:\s*[-\u2013\u2014]\s*ihr\s+k\u00f6nnt\s+zu\s+jeder\s+Uhrzeit\s+dazusto\u00dfen!)?",
        labelled.group(1) if labelled else "", re.I)
    if not match:
        return None
    lo, hi = ":".join(match.group(1, 2)), ":".join(match.group(3, 4))
    if hi <= lo:
        return None
    return f"{start}T{lo}:00", f"{start}T{hi}:00"


def to_event(item: Dict[str, Any], page_url: str, category: str, session,
             venue_default: Optional[Dict[str, Any]] = None,
             skip_rx: Optional[re.Pattern] = None, *, occurrence_key: bool = False,
             price_withdrawn: bool = False, tombstone: bool = False,
             description_hours: Optional[str] = None) -> Optional[NormalizedEvent]:
    """occurrence_key: this url carries other dates on the same page (_shared_urls).
    price_withdrawn: a price the page printed was set aside (_mec_unset_price), so an
    empty result must still be sent as {} - the sync then CLEARS facts an earlier
    run stored from that price instead of leaving them on the row.
    tombstone: build the record of an Event the page marks dead (dead_status), for
    EventStore.cancel; None for a live one. Without it a dead Event is None."""
    # schema.org/eventStatus is the publisher saying the show is off, in the same
    # markup we are already reading for the date and the venue. Refusing it kept
    # the gig off the map only if we had not stored it yet: an upsert cannot
    # delete. So a dead Event is built by THIS function, exactly as the live row
    # was (same source_id, same name|date|venue fingerprint), and tombstoned
    # (store_item). Only what places a pin is skipped: the fingerprint never
    # reads coordinates, so a cancelled show is not geocoded.
    if bool(dead_status(item)) != tombstone:
        return None
    if not tombstone and "OnlineEventAttendanceMode" in str(item.get("eventAttendanceMode") or ""):
        return None
    name = _clean(item.get("name"))
    # "CANCELLED: Trio Reunion" was stored as "Trio Reunion" (EventStore refuses a
    # notice title), so a dead Event is keyed by the title inside the notice; a
    # closure, or nothing but the word, names no row (strip_notice -> None).
    if tombstone and notice_reason(name):
        name = strip_notice(name)
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
    end = (item.get("endDate") or "").strip() or None
    center_place, center_days = None, None
    center_target = (_center_place_url(item.get("url") or page_url)
                     if urlparse(page_url).hostname == "thecentersd.org" else None)
    if description_hours == "center_when" and tombstone and center_target:
        return None  # A dated closure is not evidence that a permanent place closed.
    if description_hours == "center_when" and not tombstone:
        center_place = center_target
        if center_place:
            if _center_place_url(item.get("url") or page_url) != center_place:
                return None
            center_days = _center_place_hours(item, center_place)
            try:
                weekday = str(datetime.strptime(date_key, "%Y-%m-%d").weekday())
            except ValueError:
                return None
            if not center_days or weekday not in center_days:
                return None                         # unknown hours, never a dated substitute
            start = date_key + "T" + center_days[weekday][0][0] + ":00"
            end = date_key + "T" + center_days[weekday][-1][1] + ":00"
        else:
            recovered = _center_when_hours(item, page_url)
            if recovered:
                start, end = recovered
    elif description_hours == "dortmund_schedule" and not tombstone:
        recovered = _dortmund_schedule_hours(item, page_url)
        if recovered:
            start, end = recovered
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
    if lat is None and not (parts["address"] and parts["city"]) and not tombstone:
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
        end_local=end,
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
    if center_place:
        ev.name = "Center Library" if center_place.endswith("/center-library/") else "Cyber Center"
        ev.source_id = center_place + "#place"
        ev.fingerprint = make_fingerprint(center_place, "standing", "thecentersd.org")
        ev.recurring_days = center_days
        ev.icon = "\U0001f4d6" if center_place.endswith("/center-library/") else "\U0001f3d8"
        ev.ticket_url = center_place
    return ev


def store_item(store: EventStore, item: Dict[str, Any], page_url: str, category: str, session,
               venue_default: Optional[Dict[str, Any]], skip_rx: Optional[re.Pattern],
               opts: Dict[str, Any], tally: Dict[str, int]) -> bool:
    """Upsert a live Event, or tombstone the row a dead one was stored as.
    True when a live row was stored; tally counts the tombstones."""
    dead = dead_status(item)
    if dead:
        ev = to_event(item, page_url, category, session, venue_default, skip_rx,
                      tombstone=True, **opts)
        if ev is not None and store.cancel(ev, dead) == "cancelled":
            tally["cancelled"] = tally.get("cancelled", 0) + 1
        return False
    ev = to_event(item, page_url, category, session, venue_default, skip_rx, **opts)
    if ev is None:
        return False
    for prev in previous_starts(item):
        # The same Event on the day it left, built by the same function: its
        # name, venue and url are the row's, only the date is the old one.
        old = to_event(dict(item, startDate=prev, endDate=None, eventStatus="EventPostponed"),
                       page_url, category, session, venue_default, skip_rx, tombstone=True, **opts)
        if old is not None and old.fingerprint != ev.fingerprint \
                and store.cancel(old, "EventRescheduled") == "cancelled":
            tally["rescheduled"] = tally.get("rescheduled", 0) + 1
    store.upsert(ev)
    if opts.get("description_hours") == "center_when":
        cached = getattr(store, "records", {}).get(ev.fingerprint)
        if cached and _center_owns_record(cached):
            # EventStore fills missing fields. This publisher has now explicitly
            # corrected the clock/pattern, so its sole-owned cache must refresh.
            for field in ("start_local", "end_local", "recurring_days", "icon"):
                cached[field] = getattr(ev, field)
    elif (opts.get("description_hours") == "dortmund_schedule"
          and _dortmund_schedule_url(item.get("url") or page_url)
          and ev.name == "Stricken mit Ursel"
          and ev.start_local and ev.end_local
          and len(ev.start_local) > 10 and len(ev.end_local) > 10
          and ev.start_local[:10] == ev.end_local[:10]):
        cached = getattr(store, "records", {}).get(ev.fingerprint)
        refs = (cached or {}).get("sources") or []
        if refs and all(isinstance(ref, dict) and ref.get("source") == "jsonld"
                        and _dortmund_schedule_url(ref.get("source_id")) for ref in refs):
            for field in ("start_local", "end_local"):
                cached[field] = getattr(ev, field)
    return True


def ingest_site(store: EventStore, session, site: Dict[str, Any], *, start_offset=0,
                start_inline=0, deadline=None, robots=None) -> int:
    """robots: a robots_txt.Robots shared by the run (main makes one), so each
    host's file is read once; None asks no robots.txt (the offline tests)."""
    started, photon = time.perf_counter(), _photon_count()
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
    use_meta_description = site.get("meta_description") is True
    site_delay = _site_delay(site)
    utc_zone = _site_zone(site)
    # Non-events a venue posts to its own calendar (closure and private-hire
    # notices). Matched against the event NAME, which is what they actually mean;
    # the alternative tell — a 00:00 start — would also throw away a New Year show.
    skip_rx = re.compile(site["skip_title"], re.I) if site.get("skip_title") else None
    urls: List[str] = []
    seen = set()
    kept = 0
    tally: Dict[str, Any] = {}
    if site.get("description_hours") == "center_when" and any(
            urlparse(u).hostname == "thecentersd.org" for u in site.get("listing", [])):
        dropped = _drop_center_cached_places(store)
        if dropped:
            print(f"[jsonld] {name}: retired {dropped} cached per-date place rows")
    inline_seen = 0
    for listing in site.get("listing", []):
        now = time.monotonic() if deadline is not None else None
        if now is not None and now >= deadline:
            raise BudgetExpired(start_offset, max(inline_seen, start_inline))
        try:
            r = _ask(session, robots, listing, site_delay, now, deadline,
                     BudgetExpired(start_offset, max(inline_seen, start_inline)), tally)
            if r is None:
                continue
            r.raise_for_status()
        except BudgetExpired:
            raise
        except Exception as exc:
            print(f"[jsonld] {name} listing {listing} failed: {exc}")
            continue
        # harvest Event blocks embedded in the listing page itself
        for item, opts in _prepared(r.text, listing, detail=False, utc_zone=utc_zone):
            if site.get("description_hours") == "center_when":
                opts["description_hours"] = "center_when"
            elif site.get("description_hours") == "dortmund_schedule":
                opts["description_hours"] = "dortmund_schedule"
            if inline_seen < start_inline:
                inline_seen += 1
                continue
            if deadline is not None and time.monotonic() >= deadline:
                raise BudgetExpired(start_offset, inline_seen)
            if store_item(store, item, listing, category, session, venue_default, skip_rx, opts, tally):
                kept += 1
            inline_seen += 1
        for m in (pattern.finditer(r.text) if pattern else ()):
            frag = m.group(1) if (tmpl and m.groups()) else m.group(0)
            u = urljoin(listing, tmpl.format(frag) if tmpl else frag)
            if u not in seen:
                seen.add(u)
                urls.append(u)
    if len(urls) > cap:
        print(f"[jsonld] {name}: {len(urls)} event links found but max_events={cap} "
              f"— NOT reading {len(urls) - cap}; raise max_events to cover the calendar")
    for offset in range(start_offset, min(len(urls), cap)):
        now = time.monotonic() if deadline is not None else None
        if now is not None and now >= deadline:
            raise BudgetExpired(offset, inline_seen)
        u = urls[offset]
        try:
            r = _ask(session, robots, u, site_delay, now, deadline, BudgetExpired(offset, inline_seen), tally)
            if r is None:
                continue
            r.raise_for_status()
        except BudgetExpired:
            raise
        except Exception as exc:
            print(f"[jsonld] {name} {u} failed: {exc}")
            continue
        for item, opts in _prepared(r.text, u, detail=True, meta_description=use_meta_description,
                                    utc_zone=utc_zone):
            if site.get("description_hours") == "center_when":
                opts["description_hours"] = "center_when"
            elif site.get("description_hours") == "dortmund_schedule":
                opts["description_hours"] = "dortmund_schedule"
            if store_item(store, item, u, category, session, venue_default, skip_rx, opts, tally):
                kept += 1
    print(f"[jsonld] {name}: kept {kept} events from {min(len(urls), cap)} pages"
          + (f"; {tally['cancelled']} cancelled/postponed/moved online (tombstoned)"
             if tally.get("cancelled") else "")
          + (f"; {tally['rescheduled']} old date(s) of a rescheduled event tombstoned"
             if tally.get("rescheduled") else "")
          + (f"; {tally['refused']} page(s) not asked, {tally['refusal']}"
             if tally.get("refused") else "")
          + (f"; {tally['unasked']} page(s) not asked: the host asks {tally['asks']:g} s between "
             f"requests, over the {MAX_CRAWL_DELAY_S:g} s this run will wait"
             if tally.get("unasked") else "")
          + (f"; {tally['throttled']} page(s) not asked: the host answered 429 this run"
             if tally.get("throttled") else "")
          + _took(started, photon), flush=True)
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
    robots = robots_txt.Robots(session)       # each host's file read once a run, by this UA
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
        started, photon = time.perf_counter(), _photon_count()
        try:                                              # one site failing must not abort the sweep
            total += ingest_site(store, session, site, start_offset=offset,
                                 start_inline=inline_offset, deadline=deadline, robots=robots)
            complete = True
        except BudgetExpired as exc:
            partial_offset = exc.offset
            partial_inline = exc.inline_offset
        except Exception as exc:
            print(f"[jsonld] {site.get('name','?')} FAILED: {exc}{_took(started, photon)}", flush=True)
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
