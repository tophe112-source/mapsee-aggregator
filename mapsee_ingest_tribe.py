#!/usr/bin/env python3
"""
mapsee_ingest_tribe.py - import events from any WordPress site running "The
Events Calendar" (Modern Tribe), via its public REST API.

    python mapsee_ingest_tribe.py --config tribe_sources.json --store feeds_events.json

One adapter, many sites: the plugin ships the same `/wp-json/tribe/events/v1/`
route on every host that installs it, so adding a source is a config line. The
plugin is on hundreds of thousands of sites and is especially common exactly
where mapsee is thin - comedy rooms, running clubs, native plant societies,
audubon chapters, small museums.

WHY NOT THE .ics FEED. mapsee_ingest_ics.py already reads the same plugin's
`/events/?ical=1`, and for a handful of sources that is fine. The REST API is
strictly better where it exists: it carries `venue.geo_lat`/`geo_lng`, so the
event lands on the map without a geocoder round trip; it separates venue name
from street, city and state instead of gluing them into one LOCATION line; it
gives a real ticket URL; and it paginates, so a site with 10,000 events can be
walked instead of downloading one enormous calendar. Probing
`/wp-json/tribe/events/v1/events` also cleanly trisects a new candidate: JSON
with `events` means yes, a `rest_no_route` JSON error means WordPress without
the plugin, HTML means it is not WordPress at all.

CRAWL DELAY IS PER SITE AND IT IS NOT OPTIONAL. These are small nonprofits on
shared hosting, and several of them say so in robots.txt: The Comedy Bureau asks
10s, the Georgia Native Plant Society asks 150s. `crawl_delay` defaults to a
polite 2s and every configured value is honoured between page requests. A source
whose robots.txt disallows the API does not belong in the config at all.
"""
from __future__ import annotations

import argparse
import html as html_mod
import json
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

from mapsee_ingest import NormalizedEvent, EventStore, make_fingerprint, norm_categories

UA = "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)"
_TAG = re.compile(r"<[^>]+>")


def _clean(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    s = html_mod.unescape(_TAG.sub(" ", str(s)))
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def _f(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    # The plugin writes 0/0 when a venue has no coordinates rather than leaving
    # them null, and 0,0 is the Atlantic. Treat it as absent.
    return None if f == 0.0 else f


def _obj(v) -> Dict[str, Any]:
    """One of the plugin's object fields, as a dict — whatever shape it arrived in.

    `venue` is a dict on most events, `[]` when unset, and `[{...}]` on some — all
    three from the SAME site: measured on bicyclecolorado.org, 105 dicts, 4
    populated lists, out of 109. `image` is a dict or the bare boolean `false`
    (72 of 109). `or {}` alone covers the empty list, which is why this went
    unnoticed; a POPULATED list sails through it and raises AttributeError on the
    first `.get`.

    That exception is not caught per event — `ingest_site` wraps the whole site —
    so a single malformed record does not lose one event, it loses the ENTIRE
    SOURCE, and it does it while printing a line that looks like a network fault
    ("FAILED: 'list' object has no attribute 'get'"). Bicycle Colorado ingested 0
    of its 105 placeable events that way.
    """
    if isinstance(v, dict):
        return v
    if isinstance(v, list):
        return next((x for x in v if isinstance(x, dict)), {})
    return {}


def to_event(ev: Dict[str, Any], site: Dict[str, Any]) -> Optional[NormalizedEvent]:
    name = _clean(ev.get("title"))
    start = (ev.get("start_date") or "").strip()          # "2026-08-04 10:00:00", site-local
    if not name or len(start) < 10:
        return None
    start_local = start.replace(" ", "T")[:19]
    v = _obj(ev.get("venue"))
    lat, lon = _f(v.get("geo_lat")), _f(v.get("geo_lng"))
    # The plugin's own taxonomy, folded in alongside the configured default so a
    # site that files things usefully (a running club tagging "volunteer") lands
    # on more than one lens. norm_categories drops anything outside mapsee's
    # vocabulary, so a source's private labels can never reach the database.
    primary = site.get("category", "community")
    cats = ev.get("categories")
    extras = norm_categories(primary, [c.get("slug") or c.get("name")
                                       for c in (cats if isinstance(cats, list) else [])
                                       if isinstance(c, dict)])
    nev = NormalizedEvent(
        source="tribe",
        source_id=str(ev.get("id") or ev.get("global_id") or make_fingerprint(name, start_local[:10], v.get("venue"))),
        name=name,
        description=_clean(ev.get("description")),
        start_local=start_local,
        end_local=((ev.get("end_date") or "").strip().replace(" ", "T")[:19] or None),
        timezone=ev.get("timezone") or None,
        venue_name=_clean(v.get("venue")) or site.get("venue_name"),
        latitude=lat, longitude=lon,
        address=_clean(v.get("address")),
        city=_clean(v.get("city")) or site.get("default_city"),
        region=_clean(v.get("state") or v.get("stateprovince") or v.get("province")) or site.get("default_region"),
        country=_clean(v.get("country")) or site.get("default_country"),
        postal_code=_clean(v.get("zip")),
        category=primary,
        categories=extras,
        poster_image_url=_obj(ev.get("image")).get("url"),
        # `website` is the venue's own ticket link when set; `url` is the event
        # page on the source site, which is always present and always useful.
        ticket_url=ev.get("website") or ev.get("url"),
    )
    nev.fingerprint = make_fingerprint(name, start_local[:10], nev.venue_name, nev.city)
    return nev


def ingest_site(store: EventStore, session, site: Dict[str, Any]) -> int:
    base = (site.get("base_url") or "").rstrip("/")
    if not base:
        print(f"[tribe] {site.get('name','?')}: no base_url"); return 0
    api = f"{base}/wp-json/tribe/events/v1/events"
    delay = float(site.get("crawl_delay", 2))
    per_page = int(site.get("per_page", 50))
    max_pages = int(site.get("max_pages", 20))
    now = datetime.now(timezone.utc)
    params = {
        "per_page": per_page,
        "start_date": now.strftime("%Y-%m-%d"),
        "end_date": (now + timedelta(days=int(site.get("within_days", 180)))).strftime("%Y-%m-%d"),
    }
    kept = 0
    malformed = 0
    for page in range(1, max_pages + 1):
        try:
            r = session.get(api, params=dict(params, page=page), timeout=45)
        except Exception as exc:
            print(f"[tribe] {site.get('name')} p{page} failed: {exc}"); break
        # The plugin answers a page past the end with 404 + rest_post_invalid_page_number.
        if r.status_code == 404:
            break
        if r.status_code != 200:
            print(f"[tribe] {site.get('name')} p{page} HTTP {r.status_code}"); break
        try:
            body = r.json()
        except Exception as exc:
            print(f"[tribe] {site.get('name')} p{page} bad JSON: {exc}"); break
        rows = body.get("events") or []
        if not rows:
            break
        for ev in rows:
            # Per EVENT, not per site. The site-level try in main() means one
            # unreadable record costs the whole calendar (see _obj), and it
            # reports as a site failure, which reads like the host was down.
            # Counted and printed rather than swallowed: a silent skip is how a
            # shape change eats a source one record at a time.
            try:
                nev = to_event(ev, site)
            except Exception as exc:                       # noqa: BLE001
                if not malformed:
                    print(f"[tribe] {site.get('name')}: skipping unreadable record "
                          f"{ev.get('id')!r} ({type(exc).__name__}: {exc})")
                malformed += 1
                continue
            if nev:
                store.upsert(nev)
                kept += 1
        if page >= int(body.get("total_pages") or page):
            break
        if delay:
            time.sleep(delay)                              # robots.txt Crawl-delay
    print(f"[tribe] {site.get('name')}: kept {kept} events"
          + (f" ({malformed} unreadable record(s) skipped)" if malformed else ""))
    return kept


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import 'The Events Calendar' sites into the Mapsee store.")
    ap.add_argument("--config", required=True)
    ap.add_argument("--store", default="mapsee_events.json")
    ap.add_argument("--only", help="ingest just this site name (substring match)")
    a = ap.parse_args(argv)

    cfg = json.loads(open(a.config, encoding="utf-8").read())
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept": "application/json"})
    store = EventStore(a.store)
    total = 0
    for site in cfg.get("sites", []):
        if a.only and a.only.lower() not in str(site.get("name", "")).lower():
            continue
        try:
            total += ingest_site(store, session, site)
        except Exception as exc:
            print(f"[tribe] {site.get('name','?')} FAILED: {exc}")
    store.save()
    print(f"[tribe] done: +{total} events; store now holds {len(store.records)} unique events.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
