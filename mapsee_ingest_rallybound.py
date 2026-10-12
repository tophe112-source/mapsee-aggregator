#!/usr/bin/env python3
"""
mapsee_ingest_rallybound.py - charity walks on Neon One's RallyBound / Neon
Fundraise platform: the National Kidney Foundation's Kidney Walks and the Cystic
Fibrosis Foundation's Great Strides.

    python mapsee_ingest_rallybound.py --config rallybound_sources.json --store feeds_events.json

WHAT IT READS. Each charity's own event finder is a search widget over
GET https://<host>/widgetapi/EventsLite[?eventSeriesId=N], which answers JSON:
one record per walk with its name, local date-time, an IANA zone, the event
page, and a Location of Lat, Lon, Address and State. One request per
configured tenant, so a run is two requests. robots.txt on both hosts ends in
`Allow: /` and nothing in it touches /widgetapi/ (checked 2026-10-11 and on
every run). No usage policy for the widget API was found, so it is read as a
publisher feed: robots.txt binds it, and a 401/403/429 or a bot challenge ends
that tenant's read.

Measured 2026-10-11: kidneywalk.org 20 walks (19 on 2026-10-17..11-14, one in
May 2027), all 20 with coordinates and a street address; fundraise.cff.org
(series 9, Great Strides) 7, of which 5 carry a place and 2 say only "TBD".

WHY THESE RULES:

1. THE WALL CLOCK, NOT THE ZONE. EventDateTimeLocal is the clock the organiser
   typed; EventTimeZone is a setting beside it, and 3 of the 25 placed walks
   have the wrong one: Twin Cities (St. Paul, MN) and Memphis say US/Eastern,
   Louisville says US/Central, so EventDateUtc puts St. Paul's 9:00 walk at
   8:00 there. The row carries the naive local time and the sync zones it from
   the walk's own coordinates (as for every naive time), which is right in all
   25.

2. "TBD" IS NOT A PLACE. A walk whose Location has no coordinates and says
   "TBD" is not written: it would land nowhere, or on a city centre.

3. THE ADDRESS IS ONE STRING. "1123 Coleman Ave , San Jose , CA , 95110",
   "Siesta Key Beach, 948 Beach Road, Siesta Key, FL 34242", "9151 NW 2nd
   Street   Plantation, FL 33324", "3000 Downs Blvd. Metairie, LA 70003": the
   ZIP and the state come off the end, a run of spaces is a comma, a named
   place before the street becomes the venue, the words after a street's
   suffix with no comma left for a town are the town, and what is left is the
   street (2 of the 25 placed walks of 2026-10-11 had no town before the last
   two rules). The coordinates are the platform's own and are kept as they are
   (coords_exact); the street is for reading.

4. NOTHING ABOUT PRICE. Neither charity's registration fee is in the feed, and
   neither was confirmed on the charity's own pages, so a row says nothing
   about cost rather than guessing "free".

Category: community, with fitness and outdoors (both programmes are outdoor
walks).
"""
from __future__ import annotations

import argparse
import html as htmllib
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import urlsplit

from mapsee_ingest import (EventStore, NormalizedEvent, USER_AGENT, looks_online_only,
                           make_fingerprint, norm_categories)

SOURCE = "rallybound"
DEFAULT_WITHIN_DAYS = 180
_PLACEHOLDER_RX = re.compile(r"^\s*(tbd|tba|to be (?:determined|announced)|virtual|online|n/?a)\.?\s*$", re.I)
_STREET_START_RX = re.compile(r"^\s*\d{1,6}[A-Za-z]?\s+\S")
# "3000 Downs Blvd. Metairie": a numbered street, its suffix, then the town.
_STREET_THEN_TOWN_RX = re.compile(
    r"^(\d{1,6}[A-Za-z]?\s+.*?\b(?:St|Street|Ave|Avenue|Rd|Road|Dr|Drive|Blvd|Boulevard|Ln|Lane|Way|Hwy|"
    r"Highway|Pkwy|Parkway|Ct|Court|Pl|Place|Cir|Circle|Ter|Terrace|Pike|Trail|Loop|Plaza|Square)\b\.?)"
    r"\s+((?!(?:N|S|E|W|NE|NW|SE|SW|North|South|East|West)\b)[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){0,2})$")


def _clean(s: Any) -> str:
    return re.sub(r"\s+", " ", htmllib.unescape(str(s or ""))).strip()


def split_address(raw: Optional[str], state: Optional[str]) -> Dict[str, Optional[str]]:
    """{venue, street, city, region, postal_code} from RallyBound's one-line
    Address (rule 3). Anything unreadable stays in `street` as given."""
    out: Dict[str, Optional[str]] = {"venue": None, "street": None, "city": None,
                                     "region": (state or "").strip().upper() or None, "postal_code": None}
    # A run of spaces is where the organiser's form put a comma: "9151 NW 2nd
    # Street   Plantation, FL 33324" (Ft. Lauderdale's walk).
    s = _clean(re.sub(r"(?<=\S) {2,}(?=\S)", ", ", str(raw or "")))
    if not s or _PLACEHOLDER_RX.match(s):
        return out
    parts = [p.strip() for p in s.split(",") if p.strip()]
    # "FL 34242" or "FL" then "34242", at the end.
    if parts:
        m = re.fullmatch(r"([A-Za-z]{2})\s+(\d{5})(?:-\d{4})?", parts[-1])
        if m:
            out["region"], out["postal_code"] = out["region"] or m.group(1).upper(), m.group(2)
            parts.pop()
        else:
            if re.fullmatch(r"\d{5}(?:-\d{4})?", parts[-1]):
                out["postal_code"] = parts.pop()[:5]
            if parts and re.fullmatch(r"[A-Za-z]{2}", parts[-1]):
                out["region"] = out["region"] or parts[-1].upper()
                parts.pop()
    if len(parts) >= 2:
        out["city"] = parts.pop()
    elif parts:
        m = _STREET_THEN_TOWN_RX.match(parts[0])
        if m:
            parts[0], out["city"] = m.group(1), m.group(2)
    if len(parts) >= 2 and not _STREET_START_RX.match(parts[0]) and _STREET_START_RX.match(parts[1]):
        out["venue"] = parts.pop(0)
    out["street"] = ", ".join(parts) or None
    return out


def _f(v: Any) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x


def to_event(rec: Dict[str, Any], src: Dict[str, Any], host: str, today: date,
             within_days: int) -> Tuple[Optional[NormalizedEvent], str]:
    """(event, "") or (None, why-not)."""
    name = _clean(rec.get("Name"))
    local = str(rec.get("EventDateTimeLocal") or "").strip()
    if not name or not re.match(r"\d{4}-\d{2}-\d{2}", local):
        return None, "no name or date"
    try:
        start = datetime.fromisoformat(local[:19])
    except ValueError:
        return None, "unreadable date"
    if start.date() < today:
        return None, "past"
    if start.date() > today + timedelta(days=within_days):
        return None, "beyond the window"
    loc = rec.get("Location") or {}
    lat, lon = _f(loc.get("Lat")), _f(loc.get("Lon"))
    if lat is None or lon is None or (abs(lat) < 0.5 and abs(lon) < 0.5):
        return None, "no place (TBD)"
    if looks_online_only(name, None):
        return None, "online"
    addr = split_address(loc.get("Address"), loc.get("State"))
    end_local = None
    end = str(rec.get("EndDateTimeLocal") or "").strip()
    if re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", end) and end[:19] > local[:19]:
        end_local = end[:19]
    url = str(rec.get("URL") or "").strip()
    if url and not re.match(r"https?://", url):
        url = "https://" + url.lstrip("/")
    program, org = src.get("program") or "", src.get("org") or ""
    lines = [f"{program}, a {org} walk." if program and org else (program or org or "Charity walk.")]
    if src.get("about"):
        lines.append(src["about"])
    lines.append("Registration, start time details and the route are on the event page.")
    nev = NormalizedEvent(
        source=SOURCE,
        source_id=f"{host}:{rec.get('Id')}",
        name=name,
        description="\n\n".join(lines),
        start_local=start.strftime("%Y-%m-%dT%H:%M:%S"),
        end_local=end_local,
        venue_name=addr["venue"],
        address=addr["street"],
        latitude=lat, longitude=lon,
        city=addr["city"], region=addr["region"], postal_code=addr["postal_code"],
        country=src.get("country") or "US",
        category="community",
        categories=norm_categories("community", src.get("categories") or ["fitness", "outdoors"]),
        promoter=org or None,
        ticket_url=url or None,
        coords_exact=True,
    )
    nev.fingerprint = make_fingerprint(name, start.date().isoformat(), addr["venue"] or addr["street"], addr["city"])
    return nev, ""


class Refused(Exception):
    """The host said no: 401/403/429, a bot challenge or robots.txt."""


def fetch(session, url: str, robots=None) -> Any:
    """The decoded JSON body of one GET, or raises Refused / ValueError."""
    from robots_txt import CHALLENGE_RX
    if robots is not None:
        v = robots.check(url)
        if v.get("allowed") is not True:
            raise Refused(f"robots.txt ({v.get('status')}): {v.get('rule')} for {url}")
    r = session.get(url, timeout=40, allow_redirects=True, headers={"Accept": "application/json"})
    if r.status_code in (401, 403, 429):
        raise Refused(f"HTTP {r.status_code} for {url}")
    text = r.text or ""
    if CHALLENGE_RX.search(text[:6000]):
        raise Refused(f"bot challenge for {url}")
    if r.status_code != 200:
        raise ValueError(f"HTTP {r.status_code} for {url}")
    return json.loads(text)


def ingest_source(src: Dict[str, Any], get_json: Callable[[str], Any], today: date,
                  within_days: int, emit: Callable[[NormalizedEvent], None]) -> Dict[str, Any]:
    url = src["url"]
    host = urlsplit(url).netloc
    rep: Dict[str, Any] = {"listed": 0, "kept": 0, "skipped": {}}
    data = get_json(url)
    rows = data if isinstance(data, list) else (data or {}).get("Events") or []
    rep["listed"] = len(rows)
    seen = set()
    for rec in rows:
        if not isinstance(rec, dict) or rec.get("Id") in seen:
            continue
        seen.add(rec.get("Id"))
        ev, why = to_event(rec, src, host, today, within_days)
        if ev is None:
            rep["skipped"][why] = rep["skipped"].get(why, 0) + 1
            continue
        emit(ev)
        rep["kept"] += 1
    return rep


def _today() -> date:
    fixed = os.environ.get("MAPSEE_TODAY")
    if fixed:
        return datetime.strptime(fixed, "%Y%m%d").date()
    return date.today()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import charity walks from RallyBound / Neon Fundraise tenants.")
    ap.add_argument("--config", default="rallybound_sources.json")
    ap.add_argument("--store", default="feeds_events.json")
    ap.add_argument("--dry-run", action="store_true", help="read and report, write nothing")
    a = ap.parse_args(argv)

    cfg = json.loads(open(a.config, encoding="utf-8").read())
    try:
        import requests
    except ImportError:  # pragma: no cover
        sys.exit("This script needs 'requests'.  Install it with:  pip install requests")
    from robots_txt import Robots

    # ONLY the User-Agent on the session. Robots reads robots.txt with this
    # session, and with a session-wide "Accept: application/json" both hosts
    # answered /robots.txt with 406 (2026-10-11) - a 4xx, which RFC 9309 reads as
    # "no rules", so the check passed without reading a rule. The API request
    # asks for JSON on its own.
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    robots = Robots(session)
    store = None if a.dry_run else EventStore(a.store)
    today = _today()
    within = int(cfg.get("within_days", DEFAULT_WITHIN_DAYS))
    pause = float(cfg.get("pause", 1.0))
    total = 0
    for i, src in enumerate(s for s in cfg.get("sources", []) if not s.get("skip")):
        if i:
            time.sleep(pause)
        name = src.get("name") or src.get("url")
        try:
            rep = ingest_source(src, lambda u: fetch(session, u, robots), today, within,
                                (store.upsert if store is not None else (lambda ev: None)))
        except Refused as exc:
            print(f"[{SOURCE}] {name}: REFUSED - {exc}; not retried", flush=True)
            continue
        except Exception as exc:                          # noqa: BLE001
            # One tenant must never cost the other.
            print(f"[{SOURCE}] {name} FAILED: {type(exc).__name__}: {exc}", flush=True)
            continue
        skipped = ", ".join(f"{k} {v}" for k, v in sorted(rep["skipped"].items())) or "none"
        print(f"[{SOURCE}] {name}: listed {rep['listed']}, kept {rep['kept']} within {within} days; "
              f"skipped: {skipped}", flush=True)
        total += rep["kept"]
    if store is not None:
        store.save()
        print(f"[{SOURCE}] done: +{total} walks; store now holds {len(store.records)} unique events.", flush=True)
    else:
        print(f"[{SOURCE}] dry run - {total} walk(s) would be written", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
