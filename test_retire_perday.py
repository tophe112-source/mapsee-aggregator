#!/usr/bin/env python3
"""
test_retire_perday.py — the one property that must hold: a venue never vanishes.

mapsee_retire_perday_osm hides the OLD per-day OSM rows, and its safety rule is
that an old row goes only when a standing replacement exists at the same venue.
--collapse relaxes that for venues the new model has NOT reached: it keeps the
soonest row and hides the rest, so a venue that would otherwise show five
identical listings one day apart shows one.

Relaxing a safety rule is where this could go wrong, and it could go wrong
silently — a venue quietly disappearing from the map looks like a venue that
closed. So the invariant is pinned here rather than reasoned about:

    for every venue, at least one row survives.

Also pinned, at the end: the standing-row read, an id keyset over ../mapsee
0247's events_standing_walk_idx (R1), never an ends_at or OFFSET walk.

Run: python test_retire_perday.py
"""
import io
import sys
from contextlib import redirect_stderr, redirect_stdout

import mapsee_retire_perday_osm as R
import mapsee_standing

mapsee_standing.RETRY_PAUSE = 0          # a retry is about which request goes next, not the wait
from mapsee_retire_perday_osm import collapse_orphans

fails = []


def check(label, cond, detail=""):
    print(f"{'ok  ' if cond else 'FAIL'}  {label}{'' if cond else '   ' + str(detail)}")
    if not cond:
        fails.append(label)


def row(i, starts):
    return {"id": f"r{i}", "starts_at": starts, "title": "Top Pot Doughnuts"}


# The live case, from 2026-08-14 at (47.6099, -122.3239): five per-day rows, no
# standing replacement anywhere in the table.
five = [row(1, "2026-08-14T13:00:00Z"), row(2, "2026-08-15T14:00:00Z"),
        row(3, "2026-08-16T14:00:00Z"), row(4, "2026-08-17T13:00:00Z"),
        row(5, "2026-08-18T13:00:00Z")]
hide = collapse_orphans({"k1": list(five)})
check("four of five go", len(hide) == 4, hide)
check("the SOONEST is the one kept", "r1" not in {r["id"] for r in hide}, hide)

# order in must not decide the outcome
hide = collapse_orphans({"k1": list(reversed(five))})
check("input order does not change which row survives",
      len(hide) == 4 and "r1" not in {r["id"] for r in hide}, hide)

# THE INVARIANT
for n in range(1, 8):
    rows = [row(i, f"2026-08-{10 + i:02d}T12:00:00Z") for i in range(n)]
    kept = n - len(collapse_orphans({"k": rows}))
    if kept != 1:
        check(f"exactly one row survives at a venue of {n}", False, kept)
        break
else:
    check("exactly one row survives, for venues of 1 through 7 rows", True)

check("a venue with a single row is left completely alone",
      collapse_orphans({"k": [row(1, "2026-08-14T13:00:00Z")]}) == [])

# Several venues at once, which is the real shape of a sweep
many = {"a": [row(1, "2026-08-14T13:00:00Z"), row(2, "2026-08-15T13:00:00Z")],
        "b": [row(3, "2026-08-14T13:00:00Z")],
        "c": [row(4, "2026-08-16T13:00:00Z"), row(5, "2026-08-14T13:00:00Z"), row(6, "2026-08-15T13:00:00Z")]}
hide = collapse_orphans(many)
gone = {r["id"] for r in hide}
check("each venue keeps one, independently", gone == {"r2", "r4", "r6"}, sorted(gone))
check("...and b, the venue with one row, keeps it", "r3" not in gone, sorted(gone))

# A row with no start time must not win the keep slot by sorting first.
hide = collapse_orphans({"k": [{"id": "none", "starts_at": None},
                               {"id": "real", "starts_at": "2026-08-14T13:00:00Z"}]})
check("a row with no start time is not the one kept",
      [r["id"] for r in hide] == ["none"], hide)

# THE STANDING-ROW READ IS AN ID KEYSET OVER ../mapsee 0247's
# events_standing_walk_idx (id where recurring_hours is not null and hidden_at
# is null and pin_only is false), through mapsee_standing.standing_pages. It
# was an ends_at walk by OFFSET over 0235's events_recurring_listed_idx, which
# refused past 100k; after ../mapsee 0248 the stored window of a standing row is
# its anchor, so nothing that reads it may page by it. Driven through main()
# with the REST call stubbed, so the URLs pinned are the ones the script sends,
# in a dry run and in --unhide's.
def _reads(argv, serve=None):
    calls = []

    def sb(path, method="GET", body=None, prefer=""):
        calls.append((method, path))
        return serve(path) if serve else []
    real = (R.sb, R.SUPABASE_URL, R.SERVICE_KEY, sys.argv)
    R.sb, R.SUPABASE_URL, R.SERVICE_KEY = sb, "https://db.test", "key"
    sys.argv = ["mapsee_retire_perday_osm.py"] + argv
    try:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            code = R.main()
    finally:
        R.sb, R.SUPABASE_URL, R.SERVICE_KEY, sys.argv = real
    return calls, code


WALK = "recurring_hours=not.is.null&hidden_at=is.null&pin_only=is.false"
for argv in ([], ["--unhide"]):
    calls, _ = _reads(argv)
    standing = [p for m, p in calls if "recurring_hours=not.is.null" in p]
    label = " ".join(argv) or "dry run"
    check(f"{label}: one standing-row read", len(standing) == 1, standing)
    url = standing[0] if standing else ""
    check(f"{label}: the standing read is the walk index's literal predicate, pins out by is.false",
          f"&{WALK}&" in url and "pin_only=eq.false" not in url, url)
    check(f"{label}: ...with its own columns and filter, ordered by id, never OFFSET",
          url.startswith("events?select=id,title,lat,lon,starts_at,claimed_by&" + WALK + "&external_source=eq.mapsee&")
          and "&order=id.asc&limit=1000" in url and "offset=" not in url and "order=ends_at" not in url, url)
    check(f"{label}: ...and is not windowed by starts_at", "starts_at=" not in url, url)
    check(f"{label}: nothing is written", all(m == "GET" for m, _ in calls),
          sorted({m for m, _ in calls}))


# PAST 100k, BY KEYSET: the old walk refused beyond OFFSET 100,000. Two full
# pages and a short one, each continued from the last id it saw; a venue whose
# replacement is on the third page still counts as replaced.
def _uuid(n):
    return f"{n:08x}-0000-0000-0000-000000000000"


standing_rows = [{"id": _uuid(i), "title": "new", "lat": 47.0 + i / 1e3, "lon": -122.0,
                  "starts_at": "2026-01-01T00:00:00+00:00", "claimed_by": None} for i in range(2500)]
old = [{"id": "old-1", "title": "Old Pho", "lat": 47.0 + 2400 / 1e3, "lon": -122.0,
        "starts_at": "2026-10-29T18:00:00+00:00", "claimed_by": None}]


def serve(path):
    if "recurring_hours=not.is.null" in path:
        after = path.split("&id=gt.")[1].split("&")[0] if "&id=gt." in path else ""
        rows = [r for r in standing_rows if r["id"] > after]
        return rows[:1000]
    if "recurring_hours=is.null" in path and "offset=0" in path:
        return old
    return []


calls, code = _reads(["--days", "1", "--max-pages", "1"], serve)
reads = [p for m, p in calls if "recurring_hours=not.is.null" in p]
check("three keyset pages: the first unbounded, then id=gt.<last id of the page before>",
      [("&id=gt." + p.split("&id=gt.")[1].split("&")[0]) if "&id=gt." in p else "" for p in reads],
      ["", f"&id=gt.{_uuid(999)}", f"&id=gt.{_uuid(1999)}"])
out = io.StringIO()
real = (R.sb, R.SUPABASE_URL, R.SERVICE_KEY, sys.argv)
R.sb, R.SUPABASE_URL, R.SERVICE_KEY = (lambda p, *a, **k: serve(p)), "https://db.test", "key"
sys.argv = ["mapsee_retire_perday_osm.py", "--days", "1", "--max-pages", "1"]
try:
    with redirect_stdout(out):
        R.main()
finally:
    R.sb, R.SUPABASE_URL, R.SERVICE_KEY, sys.argv = real
check("a replacement on the third page still makes its venue's old row hideable",
      "old rows to hide                   : 1" in out.getvalue(), out.getvalue())


# A READ THAT FAILS PART-WAY WRITES NOTHING: a partial `replaced` hides less and
# looks fine, which is the failure this script's safety rule exists to refuse.
def failing(path):
    if "recurring_hours=not.is.null" in path and "&id=gt." in path:
        raise RuntimeError("HTTP 500")
    return serve(path)


calls, code = _reads(["--apply"], failing)
check("a standing read that fails part-way ends the run, non-zero, before any write",
      (code, [m for m, _ in calls if m != "GET"]), (1, []))

print()
print(f"{'FAILURES: ' + ', '.join(fails) if fails else 'all checks passed'}")
sys.exit(1 if fails else 0)
