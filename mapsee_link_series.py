#!/usr/bin/env python3
"""
mapsee_link_series.py — chain a repeating imported listing into ONE series.

WHY THIS EXISTS. A weekly farmers' market, a nightly trivia, a daily museum
tour: the source publishes each occurrence as its own listing, so the table
holds forty rows with the same title on the same corner and nothing tying them
together. `series_id` is the column that says "these are one thing happening
again", and no imported event has ever carried it — only events a human made
from inside the app, where the host links them by hand.

What that costs, downstream:

  • The MAP sizes a dot by how much is happening at a spot, and forty rows of
    one market drew the biggest circle in the city (see eventActs in
    ../mapsee/site/js/app.js — it now folds same-title repeats client-side,
    which fixes the picture but not the data).
  • Nearby lists the same market forty times. collapseSeries() already knows how
    to fold a series down to its next occurrence and tag it 🔁 — it just never
    had a series to fold.
  • The event sheet's "other dates" list is empty for every imported event.

WHY A SEPARATE PASS, not the ingest. The occurrences never meet in memory: the
store is rebuilt each run, sources run on different weekdays, and `--only-new`
(the CI default) means a run sees this week's rows and not the thirty already in
the table. The database is the one place a series is visible whole — the same
argument mapsee_dedupe_events.py makes about duplicates. It also means this
fixes the BACKLOG, which an ingest-time change never could.

WHAT COUNTS AS A SERIES. Same normalized title, within --radius-km of each
other, on at least --min-dates DIFFERENT local dates, with no gap longer than
--max-gap-days between consecutive dates. The gap rule is what keeps "Winter
Market" 2026 and "Winter Market" 2027 two separate things while keeping every
Saturday of one season together; a run that breaks at a longer gap simply
becomes two series, which is what it is.

Rows sharing a title on the SAME date are duplicates, not a series — that is
mapsee_dedupe_events.py's job, and chaining them here would paper over it. They
count once toward the date test and are still linked, so a series survives a
source publishing one occurrence twice.

SAFETY. Scoped to `external_source = 'mapsee'`, so community events cannot be
touched. CLAIMED rows are skipped entirely — a real person owns that event and
their series is theirs to set. An existing series_id in a group WINS, so a
host's chain is extended rather than replaced, and re-runs are idempotent.
Refuses to run if it would stamp more than --max-share of the rows examined, or
if one group swallows more than --max-cluster rows — both the shape a bug in the
grouping would take. --dry-run reports and writes nothing.

Env:  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
Run:  python mapsee_link_series.py --dry-run
      python mapsee_link_series.py
"""
import argparse
import math
import os
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

from mapsee_ingest import normalize_text

PAGE = 1000                    # rows per PostgREST fetch
BATCH_DEFAULT = 100            # ids per PATCH statement — see mapsee_cleanup.py
BATCH_MIN = 10
TIMEOUT_CODE = "57014"         # postgres: canceling statement due to statement timeout
COLUMNS = "id,title,lat,lon,starts_at,series_id,claimed_at"


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
    """Calendar date at the venue, approximated from longitude.

    starts_at is UTC, so a 7 p.m. Pacific trivia night is 03:00Z the NEXT day and
    grouping on the UTC date would call Tuesday's and Wednesday's occurrence the
    same evening. The offset only has to be consistent between rows of the same
    event, which share a longitude — same reasoning as mapsee_dedupe_events.
    """
    if not starts_at:
        return ""
    try:
        dt = datetime.fromisoformat(starts_at.replace("Z", "+00:00"))
    except ValueError:
        return starts_at[:10]
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    hours = max(-12, min(14, round((lon or 0.0) / 15.0)))
    return dt.astimezone(timezone(timedelta(hours=hours))).strftime("%Y-%m-%d")


def fetch_events(base: str, auth: Dict[str, str], since: Optional[str]) -> List[Dict[str, Any]]:
    """Every imported event in scope, paged."""
    rows: List[Dict[str, Any]] = []
    offset = 0
    while True:
        q = f"{base}&select={COLUMNS}&order=id.asc&limit={PAGE}&offset={offset}"
        if since:
            q += f"&starts_at=gte.{since}"
        r = requests.get(q, headers=auth, timeout=120)
        if r.status_code >= 300:
            sys.exit(f"Couldn't list imported events [{r.status_code}]: {r.text[:300]}")
        page = r.json() or []
        rows.extend(page)
        if len(page) < PAGE:
            return rows
        offset += PAGE


def cluster(group: List[Dict[str, Any]], radius_km: float) -> List[List[Dict[str, Any]]]:
    """Split same-title rows into one cluster per physical venue.

    Single-link: a row joins a cluster if it is within radius of ANY member,
    because a source that nudges its coordinates between occurrences forms a
    chain rather than a circle around a point. Groups are small; the quadratic
    scan is free.
    """
    out: List[List[Dict[str, Any]]] = []
    for row in group:
        lat, lon = row.get("lat"), row.get("lon")
        if lat is None or lon is None:
            out.append([row])                        # no coordinates -> never chained
            continue
        for c in out:
            if any(m.get("lat") is not None
                   and _km(lat, lon, m["lat"], m["lon"]) <= radius_km for m in c):
                c.append(row)
                break
        else:
            out.append([row])
    return out


def _runs(rows: List[Dict[str, Any]], max_gap_days: int) -> List[List[Dict[str, Any]]]:
    """Split a venue's same-title rows into runs of a regular-ish cadence.

    A break longer than max_gap_days ends the run: a market that goes every
    Saturday from May to September and comes back next May is two series, not
    one twelve-month chain, and calling it one would put next year's date on this
    year's map. Rows sharing a date stay together — they are duplicates, and a
    duplicate must not open a gap that splits the run around it.
    """
    if not rows:
        return []
    by_date: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        by_date.setdefault(r["_date"], []).append(r)
    dates = sorted(by_date)
    out: List[List[Dict[str, Any]]] = []
    run: List[Dict[str, Any]] = list(by_date[dates[0]])
    run_dates = 1
    for prev, cur in zip(dates, dates[1:]):
        gap = (datetime.strptime(cur, "%Y-%m-%d") - datetime.strptime(prev, "%Y-%m-%d")).days
        if gap > max_gap_days:
            out.append(run)
            run, run_dates = list(by_date[cur]), 1
            continue
        run.extend(by_date[cur])
        run_dates += 1
    out.append(run)
    return out


def find_series(rows: List[Dict[str, Any]], radius_km: float, min_dates: int,
                max_gap_days: int) -> List[Tuple[str, List[Dict[str, Any]]]]:
    """-> [(series_id, [rows needing that series_id, ...]), ...]

    The root is an existing series_id where the run already has one (so a host's
    chain is extended, never replaced, and re-runs write nothing) and otherwise
    the id of the EARLIEST occurrence — which is what the app itself uses when a
    host links a series by hand, and is stable as long as no earlier occurrence
    turns up later.
    """
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        if row.get("claimed_at"):
            continue                                 # somebody owns this one now
        title = normalize_text(row.get("title"))
        if not title:
            continue                                 # an untitled row is not safely identifiable
        date = _local_date(row.get("starts_at") or "", row.get("lon"))
        if not date:
            continue
        row["_date"] = date
        groups.setdefault(title, []).append(row)

    found: List[Tuple[str, List[Dict[str, Any]]]] = []
    for group in groups.values():
        if len(group) < min_dates:
            continue                                 # cheap reject before the O(n²) cluster
        for venue in cluster(group, radius_km):
            for run in _runs(venue, max_gap_days):
                if len({r["_date"] for r in run}) < min_dates:
                    continue
                existing = Counter(r["series_id"] for r in run if r.get("series_id"))
                if existing:
                    root = existing.most_common(1)[0][0]
                else:
                    root = min(run, key=lambda r: (r.get("starts_at") or "", r["id"]))["id"]
                todo = [r for r in run if r.get("series_id") != root]
                if todo:
                    found.append((root, todo))
    return found


def stamp(base: str, auth: Dict[str, str], series_id: str, ids: List[str],
          batch: int, deadline: float) -> int:
    """PATCH series_id onto these rows, still carrying the scoping filter — the
    primary keys alone would identify them, but a bug up in the grouping could
    then reach a community event, and the filter is what makes that impossible.
    Halves the batch on a statement timeout, exactly like mapsee_cleanup."""
    headers = dict(auth)
    headers["Content-Type"] = "application/json"
    headers["Prefer"] = "return=minimal"
    done, i = 0, 0
    while i < len(ids):
        if time.monotonic() > deadline:
            print(f"  time budget spent — stopped after {done} row(s)")
            return done
        take = ids[i:i + batch]
        q = f"{base}&id=in.({','.join(take)})"
        r = requests.patch(q, headers=headers, json={"series_id": series_id}, timeout=120)
        if r.status_code < 300:
            done += len(take)
            i += len(take)
            continue
        if _timed_out(r) and batch > BATCH_MIN:
            batch = max(BATCH_MIN, batch // 2)
            print(f"  statement timeout — retrying in batches of {batch}")
            continue
        print(f"  skipped {len(take)} row(s) [{r.status_code} {r.text[:120]}]")
        i += len(take)
    return done


def main() -> int:
    ap = argparse.ArgumentParser(description="Chain repeating imported listings into one series.")
    ap.add_argument("--dry-run", action="store_true", help="Report and write nothing.")
    ap.add_argument("--radius-km", type=float, default=0.2,
                    help="How far apart two occurrences can be and still be the same venue.")
    ap.add_argument("--min-dates", type=int, default=2,
                    help="Distinct local dates before a repeat counts as a series.")
    ap.add_argument("--max-gap-days", type=int, default=45,
                    help="A longer gap between consecutive dates starts a new series.")
    ap.add_argument("--include-past", action="store_true",
                    help="Also chain events that have already happened.")
    ap.add_argument("--max-cluster", type=int, default=400,
                    help="Refuse if one series swallows more rows than this.")
    ap.add_argument("--max-share", type=float, default=0.85,
                    help="Refuse if more than this share of rows examined would be stamped.")
    ap.add_argument("--batch", type=int, default=BATCH_DEFAULT, help="Ids per PATCH.")
    ap.add_argument("--max-seconds", type=int, default=600, help="Wall-clock budget for the writes.")
    ap.add_argument("--show", type=int, default=12, help="How many series to print.")
    a = ap.parse_args()

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not (url and key):
        sys.exit("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY (server-side secrets).")

    # SAFETY: imported events only. Asserted rather than trusted, because
    # everything below appends to this string.
    flt = "external_source=eq.mapsee"
    assert "external_source=eq.mapsee" in flt, "refusing an unscoped write"
    base = url.rstrip("/") + "/rest/v1/events?" + flt
    auth = {"apikey": key, "Authorization": f"Bearer {key}"}

    since = None if a.include_past else datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = fetch_events(base, auth, since)
    print(f"Imported events examined: {len(rows)}"
          f"{'' if a.include_past else ' (upcoming only)'}")
    if not rows:
        return 0

    series = find_series(rows, a.radius_km, a.min_dates, a.max_gap_days)
    total = sum(len(todo) for _, todo in series)
    print(f"Repeating listings found: {len(series)}; rows to chain: {total}")

    for root, todo in series[:a.show]:
        print(f"  {todo[0].get('title')!r} -> {len(todo)} occurrence(s) under {root[:8]}…")
    if len(series) > a.show:
        print(f"  ... and {len(series) - a.show} more")

    if not total:
        print("Nothing to do.")
        return 0

    biggest = max(((len(todo), todo[0]) for _, todo in series), key=lambda t: t[0])
    if biggest[0] > a.max_cluster:
        sys.exit(f"Refusing: {biggest[1].get('title')!r} chained {biggest[0]} rows into one series "
                 f"(limit {a.max_cluster}). Lower --radius-km or --max-gap-days, or raise "
                 f"--max-cluster deliberately.")

    share = total / len(rows)
    if share > a.max_share:
        sys.exit(f"Refusing to stamp {share:.0%} of the rows examined (limit {a.max_share:.0%}). "
                 f"Re-check the grouping, or raise --max-share deliberately.")

    if a.dry_run:
        print("Dry run — nothing written.")
        return 0

    deadline = time.monotonic() + a.max_seconds
    done = 0
    for root, todo in series:
        done += stamp(base, auth, root, [r["id"] for r in todo if r.get("id")],
                      max(BATCH_MIN, a.batch), deadline)
        if time.monotonic() > deadline:
            break
    print(f"Chained {done} occurrence(s) into {len(series)} series.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
