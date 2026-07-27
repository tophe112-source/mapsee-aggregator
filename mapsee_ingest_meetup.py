#!/usr/bin/env python3
"""
mapsee_ingest_meetup.py — import public Meetup events near a point via Meetup's
GraphQL API into the Mapsee store, for the community / learning layers.

Meetup's original /gql GraphQL endpoint was retired with the February 2025 API
release (it now returns an HTML 404 page); the current endpoint is /gql-ext and
the old keywordSearch query was split into eventSearch + groupSearch. Verified
against live schema introspection (July 2026):

  eventSearch(filter: EventSearchFilter!, first: Int)
    EventSearchFilter: query String! (REQUIRED — an empty keyword returns
    nothing, so we sweep a set of broad keywords per metro and dedupe via the
    store's fingerprint upsert), lat Float!, lon Float!, radius Float,
    startDateRange/endDateRange DateTime ("...T00:00:00Z" accepted)
  Event: venue.lon (was lng), featuredEventPhoto (was image)

An OAuth bearer token (MEETUP_OAUTH_TOKEN, see meetup_token.py) is sent when
present but eventSearch also answers unauthenticated — so a missing token logs
a note and proceeds instead of skipping.

    python mapsee_ingest_meetup.py --latlong 47.6062,-122.3321 --radius 25 --store mapsee_events.json

NOTE: Meetup's GraphQL schema evolves; if a field is renamed again, the printed
GraphQL errors from a run name the offending field — adjust QUERY / to_event,
the rest of the pipeline is unaffected.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

from mapsee_ingest import NormalizedEvent, EventStore, make_fingerprint

GQL = "https://api.meetup.com/gql-ext"

# eventSearch REQUIRES a keyword; these broad ones together approximate the old
# "everything nearby" sweep. Dupes across keywords collapse in store.upsert.
# Each entry is keyword:mapsee-category — the keyword that FOUND an event is a
# decent guess at its layer (first keyword to see it wins; the sync's keyword
# pass can still promote volunteer). Bare keywords default to community.
# One broad keyword per Mapsee layer — trimmed from 12 to 8 by dropping the
# redundant ones (cooking/popup fold into food, games into social, learning into
# tech), which cuts ~33% of the per-metro Meetup requests with ~no coverage loss.
DEFAULT_KEYWORDS = ("social:community,music:music,food:food,tech:learning,"
                    "outdoors:outdoors,art:arts,sports:sports,volunteer:volunteer")

QUERY = """
query($query: String!, $lat: Float!, $lon: Float!, $radius: Float,
      $start: DateTime, $end: DateTime, $first: Int!) {
  eventSearch(filter: {query: $query, lat: $lat, lon: $lon, radius: $radius,
                       startDateRange: $start, endDateRange: $end}, first: $first) {
    edges { node {
      id
      title
      eventUrl
      dateTime
      endTime
      description
      venue { name lat lon address city state postalCode country }
      group { name urlname }
      featuredEventPhoto { standardUrl highResUrl }
    } }
  }
}
"""


def to_event(ev: Dict[str, Any], category: str = "community") -> Optional[NormalizedEvent]:
    title = (ev.get("title") or "").strip()
    start = ev.get("dateTime")
    if not title or not start:
        return None
    v = ev.get("venue") or {}
    lat, lon = v.get("lat"), v.get("lon")
    if lat is None or lon is None:
        return None                                        # online / no venue -> can't map it
    group = (ev.get("group") or {}).get("name")
    desc = (ev.get("description") or "").strip() or None
    if desc:
        desc = " ".join(desc.split())
    nev = NormalizedEvent(
        source="meetup",
        source_id=str(ev.get("id")),
        name=title,
        description=desc,
        start_local=start,
        venue_name=v.get("name") or group,
        latitude=float(lat), longitude=float(lon),
        address=v.get("address"), city=v.get("city"), region=v.get("state"),
        country=v.get("country"), postal_code=v.get("postalCode"),
        category=category,                                 # from the keyword that found it
        promoter=group,
        # standardUrl/highResUrl are complete image URLs; baseUrl is just a
        # meetupstatic path PREFIX (a broken link if used directly)
        poster_image_url=(ev.get("featuredEventPhoto") or {}).get("standardUrl")
                      or (ev.get("featuredEventPhoto") or {}).get("highResUrl"),
        ticket_url=ev.get("eventUrl"),
    )
    nev.fingerprint = make_fingerprint(title, str(start)[:10], v.get("name") or group)
    return nev


def ingest(store: EventStore, session, token: str, lat: str, lon: str, radius: int,
           first: int, keywords: list, within_days: int) -> int:
    now = datetime.now(timezone.utc)
    start = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    end = (now + timedelta(days=within_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    kept = seen = 0
    for kw, category in keywords:
        r = session.post(
            GQL,
            headers=headers,
            json={"query": QUERY, "variables": {
                "query": kw, "lat": float(lat), "lon": float(lon),
                "radius": float(radius), "start": start, "end": end, "first": int(first)}},
            timeout=30,
        )
        if r.status_code != 200:
            print(f"[meetup] {lat},{lon} '{kw}' HTTP {r.status_code}: {r.text[:200]}")
            continue
        data = r.json()
        if data.get("errors"):
            print(f"[meetup] '{kw}' GraphQL errors: {str(data['errors'])[:200]}")
        edges = (((data.get("data") or {}).get("eventSearch") or {}).get("edges")) or []
        seen += len(edges)
        for e in edges:
            nev = to_event(e.get("node") or {}, category)
            if nev:
                store.upsert(nev)
                kept += 1
        time.sleep(0.3)                                    # be polite across the keyword sweep
    print(f"[meetup] {lat},{lon}: kept {kept} of {seen} across {len(keywords)} keywords")
    return kept


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import public Meetup events near a point into the Mapsee store.")
    ap.add_argument("--latlong", required=True, help="lat,lon  e.g. 47.6062,-122.3321")
    ap.add_argument("--radius", type=int, default=25, help="miles")
    ap.add_argument("--first", type=int, default=200)
    ap.add_argument("--within-days", type=int, default=90)
    ap.add_argument("--keywords", default=DEFAULT_KEYWORDS,
                    help="comma-separated keyword[:category] sweep (eventSearch requires a keyword)")
    ap.add_argument("--store", default="mapsee_events.json")
    ap.add_argument("--token", default=os.environ.get("MEETUP_OAUTH_TOKEN"))
    a = ap.parse_args(argv)
    if not a.token:
        print("[meetup] no MEETUP_OAUTH_TOKEN — proceeding unauthenticated (eventSearch allows it)")
    try:
        lat, lon = a.latlong.split(",")
    except ValueError:
        sys.exit("--latlong must look like  47.6062,-122.3321")
    keywords = [(k.split(":")[0].strip(), (k.split(":")[1].strip() if ":" in k else "community"))
                for k in a.keywords.split(",") if k.strip()]
    session = requests.Session()
    session.headers.update({"User-Agent": "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)"})
    store = EventStore(a.store)
    try:
        ingest(store, session, a.token or "", lat.strip(), lon.strip(), a.radius,
               a.first, keywords, a.within_days)
    except Exception as exc:
        print(f"[meetup] {a.latlong} FAILED: {exc}")
    store.save()
    print(f"[meetup] store now holds {len(store.records)} unique events.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
