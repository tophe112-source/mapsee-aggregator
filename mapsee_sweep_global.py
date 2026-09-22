#!/usr/bin/env python3
"""
mapsee_sweep_global.py - international event sweep for the top-10-country config.

Generalizes the US metro sweep to other countries using the sources that are
already global and above-board:
  • Ticketmaster Discovery API (official, countryCode-filtered; events carry
    lat/lon so NO geocoding is needed) - needs TICKETMASTER_API_KEY.
  • Meetup GraphQL eventSearch (global, keyword sweep on lat/lon) - works
    unauthenticated; MEETUP_OAUTH_TOKEN used if present.

It just orchestrates the existing per-metro ingesters (mapsee_ingest.py and
mapsee_ingest_meetup.py) over metros_global.json - no source logic is
duplicated, so any fix there applies here too. Everything lands in one store,
deduped by the usual fingerprint, ready for the normal Supabase sync.

    python mapsee_sweep_global.py --config metros_global.json --store global_events.json

Country selection weighs (1) legally harvestable data, (2) virality potential,
(3) wholesome-community value - see metros_global.json's _comment. The US is
covered by the main metros job and is intentionally NOT repeated here.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Per metro, per source. Measured 2026-09-04 and 09-13 across all 179
# international metros: Meetup's slowest took 88s (p50 65s) and Ticketmaster's
# 7s, and the US Meetup leg's slowest 80s. 1800 let one hung child eat half an
# hour of a job that already ran 326 of its 360 minutes; 600 is still ~7x the
# slowest metro seen.
METRO_TIMEOUT_S = 600


# THE TAIL OF THE FILE IS NOT A PLACE TO PUT A COUNTRY.
#
# This sweep has no cursor: it walks metros_global.json from the top every time
# and stops when the job's clock runs out, which is a real event the workflow
# plans around (`--deadline`, and the ::warning:: below that counts what was
# never started). So the countries LAST in the file are the ones a slow run
# always drops — and the order of that file is the order countries were added,
# which puts Brazil, Hong Kong, the UAE, South Korea, Singapore and South Africa
# at the bottom. Those are six of the eight thinnest countries in the catalog:
# the ones a slow run drops are the ones with the least to lose and the most to
# gain. Measured from the workflow's own header: the Meetup job ran 288-326
# minutes against a 350-minute cap, and its international leg stops starting
# metros at 320.
#
# The rotation is by DAY and by COUNTRY, and both halves matter. By day, so
# every country reaches the front of the queue within one turn of the wheel
# (29 countries, so 29 days) rather than by anybody's judgment of which country
# deserves the budget. By country, so a country is swept whole or not at all —
# cutting Germany in half every day would give six of its fourteen metros a
# permanent seat and the other eight none.
#
# NOT thinnest-catalog-first, which is how catalog_discover_osm.metros() sorts
# and was the first thing tried here. That rule is right for a CURATION sweep,
# where the budget should go where the catalog is empty. It is wrong for an
# INGEST sweep that runs every day: it would pin the same handful of thin
# countries at the front for ever and make the drop fall permanently on London,
# Toronto and Sydney, which is the same defect wearing better intentions.
def _day_ordinal() -> int:
    """Today as a day number. MAPSEE_TODAY=YYYYMMDD fixes it, as everywhere."""
    env = os.environ.get("MAPSEE_TODAY")
    if env and re.fullmatch(r"\d{8}", env):
        d = datetime.date(int(env[:4]), int(env[4:6]), int(env[6:8]))
    else:
        d = datetime.datetime.now(datetime.timezone.utc).date()
    return d.toordinal()


def rotate_countries(countries: list, day: int = None) -> list:
    """The configured countries, started at a different one each day.

    Order inside a country is untouched: its metros are already written
    largest-first and that is the order to spend a partial budget in.
    """
    usable = [c for c in countries if any(m.get("latlong") for m in c.get("metros", []))]
    if len(usable) < 2:
        return countries
    start = (_day_ordinal() if day is None else day) % len(usable)
    return usable[start:] + usable[:start]


def run(script: str, args: list) -> None:
    """Run a sibling ingester; never let one metro/source abort the sweep."""
    cmd = [sys.executable, str(HERE / script), *args]
    try:
        r = subprocess.run(cmd, cwd=HERE, timeout=METRO_TIMEOUT_S)
        if r.returncode != 0:
            print(f"[global] {script} exited {r.returncode} for {args}", flush=True)
    except Exception as exc:                                   # timeout / spawn failure
        print(f"[global] {script} FAILED for {args}: {exc}", flush=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="International event sweep (Ticketmaster + Meetup) for the top-10-country config.")
    ap.add_argument("--config", default="metros_global.json")
    ap.add_argument("--store", default="global_events.json")
    ap.add_argument("--within-days", type=int, default=90)
    ap.add_argument("--sources", default="ticketmaster,meetup",
                    help="comma list: ticketmaster,meetup")
    # THE JOB'S CLOCK, NOT THIS PROCESS'S. The Meetup job ran p50 292 / max 326
    # minutes against a 360-minute timeout that equals GitHub's own 6-hour job
    # limit — where the runner is terminated and no `always()` sync runs — and
    # grew ~22 minutes in three weeks. The workflow stamps an epoch deadline at
    # job start; this stops spawning metros once it has passed, so the final
    # sync still has time. 0 = no deadline (local runs, the Ticketmaster job).
    ap.add_argument("--deadline", type=float, default=0.0,
                    help="epoch seconds; start no metro at or after this (0 = none)")
    # On by default, because the default is the one the scheduled job uses and
    # the file order is the thing being corrected. --no-rotate is for comparing
    # a run against an older one.
    ap.add_argument("--no-rotate", dest="rotate", action="store_false",
                    help="walk metros_global.json top to bottom (see rotate_countries)")
    ap.set_defaults(rotate=True)
    a = ap.parse_args(argv)

    cfg = json.loads((HERE / a.config).read_text(encoding="utf-8"))
    srcs = {s.strip() for s in a.sources.split(",") if s.strip()}
    total = sum(1 for c in cfg.get("countries", []) for m in c.get("metros", []) if m.get("latlong"))
    n_metros = 0
    stopped = False
    order = (rotate_countries(cfg.get("countries", []))
             if a.rotate else list(cfg.get("countries", [])))
    if order:
        print(f"[global] starting at {order[0].get('name', '?')} "
              f"({'rotating daily' if a.rotate else 'file order'})", flush=True)
    for country in order:
        code, cname = country.get("code"), country.get("name", "?")
        for m in country.get("metros", []):
            ll, radius, mname = m.get("latlong"), int(m.get("radius", 25)), m.get("name", "?")
            if not ll:
                continue
            # Checked BEFORE the metro is counted: a metro never started must
            # not appear in "swept N metros", which is the only line anyone reads.
            if a.deadline and time.time() >= a.deadline:
                # `::warning::` first, or the runner never annotates it.
                print(f"::warning::[global] deadline reached before {cname} / {mname}: "
                      f"{total - n_metros} of {total} metros not started this run", flush=True)
                stopped = True
                break
            n_metros += 1
            print(f"== {cname} / {mname} ({code}) {ll} ==", flush=True)
            # `--latlong=VALUE`, never `--latlong VALUE`. A southern-hemisphere
            # metro starts with a minus, argparse only exempts things matching
            # ^-\d+$|^-\d*\.\d+$ from being read as an option, and "-33.8688,
            # 151.2093" has a comma in it — so the child died on "expected one
            # argument" for every metro below the equator. That was all of
            # Australia, New Zealand and South Africa: 22 of 165 metros, on both
            # sources, twice a week, since the sweep was written. It cost nothing
            # visible because run() only prints the non-zero exit and the job is
            # failure-tolerant by design, so a sweep missing three countries
            # still reported "swept 165 metros" and a green tick.
            if "ticketmaster" in srcs:
                run("mapsee_ingest.py", [f"--latlong={ll}", "--radius", str(radius), "--unit", "miles",
                                         "--country", code, "--within-days", str(a.within_days),
                                         "--store", a.store])
            if "meetup" in srcs:
                run("mapsee_ingest_meetup.py", [f"--latlong={ll}", "--radius", str(radius),
                                                "--within-days", str(a.within_days), "--store", a.store])
            time.sleep(0.5)                                    # gentle between metros
        if stopped:
            break
    print(f"[global] swept {n_metros}{f' of {total}' if stopped else ''} metros across "
          f"{len(cfg.get('countries', []))} countries"
          + (" (stopped at the job deadline)" if stopped else ""), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
