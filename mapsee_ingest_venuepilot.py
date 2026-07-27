#!/usr/bin/env python3
"""
mapsee_ingest_venuepilot.py - import events from venues that sell tickets through
VenuePilot, via VenuePilot's PUBLIC GraphQL API. No key needed (this is the same
public endpoint the venues' own embedded calendar widgets call).

VenuePilot powers a growing slice of independent music rooms (e.g. Baba Yaga in
Seattle's Pioneer Square). Point this at a venue's VenuePilot account id and it
pulls their upcoming shows - name, date/time, description, ticket link, lineup +
artist streaming links - all structured.

    python mapsee_ingest_venuepilot.py --config venuepilot_sources.json \
        --store feeds_events.json

Config (venuepilot_sources.json):
    { "sites": [
        { "name": "Baba Yaga",
          "account_ids": [2906],
          "category": "music",
          "within_days": 120,
          "venue": { "name": "Baba Yaga", "address": "124 S Washington St",
                     "city": "Seattle", "region": "WA", "postal_code": "98104" } } ] }

The public API exposes only the venue NAME (no address/coordinates), so each
single-venue site supplies a "venue" block - address+city+region (the sync
geocodes it) or lat+lon - exactly like jsonld_sources.json. Find an account id
in a venue site's widget config (window.venuepilotSettings.general.accountIds).
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

from mapsee_ingest import NormalizedEvent, EventStore, make_fingerprint

API = "https://www.venuepilot.co/graphql"
UA = "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)"

QUERY = """
query($ids:[Int!],$sd:String,$ed:String,$limit:Int,$page:Int){
  paginatedEvents(arguments:{accountIds:$ids,startDate:$sd,endDate:$ed,limit:$limit,page:$page}){
    collection{
      id name date startTime doorTime status description websiteUrl ticketsUrl
      venue{ name }
      announceArtists{ name spotify website }
    }
    metadata{ totalCount totalPages currentPage }
  }
}"""

_TAG = re.compile(r"<[^>]+>")


def _clean(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    s = html_mod.unescape(_TAG.sub(" ", str(s)))
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def to_event(ev: Dict[str, Any], category: str, venue_default: Dict[str, Any]) -> Optional[NormalizedEvent]:
    name = (ev.get("name") or "").strip()
    date = (ev.get("date") or "").strip()
    if not name or len(date) < 10:
        return None
    if "cancel" in str(ev.get("status") or "").lower():
        return None
    t = (ev.get("startTime") or ev.get("doorTime") or "19:00:00")[:8]
    start_local = f"{date}T{t}"                       # naive local; sync -> UTC via venue coords
    artists = [a.get("name") for a in (ev.get("announceArtists") or []) if a.get("name")]
    artists = [str(a).strip() for a in artists if str(a or "").strip()]
    spotify = next((a.get("spotify") for a in (ev.get("announceArtists") or []) if a.get("spotify")), None)
    vd = venue_default or {}
    nev = NormalizedEvent(
        source="venuepilot",
        source_id=str(ev.get("id") or make_fingerprint(name, date[:10], vd.get("name"))),
        name=name,
        description=_clean(ev.get("description")),
        start_local=start_local,
        venue_name=(ev.get("venue") or {}).get("name") or vd.get("name"),
        latitude=vd.get("lat"), longitude=vd.get("lon"),
        address=vd.get("address"), city=vd.get("city"), region=vd.get("region"),
        country=vd.get("country"), postal_code=vd.get("postal_code"),
        category=category,
        lineup=artists,
        spotify_url=spotify,
        ticket_url=ev.get("ticketsUrl") or ev.get("websiteUrl"),
    )
    nev.fingerprint = make_fingerprint(name, date[:10], nev.venue_name)
    return nev


def ingest_site(store: EventStore, session, site: Dict[str, Any]) -> int:
    ids = site.get("account_ids") or []
    if not ids:
        print(f"[venuepilot] {site.get('name','?')}: no account_ids"); return 0
    category = site.get("category", "music")
    venue = site.get("venue") or {}
    now = datetime.now(timezone.utc)
    sd = now.strftime("%Y-%m-%d")
    ed = (now + timedelta(days=int(site.get("within_days", 120)))).strftime("%Y-%m-%d")
    kept = 0
    for page in range(1, 21):                         # cap ~1000 events/site
        variables = {"ids": ids, "sd": sd, "ed": ed, "limit": 50, "page": page}
        try:
            r = session.post(API, data=json.dumps({"query": QUERY, "variables": variables}), timeout=30)
            time.sleep(0.3)
        except Exception as exc:
            print(f"[venuepilot] {site.get('name')} p{page} failed: {exc}"); break
        if r.status_code != 200:
            print(f"[venuepilot] {site.get('name')} p{page} HTTP {r.status_code}"); break
        body = r.json()
        if body.get("errors"):
            print(f"[venuepilot] {site.get('name')} query errors: {body['errors'][:1]}"); break
        node = (body.get("data") or {}).get("paginatedEvents") or {}
        rows = node.get("collection") or []
        if not rows:
            break
        for ev in rows:
            nev = to_event(ev, category, venue)
            if nev:
                store.upsert(nev)
                kept += 1
        meta = node.get("metadata") or {}
        if page >= int(meta.get("totalPages") or page):
            break
    print(f"[venuepilot] {site.get('name')}: kept {kept} events")
    return kept


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import VenuePilot venue events into the Mapsee store.")
    ap.add_argument("--config", required=True)
    ap.add_argument("--store", default="mapsee_events.json")
    a = ap.parse_args(argv)

    cfg = json.loads(open(a.config, encoding="utf-8").read())
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Content-Type": "application/json", "Accept": "application/json"})
    store = EventStore(a.store)
    total = 0
    for site in cfg.get("sites", []):
        try:
            total += ingest_site(store, session, site)
        except Exception as exc:
            print(f"[venuepilot] {site.get('name','?')} FAILED: {exc}")
    store.save()
    print(f"[venuepilot] done: +{total} events; store now holds {len(store.records)} unique events.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
