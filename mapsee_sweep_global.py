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
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def run(script: str, args: list) -> None:
    """Run a sibling ingester; never let one metro/source abort the sweep."""
    cmd = [sys.executable, str(HERE / script), *args]
    try:
        r = subprocess.run(cmd, cwd=HERE, timeout=1800)
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
    a = ap.parse_args(argv)

    cfg = json.loads((HERE / a.config).read_text(encoding="utf-8"))
    srcs = {s.strip() for s in a.sources.split(",") if s.strip()}
    n_metros = 0
    for country in cfg.get("countries", []):
        code, cname = country.get("code"), country.get("name", "?")
        for m in country.get("metros", []):
            ll, radius, mname = m.get("latlong"), int(m.get("radius", 25)), m.get("name", "?")
            if not ll:
                continue
            n_metros += 1
            print(f"== {cname} / {mname} ({code}) {ll} ==", flush=True)
            if "ticketmaster" in srcs:
                run("mapsee_ingest.py", ["--latlong", ll, "--radius", str(radius), "--unit", "miles",
                                         "--country", code, "--within-days", str(a.within_days),
                                         "--store", a.store])
            if "meetup" in srcs:
                run("mapsee_ingest_meetup.py", ["--latlong", ll, "--radius", str(radius),
                                                "--within-days", str(a.within_days), "--store", a.store])
            time.sleep(0.5)                                    # gentle between metros
    print(f"[global] swept {n_metros} metros across {len(cfg.get('countries', []))} countries", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
