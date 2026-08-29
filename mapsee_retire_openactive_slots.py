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

TWO COLLAPSES, TWO RULES, ONE SAFETY RULE. `collapse_weekly_series` (2026-08-29)
then folded a class that recurs on the same weekday at the same hour into ONE
standing row — 77,346 of Everyone Active's occurrences into 38,227 of them on the
first production run — and those orphans are INVISIBLE to the grid rule, because
its groups are per-DAY and a weekly occurrence sits alone in its day. So there is
a second rule, `weekly_superseded`, keyed on title and venue with no day in it.

THE SAFETY RULE is the same for both and it is the whole design: a row is hidden
ONLY when the thing that replaced it is already in the table — the collapsed DAY
row for that title, venue and date, or the STANDING row for that title and venue
whose weekly pattern this row's local weekday and start time sit on. Without that
check, a publisher whose feed 500'd mid-import — which is exactly what Better's
SessionSeries feed did — would have its sessions vanish from the map, and the run
that was supposed to replace them is precisely the thing that may not have
finished.

Never touches:
  * a CLAIMED row. Once a venue owns its listing the row is theirs.
  * anything without the OpenActive attribution line. `external_source =
    'mapsee'` is every adapter in this repo, not this one.
  * a group SMALLER than the grid threshold. Three sessions in a day are a
    schedule; the collapse never touched them and neither does this.
  * an occurrence that does NOT sit on the standing row's weekly pattern. The
    fold leaves the bank-holiday special dated on purpose and still writes it
    every run; hiding it would delete a real listing.
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
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

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
# What collapse_weekly_series writes on the standing row it keeps. Same job as
# KEPT_MARK one collapse further on: its presence is the proof that the weekly
# arrangement now speaks for the dated rows underneath it.
WEEKLY_MARK = "\U0001F501 Runs weekly"


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


def standing_key(row):
    """Title and venue point at the adapter's own 5dp — NOT group_key's 4dp.

    A weekly arrangement has no day in its key: that is the whole difference
    between the two collapses, and it is why `superseded` cannot see these rows
    at all. Its groups are per-DAY, so an occurrence that the weekly fold
    replaced sits alone in its day, fails the `min_per_day` test, and is never
    even considered.
    """
    return ((row.get("title") or "").strip().lower(),
            f"{round(float(row.get('lat') or 0), 5):.5f}",
            f"{round(float(row.get('lon') or 0), 5):.5f}")


def _local(row, tzname):
    """The row's start as WALL CLOCK in `tzname`, or None.

    Deliberately converted rather than read off the string. `group_key` takes
    the first ten characters of `starts_at` and calls them the local date, which
    holds only while PostgREST renders the offset the publisher sent; a
    timestamptz normalises to UTC in the column, so a 00:30 class in British
    Summer Time reads as the previous day. Converting into the tz the sync
    derived from the venue's own coordinates cannot be wrong in that way.
    """
    try:
        t = datetime.fromisoformat(row.get("starts_at") or "")
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    try:
        return t.astimezone(ZoneInfo(tzname or "UTC"))
    except Exception:                                          # noqa: BLE001
        return t.astimezone(timezone.utc)


def _fits_pattern(row, keeper):
    """Is this dated row one the keeper's weekly pattern now speaks for?

    The match is on the LOCAL weekday and the LOCAL start time, which is exactly
    the slot key `collapse_weekly_series` folded on. Anything else — a bank
    holiday special, a one-off at a different hour — is an occasion the adapter
    still emits as its own dated row, and hiding it would delete a real listing.

    Fails CLOSED on every disagreement. If a publisher stamped its UK sessions
    with a UTC offset the pattern would be an hour off the tz the sync derived
    from the coordinates, nothing would match, and this hides nothing — which is
    the right way round for a script that writes hidden_at.
    """
    rec = keeper.get("recurring_hours")
    if not isinstance(rec, dict):
        return False
    days = rec.get("days")
    if not isinstance(days, dict):
        return False
    local = _local(row, rec.get("tz"))
    if local is None:
        return False
    spans = days.get(str(local.weekday()))
    if not isinstance(spans, list) or not spans:
        return False
    # SELF-DESCRIBING SHAPE, the same one ../mapsee 0188 reads: ["10:00","20:00"]
    # is one span, [["10:00","14:00"], ...] is a list of them.
    if not isinstance(spans[0], list):
        spans = [spans]
    hhmm = local.strftime("%H:%M")
    return any(isinstance(sp, list) and sp and sp[0] == hhmm for sp in spans)


def weekly_superseded(rows):
    """Which dated rows a standing row has taken over.

    THE SAFETY RULE IS THE SAME ONE, one collapse further on: a dated row is
    hidden ONLY when the standing row for that title and venue is already in the
    table AND this row sits on one of its weekly slots. Both halves are needed —
    the first because a publisher whose feed died mid-import must not have its
    sessions vanish, the second because the fold deliberately leaves the
    non-repeating occasions dated and they are still being written every run.

    A grid DAY row is fair game here and that is intended: the two collapses
    compose (110 slots -> 7 day rows -> one standing row), so once the standing
    row exists the day rows underneath it are superseded exactly as the slots
    were. It is the pattern match that keeps a day row the fold did NOT take.
    """
    standing = {}
    for r in rows:
        if WEEKLY_MARK not in (r.get("description") or ""):
            continue
        if not isinstance(r.get("recurring_hours"), dict):
            continue
        standing[standing_key(r)] = r
    keeper_ids = {r["id"] for r in standing.values()}
    out = []
    for r in rows:
        if r["id"] in keeper_ids:
            continue                      # the arrangement itself is the thing kept
        keeper = standing.get(standing_key(r))
        if keeper is not None and _fits_pattern(r, keeper):
            out.append(r)
    return out


def scan(days, back, max_pages, include_hidden=False):
    """Walk time windows. A deep OFFSET over this table dies at the ceiling.

    `include_hidden` IS WHAT MAKES --unhide WORK AT ALL. This query filtered
    `hidden_at=is.null` unconditionally, so the reverse pass could only ever see
    rows it had not hidden: after a successful --apply the escape hatch reported
    zero and wrote nothing, and the reversibility the whole design leans on did
    not exist. The test could not see it either, because its stub answered every
    URL with the same fixture and never looked at the query it was asked.

    The keepers stay VISIBLE while the rows they replaced are hidden, so the
    reverse pass needs both halves and therefore no hidden filter at all; the
    direction of the write is decided afterwards, on each row's own hidden_at.
    """
    q = ("events?select=id,title,lat,lon,starts_at,description,claimed_by,"
         "hidden_at,recurring_hours"
         "&external_source=eq.mapsee&claimed_by=is.null")
    if not include_hidden:
        q += "&hidden_at=is.null"
    now = time.time()
    t = now - back * 86400
    step = 86400 * 2
    out, pages, skipped, unmarked = [], 0, 0, 0
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
            for r in batch:
                if OA_MARK in (r.get("description") or ""):
                    out.append(r)
                else:
                    unmarked += 1
            if len(batch) < PAGE:
                break
            offset += PAGE
        pages += 1
        t += step
    print(f"  scanned to +{days}d: {len(out)} OpenActive row(s)"
          + (f"  ({skipped} windows errored)" if skipped else ""))
    # WHY THIS IS COUNTED. Every rule here is gated on OA_MARK, the attribution
    # line — "a row without it is not ours to judge". Until 2026-08-29 the sync
    # trimmed a long description from the END and cut that line off, and the
    # rows it hit hardest were the COLLAPSED ones, which carry an extra
    # prepended line and are therefore the longest. A keeper missing its mark is
    # invisible here, and the safety rule then refuses to retire its orphans —
    # so this number is the difference between "nothing to do" and "cannot see".
    print(f"  ({unmarked} row(s) in those windows carried no attribution line)")
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
    ap.add_argument("--no-grid", action="store_true",
                    help="skip the per-day grid rule")
    ap.add_argument("--no-weekly", action="store_true",
                    help="skip the weekly standing-row rule")
    ap.add_argument("--limit", type=int, default=0, help="cap the write (0 = no cap)")
    a = ap.parse_args(argv)

    if not (SUPABASE_URL and SERVICE_KEY):
        sys.exit("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY.")

    rows, skipped = scan(a.days, a.back, a.max_pages, include_hidden=a.unhide)
    if skipped:
        # A partial scan can only UNDER-report; it can never invent a group that
        # looks collapsed. Said out loud anyway, because a number that came from
        # an incomplete read must not read like a complete one.
        print(f"::warning::{skipped} window(s) could not be read — this pass is partial")

    standing = [r for r in rows if isinstance(r.get("recurring_hours"), dict)
                and WEEKLY_MARK in (r.get("description") or "")]
    print(f"  {len(standing)} standing row(s) available as weekly keepers")
    grid = [] if a.no_grid else superseded(rows, a.min_per_day)
    weekly = [] if a.no_weekly else weekly_superseded(rows)
    seen, doomed = set(), []
    for r in grid + weekly:                 # a day row can be BOTH; write it once
        if r["id"] not in seen:
            seen.add(r["id"])
            doomed.append(r)
    print(f"  grid: {len(grid)} row(s) whose collapsed day row is present "
          f"(groups of >= {a.min_per_day})")
    print(f"  weekly: {len(weekly)} dated row(s) whose standing row is present")

    # THE DIRECTION IS DECIDED PER ROW, not by the query. Forward only touches
    # what is visible; the reverse only touches what is hidden. Anything else
    # would let a second --apply restamp rows already hidden, and would let
    # --unhide reach a row somebody else hid on purpose.
    want_hidden = bool(a.unhide)
    doomed = [r for r in doomed if bool(r.get("hidden_at")) == want_hidden]
    if a.limit:
        doomed = doomed[:a.limit]
    verb = "un-hide" if a.unhide else "hide"
    print(f"  {len(doomed)} row(s) to {verb}")
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
    print(f"  {'un-hid' if a.unhide else 'hid'} {done} of {len(doomed)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
