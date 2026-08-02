#!/usr/bin/env python3
"""
mapsee_dedupe_markets.py — collapse the same market, imported from two sources,
into one row in the Mapsee events table.

WHY THIS EXISTS. A market reaches the map from up to three places: a curated
city list, OpenStreetMap's `amenity=marketplace`, and the USDA Local Food
Directories. They agree on the name and on the coordinates to within a few
hundred metres, and disagree on the address string — "Ballard Ave NW & Vernon
Pl NW" vs "5345 Ballard Ave NW" vs "5301 Ballard Avenue Northwest". The import
fingerprint used to be keyed on that address, so one market was filed three
times and drew three pins.

mapsee_ingest_markets.py now keys identity on the name plus a ~5km geohash cell,
which collapses the vast majority at import. This handles the two cases that
cannot:

  • the BACKLOG — rows already in the table under the old address-keyed
    fingerprint, which no import will ever revisit;
  • the RESIDUE — a market whose sources land either side of a geohash cell
    boundary. Import cannot see it (sources run on different weekdays and the
    store is rebuilt each run, so the duplicates are never in memory together);
    the database is the one place where every source's rows do meet.

Two rows are the same market when the normalized title matches, the local date
matches, and they are within --radius-km of each other. Different weekdays of
one market are different events and are never merged: the date is part of the key.

SAFETY. Scoped to `external_source = 'mapsee'` AND `category = 'market'`, so
user/community events cannot be matched. A CLAIMED row is always the one kept —
someone has taken that event over. It refuses to run if the duplicates come to
more than --max-share of the rows examined, which is the shape a bug in the
grouping would take. --dry-run reports and deletes nothing.

Env:  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
Run:  python mapsee_dedupe_markets.py --dry-run
      python mapsee_dedupe_markets.py
"""
import argparse
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

from mapsee_ingest import normalize_text

PAGE = 1000                    # rows per PostgREST fetch
BATCH_DEFAULT = 100            # ids per delete statement — see mapsee_cleanup.py
BATCH_MIN = 10
TIMEOUT_CODE = "57014"         # postgres: canceling statement due to statement timeout
COLUMNS = "id,title,lat,lon,starts_at,claimed_at,created_at"


def _timed_out(resp) -> bool:
    return resp.status_code >= 500 and TIMEOUT_CODE in (resp.text or "")


def _km(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
    """Haversine, kilometres."""
    r = 6371.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp = p2 - p1
    dl = math.radians(b_lon - a_lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def _local_date(starts_at: str, lon: Optional[float]) -> str:
    """Calendar date at the market, approximated from longitude.

    starts_at is UTC, and a 9 a.m. Pacific market is 16:00Z — grouping on the UTC
    date would file an evening market's duplicates on two different days. The
    offset only has to be consistent between two rows of the SAME market, and
    they are within a few hundred metres of each other, so lon/15 is exact enough
    and needs no timezone table.
    """
    try:
        dt = datetime.fromisoformat(starts_at.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return ""
    if lon is not None:
        dt = dt + timedelta(hours=lon / 15.0)
    return dt.date().isoformat()


def fetch_markets(base: str, auth: Dict[str, str], since: Optional[str]) -> List[Dict[str, Any]]:
    """Every aggregator market row (optionally only upcoming ones), paged."""
    rows: List[Dict[str, Any]] = []
    columns = COLUMNS
    offset = 0
    while True:
        q = (f"{base}&select={columns}&order=id.asc&limit={PAGE}&offset={offset}")
        if since:
            q += f"&starts_at=gte.{since}"
        r = requests.get(q, headers=auth, timeout=120)
        if r.status_code == 400 and "created_at" in columns and "created_at" in (r.text or ""):
            # Older schema — order by id alone instead of creation time.
            columns = COLUMNS.replace(",created_at", "")
            continue
        if r.status_code >= 300:
            sys.exit(f"Couldn't list market events [{r.status_code}]: {r.text[:300]}")
        page = r.json() or []
        rows.extend(page)
        if len(page) < PAGE:
            return rows
        offset += PAGE


def cluster(group: List[Dict[str, Any]], radius_km: float) -> List[List[Dict[str, Any]]]:
    """Split same-name/same-date rows into one cluster per physical market.

    Single-link: A joins a cluster if it is within radius of ANY member, because
    three sources of one market form a chain rather than a circle around a point.
    Groups are tiny (a handful of rows), so the quadratic scan is free.
    """
    out: List[List[Dict[str, Any]]] = []
    for row in group:
        lat, lon = row.get("lat"), row.get("lon")
        if lat is None or lon is None:
            out.append([row])                        # no coordinates -> never merged
            continue
        for c in out:
            if any(m.get("lat") is not None
                   and _km(lat, lon, m["lat"], m["lon"]) <= radius_km for m in c):
                c.append(row)
                break
        else:
            out.append([row])
    return out


def _rank(row: Dict[str, Any]) -> Tuple[int, str, str]:
    """Sort key for "which row survives": claimed first, then oldest, then id.
    Keeping the oldest row keeps whatever already points at it."""
    return (0 if row.get("claimed_at") else 1,
            row.get("created_at") or "",
            row.get("id") or "")


def find_duplicates(rows: List[Dict[str, Any]], radius_km: float
                    ) -> List[Tuple[Dict[str, Any], List[Dict[str, Any]]]]:
    """-> [(kept, [dropped, ...]), ...] for every cluster holding more than one row."""
    groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for row in rows:
        title = normalize_text(row.get("title"))
        if not title:
            continue                                 # an untitled row is not safely identifiable
        day = _local_date(row.get("starts_at") or "", row.get("lon"))
        if not day:
            continue
        groups.setdefault((title, day), []).append(row)

    dupes: List[Tuple[Dict[str, Any], List[Dict[str, Any]]]] = []
    for group in groups.values():
        if len(group) < 2:
            continue
        for c in cluster(group, radius_km):
            if len(c) < 2:
                continue
            c.sort(key=_rank)
            dupes.append((c[0], c[1:]))
    return dupes


def delete_ids(base: str, auth: Dict[str, str], ids: List[str], batch: int,
               max_seconds: int) -> int:
    """Delete by id, still carrying the scoping filter — the primary keys alone
    would identify the rows, but then a bug up in the grouping could reach a row
    this job is not allowed to touch."""
    deleted = 0
    started = time.monotonic()
    i = 0
    while i < len(ids):
        left = max_seconds - (time.monotonic() - started)
        if left <= 0:
            print(f"Reached the {max_seconds}s budget — {len(ids) - i} left for the next run.")
            break
        chunk = ids[i:i + batch]
        resp = requests.delete(f"{base}&id=in.({','.join(chunk)})", headers=auth,
                               timeout=min(120, max(10, int(left))))
        if _timed_out(resp):
            if batch <= BATCH_MIN:
                print(f"Even {batch} ids time out — stopping; re-run to continue.")
                break
            batch = max(BATCH_MIN, batch // 2)
            print(f"Delete timed out — retrying with batches of {batch}.")
            continue
        if resp.status_code >= 300:
            print(f"Delete failed [{resp.status_code}]: {resp.text[:300]}")
            break
        deleted += len(chunk)
        i += batch
    return deleted


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Collapse duplicate imported market events in Supabase.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Report what would be removed; delete nothing.")
    ap.add_argument("--radius-km", type=float, default=1.5,
                    help="Two same-name, same-date rows this close are one market (default 1.5).")
    ap.add_argument("--include-past", action="store_true",
                    help="Also dedupe events that have already started "
                         "(default: upcoming only — mapsee_cleanup.py prunes the past).")
    ap.add_argument("--batch", type=int, default=BATCH_DEFAULT,
                    help=f"Ids per delete statement (default {BATCH_DEFAULT}).")
    ap.add_argument("--max-seconds", type=int, default=600,
                    help="Stop cleanly after this long (default 600).")
    # A grouping bug shows up as one cluster swallowing unrelated markets, not as
    # a high overall share: the first run after an identity change legitimately
    # finds a duplicate for nearly every market, so a share limit tight enough to
    # catch a bug would block exactly the run that matters most.
    ap.add_argument("--max-cluster", type=int, default=8,
                    help="Refuse if any one market clusters more rows than this "
                         "(default 8) — three sources cannot make more than a few.")
    ap.add_argument("--max-share", type=float, default=0.75,
                    help="Backstop: refuse if duplicates exceed this share of the rows "
                         "examined (default 0.75).")
    ap.add_argument("--show", type=int, default=15, help="Examples to print (default 15).")
    a = ap.parse_args()

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not (url and key):
        sys.exit("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY (server-side secrets).")

    # SAFETY: imported market events only. Asserted rather than trusted, because
    # everything below appends to this string.
    flt = "external_source=eq.mapsee&category=eq.market"
    assert "external_source=eq.mapsee" in flt and "category=eq.market" in flt, \
        "refusing an unscoped delete"
    base = url.rstrip("/") + "/rest/v1/events?" + flt
    auth = {"apikey": key, "Authorization": f"Bearer {key}"}

    since = None if a.include_past else datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = fetch_markets(base, auth, since)
    print(f"Imported market events examined: {len(rows)}"
          f"{'' if a.include_past else ' (upcoming only)'}")
    if not rows:
        return 0

    dupes = find_duplicates(rows, a.radius_km)
    drop = [r for _, dropped in dupes for r in dropped]
    claimed_kept = sum(1 for kept, _ in dupes if kept.get("claimed_at"))
    print(f"Markets appearing more than once: {len(dupes)}; rows to remove: {len(drop)}"
          f"{f' ({claimed_kept} kept because they are claimed)' if claimed_kept else ''}")

    for kept, dropped in dupes[:a.show]:
        print(f"  keep {kept.get('title')!r} {(kept.get('starts_at') or '')[:16]}"
              f"  drop {len(dropped)}")
    if len(dupes) > a.show:
        print(f"  ... and {len(dupes) - a.show} more")

    if not drop:
        print("Nothing to do.")
        return 0

    biggest = max(((len(dropped) + 1, kept) for kept, dropped in dupes), key=lambda t: t[0])
    if biggest[0] > a.max_cluster:
        sys.exit(f"Refusing: {biggest[1].get('title')!r} clustered {biggest[0]} rows into one "
                 f"market (limit {a.max_cluster}). Lower --radius-km, or raise --max-cluster "
                 f"deliberately.")

    share = len(drop) / len(rows)
    if share > a.max_share:
        sys.exit(f"Refusing to delete {share:.0%} of the market rows examined "
                 f"(limit {a.max_share:.0%}). Re-check --radius-km, or raise --max-share "
                 f"deliberately.")

    if a.dry_run:
        print("Dry run — nothing deleted.")
        return 0

    deleted = delete_ids(base, auth, [r["id"] for r in drop if r.get("id")],
                         max(BATCH_MIN, a.batch), a.max_seconds)
    print(f"Deleted {deleted} duplicate market event(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
