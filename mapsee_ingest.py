#!/usr/bin/env python3
"""
mapsee_ingest.py  —  Mapsee unified event ingestor (Phase 0 + Phase 1)
=============================================================================
One command that populates the Mapsee events "database" (a JSON store by
default) from multiple COMPLIANT sources, normalizing and DEDUPING as it goes:

  1. TICKETMASTER Discovery API   — the ticketed/mid-and-up backbone
  2. VENUE iCal / .ics feeds      — indie / DIY long tail (facts are free)
  3. VENUE schema.org/Event JSON-LD embedded in public event pages

The same show appearing on Ticketmaster AND a venue's own calendar collapses
into ONE canonical record (deduped on a name+date+venue fingerprint), while
re-runs never create duplicates. This supersedes the earlier
`mapsee_ingest_ticketmaster.py` (Ticketmaster-only) file.

-----------------------------------------------------------------------------
WHY THESE SOURCES (compliance recap)
-----------------------------------------------------------------------------
Ticketmaster's ToU permit an event-discovery app (link back to buy; cache only
for "reasonable periods"; monetize via the affiliate program). Venue-owned
calendars publish facts (date/venue/time) that aren't copyrightable (Feist);
we take the facts, keep a deep link, and HOTLINK poster images rather than
re-hosting them. SeatGeek / Bandsintown / Songkick bar aggregation in their
ToS, and DICE has no public API — they are left as TODO hooks for later,
partnership-based integration.

-----------------------------------------------------------------------------
REQUIREMENTS
-----------------------------------------------------------------------------
* Python 3.9+
* pip install requests
* Ticketmaster API key (Consumer Key), free & instant:
      https://developer.ticketmaster.com/
      export TICKETMASTER_API_KEY=your_consumer_key
  (Only the Consumer Key is needed for Discovery; the Consumer Secret is for
   the OAuth Commerce/partner APIs and is NOT used here.)

-----------------------------------------------------------------------------
USAGE
-----------------------------------------------------------------------------
  # Ticketmaster only, by city:
  python mapsee_ingest.py --city "Nashville" --store mapsee_events.json

  # Ticketmaster by geo point + radius:
  python mapsee_ingest.py --latlong 36.1627,-86.7816 --radius 25 --unit miles

  # Add venue calendars (repeatable), auto-detected .ics vs HTML page:
  python mapsee_ingest.py --city "Nashville" \
      --ics-url https://thevenue.example/events.ics \
      --page-url https://anothervenue.example/calendar

  # Or list venue feeds in a file (one per line; see --venues-file):
  python mapsee_ingest.py --city "Nashville" --venues-file venues.txt

  venues.txt lines look like (label optional; ics/page auto-detected):
      ics   https://thevenue.example/events.ics
      page  https://anothervenue.example/shows
      https://thirdvenue.example/calendar.ics      # auto → ics

The store is a JSON file you can inspect directly. In production, point
write_to_mapsee() at a real DB (see the TODO near the bottom).

NOTE: needs a valid Ticketmaster key and network access to actually fetch.
=============================================================================
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import logging
import os
import random
import re
import sys
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

try:
    from zoneinfo import ZoneInfo          # stdlib 3.9+, used to resolve ICS TZIDs
except ImportError:                        # pragma: no cover
    ZoneInfo = None  # type: ignore


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
DISCOVERY_URL = "https://app.ticketmaster.com/discovery/v2/events.json"
API_KEY_ENV = "TICKETMASTER_API_KEY"

MIN_REQUEST_INTERVAL_S = 0.5     # ~2 req/sec (Ticketmaster FAQ says 2/sec)
DEFAULT_PAGE_SIZE = 100
MAX_RESULT_WINDOW = 1000         # Discovery only returns the first ~1,000 hits

MAX_RETRIES = 5
BACKOFF_BASE_S = 1.0
BACKOFF_CAP_S = 30.0

USER_AGENT = "MapseeBot/0.2 (+https://mapsee.example; contact@mapsee.example)"

log = logging.getLogger("mapsee.ingest")


# --------------------------------------------------------------------------- #
# Generic helpers
# --------------------------------------------------------------------------- #
def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WS_RE = re.compile(r"\s+")


def normalize_text(value: Optional[str]) -> str:
    """Lower-case, de-punctuate, collapse whitespace, drop a leading 'the '."""
    if not value:
        return ""
    text = value.lower().strip().replace("&", "and")
    text = _PUNCT_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    if text.startswith("the "):
        text = text[4:]
    return text


_GEOHASH_BASE32 = "0123456789bcdefghjkmnpqrstuvwxyz"


def geohash_encode(lat: float, lon: float, precision: int = 9) -> str:
    """Standard geohash encoder (Ticketmaster's geoPoint param expects one)."""
    lat_lo, lat_hi = -90.0, 90.0
    lon_lo, lon_hi = -180.0, 180.0
    bits = [16, 8, 4, 2, 1]
    out: List[str] = []
    bit = ch = 0
    even = True
    while len(out) < precision:
        if even:
            mid = (lon_lo + lon_hi) / 2
            if lon > mid:
                ch |= bits[bit]; lon_lo = mid
            else:
                lon_hi = mid
        else:
            mid = (lat_lo + lat_hi) / 2
            if lat > mid:
                ch |= bits[bit]; lat_lo = mid
            else:
                lat_hi = mid
        even = not even
        if bit < 4:
            bit += 1
        else:
            out.append(_GEOHASH_BASE32[ch]); bit = ch = 0
    return "".join(out)


def make_fingerprint(name: str, local_date: Optional[str], venue_name: Optional[str],
                     city: Optional[str] = None) -> str:
    """Cross-source dedupe key: normalized(headliner) | YYYY-MM-DD | normalized(venue|city)."""
    date_key = (local_date or "")[:10]
    place = normalize_text(venue_name) or normalize_text(city)
    basis = f"{normalize_text(name)}|{date_key}|{place}"
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Normalized event model + deduplicating store
# --------------------------------------------------------------------------- #
@dataclass
class NormalizedEvent:
    source: str
    source_id: str
    name: str
    fingerprint: str = ""
    description: Optional[str] = None
    start_local: Optional[str] = None
    start_utc: Optional[str] = None
    end_local: Optional[str] = None
    end_utc: Optional[str] = None
    timezone: Optional[str] = None
    venue_name: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    address: Optional[str] = None
    city: Optional[str] = None
    region: Optional[str] = None
    country: Optional[str] = None
    postal_code: Optional[str] = None
    category: Optional[str] = None
    promoter: Optional[str] = None
    lineup: List[str] = field(default_factory=list)
    poster_image_url: Optional[str] = None
    ticket_url: Optional[str] = None
    spotify_url: Optional[str] = None      # artist's Spotify page (exact, when a source provides it)
    youtube_url: Optional[str] = None      # artist's YouTube (exact, when a source provides it)

    def source_ref(self) -> Dict[str, Optional[str]]:
        return {"source": self.source, "source_id": self.source_id, "url": self.ticket_url}

    def as_record(self, now: str) -> Dict[str, Any]:
        rec = dataclasses.asdict(self)
        for k in ("source", "source_id", "ticket_url"):
            rec.pop(k, None)
        rec["sources"] = [self.source_ref()]
        rec["first_seen"] = now
        rec["last_seen"] = now
        return rec


_FILLABLE = (
    "description", "start_local", "start_utc", "end_local", "end_utc", "timezone", "venue_name",
    "latitude", "longitude", "address", "city", "region", "country",
    "postal_code", "category", "promoter", "poster_image_url",
    "spotify_url", "youtube_url",
)


class EventStore:
    """JSON-file store that dedupes on fingerprint (primary) and (source, source_id) (guard)."""

    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.records: Dict[str, Dict[str, Any]] = {}
        self.source_to_fp: Dict[Tuple[str, str], str] = {}
        self.stats = {"added": 0, "merged": 0, "updated": 0}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            log.info("No existing store at %s — starting fresh.", self.path)
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Could not read store (%s); starting fresh.", exc)
            return
        for rec in data.get("events", []):
            fp = rec.get("fingerprint")
            if not fp:
                continue
            self.records[fp] = rec
            for ref in rec.get("sources", []):
                key = (ref.get("source"), ref.get("source_id"))
                if key[0] is not None and key[1] is not None:
                    self.source_to_fp[key] = fp
        log.info("Loaded %d existing events from %s", len(self.records), self.path)

    def save(self) -> None:
        payload = {
            "_meta": {"version": 1, "updated": iso_now(), "count": len(self.records)},
            "events": list(self.records.values()),
        }
        self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        log.info("Saved %d events to %s", len(self.records), self.path)

    def _fill_missing(self, rec: Dict[str, Any], ev: NormalizedEvent) -> None:
        for f in _FILLABLE:
            if not rec.get(f):
                val = getattr(ev, f)
                if val:
                    rec[f] = val
        if ev.lineup and not rec.get("lineup"):
            rec["lineup"] = ev.lineup

    def _add_source_ref(self, rec: Dict[str, Any], ev: NormalizedEvent) -> None:
        ref = ev.source_ref()
        for existing in rec.setdefault("sources", []):
            if existing.get("source") == ref["source"] and existing.get("source_id") == ref["source_id"]:
                existing.update(ref)
                return
        rec["sources"].append(ref)

    def upsert(self, ev: NormalizedEvent) -> str:
        now = iso_now()
        key = (ev.source, ev.source_id)

        # 1) exact source event seen before -> update in place
        if key in self.source_to_fp:
            old_fp = self.source_to_fp[key]
            rec = self.records.get(old_fp)
            if rec is not None:
                if old_fp != ev.fingerprint:
                    self.records.pop(old_fp, None)
                    if ev.fingerprint in self.records:
                        rec = self.records[ev.fingerprint]
                    else:
                        rec["fingerprint"] = ev.fingerprint
                        self.records[ev.fingerprint] = rec
                    self.source_to_fp[key] = ev.fingerprint
                self._add_source_ref(rec, ev)
                self._fill_missing(rec, ev)
                rec["last_seen"] = now
                self.stats["updated"] += 1
                return "updated"

        # 2) same logical event from another source -> merge
        if ev.fingerprint in self.records:
            rec = self.records[ev.fingerprint]
            self._add_source_ref(rec, ev)
            self._fill_missing(rec, ev)
            rec["last_seen"] = now
            self.source_to_fp[key] = ev.fingerprint
            self.stats["merged"] += 1
            return "merged"

        # 3) brand new
        self.records[ev.fingerprint] = ev.as_record(now)
        self.source_to_fp[key] = ev.fingerprint
        self.stats["added"] += 1
        return "added"


# --------------------------------------------------------------------------- #
# HTTP: rate limiting + retry/backoff (shared by all sources)
# --------------------------------------------------------------------------- #
class RateLimiter:
    def __init__(self, min_interval_s: float) -> None:
        self.min_interval_s = min_interval_s
        self._last = 0.0

    def wait(self) -> None:
        now = time.monotonic()
        delta = now - self._last
        if delta < self.min_interval_s:
            time.sleep(self.min_interval_s - delta)
        self._last = time.monotonic()


def _backoff_seconds(attempt: int) -> float:
    return min(BACKOFF_BASE_S * (2 ** (attempt - 1)), BACKOFF_CAP_S) + random.uniform(0, 0.5)


def http_get(session: requests.Session, url: str, limiter: RateLimiter,
             params: Optional[Dict[str, Any]] = None) -> requests.Response:
    """GET with polite spacing, 429 (Retry-After) handling, and 5xx/network retries."""
    for attempt in range(1, MAX_RETRIES + 1):
        limiter.wait()
        try:
            resp = session.get(url, params=params, timeout=30)
        except requests.RequestException as exc:
            wait = _backoff_seconds(attempt)
            log.warning("Network error (%s); retry %d/%d in %.1fs", exc, attempt, MAX_RETRIES, wait)
            time.sleep(wait)
            continue
        if resp.status_code == 200:
            return resp
        if resp.status_code == 429:
            ra = resp.headers.get("Retry-After")
            wait = float(ra) if (ra and ra.isdigit()) else _backoff_seconds(attempt)
            log.warning("Rate limited (429) on %s; backing off %.1fs", url, wait)
            time.sleep(wait)
            continue
        if 500 <= resp.status_code < 600:
            wait = _backoff_seconds(attempt)
            log.warning("Server error %d on %s; retry %d/%d in %.1fs",
                        resp.status_code, url, attempt, MAX_RETRIES, wait)
            time.sleep(wait)
            continue
        raise RuntimeError(f"HTTP {resp.status_code} for {url}: {resp.text[:300]}")
    raise RuntimeError(f"Giving up on {url} after {MAX_RETRIES} retries.")


# =========================================================================== #
# SOURCE 1 — TICKETMASTER DISCOVERY API
# =========================================================================== #
def pick_best_image(images: Iterable[Dict[str, Any]]) -> Optional[str]:
    """Highest-resolution poster from Ticketmaster images[] (prefer non-fallback, max area)."""
    best_url: Optional[str] = None
    best_area = -1
    best_is_fallback = True
    for img in images or []:
        url = img.get("url")
        if not url:
            continue
        area = (_to_float(img.get("width")) or 0) * (_to_float(img.get("height")) or 0)
        is_fallback = bool(img.get("fallback", False))
        if (not is_fallback and best_is_fallback) or (is_fallback == best_is_fallback and area > best_area):
            best_url, best_area, best_is_fallback = url, area, is_fallback
    return best_url


def _primary_segment(raw: Dict[str, Any]) -> Optional[str]:
    """Ticketmaster top-level segment: Music / Sports / Arts & Theatre / Film / Miscellaneous."""
    classifications = raw.get("classifications", []) or []
    primary = (next((c for c in classifications if c.get("primary")), None)
               or (classifications[0] if classifications else None))
    return (primary.get("segment") or {}).get("name") if primary else None


def _fmt_price(price_ranges) -> Optional[str]:
    """Approximate ticket price like '$39' or '$29-$59' from Discovery priceRanges."""
    if not price_ranges:
        return None
    pr = next((p for p in price_ranges if p.get("type") == "standard"), price_ranges[0])
    lo, hi, cur = pr.get("min"), pr.get("max"), (pr.get("currency") or "USD")
    if lo is None:
        return None
    sym = "$" if cur == "USD" else f"{cur} "
    lo_i, hi_i = round(lo), round(hi if hi is not None else lo)
    return f"{sym}{lo_i}" if lo_i == hi_i else f"{sym}{lo_i}-{sym}{hi_i}"


def _primary_genre(raw: Dict[str, Any]) -> Optional[str]:
    """Specific genre from the primary classification, e.g. 'Alternative Rock'."""
    for c in raw.get("classifications", []) or []:
        if c.get("primary"):
            for key in ("subGenre", "genre"):
                name = (c.get(key) or {}).get("name")
                if name and name not in ("Undefined", "Other"):
                    return name
    return None


def _tm_description(raw: Dict[str, Any], lineup) -> Optional[str]:
    """Rich event description: blurb + approx price + genre + lineup."""
    segs = []
    blurb = [p for p in (raw.get("info"), raw.get("pleaseNote")) if p]
    if blurb:
        segs.append("\n\n".join(blurb))
    meta = []
    price = _fmt_price(raw.get("priceRanges") or [])
    if price:
        meta.append(f"Approx. price: {price}")
    genre = _primary_genre(raw)
    if genre:
        meta.append(genre)
    if meta:
        segs.append(" · ".join(meta))
    if len(lineup) > 1:
        segs.append("Lineup: " + ", ".join(lineup[:8]))
    return "\n\n".join(segs) if segs else None


def _tm_external_links(attractions):
    """(spotify, youtube) from the headliner attraction's externalLinks — TM ships
    these for music acts, so exact 'listen' links come free with the event."""
    for a in attractions or []:
        links = a.get("externalLinks") or {}
        sp = ((links.get("spotify") or [{}])[0] or {}).get("url")
        yt = ((links.get("youtube") or [{}])[0] or {}).get("url")
        if sp or yt:
            return sp, yt
    return None, None


def parse_ticketmaster_event(raw: Dict[str, Any]) -> NormalizedEvent:
    embedded = raw.get("_embedded", {}) or {}
    attractions = embedded.get("attractions", []) or []
    lineup = [a.get("name") for a in attractions if a.get("name")]
    spotify_url, youtube_url = _tm_external_links(attractions)
    title = raw.get("name") or (lineup[0] if lineup else "Untitled event")
    headliner = lineup[0] if lineup else title

    dates = raw.get("dates", {}) or {}
    start = dates.get("start", {}) or {}
    local_date = start.get("localDate")
    local_time = start.get("localTime")
    start_local = (f"{local_date}T{local_time}" if local_time else local_date) if local_date else None

    venues = embedded.get("venues", []) or []
    v = venues[0] if venues else {}
    loc = v.get("location", {}) or {}

    segment = _primary_segment(raw)              # Ticketmaster segment -> Mapsee category
    ev = NormalizedEvent(
        source="ticketmaster",
        source_id=str(raw.get("id")),
        name=title,
        description=_tm_description(raw, lineup),   # blurb + approx price + genre + lineup
        start_local=start_local,
        start_utc=start.get("dateTime"),
        timezone=dates.get("timezone"),
        venue_name=v.get("name"),
        latitude=_to_float(loc.get("latitude")),
        longitude=_to_float(loc.get("longitude")),
        address=(v.get("address", {}) or {}).get("line1"),
        city=(v.get("city", {}) or {}).get("name"),
        region=(v.get("state", {}) or {}).get("stateCode"),
        country=(v.get("country", {}) or {}).get("countryCode"),
        postal_code=v.get("postalCode"),
        category=segment,
        promoter=(raw.get("promoter") or {}).get("name"),
        lineup=lineup,
        poster_image_url=pick_best_image(raw.get("images", [])),
        ticket_url=raw.get("url"),
        spotify_url=spotify_url,
        youtube_url=youtube_url,
    )
    ev.fingerprint = make_fingerprint(headliner, local_date, v.get("name"),
                                      (v.get("city", {}) or {}).get("name"))
    return ev


def build_tm_params(args: argparse.Namespace, api_key: str) -> Dict[str, Any]:
    params: Dict[str, Any] = {
        "apikey": api_key,
        "sort": "date,asc",
        "locale": "*",
    }
    if args.classification:                      # omit entirely => ALL categories
        params["classificationName"] = args.classification
    if args.latlong:
        try:
            lat_s, lon_s = args.latlong.split(",")
            params["geoPoint"] = geohash_encode(float(lat_s), float(lon_s), 9)
        except ValueError:
            sys.exit("--latlong must look like  36.1627,-86.7816")
        params["radius"] = str(args.radius)
        params["unit"] = args.unit
    elif args.city:
        params["city"] = args.city
    else:
        return {}  # no location -> caller skips Ticketmaster
    if args.country:
        params["countryCode"] = args.country
    params["startDateTime"] = args.start or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if args.end:
        params["endDateTime"] = args.end
    elif getattr(args, "within_days", 0):
        end = datetime.now(timezone.utc) + timedelta(days=args.within_days)
        params["endDateTime"] = end.strftime("%Y-%m-%dT%H:%M:%SZ")
    return params


def ingest_ticketmaster(store: EventStore, session: requests.Session, limiter: RateLimiter,
                        args: argparse.Namespace, api_key: str) -> int:
    base = build_tm_params(args, api_key)
    if not base:
        log.info("No --city/--latlong given; skipping Ticketmaster.")
        return 0
    page_size = max(1, min(args.size, 199))
    processed = 0
    page = 0
    while True:
        data = http_get(session, DISCOVERY_URL, limiter, dict(base, size=page_size, page=page)).json()
        events = (data.get("_embedded", {}) or {}).get("events", []) or []
        if not events:
            break
        for raw in events:
            ev = parse_ticketmaster_event(raw)
            if not ev.source_id or ev.source_id == "None":
                continue
            store.upsert(ev)
            processed += 1
        info = data.get("page", {}) or {}
        total_pages = int(info.get("totalPages", 0) or 0)
        current = int(info.get("number", page) or 0)
        log.info("[ticketmaster] page %d/%s (%d processed)", current + 1, total_pages or "?", processed)
        if current + 1 >= total_pages:
            break
        if (page + 1) * page_size >= MAX_RESULT_WINDOW:
            log.warning("[ticketmaster] hit ~%d-result cap; slice by date to fetch more.", MAX_RESULT_WINDOW)
            break
        if args.max_pages and (page + 1) >= args.max_pages:
            break
        page += 1
    return processed


# =========================================================================== #
# SOURCE 2 — VENUE iCal / .ics FEEDS
# =========================================================================== #
def _ics_unfold(text: str) -> List[str]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines: List[str] = []
    for line in text.split("\n"):
        if line[:1] in (" ", "\t") and lines:
            lines[-1] += line[1:]          # RFC 5545 line unfolding
        else:
            lines.append(line)
    return lines


def _ics_prop(line: str) -> Optional[Tuple[str, Dict[str, str], str]]:
    if ":" not in line:
        return None
    head, value = line.split(":", 1)
    bits = head.split(";")
    name = bits[0].upper()
    params: Dict[str, str] = {}
    for p in bits[1:]:
        if "=" in p:
            k, val = p.split("=", 1)
            params[k.upper()] = val.strip('"')
    return name, params, value


def _ics_unescape(s: str) -> str:
    return (s.replace("\\n", "\n").replace("\\N", "\n")
             .replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\"))


def parse_ics(text: str) -> List[Dict[str, Dict[str, Any]]]:
    """Return a list of VEVENTs, each a dict of PROP -> {'params':..., 'value':...}."""
    events: List[Dict[str, Dict[str, Any]]] = []
    cur: Optional[Dict[str, Dict[str, Any]]] = None
    for line in _ics_unfold(text):
        if line == "BEGIN:VEVENT":
            cur = {}
        elif line == "END:VEVENT":
            if cur is not None:
                events.append(cur)
            cur = None
        elif cur is not None:
            parsed = _ics_prop(line)
            if parsed:
                name, params, value = parsed
                cur[name] = {"params": params, "value": value}
    return events


def _parse_ics_dt(value: str, params: Dict[str, str]):
    """Return (start_local, start_utc, date_key, tz_name) from an ICS DTSTART value."""
    v = value.strip()
    if params.get("VALUE") == "DATE" or (len(v) == 8 and "T" not in v):
        d = f"{v[0:4]}-{v[4:6]}-{v[6:8]}"
        return (d, None, d, None)
    is_utc = v.endswith("Z")
    core = v[:-1] if is_utc else v
    try:
        dt = datetime.strptime(core, "%Y%m%dT%H%M%S")
    except ValueError:
        d = f"{v[0:4]}-{v[4:6]}-{v[6:8]}" if len(v) >= 8 else v
        return (None, None, d, None)
    if is_utc:
        dt = dt.replace(tzinfo=timezone.utc)
        us = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        return (None, us, dt.date().isoformat(), "UTC")
    tzid = params.get("TZID")
    if tzid and ZoneInfo is not None:
        try:
            local = dt.replace(tzinfo=ZoneInfo(tzid))
            us = local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            return (dt.isoformat(), us, dt.date().isoformat(), tzid)
        except Exception:
            pass
    return (dt.isoformat(), None, dt.date().isoformat(), tzid)   # floating / unresolved tz


def ics_vevent_to_event(v: Dict[str, Dict[str, Any]], source_label: str) -> Optional[NormalizedEvent]:
    def val(name: str) -> Optional[str]:
        node = v.get(name)
        return node["value"] if node else None

    summary = (_ics_unescape(val("SUMMARY") or "")).strip() or "Untitled event"
    location = (_ics_unescape(val("LOCATION") or "")).strip() or None
    geo = val("GEO")
    lat = lon = None
    if geo and ";" in geo:
        a, b = geo.split(";", 1)
        lat, lon = _to_float(a), _to_float(b)

    dtstart = v.get("DTSTART")
    if dtstart:
        start_local, start_utc, date_key, tz = _parse_ics_dt(dtstart["value"], dtstart["params"])
    else:
        start_local = start_utc = date_key = tz = None

    venue_name = location.split(",")[0].strip() if location else None
    ev = NormalizedEvent(
        source=source_label,
        source_id=val("UID") or "",
        name=summary,
        description=(_ics_unescape(val("DESCRIPTION") or "")).strip() or None,
        start_local=start_local, start_utc=start_utc, timezone=tz,
        venue_name=venue_name, latitude=lat, longitude=lon, address=location,
        ticket_url=val("URL"),
    )
    if not ev.source_id:
        ev.source_id = hashlib.sha1(f"{summary}|{date_key}|{location}".encode("utf-8")).hexdigest()[:16]
    ev.fingerprint = make_fingerprint(summary, date_key, venue_name)
    # TODO(geo): if lat/lon is None, geocode the venue address once and cache (see geocode_venue()).
    return ev


def ingest_ics(store: EventStore, session: requests.Session, limiter: RateLimiter,
               url: str, music_only: bool) -> int:
    label = f"venue:{urllib.parse.urlparse(url).netloc}"
    text = http_get(session, url, limiter).text
    count = 0
    for vevent in parse_ics(text):
        ev = ics_vevent_to_event(vevent, label)
        if ev is None:
            continue
        if music_only and not _looks_like_music(ev):
            continue
        store.upsert(ev)
        count += 1
    log.info("[ics] %s -> %d events", url, count)
    return count


# =========================================================================== #
# SOURCE 3 — VENUE schema.org/Event JSON-LD (embedded in event pages)
# =========================================================================== #
_JSONLD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)


def _iter_jsonld_nodes(node: Any) -> Iterator[Dict[str, Any]]:
    if isinstance(node, list):
        for x in node:
            yield from _iter_jsonld_nodes(x)
    elif isinstance(node, dict):
        if "@graph" in node:
            yield from _iter_jsonld_nodes(node["@graph"])
        yield node


def _types_of(obj: Dict[str, Any]) -> List[str]:
    t = obj.get("@type")
    return [x for x in (t if isinstance(t, list) else [t]) if isinstance(x, str)]


def _is_event(obj: Dict[str, Any]) -> bool:
    return any(t.endswith("Event") for t in _types_of(obj))


def extract_jsonld_events(html: str) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for block in _JSONLD_RE.findall(html):
        block = block.strip()
        if not block:
            continue
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        for node in _iter_jsonld_nodes(data):
            if _is_event(node):
                events.append(node)
    return events


def _extract_image(img: Any) -> Optional[str]:
    if not img:
        return None
    if isinstance(img, str):
        return img
    if isinstance(img, dict):
        return img.get("url") or img.get("contentUrl")
    if isinstance(img, list):
        for x in img:
            u = _extract_image(x)
            if u:
                return u
    return None


def _extract_location(loc: Any):
    """Return (venue_name, address, lat, lon) from a schema.org location value."""
    if isinstance(loc, list):
        loc = loc[0] if loc else None
    if isinstance(loc, str):
        return (loc, None, None, None)
    if isinstance(loc, dict):
        name = loc.get("name")
        addr = loc.get("address")
        if isinstance(addr, dict):
            addr = ", ".join(str(addr[k]) for k in
                             ("streetAddress", "addressLocality", "addressRegion", "postalCode")
                             if addr.get(k)) or None
        geo = loc.get("geo") or {}
        lat = _to_float(geo.get("latitude")) if isinstance(geo, dict) else None
        lon = _to_float(geo.get("longitude")) if isinstance(geo, dict) else None
        return (name, addr if isinstance(addr, str) else None, lat, lon)
    return (None, None, None, None)


def _extract_performers(obj: Dict[str, Any]) -> List[str]:
    perf = obj.get("performer") or obj.get("performers")
    out: List[str] = []
    if isinstance(perf, dict) and perf.get("name"):
        out.append(perf["name"])
    elif isinstance(perf, list):
        for p in perf:
            if isinstance(p, dict) and p.get("name"):
                out.append(p["name"])
            elif isinstance(p, str):
                out.append(p)
    elif isinstance(perf, str):
        out.append(perf)
    return out


def _iso_parts(s: Any):
    """Return (start_local, start_utc, date_key, tz) from a schema.org date string."""
    if not s or not isinstance(s, str):
        return (None, None, None, None)
    s = s.strip()
    if len(s) == 10 and s[4] == "-":
        return (s, None, s, None)
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return (s, None, s[:10], None)
    if dt.tzinfo is not None:
        us = dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return (dt.isoformat(), us, dt.date().isoformat(), None)
    return (dt.isoformat(), None, dt.date().isoformat(), None)


SCHEMA_TYPE_CATEGORY = {
    "MusicEvent": "Music", "SportsEvent": "Sports", "TheaterEvent": "Arts & Theatre",
    "DanceEvent": "Arts & Theatre", "ScreeningEvent": "Film", "ComedyEvent": "Comedy",
    "Festival": "Festival", "FoodEvent": "Food", "ChildrensEvent": "Family",
}


def jsonld_to_event(obj: Dict[str, Any], source_label: str, page_url: str) -> Optional[NormalizedEvent]:
    name = obj.get("name")
    if isinstance(name, list):
        name = name[0] if name else None
    if not name:
        return None
    desc = obj.get("description")
    if isinstance(desc, list):
        desc = desc[0] if desc else None

    start_local, start_utc, date_key, tz = _iso_parts(obj.get("startDate"))
    venue_name, address, lat, lon = _extract_location(obj.get("location"))
    lineup = _extract_performers(obj)
    url = obj.get("url") or page_url
    sid = obj.get("@id") or url or hashlib.sha1(f"{name}|{date_key}".encode("utf-8")).hexdigest()[:16]

    ev = NormalizedEvent(
        source=source_label, source_id=str(sid), name=str(name),
        description=str(desc) if desc else None,
        start_local=start_local, start_utc=start_utc, timezone=tz,
        venue_name=venue_name, latitude=lat, longitude=lon, address=address,
        category=next((SCHEMA_TYPE_CATEGORY[t] for t in _types_of(obj) if t in SCHEMA_TYPE_CATEGORY), None),
        lineup=lineup, poster_image_url=_extract_image(obj.get("image")), ticket_url=url,
    )
    ev.fingerprint = make_fingerprint(lineup[0] if lineup else str(name), date_key, venue_name)
    return ev


def ingest_jsonld(store: EventStore, session: requests.Session, limiter: RateLimiter,
                  url: str, music_only: bool) -> int:
    label = f"venue:{urllib.parse.urlparse(url).netloc}"
    html = http_get(session, url, limiter).text
    count = 0
    for obj in extract_jsonld_events(html):
        ev = jsonld_to_event(obj, label, url)
        if ev is None:
            continue
        # For JSON-LD we can filter precisely on the declared @type.
        if music_only and "MusicEvent" not in _types_of(obj) and not ev.lineup:
            continue
        store.upsert(ev)
        count += 1
    log.info("[jsonld] %s -> %d events", url, count)
    return count


_MUSIC_HINTS = ("concert", "live music", "band", "dj", "tour", "gig", "singer",
                "songwriter", "orchestra", "acoustic", "jazz", "hip hop", "rock")


def _looks_like_music(ev: NormalizedEvent) -> bool:
    """Cheap heuristic for ICS feeds (which lack a category field)."""
    blob = " ".join(filter(None, [ev.name, ev.description])).lower()
    return bool(ev.lineup) or any(h in blob for h in _MUSIC_HINTS)


# =========================================================================== #
# TODO hooks — future sources & production write path
# =========================================================================== #
def fetch_seatgeek_events(*_a, **_k) -> List[NormalizedEvent]:
    """TODO: SeatGeek. ToS (Mar 2025) bar 'directory'/'competitive' use — partner deal required."""
    raise NotImplementedError("SeatGeek not enabled (partner terms required).")


def fetch_bandsintown_events(*_a, **_k) -> List[NormalizedEvent]:
    """TODO: Bandsintown. §3g bars aggregation — partner deal required. (Artist API still useful
    for enrichment: it returns the MusicBrainz MBID, the join key for genres.)"""
    raise NotImplementedError("Bandsintown not enabled (partner terms required).")


def geocode_venue(address: Optional[str]):
    """TODO: geocode a venue once and cache the lat/lon (Nominatim self-hosted, or Google/Mapbox
    free tier). Venues repeat across events, so cache by normalized address."""
    return (None, None)


def write_to_mapsee(records: List[Dict[str, Any]], db_path: str = "mapsee.db") -> int:
    """Upsert normalized events into Mapsee's production DB (SQLite, keyed on `fingerprint`).
    Implemented in mapsee_db.py; swap that module for Postgres + PostGIS at scale.
    Poster images stay as hotlinks unless you have a license to cache them."""
    from mapsee_db import write_to_mapsee as _write
    return _write(records, db_path)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _read_venues_file(path: str) -> List[Tuple[str, str]]:
    """Parse a venues file into (kind, url) pairs. kind in {'ics','page'}; auto-detected if omitted."""
    out: List[Tuple[str, str]] = []
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[0].lower() in ("ics", "page"):
            kind, url = parts[0].lower(), parts[1].strip()
        else:
            url = line
            kind = "ics" if url.lower().endswith(".ics") else "page"
        out.append((kind, url))
    return out


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Mapsee unified event ingestor (Ticketmaster + venue calendars).")
    loc = p.add_argument_group("Ticketmaster location (choose one; omit both to skip Ticketmaster)")
    loc.add_argument("--city", help='City name, e.g. "Nashville".')
    loc.add_argument("--latlong", help='Geo point "lat,lon", e.g. 36.1627,-86.7816.')
    p.add_argument("--radius", type=int, default=25)
    p.add_argument("--unit", choices=["miles", "km"], default="miles")
    p.add_argument("--classification", default=None,
                   help="Discovery classificationName, e.g. 'music'. OMIT to ingest ALL public categories.")
    p.add_argument("--country", help='Optional ISO country code, e.g. "US".')
    p.add_argument("--start", help="Only events on/after this UTC ISO time (default: now).")
    p.add_argument("--end", help="Only events on/before this UTC ISO time.")
    p.add_argument("--within-days", type=int, default=0,
                   help="Only events within the next N days (0 = no end cap). e.g. 90 for ~3 months.")
    p.add_argument("--size", type=int, default=DEFAULT_PAGE_SIZE, help="Ticketmaster page size (max 199).")
    p.add_argument("--max-pages", type=int, default=0, help="Cap Ticketmaster pages (0 = no cap).")

    ven = p.add_argument_group("Venue calendars (repeatable)")
    ven.add_argument("--ics-url", action="append", default=[], help="A venue .ics/iCal feed URL.")
    ven.add_argument("--page-url", action="append", default=[], help="A venue page URL with schema.org JSON-LD.")
    ven.add_argument("--venues-file", help="File of venue feeds (lines: 'ics <url>' / 'page <url>' / auto).")
    ven.add_argument("--music-only", action="store_true",
                     help="Filter venue events to likely-music only (JSON-LD MusicEvent / ICS keyword heuristic).")

    p.add_argument("--store", default="mapsee_events.json", help="Path to the JSON event store.")
    p.add_argument("--sqlite-db", help="Also upsert results into this SQLite production DB (see mapsee_db.py).")
    p.add_argument("--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s", datefmt="%H:%M:%S",
    )

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    limiter = RateLimiter(MIN_REQUEST_INTERVAL_S)
    store = EventStore(args.store)

    # --- Source 1: Ticketmaster (only if a location AND a key are present) ---
    api_key = os.environ.get(API_KEY_ENV)
    if (args.city or args.latlong):
        if not api_key:
            log.warning("Location given but %s not set — skipping Ticketmaster. "
                        "Get a free key at https://developer.ticketmaster.com/", API_KEY_ENV)
        else:
            try:
                n = ingest_ticketmaster(store, session, limiter, args, api_key)
                log.info("Ticketmaster: processed %d events.", n)
            except RuntimeError as exc:
                log.error("Ticketmaster ingestion failed: %s", exc)

    # --- Sources 2 & 3: venue calendars ---
    venue_feeds: List[Tuple[str, str]] = [("ics", u) for u in args.ics_url] + \
                                         [("page", u) for u in args.page_url]
    if args.venues_file:
        venue_feeds += _read_venues_file(args.venues_file)

    for kind, url in venue_feeds:
        try:
            if kind == "ics":
                ingest_ics(store, session, limiter, url, args.music_only)
            else:
                ingest_jsonld(store, session, limiter, url, args.music_only)
        except RuntimeError as exc:
            log.error("Venue feed failed (%s): %s", url, exc)

    store.save()
    if args.sqlite_db:
        n = write_to_mapsee(list(store.records.values()), args.sqlite_db)
        log.info("Synced %d events into SQLite production DB %s", n, args.sqlite_db)
    s = store.stats
    log.info("Done. added %d, merged %d (cross-source), updated %d (re-runs). "
             "Store now holds %d unique events at %s.",
             s["added"], s["merged"], s["updated"], len(store.records), args.store)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

 