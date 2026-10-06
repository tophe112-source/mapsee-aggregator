#!/usr/bin/env python3
"""
test_ingest_bibliocommons.py - the ways a library events gateway is not what it
looks like.

Every fixture here is the SHAPE of something read live off
gateway.bibliocommons.com on 2026-09-03 across six library systems. The
expensive cases are the two that return 200 and look completely healthy: a date
filter that is accepted and ignored, and a `featuredImageId` that resolves to a
stock tile shared by four hundred rows.

    python test_ingest_bibliocommons.py
"""
import copy
import json
import os
import sys
import tempfile
import time
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mapsee_ingest_bibliocommons as BC
import mapsee_ingest as shared_ingest

fails = []


def check(label, cond, detail=""):
    print(f"{'ok  ' if cond else 'FAIL'} {label}{'' if cond else '   ' + str(detail)}")
    if not cond:
        fails.append(label)


SITE = {"name": "Test Library", "slug": "testlib", "category": "learning"}

BRANCH = {
    "id": "48", "name": "Manning",
    "address": {"country": "US", "zip": "60612", "state": "IL",
                "city": "Chicago", "street": "S. Hoyne Avenue", "number": "6"},
    "mapLocation": {"timeZone": "America/Chicago",
                    "centrePoint": {"lat": 41.881111, "lng": -87.679283}},
}
OFFSITE = {
    "id": "p1", "name": "Coulwood Community Clubhouse",
    "address": {"country": "US", "zip": "28214", "state": "NC",
                "city": "Charlotte", "street": "Coulwood Drive", "number": "506"},
    "mapLocation": {"timeZone": "America/New_York", "isGeocoded": True,
                    "centrePoint": {"lat": 35.2980823, "lng": -80.9451317}},
}


def ent(**over):
    base = {
        "locations": {"48": BRANCH},
        "places": {"p1": OFFSITE},
        "eventTypes": {"t1": {"name": "Story Time"},
                       "t2": {"name": "Crafts, Games and Play"},
                       "t3": {"name": "Computers and Technology"},
                       "t4": {"name": "Community & Volunteering"}},
        "eventAudiences": {"a1": {"name": "Toddlers: 18 to 36 months"},
                           "a2": {"name": "Adults: 18 and up"},
                           "a3": {"name": "Teens: 13 to 19 years"}},
        "images": {"stock": {"id": "stock", "tag": "EventType",
                             "url": "https://x.test/AuthorEvent.png"},
                   "own": {"id": "own", "tag": "Event",
                           "url": "https://x.test/real-poster.jpg"}},
        "events": {},
    }
    base.update(over)
    return base


def evt(eid="e1", title="Computer Basics", start="2026-09-04T09:30",
        end="2026-09-04T11:00", branch="48", nonbranch=None, types=("t3",),
        auds=(), image=None, cancelled=False, desc="<p>Bring a <b>laptop</b>.</p>"):
    return {"id": eid, "definition": {
        "title": title, "start": start, "end": end, "description": desc,
        "branchLocationId": branch, "nonBranchLocationId": nonbranch,
        "typeIds": list(types), "audienceIds": list(auds),
        "featuredImageId": image, "isCancelled": cancelled}}


# ---------------------------------------------------- 1. times are LOCAL
print("the clock time is a clock time, not an instant")
e = BC.to_event(evt(), ent(), SITE)
check("start goes to start_local with seconds", e.start_local == "2026-09-04T09:30:00", e.start_local)
check("...and start_utc stays None, or a 10:00 storytime is served at 03:00",
      e.start_utc is None, e.start_utc)
check("the end is treated the same", e.end_local == "2026-09-04T11:00:00", e.end_local)
check("the branch's IANA zone is carried", e.timezone == "America/Chicago", e.timezone)
check("an end at or before the start is dropped",
      BC.to_event(evt(end="2026-09-04T09:30"), ent(), SITE).end_local is None)
check("a bare date survives as a date", BC._iso_local("2026-09-04") == "2026-09-04")
check("an offset that IS present is left alone, not reinterpreted",
      BC._iso_local("2026-09-04T09:30:00Z") == "2026-09-04T09:30:00Z")
check("...including a numeric offset",
      BC._iso_local("2026-09-04T09:30:00+01:00") == "2026-09-04T09:30:00+01:00")
check("garbage is None, not a guess", BC._iso_local("next Tuesday") is None)

# ---------------------------------------------- 2. the coordinate is the fact
print()
print("the coordinate is the fact and the address is derived")
check("the branch point is used", (e.latitude, e.longitude) == (41.881111, -87.679283))
check("...and marked exact, so the sync does not geocode over it", e.coords_exact is True)
check("the address is assembled from parts", e.address == "6 S. Hoyne Avenue", e.address)
check("city / region / country / postcode all survive",
      (e.city, e.region, e.country, e.postal_code) == ("Chicago", "IL", "US", "60612"),
      (e.city, e.region, e.country, e.postal_code))
# A NUMBER WITH NO STREET IS NOT AN ADDRESS, and a street with no number
# geocodes to the middle of the road. Neither is fatal here (the coordinate
# places the pin) but neither may be written as if it were a doorstep.
half = dict(BRANCH, address={"city": "Chicago", "street": "S. Hoyne Avenue"})
e2 = BC.to_event(evt(), ent(locations={"48": half}), SITE)
check("a street with no number is kept as the street", e2.address == "S. Hoyne Avenue", e2.address)
nonum = dict(BRANCH, address={"city": "Chicago", "number": "6"})
e3 = BC.to_event(evt(), ent(locations={"48": nonum}), SITE)
check("a number with no street is not an address", e3.address is None, e3.address)

# 0,0 is what a geocoder writes for "no idea".
null_island = dict(BRANCH, mapLocation={"centrePoint": {"lat": 0, "lng": 0}})
check("null island is not a location",
      BC.to_event(evt(), ent(locations={"48": null_island}), SITE) is None)

# ------------------------------------------------------- 3. where it happens
print()
print("an event can be off-site, and dropping those loses the outreach work")
off = BC.to_event(evt(branch=None, nonbranch="p1"), ent(), SITE)
check("nonBranchLocationId resolves through `places`", off is not None)
check("...to that place's own coordinate",
      (off.latitude, off.longitude) == (35.2980823, -80.9451317))
check("...and its own name", off.venue_name == "Coulwood Community Clubhouse", off.venue_name)
check("no branch and no place means no pin, so no row",
      BC.to_event(evt(branch=None, nonbranch=None), ent(), SITE) is None)
check("a cancelled programme is not an event",
      BC.to_event(evt(cancelled=True), ent(), SITE) is None)
check("a row with no title is dropped", BC.to_event(evt(title=""), ent(), SITE) is None)
check("a row with no start is dropped", BC.to_event(evt(start=""), ent(), SITE) is None)

# ------------------------------------------------------------ 4. stock art
print()
print("most event images are one stock tile shared by hundreds of rows")
check("an EventType tile is refused", BC.to_event(evt(image="stock"), ent(), SITE).poster_image_url is None)
check("the event's own picture is taken",
      BC.to_event(evt(image="own"), ent(), SITE).poster_image_url == "https://x.test/real-poster.jpg")
check("a missing image is not an error", BC.to_event(evt(image=None), ent(), SITE).poster_image_url is None)

# ---------------------------------------------------------- 5. categorising
print()
print("the audience is a better signal than the type, where it exists")
check("a toddler storytime is kids", BC.categorise(["Story Time"], ["Toddlers: 18 to 36 months"], "learning")[0] == "kids")
check("...and a teen session is too", BC.categorise(["Computers and Technology"], ["Teens: 13 to 19 years"], "learning")[0] == "kids")
# The one that must NOT fire: an event for children AND adults is a community
# event children are welcome at, and filing it under kids takes it off the door
# where most of its audience is looking.
p, x = BC.categorise(["Crafts, Games and Play"], ["Toddlers: 18 to 36 months", "Adults: 18 and up"], "learning")
check("a mixed audience is NOT kids", p != "kids", (p, x))
check("no audience at all falls back to the type",
      BC.categorise(["Computers and Technology"], [], "learning")[0] == "learning")
check("an unrecognised type falls back to the config",
      BC.categorise(["Zzz Unknown"], [], "learning") == ("learning", []))
check("...and an empty type list does too",
      BC.categorise([], [], "community") == ("community", []))

print()
print("a type name that names two things reaches two doors")
# Chicago files chess night under "Crafts, Games and Play". Stopping at the
# first matching rule sent it to `arts` and nowhere else.
p, x = BC.categorise(["Crafts, Games and Play"], [], "learning")
check("crafts+games is arts AND party", p == "arts" and "party" in x, (p, x))
check("volunteering outranks community in the same name",
      BC.categorise(["Community & Volunteering"], [], "learning")[0] == "volunteer")
check("every emitted key is one the app can render",
      all(k in BC.norm_categories.__globals__["VALID_CATEGORIES"]
          for k in [p] + list(x)), (p, x))
check("extras never exceed what the DB stores", len(BC.categorise(
    ["Crafts, Games and Play", "Computers and Technology", "Story Time",
     "Community & Volunteering", "Health and Science"], [], "learning")[1]) <= 2)

# ------------------------------------- 5b. two sessions in a day are two events
print()
print("a programme run twice in one day is two events")
# The shared fingerprint is a CROSS-SOURCE key and is date-granular by design.
# Here that silently ate one of every pair: measured on one page of Vancouver,
# 9 rows of 197 merged into another and were never written.
a = BC.to_event(evt("s1", "Family Storytime", "2026-09-04T10:15", "2026-09-04T11:00"), ent(), SITE)
b = BC.to_event(evt("s2", "Family Storytime", "2026-09-04T11:15", "2026-09-04T12:00"), ent(), SITE)
check("same title, same day, same branch, different hour -> different rows",
      a.fingerprint != b.fingerprint)
same = BC.to_event(evt("s3", "Family Storytime", "2026-09-04T10:15", "2026-09-04T11:00"), ent(), SITE)
check("...but the SAME session read twice still merges", a.fingerprint == same.fingerprint)
check("the stored name is untouched by the key", a.name == "Family Storytime", a.name)
with tempfile.TemporaryDirectory() as d:
    st = BC.EventStore(os.path.join(d, "s.json"))
    st.upsert(a)
    st.upsert(b)
    check("...and the store keeps both", len(st.records) == 2, len(st.records))

# ------------------------------------------------------------- 6. identity
print()
print("identity and links")
check("source_id is namespaced by library, or two systems collide on one id",
      e.source_id == "testlib:e1", e.source_id)
check("the link goes to the library's own page",
      e.ticket_url == "https://testlib.bibliocommons.com/events/e1", e.ticket_url)
check("HTML is stripped from the description",
      e.description == "Bring a laptop.", e.description)

# --------------------------------------------- 7. ingest_site, against a stub
print()
print("ingest_site, and the filter the server accepts and ignores")
soon = (date.today() + timedelta(days=7)).isoformat()
past = (date.today() - timedelta(days=7)).isoformat()
far = (date.today() + timedelta(days=900)).isoformat()


class Resp:
    def __init__(self, body):
        self._b, self.status_code = body, 200

    def json(self):
        return self._b


class Sess:
    """One page holding a keeper, a past row, a far-future row and an orphan.

    `params` is IGNORED on purpose: that is precisely what the real gateway does
    with startDate/endDate, and an adapter that trusts them reads the archive.
    """
    headers = {}
    seen = []

    def get(self, url, params=None, timeout=None):
        Sess.seen.append(params)
        if (params or {}).get("page", 1) > 1:
            return Resp({"entities": {"events": {}}, "events": {"pagination": {"pages": 1}}})
        evs = {"k": evt("k", "Keeper", f"{soon}T10:00", f"{soon}T11:00"),
               "p": evt("p", "Last week", f"{past}T10:00", f"{past}T11:00"),
               "f": evt("f", "Two years out", f"{far}T10:00", f"{far}T11:00"),
               "o": evt("o", "Online only", f"{soon}T10:00", f"{soon}T11:00", branch=None)}
        return Resp({"entities": ent(events=evs), "events": {"pagination": {"pages": 1}}})


with tempfile.TemporaryDirectory() as d:
    store = BC.EventStore(os.path.join(d, "s.json"))
    n = BC.ingest_site(store, Sess(), dict(SITE, horizon_days=180, crawl_delay=0, max_pages=3))
    names = sorted(r["name"] for r in store.records.values())
check("only the row inside the horizon is kept", n == 1 and names == ["Keeper"], names)
check("the request asked for the biggest page the gateway honours",
      Sess.seen and Sess.seen[0].get("limit") == BC.PAGE_LIMIT, Sess.seen[:1])
check("...and did NOT pass startDate/endDate, which are accepted and ignored",
      all("startDate" not in (p or {}) for p in Sess.seen), Sess.seen)

# ------------------- 7c. a cancelled programme is a tombstone, keyed as stored
# The library cancels AFTER publishing (8 of 600 rows were isCancelled on the
# first pages of three systems, 2026-10-05), so refusing the row left the one we
# stored last week on the map. The tombstone must carry that row's key: name +
# HH:MM | day | branch | city. Proved by building both through ingest_site.
print()
print("a cancelled programme tombstones the row the live one was stored as")


class OneRow:
    headers = {}

    def __init__(self, row):
        self.row = row

    def get(self, url, params=None, timeout=None):
        if (params or {}).get("page", 1) > 1:
            return Resp({"entities": {"events": {}}, "events": {"pagination": {"pages": 1}}})
        return Resp({"entities": ent(events={"x": self.row}), "events": {"pagination": {"pages": 1}}})


def _run(row):
    with tempfile.TemporaryDirectory() as d:
        st = BC.EventStore(os.path.join(d, "s.json"))
        BC.ingest_site(st, OneRow(row), dict(SITE, horizon_days=180, crawl_delay=0, max_pages=2))
        return st


live = _run(evt("c1", "Story and Play Time", f"{soon}T11:00", f"{soon}T11:30"))
gone = _run(evt("c1", "CANCELLED - Story and Play Time", f"{soon}T11:00", f"{soon}T11:30",
                cancelled=True))
(live_fp, live_rec), = live.records.items()
check("the cancelled row is not stored live", gone.records == {}, list(gone.records))
check("its tombstone has the live row's fingerprint (the title inside the notice)",
      list(gone.tombstones) == [live_fp], (list(gone.tombstones), live_fp))
check("...and the same source and source_id",
      [(t["source"], t["source_id"]) for t in gone.tombstones.values()]
      == [(live_rec["sources"][0]["source"], live_rec["sources"][0]["source_id"])],
      gone.tombstones)
other = _run(evt("c1", "Story and Play Time", f"{soon}T14:00", f"{soon}T14:30", cancelled=True))
check("the 14:00 session's tombstone is not the 11:00 session's row",
      live_fp not in other.tombstones and len(other.tombstones) == 1, other.tombstones)
check("to_event alone still returns no live row for a cancelled programme",
      BC.to_event(evt(cancelled=True), ent(), SITE) is None
      and BC.to_event(evt(), ent(), SITE, tombstone=True) is None)


# --------------------------- 7a. locations are cached within one page
# Before location memoization, this 200-row page made 200 coordinate parses and
# 200 address parses. Both variants read a mixed branch/offsite page and a
# second page that reuses the IDs with changed location data. Compare the full
# stored records; a fixed timestamp makes the run deterministic. Runtime is a
# local measurement, not asserted because wall time is noisy.
print()
print("branch and offsite locations reused within each page")
future_day = (date.today() + timedelta(days=7)).isoformat()


def location_page(first, count, branch, offsite):
    events = {}
    for i in range(first, first + count):
        eid = str(i)
        branch_id = i % 2 == 0
        events[eid] = evt(eid, f"Program {i}", f"{future_day}T10:{i % 60:02d}",
                          f"{future_day}T11:00", branch="48" if branch_id else None,
                          nonbranch=None if branch_id else "p1")
    return {"entities": ent(events=events, locations={"48": branch}, places={"p1": offsite}),
            "events": {"pagination": {"pages": 2}}}


branch_page1, offsite_page1 = copy.deepcopy(BRANCH), copy.deepcopy(OFFSITE)
branch_page2, offsite_page2 = copy.deepcopy(BRANCH), copy.deepcopy(OFFSITE)
branch_page2["address"]["street"] = "Changed Branch Road"
branch_page2["mapLocation"]["centrePoint"] = {"lat": 40.7128, "lng": -74.006}
branch_page2["mapLocation"]["timeZone"] = "America/New_York"
offsite_page2["address"]["street"] = "Changed Offsite Avenue"
offsite_page2["mapLocation"]["centrePoint"] = {"lat": 34.0522, "lng": -118.2437}
offsite_page2["mapLocation"]["timeZone"] = "America/Los_Angeles"
location_pages = {1: location_page(0, 200, branch_page1, offsite_page1),
                  2: location_page(200, 4, branch_page2, offsite_page2)}
fixture_bytes = sum(len(json.dumps(body).encode("utf-8"))
                    for body in location_pages.values())


class ReusedBranch:
    headers = {}

    def __init__(self):
        self.calls = 0
        self.bytes = 0

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        page = (params or {}).get("page", 1)
        body = location_pages[page]
        self.bytes += len(json.dumps(body).encode("utf-8"))
        return Resp(body)


point, address = BC._point, BC._address
original_to_event = BC.to_event
original_iso_now = shared_ingest.iso_now

def run_reused_branch(disable_cache):
    counts = {"point": 0, "address": 0}
    session = ReusedBranch()

    def counted_point(where):
        counts["point"] += 1
        return point(where)

    def counted_address(where):
        counts["address"] += 1
        return address(where)

    def legacy_to_event(ev, entities, site, location_cache=None, **kw):
        return original_to_event(ev, entities, site, **kw)

    BC._point, BC._address = counted_point, counted_address
    shared_ingest.iso_now = lambda: "2026-10-01T12:00:00Z"
    if disable_cache:
        BC.to_event = legacy_to_event
    try:
        with tempfile.TemporaryDirectory() as d:
            store = BC.EventStore(os.path.join(d, "s.json"))
            started = time.perf_counter()
            n = BC.ingest_site(store, session, dict(SITE, horizon_days=180,
                                                   crawl_delay=0, max_pages=3))
            elapsed_ms = (time.perf_counter() - started) * 1000
            return n, len(store.records), dict(store.records), counts, session, elapsed_ms
    finally:
        BC._point, BC._address, BC.to_event = point, address, original_to_event
        shared_ingest.iso_now = original_iso_now


baseline = run_reused_branch(disable_cache=True)
optimized = run_reused_branch(disable_cache=False)
for label, result in (("before", baseline), ("after", optimized)):
    n, stored, records, counts, session, elapsed_ms = result
    print(f"{label}: {session.calls} GET, {session.bytes} response bytes, "
          f"{counts['point']} coordinate parses, {counts['address']} address parses, "
          f"{elapsed_ms:.2f} ms")
    check(f"{label} keeps all events from both pages", n == stored == 204, (n, stored))
check("the cache leaves fixture requests and bytes unchanged",
      baseline[4].calls == optimized[4].calls == 2 and
      baseline[4].bytes == optimized[4].bytes == fixture_bytes,
      (baseline[4].calls, baseline[4].bytes, optimized[4].calls, optimized[4].bytes))
check("location normalization runs once per entity per page",
      optimized[3] == {"point": 4, "address": 4}, optimized[3])
check("every emitted event record matches the uncached baseline",
      baseline[2] == optimized[2],
      (len(baseline[2]), len(optimized[2])))
check("the second page uses its changed branch and offsite locations",
      all((r["latitude"], r["longitude"], r["address"], r["timezone"]) in {
              (40.7128, -74.006, "6 Changed Branch Road", "America/New_York"),
              (34.0522, -118.2437, "506 Changed Offsite Avenue", "America/Los_Angeles")}
          for r in optimized[2].values() if r["name"] in {f"Program {i}" for i in range(200, 204)}),
      [r for r in optimized[2].values() if r["name"] in {f"Program {i}" for i in range(200, 204)}])

# ------------------------------ 7b. a 5xx page is re-read in smaller pieces
# The shape of Santa Clara County's page 2 on 2026-09-24: HTTP 500 at limit=200
# on every try, and the same rows answering 200 as four pages of 50. The loop
# used to stop there, which is how Boston Public Library kept 1,856 of ~4,000.
print()
print("a page the gateway cannot build at 200 rows")


class Status(Resp):
    def __init__(self, code):
        super().__init__({})
        self.status_code = code


class Split:
    """limit=200: page 1 answers 500 and page 2 is past the end. limit=50: each
    of pages 1-4 carries one future row, except the pages in `bad`, which 500
    as well. `first` replaces the page-1 answer, for the refusal case."""
    headers = {}

    def __init__(self, bad=(), first=500):
        self.bad, self.first, self.seen = set(bad), first, []

    def get(self, url, params=None, timeout=None):
        p = dict(params or {})
        self.seen.append(p)
        if p.get("limit") == BC.PAGE_LIMIT:
            if p.get("page") == 1:
                return Status(self.first)
            return Resp({"entities": {"events": {}}, "events": {"pagination": {"pages": 1}}})
        n = p.get("page")
        if n in self.bad:
            return Status(500)
        eid = f"s{n}"
        return Resp({"entities": ent(events={eid: evt(eid, f"Storytime {n}", f"{soon}T10:0{n}",
                                                      f"{soon}T11:00")}),
                     "events": {"pagination": {"count": 4, "pages": 1, "limit": BC.SPLIT_LIMIT}}})


def run_split(sess):
    with tempfile.TemporaryDirectory() as d:
        store = BC.EventStore(os.path.join(d, "s.json"))
        n = BC.ingest_site(store, sess, dict(SITE, horizon_days=180, crawl_delay=0, max_pages=5))
        return n, sorted(r["name"] for r in store.records.values())


n, names = run_split(Split())
check("every row of the failed page is kept, read as four pages of 50",
      n == 4 and names == ["Storytime 1", "Storytime 2", "Storytime 3", "Storytime 4"], names)
n, names = run_split(Split(bad={2}))
check("a small page that fails too costs its own rows and nothing else",
      n == 3 and "Storytime 2" not in names, names)
sess = Split(bad={1, 2, 3, 4})
n, names = run_split(sess)
check("when every piece fails the system stops, as it did before",
      n == 0 and not any(p.get("page", 0) > 1 and p.get("limit") == BC.PAGE_LIMIT
                         for p in sess.seen), sess.seen)
sess = Split(first=403)
n, names = run_split(sess)
check("a 403 is the library declining: stopped, and never asked again in pieces",
      n == 0 and all(p.get("limit") == BC.PAGE_LIMIT for p in sess.seen), sess.seen)

# ------------------------------------------------------- 8. the config itself
print()
print("the shipped config")
cfg = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "bibliocommons_sources.json"), encoding="utf-8"))
slugs = [s["slug"] for s in cfg["sites"]]
check("every site has a slug and a category",
      all(s.get("slug") and s.get("category") for s in cfg["sites"]))
check("slugs are unique - they namespace source_id", len(slugs) == len(set(slugs)), slugs)
check("every site carries a horizon, or a 2027 book group pins for ever",
      all(s.get("horizon_days") for s in cfg["sites"]))
declined = " ".join(k for k in cfg["_not_included"] if k.startswith("http"))
check("nothing configured is also declined",
      not any(f"/{s}/" in declined for s in slugs), slugs)
check("the declines are recorded with reasons, not just listed",
      all(isinstance(v, str) and len(v) > 20
          for k, v in cfg["_not_included"].items() if k.startswith("http")))

print()
print(f"{'FAILURES: ' + ', '.join(fails) if fails else 'all checks passed'}")
sys.exit(1 if fails else 0)
