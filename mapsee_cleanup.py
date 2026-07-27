#!/usr/bin/env python3
"""
mapsee_cleanup.py — delete aggregator-imported events that are in the past, to
keep the Mapsee events table lean (and the sync's only-new lookup fast).

SAFETY: this is scoped to `external_source = 'mapsee'` ONLY — the events imported
by this aggregator. User/community events (external_source IS NULL) are never
matched, so they can't be deleted. Every request also carries a `starts_at`
cutoff, so it can never become an unfiltered delete.

Default keeps events for a WEEK after they start, so attendees can keep chatting
about a show before it disappears. Aggregator events store ends_at = NULL, so a
start-time cutoff reliably means "over" for single-day events; if it ever removed a
still-upcoming event, the next ingest run would simply re-add it (90-day window).

Env:  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
Run:  python mapsee_cleanup.py [--older-than-days 7] [--dry-run]
"""
import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")


def main() -> int:
    ap = argparse.ArgumentParser(description="Delete past aggregator events from Supabase.")
    ap.add_argument("--older-than-days", type=int, default=7,
                    help="Delete aggregator events that started more than N days ago "
                         "(default 7 = a week of post-event chat).")
    ap.add_argument("--dry-run", action="store_true", help="Report the count; delete nothing.")
    a = ap.parse_args()

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not (url and key):
        sys.exit("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY (server-side secrets).")

    cutoff = (datetime.now(timezone.utc) - timedelta(days=max(0, a.older_than_days))
              ).strftime("%Y-%m-%dT%H:%M:%SZ")

    # SAFETY: always scoped to imported events AND a past cutoff — never unfiltered.
    flt = f"external_source=eq.mapsee&starts_at=lt.{cutoff}"
    assert "external_source=eq.mapsee" in flt and "starts_at=lt." in flt, "refusing an unscoped delete"
    base = url.rstrip("/") + "/rest/v1/events?" + flt
    auth = {"apikey": key, "Authorization": f"Bearer {key}"}

    # How many match?
    cnt = requests.get(base + "&select=external_id",
                       headers=dict(auth, **{"Range-Unit": "items", "Range": "0-0", "Prefer": "count=exact"}),
                       timeout=60)
    total = cnt.headers.get("Content-Range", "*/?").split("/")[-1]
    print(f"Aggregator events that started before {cutoff}: {total}")

    if a.dry_run:
        print("Dry run — nothing deleted.")
        return 0

    resp = requests.delete(base, headers=dict(auth, Prefer="count=exact"), timeout=300)
    if resp.status_code >= 300:
        sys.exit(f"Delete failed [{resp.status_code}]: {resp.text[:300]}")
    print(f"Deleted past aggregator events (HTTP {resp.status_code}); {total} were eligible.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
