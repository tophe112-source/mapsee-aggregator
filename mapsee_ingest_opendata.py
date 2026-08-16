#!/usr/bin/env python3
"""
mapsee_ingest_opendata.py — import city OPEN-DATA event calendars into the Mapsee
store, normalized + deduped alongside Ticketmaster and venue feeds.

Many US cities publish events on Socrata portals (data.<city>.gov) as JSON REST
endpoints. Each dataset uses different column names, so you describe each one in a
config file (opendata_sources.json) — see opendata_sources.example.json.

    python mapsee_ingest_opendata.py --config opendata_sources.json --store mapsee_events.json

Then the normal sync uploads them (and batch-geocodes their addresses):

    python mapsee_supabase_sync.py --store mapsee_events.json

Config per source (JSON):
    name         label for provenance (source = "opendata:<name>")
    url          Socrata resource JSON endpoint (…/resource/xxxx-xxxx.json)
    app_token    optional Socrata app token (raises the rate limit); null if none
    category     optional fixed Mapsee category KEY for the whole dataset
                 (community / outdoors / arts / music / sports / market / kids / learning / party)
    where        optional SoQL $where filter; "{now}" is replaced with the current UTC time
    limit        max rows to pull (default 1000)
    map          which columns hold each field:
                 id, title, description, start, end, venue, address, url,
                 lat, lon   (separate numeric columns), and/or
                 geo        (a Socrata point/location column — GeoJSON Point or
                            {latitude, longitude})

Data licence note: municipal open data is typically public domain / open licence —
still keep attribution and a link back where the dataset provides one.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Optional

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

# Reuse the model + dedup store from the main ingester.
from mapsee_ingest import NormalizedEvent, EventStore, make_fingerprint, _to_float
from mapsee_geo_budget import geocode_allowed

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



def _get(row: Dict[str, Any], col: Optional[str]):
    if not col:
        return None
    v = row.get(col)
    return v if v not in ("", None) else None


def _parse_ampm(t: Optional[str]) -> Optional[str]:
    """'7:00 am' -> '07:00:00', '12:00 pm' -> '12:00:00' — for datasets that keep
    the clock time in a separate 12-hour column (e.g. NYC Parks)."""
    if not t or not isinstance(t, str):
        return None
    mt = re.match(r"\s*(\d{1,2}):(\d{2})\s*([ap])\.?m", t.lower())
    if not mt:
        return None
    h, mi, ap = int(mt.group(1)), mt.group(2), mt.group(3)
    if ap == "p" and h != 12:
        h += 12
    if ap == "a" and h == 12:
        h = 0
    return f"{h:02d}:{mi}:00"


def _src_tz(name: Optional[str]):
    if not name:
        return None
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        return None


def _localize_dt(date_key: Optional[str], hms: Optional[str], tz):
    """(local, utc) for date_key + 'HH:MM:SS'. With a tz we emit a real UTC instant
    so folded clock times show at their true local time, not shifted by the offset."""
    if not date_key or not hms:
        return None, None
    if tz is not None:
        dt = datetime.fromisoformat(f"{date_key}T{hms}").replace(tzinfo=tz)
        return dt.isoformat(), dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"{date_key}T{hms}", None


def _coords(row: Dict[str, Any], m: Dict[str, str]):
    """Extract (lat, lon) from explicit columns or a Socrata point/location field."""
    lat = _to_float(_get(row, m.get("lat")))
    lon = _to_float(_get(row, m.get("lon")))
    if lat is not None and lon is not None:
        return lat, lon
    g = _get(row, m.get("geo"))
    if isinstance(g, dict):
        coords = g.get("coordinates")                       # GeoJSON Point: [lon, lat]
        if isinstance(coords, (list, tuple)) and len(coords) >= 2:
            return _to_float(coords[1]), _to_float(coords[0])
        la, lo = _to_float(g.get("latitude")), _to_float(g.get("longitude"))
        if la is not None and lo is not None:               # Socrata location: {latitude, longitude}
            return la, lo
    ll = _get(row, m.get("latlon"))                         # a single "lat, lon" string column
    if isinstance(ll, str) and "," in ll:
        a, b = ll.split(",", 1)
        la, lo = _to_float(a), _to_float(b)
        if la is not None and lo is not None:
            return la, lo
    return None, None


_MONTHS = {m[:3]: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"], 1)}


def _freetext_date(s):
    """Human date columns some civic portals publish instead of ISO, e.g.
    'October 8 2026 6 - 7 p.m.' or '8 Oct 2026, 7:00 PM'. Returns
    (start_local, start_utc, date_key) or None. Best-effort: pulls a
    month-name + day + year, then an optional first clock time."""
    m = (re.search(r"([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})", s)
         or re.search(r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})", s))
    if not m:
        return None
    g = m.groups()
    mon = _MONTHS.get((g[0] if g[0].isalpha() else g[1])[:3].lower())
    day = int(g[1] if g[0].isalpha() else g[0])
    yr = int(g[2])
    if not mon:
        return None
    hh = mm = 0
    t = re.search(r"(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s*m", s, re.I)
    if t:
        hh = int(t.group(1)) % 12 + (12 if t.group(3).lower() == "p" else 0)
        mm = int(t.group(2) or 0)
    try:
        dt = datetime(yr, mon, day, hh, mm)
    except ValueError:
        return None
    return (dt.isoformat(), None, dt.date().isoformat())


def _iso_parts(s):
    """(start_local, start_utc, date_key) from an ISO-ish date string."""
    if not s or not isinstance(s, str):
        return (None, None, None)
    s = s.strip()
    if len(s) == 10:
        return (s, None, s)
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        ft = _freetext_date(s)          # civic free-text ("October 8 2026 6 p.m.")
        return ft if ft else (s, None, s[:10])
    if dt.tzinfo:
        return (dt.isoformat(), dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                dt.date().isoformat())
    # A naive EXACT midnight is a date stored in a timestamp column, not an event
    # that starts at 00:00 — civic datasets do this constantly (Chicago's youth
    # programs are all "T00:00:00"), and taken literally the whole feed renders
    # at midnight. Emit an all-day event instead, which is what it is.
    if dt.hour == dt.minute == dt.second == 0:
        return (dt.date().isoformat(), None, dt.date().isoformat())
    return (dt.isoformat(), None, dt.date().isoformat())      # naive local (no tz in the dataset)


def make_venue_geocoder(session, src: Dict[str, Any]):
    """Photon (OSM) lookup for VENUE NAMES ("Gas Works Park") in datasets that
    publish no coordinates and no street address — Seattle's special-event
    permits, for one. The sync's Census geocoder only resolves street
    addresses, so this fills coords at ingest. Config-gated per source:
        "geocode_venue": true, "geocode_suffix": ", Seattle, WA"
    One polite request (~1/s, photon fair use) per UNIQUE venue, cached."""
    if not src.get("geocode_venue"):
        return None
    import time
    suffix = src.get("geocode_suffix", "")
    cache = _GEO_CACHE

    def geocode(venue: str):
        key = (venue + "|" + suffix).strip().lower()
        if key in cache:
            return cache[key]
        if not geocode_allowed():                    # over this run's budget → retry next run
            return (None, None)
        try:
            time.sleep(1.1)
            r = session.get("https://photon.komoot.io/api/",
                            params={"q": venue + suffix, "limit": 1}, timeout=20)
            r.raise_for_status()                     # a 502 is not an empty result
            f = (r.json().get("features") or [None])[0]
            out = (f["geometry"]["coordinates"][1], f["geometry"]["coordinates"][0]) if f else (None, None)
        except Exception:                            # noqa: BLE001
            return (None, None)                      # NOT cached — retry next run
        # Only a HIT is cached, on the same reasoning as
        # mapsee_ingest_markets._geocode and mapsee_ingest_ics: geocode_cache.json
        # is COMMITTED, so a null written on a transient Photon failure is
        # permanent, and a coordless event is dropped at the sync.
        if out[0] is not None:
            cache[key] = out
        return out
    return geocode


def row_to_event(row: Dict[str, Any], src: Dict[str, Any], geocoder=None) -> Optional[NormalizedEvent]:
    m = src.get("map", {})
    title = _get(row, m.get("title"))
    if not title:
        return None
    start_local, start_utc, date_key = _iso_parts(_get(row, m.get("start")))
    if not date_key:
        return None
    end_local, end_utc, _ = _iso_parts(_get(row, m.get("end")))
    # datasets that split the clock time into a separate 12-hour column (NYC Parks:
    # startdate=midnight + starttime="7:00 am") — fold that time onto the date.
    tz = _src_tz(src.get("timezone"))
    st = _parse_ampm(_get(row, m.get("start_time")))
    if st:
        start_local, start_utc = _localize_dt(date_key, st, tz)
    et = _parse_ampm(_get(row, m.get("end_time")))
    if et:
        end_local, end_utc = _localize_dt((end_local or date_key)[:10], et, tz)
    # Client-side past-event drop. Datasets with ISO date columns filter past
    # server-side via the {now} where-clause; datasets with FREE-TEXT dates
    # (no usable where) rely on this. A no-op for the former. Keep if the event's
    # last day is today or later.
    if src.get("drop_past", True):
        last_day = ((end_local or date_key) or "")[:10]
        if last_day and last_day < datetime.now(timezone.utc).date().isoformat():
            return None
    venue = _get(row, m.get("venue"))
    v2 = _get(row, m.get("venue2"))                      # e.g. borough — helps display AND geocoding
    if venue and v2:
        venue = f"{venue}, {v2}"
    lat, lon = _coords(row, m)
    if (lat is None or lon is None) and geocoder and venue:
        lat, lon = geocoder(str(venue))                      # venue-name lookup (cached)
    if lat is None or lon is None:
        return None                                          # no geo -> can't place on the map
    url = _get(row, m.get("url"))
    if isinstance(url, dict):                                # Socrata URL column -> {"url": "..."}
        url = url.get("url")
    # ALWAYS carry a source URL so every civic event is verifiable: the row's own
    # link, else a configured landing page, else the Socrata dataset's public
    # page derived from the API URL (proves the data is official + findable).
    url = url or src.get("url_home") or _dataset_page(src.get("url"))
    label = "opendata:" + src["name"].lower().replace(" ", "-")
    ev = NormalizedEvent(
        source=label,
        source_id=str(_get(row, m.get("id")) or make_fingerprint(str(title), date_key, venue)),
        name=str(title),
        description=(str(_get(row, m.get("description"))) if _get(row, m.get("description")) else None),
        start_local=start_local, start_utc=start_utc,
        end_local=end_local, end_utc=end_utc,
        venue_name=venue, latitude=lat, longitude=lon,
        address=_get(row, m.get("address")),
        category=src.get("category"),                        # optional fixed Mapsee key
        ticket_url=url,
    )
    ev.fingerprint = make_fingerprint(str(title), date_key, venue)
    return ev


def _dataset_page(api_url: Optional[str]) -> Optional[str]:
    """Human Socrata dataset page from the resource API URL, so a URL-less row
    still links somewhere official + findable:
    https://<domain>/resource/<4x4>.json -> https://<domain>/d/<4x4>"""
    m = re.search(r"^(https?://[^/]+)/resource/([a-z0-9]{4}-[a-z0-9]{4})", str(api_url or ""), re.I)
    return f"{m.group(1)}/d/{m.group(2)}" if m else None


def ingest_socrata(store: EventStore, session, src: Dict[str, Any]) -> int:
    params: Dict[str, Any] = {"$limit": src.get("limit", 1000), "$order": src.get("order", ":id")}
    where = src.get("where")
    if where:
        params["$where"] = where.replace("{now}", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"))
    headers = {}
    if src.get("app_token"):
        headers["X-App-Token"] = src["app_token"]
    resp = session.get(src["url"], params=params, headers=headers, timeout=25)  # fail fast
    resp.raise_for_status()
    rows = resp.json()
    rows = rows if isinstance(rows, list) else []
    geocoder = make_venue_geocoder(session, src)
    n = 0
    for row in rows:
        ev = row_to_event(row, src, geocoder)
        if ev:
            store.upsert(ev)
            n += 1
    print(f"[opendata] {src.get('name', '?')}: kept {n} of {len(rows)} rows")
    return n


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import city open-data events (Socrata) into the Mapsee store.")
    ap.add_argument("--config", required=True, help="JSON file describing the datasets (see the .example).")
    ap.add_argument("--store", default="mapsee_events.json")
    a = ap.parse_args(argv)

    sources = json.loads(open(a.config, encoding="utf-8").read())
    session = requests.Session()
    session.headers.update({"User-Agent": "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)"})
    store = EventStore(a.store)

    total = 0
    for src in sources:
        try:
            total += ingest_socrata(store, session, src)
        except Exception as exc:
            print(f"[opendata] {src.get('name', '?')} FAILED: {exc}")
    store.save()
    _save_geo_cache(_GEO_CACHE)
    print(f"[opendata] done: +{total} events processed; store now holds {len(store.records)} unique events.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
