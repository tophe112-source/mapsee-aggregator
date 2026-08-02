#!/usr/bin/env python3
"""
mapsee_ingest_parkrun.py — turn the worldwide parkrun event list into dated
Mapsee events on the 'running' layer.

WHY THIS SOURCE. `running` was an empty column in every country the catalog
covers — `catalog_curate.py coverage` flagged it for all 19 of them. parkrun is
the obvious filler: ~2,900 free, weekly, volunteer-run events across 21
countries, every one with fixed coordinates and a fixed weekday. Like markets,
it publishes a SCHEDULE rather than dated events, so this expands each one into
occurrences over a rolling horizon and re-runs regenerate the window
idempotently (identity = the globally unique parkrun event name).

  seriesid 1  5k, every Saturday        -> category running, secondary outdoors
  seriesid 2  junior 2k, every Sunday   -> category running, secondary kids

START TIMES ARE NOT IN THE FEED, and they vary by country and season (a UK 9am
is an Australian 7am in summer). Rather than invent one, events are emitted
ALL-DAY by default and link to the event's own page, which states the local
time. Fill `start_times` in the config, keyed by parkrun country code, to pin
the ones you have checked; the value is a naive local "HH:MM:SS" and
mapsee_supabase_sync converts it to a real UTC instant from the coordinates.

CONDUCT. events.json is the public event list parkrun's own event map reads,
fetched once per run under the production User-Agent. Results pages are not
touched.

    python mapsee_ingest_parkrun.py --config parkrun_sources.json --store events.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

from mapsee_ingest import NormalizedEvent, EventStore, make_fingerprint
from mapsee_ingest_markets import _occurrences

FEED_URL = "https://images.parkrun.com/events.json"

# seriesid -> (weekday, label, secondary category). parkrun runs the 5k on
# Saturday and the junior 2k on Sunday everywhere it operates; that is the one
# thing about the schedule the feed does not need to tell us.
SERIES = {
    1: {"weekday": 5, "label": "parkrun",
        "blurb": "Free, weekly, timed 5k. Walk, jog, run, spectate or volunteer.",
        "secondary": "outdoors"},
    2: {"weekday": 6, "label": "junior parkrun",
        "blurb": "Free, weekly, timed 2k for ages 4-14. Run, walk or volunteer.",
        "secondary": "kids"},
}


def load_feed(session, url: str) -> Dict[str, Any]:
    r = session.get(url, timeout=60)
    r.raise_for_status()
    return r.json()


def event_page(countries: Dict[str, Any], code: Any, eventname: str) -> Optional[str]:
    """The event's own page — where the local start time is published."""
    host = (countries.get(str(code)) or {}).get("url")
    return f"https://{host}/{eventname}/" if host and eventname else None


def parkrun_events(feature: Dict[str, Any], countries: Dict[str, Any],
                   cfg: Dict[str, Any]) -> List[NormalizedEvent]:
    props = feature.get("properties") or {}
    series = SERIES.get(props.get("seriesid"))
    eventname = props.get("eventname")
    name = props.get("LocalisedEventLongName") or props.get("EventLongName")
    coords = (feature.get("geometry") or {}).get("coordinates") or []
    if not (series and eventname and name and len(coords) == 2):
        return []
    lon, lat = coords[0], coords[1]
    if lat is None or lon is None:
        return []

    code = props.get("countrycode")
    url = event_page(countries, code, eventname)
    # Naive local "HH:MM:SS"; the sync turns it into a real instant from the
    # coordinates. Absent -> an all-day event, which is honest rather than wrong.
    start_t = (cfg.get("start_times") or {}).get(str(code))
    place = props.get("EventLocation") or name
    blurb = series["blurb"]
    if not start_t and url:
        blurb += " Start time on the event page."

    out: List[NormalizedEvent] = []
    for d in _occurrences([series["weekday"]], cfg.get("horizon_days", 42), None, None):
        ds = d.isoformat()
        # The parkrun event name is globally unique and stable, which is a better
        # identity than the venue text every other adapter has to fall back on.
        fp = make_fingerprint(name, ds, f"parkrun {eventname}")
        ev = NormalizedEvent(
            source="parkrun",
            source_id=fp,
            name=name,
            description=blurb,
            start_local=f"{ds}T{start_t}" if start_t else ds,
            start_utc=None,
            end_local=None,
            end_utc=None,
            venue_name=place,
            latitude=lat, longitude=lon,
            address=props.get("EventLocation"),
            category="running",
            categories=[series["secondary"]],
            ticket_url=url,
        )
        ev.fingerprint = fp
        out.append(ev)
    return out


def ingest(store: EventStore, session, cfg: Dict[str, Any]) -> int:
    feed = load_feed(session, cfg.get("url", FEED_URL))
    countries = feed.get("countries") or {}
    features = ((feed.get("events") or {}).get("features")) or []
    only = {str(c) for c in (cfg.get("only_countries") or [])}
    kept = seen = skipped = 0
    for f in features:
        if only and str((f.get("properties") or {}).get("countrycode")) not in only:
            continue
        seen += 1
        evs = parkrun_events(f, countries, cfg)
        if not evs:
            skipped += 1
            continue
        for ev in evs:
            store.upsert(ev)
            kept += 1
    print(f"[parkrun] {seen} event(s) in the feed, {skipped} unusable; "
          f"+{kept} occurrences")
    return kept


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Expand the worldwide parkrun schedule into dated Mapsee events.")
    ap.add_argument("--config", default="parkrun_sources.json")
    ap.add_argument("--store", default="mapsee_events.json")
    # Weekly schedules expanded over a rolling horizon: a daily re-run emits
    # byte-identical rows, so the config pins this to one weekday. --force ignores it.
    ap.add_argument("--force", action="store_true",
                    help="run regardless of the config's run_weekdays")
    a = ap.parse_args(argv)

    cfg: Dict[str, Any] = {}
    if os.path.exists(a.config):
        cfg = json.loads(open(a.config, encoding="utf-8").read())
    else:
        print(f"[parkrun] no {a.config} — using defaults")

    wd = cfg.get("run_weekdays")
    if wd and not a.force and datetime.now().weekday() not in wd:
        print(f"[parkrun] skipped (runs on weekdays {wd})")
        return 0

    session = requests.Session()
    session.headers.update(
        {"User-Agent": "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)"})
    store = EventStore(a.store)
    try:
        ingest(store, session, cfg)
    except Exception as exc:  # noqa: BLE001
        print(f"[parkrun] FAILED: {exc}")
        return 0                       # a dead feed must not fail the pipeline
    store.save()
    print(f"[parkrun] done; store now holds {len(store.records)} unique events.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
