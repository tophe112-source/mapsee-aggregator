#!/usr/bin/env python3
"""
mapsee_retire_openactive_slots.py — hide the OLD per-slot OpenActive rows.

WHY. Until 2026-08-29 the OpenActive adapter wrote one row per published
occurrence, and some publishers publish a BOOKING GRID rather than a schedule:
measured in a ±0.03 box on central London, one pool published "Swim For Fitness"
255 times in a week, 110 of them on a single day at ten-minute spacing from
05:40. Three title/venue pairs like it were about half of the 800 rows
`events_near` returns for that viewport, which is most of why central London
exceeds the API role's ~3s statement timeout while Seattle and New York do not.

`collapse_booking_grids` now keeps ONE row per title per venue per day. But an
upsert cannot delete, and the collapsed row carries a NEW fingerprint — so it is
INSERTED alongside the fifty it replaces rather than replacing them. Left alone
the old rows age out as their windows pass, which for a 120-day horizon is
months of both models on the map at once, and months of the timeout this was
meant to fix. That is the same shape mapsee_retire_perday_osm.py was written
for, and this is its sibling.

`hidden_at`, not DELETE: hiding is reversible and these are somebody's real
sessions, however badly published.

THE SAFETY RULE, and it is the whole design: a slot row is hidden ONLY when the
collapsed DAY row for that same title, venue and date is already in the table.
Without that check, a publisher whose feed 500'd mid-import — which is exactly
what Better's SessionSeries feed did — would have its sessions vanish from the
map, and the run that was supposed to replace them is precisely the thing that
may not have finished.

Never touches:
  * a CLAIMED row. Once a venue owns its listing the row is theirs.
  * anything without the OpenActive attribution line. `external_source =
    'mapsee'` is every adapter in this repo, not this one.
  * a group SMALLER than the grid threshold. Three sessions in a day are a
    schedule; the collapse never touched them and neither does this.
  * the collapsed row itself, which is the thing being kept.

Env:  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
Run:  python mapsee_retire_openactive_slots.py                  # dry run, default
      python mapsee_retire_openactive_slots.py --apply
      python mapsee_retire_openactive_slots.py --apply --unhide # put them back
"""
import argparse
import collections
import json
import os
import sys
import time
import urllib.request

from mapsee_ingest_openactive import GRID_MIN_PER_DAY

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
PAGE = 500
# The licence line every OpenActive row carries. It is the attribution CC-BY
# requires, which is why it is reliable: a row without it is not ours to judge.
OA_MARK = "via OpenActive"
# What collapse_booking_grids writes on the row it keeps. Its presence is the
# proof that the replacement exists.
KEPT_MARK = "bookable slots on this day"


def sb(path, method="GET", body=None, prefer=""):
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None)
    req.add_header("apikey", SERVICE_KEY)
    req.add_header("authorization", f"Bearer {SERVICE_KEY}")
    req.add_header("content-type", "application/json")
    if prefer:
        req.add_header("prefer", prefer)
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read().decode("utf-8", "replace")
        return json.loads(raw) if raw.strip() else None


def group_key(row):
    """Title, venue to 4dp, and the LOCAL day — the same key the adapter uses.

    `starts_at` comes back from PostgREST with an offset, so its first ten
    characters are the local date, which is the day a person is looking at.
    """
    return ((row.get("title") or "").strip().lower(),
            f"{round(float(row.get('lat') or 0), 4):.4f}",
            f"{round(float(row.get('lon') or 0), 4):.4f}",
            (row.get("starts_at") or "")[:10])


def superseded(rows, min_per_day=GRID_MIN_PER_DAY):
    """Which rows are slots whose collapsed day row already exists.

    Pure, and split out from main() deliberately: the property that matters is
    "this can never empty a venue-day", and that is worth a test rather than a
    careful read. mapsee_retire_thin_artwork.py failed live three times and not
    once in its judgement — twice on its query and once on the line that printed
    the result — so main() is driven end to end in the test as well.
    """
    by_group = collections.defaultdict(list)
    for r in rows:
        by_group[group_key(r)].append(r)
    out = []
    for group in by_group.values():
        if len(group) < min_per_day:
            continue                      # a schedule, not a grid — leave it alone
        kept = [r for r in group if KEPT_MARK in (r.get("description") or "")]
        if not kept:
            continue                      # THE SAFETY RULE: no replacement, no hiding
        keep_ids = {r["id"] for r in kept}
        out.extend(r for r in group if r["id"] not in keep_ids)
    return out


def scan(days, back, max_pages):
    """Walk time windows. A deep OFFSET over this table dies at the ceiling."""
    q = ("events?select=id,title,lat,lon,starts_at,description,claimed_by,hidden_at"
         "&external_source=eq.mapsee&hidden_at=is.null&claimed_by=is.null")
    now = time.time()
    t = now - back * 86400
    step = 86400 * 2
    out, pages, skipped = [], 0, 0
    while t < now + days * 86400 and pages < max_pages:
        w_a = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
        w_b = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                            time.gmtime(min(t + step, now + days * 86400)))
        offset = 0
        while True:
            url = f"{q}&starts_at=gte.{w_a}&starts_at=lt.{w_b}&limit={PAGE}&offset={offset}"
            batch = None
            for attempt in range(3):
                try:
                    batch = sb(url) or []
                    break
                except Exception as e:                       # noqa: BLE001
                    if attempt == 2:
                        print(f"  window {w_a[:10]} failed 3x ({e})", file=sys.stderr)
                        skipped += 1
                    else:
                        time.sleep(1.5 * (attempt + 1))
            if not batch:
                break
            out.extend(r for r in batch if OA_MARK in (r.get("description") or ""))
            if len(batch) < PAGE:
                break
            offset += PAGE
        pages += 1
        t += step
    print(f"  scanned to +{days}d: {len(out)} OpenActive row(s)"
          + (f"  ({skipped} windows errored)" if skipped else ""))
    return out, skipped


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--apply", action="store_true", help="write (default is a dry run)")
    ap.add_argument("--unhide", action="store_true", help="reverse: un-hide instead")
    ap.add_argument("--days", type=int, default=130,
                    help="how far ahead to scan (the adapter's horizon is 120)")
    ap.add_argument("--back", type=int, default=1)
    ap.add_argument("--max-pages", type=int, default=200)
    ap.add_argument("--min-per-day", type=int, default=GRID_MIN_PER_DAY)
    ap.add_argument("--limit", type=int, default=0, help="cap the write (0 = no cap)")
    a = ap.parse_args(argv)

    if not (SUPABASE_URL and SERVICE_KEY):
        sys.exit("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY.")

    rows, skipped = scan(a.days, a.back, a.max_pages)
    if skipped:
        # A partial scan can only UNDER-report; it can never invent a group that
        # looks collapsed. Said out loud anyway, because a number that came from
        # an incomplete read must not read like a complete one.
        print(f"::warning::{skipped} window(s) could not be read — this pass is partial")
    doomed = superseded(rows, a.min_per_day)
    if a.limit:
        doomed = doomed[:a.limit]
    verb = "un-hide" if a.unhide else "hide"
    print(f"  {len(doomed)} slot row(s) to {verb} "
          f"(groups of >= {a.min_per_day} with a collapsed day row present)")
    for r in doomed[:10]:
        print(f"    {(r.get('title') or '')[:44]:44s} {(r.get('starts_at') or '')[:16]}")
    if len(doomed) > 10:
        print(f"    ...and {len(doomed) - 10} more")
    if not doomed:
        return 0
    if not a.apply:
        print("\n  DRY RUN — nothing written. Re-run with --apply.")
        return 0

    stamp = None if a.unhide else time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    done = 0
    for i in range(0, len(doomed), 50):
        chunk = doomed[i:i + 50]
        ids = ",".join(r["id"] for r in chunk)
        try:
            sb(f"events?id=in.({ids})", method="PATCH", body={"hidden_at": stamp})
            done += len(chunk)
        except Exception as e:                                # noqa: BLE001
            print(f"  batch at {i} failed: {e}", file=sys.stderr)
    print(f"  {verb}d {done} of {len(doomed)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
