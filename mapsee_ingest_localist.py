#!/usr/bin/env python3
"""
mapsee_ingest_localist.py — import Localist (Concept3D) calendars into the Mapsee
store. Localist powers hundreds of university, city and arts-org event calendars,
and its API is READ-ONLY and PUBLIC: GET {base}/api/2/events returns JSON with
coordinates, so no geocoding is needed. Great for arts / learning / community.

Config (localist_sources.json): a list of
    { "name": "Cornell University",
      "base_url": "https://events.cornell.edu",
      "category": "learning",       # optional fixed Mapsee KEY (else derived per-event)
      "days": 90,                   # optional lookahead window
      "max_pages": 40,              # optional; DEFAULT_MAX_PAGES pages of 100 otherwise
      "crawl_delay": 1 }            # optional seconds between pages; DEFAULT_CRAWL_DELAY

A `measured_<when>` beside a max_pages is the tenant's distinct events in 90
days (distinct=true, pp=1, page.total_items): from that date, or from 2026-02-01
(spring) or 2026-06-01 (summer). max_pages is the busiest of them x 1.25, in
pages, rounded up to five; test_ingest_localist.py fails if one outgrows it.

    python mapsee_ingest_localist.py --config localist_sources.json --store mapsee_events.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any, Dict, List, Optional

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

from mapsee_ingest import (NormalizedEvent, EventStore, make_fingerprint, norm_categories,
                           notice_reason, strip_notice)

# Event.status values that take an event off the map. Measured 2026-10-05 on the
# first page (days=90, pp=100) of all 56 sources: 5,410 "live", 4 "soldout", 3
# "canceled" of 5,417. Sold out is still happening. "postponed" was not seen and
# is here because its date, like a cancelled one's, is not happening.
DEAD_STATUSES = frozenset({"canceled", "cancelled", "postponed"})

# The API's largest page.
PER_PAGE = 100

# PAGES ARE A BUDGET, NOT A GUESS ABOUT THE CALENDAR. This was a bare
# range(1, 11) until 2026-10-11, when one pp=1 request per configured tenant
# (page.total_items, days=90) found 18 of 56 holding more than the 1,000
# occurrence wraps ten pages could reach: 10,274 occurrences never read.
# events.in.gov had 3,099 over 31 pages and page 10 ended on 2026-10-26, so no
# Indiana state-park hike after that morning ever arrived; KU had 2,255, UT
# Austin 1,900, Stanford 1,825. Nothing said so: the loop simply ran out.
#
# A page is 100 EVENTS now (distinct=true, below), not 100 occurrences. The 57
# tenants held 25,285 distinct events on 281 pages that day, the largest KU's
# 1,752 on 18. Their busiest 90 days, one pp=1 request each over a past window
# (spring for a campus, summer for a state calendar): seven pass 2,000, Indiana
# at 3,638 the most. Twelve, those seven among them, need more than 20 pages
# once a quarter is added for growth, and carry their own `max_pages` beside
# the measurements that sized it; 20 reads the other 45 whole with that quarter
# to spare. The walk stops at page.total, so a cap costs nothing until a
# calendar really is that big, and a walk that ends on the cap says so.
DEFAULT_MAX_PAGES = 20

# Every configured tenant's robots.txt, read 2026-10-11 with the production
# User-Agent, is Localist's stock file: `Allow: /` and `Crawl-delay: 1`, 57 of
# 57. The API is a documented one and so not bound by the file, but the delay
# is the publisher saying how fast, and tribe and ics honour theirs; the pages
# of one tenant used to go back to back.
DEFAULT_CRAWL_DELAY = 1.0

# Localist event_type filter name -> Mapsee frontend category KEY.
_TYPE_MAP = {
    "concert": "music", "music": "music", "performance": "arts",
    # A campus calendar's own "Theatre" event_type is the least ambiguous signal
    # any adapter gets, and it used to land on 'arts' — so a production showed a
    # 🎨 pin labelled Arts, and nothing downstream ever corrected it:
    # _PROMOTABLE_TO_THEATER in mapsee_supabase_sync.py is {"music", "other"},
    # which does not include 'arts'. 'performance' stays 'arts' on purpose — a
    # dance or a recital is tagged that way as often as a play is.
    "theatre": "theater", "theater": "theater",
    "art": "arts", "exhibit": "arts", "exhibition": "arts",
    "dance": "arts", "film": "arts", "lecture": "learning", "workshop": "learning",
    "class": "learning", "seminar": "learning", "conference": "learning",
    "athletics": "sports", "sports": "sports", "game": "sports",
    "volunteer": "volunteer", "community": "community", "festival": "music",
    "food": "food", "market": "market", "kids": "kids", "family": "kids",
    "outdoor": "outdoors", "recreation": "outdoors",
    # movement (wegosie.com). Campus calendars carry these as their own
    # event_types, and every one of them used to land on "community".
    "fitness": "fitness", "wellness": "fitness", "exercise": "fitness",
    "yoga": "fitness", "intramural": "fitness", "run": "running",
    "walk": "fitness", "hike": "outdoors", "bike": "fitness",
}


def _categories(event: Dict[str, Any], default: Optional[str]) -> tuple:
    """(primary, extras). Localist events carry a LIST of event_types — a campus
    "Wellness Walk" is tagged Recreation AND Wellness — and this used to return
    the first match and bin the rest. Keeping the others as secondaries is free
    breadth: that walk now reaches both the outdoors and the movement lens."""
    hits = []
    for t in (((event.get("filters") or {}).get("event_types")) or []):
        m = _TYPE_MAP.get((t.get("name") or "").strip().lower())
        if m and m not in hits:
            hits.append(m)
    if not hits:
        return (default or "community", [])
    return (hits[0], norm_categories(hits[0], hits[1:]))


def _instance(event: Dict[str, Any]) -> Dict[str, Any]:
    """The single occurrence Localist attached to THIS wrap.

    /api/2/events returns one wrap PER OCCURRENCE, not per event: a three-month
    exhibition comes back once for every day it is open, same event id each
    time, each wrap carrying exactly one instance on a different date. Measured
    on UT Dallas 2026-08-12: 774 wraps for 357 distinct events, one exhibition
    accounting for 59 of them on its own.
    """
    inst = event.get("event_instances") or []
    return (inst[0].get("event_instance") or {}) if (inst and isinstance(inst[0], dict)) else {}


def _instance_start(event: Dict[str, Any]) -> str:
    return str(_instance(event).get("start") or "")


def dead_status(wrap: Dict[str, Any]) -> Optional[str]:
    status = str((wrap.get("event") or {}).get("status") or "").strip().lower()
    return status if status in DEAD_STATUSES else None


def to_event(wrap: Dict[str, Any], src: Dict[str, Any], *,
             tombstone: bool = False) -> Optional[NormalizedEvent]:
    """The live row, or with tombstone=True the record of an event Localist marks
    canceled (for EventStore.cancel); None for the other kind. One builder, so
    the tombstone carries the stored row's fingerprint: title | first_date |
    place. The status is the EVENT's, so it covers every occurrence."""
    event = wrap.get("event") or {}
    title = (event.get("title") or "").strip()
    if not title:
        return None
    if bool(dead_status(wrap)) != tombstone:
        return None
    # "CANCELED: Ecology Seminar Series: Joseph Hoyt" (UGA, 2026-10-05) was
    # stored under the title inside the notice; "CANCELLED" alone names nothing.
    if tombstone and notice_reason(title):
        title = strip_notice(title)
        if not title:
            return None
    inst0 = _instance(event)
    start = inst0.get("start")
    end = inst0.get("end")
    if not start:
        return None
    geo = event.get("geo") or {}
    try:
        lat = float(geo.get("latitude"))
        lon = float(geo.get("longitude"))
    except (TypeError, ValueError):
        return None                                        # no coordinates -> can't map it
    # IDENTITY IS THE EVENT'S OWN FIRST DATE, not the occurrence this wrap
    # happens to carry. That distinction is the whole bug.
    #
    # `days=90` is a ROLLING window, so which occurrences come back depends on
    # the day the job runs. Every wrap of one event shares a source_id (the
    # Localist id) but produced a DIFFERENT fingerprint here, so EventStore
    # re-keyed the record once per wrap and the survivor was whichever occurrence
    # happened to be processed last. Move the window and that survivor changes,
    # so the fingerprint changed on nearly every run — a new external_id, a new
    # database row, and the old one left behind because the sync upserts on
    # (external_source, external_id). Meanwhile _fill_missing kept the FIRST
    # start_local it saw, which is why the duplicates all carried an identical
    # starts_at and different ids: the visible field was stable and the identity
    # underneath it was not.
    #
    # first_date is a property of the event, so it does not move with the window.
    # Measured on UT Dallas: 311 of 315 single-occurrence events already have
    # first_date == their instance date, so their identity is UNCHANGED and the
    # catalog is not mass re-keyed. The 4 that differ are recurring events whose
    # earlier dates have passed - exactly the unstable ones this is fixing.
    date_key = str(event.get("first_date") or start)[:10]
    desc = (event.get("description_text") or "").strip() or None
    if desc:
        desc = " ".join(desc.split())
    place = event.get("location_name") or geo.get("street")
    nev = NormalizedEvent(
        source="localist:" + src["name"].lower().replace(" ", "-"),
        source_id=str(event.get("id") or make_fingerprint(title, date_key, place)),
        name=title,
        description=desc,
        start_local=start,
        end_local=end,
        venue_name=place,
        latitude=lat, longitude=lon,
        address=geo.get("street"), city=geo.get("city"),
        region=geo.get("state"), country=geo.get("country"), postal_code=geo.get("zip"),
        **dict(zip(("category", "categories"), _categories(event, src.get("category")))),
        poster_image_url=event.get("photo_url") or None,
        ticket_url=event.get("localist_url") or event.get("url") or None,
    )
    nev.fingerprint = make_fingerprint(title, date_key, place)
    return nev


def ingest(store: EventStore, session, src: Dict[str, Any]) -> int:
    base = (src.get("base_url") or "").rstrip("/")
    if not base:
        return 0                                           # allow note-only config entries
    name = src.get("name", "?")
    days = src.get("days", 90)
    max_pages = max(1, int(src.get("max_pages") or DEFAULT_MAX_PAGES))
    delay = float(src.get("crawl_delay", DEFAULT_CRAWL_DELAY) or 0)
    # One wrap per OCCURRENCE arrives (see _instance), and they span pages, so
    # collapse by event id across the whole fetch before anything is upserted.
    # Keeping the EARLIEST occurrence makes start_local the next upcoming date
    # rather than whichever wrap happened to land first, which is what the
    # listing should show and what _fill_missing was silently deciding before.
    #
    # Upserting each wrap also meant EventStore.upsert ran once per occurrence
    # for the same (source, source_id), popping and re-adding the record every
    # time. Sixty writes to arrive where one belongs.
    #
    # distinct=true asks the API to do that collapse itself: one wrap per event,
    # carrying its first instance in the window, which is the occurrence the
    # collapse keeps. Measured 2026-10-11 over all 57 tenants (days=90): 48,710
    # wraps became 25,285 events, and 515 pages became 281. The old walk and
    # this one, run the same evening, stored 11,246 rows in common, identical
    # field for field; the old walk's only two others were publisher edits
    # between the runs (one event deleted, its /api/2/events/<id> a 404; one
    # that lost its geo). The collapse stays, for a server that ignores the
    # parameter.
    best: Dict[Any, Dict[str, Any]] = {}
    order: List[Any] = []
    seen_wraps = 0
    pages = 0
    total_pages: Optional[int] = None
    last = False
    for page in range(1, max_pages + 1):
        if page > 1 and delay > 0:
            time.sleep(delay)                              # robots.txt Crawl-delay
        # A failed page ends the walk and keeps what the earlier pages brought.
        # It used to raise into main()'s per-source handler, which threw away
        # every page already read, and a longer walk is more pages to time out:
        # UGA's page 9 timed out in the 2026-10-11 measurement, and the eight
        # before it still gave 583 rows where the old code would have kept none.
        try:
            r = session.get(f"{base}/api/2/events",
                            params={"days": days, "pp": PER_PAGE, "page": page,
                                    "distinct": "true"}, timeout=25)
        except Exception as exc:                           # noqa: BLE001
            print(f"[localist] {name} p{page} failed: {type(exc).__name__}: {exc}")
            break
        if r.status_code != 200:
            print(f"[localist] {name} p{page} HTTP {r.status_code}")
            break
        try:
            data = r.json()
        except ValueError as exc:
            print(f"[localist] {name} p{page} bad JSON: {exc}")
            break
        pages += 1
        events = data.get("events") or []
        if not events:
            last = True
            break
        for wrap in events:
            seen_wraps += 1
            event = wrap.get("event") or {}
            # No id to group on -> keep the wrap on its own key rather than
            # letting every id-less event collapse into one.
            key = event.get("id")
            if key is None:
                key = ("_noid", seen_wraps)
            prev = best.get(key)
            if prev is None:
                best[key] = wrap
                order.append(key)
            elif _instance_start(event) < _instance_start(prev.get("event") or {}):
                best[key] = wrap
        # THE LAST PAGE IS THE ONE THE API NAMES. page.total is the page count
        # (page.total_items the wraps it counts); a response without the block
        # ends on its first short page instead of asking for one more empty one.
        pg = data.get("page") or {}
        try:
            total_pages = int(pg["total"]) if pg.get("total") is not None else None
            current = int(pg.get("current") or page)
        except (TypeError, ValueError):
            total_pages, current = None, page
        if total_pages is not None:
            if current >= total_pages:
                last = True
                break
        elif len(events) < PER_PAGE:
            last = True
            break
    if not last and pages == max_pages:
        left = (f"{total_pages - pages} of {total_pages} pages unread" if total_pages
                else "more pages may follow")
        print(f"[localist] {name}: stopped at max_pages={max_pages}, {left}; "
              f"raise its max_pages in the config")
    kept = cancelled = 0
    for key in order:
        dead = dead_status(best[key])
        nev = to_event(best[key], src, tombstone=bool(dead))
        if not nev:
            continue
        if dead:
            # Refused, it stayed on the map if an earlier run stored it.
            if store.cancel(nev, f"status:{dead}") == "cancelled":
                cancelled += 1
            continue
        store.upsert(nev)
        kept += 1
    collapsed = seen_wraps - len(order)
    print(f"[localist] {name}: kept {kept} of {len(order)} event(s) on {pages} page(s)"
          + (f" ({collapsed} duplicate occurrence wrap(s) collapsed)" if collapsed else "")
          + (f"; {cancelled} canceled (tombstoned)" if cancelled else ""))
    return kept


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import Localist calendars into the Mapsee store.")
    ap.add_argument("--config", required=True)
    ap.add_argument("--store", default="mapsee_events.json")
    a = ap.parse_args(argv)
    sources = json.loads(open(a.config, encoding="utf-8").read())
    session = requests.Session()
    session.headers.update({"User-Agent": "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)"})
    store = EventStore(a.store)
    total = 0
    for src in sources:
        try:
            total += ingest(store, session, src)
        except Exception as exc:
            print(f"[localist] {src.get('name', '?')} FAILED: {exc}")
    store.save()
    print(f"[localist] done: +{total}; store now holds {len(store.records)} unique events.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
