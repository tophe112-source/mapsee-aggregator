#!/usr/bin/env python3
"""
mapsee_ingest_ubereats.py - Uber Eats Marketplace API -> pickup pins.

Uber Eats has NO public catalog/discovery API; marketplace-wide discovery comes
from the affiliate feed (see mapsee_ingest_affiliates.py). What Uber's
developer platform DOES offer is the Eats Marketplace API: OAuth2
client-credentials, then GET /v1/eats/stores returns the stores that have
AUTHORIZED this application (scope eats.store). That is the direct half of the
funnel: a restaurant claims its mapsee pin, authorizes the app on Uber, and its
name/address/coords/link sync straight from Uber with no scraping and no feed.

    UBER_CLIENT_ID=... UBER_CLIENT_SECRET=... python mapsee_ingest_ubereats.py --store feeds_events.json

Key-gated: silent no-op until UBER_CLIENT_ID/UBER_CLIENT_SECRET are set AND
Uber has whitelisted the scope. Returns zero stores until merchants authorize
the app - that is expected, not a failure. First live run: use --dump to print
the raw first response and adjust field fallbacks if Uber's shape differs.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

from mapsee_ingest import NormalizedEvent, EventStore, make_fingerprint

UA = "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)"
AUTH_URL = os.environ.get("UBER_AUTH_URL", "https://auth.uber.com/oauth/v2/token")
API_BASE = os.environ.get("UBER_API_BASE", "https://api.uber.com")
SCOPE = os.environ.get("UBER_SCOPE", "eats.store")


def get_token(session, cid: str, secret: str) -> str:
    r = session.post(AUTH_URL, data={
        "client_id": cid, "client_secret": secret,
        "grant_type": "client_credentials", "scope": SCOPE,
    }, timeout=30)
    if r.status_code != 200:
        # surface Uber's OAuth error code (invalid_client / invalid_scope /
        # invalid_grant) so the failure is diagnosable, not just "401"
        raise RuntimeError(f"token endpoint {r.status_code}: {r.text[:400]}")
    return r.json()["access_token"]


def fetch_stores(session, token: str, dump: bool = False) -> list:
    r = session.get(f"{API_BASE}/v1/eats/stores",
                    headers={"Authorization": f"Bearer {token}"}, timeout=30)
    r.raise_for_status()
    body = r.json() or {}
    if dump:
        print(json.dumps(body, indent=1)[:2000])
    return body.get("stores") or body.get("data") or []


def emit(store: EventStore, stores: list, days_ahead: int) -> int:
    today = datetime.now()
    total = 0
    for s in stores:
        name = (s.get("name") or "").strip()
        loc = s.get("location") or {}
        lat = loc.get("latitude") or s.get("latitude")
        lon = loc.get("longitude") or s.get("longitude")
        if not name or lat is None or lon is None:
            continue
        sid = str(s.get("store_id") or s.get("id") or name)
        link = (s.get("web_url") or s.get("url")
                or f"https://www.ubereats.com/store/{sid}")
        addr = loc.get("address") or loc.get("street_address")
        if isinstance(addr, list):
            addr = ", ".join(a for a in addr if a)
        desc = ("🛒 Order pickup on Uber Eats: " + link +
                "\n\nThis restaurant syncs directly from Uber Eats.")
        for d in range(days_ahead):
            day = today + timedelta(days=d)
            date_key = day.strftime("%Y-%m-%d")
            ev = NormalizedEvent(
                source="ubereats",
                source_id=f"{sid}#{date_key}",
                name=f"{name} · pickup",
                description=desc,
                start_local=f"{date_key}T11:00:00",
                end_local=f"{date_key}T21:00:00",
                venue_name=name,
                latitude=float(lat), longitude=float(lon),
                address=addr if isinstance(addr, str) else None,
                category="food",
                ticket_url=link,
            )
            ev.fingerprint = make_fingerprint(f"{name} · pickup", date_key, name)
            store.upsert(ev)
            total += 1
    return total


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import authorized Uber Eats stores into the Mapsee store.")
    ap.add_argument("--store", default="feeds_events.json")
    ap.add_argument("--days-ahead", type=int, default=3)
    ap.add_argument("--dump", action="store_true", help="print the raw stores response (field-map debugging)")
    ap.add_argument("--token", default=os.environ.get("UBER_ACCESS_TOKEN", ""),
                    help="use a ready OAuth token (e.g. from the dashboard OAuth Playground) instead of minting one")
    a = ap.parse_args(argv)
    session = requests.Session()
    session.headers.update({"User-Agent": UA})
    try:
        if a.token.strip():
            token = a.token.strip()                       # skip the mint: verify the API half directly
        else:
            cid = os.environ.get("UBER_CLIENT_ID", "").strip()
            secret = os.environ.get("UBER_CLIENT_SECRET", "").strip()
            if not cid or not secret:
                print("[ubereats] UBER_CLIENT_ID/UBER_CLIENT_SECRET unset - skipped")
                return 0
            token = get_token(session, cid, secret)
        stores = fetch_stores(session, token, dump=a.dump)
    except (requests.HTTPError, RuntimeError) as exc:
        # scope not yet whitelisted / no stores authorized: report, don't fail the job
        print(f"[ubereats] API refused: {exc}\n  (scope requested: {SCOPE})")
        return 0
    if not stores:
        print("[ubereats] 0 authorized stores (merchants authorize the app as they claim pins)")
        return 0
    st = EventStore(a.store)
    total = emit(st, stores, a.days_ahead)
    st.save()
    print(f"[ubereats] done: {len(stores)} stores -> {total} pickup windows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
