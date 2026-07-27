#!/usr/bin/env python3
"""
mapsee_ingest_affiliates.py - DoorDash / Uber Eats affiliate catalogs -> pickup pins.

Both platforms run their affiliate programs through networks like Impact, which
hand APPROVED affiliates periodic location/product catalog feeds (CSV or JSON)
rather than a discovery API. This adapter turns those catalogs into the same
claimable "X · pickup" food events the restaurants adapter emits, so every
downstream piece - supabase sync, the Nearby map, "Organizing this? Claim it",
and the storefront wizard - works unchanged. The funnel is: affiliate pin ->
owner claims it -> onboards the storefront -> direct, commission-free orders
replace the affiliate link (sync never overwrites claimed listings).

    python mapsee_ingest_affiliates.py --config affiliate_sources.json --store feeds_events.json

Config (affiliate_sources.json): {"days_ahead", "pickup_hours", "feeds": [...]}.
Each feed: source label, "url" with ${ENV_VAR} placeholders (the feed is
SKIPPED while the var is unset - the adapter is a silent no-op until the
affiliate program approves you and the secret is set), format csv|json,
"map" translating that catalog's column names, optional countries/cities
filters and max_locations cap (Impact catalogs can carry 100k+ rows; caps keep
the event store and the map sane - raise them deliberately).

DEDUPE: the same restaurant often lists on both platforms. Rows from ALL feeds
are merged when they sit within ~120m of each other AND their normalized names
fuzzy-match; the merged pin carries every platform's order link in the
description, with the first feed's link as the primary button.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import re
import sys
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

from mapsee_ingest import NormalizedEvent, EventStore, make_fingerprint

UA = "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)"
PRETTY = {"DOORDASH": "DoorDash", "UBER_EATS": "Uber Eats"}


def _expand_env(url: str) -> Optional[str]:
    """${VAR} substitution; None when any referenced var is unset (feed gated off)."""
    missing = []
    out = re.sub(r"\$\{(\w+)\}", lambda m: os.environ.get(m.group(1)) or missing.append(m.group(1)) or "", url)
    return None if missing else out


def _load_rows(session, url: str, fmt: str) -> List[Dict[str, Any]]:
    if re.match(r"^https?://", url):
        r = session.get(url, timeout=120)
        r.raise_for_status()
        text = r.text
    else:
        text = open(url, encoding="utf-8-sig").read()
    if fmt == "json":
        data = json.loads(text)
        return data if isinstance(data, list) else data.get("items") or data.get("records") or []
    return list(csv.DictReader(io.StringIO(text)))


def _norm_name(name: str) -> str:
    s = re.sub(r"[^a-z0-9 ]+", " ", name.lower())
    s = re.sub(r"\b(the|restaurant|cafe|kitchen|bar|grill|and|co)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _haversine_m(a, b) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 12742000 * math.asin(math.sqrt(h))


def _same_place(a: Dict, b: Dict) -> bool:
    if _haversine_m((a["lat"], a["lon"]), (b["lat"], b["lon"])) > 120:
        return False
    na, nb = a["norm"], b["norm"]
    return na in nb or nb in na or SequenceMatcher(None, na, nb).ratio() >= 0.82


def collect(session, cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Load every enabled feed, then merge cross-platform duplicates."""
    places: List[Dict[str, Any]] = []
    grid: Dict[tuple, List[int]] = {}                 # ~100m buckets -> indexes into places
    for feed in cfg.get("feeds", []):
        src = feed["source"]
        url = _expand_env(feed["url"])
        if not url:
            print(f"[affiliates] {src}: feed secret unset - skipped")
            continue
        m = feed["map"]
        countries = {c.upper() for c in feed.get("countries", [])}
        cities = {c.lower() for c in feed.get("cities", [])}
        cap = int(feed.get("max_locations", 500))
        kept = 0
        try:
            rows = _load_rows(session, url, feed.get("format", "csv"))
        except Exception as exc:
            print(f"[affiliates] {src}: feed load FAILED: {exc}")
            continue
        for row in rows:
            if kept >= cap:
                break
            try:
                lat, lon = float(row[m["lat"]]), float(row[m["lon"]])
            except Exception:
                continue                               # no coordinates, no pin
            name = str(row.get(m["name"], "")).strip()
            link = str(row.get(m["url"], "")).strip()
            if not name or not link:
                continue
            if countries and str(row.get(m.get("country", ""), "")).upper() not in countries:
                continue
            if cities and str(row.get(m.get("city", ""), "")).lower() not in cities:
                continue
            p = {
                "name": name, "norm": _norm_name(name), "lat": lat, "lon": lon,
                "links": {src: link},
                "id": str(row.get(m.get("id", ""), "") or link),
                "address": str(row.get(m.get("address", ""), "")).strip() or None,
                "city": str(row.get(m.get("city", ""), "")).strip() or None,
                "region": str(row.get(m.get("region", ""), "")).strip() or None,
                "country": str(row.get(m.get("country", ""), "")).strip() or None,
                "postal": str(row.get(m.get("postal", ""), "")).strip() or None,
                "desc": str(row.get(m.get("description", ""), "")).strip()[:300] or None,
            }
            # spatial+fuzzy merge against neighbours in the surrounding grid cells
            cell = (round(lat, 3), round(lon, 3))
            hit = None
            for dy in (-0.001, 0, 0.001):
                for dx in (-0.001, 0, 0.001):
                    for i in grid.get((round(cell[0] + dy, 3), round(cell[1] + dx, 3)), []):
                        if _same_place(places[i], p):
                            hit = i
                            break
            if hit is not None:
                places[hit]["links"].update(p["links"])   # same spot on another platform
            else:
                grid.setdefault(cell, []).append(len(places))
                places.append(p)
                kept += 1
        print(f"[affiliates] {src}: {kept} locations kept of {len(rows)} rows")
    return places


def emit(store: EventStore, places: List[Dict[str, Any]], cfg: Dict[str, Any]) -> int:
    days_ahead = min(int(cfg.get("days_ahead", 3)), 14)
    open_t, close_t = cfg.get("pickup_hours", ["11:00", "21:00"])
    today = datetime.now()
    total = 0
    for p in places:
        primary = next(iter(p["links"].values()))
        order_lines = "\n".join(
            f"🛒 Order pickup on {PRETTY.get(s, s)}: {u}" for s, u in p["links"].items())
        desc = (order_lines + "\n\nThis listing came from a delivery-platform catalog. "
                "Own this business? Claim it to take direct, commission-free pickup orders here.")
        if p["desc"]:
            desc = p["desc"] + "\n\n" + desc
        for d in range(days_ahead):
            day = today + timedelta(days=d)
            date_key = day.strftime("%Y-%m-%d")
            ev = NormalizedEvent(
                source="affiliate",
                source_id=f"{p['id']}#{date_key}",
                name=f"{p['name']} · pickup",
                description=desc,
                start_local=f"{date_key}T{open_t}:00",
                end_local=f"{date_key}T{close_t}:00",
                venue_name=p["name"],
                latitude=p["lat"], longitude=p["lon"],
                address=p["address"], city=p["city"], region=p["region"],
                country=p["country"], postal_code=p["postal"],
                category="food",
                ticket_url=primary,
            )
            ev.fingerprint = make_fingerprint(f"{p['name']} · pickup", date_key, p["name"])
            store.upsert(ev)
            total += 1
    return total


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import DoorDash/Uber Eats affiliate catalogs into the Mapsee store.")
    ap.add_argument("--config", required=True)
    ap.add_argument("--store", default="feeds_events.json")
    a = ap.parse_args(argv)
    cfg = json.loads(open(a.config, encoding="utf-8").read())
    session = requests.Session()
    session.headers.update({"User-Agent": UA})
    places = collect(session, cfg)
    if not places:
        print("[affiliates] nothing to ingest (no feeds enabled or no usable rows)")
        return 0
    store = EventStore(a.store)
    total = emit(store, places, cfg)
    store.save()
    merged = sum(1 for p in places if len(p["links"]) > 1)
    print(f"[affiliates] done: {len(places)} restaurants ({merged} cross-platform merges) -> {total} pickup windows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
