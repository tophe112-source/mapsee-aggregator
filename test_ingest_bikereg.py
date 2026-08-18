"""Does the BikeReg adapter read the right DATE, keep every occurrence, and
refuse the listings that are not events?

Four traps, and three of them produce a well-formed, plausible, WRONG row rather
than an error - the failure this repo cares most about:

  THE OFFSET ON THE DATE IS THE SERVER'S, NOT THE EVENT'S. `startDate` arrives
  as "2026-08-16T00:00:00.000-04:00" on every row - the time is 00:00:00 on all
  1,367, and the offset is only ever -04:00 or -05:00 while the events' own
  `eventTimeZone` spans Eastern through Hawaiian. Patricio's Kua Bay TTT in
  Kailua-Kona is stamped -04:00; parse that as an instant and the race lands at
  18:00 on 2026-08-15, a day before it happens, in a row that validates,
  geocodes and syncs without complaint. Only the date is true.

  ONE eventId IS NOT ONE EVENT. 39 of 1,246 ids come back once per OCCURRENCE -
  id 75112 on three separate dates. EventStore keys on (source, source_id) and
  POPS the old record when the fingerprint moves, so a bare eventId makes each
  occurrence delete the one before it: 1,269 in, 1,148 out, and the 121 missing
  are the earlier dates of every series. Same shape as the Localist duplicate
  bug in CLAUDE.md, running backwards.

  A MEMBERSHIP IS NOT AN EVENT. Club dues, coaching passes and volunteer rosters
  are sold through the same calendar, on a placeholder date, at the promoter's
  address. 112 of 1,367. Ingesting one puts a pin where nothing is happening.
  But a listing typed 'Club Membership' AND 'Mountain Bike' is a real race with
  a membership attached, and dropping it loses a genuine event.

  THE COORDINATES ARE THE FACT AND MUST SURVIVE THE SYNC. The feed carries no
  street address, so re-geocoding city+state would be strictly worse than the
  point BikeReg already holds. coords_exact is what opts out of that pass.

Pure functions and literal payloads: no network, no store, no database.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mapsee_ingest_bikereg as BR

fails = []


def check(label, cond, detail=""):
    print(f"{'ok  ' if cond else 'FAIL'} {label}{'' if cond else '   ' + str(detail)}")
    if not cond:
        fails.append(label)


CFG = dict(BR.DEFAULTS)


def node(**over):
    """One calendar node, shaped exactly as the gateway returns it."""
    ae = {
        "staticUrl": "https://www.bikereg.com/70726",
        "zip": "80424",
        "country": "USA",
        "eventTimeZone": "Mountain Standard Time",
        "eventTypes": ["Mountain Bike"],
        "coverPhoto": None,
        "registrationCount": {"count": 42},
    }
    ae.update(over.pop("athleticEvent", {}))
    n = {
        "eventId": 70726,
        "name": "THE BRECK EPIC MTB STAGE RACE",
        "city": "Breckenridge",
        "state": "CO",
        "startDate": "2026-08-16T00:00:00.000-04:00",
        "endDate": "2026-08-16T00:00:00.000-04:00",
        "latitude": 39.4734168,
        "longitude": -106.0509224,
        "distanceString": "230 miles",
        "athleticEvent": ae,
    }
    n.update(over)
    return n


# --------------------------------------------------------------------------- #
# the date, and the offset that contradicts it
# --------------------------------------------------------------------------- #
# The real Hawaii row: eventTimeZone says Hawaiian, startDate says -04:00.
hawaii = node(name="Patricio's Kua Bay TTT - 2026", city="Kailua Kona", state="HI",
              latitude=19.9134871446212, longitude=-155.99,
              startDate="2026-08-16T00:00:00.000-04:00",
              endDate="2026-08-16T00:00:00.000-04:00",
              athleticEvent={"eventTimeZone": "Hawaiian", "eventTypes": ["Time Trial"]})
ev = BR.to_event(hawaii, CFG)
check("a Hawaii event keeps the date the feed names, not the one its offset implies",
      ev is not None and ev.start_local == "2026-08-16", ev and ev.start_local)
check("the date is date-only, so the sync treats it as all-day and does not shift it",
      ev is not None and len(ev.start_local) == 10, ev and ev.start_local)

check("the offset is never read as an instant",
      BR._local_date("2026-08-16T00:00:00.000-04:00") == "2026-08-16")
check("a malformed date is refused rather than guessed",
      BR._local_date("not-a-date") is None and BR._local_date("") is None and BR._local_date(None) is None)

# endDate == startDate is 1,150 of 1,367 rows; a same-day end says nothing.
ev = BR.to_event(node(), CFG)
check("a single-day listing gets no end date", ev is not None and ev.end_local is None,
      ev and ev.end_local)
ev = BR.to_event(node(endDate="2026-08-20T00:00:00.000-04:00"), CFG)
check("a genuinely multi-day listing keeps its end date",
      ev is not None and ev.end_local == "2026-08-20", ev and ev.end_local)

# --------------------------------------------------------------------------- #
# identity: one id, many occurrences
# --------------------------------------------------------------------------- #
a = BR.to_event(node(eventId=75112, name="Valley Preferred Cycling Center - Try the Track",
                     startDate="2026-08-16T00:00:00.000-04:00"), CFG)
b = BR.to_event(node(eventId=75112, name="Valley Preferred Cycling Center - Try the Track",
                     startDate="2026-08-22T00:00:00.000-04:00"), CFG)
check("two occurrences of one eventId are two different events in the store",
      a is not None and b is not None and a.source_id != b.source_id,
      (a and a.source_id, b and b.source_id))
check("the occurrence date is what makes them different",
      a is not None and a.source_id.endswith(":2026-08-16") and b.source_id.endswith(":2026-08-22"),
      (a and a.source_id, b and b.source_id))
check("their fingerprints differ too, so neither can pop the other",
      a is not None and b is not None and a.fingerprint != b.fingerprint)

# --------------------------------------------------------------------------- #
# what is not an event
# --------------------------------------------------------------------------- #
for label in ("2026 USA Cycling Commissaires NASO Membership",
              "North Country Adventure Team Volunteer Signup",
              "Valley Preferred Cycling Center - Training Passes"):
    ev = BR.to_event(node(name=label, athleticEvent={"eventTypes": ["Club Membership"]}), CFG)
    check(f"a membership listing is not an event ({label[:34]})", ev is None, ev)

ev = BR.to_event(node(name="CCAP Fall School MTB Clubs",
                      athleticEvent={"eventTypes": ["Mountain Bike", "Club Membership", "NEBRA"]}), CFG)
check("a membership tag on a REAL race does not drop the race",
      ev is not None and ev.category == "fitness", ev)

# --------------------------------------------------------------------------- #
# classification
# --------------------------------------------------------------------------- #
ev = BR.to_event(node(athleticEvent={"eventTypes": ["Criterium"]}), CFG)
check("racing carries the sports secondary",
      ev is not None and ev.category == "fitness" and "sports" in ev.categories,
      ev and (ev.category, ev.categories))
ev = BR.to_event(node(athleticEvent={"eventTypes": ["Gran Fondo"]}), CFG)
check("a ride is outdoors, not sport",
      ev is not None and "sports" not in ev.categories and "outdoors" in ev.categories,
      ev and ev.categories)
check("secondaries never exceed what the column accepts",
      all(len((BR.to_event(node(athleticEvent={"eventTypes": t}), CFG) or BR).categories) <= 2
          for t in (["Criterium"], ["Gran Fondo"], ["Track", "Road Race", "Gravel"])))

# Sanctioning bodies are tagged into the same list and are not kinds of event.
ev = BR.to_event(node(athleticEvent={"eventTypes": ["NEBRA", "Utah Cycling Association"]}), CFG)
check("a sanctioning-body-only listing still lands somewhere honest",
      ev is not None and ev.category == "fitness", ev and ev.category)
check("an unknown type does not silently become a membership",
      BR.to_event(node(athleticEvent={"eventTypes": ["Some New Discipline"]}), CFG) is not None)

# --------------------------------------------------------------------------- #
# placement
# --------------------------------------------------------------------------- #
ev = BR.to_event(node(), CFG)
check("the feed's coordinates are marked exact so the sync does not geocode over them",
      ev is not None and ev.coords_exact is True, ev and ev.coords_exact)
check("no street address is invented to geocode with",
      ev is not None and not ev.address, ev and ev.address)
check("ISO-3 country is normalised to the ISO-2 the rest of the pipeline speaks",
      ev is not None and ev.country == "US", ev and ev.country)
check("a listing with no coordinates is refused, not placed by city name",
      BR.to_event(node(latitude=None, longitude=None), CFG) is None)
check("0,0 is the Atlantic, not a location",
      BR.to_event(node(latitude=0, longitude=0), CFG) is None)

# --------------------------------------------------------------------------- #
# the engagement floor
# --------------------------------------------------------------------------- #
strict = dict(CFG, min_registrations=100)
check("min_registrations drops a listing below the floor",
      BR.to_event(node(athleticEvent={"registrationCount": {"count": 12}}), strict) is None)
check("min_registrations keeps one above it",
      BR.to_event(node(athleticEvent={"registrationCount": {"count": 900}}), strict) is not None)
check("a missing count is not read as zero-and-drop when the floor is 0",
      BR.to_event(node(athleticEvent={"registrationCount": None}), CFG) is not None)
check("the registration count is never stored on the event",
      "registration" not in json.dumps(BR.to_event(node(), CFG).__dict__, default=str).lower())

# --------------------------------------------------------------------------- #
# the config the adapter actually ships with
# --------------------------------------------------------------------------- #
here = os.path.dirname(os.path.abspath(__file__))
cfg = json.load(open(os.path.join(here, "bikereg_sources.json"), encoding="utf-8"))
check("the shipped config still points at the GraphQL gateway, not the capped REST search",
      "outsideapi.com/fed-gw/graphql" in cfg.get("_endpoint", "")
      and "api/search" not in BR.API_URL, BR.API_URL)
check("the shipped config keeps every event by default while the layer is empty",
      int(cfg.get("min_registrations", 0)) == 0, cfg.get("min_registrations"))
check("RUNREG stays off, so this cannot re-ingest RunSignup under a second key",
      cfg.get("app_types") == ["BIKEREG"], cfg.get("app_types"))

# The query must keep asking for the concrete type: these four fields live on
# AthleticEvent, not on the IAthleticEventDisplay interface the node exposes,
# and asking for them without the fragment is a GRAPHQL_VALIDATION_FAILED that
# takes the whole page down with it.
check("the query still reaches through the interface to the concrete type",
      "... on AthleticEvent" in BR.QUERY
      and all(f in BR.QUERY for f in ("staticUrl", "eventTypes", "registrationCount", "zip")))

print()
print(f"{'FAILURES: ' + ', '.join(fails) if fails else 'all checks passed'}")
sys.exit(1 if fails else 0)
