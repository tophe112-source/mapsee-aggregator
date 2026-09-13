#!/usr/bin/env python3
"""A Ticketmaster listing with no published start time is not ingested.

Ticketmaster ships tours, and some games before their time is set, as a bare
`localDate`. The sync anchors a bare day to the venue's local midnight for the
whole day, so on a phone the row read "Today, 12:00 AM" and sorted above every
real event in Nearby. In Seattle on 2026-09-13 Huskies Women's Volleyball was in
the table twice: SeatGeek at 2:00 PM, Ticketmaster with no time.

Cases, one line each; exits non-zero on any failure.
  python test_ingest_tm_no_time.py
"""
import argparse
import sys

import mapsee_ingest as mi

fails = 0


def check(label, ok, detail=""):
    global fails
    print(("ok    " if ok else "FAIL  ") + label + ("" if ok else f"   {detail}"))
    if not ok:
        fails += 1


def listing(eid, **start):
    return {
        "id": eid, "name": f"Listing {eid}", "url": f"https://www.ticketmaster.com/event/{eid}",
        "dates": {"start": start, "timezone": "America/Los_Angeles"},
        "_embedded": {"venues": [{"name": "Alaska Airlines Arena", "city": {"name": "Seattle"},
                                   "state": {"stateCode": "WA"}, "country": {"countryCode": "US"},
                                   "location": {"latitude": "47.6516", "longitude": "-122.3016"}}]},
    }


timed = listing("timed", localDate="2026-09-13", localTime="14:00:00", dateTime="2026-09-13T21:00:00Z")
tour = listing("tour", localDate="2026-09-13", noSpecificTime=True)
tba = listing("tba", localDate="2026-09-13", timeTBA=True)
tba_with_time = listing("tba-time", localDate="2026-09-13", localTime="00:00:00", timeTBA=True)
date_tbd = listing("tbd", localDate="2026-09-13", localTime="19:00:00", dateTBD=True)
bare = listing("bare", localDate="2026-09-13")

# ---- the predicate -----------------------------------------------------------
check("a listing with a published local time has a clock", mi.tm_has_clock(timed) is True)
check("a tour flagged noSpecificTime has no clock", mi.tm_has_clock(tour) is False)
check("timeTBA with no localTime has no clock", mi.tm_has_clock(tba) is False)
check("timeTBA wins over a placeholder 00:00 localTime", mi.tm_has_clock(tba_with_time) is False)
check("a dateTBD listing has no clock even with a time", mi.tm_has_clock(date_tbd) is False)
check("a bare localDate with no flags has no clock", mi.tm_has_clock(bare) is False)
check("a listing with no dates at all has no clock", mi.tm_has_clock({"id": "x"}) is False)

# ---- the fetch loop: only the timed listing reaches the store ----------------
class FakeResp:
    def __init__(self, body):
        self.body = body

    def json(self):
        return self.body


class FakeStore:
    def __init__(self):
        self.rows = []

    def upsert(self, ev):
        self.rows.append(ev)


page = {"_embedded": {"events": [timed, tour, tba, tba_with_time, date_tbd, bare]},
        "page": {"totalPages": 1, "number": 0}}
real_get, real_params = mi.http_get, mi.build_tm_params
mi.http_get = lambda *a, **k: FakeResp(page)
mi.build_tm_params = lambda args, key: {"apikey": key}
try:
    store = FakeStore()
    n = mi.ingest_ticketmaster(store, None, None, argparse.Namespace(size=199, max_pages=0), "key")
finally:
    mi.http_get, mi.build_tm_params = real_get, real_params

ids = [ev.source_id for ev in store.rows]
check("the loop stores only the listing with a start time", ids == ["timed"], ids)
check("and counts it as the one processed", n == 1, n)
check("the stored row keeps its real local start", store.rows and store.rows[0].start_local == "2026-09-13T14:00:00",
      store.rows[0].start_local if store.rows else None)

print(f"\n{'PASS' if not fails else 'FAIL'}: Ticketmaster no-start-time listings ({fails} failing)")
sys.exit(1 if fails else 0)
