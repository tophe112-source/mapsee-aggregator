#!/usr/bin/env python3
"""
mapsee_ingest_ics.py — import ICS/iCalendar feeds into the Mapsee store.

Many cities publish their LIVE event calendars as ICS rather than open-data
rows (Seattle's Socrata permits dataset went stale in 2025; the real city-wide
calendar is Trumba ICS). ICS is also the lingua franca for Seattle Center,
libraries, parkrun, etc., so this adapter generalizes far beyond one city.

    python mapsee_ingest_ics.py --config ics_sources.json --store mapsee_events.json

Config per source (JSON list):
    name             provenance label (source = "ics:<name>")
    url              the .ics feed
    category         optional fixed Mapsee category KEY
    geocode_suffix   appended to LOCATION for Photon lookups (", Seattle, WA")
    limit            max future events to keep (default 500)
    venue            optional {name, address, city, region, postal_code, country,
                     lat, lon} used ONLY for a VEVENT that carries neither
                     LOCATION nor GEO. A neighbourhood council's annual yard
                     sale is a few hundred porches and a map, not an address,
                     so the one event such a calendar exists for is exactly the
                     one with no LOCATION - and it was being dropped as
                     unplaceable. The pin is the neighbourhood centroid. A
                     LOCATION that fails to geocode is still dropped: this is
                     for events that HAVE no place, not for places Photon missed.

Events need coordinates to land on the map: a VEVENT GEO property wins;
otherwise the LOCATION string is geocoded via Photon (OSM) — one polite
request per UNIQUE location, cached. Past events and unparseable rows are
skipped; recurring events keep only their base instance if it's upcoming
(Trumba feeds mostly pre-expand occurrences anyway).
"""
from __future__ import annotations

import argparse
import codecs
import html as _html
import json
import re
import sys
import time
from mapsee_geo_budget import geocode_allowed
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urljoin, urlparse

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

from mapsee_ingest import NormalizedEvent, EventStore, make_fingerprint, notice_reason, strip_notice
from catalog_discover_osm import CIVIC_TITLE_RX, CIVIC_HOLIDAY_RX
import mapsee_gcal

# ---- persistent geocode cache -----------------------------------------------
# Photon lookups sleep ~1.1s each for fair use; without persistence every
# Action run re-pays ~20 minutes for the SAME venues. The cache file survives
# runs via actions/cache (see aggregate-events.yml). Keyed on the full query
# (venue + suffix) so identical names in different cities never collide.
import os as _os
GEO_CACHE_PATH = _os.environ.get("GEOCODE_CACHE", "geocode_cache.json")
def _load_geo_cache():
    try:
        return json.loads(open(GEO_CACHE_PATH, encoding="utf-8").read())
    except Exception:
        return {}
GEO_CACHE_MAX = 20000  # cap the shared file; dicts keep insertion order, so trimming the front drops the oldest entries
# The LAST GATE before a committed file. Every _geocode in this repo now caches
# only a hit, but this is what makes that true regardless of which call site ran:
# a null in geocode_cache.json is permanent, and a coordless event is dropped at
# the sync, so one Photon timeout can retire a venue from the map for ever with
# nothing in any log to say so. Cheap, and self-healing for anything a previous
# version already wrote.
def _drop_nulls(cache):
    return {k: v for k, v in cache.items()
            if v is not None and isinstance(v, (list, tuple)) and len(v) >= 2 and v[0] is not None}


def _save_geo_cache(cache):
    try:
        cache = _drop_nulls(cache)
        if len(cache) > GEO_CACHE_MAX:
            cache = dict(list(cache.items())[-GEO_CACHE_MAX:])
        open(GEO_CACHE_PATH, "w", encoding="utf-8").write(json.dumps(cache))
    except Exception:
        pass
_GEO_CACHE = _load_geo_cache()

# ---- conditional-GET feed cache ----------------------------------------------
# ~141 feeds were re-downloaded IN FULL every run even though most change
# rarely. We revalidate with ETag/If-Modified-Since: an unchanged feed answers
# 304 with zero body bytes and we re-parse the cached text instead (parsing is
# milliseconds - the download was the real cost). Bodies are cached only when
# the server hands us a validator; entries unused for 30 days are pruned. On a
# network error the cached body doubles as a resilience fallback.
FEED_CACHE_PATH = _os.environ.get("ICS_FEED_CACHE", "ics_feed_cache.json")
FEED_BODY_MAX = 3_000_000          # don't cache pathological multi-MB feeds
FEED_KEEP_DAYS = 30
# Entries without this version hold `resp.text` as requests decoded it before
# _decode_ics existed, which for a charset-less text/calendar is ISO-8859-1. A
# 304 would hand that text straight back, so they are ignored: one full fetch
# each, once.
FEED_CACHE_VERSION = 2
# The old reader's keys are worth computing only while a row it wrote can still
# be upcoming (mapsee_supabase_sync.rekey_legacy moves those rows). OpenAgenda's
# feeds list current and upcoming events; two months after the fix every such
# row has been moved or is past, and each sync stops paying the lookup.
LEGACY_KEYS_UNTIL = "2026-11-30"

def _load_feed_cache():
    try:
        return json.loads(open(FEED_CACHE_PATH, encoding="utf-8").read())
    except Exception:
        return {}

def _save_feed_cache(cache):
    try:
        cutoff = (datetime.now(timezone.utc).timestamp() - FEED_KEEP_DAYS * 86400)
        cache = {u: e for u, e in cache.items() if e.get("ts", 0) > cutoff}
        tmp = FEED_CACHE_PATH + ".tmp"
        open(tmp, "w", encoding="utf-8").write(json.dumps(cache))
        _os.replace(tmp, FEED_CACHE_PATH)             # atomic: a killed run can't corrupt it
    except Exception:
        pass

_FEED_CACHE = _load_feed_cache()

CURSOR_PATH = _os.environ.get("ICS_CURSOR", "ics_cursor.json")


class BudgetExpired(Exception):
    def __init__(self, offset, kept=0):
        self.offset = offset
        self.kept = kept


def _load_cursor(path):
    try:
        cursor = json.load(open(path, encoding="utf-8"))
        if isinstance(cursor, dict) and isinstance(cursor.get("source"), str) \
                and isinstance(cursor.get("offset", 0), int) and cursor.get("offset", 0) >= 0 \
                and isinstance(cursor.get("kept", 0), int) and cursor.get("kept", 0) >= 0:
            return cursor
    except (OSError, ValueError):
        pass
    return {}


def _save_cursor(path, source, offset=0, kept=0):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"source": source, "offset": offset, "kept": kept}, fh)
    _os.replace(tmp, path)

# THE DEFAULT CHARSET OF AN iCALENDAR STREAM IS UTF-8 (RFC 5545 3.1.4), and
# `resp.text` did not know it. requests decodes a text/* body whose Content-Type
# names no charset as ISO-8859-1, HTTP/1.1's default and not iCalendar's, so
# every non-ASCII character of such a feed became two: the "é" of "Numériques"
# arrived as U+00C3 U+00A9. OpenAgenda serves a bare `text/calendar`, and in the
# 2026-09-28 corpus (40,920 distinct listings from 831 feeds) 3,692 listings
# from 58 feeds were garbled this way, 57 of the feeds OpenAgenda's. A row a
# charset-less feed put on the map that way carries that text, and its
# fingerprint was hashed from it, so reading the feed correctly changes its
# identity: see `legacy_fingerprints` in mapsee_ingest.NormalizedEvent. (The
# OpenAgenda feeds themselves first ran in production after this fix; the
# re-key moved 7 older rows on 2026-09-29. adapters-and-sources.md.)
def _decode_ics(resp) -> Tuple[str, Optional[str]]:
    """(text, legacy_encoding): the body as RFC 5545 says to read it, and the
    encoding `resp.text` used instead when that reading differed, else None."""
    if "charset=" in (resp.headers.get("Content-Type") or "").lower():
        return resp.text, None                         # the server said; requests honours it
    raw = resp.content or b""
    # Unfold at the octet level first: RFC 5545 folds at 75 OCTETS, and a
    # generator that splits a character across a fold leaves UTF-8 that only
    # decodes once the fold is gone. Idempotent for _unfold afterwards.
    raw = re.sub(rb"\r?\n[ \t]", b"", raw)
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return resp.text, None                         # not UTF-8: read it as we always did
    if raw.isascii():
        return text, None
    old = resp.encoding or resp.apparent_encoding or "utf-8"
    try:
        same = codecs.lookup(old).name == "utf-8"
    except LookupError:
        return text, None                              # a reading Python cannot reproduce
    return text, (None if same else old)


def _fetch_ics(session, url, days=None):
    """GET with revalidation. Returns (text, how, legacy_encoding), how being
    200/304/reuse and legacy_encoding what the pre-_decode_ics reader used where
    it read this body differently (see _decode_ics), else None."""
    # webcal:// is https:// wearing a hat — the standard "subscribe to this
    # calendar" scheme, and what parish and club sites publish. requests has no
    # adapter for it and raises InvalidSchema, which reads as a broken feed
    # rather than as a URL nobody normalised.
    if url.lower().startswith("webcal://"):
        url = "https://" + url[9:]
    # A GOOGLE CALENDAR IS READ THROUGH THE CALENDAR API when the key is set:
    # calendar.google.com/robots.txt refuses its iCal export (see mapsee_gcal).
    # The text that comes back is built to parse exactly like the export did,
    # so a one-off event keeps its fingerprint. Not cached: the API sends no
    # validator worth the bookkeeping, and it is one request per calendar.
    # `days` is the source's `within_days`, the window the API reads ahead.
    key = mapsee_gcal.api_key()
    if key and mapsee_gcal.calendar_id(url):
        return mapsee_gcal.fetch_as_ics(session, url, key, days=int(days or mapsee_gcal.DAYS_AHEAD)), "api", None
    ent = _FEED_CACHE.get(url)
    if ent and ent.get("v") != FEED_CACHE_VERSION:
        ent = None                                     # decoded the old way; see FEED_CACHE_VERSION
    headers = {}
    if ent:
        if ent.get("etag"):
            headers["If-None-Match"] = ent["etag"]
        if ent.get("lm"):
            headers["If-Modified-Since"] = ent["lm"]
    try:
        resp = session.get(url, timeout=25, headers=headers)   # fail fast: a hanging feed must not burn a minute each
    except Exception:
        if ent and ent.get("body"):
            return ent["body"], "reuse", ent.get("legacy_enc")   # network hiccup → last good body
        raise
    now_ts = datetime.now(timezone.utc).timestamp()
    if resp.status_code == 304 and ent and ent.get("body"):
        ent["ts"] = now_ts                             # keep it inside the prune window
        return ent["body"], "304", ent.get("legacy_enc")
    resp.raise_for_status()
    text, legacy_enc = _decode_ics(resp)
    etag, lm = resp.headers.get("ETag"), resp.headers.get("Last-Modified")
    if (etag or lm) and len(text) < FEED_BODY_MAX:
        _FEED_CACHE[url] = {"etag": etag, "lm": lm, "ts": now_ts, "body": text,
                            "v": FEED_CACHE_VERSION, "legacy_enc": legacy_enc}
    else:
        _FEED_CACHE.pop(url, None)                     # no validator → full fetch every time
    return text, "200", legacy_enc



def _unfold(text: str) -> List[str]:
    """RFC 5545 line unfolding: a line starting with space/tab continues the previous."""
    out: List[str] = []
    for raw in text.splitlines():
        if raw[:1] in (" ", "\t") and out:
            out[-1] += raw[1:]
        else:
            out.append(raw)
    return out


def _unescape(v: str) -> str:
    return v.replace("\\n", "\n").replace("\\N", "\n").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")


def _parse_dt(value: str, params: Dict[str, str]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """(start_local, start_utc, date_key) from a DTSTART value + its params."""
    value = value.strip()
    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value):
        d = f"{value[0:4]}-{value[4:6]}-{value[6:8]}"
        return d, None, d
    m = re.fullmatch(r"(\d{8})T(\d{6})(Z?)", value)
    if not m:
        return None, None, None
    d, t, z = m.groups()
    iso = f"{d[0:4]}-{d[4:6]}-{d[6:8]}T{t[0:2]}:{t[2:4]}:{t[4:6]}"
    date_key = iso[:10]
    if z:  # UTC
        return iso + "Z", iso + "Z", date_key
    tzid = params.get("TZID")
    if tzid and ZoneInfo is not None:
        try:
            dt = datetime.fromisoformat(iso).replace(tzinfo=ZoneInfo(tzid))
            return dt.isoformat(), dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), date_key
        except Exception:
            pass
    return iso, None, date_key  # naive local


def parse_ics(text: str) -> List[Dict[str, Any]]:
    """Minimal VEVENT extractor: SUMMARY/DTSTART/DTEND/LOCATION/DESCRIPTION/URL/UID/GEO/STATUS."""
    events: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None
    for line in _unfold(text):
        if line == "BEGIN:VEVENT":
            cur = {}
            continue
        if line == "END:VEVENT":
            if cur is not None:
                events.append(cur)
            cur = None
            continue
        if cur is None or ":" not in line:
            continue
        head, value = line.split(":", 1)
        parts = head.split(";")
        prop = parts[0].upper()
        params = {}
        for p in parts[1:]:
            if "=" in p:
                k, v = p.split("=", 1)
                params[k.upper()] = v
        # STATUS is how RFC 5545 says an event is off (CANCELLED) or not yet
        # real (TENTATIVE). It is the ONE cancellation signal a calendar feed
        # can send us — a library or a town hall cannot delete a VEVENT it has
        # already published, so it flips STATUS instead — and this parser was
        # dropping the property on the floor, which made every cancellation
        # invisible to the whole pipeline.
        if prop in ("SUMMARY", "LOCATION", "DESCRIPTION", "URL", "UID", "GEO", "DTSTART", "DTEND",
                    "STATUS"):
            cur[prop] = (value, params)
    return events


# "Venue Name - 123 Street  City ST 12345", which is how CivicPlus writes every
# LOCATION on every municipal calendar it hosts. Photon reads it as ONE place
# name and finds nothing at all:
#
#   Elmer W. Oliver Nature Park - 1650 Matlock Road  Mansfield TX 76063  -> None
#   1650 Matlock Road, Mansfield TX 76063                -> 32.6057, -97.1148
#   Elmer W. Oliver Nature Park, Mansfield TX            -> 32.5859, -97.1025
#
# Both halves geocode; the two of them joined by a dash do not. That is the
# whole reason four newly-found city feeds ingested 0 of 34, 0 of 6, 0 of 3 and
# 1 of 16 events while carrying a full street address on every single one — and
# the log said "no LOCATION/GEO", which is the one thing that was not wrong with
# them. It is not one bad feed: it is the default output of the CMS a large share
# of US municipalities run, so it is worth a rule.
#
# ADDRESS FIRST, THEN THE VENUE. The address is the more precise answer when it
# resolves; the venue name is the better fallback than nothing, and for a park
# or a trailhead it is often the ONLY thing with a real position (407 Industrial
# Blvd. does not resolve; Animal Care and Control Facility might).
_CIVIC_DASH = re.compile(r"\s+-\s+")


def location_attempts(loc: str) -> List[str]:
    """The strings to try for one LOCATION, best first, never more than three.

    Bounded deliberately: every attempt is a second of Photon's fair-use budget
    and that budget is shared across every ics source in the run.
    """
    loc = (loc or "").strip()
    if not loc:
        return []
    out = [loc]
    # Stripped of dashes as well as spaces and commas: a LOCATION with two
    # separators ("City Hall - Council Chamber - 1200 E. Broad St") splits once
    # and leaves the second dash leading the remainder, and "- 1200 E. Broad St"
    # is a query we should not be sending anybody.
    parts = [p.strip(" ,-") for p in _CIVIC_DASH.split(loc, 1)]
    if len(parts) == 2 and all(parts):
        venue, rest = parts
        # The address half is the one with a house number in it. Checked rather
        # than assumed positional, because a few sites write it the other way
        # round ("1650 Matlock Road - Oliver Nature Park").
        if re.match(r"\d", rest):
            out += [rest, venue]
        else:
            out += [venue, rest]
        return out
    # THE EVENTS CALENDAR WRITES "Venue, street, city, ST zip, Country" WITH NO
    # DASH, so the split above never fires and Photon sees only the whole
    # string. Measured 2026-09-28 on 40 of the 125 such LOCATIONs in the 837-feed
    # corpus: the whole string placed 21, all in the right town. Adding the
    # street-onwards remainder as a second attempt placed 12 more, every one in
    # the right town, and two of them to the house number. The venue name alone
    # was measured as a third attempt and NOT added: where the first two missed,
    # it put 2 of 5 in the wrong town, which is a guess.
    commas = [p.strip() for p in loc.split(",")]
    street = next((i for i, p in enumerate(commas) if i and re.match(r"\d", p)), None)
    if street and len(commas) - street >= 2 and not re.search(r"\d", commas[0]):
        out.append(", ".join(commas[street:]))
    return out


# A SOURCE'S VENUE IS WHERE MOST OF ITS EVENTS ARE, NOT ALL OF THEM. The venue
# fallback pins every event that names no place, and a calendar's own titles
# often say one is elsewhere. Measured 2026-09-30 on the calendars that first
# got a `venue`: Betlehem in Bergen lists "Bønnemøte på nett" (online),
# "Gateevangelisering" (in the street) and a camp at Alværa beside its
# services, and Lasswade Archery Club's "Grove club session" is at Grove Farm,
# not the school hall its other sessions use. `venue_skip` is a regex over the
# title. A match is left unplaced, which is the adapter's rule for a place
# it cannot find: never pin by a guess.
_SKIP_RX_CACHE: Dict[str, "re.Pattern[str]"] = {}


def _off_venue(src: Dict[str, Any], title: str) -> bool:
    pattern = src.get("venue_skip")
    if not pattern:
        return False
    rx = _SKIP_RX_CACHE.get(pattern)
    if rx is None:
        rx = _SKIP_RX_CACHE[pattern] = re.compile(pattern, re.I)
    return bool(rx.search(title or ""))


def _venue_pin(venue) -> bool:
    """True when a source's `venue` block carries a usable coordinate pair."""
    if not isinstance(venue, dict):
        return False
    try:
        lat, lon = float(venue.get("lat")), float(venue.get("lon"))
    except (TypeError, ValueError):
        return False
    return -90 <= lat <= 90 and -180 <= lon <= 180 and (lat, lon) != (0.0, 0.0)


def make_location_geocoder(session, suffix: str):
    cache = _GEO_CACHE

    def _key(loc: str) -> str:
        return (loc + "|" + suffix).strip().lower()

    def geocode(loc: str):
        # THE ANSWER IS CACHED AGAINST WHAT THE FEED SAID, not against the
        # attempt that happened to work. Only hits are cached (see below), so
        # without this a location that needs the split pays its FAILED first
        # attempt again on every run, for ever: one wasted 1.1s Photon call per
        # CivicPlus event per day, and CivicPlus writes every location this way.
        # That is how this adapter's step went from 2h08 to 3h06 in one day and
        # took the job past its cap with The Events Calendar still to run.
        first = _key(loc)
        if first in cache:
            return cache[first]
        for attempt in location_attempts(loc):
            lat, lon = _geocode_one(attempt)
            if lat is not None:
                if attempt != loc:
                    cache[first] = (lat, lon)
                return (lat, lon)
        return (None, None)

    def _geocode_one(loc: str):
        key = _key(loc)
        if key in cache:
            return cache[key]
        if not geocode_allowed():                    # over this run's budget → retry next run
            return (None, None)
        try:
            time.sleep(1.1)                          # photon fair use ~1 req/s
            r = session.get("https://photon.komoot.io/api/",
                            params={"q": loc + suffix, "limit": 1}, timeout=20)
            r.raise_for_status()                     # a 502 is not an empty result
            f = (r.json().get("features") or [None])[0]
            out = (f["geometry"]["coordinates"][1], f["geometry"]["coordinates"][0]) if f else (None, None)
        except Exception:                            # noqa: BLE001
            return (None, None)                      # NOT cached — see below
        # ONLY A HIT IS CACHED. This is the rule mapsee_ingest_markets._geocode
        # already arrived at, on the same shared, COMMITTED file: a miss stored
        # as [null, null] was survivable while the cache was disposable, because
        # the next eviction retried it. Committed, one Photon timeout is baked
        # into git for ever and no later run ever looks that venue up again —
        # and a coordless event is dropped at the sync, so the row simply
        # vanishes with nothing anywhere saying why.
        #
        # The failing branch above returns WITHOUT caching, so a 502, a timeout
        # or a rate-limit retries next run. A genuine empty answer is not cached
        # either, which costs one 1.1s lookup per unbfindable venue per run and
        # is bounded by geocode_allowed().
        if out[0] is not None:
            cache[key] = out
        return out
    return geocode


def _summary(ev: Dict[str, Any]) -> str:
    return _unescape(ev.get("SUMMARY", ("", {}))[0]).strip()


def _location(ev: Dict[str, Any]) -> Optional[str]:
    loc = _unescape(ev.get("LOCATION", ("", {}))[0]).strip() or None
    if loc:  # Trumba locations can carry HTML ("111 Alamo Plaza<br>San Antonio")
        loc = _html.unescape(re.sub(r"<[^>]+>", ", ", loc))    # &amp; and &nbsp; alike
        loc = re.sub(r"\s*,\s*,+", ", ", re.sub(r"\s+", " ", loc)).strip(" ,") or None
    return loc


def _location_override(src: Dict[str, Any], location: Optional[str]) -> Optional[Dict[str, Any]]:
    """Return a source-configured, exact LOCATION pin, if one matches."""
    if not location:
        return None
    for rule in src.get("location_overrides", []):
        if isinstance(rule, dict) and rule.get("location") == location:
            venue = rule.get("venue")
            if _venue_pin(venue):
                return venue
    return None


def _datetime_override(src: Dict[str, Any], title: str, location: Optional[str],
                       value: str, params: Dict[str, str]) -> Optional[Dict[str, str]]:
    """Match only a source's exact timed local-wall-clock correction rule."""
    if value.endswith("Z") or not re.fullmatch(r"\d{8}T\d{6}", value):
        return None
    for rule in src.get("datetime_overrides", []):
        if (isinstance(rule, dict) and title == rule.get("title")
                and location == rule.get("location")
                and params.get("TZID") == rule.get("from_tzid")
                and rule.get("to_tzid")):
            adjusted = dict(params)
            adjusted["TZID"] = rule["to_tzid"]
            return adjusted
    return None


# SOME FEEDS STAMP THE WALL CLOCK AS UTC. A WordPress site left on the "UTC"
# timezone exports The Events Calendar's local times as TZID=UTC: Nottingham
# and Nottinghamshire Carers Hub's 13:00 quilting group, all 30 of its VEVENTs
# (2026-10-05), arrived as 14:00 BST. osmcal.org gives an event whose organiser
# set no zone `+00:00`: its own API shows the Hamburger Mappertreffen as
# "13th October 19:00" with start 2026-10-13T19:00:00+00:00, and its iCal says
# 20261013T190000Z, which is 21:00 in Hamburg (32 of its 207 VEVENTs are Z).
# datetime_overrides cannot reach either: it matches one exact title and place,
# and it leaves a Z value alone. A source with `utc_is_wall_clock` has its UTC
# stamps read as floating local time instead, and the sync gives each row the
# zone of its own coordinates. A value with any other TZID is left as it is.
#
# `wall_clock_tzids` is the same reading for a platform's DEFAULT zone. Every
# Restarters group that never set one is exported as TZID=Europe/London over
# its own wall clock: Repair Café Waremme's 09:30 in Belgium, Manurewa's 10:00
# in Auckland. The reviewers caught three such groups by title and place, and a
# rule per group misses the next one, so a non-UK Restarters feed names the
# zone instead: its rows are never in London.
UTC_TZIDS = {"UTC", "ETC/UTC", "GMT", "ETC/GMT", "UNIVERSAL", "ETC/UNIVERSAL", "ZULU"}


def _wall_clock(src: Dict[str, Any], value: str, params: Dict[str, str]) -> Tuple[str, Dict[str, str]]:
    """A DTSTART/DTEND as `utc_is_wall_clock` / `wall_clock_tzids` say to read
    it: floating local time, which the sync zones from the row's coordinates."""
    utc = bool(src.get("utc_is_wall_clock"))
    named = {str(z).strip().upper() for z in (src.get("wall_clock_tzids") or [])}
    if not utc and not named:
        return value, params
    v = value.strip()
    tzid = (params.get("TZID") or "").strip().upper()
    if utc and re.fullmatch(r"\d{8}T\d{6}Z", v):
        v = v[:-1]
    elif not tzid or tzid not in (named | (UTC_TZIDS if utc else set())):
        return value, params
    return v, {k: p for k, p in params.items() if k != "TZID"}


def _event_url(ev: Dict[str, Any], src: Dict[str, Any]) -> Optional[str]:
    """Keep published event/signup links; a calendar subscription is not one.

    CivicPlus emits its relative subscription URL in every VEVENT, but appends
    the real event page to DESCRIPTION. Use that existing link only when its
    publisher and numeric EID match this event's UID. No detail-page fetch.
    """
    base = src["url"]
    try:
        raw = _html.unescape(_unescape(ev.get("URL", ("", {}))[0] or "")).strip()
        url = urljoin(base, raw) if raw else None
        feed = urlparse(base)
        link = urlparse(url) if url else None
        civic = feed.path.lower().endswith("/common/modules/icalendar/icalendar.aspx")
        same_path = link is not None and (link.netloc.lower(), link.path.lower()) == (
            feed.netloc.lower(), feed.path.lower())
        subscription = same_path and (civic or parse_qs(link.query) == parse_qs(feed.query))
        if url and not subscription:
            return url                             # includes third-party signup URLs
        uid = (ev.get("UID", ("", {}))[0] or "").strip()
        if civic and uid.isdecimal():
            description = _html.unescape(_unescape(ev.get("DESCRIPTION", ("", {}))[0] or ""))
            for candidate in re.findall(r"https?://[^\s<>\"']+", description):
                candidate = candidate.rstrip(".,;:)")
                page = urlparse(candidate)
                query = {k.lower(): v for k, v in parse_qs(page.query).items()}
                if (page.netloc.lower() == feed.netloc.lower()
                        and page.path.lower().endswith("/calendar.aspx")
                        and query.get("eid") == [uid]):
                    return candidate
    except ValueError:                              # malformed source links do not lose the feed
        return None
    return None


def _legacy_fingerprint(old_ev: Dict[str, Any], date_key: str, venue: Optional[Dict[str, Any]],
                        cancelled: bool = False) -> str:
    """The fingerprint the pre-_decode_ics reader gave this VEVENT: the same
    parser and the same steps, over the text it was reading. A row the old
    reader pinned to the source's venue carried the venue's name, which comes
    from the config and was never misread. `cancelled`: the title as the live
    row had it, without a "CANCELLED - " the publisher added (see ingest_ics)."""
    if venue is not None:
        loc = venue.get("name") or None
    else:
        loc = _location(old_ev)
        if loc and (PLACEHOLDER_LOC_RX.match(loc) or _all_placeholders(loc)):
            loc = None
    title = _summary(old_ev)
    if cancelled and notice_reason(title):
        title = strip_notice(title) or title
    return make_fingerprint(title, date_key, loc)


def _start_key(ev) -> str:
    """DTSTART as a sortable "YYYYMMDDHHMMSS" (zone ignored: an ordering, not an
    instant). A VEVENT with no readable DTSTART sorts last; the loop skips it."""
    raw = ev.get("DTSTART", ("", {}))
    value = raw[0] if isinstance(raw, tuple) else raw
    digits = re.sub(r"[^0-9]", "", str(value or ""))
    return (digits + "000000")[:14] if len(digits) >= 8 else "99999999999999"


def ingest_ics(store: EventStore, session, src: Dict[str, Any], *, start_offset=0, start_kept=0, deadline=None) -> int:
    details_reader = None
    if src.get("details") == "ramart":
        from mapsee_event_details import ramart_reader
        details_reader = ramart_reader(session)
    got = _fetch_ics(session, src["url"], days=src.get("within_days"))
    text, how = got[0], got[1]
    legacy_enc = got[2] if len(got) > 2 else None      # tests patch in the older (text, how)
    events = parse_ics(text)
    skip_title = None
    if src.get("skip_title"):
        try:
            skip_title = re.compile(src["skip_title"], re.I)
        except (re.error, TypeError) as exc:
            raise ValueError(f"{src.get('name', '?')}: invalid skip_title regex: {exc}") from exc
    # The VEVENTs as the old reader saw them, index for index: BEGIN/END and
    # every property name are ASCII, so both readings split the same way.
    old_events = None
    if legacy_enc and datetime.now(timezone.utc).strftime("%Y-%m-%d") <= LEGACY_KEYS_UNTIL:
        try:
            old = parse_ics(text.encode("utf-8").decode(legacy_enc, errors="replace"))
        except (LookupError, ValueError):
            old = []                                   # the old keys are a courtesy, never a failure
        if len(old) == len(events):
            old_events = old
        else:
            print(f"::warning::[ics] {src['name']}: read as {legacy_enc} it has {len(old)} VEVENTs "
                  f"and as UTF-8 {len(events)}; its rows keep no legacy fingerprint", flush=True)
    # SOONEST FIRST, because `limit` is counted in walk order. CivicPlus writes
    # some calendars newest first: Capitol Heights, MD lists 790 VEVENTs from
    # 2034-11-27 down to tomorrow, so limit=100 kept 78 'Grocery Giveaway' rows
    # dated 2032-2034 and none in the next 90 days (row review, 2026-10-10).
    # The legacy reading is paired with these index for index, so it is
    # permuted the same way; the store keys rows by fingerprint, not position.
    order = sorted(range(len(events)), key=lambda i: (_start_key(events[i]), i))
    events = [events[i] for i in order]
    if old_events is not None:
        old_events = [old_events[i] for i in order]
    label = "ics:" + src["name"].lower().replace(" ", "-")
    geocode = make_location_geocoder(session, src.get("geocode_suffix", ""))
    now_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    limit = src.get("limit", 500)
    kept = start_kept
    # A VEVENT with no LOCATION and no GEO cannot be pinned, so it is dropped -
    # correctly, but until this counter it was dropped in SILENCE. Seattle Parks
    # Foundation was publishing 30 events of which 20 had no LOCATION at all,
    # and the only visible symptom was a feed that looked two thirds empty with
    # nothing anywhere saying why. A source that is mostly unplaceable is a
    # source wired up the wrong way (that one wanted the Tribe REST API, which
    # carries venues the iCal export omits), and the log has to be able to say so.
    unplaceable = 0
    pinned_by_venue = 0
    off_venue = 0
    past = 0
    governance = 0
    cancelled = 0
    cancelled_unnamed = 0
    online = 0
    skipped_title = 0
    is_civic = str(src.get("_found", "")).startswith("civic:")
    for offset in range(start_offset, len(events)):
        if kept >= limit:
            break
        if deadline is not None and time.monotonic() >= deadline:
            raise BudgetExpired(offset, kept)
        ev = events[offset]
        title = _summary(ev)
        if not title or "DTSTART" not in ev:
            continue
        # STATUS:CANCELLED, and the publisher has told us in the only way a
        # subscribed calendar can. Refusing the VEVENT kept a cancellation off
        # the map only for rows we had not written YET: an upsert cannot delete,
        # so the storytime we stored last week stayed on the map. So the record
        # is built below EXACTLY as the live one would be - same source, UID,
        # fingerprint and legacy fingerprint, which is what the stored row is
        # keyed by - and handed to store.cancel, which tombstones that row
        # (mapsee_supabase_sync sets cancelled_at + hidden_at). Nothing that only
        # PLACES the event is paid for: the fingerprint is title|date|location
        # text, never coordinates, so a cancelled VEVENT is not geocoded and its
        # page is not fetched. TENTATIVE is left alone: "not confirmed" is how a
        # lot of municipal software publishes everything, and it is not a
        # cancellation.
        is_cancelled = (ev.get("STATUS", ("", {}))[0] or "").strip().upper() == "CANCELLED"
        # A publisher that flips STATUS often renames the event too ("CANCELLED -
        # Family Storytime"); the row it cancels was stored as "Family
        # Storytime", so that is the title the record is built with - before skip_title
        # and the title-keyed overrides below read it, as they did for the live row. A
        # closure notice, or a title that is nothing but the word, names no
        # event we could have stored: counted, never guessed at.
        if is_cancelled and notice_reason(title):
            title = strip_notice(title)
            if not title:
                cancelled_unnamed += 1
                continue
        if skip_title and skip_title.search(title):
            skipped_title += 1
            continue
        # A CITY CALENDAR IS TWO CALENDARS SHARING A FEED. The discovery side
        # refuses a feed that is NOTHING but meetings (governance_heavy, at two
        # thirds), which leaves the mixed ones: Baldwin Park's Main Calendar and
        # Mansfield's carry a real programme AND their committee minutes, and
        # brought 24 and 25 governance rows onto the map. Only for sources
        # discovery proposed as civic, because the phrases are about a town hall
        # and nothing else in this config is one.
        # ...and the bare holiday, which is how a city says "we are shut" without
        # using a single governance word. Same anchored match the feed-level rule
        # uses, so "Christmas Day" goes and "Christmas Tree Lighting" stays.
        if is_civic and (CIVIC_TITLE_RX.search(title)
                         or CIVIC_HOLIDAY_RX.match(title.strip())):
            governance += 1
            continue
        start_value, start_params = _wall_clock(src, *ev["DTSTART"])
        start_local, start_utc, date_key = _parse_dt(start_value, start_params)
        adjusted_params = _datetime_override(src, title, _location(ev), start_value, start_params)
        if adjusted_params is not None:
            adjusted_local, adjusted_utc, _ = _parse_dt(start_value, adjusted_params)
            start_local, start_utc = adjusted_local, adjusted_utc
        if not date_key or date_key < now_key:
            if date_key:
                past += 1                             # counted; see the report below
            continue                                  # past (or unparseable) → skip
        end_local = end_utc = None
        if "DTEND" in ev:
            end_value, end_params = _wall_clock(src, *ev["DTEND"])
            adjusted_params = _datetime_override(src, title, _location(ev), end_value, end_params)
            end_local, end_utc, _ = _parse_dt(end_value, adjusted_params or end_params)
        loc = _location(ev)
        # An online session is on no map, whatever GEO its branch stamped on it:
        # see ONLINE_LOC_RX. Before GEO is read, because Communico gives these
        # rows the owning branch's coordinates (or 0;0, which the geocoder then
        # guesses at).
        if loc and ONLINE_LOC_RX.search(loc):
            online += 1
            continue
        elsewhere = False
        if loc and (PLACEHOLDER_LOC_RX.match(loc) or _all_placeholders(loc)):
            elsewhere = bool(ELSEWHERE_LOC_RX.match(loc)) or _all_placeholders(loc, ELSEWHERE_LOC_RX)
            loc = None                                # "TBD" names no place; see the pattern
        fingerprint_loc = loc
        had_source_location = bool(loc)
        venue = _location_override(src, loc)
        if venue:
            lat, lon = float(venue["lat"]), float(venue["lon"])
            loc = venue.get("name") or loc
        else:
            lat = lon = None
        if venue is None and "GEO" in ev:             # "lat;lon"
            try:
                lat, lon = (float(x) for x in ev["GEO"][0].split(";")[:2])
            except Exception:
                lat = lon = None
            if lat is not None and abs(lat) < 1e-9 and abs(lon) < 1e-9:
                lat = lon = None                      # GEO:0;0 is "unset", not the Gulf of Guinea
        if venue is None and (lat is None or lon is None) and loc and not is_cancelled:
            lat, lon = geocode(loc)
        if venue is None and lat is None and not loc and not elsewhere and _venue_pin(src.get("venue")):
            if _off_venue(src, title):
                off_venue += 1                        # its own title says it is somewhere else
            else:
                venue = src["venue"]                  # no place named at all: the source's own fallback
                lat, lon = float(venue["lat"]), float(venue["lon"])
                loc = venue.get("name") or None
                fingerprint_loc = loc
                pinned_by_venue += 1
        if (lat is None or lon is None) and not is_cancelled:
            unplaceable += 1
            continue                                  # nowhere to pin it
        desc = _unescape(ev.get("DESCRIPTION", ("", {}))[0]).strip() or None
        if desc:                                      # ICS descriptions are often HTML-ish — keep them short + plain
            desc = re.sub(r"<[^>]+>", " ", desc)
            desc = re.sub(r"\s+", " ", desc).strip() or None
        url = _event_url(ev, src)
        uid = (ev.get("UID", ("", {}))[0] or "").strip()
        nev = NormalizedEvent(
            source=label,
            source_id=uid or make_fingerprint(title, date_key, fingerprint_loc),
            name=title,
            description=desc,
            start_local=start_local, start_utc=start_utc,
            end_local=end_local, end_utc=end_utc,
            venue_name=loc, latitude=lat, longitude=lon,
            address=(venue or {}).get("address"),
            city=(venue or {}).get("city"), region=(venue or {}).get("region"),
            country=(venue or {}).get("country"), postal_code=(venue or {}).get("postal_code"),
            category=src.get("category"),
            ticket_url=url or (urljoin(src["url"], src["url_home"]) if src.get("url_home") else None),
            source_details=({"organizer": venue["organizer"]}
                            if venue and isinstance(venue.get("organizer"), dict) else None),
        )
        nev.fingerprint = make_fingerprint(title, date_key, fingerprint_loc)
        # A configured calendar home is an info fallback, not an individual
        # event page to fetch repeatedly for optional source-detail enrichment.
        if details_reader and url and not is_cancelled:
            details = details_reader(url, title, start_utc or start_local)
            if details is not None:
                for key, value in details.items():
                    setattr(nev, key, value)
        if old_events is not None:
            legacy_venue = venue if not had_source_location else None
            legacy = _legacy_fingerprint(old_events[offset], date_key, legacy_venue, is_cancelled)
            if legacy != nev.fingerprint:
                nev.legacy_fingerprints = [legacy]
        if is_cancelled:
            # Not counted against `limit`: a tombstone writes no row. A
            # RECURRENCE-ID override that cancels one instance carries the
            # master's UID and that instance's DTSTART, so it tombstones the
            # master's row only when it cancels the date the master is stored
            # under; the store keeps the tombstone over the master in either order.
            # A collector that only reads rows (mapsee_gcal's smoke read) has no
            # cancel; there the VEVENT is skipped, as it always was.
            cancel = getattr(store, "cancel", None)
            if cancel is not None and cancel(nev, "STATUS:CANCELLED") == "cancelled":
                cancelled += 1
            continue
        store.upsert(nev)
        kept += 1
    note = f" ({how})" if how != "200" else ""
    if pinned_by_venue:
        note += f" — {pinned_by_venue} pinned to the source's venue (no LOCATION/GEO)"
    if off_venue:
        note += f" — {off_venue} kept off the venue by venue_skip"
    if unplaceable:
        note += f" — {unplaceable} unplaceable (no LOCATION/GEO)"
        if events and unplaceable >= max(3, len(events) // 2):
            note += "; more than half this feed has no location — check whether the source offers a richer feed"
    # A FEED THAT IS ENTIRELY IN THE PAST IS A CONFIG PROBLEM, NOT A QUIET DAY,
    # and until this line it looked exactly like a venue between seasons.
    #
    # LibCal's ical_subscribe.php serves a FIXED rolling window capped at 500
    # VEVENTs, and it starts about a month BACK — so for a library busy enough
    # to fill the cap, history eats the entire future. Measured 2026-08-17:
    # Fairfax County Public Library returned 500 events ending 2026-07-26 and
    # Denver Public Library 500 ending 2026-08-15, both wholly past, both
    # contributing nothing; Arlington VA had 96 future events out of 500. No
    # parameter shifts the window — start/end, days, limit and num are all
    # accepted and ignored.
    #
    # Nothing else in the pipeline can notice this. `status` empty is not fail,
    # so the curator audit stays green, and "kept 0 of 500" is one line in a run
    # that prints 258 of them.
    if past and not kept:
        note += (f" — ALL {past} events are in the PAST; this feed is stale or "
                 f"window-capped and is contributing nothing")
    elif past and kept and past >= max(10, len(events) // 2):
        note += f" — {past} of {len(events)} already past (window may be capped)"
    # COUNTED AND PRINTED, never a silent skip. A source whose events are mostly
    # its town hall's diary is a source worth reconsidering, and that is only
    # visible if the number is on the line.
    if governance:
        note += f"; {governance} town-hall row(s) refused"
    # Same rule as governance: counted and printed, never a silent skip. A feed
    # whose cancellations suddenly jump is telling us something about the venue.
    if cancelled or cancelled_unnamed:
        note += f"; {cancelled} cancelled (STATUS:CANCELLED), tombstoned"
        if cancelled_unnamed:
            note += f", {cancelled_unnamed} more naming no event"
    if online:
        note += f"; {online} online (a virtual room, not a place)"
    if skipped_title:
        note += f"; {skipped_title} title-filtered"
    print(f"[ics] {src.get('name', '?')}: kept {kept} of {len(events)} VEVENTs{note}")
    return kept - start_kept


# A LOCATION THAT NAMES NO PLACE. Willoughby-Eastlake Public Library files its
# off-site programmes under the LOCATION "Offsite", and Photon, handed
# "Offsite, OH", placed eleven of them at a post office near Cincinnati, 369 km
# away (measured 2026-09-27). Read as no LOCATION, the event is dropped as
# unplaceable, which is the rule this adapter already keeps for a LOCATION that
# fails to geocode: never pin by a guess. Whole-value matches only.
#
# Two kinds, because they disagree about the source's own `venue` fallback:
#   - UNKNOWN ("TBD", "See description"): the source did not say where, and its
#     venue is the best guess it would make itself. The fallback applies.
#   - ELSEWHERE ("Offsite", "Outreach", "Bookmobile", "Various locations"): the
#     source said the event is NOT at its venue, so the fallback would pin it
#     exactly where it is not. So is a bare country: LibraryMarket files online
#     sessions under the LOCATION "US" (71 rows from five libraries in a sample
#     of 837 feeds, 2026-09-27; 59 of them say online or virtual in the title),
#     and "US" plus a geocode suffix is a guess.
# Communico writes LOCATION as "Branch - Room", so an event with no room ends in
# a dash: "Offsite -" (64 rows in that sample), "Bookmobile -" (210),
# "Outreach -", "External -". The trailing separators are part of the match.
# Its "not here" branches have other names too, and a review of 46 library
# calendars (2026-09-28) found them guessed onto a community centre, a school
# and a post office: "In the Community -", "Community Location -", "Outside
# Venue -", "All Library Locations -", and "Offsite - Offsite", which is two
# placeholders in a row (_all_placeholders).
_UNKNOWN_LOC = (r"tbd|tba|to be (?:announced|determined|confirmed)|location tbd|n/?a|none|"
                r"see (?:description|details|website|below)")
_ELSEWHERE_LOC = (r"off[\s-]?site|outreach|external|bookmobile|various(?: locations)?|"
                  r"multiple locations|in the community|community location|outside venue|"
                  r"all (?:library )?(?:locations|branches)|"
                  r"us|usa|u\.s\.(?:a\.)?|united states(?: of america)?|"
                  r"canada|uk|u\.k\.|united kingdom|australia|new zealand|ireland|france|"
                  r"deutschland|germany")
_LOC_TAIL = r"[\s.\-\u2013\u2014|,;:]*$"
PLACEHOLDER_LOC_RX = re.compile(rf"^\s*(?:{_UNKNOWN_LOC}|{_ELSEWHERE_LOC}){_LOC_TAIL}", re.I)
ELSEWHERE_LOC_RX = re.compile(rf"^\s*(?:{_ELSEWHERE_LOC}){_LOC_TAIL}", re.I)
_LOC_SPLIT_RX = re.compile(r"\s+[-\u2013\u2014|]\s*|\s*[-\u2013\u2014|]\s*$")


def _all_placeholders(loc: str, rx: "re.Pattern[str]" = PLACEHOLDER_LOC_RX) -> bool:
    """Is every " - " part of this LOCATION a placeholder ("Offsite - Offsite")?"""
    parts = [p for p in _LOC_SPLIT_RX.split(loc) if p.strip()]
    return len(parts) > 1 and all(rx.match(p) for p in parts)


# AN ONLINE SESSION IS NOT A PLACE, and Communico says so in the branch half of
# "Branch - Room": "Online - Virtual Room", "Virtual - Zoom", "Virtual Branch -
# Virtual Room 3", "Virtual Library - ...", or in the room half, "Westlake Porter
# Public Library - Online". The same review of 46 library calendars counted 101
# such rows with GEO 0;0, which the geocoder turned into a pin anyway (OCLS: 56
# on the Orange County centroid and 3 on a shoe shop), and 114 more carrying the
# owning branch's GEO. The sync's is_virtual only knows a venue that is nothing
# BUT placeholder words, so "Virtual - Virtual Room - Adult Programming" passed
# it and landed on a bank. Checked before GEO, and the row is skipped. Bare
# "Online" and "Virtual" match too. "Virtual Reality Lab - Room 2" does not.
ONLINE_LOC_RX = re.compile(
    r"^\s*(?:online|virtual)(?:\s+(?:branch|library|room|programs?|events?))?\s*(?:$|[-\u2013\u2014|:,])|"
    r"(?:[-\u2013\u2014|]\s*|\(\s*)(?:online|virtual)(?:\s+(?:room|event|program|class))?\s*\)?\s*$",
    re.I)


def _host(url: str) -> str:
    return urlparse(url if "://" in url else "https://" + url).netloc.lower()


def _crawl_delays(sources: List[Dict[str, Any]]) -> Dict[str, float]:
    """The longest `crawl_delay` any source on a host asks for, per host.

    ONE FEED IS ONE REQUEST, so a host's robots.txt Crawl-delay only binds when
    it serves SEVERAL feeds in one run - a LibCal library with a calendar per
    branch is the common case: Phoenix Public Library's nine branch calendars
    are nine back-to-back requests to one host whose robots.txt asks for 10 s.
    Keyed on the host, not the source, so a sibling feed that carries no
    `crawl_delay` of its own is still paced by the one that does.
    """
    out: Dict[str, float] = {}
    for src in sources:
        try:
            d = float(src.get("crawl_delay") or 0)
        except (TypeError, ValueError):
            d = 0.0
        if d > 0:
            h = _host(src.get("url", ""))
            out[h] = max(out.get(h, 0.0), d)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import ICS/iCalendar event feeds into the Mapsee store.")
    ap.add_argument("--config", required=True, help="JSON list of feeds (name, url, …).")
    ap.add_argument("--store", default="mapsee_events.json")
    ap.add_argument("--max-minutes", type=float, default=0,
                    help="Finish before the Actions step cap, then sync the checkpointed store.")
    ap.add_argument("--cursor", default=CURSOR_PATH)
    a = ap.parse_args(argv)
    if a.max_minutes < 0:
        ap.error("--max-minutes must be non-negative")

    sources = json.loads(open(a.config, encoding="utf-8").read())
    session = requests.Session()
    session.headers.update({"User-Agent": "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)"})
    store = EventStore(a.store)

    deadline = time.monotonic() + a.max_minutes * 60 if a.max_minutes else None
    cursor = _load_cursor(a.cursor) if deadline is not None else {}
    start = next((i for i, src in enumerate(sources) if src["url"] == cursor.get("source")), None)
    if start is None:
        start, cursor = 0, {}  # a removed source must not lend its offset to another
    total = 0
    attempted = 0
    delays = _crawl_delays(sources)
    last_hit: Dict[str, float] = {}
    for step in range(len(sources)):
        index = (start + step) % len(sources)
        src = sources[index]
        if deadline is not None and time.monotonic() >= deadline:
            print(f"[ics] budget reached after {attempted} sources; next: {src['name']}")
            break
        host = _host(src.get("url", ""))
        if host in delays and host in last_hit:
            wait = last_hit[host] + delays[host] - time.monotonic()
            if wait > 0:
                # A wait that would outlast the budget is the budget: the
                # cursor already names this source, so the next run starts here.
                if deadline is not None and time.monotonic() + wait >= deadline:
                    print(f"[ics] budget reached waiting out {host}'s crawl delay; next: {src['name']}")
                    break
                time.sleep(wait)
        last_hit[host] = time.monotonic()
        offset = cursor.get("offset", 0) if step == 0 and index == start else 0
        kept_before = cursor.get("kept", 0) if step == 0 and index == start else 0
        complete = False
        partial_offset = None
        try:
            total += ingest_ics(store, session, src, start_offset=offset,
                                start_kept=kept_before, deadline=deadline)
            complete = True
        except BudgetExpired as exc:
            partial_offset = exc.offset
            partial_kept = exc.kept
        except Exception as exc:
            print(f"[ics] {src.get('name', '?')} FAILED: {exc}")
            complete = True  # a refused source must not pin the cursor forever
        finally:
            # One source is the unit of recoverable work.  The Actions cache is
            # uploaded only after the process exits, but its files used to be
            # written only after every source: cancelling a long run discarded
            # every Photon result learned so far and made the next run repay the
            # same 1.1s lookups.  Keep the event store beside those caches at the
            # same boundary so a caller can safely sync a deliberately bounded
            # or interrupted run.
            store.save()
            _save_geo_cache(_GEO_CACHE)
            _save_feed_cache(_FEED_CACHE)
        if partial_offset is not None:
            if deadline is not None:
                _save_cursor(a.cursor, src["url"], partial_offset, partial_kept)
            print(f"[ics] budget reached inside {src['name']} at VEVENT {partial_offset}; resuming there next run")
            break
        if complete:
            attempted += 1
            if deadline is not None:
                _save_cursor(a.cursor, sources[(index + 1) % len(sources)]["url"])
    print(f"[ics] done: +{total} events processed; store now holds {len(store.records)} unique events.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
