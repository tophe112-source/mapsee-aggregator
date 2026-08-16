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

Events need coordinates to land on the map: a VEVENT GEO property wins;
otherwise the LOCATION string is geocoded via Photon (OSM) — one polite
request per UNIQUE location, cached. Past events and unparseable rows are
skipped; recurring events keep only their base instance if it's upcoming
(Trumba feeds mostly pre-expand occurrences anyway).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from mapsee_geo_budget import geocode_allowed
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

from mapsee_ingest import NormalizedEvent, EventStore, make_fingerprint

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

def _fetch_ics(session, url):
    """GET with revalidation. Returns (text, how) where how is 200/304/reuse."""
    ent = _FEED_CACHE.get(url)
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
            return ent["body"], "reuse"                # network hiccup → last good body
        raise
    now_ts = datetime.now(timezone.utc).timestamp()
    if resp.status_code == 304 and ent and ent.get("body"):
        ent["ts"] = now_ts                             # keep it inside the prune window
        return ent["body"], "304"
    resp.raise_for_status()
    etag, lm = resp.headers.get("ETag"), resp.headers.get("Last-Modified")
    if (etag or lm) and len(resp.text) < FEED_BODY_MAX:
        _FEED_CACHE[url] = {"etag": etag, "lm": lm, "ts": now_ts, "body": resp.text}
    else:
        _FEED_CACHE.pop(url, None)                     # no validator → full fetch every time
    return resp.text, "200"



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
    """Minimal VEVENT extractor: SUMMARY/DTSTART/DTEND/LOCATION/DESCRIPTION/URL/UID/GEO."""
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
        if prop in ("SUMMARY", "LOCATION", "DESCRIPTION", "URL", "UID", "GEO", "DTSTART", "DTEND"):
            cur[prop] = (value, params)
    return events


def make_location_geocoder(session, suffix: str):
    cache = _GEO_CACHE

    def geocode(loc: str):
        key = (loc + "|" + suffix).strip().lower()
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


def ingest_ics(store: EventStore, session, src: Dict[str, Any]) -> int:
    text, how = _fetch_ics(session, src["url"])
    events = parse_ics(text)
    label = "ics:" + src["name"].lower().replace(" ", "-")
    geocode = make_location_geocoder(session, src.get("geocode_suffix", ""))
    now_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    limit = src.get("limit", 500)
    kept = 0
    # A VEVENT with no LOCATION and no GEO cannot be pinned, so it is dropped -
    # correctly, but until this counter it was dropped in SILENCE. Seattle Parks
    # Foundation was publishing 30 events of which 20 had no LOCATION at all,
    # and the only visible symptom was a feed that looked two thirds empty with
    # nothing anywhere saying why. A source that is mostly unplaceable is a
    # source wired up the wrong way (that one wanted the Tribe REST API, which
    # carries venues the iCal export omits), and the log has to be able to say so.
    unplaceable = 0
    for ev in events:
        if kept >= limit:
            break
        title = _unescape(ev.get("SUMMARY", ("", {}))[0]).strip()
        if not title or "DTSTART" not in ev:
            continue
        start_local, start_utc, date_key = _parse_dt(*ev["DTSTART"])
        if not date_key or date_key < now_key:
            continue                                  # past (or unparseable) → skip
        end_local = end_utc = None
        if "DTEND" in ev:
            end_local, end_utc, _ = _parse_dt(*ev["DTEND"])
        loc = _unescape(ev.get("LOCATION", ("", {}))[0]).strip() or None
        if loc:  # Trumba locations can carry HTML ("111 Alamo Plaza<br>San Antonio")
            loc = re.sub(r"<[^>]+>", ", ", loc).replace("&amp;", "&")
            loc = re.sub(r"\s*,\s*,+", ", ", re.sub(r"\s+", " ", loc)).strip(" ,") or None
        lat = lon = None
        if "GEO" in ev:                               # "lat;lon"
            try:
                lat, lon = (float(x) for x in ev["GEO"][0].split(";")[:2])
            except Exception:
                lat = lon = None
        if (lat is None or lon is None) and loc:
            lat, lon = geocode(loc)
        if lat is None or lon is None:
            unplaceable += 1
            continue                                  # nowhere to pin it
        desc = _unescape(ev.get("DESCRIPTION", ("", {}))[0]).strip() or None
        if desc:                                      # ICS descriptions are often HTML-ish — keep them short + plain
            desc = re.sub(r"<[^>]+>", " ", desc)
            desc = re.sub(r"\s+", " ", desc).strip() or None
        url = (ev.get("URL", ("", {}))[0] or "").strip() or None
        uid = (ev.get("UID", ("", {}))[0] or "").strip()
        nev = NormalizedEvent(
            source=label,
            source_id=uid or make_fingerprint(title, date_key, loc),
            name=title,
            description=desc,
            start_local=start_local, start_utc=start_utc,
            end_local=end_local, end_utc=end_utc,
            venue_name=loc, latitude=lat, longitude=lon,
            address=None,
            category=src.get("category"),
            ticket_url=url or src.get("url_home"),   # every event links somewhere - the VEVENT URL, else the calendar's page
        )
        nev.fingerprint = make_fingerprint(title, date_key, loc)
        store.upsert(nev)
        kept += 1
    note = f" ({how})" if how != "200" else ""
    if unplaceable:
        note += f" — {unplaceable} unplaceable (no LOCATION/GEO)"
        if events and unplaceable >= max(3, len(events) // 2):
            note += "; more than half this feed has no location — check whether the source offers a richer feed"
    print(f"[ics] {src.get('name', '?')}: kept {kept} of {len(events)} VEVENTs{note}")
    return kept


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import ICS/iCalendar event feeds into the Mapsee store.")
    ap.add_argument("--config", required=True, help="JSON list of feeds (name, url, …).")
    ap.add_argument("--store", default="mapsee_events.json")
    a = ap.parse_args(argv)

    sources = json.loads(open(a.config, encoding="utf-8").read())
    session = requests.Session()
    session.headers.update({"User-Agent": "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)"})
    store = EventStore(a.store)

    total = 0
    for src in sources:
        try:
            total += ingest_ics(store, session, src)
        except Exception as exc:
            print(f"[ics] {src.get('name', '?')} FAILED: {exc}")
    store.save()
    _save_geo_cache(_GEO_CACHE)
    _save_feed_cache(_FEED_CACHE)
    print(f"[ics] done: +{total} events processed; store now holds {len(store.records)} unique events.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
