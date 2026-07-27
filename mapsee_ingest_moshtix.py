#!/usr/bin/env python3
"""
mapsee_ingest_moshtix.py - import public events from Moshtix (Australia / NZ live
events + music ticketing) via their official GraphQL API into the Mapsee store.

Moshtix exposes a public, anonymous GraphQL endpoint - getEvents returns publicly
listed events with no token, fully within their terms (developer.moshtix.com).
We link back to moshtix.com.au for tickets. Great AU music/gig coverage plus
comedy, markets, festivals, and community events.

  Endpoint : https://api.moshtix.com/v1/graphql  (POST)
  Query    : viewer.getEvents(eventStartDateFrom, publicOnly, searchableOnly,
             sortBy: STARTDATE, pageIndex/pageSize) → items { name, dates, venue
             (address + sometimes location), images, genre, eventUrl }
  Coords   : venue.location is often null anonymously, so each venue address is
             geocoded via Photon (cached, shared with the other feed adapters).

    python mapsee_ingest_moshtix.py --store feeds_events.json --within-days 120
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

from mapsee_ingest import NormalizedEvent, EventStore, make_fingerprint
from mapsee_ingest_jsonld import _geocode, _save_geo_cache, UA

GQL = "https://api.moshtix.com/v1/graphql"

QUERY = """
query($from: Date!, $to: Date, $page: Int!, $size: IntBetween1and200!) {
  viewer {
    getEvents(eventStartDateFrom: $from, eventStartDateTo: $to,
              publicOnly: true, searchableOnly: true, excludeClosed: true,
              includePastEvents: false, sortBy: STARTDATE, sortByDirection: ASC,
              pageIndex: $page, pageSize: $size) {
      totalCount
      pageInfo { hasNextPage }
      items {
        id name teaser description startDate endDate eventUrl
        genre { name }
        venue { name location { latitude longitude }
                address { line1 line2 locality region postCode country } }
        images { items { url } }
      }
    }
  }
}
"""

# Moshtix genre → Mapsee frontend category key (default music: the catalogue
# skews live music/gigs). The sync's keyword pass still promotes clear cases.
_GENRE = {
    "music": "music", "gig": "music", "gigs": "music", "concert": "music",
    "festival": "music", "electronic": "music", "hip hop": "music", "rock": "music",
    "comedy": "arts", "theatre": "arts", "theater": "arts", "arts": "arts",
    "cabaret": "arts", "film": "arts", "exhibition": "arts", "dance": "arts",
    "sport": "sports", "sports": "sports",
    "food": "food", "food & wine": "food", "food and wine": "food", "drink": "food",
    "market": "market", "markets": "market", "fashion": "market",
    "family": "kids", "kids": "kids",
    "workshop": "learning", "conference": "learning", "seminar": "learning", "talk": "learning",
    "community": "community",
}


def _fix_text(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    s = str(s)
    # some Moshtix copy is double-encoded UTF-8 (â€™ / â€“) — repair best-effort
    if "Ã" in s or "â€" in s:
        try:
            s = s.encode("latin-1").decode("utf-8")
        except Exception:
            pass
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def _image(node: Dict[str, Any]) -> Optional[str]:
    for it in ((node.get("images") or {}).get("items") or []):
        u = (it.get("url") or "").strip()
        if u and u.rstrip("/").endswith("uploads") is False and "/uploads/" in u and not u.endswith("/uploads/"):
            # upsize the thumbnail suffix (…xWxH) to a poster-worthy size
            return re.sub(r"x\d+x\d+$", "x600x600", u)
    return None


def to_event(node: Dict[str, Any], session, default_days: int) -> Optional[NormalizedEvent]:
    name = _fix_text(node.get("name"))
    start = (node.get("startDate") or "").strip()
    if not name or len(start) < 10:
        return None
    v = node.get("venue") or {}
    addr = v.get("address") or {}
    loc = v.get("location") or {}
    lat, lon = loc.get("latitude"), loc.get("longitude")
    if lat is None or lon is None:                          # anonymous → geocode the address
        parts = [addr.get("line1"), addr.get("locality"), addr.get("region"),
                 addr.get("postCode"), addr.get("country") or "Australia"]
        q = ", ".join(p for p in parts if p) or (v.get("name") or "")
        if not q:
            return None
        lat, lon = _geocode(session, q)
        if lat is None:
            return None                                    # nowhere to pin it
    genre = ((node.get("genre") or {}).get("name") or "").strip().lower()
    date_key = start[:10]
    nev = NormalizedEvent(
        source="moshtix",
        source_id=str(node.get("id")),
        name=name,
        description=_fix_text(node.get("description")) or _fix_text(node.get("teaser")),
        start_utc=start,                                   # UTC ISO; sync derives local from coords
        end_utc=(node.get("endDate") or "").strip() or None,
        venue_name=_fix_text(v.get("name")),
        latitude=float(lat), longitude=float(lon),
        address=_fix_text(addr.get("line1")),
        city=_fix_text(addr.get("locality")), region=addr.get("region"),
        country=addr.get("country"), postal_code=addr.get("postCode"),
        category=_GENRE.get(genre, "music"),
        poster_image_url=_image(node),
        ticket_url=node.get("eventUrl"),
    )
    nev.fingerprint = make_fingerprint(name, date_key, v.get("name"))
    return nev


def ingest(store: EventStore, session, within_days: int, max_pages: int) -> int:
    frm = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    to = (datetime.now(timezone.utc) + timedelta(days=within_days)).strftime("%Y-%m-%d")
    kept = seen = 0
    for page in range(0, max_pages):
        try:
            r = session.post(GQL, json={"query": QUERY, "variables": {
                "from": frm, "to": to, "page": page, "size": 200}}, timeout=45)
        except Exception as exc:
            print(f"[moshtix] page {page} request failed: {exc}")
            break
        if r.status_code != 200:
            print(f"[moshtix] page {page} HTTP {r.status_code}: {r.text[:160]}")
            break
        data = r.json()
        if data.get("errors"):
            print(f"[moshtix] GraphQL errors: {str(data['errors'])[:200]}")
            break
        conn = (((data.get("data") or {}).get("viewer") or {}).get("getEvents") or {})
        items = conn.get("items") or []
        if not items:
            break
        for node in items:
            seen += 1
            nev = to_event(node, session, within_days)
            if nev:
                store.upsert(nev)
                kept += 1
        if not (conn.get("pageInfo") or {}).get("hasNextPage"):
            break
        time.sleep(0.4)                                    # polite between pages
    print(f"[moshtix] kept {kept} of {seen} events across {page + 1} page(s)")
    return kept


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import public Moshtix (AU/NZ) events into the Mapsee store.")
    ap.add_argument("--store", default="feeds_events.json")
    ap.add_argument("--within-days", type=int, default=120)
    ap.add_argument("--max-pages", type=int, default=20)   # 20 × 200 = 4000 events cap
    a = ap.parse_args(argv)
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Content-Type": "application/json",
                            "Accept": "application/json"})
    store = EventStore(a.store)
    try:
        ingest(store, session, a.within_days, a.max_pages)
    except Exception as exc:
        print(f"[moshtix] FAILED: {exc}")
    store.save()
    _save_geo_cache()
    print(f"[moshtix] store now holds {len(store.records)} unique events.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
