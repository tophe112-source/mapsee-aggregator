#!/usr/bin/env python3
"""
test_retire_openactive_slots.py — what may be hidden, and what must never be.

The judgement here is one property: this can never empty a venue-day. Everything
else is detail. mapsee_retire_thin_artwork.py failed live THREE times and not
once in its judgement — twice on its own query and once on the line that printed
the result — so main() is driven end to end against a stubbed transport too.

Run: python test_retire_openactive_slots.py
"""
import json
import sys

import mapsee_retire_openactive_slots as R

OA = "Session data published by Test Pool via OpenActive, licensed CC-BY 4.0."
KEPT = "🎟 11 bookable slots on this day, from 05:40 to 21:00.\n\n" + OA


def row(i, title="Swim For Fitness", day="2026-09-07", hh="05", desc=OA,
        lat=51.5, lon=-0.12):
    return {"id": f"id{i}", "title": title, "lat": lat, "lon": lon,
            "starts_at": f"{day}T{hh}:40:00+01:00", "description": desc,
            "claimed_by": None, "hidden_at": None}


def main():
    checks = []

    # ---------------------------------------------- the grid, with its keeper
    grid = [row(i, hh=f"{5 + i:02d}") for i in range(10)] + [row(99, desc=KEPT)]
    out = R.superseded(grid, min_per_day=6)
    checks.append((len(out) == 10, "ten slot rows are superseded by the collapsed day row"))
    checks.append((all(KEPT not in r["description"] for r in out),
                   "the collapsed row itself is never hidden — it is the thing being kept"))

    # -------------------------------------------------------- THE SAFETY RULE
    #
    # No replacement in the table means the import that should have written one
    # did not finish. Better's SessionSeries feed 500'd mid-run; hiding on that
    # evidence would empty a venue-day and the run that was meant to refill it
    # is exactly the thing that failed.
    no_keeper = [row(i, hh=f"{5 + i:02d}") for i in range(10)]
    checks.append((R.superseded(no_keeper, min_per_day=6) == [],
                   "no collapsed row present -> nothing is hidden, however big the group"))

    # ------------------------------------------------ below the threshold
    small = [row(i, hh=f"{9 + i:02d}") for i in range(3)] + [row(98, desc=KEPT)]
    checks.append((R.superseded(small, min_per_day=6) == [],
                   "three sessions in a day are a schedule — untouched"))

    # ------------------------------------------------ groups stay separate
    twodays = ([row(i, day="2026-09-07", hh=f"{5 + i:02d}") for i in range(10)]
               + [row(90, day="2026-09-07", desc=KEPT)]
               + [row(50 + i, day="2026-09-08", hh=f"{5 + i:02d}") for i in range(10)])
    out2 = R.superseded(twodays, min_per_day=6)
    checks.append((len(out2) == 10 and all(r["starts_at"][:10] == "2026-09-07" for r in out2),
                   "a day with no keeper is not hidden because the NEXT day has one"))

    twovenues = ([row(i, hh=f"{5 + i:02d}") for i in range(10)] + [row(91, desc=KEPT)]
                 + [row(60 + i, hh=f"{5 + i:02d}", lat=51.6, lon=-0.2) for i in range(10)])
    out3 = R.superseded(twovenues, min_per_day=6)
    checks.append((len(out3) == 10 and all(abs(r["lat"] - 51.5) < 1e-9 for r in out3),
                   "and one venue's keeper does not license hiding at another"))

    titles = ([row(i, hh=f"{5 + i:02d}") for i in range(10)] + [row(92, desc=KEPT)]
              + [row(70 + i, title="Lane Swim", hh=f"{5 + i:02d}") for i in range(10)])
    out4 = R.superseded(titles, min_per_day=6)
    checks.append((len(out4) == 10 and all(r["title"] == "Swim For Fitness" for r in out4),
                   "nor does one title's keeper license hiding another title"))

    # --------------------------------------------------------- main(), stubbed
    #
    # Both of mapsee_ingest_osm_amenities' production failures were in main(),
    # and nothing ran main(). Same for the artwork retirement's NameError, which
    # ran the whole sweep, wrote 54 rows and then died printing the summary.
    calls = {"get": 0, "patch": []}
    served = [grid]

    def fake_sb(path, method="GET", body=None, prefer=""):
        if method == "PATCH":
            calls["patch"].append((path, body))
            return None
        calls["get"] += 1
        return served.pop(0) if served else []
    R.sb = fake_sb
    R.SUPABASE_URL, R.SERVICE_KEY = "https://x.test", "k"

    rc = R.main(["--days", "1", "--back", "0", "--max-pages", "1"])
    checks.append((rc == 0 and not calls["patch"],
                   "a dry run reads and writes NOTHING, and returns 0"))

    served[:] = [grid]
    calls["patch"].clear()
    rc = R.main(["--days", "1", "--back", "0", "--max-pages", "1", "--apply"])
    checks.append((rc == 0 and len(calls["patch"]) == 1,
                   "--apply issues the PATCH"))
    checks.append((calls["patch"][0][1] == {"hidden_at": None} or
                   isinstance(calls["patch"][0][1].get("hidden_at"), str),
                   "and it writes a hidden_at stamp, never a DELETE"))
    checks.append(("id99" not in calls["patch"][0][0],
                   "the kept row's id is not in the PATCH"))

    served[:] = [grid]
    calls["patch"].clear()
    rc = R.main(["--days", "1", "--back", "0", "--max-pages", "1", "--apply", "--unhide"])
    checks.append((calls["patch"] and calls["patch"][0][1] == {"hidden_at": None},
                   "--unhide clears the stamp, so the pass is reversible"))

    served[:] = [[row(i, hh=f"{5 + i:02d}", desc="no attribution here") for i in range(10)]]
    calls["patch"].clear()
    rc = R.main(["--days", "1", "--back", "0", "--max-pages", "1", "--apply"])
    checks.append((not calls["patch"],
                   "rows without the OpenActive line are another adapter's and are skipped"))

    served[:] = [[]]
    calls["patch"].clear()
    rc = R.main(["--days", "1", "--back", "0", "--max-pages", "1", "--apply"])
    checks.append((rc == 0 and not calls["patch"],
                   "an empty scan is a clean exit, not a crash on the summary line"))

    failed = 0
    for ok, why in checks:
        failed += 0 if ok else 1
        print(f"{'ok  ' if ok else 'FAIL'}  {why}")
    print(f"\n{len(checks)} cases, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
