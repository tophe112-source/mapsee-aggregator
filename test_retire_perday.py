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

Run: python test_retire_perday.py
"""
import sys

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

print()
print(f"{'FAILURES: ' + ', '.join(fails) if fails else 'all checks passed'}")
sys.exit(1 if fails else 0)
