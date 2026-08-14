#!/usr/bin/env python3
"""
mapsee_dedupe_events.py — collapse the same imported event, filed twice, into
one row in the Mapsee events table.

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

NOT ONLY MARKETS, since 2026-08-12. Markets were simply the case somebody
measured. The same failure — a fingerprint whose inputs changed, so one event
was filed twice and nothing will ever revisit it — is not market-specific, and
markets were only ever the category that HAD a collapser. Measured over the
20,000 nearest upcoming aggregator rows: 407 clusters, 428 redundant rows, in
thirteen categories. Markets were 241 of them, which is the more interesting
number: they are the majority DESPITE being the only category swept, which is
what a time-boxed job quietly not finishing looks like (see --max-seconds and
the exit code below).

WHAT COUNTS AS THE SAME EVENT, and the one place this had to grow a second rule.
Same normalized title, same category, within --radius-km — plus a time key that
depends on the category:

  • market and anything else in DATE_KEYED: the LOCAL DATE. A market runs once
    on the day it runs, and its sources disagree about the clock time.
  • EVERYTHING ELSE: the exact start instant. A matinee and an evening
    performance of one show share a title, a venue and a date, and collapsing
    them would delete a real event that real people are attending separately.
    This is the whole reason the tool could not simply drop its category filter.

Different days of one market are still different events, and still never merged.

SAFETY. Scoped to `external_source = 'mapsee'`, asserted rather than trusted, so
a user's own events can never be matched — that is the guard that matters and it
is unchanged. `--category` narrows it further (`--category market` reproduces
the old behaviour exactly). A CLAIMED row is always the one kept: someone has
taken that event over. It refuses if any one cluster exceeds --max-cluster, or
if duplicates exceed --max-share of the rows examined. --dry-run reports and
deletes nothing.

EXIT CODE. Non-zero when it leaves a backlog — a --max-seconds budget that ran
out with rows still to delete. It used to print that and exit 0, and the
workflow ran it with `|| true`, so a job that had silently stopped keeping up
looked exactly like a clean one. Same reasoning as runCronTask in ../mapsee.

Env:  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
Run:  python mapsee_dedupe_events.py --dry-run
      python mapsee_dedupe_events.py
      python mapsee_dedupe_events.py --category market      # the old scope
"""
import argparse
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
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
COLUMNS = "id,title,category,lat,lon,starts_at,ends_at,claimed_at,created_at"

# Categories whose identity is a calendar DATE rather than an instant, because
# the thing happens once on the day it happens and the sources disagree about
# the clock. Everything NOT in here keys on the exact start instant — see the
# matinee argument in the module docstring. Keep this set small and justified:
# every addition is a licence to merge two rows that start at different times.
DATE_KEYED = {"market"}


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


def fetch_rows(base: str, auth: Dict[str, str], since: Optional[str]) -> List[Dict[str, Any]]:
    """Every aggregator row in scope (optionally only upcoming ones), paged."""
    rows: List[Dict[str, Any]] = []
    columns = COLUMNS
    page_size = PAGE
    # KEYSET, not OFFSET. Scoped to markets the catalog was small enough that
    # `offset=N` never hurt; across every category it is the whole problem.
    # OFFSET makes Postgres walk and discard all N skipped rows on EVERY page,
    # so the cost climbs with depth until a page is canceled by the service
    # role's statement timeout (57014) — which nothing here can raise from the
    # inside (../mapsee 0112). Measured while writing this: offsets 0-3000 came
    # back in ~0.3s each and a deep one died, and halving the page size did not
    # help ONCE, because the offset is the cost, not the page.
    #
    # So we carry a cursor instead: order by starts_at, then ask for the rows at
    # or after the last one we saw. Every page is the same cheap index range.
    #
    # `skip` is the one wrinkle. Many imports land on a round timestamp, so a
    # single starts_at can hold more rows than one page; the cursor then cannot
    # advance and would re-fetch the same rows forever. skip counts how many of
    # the CURRENT timestamp we have already taken, so it is an offset bounded by
    # one bucket rather than by the whole table.
    cursor = since or "1970-01-01T00:00:00Z"
    skip = 0
    while True:
        # quote() is not optional: the cursor comes back off a row as
        # "2026-08-13T01:00:00+00:00", and a bare + in a query string decodes to
        # a SPACE, so Postgres was handed "...01:00:00 00:00" and rejected it
        # (22007). `since` never showed this because strftime writes a Z.
        q = (f"{base}&select={columns}&order=starts_at.asc,id.asc"
             f"&limit={page_size}&offset={skip}"
             f"&starts_at=gte.{quote(cursor, safe='')}")
        r = requests.get(q, headers=auth, timeout=120)
        if r.status_code == 400 and "created_at" in columns and "created_at" in (r.text or ""):
            # Older schema — drop the creation time and re-ask.
            columns = COLUMNS.replace(",created_at", "")
            continue
        if _timed_out(r) and page_size > BATCH_MIN:
            # Kept as a backstop for a genuinely slow page, not as the fix for
            # depth — the cursor above is what stops depth mattering.
            page_size = max(BATCH_MIN, page_size // 2)
            print(f"Listing timed out — retrying with pages of {page_size}.")
            continue
        if r.status_code >= 300:
            sys.exit(f"Couldn't list imported events [{r.status_code}]: {r.text[:300]}")
        page = r.json() or []
        if not page:
            return rows
        rows.extend(page)
        if len(page) < page_size:
            return rows
        last = page[-1].get("starts_at") or cursor
        if last == cursor:
            skip += len(page)                        # still inside one timestamp
        else:
            cursor = last
            skip = sum(1 for x in page if x.get("starts_at") == last)


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
    groups: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = {}
    for row in rows:
        title = normalize_text(row.get("title"))
        if not title:
            continue                                 # an untitled row is not safely identifiable
        cat = (row.get("category") or "").strip().lower()
        if cat in DATE_KEYED:
            when = _local_date(row.get("starts_at") or "", row.get("lon"))
        else:
            # The exact instant, verbatim from the row. Two rows written by two
            # sources for one showing carry the same timestamp; a matinee and an
            # evening show do not, and that is exactly the distinction the date
            # key cannot make.
            when = (row.get("starts_at") or "").strip()
        if not when:
            continue
        # Category is IN the key: an event the reclassifier files under two
        # different categories is a classification disagreement, not a duplicate
        # import, and deleting one of them silently picks a winner.
        groups.setdefault((cat, title, when), []).append(row)

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
               max_seconds: int) -> Tuple[int, int]:
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
        # len(chunk), NOT batch. The last slice is short whenever batch does not
        # divide the list, so `i += batch` walks PAST the end — harmless while
        # the caller only wanted `deleted`, but it makes the shortfall below
        # negative (a real sweep of 4,866 rows reported "BACKLOG: -34" and
        # exited non-zero on a run that had finished everything).
        i += len(chunk)
    # The caller needs the SHORTFALL, not just the count: every early exit above
    # is a break, and a break that reports only what it managed reads as success.
    return deleted, max(0, len(ids) - i)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Collapse duplicate imported events in Supabase.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Report what would be removed; delete nothing.")
    ap.add_argument("--category", default=None,
                    help="Narrow to one category (e.g. market). Default: every "
                         "category, still only external_source=mapsee rows.")
    ap.add_argument("--radius-km", type=float, default=1.5,
                    help="Two same-name, same-time rows this close are one event (default 1.5).")
    ap.add_argument("--include-past", action="store_true",
                    help="Also dedupe events that have already ENDED "
                         "(default: anything still running or still to come).")
    ap.add_argument("--batch", type=int, default=BATCH_DEFAULT,
                    help=f"Ids per delete statement (default {BATCH_DEFAULT}).")
    ap.add_argument("--max-seconds", type=int, default=600,
                    help="Stop cleanly after this long (default 600).")
    # A grouping bug shows up as one cluster swallowing unrelated events, not as
    # a high overall share: the first run after an identity change legitimately
    # finds a duplicate for nearly every row, so a share limit tight enough to
    # catch a bug would block exactly the run that matters most.
    #
    # 8 was right when the cause was SOURCES — three feeds for one market cannot
    # make more than a few. Unscoped the cause is different and unbounded: a
    # fingerprint that drifts adds one row PER RUN, so a months-away listing
    # accumulates rows for as long as it stays in the window. The three worst are
    # university/museum exhibitions running since July, one row every few days.
    #
    # Measured over all 192,658 upcoming imported rows, 2026-08-12: 4,532
    # clusters, of which 4,271 are plain pairs and 225 are triples. The tail is
    # 22 fours, then single digits, and it STOPS at 10 — nothing above it at any
    # size. 12 sits above the observed ceiling with margin while staying far
    # below what a grouping bug looks like, which is hundreds in one cluster.
    ap.add_argument("--max-cluster", type=int, default=12,
                    help="Refuse if any one event clusters more rows than this "
                         "(default 12; the most seen in production is 10).")
    ap.add_argument("--max-share", type=float, default=0.75,
                    help="Backstop: refuse if duplicates exceed this share of the rows "
                         "examined (default 0.75).")
    ap.add_argument("--show", type=int, default=15, help="Examples to print (default 15).")
    a = ap.parse_args()

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not (url and key):
        sys.exit("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY (server-side secrets).")

    # SAFETY: imported events only. external_source is the guard that matters —
    # it is what keeps a user's own events out of reach — and it is asserted
    # rather than trusted, because everything below appends to this string.
    # category is no longer part of the assert (the tool is no longer
    # markets-only) but --category still narrows the scope when asked.
    flt = "external_source=eq.mapsee"
    if a.category:
        flt += f"&category=eq.{a.category}"
    assert "external_source=eq.mapsee" in flt, "refusing an unscoped delete"
    base = url.rstrip("/") + "/rest/v1/events?" + flt
    auth = {"apikey": key, "Authorization": f"Bearer {key}"}

    # STILL RUNNING COUNTS AS CURRENT, and this used to be a blind spot big
    # enough to hold 1,354 duplicate rows.
    #
    # The filter was `starts_at >= now`, which reads as "upcoming" and is not the
    # same thing as "live". A three-month exhibition that opened in July is
    # showing on the map right now, and it was invisible to this job the day
    # after it opened. mapsee_cleanup.py could not take it either — that one
    # deliberately spares a still-running event — so anything multi-day fell
    # between the two and nothing ever collected it. Which is precisely the
    # Localist recurring-event shape that produced the duplicates in the first
    # place: measured 2026-08-12, 1,354 redundant rows across fourteen
    # categories, market 546 and community 381 of them.
    #
    # So fetch without a floor and decide here, on ends_at. The table stays
    # bounded regardless, because cleanup prunes anything ENDED more than seven
    # days ago — the extra rows this walks are ~60k, seconds of listing.
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = fetch_rows(base, auth, None)
    if not a.include_past:
        def _live(r: Dict[str, Any]) -> bool:
            # ends_at when we have one, else the start: a row with no end is a
            # point in time, and once it is past it is past.
            return str(r.get("ends_at") or r.get("starts_at") or "") >= now_iso
        rows = [r for r in rows if _live(r)]
    scope = a.category or "all categories"
    print(f"Imported events examined ({scope}): {len(rows)}"
          f"{'' if a.include_past else ' (running or upcoming)'}")
    if not rows:
        return 0

    dupes = find_duplicates(rows, a.radius_km)
    drop = [r for _, dropped in dupes for r in dropped]
    claimed_kept = sum(1 for kept, _ in dupes if kept.get("claimed_at"))
    print(f"Events appearing more than once: {len(dupes)}; rows to remove: {len(drop)}"
          f"{f' ({claimed_kept} kept because they are claimed)' if claimed_kept else ''}")

    # Per category, because one category running away is the shape a bad time
    # key would take and the total hides it.
    by_cat: Dict[str, int] = {}
    for _, dropped in dupes:
        for r in dropped:
            c = (r.get("category") or "(none)").strip().lower()
            by_cat[c] = by_cat.get(c, 0) + 1
    if by_cat:
        print("  by category: " + ", ".join(
            f"{c} {n}" for c, n in sorted(by_cat.items(), key=lambda kv: -kv[1])))

    for kept, dropped in dupes[:a.show]:
        print(f"  keep [{kept.get('category')}] {kept.get('title')!r} "
              f"{(kept.get('starts_at') or '')[:16]}  drop {len(dropped)}")
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

    deleted, left = delete_ids(base, auth, [r["id"] for r in drop if r.get("id")],
                               max(BATCH_MIN, a.batch), a.max_seconds)
    print(f"Deleted {deleted} duplicate event(s).")
    if left:
        # Loud on purpose. This is the state the job was in for weeks: doing
        # some of the work, reporting it cheerfully, and falling further behind.
        print(f"BACKLOG: {left} duplicate row(s) not deleted this run.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
