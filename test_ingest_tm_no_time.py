#!/usr/bin/env python3
"""A Ticketmaster listing with no published start time is not ingested.

Ticketmaster ships tours, and some games before their time is set, as a bare
`localDate`. The sync anchors a bare day to the venue's local midnight for the
whole day, so on a phone the row read "Today, 12:00 AM" and sorted above every
real event in Nearby. In Seattle on 2026-09-13 Huskies Women's Volleyball was in
the table twice: SeatGeek at 2:00 PM, Ticketmaster with no time.

Also what a listing is filed under, and what is not a listing: a show
Ticketmaster gave no segment, in a theatre, is theater; a "Children's Theatre"
genre adds kids; a "Not an Event Ticket" product is never stored. Measured live
2026-10-23 over 5,880 rows: 32 Broadway and West End shows on 'other', 45
children's shows on theater alone, and 7 products.

Cases, one line each; exits non-zero on any failure.
  python test_ingest_tm_no_time.py
"""
import argparse
import sys

import mapsee_ingest as mi
import mapsee_supabase_sync as sync

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


# ---- what a listing is filed under -------------------------------------------
def classified(venue, segment=None, genre=None, sub=None):
    raw = listing("c", localDate="2026-09-13", localTime="19:30:00")
    raw["_embedded"]["venues"][0]["name"] = venue
    c = {"primary": True}
    for key, name in (("segment", segment), ("genre", genre), ("subGenre", sub)):
        if name:
            c[key] = {"name": name}
    raw["classifications"] = [c]
    return mi.parse_ticketmaster_event(raw)


def same(label, got, want):
    check(label, got == want, f"got {got!r}, want {want!r}")


for venue, seg in [("Broadhurst Theatre", None), ("New World Stages - Stage 5", "Undefined"),
                   ("Palace Theatre-NY", "Miscellaneous"), ("Signature Theater", None)]:
    same(f"no segment ({seg}) at {venue!r} is a stage show", classified(venue, seg).category,
         "Arts & Theatre")
same("a segment TM did give is never replaced, even in a theatre",
     classified("WAMU Theater", "Music").category, "Music")
same("no segment at an arena stays unfiled", classified("Climate Pledge Arena").category, None)

kids = classified("Théâtre de la Porte Saint-Martin", "Arts & Theatre", "Children's Theatre")
same("a Children's Theatre genre adds kids and keeps the segment",
     (kids.category, kids.categories), ("Arts & Theatre", ["kids"]))
same("  ...in the subGenre too, with a typographic apostrophe",
     classified("Teatro Silvia Pinal", "Arts & Theatre", "Theatre", "Children\u2019s Theatre").categories,
     ["kids"])
same("  ...and Children's Music", classified("Salle Pleyel", "Music", "Children's Music").categories, ["kids"])
same("'Family' is not children: Madame Tussauds and the London Dungeon carry it",
     classified("The London Dungeon", "Miscellaneous", "Family").categories, [])
rec = kids.as_record("2026-09-13T00:00:00Z")
same("  ...and the sync puts the show on theater with kids beside it",
     sync.derive_categories(rec), ("theater", ["kids"]))


# ---- products are not listings ------------------------------------------------
class CancelStore(FakeStore):
    def __init__(self):
        super().__init__()
        self.cancelled = []

    def cancel(self, ev, why):
        self.cancelled.append(ev.source_id)
        return "cancelled"


def product(eid, name, status="onsale"):
    raw = listing(eid, localDate="2026-09-13", localTime="19:00:00")
    raw["name"] = name
    raw["dates"]["status"] = {"code": status}
    return raw


products = [product("amp", "WAMU Theater Amplified Access: MONSTA X (Not an Event Ticket)"),
            product("poster", "THIS IS NOT AN EVENT TICKET: PERSONALIZED SIGNED TOUR POSTER"),
            product("bowl", "VIP Bowling Lane Add On - Space Bacon - Not a Concert Ticket"),
            product("vip", "Akaash Singh - Generational Triumph Tour | VIP Upgrade"),
            product("park", "Parking: Hamilton"),
            product("pass", "Taylor Swift Parking Pass"),
            product("gone", "WAMU Theater Amplified Access: Chuy (Not an Event Ticket)", "canceled")]
shows = [product("show", "MONSTA X"), product("band", "4 HR PARKING"),
         product("event", "An Evening with the Not Ticketed Choir")]
page = {"_embedded": {"events": products + shows}, "page": {"totalPages": 1, "number": 0}}
mi.http_get = lambda *a, **k: FakeResp(page)
mi.build_tm_params = lambda args, key: {"apikey": key}
try:
    store = CancelStore()
    n = mi.ingest_ticketmaster(store, None, None, argparse.Namespace(size=199, max_pages=0), "key")
finally:
    mi.http_get, mi.build_tm_params = real_get, real_params
same("the loop stores the shows and none of the products",
     [ev.source_id for ev in store.rows], ["show", "band", "event"])
same("  ...and a product's cancellation tombstones nothing (it can share the show's fingerprint)",
     store.cancelled, [])

print(f"\n{'PASS' if not fails else 'FAIL'}: Ticketmaster start times, categories and products "
      f"({fails} failing)")
sys.exit(1 if fails else 0)
