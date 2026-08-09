#!/usr/bin/env python3
"""
mapsee_ingest_runsignup.py — road races, trail races and multisport from
RunSignup's public race API, onto the 'running' and 'fitness' layers.

WHY THIS SOURCE, AND WHY AN ADAPTER RATHER THAN MORE CURATION
-------------------------------------------------------------
`fitness` and `running` were the only two lens categories with ZERO curated
sources anywhere on earth — not thin, zero, in every country — so wegosie.com
opened onto an empty map.

That was not for want of trying. Both have had discovery queries since the
curation loop was written, and they return nothing: a category-pinned Socrata
sweep across all five of them reads 315 datasets and proposes none, because 174
carry no usable event shape and 140 are not event listings. Municipal open data
does not publish road races and gym timetables in a geocoded, event-shaped form.
No amount of discovery budget fixes a well that is dry; a different KIND of
source does.

RunSignup is where the races themselves live: a registration platform whose
public REST API lists every non-private race it hosts, with dates, start times,
street addresses and distances. One keyless request per page.

WHAT IT EMITS
-------------
One event per race, on the race's next date.

  running_race / running_only / trail_race / walking_only / wheelchair
      -> category 'running', secondary 'fitness' (+ 'outdoors' for trail)
  triathlon / duathlon / swim / bike_race / obstacle_course / ...
      -> category 'fitness', secondary 'outdoors'

Both zero categories fill from the one adapter, which is the point of the
secondary-category column (migration 0108): a lens matches
`category = any(keys) OR categories && keys`, so a 5k reaches the running door
AND the fitness door without being listed twice.

VIRTUAL RACES ARE DROPPED, and they are 130 of every 250 races returned. A
virtual race has no location — the address on it is the organiser's office, or a
PO box — so ingesting one puts a pin on the map at a place where nothing is
happening. That is worse than a gap: a gap is visibly empty, a wrong pin is
confidently wrong. A race is kept only if it has at least one non-virtual event.

VOLUNTEER SUB-EVENTS ARE DROPPED TOO. RunSignup models "volunteer at the 5k" as
another registerable event on the same race, at the same place and time. Taking
it would list every race twice, once on the running layer and once on the
volunteer layer, as two pins on the same corner.

NO COORDINATES IN THE FEED, by design: races carry a street address and a
timezone, and mapsee_supabase_sync geocodes US addresses in one batch through
the Census geocoder. Start times ARE in the feed, as naive local strings, which
the sync converts to real instants — so unlike parkrun this does not have to
emit all-day events.

CONDUCT. `/rest/races` is RunSignup's own documented, keyless, public API for
listing races; this reads it under the production User-Agent at the documented
paging size, and touches nothing else. No registration data, no participant
data, no results.

    python mapsee_ingest_runsignup.py --config runsignup_sources.json \\
                                      --store events.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

from mapsee_ingest import NormalizedEvent, EventStore, make_fingerprint, norm_categories

API_URL = "https://runsignup.com/rest/races"

# event_type -> (primary, secondary). The vocabulary is RunSignup's; the values
# are ours, and every one of them is in mapsee_ingest.VALID_CATEGORIES.
#
# 'running' takes anything done on foot over a measured course, including
# walking_only and wheelchair: they are the same event on the same course, and
# filing a wheelchair division as something other than the race it belongs to
# would be both wrong and unkind. Multisport goes to 'fitness' because a
# triathlon on the running layer is a lie about what it is.
EVENT_TYPES = {
    "running_race":       ("running", "fitness"),
    "running_only":       ("running", "fitness"),
    "trail_race":         ("running", "outdoors"),
    "walking_only":       ("running", "fitness"),
    "wheelchair":         ("running", "fitness"),
    "triathlon":          ("fitness", "outdoors"),
    "duathlon":           ("fitness", "outdoors"),
    "aqua_bike":          ("fitness", "outdoors"),
    "swim_run":           ("fitness", "outdoors"),
    "swim":               ("fitness", "outdoors"),
    "bike_race":          ("fitness", "outdoors"),
    "mountain_bike_race": ("fitness", "outdoors"),
    "bike_ride":          ("fitness", "outdoors"),
    "fundraising_ride":   ("fitness", "outdoors"),
    "obstacle_course":    ("fitness", "outdoors"),
    "skate":              ("fitness", "outdoors"),
    "ski":                ("fitness", "outdoors"),
}
# Types that describe how a race is SOLD rather than what it is. 'nonprofit_event'
# and 'other' are both used for real, located races, so they are kept and read as
# running only when a genuinely-typed sibling event agrees; on their own they get
# the honest generic. 'virtual_race' is the one that must never be ingested.
VIRTUAL_TYPES = {"virtual_race", "virtual_challenge"}
GENERIC_TYPES = {"nonprofit_event", "other", None, ""}

# The API answers at most RESULT_CAP rows for any one query — four pages of 250,
# then empty pages for ever, with no total anywhere in the envelope to say the
# tail was dropped. Everything about the paging below exists because of this
# number; see fetch().
RESULT_CAP = 1000

# US states + DC. RunSignup is a US platform — measured on a 250-race page: 247
# US against one each India, Italy, Germany — so partitioning by state covers it
# and there is no international partition worth the requests.
US_STATES = [
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DC", "DE", "FL", "GA", "HI",
    "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN",
    "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH",
    "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA",
    "WV", "WI", "WY",
]

DEFAULTS = {
    "horizon_days": 120,      # how far ahead to ask for; races publish months out
    "results_per_page": 250,  # the API's comfortable page size
    "states": US_STATES,      # the partition — NOT a filter. See fetch().
    "country_codes": [],      # empty = every country the API returns
    "pause_s": 0.4,           # be a polite client of somebody's free API
    "timeout_s": 60,
}


# --- the feed ---------------------------------------------------------------

def _get_page(session, page: int, cfg: Dict[str, Any], start: str, end: str,
              state: Optional[str]) -> List[dict]:
    params = {
        "format": "json",
        "results_per_page": cfg["results_per_page"],
        "page": page,
        "start_date": start,
        "end_date": end,
        "events": "T",            # sub-events carry the type, distance and start time
        "only_partner_races": "F",
    }
    if state:
        params["state"] = state
    r = session.get(API_URL, params=params, timeout=cfg["timeout_s"])
    r.raise_for_status()
    body = r.json()
    if not isinstance(body, dict):
        return []
    return [x.get("race") or {} for x in (body.get("races") or [])]


def _fetch_partition(session, cfg, start, end, state) -> tuple[List[dict], bool]:
    """(races, hit_the_cap) for one partition. Pages until a short page."""
    per = cfg["results_per_page"]
    out: List[dict] = []
    for page in range(1, (RESULT_CAP // per) + 2):
        races = _get_page(session, page, cfg, start, end, state)
        out.extend(races)
        if len(races) < per:
            break
        time.sleep(cfg["pause_s"])
    return out, len(out) >= RESULT_CAP


def fetch(session, cfg: Dict[str, Any]) -> List[dict]:
    """Every race in the horizon, partitioned by state.

    WHY PARTITION AT ALL. The API silently caps any one query at RESULT_CAP
    rows: four pages of 250, then empty pages for ever, and the envelope carries
    no total to say a tail was dropped. So an unpartitioned sweep returns
    exactly 1,000 races whether the window holds 1,000 or 20,000 and looks
    identical either way — the failure this adapter's own docstring warns about,
    which the first draft walked straight into.

    Narrowing the DATE window does not help: measured, even a seven-day window
    is over the cap. State does. Over 120 days the largest is Texas at 910,
    which fits with room to spare, and a state that does reach the cap prints a
    NOTE rather than quietly losing its tail.

    `states` is a partition, not a filter — dropping one does not narrow the
    catalogue, it blinds the sweep to a state. Use `country_codes` to filter.
    """
    start = "today"
    end = (date.today() + timedelta(days=cfg["horizon_days"])).isoformat()
    states = list(cfg.get("states") or [None])
    out: List[dict] = []
    seen: set = set()
    capped: List[str] = []
    for state in states:
        races, hit_cap = _fetch_partition(session, cfg, start, end, state)
        if hit_cap:
            capped.append(state or "(unpartitioned)")
        for r in races:
            # A race sits at one address, so a duplicate across partitions means
            # the API answered a state filter loosely; keep the first either way.
            rid = r.get("race_id")
            if rid is not None and rid in seen:
                continue
            if rid is not None:
                seen.add(rid)
            out.append(r)
        time.sleep(cfg["pause_s"])
    if capped:
        print(f"[runsignup] NOTE: {len(capped)} partition(s) hit the {RESULT_CAP}-race "
              f"API cap and lost their tail: {', '.join(capped)}. "
              f"Shorten horizon_days.")
    return out


# --- one race -> one event --------------------------------------------------

def _usable_events(race: dict) -> List[dict]:
    """Sub-events worth reading: located, and not a volunteer signup slot."""
    out = []
    for e in (race.get("events") or []):
        if not isinstance(e, dict):
            continue
        if str(e.get("volunteer") or "F").upper() == "T":
            continue
        if (e.get("event_type") or "") in VIRTUAL_TYPES:
            continue
        out.append(e)
    return out


def categorise(events: List[dict]) -> tuple[str, List[str]]:
    """(primary, secondaries) from the sub-events' types.

    A race with a 5k, a 10k and a half is three sub-events of one type; a
    triathlon weekend can carry a swim, a ride and a run. Majority wins, and a
    tie goes to 'running' — the commoner case by a wide margin, and the door
    with the least supply.
    """
    votes: Dict[str, int] = {}
    secondaries: List[str] = []
    for e in events:
        mapped = EVENT_TYPES.get(e.get("event_type") or "")
        if not mapped:
            continue
        primary, secondary = mapped
        votes[primary] = votes.get(primary, 0) + 1
        if secondary not in secondaries:
            secondaries.append(secondary)
    if not votes:
        # Only generically-typed sub-events. These are real races often enough
        # to keep, and 'running' would be a guess, so take the platform's own
        # framing: it is a fitness event on a course.
        return "fitness", ["outdoors"]
    primary = max(sorted(votes), key=lambda k: votes[k])
    if primary == "running" and "fitness" not in secondaries:
        secondaries.append("fitness")
    return primary, norm_categories(primary, secondaries)


def _parse_start(events: List[dict], next_date: str) -> Optional[str]:
    """Naive local ISO start, from the earliest sub-event that states one.

    RunSignup writes "10/10/2026 09:00". The sync turns a naive local string
    into a real UTC instant using the event's coordinates, so passing the local
    wall-clock through unchanged is correct and inventing a UTC one is not.
    """
    stamps = []
    for e in events:
        raw = (e.get("start_time") or "").strip()
        if not raw:
            continue
        try:
            stamps.append(datetime.strptime(raw, "%m/%d/%Y %H:%M"))
        except ValueError:
            continue
    if stamps:
        return min(stamps).strftime("%Y-%m-%dT%H:%M:%S")
    # No time anywhere: an all-day event on the right date beats a made-up 9am.
    try:
        return datetime.strptime(next_date, "%m/%d/%Y").date().isoformat()
    except (ValueError, TypeError):
        return None


def _distances(events: List[dict]) -> str:
    seen = []
    for e in events:
        d = (e.get("distance") or "").strip()
        if d and d not in seen:
            seen.append(d)
    return ", ".join(seen[:6])


def to_event(race: dict, cfg: Dict[str, Any]) -> Optional[NormalizedEvent]:
    if str(race.get("is_draft_race") or "F").upper() == "T":
        return None
    if str(race.get("is_private_race") or "F").upper() == "T":
        return None
    name = (race.get("name") or "").strip()
    next_date = (race.get("next_date") or "").strip()
    if not (name and next_date):
        return None

    events = _usable_events(race)
    if not events:
        return None                       # virtual-only, or volunteer-only

    addr = race.get("address") or {}
    city = (addr.get("city") or "").strip()
    region = (addr.get("state") or "").strip()
    country = (addr.get("country_code") or "").strip()
    only = {c.upper() for c in (cfg.get("country_codes") or [])}
    if only and country.upper() not in only:
        return None
    # A race with no city cannot be placed: the geocoder would fall back to the
    # street alone, which resolves to whichever town it hits first.
    if not city:
        return None

    start_local = _parse_start(events, next_date)
    if not start_local:
        return None

    primary, secondaries = categorise(events)
    dist = _distances(events)
    blurb = (race.get("description") or "").strip()
    parts = [f"Distances: {dist}." if dist else "", blurb]
    description = " ".join(p for p in parts if p).strip() or None

    # The pin's LABEL. RunSignup has no venue-name field, only a street, and a
    # street beginning with a house number makes a poor label — "6121 Highway 85"
    # tells a reader nothing, where "Vincent" at least places it. A street that
    # does NOT start with a number is usually a real venue ("Honda - Alabama Auto
    # Plant"), so keep those. The exact street survives either way in `address`,
    # which the sync appends to the description as "📍 ...".
    street = (addr.get("street") or "").strip()
    venue = street if (street and not street[0].isdigit()) else city
    fp = make_fingerprint(name, start_local[:10], venue, city)
    ev = NormalizedEvent(
        source="runsignup",
        source_id=str(race.get("race_id") or fp),
        name=name,
        description=description,
        start_local=start_local,
        venue_name=venue,
        address=street or None,
        city=city or None,
        region=region or None,
        country=country or None,
        postal_code=(addr.get("zipcode") or "").strip() or None,
        timezone=(race.get("timezone") or "").strip() or None,
        category=primary,
        categories=secondaries,
        ticket_url=(race.get("url") or "").strip() or None,
        poster_image_url=(race.get("logo_url") or "").strip() or None,
    )
    ev.fingerprint = fp
    return ev


# --- run --------------------------------------------------------------------

def ingest(store: EventStore, session, cfg: Dict[str, Any]) -> int:
    races = fetch(session, cfg)
    kept = 0
    dropped = {"virtual_or_volunteer": 0, "no_city": 0, "draft_or_private": 0,
               "no_date": 0, "other_country": 0}
    by_cat: Dict[str, int] = {}
    for race in races:
        ev = to_event(race, cfg)
        if ev is None:
            # Cheap re-derivation for the tally only — the numbers are the point
            # of the log line, and "N races, M ingested" with no reason why is
            # how a filter that swallows everything goes unnoticed.
            addr = race.get("address") or {}
            only = {c.upper() for c in (cfg.get("country_codes") or [])}
            if str(race.get("is_draft_race") or "F").upper() == "T" or \
               str(race.get("is_private_race") or "F").upper() == "T":
                dropped["draft_or_private"] += 1
            elif not _usable_events(race):
                dropped["virtual_or_volunteer"] += 1
            elif only and (addr.get("country_code") or "").upper() not in only:
                dropped["other_country"] += 1
            elif not (addr.get("city") or "").strip():
                dropped["no_city"] += 1
            else:
                dropped["no_date"] += 1
            continue
        store.upsert(ev)
        by_cat[ev.category] = by_cat.get(ev.category, 0) + 1
        kept += 1
    spread = ", ".join(f"{k} {v}" for k, v in sorted(by_cat.items()))
    print(f"[runsignup] {len(races)} race(s) in the window; +{kept} event(s)"
          + (f" ({spread})" if spread else ""))
    print("[runsignup] dropped: "
          + ", ".join(f"{v} {k}" for k, v in sorted(dropped.items()) if v))
    return kept


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Ingest RunSignup races onto the running and fitness layers.")
    ap.add_argument("--config", default="runsignup_sources.json")
    ap.add_argument("--store", default="mapsee_events.json")
    a = ap.parse_args(argv)

    cfg = dict(DEFAULTS)
    if os.path.exists(a.config):
        try:
            cfg.update(json.loads(open(a.config, encoding="utf-8").read()) or {})
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[runsignup] unreadable {a.config} ({exc}) — using defaults")
    else:
        print(f"[runsignup] no {a.config} — using defaults")

    session = requests.Session()
    session.headers.update(
        {"User-Agent": "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)",
         "Accept": "application/json"})
    store = EventStore(a.store)
    try:
        ingest(store, session, cfg)
    except Exception as exc:  # noqa: BLE001
        print(f"[runsignup] FAILED: {exc}")
        return 0                       # a dead feed must not fail the pipeline
    store.save()
    print(f"[runsignup] done; store now holds {len(store.records)} unique events.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
