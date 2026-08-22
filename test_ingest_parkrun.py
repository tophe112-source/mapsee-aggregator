#!/usr/bin/env python3
"""
test_ingest_parkrun.py — the worldwide free-weekly-run layer, against features
taken verbatim from images.parkrun.com/events.json.

Prints one line per case and exits non-zero on failure, like the other 19.

WHY THIS EXISTS AT ALL. The adapter was written, tested by hand, and wired into
aggregate-events.yml — and `parkrun_sources.json` was never committed. The step
is guarded `if [ -f parkrun_sources.json ]`, so every scheduled run since has
printed "no parkrun_sources.json — skipping parkrun" and the whole `running`
layer stayed empty, in all 20 countries, without one red tick. A config file
that must exist for a job to do anything is part of the job.

What is pinned:

  * A START TIME IS NOT IN THE FEED, so it is not invented. parkrun start times
    vary by country AND season — a UK 9am is an Australian 7am in summer, and
    some UK events start at 9:30. Absent a checked entry the event is ALL-DAY
    and says where to find the time, which is honest; a plausible country-wide
    guess is the well-formed-and-wrong failure this repo keeps paying for.
  * COUNTRY COMES FROM DATA, NOT FROM A TLD PARSE. parkrun's country codes are
    opaque integers and the feed carries only a domain, so `97 -> GB` is written
    down in the config where it can be read and checked, rather than derived
    from `parkrun.org.uk` by a regex that has to know org.uk is not UK.
  * THE JUNIOR 2K IS A KIDS EVENT. seriesid 2 runs on Sunday and carries `kids`
    as its secondary, which is the only thing putting parkrun on that lens.
  * IDENTITY IS THE EVENT NAME PLUS THE DATE, so a re-run over the same rolling
    horizon regenerates byte-identical rows instead of duplicating the world.
"""
import os
import sys
from datetime import date, timedelta

import mapsee_ingest_parkrun as pr

FAILURES = []


def check(label, got, want):
    ok = got == want
    print(("  ok   " if ok else "  FAIL ") + label + ("" if ok else f"\n         got {got!r}\n        want {want!r}"))
    if not ok:
        FAILURES.append(label)


def check_true(label, got):
    check(label, bool(got), True)


# --- fixtures: verbatim shape from images.parkrun.com/events.json ------------
COUNTRIES = {
    "97": {"url": "www.parkrun.org.uk", "bounds": [-8.6, 49.9, 1.8, 60.9]},
    "3":  {"url": "www.parkrun.com.au", "bounds": [112.9, -43.6, 153.6, -10.1]},
    "999": {"url": None, "bounds": []},
}
CFG = {"countries": {"97": "GB", "3": "AU"}, "horizon_days": 21}


def feature(seriesid=1, code=97, name="Bushy parkrun", eventname="bushy",
            lon=-0.335, lat=51.411):
    return {"geometry": {"coordinates": [lon, lat]},
            "properties": {"seriesid": seriesid, "countrycode": code,
                           "eventname": eventname, "EventLongName": name,
                           "EventLocation": "Bushy Park, Teddington"}}


def main():
    print("the country map is DATA, and it is applied")
    evs = pr.parkrun_events(feature(), COUNTRIES, CFG)
    check_true("a 5k produces occurrences", len(evs) > 0)
    check("the country comes from the config map", evs[0].country, "GB")
    check("an Australian event maps too",
          pr.parkrun_events(feature(code=3, eventname="albert"), COUNTRIES, CFG)[0].country, "AU")
    check("a code the config does not know invents nothing",
          pr.parkrun_events(feature(code=999, eventname="x"), COUNTRIES, CFG)[0].country, None)

    print()
    print("no start time in the feed, so none is invented")
    check("the event is all-day — a bare date, not a guessed instant",
          len(evs[0].start_local), 10)
    check_true("and it says where the real time is published",
               "Start time on the event page." in (evs[0].description or ""))
    timed = pr.parkrun_events(feature(), COUNTRIES, dict(CFG, start_times={"97": "09:00:00"}))
    check("a CHECKED time is used as naive local, for the sync to convert",
          timed[0].start_local[10:], "T09:00:00")
    check_true("and then the description stops pointing at the page",
               "Start time on the event page." not in (timed[0].description or ""))

    print()
    print("the junior 2k is what puts parkrun on the kids lens")
    jr = pr.parkrun_events(feature(seriesid=2, eventname="bushy-juniors"), COUNTRIES, CFG)
    check("the 5k's secondary is outdoors", evs[0].categories, ["outdoors"])
    check("the junior 2k's is kids", jr[0].categories, ["kids"])
    check("both are primarily running", (evs[0].category, jr[0].category), ("running", "running"))
    check("the 5k falls on Saturdays",
          sorted({date.fromisoformat(e.start_local[:10]).weekday() for e in evs}), [5])
    check("the junior 2k on Sundays",
          sorted({date.fromisoformat(e.start_local[:10]).weekday() for e in jr}), [6])

    print()
    print("a bounded horizon, and an identity that survives re-running")
    horizon = date.today() + timedelta(days=CFG["horizon_days"])
    check_true("nothing is projected past the configured horizon",
               all(date.fromisoformat(e.start_local[:10]) <= horizon for e in evs))
    again = pr.parkrun_events(feature(), COUNTRIES, CFG)
    check("a second expansion produces the same fingerprints — re-runs do not duplicate",
          [e.fingerprint for e in again], [e.fingerprint for e in evs])
    check("each occurrence is distinct within the run",
          len({e.fingerprint for e in evs}), len(evs))
    check_true("every occurrence keeps the surveyed coordinates",
               all(e.latitude == 51.411 and e.longitude == -0.335 for e in evs))
    check("the event's own page is the link",
          evs[0].ticket_url, "https://www.parkrun.org.uk/bushy/")

    print()
    print("a feature the feed cannot place is dropped, not guessed at")
    check("no coordinates, no event",
          pr.parkrun_events({"geometry": {"coordinates": []}, "properties":
                             {"seriesid": 1, "countrycode": 97, "eventname": "x",
                              "EventLongName": "X"}}, COUNTRIES, CFG), [])
    check("an unknown series is skipped rather than filed as running",
          pr.parkrun_events(feature(seriesid=99), COUNTRIES, CFG), [])

    print()
    print("the config the workflow guards on")
    import json, os
    check_true("parkrun_sources.json exists — the job is a no-op without it",
               os.path.exists("parkrun_sources.json"))
    cfg = json.load(open("parkrun_sources.json", encoding="utf-8"))
    check_true("it maps every country parkrun currently serves", len(cfg["countries"]) >= 20)
    check("start_times is empty on purpose — none has been checked", cfg["start_times"], {})
    check_true("and it runs on more than one weekday, so a lost run is not a lost week",
               len(cfg["run_weekdays"]) >= 2)

    print()
    print("no OTHER adapter is a silent no-op for a config nobody committed")
    # The general shape of the bug this file exists for. A workflow step guarded
    # `if [ -f X_sources.json ]` prints a friendly skip and returns 0 when the
    # file is absent, so a source config that was never committed looks exactly
    # like a source deliberately not configured — for as long as nobody counts.
    #
    # `ckan` is the one legitimate absence: catalog_curate MERGES into it, and
    # nothing has ever verified (3 ledger rows, all fail), so the file does not
    # exist yet and should not. Anything else appearing here is a job doing
    # nothing.
    import re
    KNOWN_EMPTY = {"ckan_sources.json"}
    wf = open(os.path.join(".github", "workflows", "aggregate-events.yml"),
              encoding="utf-8").read()
    guarded = set(re.findall(r"if \[ -f ([a-z_]+_sources\.json) \]", wf))
    check_true("the workflow does guard several source configs", len(guarded) >= 10)
    absent = sorted(c for c in guarded if not os.path.exists(c) and c not in KNOWN_EMPTY)
    check("every guarded source config is present", absent, [])

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: " + "; ".join(FAILURES))
        return 1
    print("all parkrun checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
