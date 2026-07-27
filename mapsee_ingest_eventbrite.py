#!/usr/bin/env python3
"""
mapsee_ingest_eventbrite.py - import events via the OFFICIAL Eventbrite API v3
into the Mapsee store, normalized + deduped alongside the other adapters.

How it works (and why): Eventbrite retired public event SEARCH in 2019, and the
v3 organization endpoints only serve organizations YOUR token belongs to - there
is no API way to list a third party's events. (The /o/<id> organizer-profile ids
are a different id space from organization ids entirely, so organizer-scoped
fetching 404s across the board.) What still works, cleanly:

  1. DISCOVER event ids from the public /d/<metro>/all-events/ pages - the
     deliberately machine-readable, robots.txt-allowed SEO surface. We read
     NOTHING but event ids from it.
  2. HYDRATE each id through the official API: /v3/events/{id}?expand=venue,
     category, under your private token. Every field we store comes from the
     API - canonical data, sanctioned interface, official rate limits.
  3. Your OWN organizations (if the token has any) still import fully via
     /v3/organizations/{id}/events/.

    export EVENTBRITE_API_TOKEN=...
    python mapsee_ingest_eventbrite.py --config eventbrite_organizers.json \
        --store mapsee_events.json

Config: { "include_my_organizations": true, "pages": 1,
          "metros": [{"slug": "wa--seattle", "name": "Seattle"}, ...],
          "organizers": [...] }
"metros" drives discovery (~20 events per page per metro; "pages" 1-3).
"organizers" is kept as a curation/reference list (names + metros) - it is NOT
fetched: organizer-profile ids aren't API-listable (above). If Eventbrite ever
grants organization-level access, they're ready to light up.

Quota note: hydration is one API call per event (50 metros x 1 page ~= 1000
calls; the default token quota is 2000/hour), throttled + 429-aware. Discovery
needs a residential-ish IP (Eventbrite's WAF 403/405-blocks datacenter ranges,
so in CI this job usually no-ops; run eventbrite_local.ps1 from a home machine
instead). GATED: no EVENTBRITE_API_TOKEN -> prints a notice, exits 0.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

from mapsee_ingest import NormalizedEvent, EventStore, make_fingerprint

API = "https://www.eventbriteapi.com/v3"
WEB_UA = "Mozilla/5.0 (compatible; MapseeAggregator/1.0; +https://mapsee.me; events@mapsee.me)"

# Eventbrite top-level category NAME -> Mapsee frontend category key. Anything
# unmapped falls to 'other'; the sync's keyword passes still promote volunteer /
# theater titles afterwards.
_EB_CATEGORY = {
    "music": "music",
    "performing & visual arts": "arts",
    "film, media & entertainment": "theater",
    "food & drink": "food",
    "community & culture": "community",
    "sports & fitness": "sports",
    "health & wellness": "community",
    "science & technology": "learning",
    "business & professional": "learning",
    "family & education": "kids",
    "charity & causes": "community",
    "travel & outdoor": "outdoors",
    "seasonal & holiday": "party",
    "hobbies & special interest": "community",
    "religion & spirituality": "community",
    "school activities": "kids",
    "government & politics": "community",
    "home & lifestyle": "community",
    "auto, boat & air": "other",
    "fashion & beauty": "other",
}

_MIN_INTERVAL_S = 0.3        # API pacing: ~3 req/s keeps a 1000-call run ~6 min
_last_req = [0.0]


def _api_get(session, url, params=None) -> Optional[requests.Response]:
    """Official-API GET with pacing + 429 backoff."""
    gap = time.monotonic() - _last_req[0]
    if gap < _MIN_INTERVAL_S:
        time.sleep(_MIN_INTERVAL_S - gap)
    r = None
    for attempt in range(1, 6):
        r = session.get(url, params=params, timeout=30)
        _last_req[0] = time.monotonic()
        if r.status_code != 429:
            return r
        ra = r.headers.get("Retry-After")
        wait = float(ra) if (ra and ra.replace(".", "", 1).isdigit()) else min(2 ** attempt, 60)
        print(f"[eventbrite] 429 rate-limited; backing off {wait:.0f}s ({attempt}/5)")
        time.sleep(wait)
    return r


# ---- discovery: event ids from the public metro pages ------------------------
def _server_data(html: str) -> Optional[Dict[str, Any]]:
    """window.__SERVER_DATA__ = {...}; via brace matching."""
    m = re.search(r"window\.__SERVER_DATA__\s*=\s*(\{)", html)
    if not m:
        return None
    s = html[m.start(1):]
    depth = 0
    in_str = esc = False
    for i, ch in enumerate(s):
        if in_str:
            if esc: esc = False
            elif ch == "\\": esc = True
            elif ch == '"': in_str = False
            continue
        if ch == '"': in_str = True
        elif ch == "{": depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(s[:i + 1])
                except Exception:
                    return None
    return None


def discover_event_ids(web, slug: str, pages: int) -> List[str]:
    """Event ids (only) from /d/<metro>/all-events/. WAF-blocked IPs get [] -
    the caller treats that as 'nothing to hydrate', never an error."""
    ids: List[str] = []
    for page in range(1, pages + 1):
        try:
            time.sleep(1.2)
            r = web.get(f"https://www.eventbrite.com/d/{slug}/all-events/",
                        params={"page": page} if page > 1 else None, timeout=20)
        except Exception as exc:
            print(f"[eventbrite] {slug} p{page} discovery failed: {exc}")
            break
        if r.status_code in (403, 405, 429):
            print(f"[eventbrite] {slug} p{page} HTTP {r.status_code} - discovery blocked from this IP (run locally)")
            break
        sd = _server_data(r.text)
        results = (((sd or {}).get("search_data") or {}).get("events") or {}).get("results") or []
        if not results:
            break
        for ev in results:
            if ev.get("is_online_event") or ev.get("is_cancelled"):
                continue
            eid = str(ev.get("eventbrite_event_id") or ev.get("id") or "").strip()
            if eid.isdigit():
                ids.append(eid)
    return ids


# ---- official-API event -> NormalizedEvent -----------------------------------
def _category(ev: Dict[str, Any], override: Optional[str]) -> str:
    if override:
        return override
    name = str(((ev.get("category") or {}).get("name")) or "").strip().lower()
    return _EB_CATEGORY.get(name, "other")


def to_event(ev: Dict[str, Any], category_override: Optional[str] = None) -> Optional[NormalizedEvent]:
    if ev.get("online_event"):
        return None                                    # no place to pin an online event
    if (ev.get("status") or "live") not in ("live", "started"):
        return None                                    # draft/ended/canceled
    name = ((ev.get("name") or {}).get("text") or "").strip()
    start = ev.get("start") or {}
    start_utc, start_local = start.get("utc"), start.get("local")
    if not name or not (start_utc or start_local):
        return None
    venue = ev.get("venue") or {}
    addr = venue.get("address") or {}
    try:
        lat = float(addr.get("latitude"))
        lon = float(addr.get("longitude"))
    except (TypeError, ValueError):
        return None                                    # nowhere to pin it
    desc = ((ev.get("description") or {}).get("text") or "").strip() or None
    logo = ev.get("logo") or {}
    poster = None
    if isinstance(logo, dict):
        poster = (logo.get("original") or {}).get("url") or logo.get("url")
    date_key = (start_utc or start_local or "")[:10]
    nev = NormalizedEvent(
        source="eventbrite",
        source_id=str(ev.get("id") or make_fingerprint(name, date_key, venue.get("name"))),
        name=name,
        description=desc,
        start_local=start_local,
        start_utc=start_utc,
        timezone=start.get("timezone"),
        venue_name=venue.get("name") or addr.get("localized_area_display"),
        latitude=lat, longitude=lon,
        address=addr.get("address_1") or None,
        city=addr.get("city"), region=addr.get("region"),
        country=addr.get("country"), postal_code=addr.get("postal_code"),
        category=_category(ev, category_override),
        poster_image_url=poster,
        ticket_url=ev.get("url"),
    )
    nev.fingerprint = make_fingerprint(name, date_key, venue.get("name"))
    return nev


def hydrate(store: EventStore, api, eid: str) -> bool:
    r = _api_get(api, f"{API}/events/{eid}/", {"expand": "venue,category"})
    if r is None or r.status_code != 200:
        return False
    nev = to_event(r.json())
    if nev:
        store.upsert(nev)
        return True
    return False


# ---- your own organizations (full API path) ----------------------------------
def my_organization_ids(api) -> List[Dict[str, Any]]:
    out, cont = [], None
    while True:
        params = {"continuation": cont} if cont else None
        r = _api_get(api, f"{API}/users/me/organizations/", params)
        if r is None or r.status_code != 200:
            print(f"[eventbrite] couldn't list your organizations: HTTP {getattr(r,'status_code','ERR')}")
            break
        body = r.json()
        for o in body.get("organizations") or []:
            out.append({"id": str(o.get("id")), "name": o.get("name")})
        pag = body.get("pagination") or {}
        if pag.get("has_more_items") and pag.get("continuation"):
            cont = pag["continuation"]
        else:
            break
    return out


def ingest_organization(store: EventStore, api, org: Dict[str, Any]) -> int:
    kept, cont = 0, None
    base = f"{API}/organizations/{org['id']}/events/"
    for _ in range(40):
        params = {"expand": "venue,category", "status": "live",
                  "time_filter": "current_future", "order_by": "start_asc", "page_size": 50}
        if cont:
            params["continuation"] = cont
        r = _api_get(api, base, params)
        if r is None or r.status_code != 200:
            print(f"[eventbrite] org {org.get('name', org['id'])}: HTTP {getattr(r,'status_code','ERR')}")
            break
        body = r.json()
        for ev in body.get("events") or []:
            nev = to_event(ev)
            if nev:
                store.upsert(nev)
                kept += 1
        pag = body.get("pagination") or {}
        if pag.get("has_more_items") and pag.get("continuation"):
            cont = pag["continuation"]
        else:
            break
    print(f"[eventbrite] org {org.get('name', org['id'])}: kept {kept} events")
    return kept


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import Eventbrite events (official API) into the Mapsee store.")
    ap.add_argument("--config", required=True)
    ap.add_argument("--store", default="mapsee_events.json")
    a = ap.parse_args(argv)

    token = os.environ.get("EVENTBRITE_API_TOKEN", "").strip() or os.environ.get("EVENTBRITE_OAUTH_TOKEN", "").strip()
    if not token:
        print("[eventbrite] EVENTBRITE_API_TOKEN not set - skipping (no events imported).")
        return 0

    cfg = json.loads(open(a.config, encoding="utf-8").read())
    api = requests.Session()
    api.headers.update({"Authorization": f"Bearer {token}",
                        "User-Agent": "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)",
                        "Accept": "application/json"})
    web = requests.Session()
    web.headers.update({"User-Agent": WEB_UA, "Accept-Language": "en-US,en;q=0.8"})

    store = EventStore(a.store)
    total = 0

    # 1) your own organizations - the only org-level access the API allows
    if cfg.get("include_my_organizations"):
        mine = my_organization_ids(api)
        print(f"[eventbrite] your token can read {len(mine)} organization(s)")
        for org in mine:
            try:
                total += ingest_organization(store, api, org)
            except Exception as exc:
                print(f"[eventbrite] org {org.get('name','?')} FAILED: {exc}")

    # 2) metro discovery (ids only) -> official-API hydration
    pages = int(cfg.get("pages", 1))
    metros = cfg.get("metros") or []
    if not metros:
        print("[eventbrite] no metros in config - nothing to discover.")
    seen: set = set()
    blocked = False
    for m in metros:
        slug = m.get("slug")
        if not slug:
            continue
        ids = discover_event_ids(web, slug, pages)
        if not ids:
            # a WAF block on one metro means every metro is blocked - stop probing
            blocked = True
        fresh = [i for i in ids if i not in seen]
        seen.update(fresh)
        kept = 0
        for eid in fresh:
            try:
                kept += 1 if hydrate(store, api, eid) else 0
            except Exception as exc:
                print(f"[eventbrite] event {eid} FAILED: {exc}")
        total += kept
        print(f"[eventbrite] {m.get('name', slug)}: {len(fresh)} discovered, {kept} kept")
        if blocked:
            print("[eventbrite] discovery unavailable from this IP - stopping the metro sweep (run eventbrite_local.ps1 from a home connection).")
            break

    store.save()
    print(f"[eventbrite] done: +{total} events; store now holds {len(store.records)} unique events.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
