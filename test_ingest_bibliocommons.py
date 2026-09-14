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
import json
import os
import sys
import tempfile
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mapsee_ingest_bibliocommons as BC

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
