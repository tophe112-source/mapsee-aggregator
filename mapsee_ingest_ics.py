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
    attendance_note  optional publisher-verified attendance instruction, prepended
                     to each normalized description before the sync's prose cap
    geocode_suffix   appended to LOCATION for Photon lookups (", Seattle, WA")
    limit            max future events to keep (default 500)
    venue            optional {name, address, city, region, postal_code, country,
                     lat, lon, aliases, street_hint, pin_unnamed, coords_exact} used for a
                     VEVENT that carries neither LOCATION nor GEO (unless
                     `pin_unnamed` is false: until a calendar's bare titles
                     have been read, see _pins_unnamed), and for one whose
                     LOCATION NAMES the venue (its name or an alias, or the
                     house number and street in `street_hint`; see
                     _names_venue); such a row keeps the feed's own LOCATION
                     as its place name and takes only the venue's point.
                     A neighbourhood council's annual yard
                     sale is a few hundred porches and a map, not an address,
                     so the one event such a calendar exists for is exactly the
                     one with no LOCATION - and it was being dropped as
                     unplaceable. The pin is the neighbourhood centroid. A
                     LOCATION that fails to geocode is still dropped: this is
                     for events that HAVE no place, not for places Photon missed.
                     A block with no `address` keeps its point exact through
                     the sync (no Census re-geocode; city, region and country
                     alone send nothing to it); `aliases` and `street_hint`
                     are only ever matched, never geocoded or written to a row.
                     A verified venue point with an address can explicitly set
                     `coords_exact: true` to bypass Census re-geocoding too.
    url_venues       optional [{pattern, venue}]: a VEVENT that carries neither
                     LOCATION nor GEO, whose URL matches `pattern` (a regex,
                     searched in the absolute URL), is pinned to that `venue`
                     block, before and instead of the source-wide `venue`.
                     For a calendar that names the place only in its URL path:
                     Texas Parks & Wildlife's 609 VEVENTs carry no LOCATION, and
                     570 sit under /calendar/<park>/ (see _url_venue_rules).

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
import math
import re
import sys
import time
import unicodedata
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

# The store, both caches and the cursor are written together between sources at
# most this often, and always when the run stops (main). The interval
# mapsee_ingest_perfectmind uses for its own group's feeds_events.json.
SAVE_EVERY_SECONDS = 120


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


# A LOCATION ON SEVERAL LINES IS "Name\nStreet\nnotes", and joined it is one
# string Photon cannot read. Shift2Bikes, Portland's social-ride calendar,
# writes "Bar Botellon\n606 NE Davis St, Portland, OR 97232\nmeet inside, drink
# wine, eat tapas"; _location makes that one line (its text is the row's place
# name and part of its fingerprint, so it stays as it was), and as the only
# query it placed 21 of the 152 upcoming VEVENTs, 2 of them in Hood River, 93
# km off, for "TBD\nTBD" (measured 2026-10-25, Photon, its 45 distinct
# upcoming LOCATIONs, suffix ", Portland, OR"). Asked line by line instead:
#   * name + ", " + second line placed 74 VEVENTs, every one right but the 3
#     whose lines are placeholders ("TBD\nTBD", "tbd\ntbd", "Online\nTBA");
#   * the street line alone placed 104, but 15 of its 33 distinct answers were
#     not the house: "1120 SE Madison St" came back as 1120 SW 5th Ave, "700 NE
#     Multnomah St 3rd floor" as 700 SW 3rd Ave, "2415 SE 11th Ave" as 206 SE
#     11th Ave, "37th & Skidmore" as a fountain downtown, a "TBD" line as the
#     city centroid;
#   * the name alone placed 123, and 12 of its 34 distinct answers were another
#     place ("NoPo" as NoPo Paws, "Migration Brewing" at its other branch).
# So the street line is asked only when it starts with a house number, and
# kept only when Photon's own answer passes _street_confirms: that keeps the 17
# right answers and none of the 15 wrong ones (8 never asked, 7 refused). The
# name is kept only under _name_confirms, which drops 11 of the 12 wrong names;
# the 12th, the chain's other branch, is never asked for in this feed,
# because its name and street answer first. The joined string stays, third,
# as the query it always was. Placeholder lines and links are never sent
# (Photon answered the one Google Maps link it was sent with a 403). A
# LOCATION on one line is unchanged: none of this is called for it.
def line_attempts(loc: str, lines: List[str]) -> List[Tuple[str, Optional[str]]]:
    """(query, check) for a LOCATION on several lines, best first: name and
    street line, the street line alone, the joined string `loc` (as before),
    the name alone. check is None, "street" (_street_confirms) or "name"
    (_name_confirms); a checked answer counts only when Photon's own
    properties pass it."""
    kept = [l for l in lines if l and "://" not in l
            and not PLACEHOLDER_LOC_RX.match(l) and not _all_placeholders(l)]
    if not kept:
        return [(loc, None)]
    name = None if _HOUSE_NUMBER_RX.match(kept[0]) else kept[0]
    street = next((l for l in kept[(1 if name else 0):] if _HOUSE_NUMBER_RX.match(l)), None)
    out: List[Tuple[str, Optional[str]]] = []
    if name and street:
        out.append((name + ", " + street, None))
    if street:
        out.append((street, "street"))
    out.append((loc, None))
    # Asked alone only when it is a NAME: two words or more, one of them more
    # than a kind of building ("Wilshire Park", not "Community Center" or
    # "NoPo"), without a bracketed aside ("Pioneer Courthouse Square (upper
    # level)").
    alone = re.sub(r"\s+", " ", _BRACKETED_RX.sub(" ", name or "")).strip()
    words = _name_words(alone)
    if len(words) >= 2 and _distinctive(words):
        out.append((alone, "name"))
    return out


# A street's type, after _STREET_ABBR: never the word that says which street.
_STREET_TYPES = frozenset("ave st rd blvd dr pl ln ct ter pkwy hwy sq cir way".split())


def _street_confirms(line: str, props: Dict[str, Any]) -> bool:
    """Photon's answer is the house the line names: the same house number, a
    street sharing one of its name words, and no other direction (NE vs SW)."""
    m = re.match(r"\s*(\d+[A-Za-z]?)\b", line)
    if not m or str(props.get("housenumber") or "").strip().lower() != m.group(1).lower():
        return False
    want = _street_words(line[m.end():].split(",")[0])
    got = _street_words(str(props.get("street") or ""))

    def core(words):
        return {w for w in words if w not in _DIRECTIONS and w not in _STREET_TYPES}
    if not core(want) & core(got):
        return False
    ways, gets = set(want) & _DIRECTIONS, set(got) & _DIRECTIONS
    return not (ways and gets and ways != gets)


def _name_confirms(name: str, props: Dict[str, Any]) -> bool:
    """Photon's answer carries exactly the name's words (see _name_words)."""
    return _name_words(name) == _name_words(str(props.get("name") or ""))


_CHECKS = {"street": _street_confirms, "name": _name_confirms}


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


# `pin_unnamed: false` ON A VENUE BLOCK: the block places an event whose
# LOCATION names the venue, never one that names no place at all. Discovery
# writes it on the block it proposes for a calendar found through a community
# centre, library or social centre (catalog_discover_osm.to_candidate), because
# being found on a centre's website does not make a calendar the centre's own
# programme: a reviewer's sample put about 10 of 30 such calendars under
# another body (a club, a scout group, a church) and 20-25 of 259 wider than
# the building (a city's, a county library system's), and every LOCATION-less
# event of those would have been drawn on one hall (2026-10-12). Such an event
# stays unplaceable, as it was before the block existed, and is counted, so a
# person reading the log knows what deleting the key would add. A block
# without the key (most hand-written ones) is the full fallback, as before.
def _pins_unnamed(venue) -> bool:
    return _venue_pin(venue) and venue.get("pin_unnamed", True) is not False


# Photon requests this process has sent, and how many came back with no point
# (an empty answer or an error). Neither is cached, so the same miss is paid
# again on every run, up to GEOCODE_MAX_NEW (5,000 in CI) at ~1.1 s each; each
# source's line prints its own share (_took).
_PHOTON = {"lookups": 0, "misses": 0}


def _photon_count() -> Tuple[int, int]:
    return _PHOTON["lookups"], _PHOTON["misses"]


def _took(started: float, photon: Tuple[int, int]) -> str:
    """The tail of a source's log line: its seconds, and its Photon requests if any.

    perf_counter, not monotonic: the budget tests drive time.monotonic with a
    list of values, and a clock read here would use one of them up."""
    lookups, misses = _PHOTON["lookups"] - photon[0], _PHOTON["misses"] - photon[1]
    out = f" in {time.perf_counter() - started:.1f}s"
    if lookups:
        out += f"; {lookups} Photon lookup(s), {misses} missed"
    return out


def make_location_geocoder(session, suffix: str):
    cache = _GEO_CACHE
    # WHAT MISSED IS NOT ASKED AGAIN IN THE SAME RUN. A miss is not cached (see
    # _geocode_one), and until this it was re-sent for every VEVENT carrying
    # the same LOCATION: Shift2Bikes' 152 upcoming VEVENTs at 45 LOCATIONs sent
    # 130 Photon requests on every warm run, all misses; with this and
    # line_attempts, 32 (2026-10-25). Per source, and only an answer (an empty
    # one, or one its check refused): an error is still retried.
    missed = set()

    def _key(loc: str) -> str:
        return (loc + "|" + suffix).strip().lower()

    def geocode(loc: str, lines: Optional[List[str]] = None):
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
        # `lines`: the LOCATION's own lines when it has several (line_attempts).
        attempts = (line_attempts(loc, lines) if lines and len(lines) > 1
                    else [(a, None) for a in location_attempts(loc)])
        for attempt, check in attempts:
            if "://" in attempt:
                continue                             # a link is not a place; Photon answers it with a 403
            lat, lon = _geocode_one(attempt, check)
            if lat is not None:
                if attempt != loc:
                    cache[first] = (lat, lon)
                return (lat, lon)
        return (None, None)

    def _geocode_one(loc: str, check: Optional[str] = None):
        # A CHECKED attempt neither reads nor writes its own key: an answer
        # cached unchecked (another feed's same string) has not passed the
        # check, and the hit is kept anyway, under the LOCATION's own key.
        key = _key(loc)
        if check is None and key in cache:
            return cache[key]
        if (key, check) in missed:
            return (None, None)
        if not geocode_allowed():                    # over this run's budget → retry next run
            return (None, None)
        _PHOTON["lookups"] += 1
        try:
            time.sleep(1.1)                          # photon fair use ~1 req/s
            r = session.get("https://photon.komoot.io/api/",
                            params={"q": loc + suffix, "limit": 1}, timeout=20)
            r.raise_for_status()                     # a 502 is not an empty result
            f = (r.json().get("features") or [None])[0]
            if f and check and not _CHECKS[check](loc, f.get("properties") or {}):
                f = None                             # an answer, but not the place this line names
            out = (f["geometry"]["coordinates"][1], f["geometry"]["coordinates"][0]) if f else (None, None)
        except Exception:                            # noqa: BLE001
            _PHOTON["misses"] += 1
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
            if check is None:
                cache[key] = out
        else:
            _PHOTON["misses"] += 1
            missed.add((key, check))
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


def _location_lines(ev: Dict[str, Any]) -> List[str]:
    """A LOCATION's own lines, each cleaned as _location cleans the whole, when
    it has two or more ("Name\nStreet\nnotes"); else []. Only a newline the
    feed escaped counts: Trumba's "<br>" stays the comma _location makes it."""
    raw = _unescape(ev.get("LOCATION", ("", {}))[0] or "")
    if "\n" not in raw.strip():
        return []
    out = []
    for part in raw.split("\n"):
        part = _html.unescape(re.sub(r"<[^>]+>", ", ", part))
        part = re.sub(r"\s*,\s*,+", ", ", re.sub(r"\s+", " ", part)).strip(" ,")
        if part:
            out.append(part)
    return out if len(out) > 1 else []


# A PLACE NAMED ONLY IN THE EVENT'S URL. Texas Parks & Wildlife's calendar
# (plone.app.event) gives none of its 609 VEVENTs a LOCATION or a GEO, and
# every one a URL under /calendar/<folder>/ (read 2026-10-25): 570 sit in 68
# folders that are state parks, whose own pages print their latitude and
# longitude, and 39 in 4 statewide folders (education, recruiting, Texas
# Outdoor Family, events), each at a career fair, a city's pond or a
# different park. The adapter dropped all 609 as unplaceable, the 474 park
# programmes still to come among them. `url_venues` maps a URL pattern to a
# venue block; a VEVENT that names no place and matches none stays
# unplaceable, and the log counts it, so a folder the book lacks shows up.
# Used where the source's `venue` fallback is (no LOCATION, no GEO, not a
# placeholder that says elsewhere), before it; `venue_skip` keeps a title off.
def _url_venue_rules(src: Dict[str, Any]) -> List[Tuple["re.Pattern[str]", Dict[str, Any]]]:
    rules = []
    for rule in src.get("url_venues") or []:
        if not isinstance(rule, dict) or not _venue_pin(rule.get("venue")):
            continue                                  # as location_overrides: no point, no rule
        try:
            rules.append((re.compile(str(rule.get("pattern") or "")), rule["venue"]))
        except re.error as exc:
            raise ValueError(f"{src.get('name', '?')}: invalid url_venues pattern: {exc}") from exc
    return rules


def _url_venue(src: Dict[str, Any], ev: Dict[str, Any],
               rules: List[Tuple["re.Pattern[str]", Dict[str, Any]]]) -> Optional[Dict[str, Any]]:
    """The venue block of the first url_venues rule this VEVENT's URL matches."""
    raw = _html.unescape(_unescape(ev.get("URL", ("", {}))[0] or "")).strip()
    if not raw:
        return None
    try:
        url = urljoin(src["url"], raw)
    except ValueError:
        return None
    return next((venue for rx, venue in rules if rx.search(url)), None)


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


def _geo_point(ev: Dict[str, Any]) -> Optional[Tuple[float, float]]:
    """A VEVENT's GEO as (lat, lon), or None when absent, unreadable or 0;0."""
    if "GEO" not in ev:                               # "lat;lon"
        return None
    try:
        lat, lon = (float(x) for x in ev["GEO"][0].split(";")[:2])
    except Exception:                                 # noqa: BLE001
        return None
    if abs(lat) < 1e-9 and abs(lon) < 1e-9:
        return None                                   # GEO:0;0 is "unset", not the Gulf of Guinea
    return lat, lon


def _geo_agrees(geo: Optional[Tuple[float, float]], venue: Dict[str, Any]) -> bool:
    """No GEO, or one within _GEO_AGREES_M of the venue pin."""
    if geo is None:
        return True
    lat, lon = float(venue["lat"]), float(venue["lon"])
    dy = (geo[0] - lat) * 111_195.0
    dx = (geo[1] - lon) * 111_195.0 * math.cos(math.radians(lat))
    return math.hypot(dx, dy) <= _GEO_AGREES_M


# A LOCATION THAT NAMES THE SOURCE'S OWN VENUE IS AT THE VENUE. A community
# centre's own calendar writes "Polish Home", "Dom Polski - Main Hall" or
# "1714 18th Ave, Seattle, WA 98122" on its events, and until this rule every
# one of those went to Photon, which puts it on the 4-decimal point the
# centre's OpenStreetMap listing is drawn at - the only key the map links an
# event to its venue by - only when it happens to answer with that same OSM
# element (Vashon Senior Center's 3 live rows did, 2026-10-12); a street
# address lands on the address point, a room name anywhere. `location_overrides`
# fixed that one exact string at a time, and 0 of the 172 Google calendars
# used it. So a LOCATION is pinned to the source's `venue` when it names it.
#
# Words first. Case, accents, punctuation, a possessive 's and articles are
# ignored, and every word is one of three kinds:
#   * GENERIC: says what kind of building, never which ("community", "centre",
#     "hall", "association");
#   * a KIND of place ("library", "branch", "park", "school", "church", "club",
#     "booth"...): generic for a venue whose own name carries it ("Atlee
#     Branch Library" is "Atlee"), and ANOTHER place when a LOCATION carries
#     one the venue's name does not ("Queen Anne Library" is not "Queen Anne
#     Community Center", "Green Lake Park" not "Green Lake Community Center");
#   * DISTINCTIVE: everything else, the words that say which venue.
# The venue's names are its `name` and its `aliases` (OpenStreetMap's
# alt_name), each without a bracketed descriptor ("Thame Town Hall (town
# centre)") or what follows a comma ("MAECare, Cranmer Bank" is MAECare; the
# rest is where it is), and each " - " part of those ("Polish Home
# Association - Dom Polski" is two names).
#
#   * by NAME: one comma-, dash- or bracket-separated part of the LOCATION
#     carries every distinctive word of a name and no kind of place the name
#     lacks; when it carries more distinctive words than the name it must also
#     carry one of the name's own generic or kind words ("Vashon Senior Center
#     Dining Room", not "Vashon Senior Living"). A name with ONE distinctive
#     word must be the whole part and carry one of the name's own generic or
#     kind words when it has any ("Ballard Center", not "Ballard" or
#     "Pottery, Ballard"): one word is too
#     easily somebody else's name, or the town's. And no part BEFORE it may
#     name another place ("Marra Farm, South Park"; "Fort Rouge Leisure Centre
#     - MPR, Osborne Library"), unless that part starts with a number.
#   * by STREET: the venue block's `street_hint` ("1714 18th Ave") or its
#     `address`, house number and street, abbreviations folded (Avenue =
#     Ave), and no different direction after it (18th Ave S is another
#     street). `street_hint` is read here and nowhere else: never sent to a
#     geocoder, never written to a row, so a venue pinned to an OpenStreetMap
#     point stays on it (`address` sends the row to the Census re-geocode at
#     the sync).
#   * never by name when the LOCATION carries some OTHER house number: that
#     is a street address of its own, and the geocoder places it as before.
#   * never at all when one part says "not here": "Out of Building, Lee
#     Road", "All Locations, Zionsville Branch", "Offsite - <branch>".
#
# A LOCATION that names anywhere else is geocoded exactly as before; a title
# `venue_skip` places elsewhere is never pinned, however it is located; and a
# publisher's own GEO more than _GEO_AGREES_M from the pin wins over the name.
#
# Checked 2026-10-12 against the config's own ground truth, the 479
# location_overrides strings on the 112 sources that also carry a `venue`:
# of the 474 a person pinned AT the venue the rule names 464, and the 10 it
# misses each put another place before the branch ("Quincy Park, Central
# Library", "Lee Road Children's Garden, Lee Road", "Park Level Room B,
# Coventry Village"), which goes to the geocoder as before; of the 5 pinned
# elsewhere it names 1 ("Fort Rouge - Kitchen, Osborne Library"), and that
# string's exact override, read first, keeps it right. Replayed offline over
# 127 of the 128 non-Google venue feeds (the other refused by robots.txt;
# 18,201 VEVENTs, 11,790 kept) it moved 229 rows, every one onto its own
# venue (Pamunkey's Atlee and Hanover branches 217, MAECare 6, Temple
# University Japan 3, three Singapore libraries 3), lost none, and left 189 to
# the geocoder where the rule off leaves 418: rooms that name no branch,
# outreach, and places that are elsewhere. Without the "not here" guard 19
# Heights Libraries outreach VEVENTs would have been pinned to the branch. The
# 185 live rows of the three neighbourhood Google calendars (Montlake,
# Overlook, Lyndale; 141 with a LOCATION) are all unchanged.
_VENUE_STOP = frozenset("""
    the of and at in on for a an de la le les du des del der die das und et y el
""".split())
_VENUE_GENERIC = frozenset("community center hall association society inc ltd regional public".split())
_VENUE_KIND = frozenset("""
    library branch park school church chapel cathedral mosque synagogue farm field
    garden playground pool museum stadium arena booth club college university
    hospital cemetery beach zoo theater cinema hotel
    biblioteca bibliotheque bibliothek bibliotheek parque parc escuela escola ecole
    schule iglesia igreja eglise kirche kerk teatro museo musee estadio piscina
""".split())
_WORD_FOLD = {
    "centre": "center", "centres": "center", "centers": "center", "halls": "hall",
    "associations": "association", "assn": "association", "assoc": "association",
    "libraries": "library", "branches": "branch", "parks": "park", "schools": "school",
    "churches": "church", "fields": "field", "gardens": "garden", "clubs": "club",
    "pools": "pool", "theatre": "theater", "theatres": "theater", "theaters": "theater",
}
# What a part BEFORE the named one must not be: another place's name.
_ELSEWHERE_KIND = _VENUE_KIND | {"center"}
_STREET_ABBR = {
    "avenue": "ave", "av": "ave", "street": "st", "road": "rd", "boulevard": "blvd",
    "drive": "dr", "place": "pl", "lane": "ln", "court": "ct", "terrace": "ter",
    "parkway": "pkwy", "highway": "hwy", "square": "sq", "circle": "cir",
    "north": "n", "south": "s", "east": "e", "west": "w",
    "northeast": "ne", "northwest": "nw", "southeast": "se", "southwest": "sw",
    "junior": "jr",
}
_DIRECTIONS = frozenset({"n", "s", "e", "w", "ne", "nw", "se", "sw"})
_NAME_PARTS_RX = re.compile(r"\s+[-–—]\s+|[,;:/|()\[\]（）]")
_BRACKETED_RX = re.compile(r"\s*[(\[（][^)\]）]*[)\]）]")
_VENUE_NAME_CACHE: Dict[Tuple[str, ...], List[Tuple[frozenset, frozenset, frozenset]]] = {}
_HOUSE_NUMBER_RX = re.compile(r"\s*\d+[A-Za-z]?(?:-\d+)?\s+\w")     # "1714 18th Ave", "11 Eskdale Drive"
# A publisher's GEO this close to the venue pin is the venue; farther, it is
# somewhere the publisher placed on purpose, and a word match does not move it.
_GEO_AGREES_M = 100
# A part of a LOCATION that says "not here": the ELSEWHERE words below, minus
# the bare countries (Google ends every picked place with ", USA"); an online
# room (ingest drops those first, ONLINE_LOC_RX, but the smoke read asks this
# function directly); and LibCal's "Out of Building", under which Heights
# Libraries' four branch feeds filed 19 outreach VEVENTs (read 2026-10-12).
_NOT_HERE_PART_RX = re.compile(
    r"^\s*(?:off[\s-]?site|outreach|external|bookmobile|various(?: locations)?|multiple locations|"
    r"in the community|community location|outside venue|all (?:library )?(?:locations|branches)|"
    r"out of (?:the )?building|(?:online|virtual)(?: (?:events?|rooms?|programs?|branch|library))?)\s*$",
    re.I)


def _words(text: str) -> List[str]:
    """Lower-case words without accents, possessives or punctuation."""
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    t = re.sub(r"['’`]s\b", "", t)                # Heron's Nest -> heron nest
    t = re.sub(r"['’`]", "", t)
    return [w for w in re.split(r"[\W_]+", t) if w]


def _name_words(text: str) -> frozenset:
    """A name's words, folded ("Centre" = "Center"), articles dropped."""
    return frozenset(_WORD_FOLD.get(w, w) for w in _words(text) if w not in _VENUE_STOP)


def _distinctive(words: frozenset) -> frozenset:
    return frozenset(w for w in words if w not in _VENUE_GENERIC and w not in _VENUE_KIND)


def _venue_names(venue: Dict[str, Any]) -> List[Tuple[frozenset, frozenset, frozenset]]:
    """The venue's names as (distinctive words, generic and kind words, kind
    words) triples: its `name` and `aliases`, each without a bracketed
    descriptor or what follows a comma, and each " - " part of those."""
    aliases = venue.get("aliases")
    names = (str(venue.get("name") or ""),) + tuple(
        str(a) for a in (aliases if isinstance(aliases, list) else []) if a)
    got = _VENUE_NAME_CACHE.get(names)
    if got is None:
        got = []
        for name in names:
            first = _BRACKETED_RX.sub(" ", name).split(",")[0]
            for part in [first] + re.split(r"\s+[-–—]\s+", first):
                words = _name_words(part)
                distinct = _distinctive(words)
                triple = (distinct, words - distinct, words & _VENUE_KIND)
                if distinct and triple not in got:
                    got.append(triple)
        _VENUE_NAME_CACHE[names] = got
    return got


def _street_words(text: str) -> List[str]:
    return [_STREET_ABBR.get(w, w) for w in _words(text)]


def _names_street(hint: str, location: str) -> bool:
    want = _street_words(hint)
    if len(want) < 2 or not want[0][:1].isdigit():
        return False                                  # a house number and a street, or nothing
    for part in location.split(","):
        got = _street_words(part)
        for i in range(len(got) - len(want) + 1):
            if got[i:i + len(want)] == want:
                after = got[i + len(want)] if i + len(want) < len(got) else None
                if after not in _DIRECTIONS:          # "18th Ave S" is not "18th Ave"
                    return True
    return False


def _names_venue(venue: Any, location: Optional[str]) -> bool:
    """True when a LOCATION names the source's own venue (see above)."""
    if not location or not _venue_pin(venue):
        return False
    parts = _NAME_PARTS_RX.split(location)
    # "Out of Building, Noble Neighborhood": the part before the comma says
    # the event is NOT at the branch the part after it names.
    if any(_NOT_HERE_PART_RX.match(p) for p in parts):
        return False
    streets = [s for s in (venue.get("street_hint"), venue.get("address"))
               if isinstance(s, str) and s.strip()]
    if any(_names_street(street, location) for street in streets):
        return True
    # A LOCATION that writes its own house number names THAT address. It is
    # the venue's only when it matched above; otherwise the geocoder places
    # it, as before. "Lyndale Neighborhood Association, 3537 Nicollet Ave S"
    # is an office, not the neighbourhood centroid its source's venue is.
    if any(_HOUSE_NUMBER_RX.match(p) for p in location.split(",")):
        return False
    words = [_name_words(p) for p in parts]
    for distinct, generic, kinds in _venue_names(venue):
        for i, every in enumerate(words):
            got = _distinctive(every)
            if not distinct <= got or (every & _VENUE_KIND) - kinds:
                continue                              # not this name, or another kind of place
            if len(distinct) == 1:
                if got != distinct or (generic and not generic & every):
                    continue                          # one word: the whole part, with its own kind word
            elif got != distinct and generic and not generic & every:
                continue                              # more words, none of the name's own kind words
            if any((words[j] & _ELSEWHERE_KIND) - generic and _distinctive(words[j])
                   for j in range(i) if not re.match(r"\s*\d", parts[j])):
                continue                              # an earlier part names another place
            return True
    return False


def _where(src: Dict[str, Any], location: Optional[str]) -> str:
    """Where ingest_ics puts a VEVENT with this LOCATION and no GEO, in the
    adapter's own order, for the smoke read to print: "online" and
    "unplaceable" are dropped, "override" (an exact location_overrides string)
    and "venue" (no place named, or one that names the venue) are pinned to a
    configured point, "geocode" goes to Photon. A title `venue_skip` matches is
    kept off the venue whatever this says."""
    venue = src.get("venue")
    bare = "venue" if _pins_unnamed(venue) else "unplaceable"
    if location and ONLINE_LOC_RX.search(location):
        return "online"
    if location and (PLACEHOLDER_LOC_RX.match(location) or _all_placeholders(location)):
        elsewhere = bool(ELSEWHERE_LOC_RX.match(location)) or _all_placeholders(location, ELSEWHERE_LOC_RX)
        return "unplaceable" if elsewhere else bare
    if not location:
        return bare
    if _location_override(src, location):
        return "override"
    return "venue" if _names_venue(venue, location) else "geocode"


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
    started, photon = time.perf_counter(), _photon_count()
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
    url_venues = _url_venue_rules(src)
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
    pinned_by_name = 0
    off_venue = 0
    past = 0
    governance = 0
    cancelled = 0
    cancelled_unnamed = 0
    cancelled_title = 0                               # see title_notice
    online = 0
    skipped_title = 0
    is_civic = str(src.get("_found", "")).startswith("civic:")
    unnamed_held = 0                                  # see _pins_unnamed
    pinned_by_url = 0                                 # see _url_venue_rules
    url_unmatched = 0
    # ONE UID, SEVERAL DATES. RFC 5545 names an occurrence by UID plus
    # RECURRENCE-ID; this parser reads no RECURRENCE-ID, and the store keys a
    # row by (source, UID), so every later date of a UID re-keyed the one row:
    # it ended carrying the LAST date's fingerprint and the FIRST date's start,
    # and a date added to the end of the series gives it a new fingerprint,
    # so a new database row, and leaves the old one behind.
    # Repair Cafe Toronto (a candidate) expands two monthly series as 16
    # upcoming RECURRENCE-ID instances of 2 UIDs: 31 VEVENTs kept, 18 rows
    # written, 13 re-keys (2026-10-23, and again 2026-10-25). In a sample of
    # 222 configured feeds read 2026-10-25 (41,109 VEVENTs), one, an Event
    # Organiser site, put 2 upcoming dates under one UID with no RECURRENCE-ID,
    # and none put one UID twice on a date. So the first date of a UID in walk
    # order keeps the bare UID (the key it always had), and each later date is
    # keyed "<UID>/<date>", as mapsee_gcal keys a Calendar API instance; a UID
    # seen twice on one date keeps one key, and the fingerprint merges it.
    uid_dates: Dict[str, Dict[str, str]] = {}
    keyed_by_date = 0
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
        # A TITLE THAT SAYS IT IS OFF, AT A LOCATION THAT IS ONLY A CALL. Anywhere
        # else the store's notice rule reads "CANCELLED - Seminar" in upsert and
        # tombstones "Seminar"; the online skip below runs before upsert, so with
        # no STATUS the row written before ONLINE_LOC_RX knew "On Zoom" stayed live
        # (review, 2026-10-24: a scratch feed tombstoned it at 87a2832 and not at
        # 3fc7a38). So it is a cancellation here, made the way that rule makes
        # one: the event's own title, notice=True. Only for an online LOCATION: a
        # placed one still takes the store's path, and a closure notice or a bare
        # "CANCELLED" names no event and stays an online skip.
        title_notice = (not is_cancelled and bool(strip_notice(title))
                        and bool(ONLINE_LOC_RX.search(_location(ev) or "")))
        is_cancelled = is_cancelled or title_notice
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
        lines = _location_lines(ev)                   # [] unless the LOCATION has several lines
        # An online session is on no map, whatever GEO its branch stamped on it:
        # see ONLINE_LOC_RX. Before GEO is read, because Communico gives these
        # rows the owning branch's coordinates (or 0;0, which the geocoder then
        # guesses at).
        # EXCEPT A CANCELLATION (STATUS, or title_notice above), which goes on to
        # store.cancel below. Every branch of the pattern was added after rows it
        # matches had been written ("On Zoom" on 2026-10-24, with 8 UW rows
        # already pinned), and an upsert cannot delete, so this skip leaves a
        # stored row exactly as it was. If the cancelled VEVENT were skipped too,
        # the publisher's word would be the one word about that row nobody
        # reads: the ics sync has no --retire-absent, and prune_cancelled never
        # hides on prose. The record below is keyed as it was before the pattern
        # matched (title, date and this same LOCATION text), and a tombstone
        # places nothing, so it costs no geocode and no page fetch. A tombstone
        # for a row never written matches no external_id and changes nothing.
        if loc and ONLINE_LOC_RX.search(loc) and not is_cancelled:
            online += 1
            continue
        elsewhere = False
        if loc and (PLACEHOLDER_LOC_RX.match(loc) or _all_placeholders(loc) or _all_placeholder_lines(lines)):
            elsewhere = (bool(ELSEWHERE_LOC_RX.match(loc)) or _all_placeholders(loc, ELSEWHERE_LOC_RX)
                         or _all_placeholder_lines(lines, ELSEWHERE_LOC_RX))
            loc = None                                # "TBD" names no place; see the pattern
        fingerprint_loc = loc
        had_source_location = bool(loc)
        venue = _location_override(src, loc)
        geo = _geo_point(ev)
        # Before GEO, as an exact override is: the venue pin is a checked
        # point, and the only one the map links the venue's events by. But a
        # word match is not a checked string, so the publisher's own GEO wins
        # when it puts the event somewhere else (_GEO_AGREES_M). The row keeps
        # the feed's own LOCATION as its place name - the room and the street
        # people read - and takes only the point.
        named = False
        if venue is None and _names_venue(src.get("venue"), loc) and _geo_agrees(geo, src["venue"]):
            if not _off_venue(src, title):
                venue, named = src["venue"], True
            # Counted for the log only when a row is written: a tombstone places nothing.
            if not is_cancelled:
                if named:
                    pinned_by_name += 1
                else:
                    off_venue += 1                    # named, but its title says elsewhere
        if venue:
            lat, lon = float(venue["lat"]), float(venue["lon"])
            if not named:
                loc = venue.get("name") or loc
        else:
            lat, lon = geo if geo else (None, None)   # GEO:0;0 is "unset", not the Gulf of Guinea
        if venue is None and (lat is None or lon is None) and loc and not is_cancelled:
            # Lines only when there are several: a geocoder other code (and
            # the tests) hands in takes the LOCATION alone.
            lat, lon = geocode(loc, lines) if lines else geocode(loc)
        url_matched = url_missed = False
        if venue is None and lat is None and not loc and not elsewhere and url_venues:
            by_url = _url_venue(src, ev, url_venues)
            url_matched, url_missed = by_url is not None, by_url is None
            if by_url is None:
                pass                                  # the source's `venue`, if any, is next
            elif _off_venue(src, title):
                off_venue += not is_cancelled         # its own title says it is somewhere else
            else:
                venue = by_url
                lat, lon = float(venue["lat"]), float(venue["lon"])
                loc = venue.get("name") or None
                fingerprint_loc = loc
                pinned_by_url += not is_cancelled
        if (venue is None and lat is None and not loc and not elsewhere and not is_cancelled
                and _venue_pin(src.get("venue")) and not _pins_unnamed(src.get("venue"))):
            unnamed_held += 1                         # counted below with the unplaceable
        if (venue is None and lat is None and not loc and not elsewhere and not url_matched
                and _pins_unnamed(src.get("venue"))):
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
            url_unmatched += url_missed               # a place url_venues does not know yet
            continue                                  # nowhere to pin it
        desc = _unescape(ev.get("DESCRIPTION", ("", {}))[0]).strip() or None
        if desc:                                      # ICS descriptions are often HTML-ish — keep them short + plain
            desc = re.sub(r"<[^>]+>", " ", desc)
            desc = re.sub(r"\s+", " ", desc).strip() or None
        attendance_note = src.get("attendance_note")
        if isinstance(attendance_note, str) and attendance_note.strip():
            # The sync caps prose at 800 characters. A publisher's short,
            # verified instruction (for example, how to confirm a recurring
            # event that may be cancelled) belongs before that cap, while the
            # source's own description remains intact after it in the store.
            note = attendance_note.strip()
            desc = f"{note}\n\n{desc}" if desc else note
        url = _event_url(ev, src)
        uid = (ev.get("UID", ("", {}))[0] or "").strip()
        source_id = uid
        if uid:                                       # see uid_dates
            dates = uid_dates.setdefault(uid, {})
            if date_key not in dates:
                dates[date_key] = f"{uid}/{date_key}" if dates else uid
                keyed_by_date += len(dates) > 1 and not is_cancelled
            source_id = dates[date_key]
        nev = NormalizedEvent(
            source=label,
            source_id=source_id or make_fingerprint(title, date_key, fingerprint_loc),
            name=title,
            description=desc,
            start_local=start_local, start_utc=start_utc,
            end_local=end_local, end_utc=end_utc,
            venue_name=loc, latitude=lat, longitude=lon,
            coords_exact=(venue or {}).get("coords_exact") is True,
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
            if cancel is None:
                continue
            if title_notice:
                if cancel(nev, "cancelled in the title", notice=True) == "cancelled":
                    cancelled_title += 1
            elif cancel(nev, "STATUS:CANCELLED") == "cancelled":
                cancelled += 1
            continue
        store.upsert(nev)
        kept += 1
    note = f" ({how})" if how != "200" else ""
    if pinned_by_venue:
        note += f" — {pinned_by_venue} pinned to the source's venue (no LOCATION/GEO)"
    if pinned_by_name:
        note += f" — {pinned_by_name} pinned to the source's venue (LOCATION names it)"
    if pinned_by_url:
        note += f" — {pinned_by_url} pinned by their URL (url_venues)"
    if off_venue:
        note += f" — {off_venue} kept off the venue by venue_skip"
    if unplaceable:
        note += f" — {unplaceable} unplaceable (no LOCATION/GEO)"
        if events and unplaceable >= max(3, len(events) // 2):
            note += "; more than half this feed has no location — check whether the source offers a richer feed"
        if unnamed_held:
            note += (f"; {unnamed_held} of them name no place and the venue block says "
                     f"pin_unnamed false: delete it if this is the venue's own programme")
        if url_unmatched:
            note += f"; {url_unmatched} of them have a URL no url_venues pattern matches"
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
    if cancelled_title:
        note += f"; {cancelled_title} online and cancelled in the title, tombstoned"
    if online:
        note += f"; {online} online (a virtual room, not a place)"
    if skipped_title:
        note += f"; {skipped_title} title-filtered"
    if keyed_by_date:
        note += f"; {keyed_by_date} keyed by date (a UID an earlier date already used)"
    print(f"[ics] {src.get('name', '?')}: kept {kept} of {len(events)} VEVENTs{note}{_took(started, photon)}",
          flush=True)
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


# ...and every LINE of one. Shift2Bikes writes "TBD\nTBD", "TBA\nTBA" and
# "tbd\ntbd", which _location joins to "TBD TBD", which no pattern above
# reads as a placeholder: Photon put "TBD TBD, Portland, OR" on a sculpture
# called "TBD - Female Figure" in Hood River, 93 km away (2026-10-25).
def _all_placeholder_lines(lines: List[str], rx: "re.Pattern[str]" = PLACEHOLDER_LOC_RX) -> bool:
    return len(lines) > 1 and all(rx.match(l) or _all_placeholders(l, rx) for l in lines)


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
# A LOCATION that is only the call's software is the same thing: "On Zoom",
# "Teams", "Online (Zoom)", "Zoom (see registration link)". In a sample of 5,880
# place names (2026-10-23) the first two branches matched 0 of the 10 that were
# only a call and the last two match 9, with 0 false positives; 8 were University
# of Washington ics rows pinned at the campus geocode. The tenth, a Meetup venue
# "ZOOM ROOM", is left out on purpose: Zoom Room is a US chain of dog-agility
# gyms, so "Zoom Room - Fremont" is a place and "room" is not a suffix here.
ONLINE_LOC_RX = re.compile(
    r"^\s*(?:online|virtual)(?:\s+(?:branch|library|room|programs?|events?))?\s*(?:$|[-\u2013\u2014|:,])|"
    r"(?:[-\u2013\u2014|]\s*|\(\s*)(?:online|virtual)(?:\s+(?:room|event|program|class))?\s*\)?\s*$|"
    r"^\s*(?:on\s+|via\s+)?(?:zoom|ms\s+teams|microsoft\s+teams|teams|google\s+meet|webex)\b"
    r"(?:\s+(?:meeting|call|webinar|link))?\s*(?:$|[-\u2013\u2014|:,(])|"
    r"^\s*(?:online|virtual)\s*\(\s*(?:via\s+)?(?:zoom|teams|microsoft teams|google meet|webex)\s*\)\s*$",
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
    # Where the next run starts, as of the last source this one finished: the
    # cursor written at the next checkpoint. None until then, so a run stopped
    # inside its first source leaves the cursor it started from.
    resume: Optional[Tuple[str, int, int]] = None
    dirty = False                                   # the store has rows no checkpoint wrote
    last_save = time.monotonic()

    def checkpoint():
        # The Actions cache is uploaded only after the process exits, so these
        # files are what a cancelled or bounded run leaves for the sync and the
        # next run: the event store, the Photon results and the feed bodies.
        # The cursor goes LAST and only here, so it never names a source past
        # what the saved store holds.
        nonlocal dirty, last_save
        store.save()
        _save_geo_cache(_GEO_CACHE)
        _save_feed_cache(_FEED_CACHE)
        if deadline is not None and resume is not None:
            _save_cursor(a.cursor, *resume)
        dirty, last_save = False, time.monotonic()

    try:
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
            dirty = True
            started, photon = time.perf_counter(), _photon_count()
            try:
                total += ingest_ics(store, session, src, start_offset=offset,
                                    start_kept=kept_before, deadline=deadline)
            except BudgetExpired as exc:
                resume = (src["url"], exc.offset, exc.kept)
                print(f"[ics] budget reached inside {src['name']} at VEVENT {exc.offset}; resuming there next run")
                break
            except Exception as exc:
                # A refused source must not pin the cursor forever.
                print(f"[ics] {src.get('name', '?')} FAILED: {exc}{_took(started, photon)}", flush=True)
            attempted += 1
            resume = (sources[(index + 1) % len(sources)]["url"], 0, 0)
            # NOT after every source. At full size this store is 198.6 MB and
            # 131,065 events, and one save takes 3.1-3.5 s; saved after every
            # source it cost 40.2 min of the 220-min budget on 2026-10-23 and
            # 19.0 on 10-22, time not spent reading feeds. A kill that gets no
            # checkpoint below loses at most this interval, and the cursor,
            # written with the store, re-reads it.
            if time.monotonic() - last_save >= SAVE_EVERY_SECONDS:
                checkpoint()
    finally:
        # The budget, the end of the catalog, a failure, and the SIGINT a
        # cancelled Actions step receives (KeyboardInterrupt).
        if dirty:
            checkpoint()
    print(f"[ics] done: +{total} events processed; store now holds {len(store.records)} unique events.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
