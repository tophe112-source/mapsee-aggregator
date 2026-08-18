#!/usr/bin/env python3
"""
mapsee_ingest_bikereg.py — bike races, gravel days, gran fondos and velodrome
series from the AthleteReg event calendar, onto the 'fitness' layer.

WHY THIS SOURCE
---------------
wegosie.com's own tagline is "Group Runs, RIDES, Hikes and Classes", and the
catalogue had no cycling source of any kind. Measured 2026-08-16 against 197,967
upcoming events: 259 rows with "bike" in the title, 59 with "cycling", 24 with
"bicycle", 0 with "gran fondo" — roughly 0.17% of the map, and smeared across
five categories (fitness 135, community 69, outdoors 52, sports 14), so even
what existed could not be filtered as riding.

That is the same shape as the gap mapsee_ingest_runsignup.py was written for,
and it has the same cause: municipal open data does not publish bike races, so
no amount of curation budget reaches them. `runsignup_sources.json` already
records the measurement — a category-pinned Socrata sweep across all five
fitness/running queries reads 315 datasets and proposes none. BikeReg is where
the races actually are: 1,367 of them in the next twelve months, keyless.

WHICH API, AND WHY NOT THE OTHER ONE
------------------------------------
BikeReg publishes two. The REST one at `bikereg.com/api/search` is the obvious
one and it is a trap: it answers 200 with exactly 100 events and IGNORES every
paging or filter parameter offered to it — `page`, `offset`, `count`,
`MaxResults`, `startdate`/`enddate`, `state`, `radius` all return the identical
100 rows, and `ResultCount` says 100, so nothing in the envelope reveals that
1,267 events were withheld. A sweep built on it would look complete for ever.

The GraphQL gateway is the one BikeReg's own docs call "the recommended way to
get event data". It is keyless, cursor-paged (`first`/`after` with a real
`hasNextPage`), carries `totalCount` so a short read is detectable, and answers
the full 1,367 in 14 pages in under a minute.

WHAT IT EMITS
-------------
One all-day event per listing, on its start date.

  Racing   (Road Race, Criterium, Cyclocross, Track, Time Trial, Mountain Bike,
            Gravel, Hill Climb, MTB Enduro, Gravity)
              -> 'fitness', secondaries ['sports', 'outdoors']
  Riding   (Recreational, Bike Tour, Gran Fondo, Cycling Camp, Multisport, …)
              -> 'fitness', secondaries ['outdoors']

'fitness' is primary throughout, matching the mapping mapsee_ingest_runsignup
already uses for bike_race / bike_ride / mountain_bike_race, so the two adapters
cannot disagree about where a bike event lives. All three keys open onto
wegosie, so the secondary is information rather than reach.

THE DATE IS A DATE, AND THE OFFSET ON IT IS A LIE
--------------------------------------------------
`startDate` arrives as "2026-08-16T00:00:00.000-04:00". The time is 00:00:00 on
all 1,367 rows, and the offset is the SERVER'S, not the event's: a Kailua-Kona
event whose own `eventTimeZone` says "Hawaiian" is stamped -04:00, and the whole
feed only ever carries -04:00 or -05:00 while `eventTimeZone` spans Eastern
through Hawaiian. Parsing that as an instant moves the Hawaii race to 18:00 the
day BEFORE — well-formed, plausible, and a day wrong.

So only the first ten characters are read, as an all-day local date. This is the
CLAUDE.md rule about a source offering two spellings of one fact: the tell was
finding a record where they DISAGREE (Patricio's Kua Bay TTT) before choosing,
not after. `test_ingest_bikereg.py` pins it.

COORDINATES ARE THE SOURCE'S AND ARE KEPT
------------------------------------------
Every one of the 1,367 rows carries latitude and longitude, and none carries a
street address — only city/state/zip. So there is nothing for the sync's Census
pass to geocode that would not be strictly worse than the point BikeReg already
holds, and `coords_exact` opts out of it.

Checked for the default-pin failure Squarespace taught us: the most-repeated
coordinate is 28 events at 40.5473,-75.6104, which is the Valley Preferred
Cycling Center velodrome in Breinigsville PA genuinely running a weekly series
(Women's Wednesday, the Mike Budjnoski Bicycling Series). A repeated coordinate
here is a real venue, not an unfilled form. mapsee_link_series.py will chain it.

MEMBERSHIPS AND SIGNUP FORMS ARE NOT EVENTS
--------------------------------------------
BikeReg sells club dues, coaching passes and volunteer rosters through the same
calendar: "2026 USA Cycling Commissaires NASO Membership", "North Country
Adventure Team Volunteer Signup", "Valley Preferred Cycling Center - Training
Passes". They are typed 'Club Membership', they carry a placeholder date, and
there is nothing happening at that place on that day. 112 of 1,367. Same call as
RunSignup's virtual races, for the same reason: a pin that says something is
happening where nothing is, is worse than a gap.

A listing typed 'Club Membership' AND something real ("CCAP Fall School MTB
Clubs" is ['Mountain Bike','Club Membership','NEBRA']) is kept and classified on
the real type — the membership tag is how it is SOLD, not what it is.

CONDUCT. The GraphQL gateway is BikeReg's own documented public endpoint for
event data, read here under the production User-Agent, at the documented page
size, with a pause between pages. bikereg.com serves no robots.txt (404). No
registration, participant or results data is read; `registrationCount` is a
count and is used only as an optional local filter, never stored.

    python mapsee_ingest_bikereg.py --config bikereg_sources.json \\
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

API_URL = "https://outsideapi.com/fed-gw/graphql"

# `athleticEvent` is typed as the IAthleticEventDisplay INTERFACE, which carries
# only the location fields; staticUrl, coverPhoto, eventTypes and
# registrationCount live on the concrete AthleticEvent, hence the inline
# fragment. Asking for them on the interface is a GRAPHQL_VALIDATION_FAILED.
QUERY = """
query($p: SearchEventQueryParamsInput, $first: Int, $after: String) {
  athleticEventCalendar(searchParameters: $p, first: $first, after: $after) {
    totalCount
    pageInfo { hasNextPage endCursor }
    nodes {
      eventId name city state startDate endDate latitude longitude distanceString
      athleticEvent {
        ... on AthleticEvent {
          staticUrl zip country eventTimeZone eventTypes
          coverPhoto(imageSize: FULL_SIZE)
          registrationCount { count }
        }
      }
    }
  }
}
"""

# eventTypes -> secondary categories. The primary is always 'fitness' (see the
# docstring); these say what KIND of riding it is. Both values are in
# mapsee_ingest.VALID_CATEGORIES.
#
# The vocabulary is open-ended and most of it is not a kind of event at all:
# 'NEBRA', 'NYSBRA', 'Pennsylvania Cycling Association', 'Utah Cycling
# Association' and friends are the SANCTIONING BODY, tagged into the same list.
# They are deliberately absent from both sets, so they simply cast no vote
# rather than needing a blocklist that grows every time a new region joins.
_RACING = {
    "road race", "criterium", "cyclocross", "track", "time trial", "hill climb",
    "mountain bike", "gravel", "mtb enduro", "gravity", "stage race", "omnium",
}
_RIDING = {
    "recreational", "bike tour", "gran fondo", "cycling camp", "special event",
    "multisport", "charity ride", "fun ride", "clinic",
}

# Typed as a thing you BUY rather than a thing you attend. Dropped when nothing
# in _RACING or _RIDING also applies — see the docstring.
_NOT_AN_EVENT = {"club membership", "membership", "license", "one-day license"}

DEFAULTS = {
    "app_types": ["BIKEREG"],   # BIKEREG | RUNREG | SKIREG | TRIREG — the AthleteReg family
    "horizon_days": 365,        # bike seasons publish a year out; measured 1,367 events in 12mo
    "page_size": 100,           # what the calendar comfortably answers; 14 pages for BikeReg
    "max_pages": 60,            # runaway guard, not a cap — see fetch()
    "min_registrations": 0,     # 0 = keep everything. See ingest() for the distribution.
    "pause_s": 0.3,             # between pages; somebody else's free API
    "timeout_s": 90,
    # For catalog_curate.py coverage, which must not hit the network. Measured
    # over the 1,367-event window: 1,357 USA, 8 CAN, 1 ISL, 1 JAM.
    "countries": ["US", "CA", "IS", "JM"],
}

# The feed writes ISO-3; everything else in the pipeline speaks ISO-2.
_ISO3 = {"USA": "US", "CAN": "CA", "MEX": "MX", "GBR": "GB", "IRL": "IE",
         "ISL": "IS", "JAM": "JM", "AUS": "AU", "NZL": "NZ", "DEU": "DE",
         "FRA": "FR", "ITA": "IT", "ESP": "ES", "NLD": "NL", "CHE": "CH",
         "AUT": "AT", "BEL": "BE", "CAN ": "CA"}


# --- the feed ---------------------------------------------------------------

def _page(session, cfg: Dict[str, Any], after: Optional[str],
          lo: str, hi: str) -> Dict[str, Any]:
    body = {
        "query": QUERY,
        "variables": {
            "p": {"appTypes": list(cfg["app_types"]), "minDate": lo, "maxDate": hi},
            "first": int(cfg["page_size"]),
            "after": after,
        },
    }
    r = session.post(API_URL, json=body, timeout=cfg["timeout_s"])
    r.raise_for_status()
    doc = r.json()
    # A GraphQL error is a 200 with an "errors" key — raising here matters,
    # because the alternative is `data: null` read as "no events today".
    if doc.get("errors"):
        raise RuntimeError(f"GraphQL: {json.dumps(doc['errors'])[:300]}")
    cal = ((doc.get("data") or {}).get("athleticEventCalendar")) or {}
    return cal


def fetch(session, cfg: Dict[str, Any]) -> List[dict]:
    """Every listing in the horizon, walked by cursor.

    Unlike RunSignup this needs no partition: the calendar reports `totalCount`
    up front and pages honestly with `hasNextPage`, so a short read is VISIBLE
    rather than silent. That is the whole reason this adapter is on GraphQL and
    not on `bikereg.com/api/search`, which caps at 100 and says so nowhere.

    max_pages is a runaway guard. If it ever binds it means the feed grew past
    the guard, so it prints how many rows were left behind rather than stopping
    quietly.
    """
    today = date.today()
    lo = today.isoformat() + "T00:00:00Z"
    hi = (today + timedelta(days=int(cfg["horizon_days"]))).isoformat() + "T00:00:00Z"
    out: List[dict] = []
    after: Optional[str] = None
    total = None
    for _ in range(int(cfg["max_pages"])):
        cal = _page(session, cfg, after, lo, hi)
        if total is None:
            total = cal.get("totalCount")
        out.extend(cal.get("nodes") or [])
        info = cal.get("pageInfo") or {}
        if not info.get("hasNextPage"):
            break
        after = info.get("endCursor")
        if not after:
            break
        time.sleep(cfg["pause_s"])
    else:
        if total and len(out) < total:
            print(f"[bikereg] NOTE: stopped at the {cfg['max_pages']}-page guard with "
                  f"{len(out)} of {total} listing(s) read. Raise max_pages.")
    if total is not None and len(out) < total:
        print(f"[bikereg] read {len(out)} of {total} listing(s) the calendar reports")
    return out


# --- one listing -> one event -----------------------------------------------

def _types(node: dict) -> List[str]:
    ae = node.get("athleticEvent") or {}
    raw = ae.get("eventTypes") or []
    return [str(t).strip().lower() for t in raw if t]


def categorise(types: List[str]) -> Optional[tuple[str, List[str]]]:
    """(primary, secondaries), or None when the listing is not an event.

    A listing is only refused when it is typed as something you buy AND nothing
    on it is a kind of riding — a membership tag alongside 'Mountain Bike' is
    how the thing is sold, not what it is.
    """
    racing = any(t in _RACING for t in types)
    riding = any(t in _RIDING for t in types)
    if not (racing or riding):
        if any(t in _NOT_AN_EVENT for t in types):
            return None
        # Sanctioning-body tags only, or a type we have not seen. It is still a
        # cycling listing on a cycling calendar, so take the honest generic
        # rather than guessing at the discipline.
        return "fitness", norm_categories("fitness", ["outdoors"])
    extras = ["sports", "outdoors"] if racing else ["outdoors"]
    return "fitness", norm_categories("fitness", extras)


def _local_date(raw: Optional[str]) -> Optional[str]:
    """The date BikeReg means, as a naive all-day local date.

    Read the docstring before 'fixing' this to parse the offset. The offset is
    the server's and contradicts the event's own eventTimeZone; the date is the
    only part of this field that is true.
    """
    s = (raw or "").strip()
    if len(s) < 10:
        return None
    d = s[:10]
    try:
        datetime.strptime(d, "%Y-%m-%d")
    except ValueError:
        return None
    return d


def to_event(node: dict, cfg: Dict[str, Any]) -> Optional[NormalizedEvent]:
    name = (node.get("name") or "").strip()
    start_local = _local_date(node.get("startDate"))
    if not (name and start_local):
        return None

    mapped = categorise(_types(node))
    if mapped is None:
        return None                      # membership / licence / signup form
    primary, secondaries = mapped

    lat, lon = node.get("latitude"), node.get("longitude")
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return None                      # unplaceable: no street to fall back on
    # 0,0 is the Atlantic, and it is what an unfilled coordinate looks like.
    if lat == 0.0 and lon == 0.0:
        return None

    ae = node.get("athleticEvent") or {}
    regs = ((ae.get("registrationCount") or {}).get("count")) or 0
    if regs < int(cfg.get("min_registrations") or 0):
        return None

    city = (node.get("city") or "").strip()
    region = (node.get("state") or "").strip()
    country = _ISO3.get((ae.get("country") or "").strip().upper(),
                        (ae.get("country") or "").strip().upper() or None)

    # An end date only when the listing genuinely spans days — endDate equals
    # startDate on 1,150 of 1,367, and writing a same-day end on an all-day
    # event tells the product nothing it did not already assume.
    end_local = _local_date(node.get("endDate"))
    if end_local and end_local <= start_local:
        end_local = None

    dist = (node.get("distanceString") or "").strip()
    kinds = ", ".join(t.title() for t in _types(node) if t in _RACING or t in _RIDING)
    parts = [f"{kinds}." if kinds else "", f"Distances: {dist}." if dist else ""]
    description = " ".join(p for p in parts if p).strip() or None

    # BikeReg has no venue-name field. The city is the honest label — a race
    # start is a road or a field, and inventing a venue from the event title
    # ("The Filthy 50") would put a name on the map that is not a place.
    fp = make_fingerprint(name, start_local, city, city)
    ev = NormalizedEvent(
        source="bikereg",
        # THE DATE BELONGS IN THE IDENTITY. One BikeReg eventId legitimately
        # appears once per OCCURRENCE: id 75112, "Valley Preferred Cycling
        # Center - Try the Track", comes back on 2026-08-16, 08-22 and 09-20 as
        # three nodes, and 39 of 1,246 ids do this across 160 rows.
        #
        # EventStore keys identity on (source, source_id) and, when the same key
        # arrives with a new fingerprint, POPS the record it had. So a bare
        # eventId makes each occurrence delete the previous one — 1,269 events
        # ingested, 1,148 surviving, the 121 missing all being the earlier dates
        # of a series. That is the Localist duplicate bug in CLAUDE.md running
        # backwards, and in the database it is worse than a loss: the sync
        # upserts on external_id, so a fingerprint that moves orphans a row
        # nothing will ever revisit.
        source_id=f"{node.get('eventId') or fp}:{start_local}",
        name=name,
        description=description,
        start_local=start_local,
        end_local=end_local,
        venue_name=city or None,
        latitude=lat,
        longitude=lon,
        city=city or None,
        region=region or None,
        country=country,
        postal_code=(ae.get("zip") or "").strip() or None,
        category=primary,
        categories=secondaries,
        ticket_url=(ae.get("staticUrl") or "").strip() or None,
        poster_image_url=(ae.get("coverPhoto") or "").strip() or None,
        # The feed's own point, and there is no street address here for the
        # Census pass to do better with. See the docstring.
        coords_exact=True,
    )
    ev.fingerprint = fp
    return ev


# --- run --------------------------------------------------------------------

def ingest(store: EventStore, session, cfg: Dict[str, Any]) -> int:
    nodes = fetch(session, cfg)
    kept = 0
    dropped = {"not_an_event": 0, "no_coords": 0, "no_date_or_name": 0,
               "below_min_registrations": 0}
    engaged: List[tuple] = []
    for n in nodes:
        ev = to_event(n, cfg)
        if ev is None:
            # Re-derived for the tally only. "N listings, M ingested" with no
            # reason why is how a filter that swallows everything goes unseen.
            ae = n.get("athleticEvent") or {}
            regs = ((ae.get("registrationCount") or {}).get("count")) or 0
            if not ((n.get("name") or "").strip() and _local_date(n.get("startDate"))):
                dropped["no_date_or_name"] += 1
            elif categorise(_types(n)) is None:
                dropped["not_an_event"] += 1
            elif n.get("latitude") in (None, 0) or n.get("longitude") in (None, 0):
                dropped["no_coords"] += 1
            elif regs < int(cfg.get("min_registrations") or 0):
                dropped["below_min_registrations"] += 1
            else:
                dropped["no_coords"] += 1
            continue
        store.upsert(ev)
        kept += 1
        regs = (((n.get("athleticEvent") or {}).get("registrationCount") or {}).get("count")) or 0
        if regs:
            engaged.append((regs, ev.name, ev.city))

    print(f"[bikereg] {len(nodes)} listing(s) in the window; +{kept} event(s)")
    shown = ", ".join(f"{v} {k}" for k, v in sorted(dropped.items()) if v)
    print(f"[bikereg] dropped: {shown or 'none'}")
    if engaged:
        engaged.sort(reverse=True)
        top = "; ".join(f"{r} {n[:34]} ({c})" for r, n, c in engaged[:3])
        print(f"[bikereg] {len(engaged)}/{kept} carry a registration count; busiest: {top}")
    return kept


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Ingest AthleteReg (BikeReg) cycling events onto the fitness layer.")
    ap.add_argument("--config", default="bikereg_sources.json")
    ap.add_argument("--store", default="mapsee_events.json")
    a = ap.parse_args(argv)

    cfg = dict(DEFAULTS)
    if os.path.exists(a.config):
        try:
            cfg.update(json.loads(open(a.config, encoding="utf-8").read()) or {})
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[bikereg] unreadable {a.config} ({exc}) — using defaults")
    else:
        print(f"[bikereg] no {a.config} — using defaults")

    session = requests.Session()
    session.headers.update(
        {"User-Agent": "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)",
         "Accept": "application/json"})
    store = EventStore(a.store)
    try:
        ingest(store, session, cfg)
    except Exception as exc:  # noqa: BLE001
        print(f"[bikereg] FAILED: {exc}")
        return 0                       # a dead feed must not fail the pipeline
    store.save()
    print(f"[bikereg] done; store now holds {len(store.records)} unique events.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
