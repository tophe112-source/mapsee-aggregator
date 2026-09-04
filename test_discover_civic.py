#!/usr/bin/env python3
"""
test_discover_civic.py - the rules the civic backend was WRONG about first.

Every case here is something a live sweep proposed and should not have, or
refused and should not have. Nothing is invented: the fixtures are the strings
those cities actually publish, kept verbatim (including the escaped comma in
"MRA Board Meeting - December 17\\, 2026", which is how iCalendar spells it and
is exactly the sort of thing a regex written from memory does not expect).

    python test_discover_civic.py
"""
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                             # noqa: BLE001
        pass

import io as _io

import catalog_discover_osm as osm
import catalog_discover_civic as civ

checks = []


def check(cond, label):
    checks.append((bool(cond), label))


CRLF = '\r\n'


def ics(*summaries, dtstart="20991231T090000"):
    """A minimal feed body: one DTSTART so it counts as upcoming, then names."""
    body = [f"DTSTART;TZID=America/Los_Angeles:{dtstart}"]
    body += [f"SUMMARY:{s}" for s in summaries]
    return "\r\n".join(body)


# ---------------------------------------------- 1. the category NAME test
# Free, so it runs first and refuses what it can before anything is fetched.
# The five it got wrong when it was written as a KEEP list are pinned here,
# because the temptation to swap it back is a real one and these are the cost.
for name in ("City Council", "Boards & Commissions", "Public Hearings",
             "City Hall Closures", "Planning Board", "Zoning Board Public Meeting",
             "Finance", "Tax", "Taxes", "City Clerk", "Civil Service",
             "Paving Schedule", "Warrant Resolution", "Docket Calendar",
             "Mosquito Control", "Police Academy Trainings",
             "Fire Training Facility", "Down Payment Assistance Program"):
    check(osm.CIVIC_DENY_RX.search(name), f"name test refuses {name!r}")

# THE FIVE A KEEP LIST THREW AWAY. A festival, two holidays, a venue and a
# whole-town calendar — none of them nameable in advance, which is the argument
# for a deny list in one line.
for name in ("4th of July", "Juneteenth", "Halloween", "Pickering Barn",
             "Community Events", "Concerts on the Green", "Farmers Market",
             "Library Calendar", "Main City Calendar", "Parks and Recreation",
             "Flag Raisings", "Nature Center", "Senior Center",
             "Volunteer Opportunities", "Environmental Sustainability"):
    check(not osm.CIVIC_DENY_RX.search(name), f"name test keeps {name!r}")

# The substring accident that made the keep list unusable, kept as a rule of its
# own: `sport` is inside `tran-sport-ation` and `art` is inside `depart-ment`.
check(osm.CIVIC_DENY_RX.search("Transportation"), "Transportation is refused...")
check(not osm.CIVIC_DENY_RX.search("Sports Complex"),
      "...but Sports Complex is not, and word boundaries are why")

# ---------------------------------------------- 2. the feed CONTENT test
# Everything below passed the name test and was proved to hold future events.
# All of it was proposed by a real sweep.
check(osm.governance_heavy(ics("City Offices Closed - Christmas",
                               "City Offices Closed - Thanksgiving",
                               "City Offices Closed - Labor Day")),
      "Woodbury's Holidays is a closure list")
check(osm.governance_heavy(ics("New Year's Day - City Offices will be Closed",
                               "Christmas Day - City Offices will be Closed")),
      "...and so is Missoula's, phrased the other way round")
check(osm.governance_heavy(ics("IDA Meeting", "IDA Meeting", "IDA Meeting")),
      "Mount Vernon's IDA Calendar is a meeting schedule")
check(osm.governance_heavy(ics(r"MRA Board Meeting - December 17\, 2026",
                               r"MRA Board Meeting - November 19\, 2026")),
      "...and the Redevelopment Agency's is too, escaped comma and all")
check(osm.governance_heavy(ics("Heart of Missoula Leadership Team Meeting")),
      "a one-entry neighborhood calendar is still a meeting schedule")
check(osm.governance_heavy(ics("Christmas Day", "Independence Day",
                               "New Year's Day", "Christmas Day")),
      "Mansfield's City Holidays is bare holiday names")
check(osm.governance_heavy(ics("Christmas", "Christmas Eve",
                               "Thanksgiving (observed)", "Veterans Day")),
      "...and (observed) does not smuggle one past")
# Blaine's is 95 entries of "Juneteenth Day Holiday" — the bare anchored name
# missed every one, which is why the wrapper words are optional on both sides.
check(osm.governance_heavy(ics("Juneteenth Day Holiday", "Juneteenth Day Holiday",
                               "Labor Day Holiday", "Holiday - Christmas")),
      "...nor does wrapping the day in the word Holiday")
check(not osm.governance_heavy(ics("Juneteenth Jubilee in the Park",
                                   "Christmas Tree Lighting",
                                   "Memorial Day Parade")),
      "and a town that PROGRAMMES its holidays keeps its calendar")

# THE ONES THAT MUST SURVIVE IT. A holiday with an event attached is an event,
# and this is the whole reason the holiday match is anchored rather than loose.
check(not osm.governance_heavy(ics("Down Home 4th of July Parade",
                                   "Fireworks at Lake Sammamish",
                                   "4th of July Pancake Breakfast")),
      "a real 4th of July programme is not a closure list")
check(not osm.governance_heavy(ics("Halloween Spooktacular at Pickering Barn",
                                   "Trunk or Treat", "Haunted Trail")),
      "...nor is a Halloween programme")
check(not osm.governance_heavy(ics("Youth Climate Action Fund Office Hours",
                                   "Community Preparedness Fair",
                                   "Youth Climate Action Fund Office Hours")),
      "Redmond's Environmental Sustainability survives")
check(not osm.governance_heavy(ics("Volunteer Event at Heron Rookery Park",
                                   "Volunteer Event at Heron Rookery Park")),
      "...and so does its Volunteer Opportunities")
# The threshold exists for exactly this: a parks calendar carrying one board
# meeting is a parks calendar.
check(not osm.governance_heavy(ics("Summer Concert", "Farmers Market",
                                   "Parks Board Meeting", "Movie in the Park")),
      "one board meeting among four events does not condemn the calendar")
check(not osm.governance_heavy(""), "an empty body is not 'governance'")

# ---------------------------------------------- 3. upcoming, not merely present
# 30 of two cities' 47 categories are valid iCalendar holding nothing at all.
check(osm.future_vevents("DTSTART:20991231\nDTSTART:20010101") == 1,
      "future_vevents counts only what is still to come")
check(osm.future_vevents("BEGIN:VEVENT\nEND:VEVENT") == 0,
      "...and a VEVENT with no DTSTART counts for nothing")
check(osm.future_vevents("DTSTART;TZID=America/Chicago:20991231T090000") == 1,
      "...through a TZID parameter, which is how CivicPlus writes them")

# ---------------------------------------------- 3b. verifying is not ingesting
# A feed can hold future events and still put none of them on a map, because the
# ics adapter drops an event it cannot place. This tests PRESENCE of a location.
def vevent(dtstart="20991231", **fields):
    lines = ["BEGIN:VEVENT", f"DTSTART;TZID=America/Chicago:{dtstart}T090000"]
    lines += [f"{k.upper()}:{v}" for k, v in fields.items()]
    return CRLF.join(lines + ["END:VEVENT"])


check(osm.placeable_share(vevent(LOCATION="1 Main St") * 1
                          + vevent(LOCATION="2 Main St")) == (2, 2),
      "a feed with a LOCATION on every event is fully placeable")
check(osm.placeable_share(vevent(SUMMARY="x") + vevent(SUMMARY="y")) == (0, 2),
      "a feed that says nothing about where is refused")
check(osm.placeable_share(vevent(dtstart="20010101", LOCATION="x")) == (0, 0),
      "a feed of past events is neither placeable nor upcoming")
check(osm.placeable_share(vevent(LOCATION="")) == (0, 1),
      "an EMPTY location line does not count as a place")
check(osm.placeable_share(vevent(GEO="47.5;-122.0")) == (1, 1),
      "...and a GEO line does, which is the other way a feed says where")
# Counted per VEVENT, not per line: a single wrapped LOCATION must not vouch for
# nine events that have none.
check(osm.placeable_share(vevent(LOCATION="1 Main St") + vevent() * 3) == (1, 4),
      "one placeable event among four does not make the feed placeable")

# ------------------------------- 3c. the bug those four feeds ACTUALLY had
# Found by this backend, fixed in the ics adapter, pinned here because this is
# where the evidence is. CivicPlus writes every LOCATION as
# "Venue Name - 123 Street  City ST ZIP", and Photon answers None for the whole
# string while geocoding either half:
#     Elmer W. Oliver Nature Park - 1650 Matlock Road  Mansfield TX 76063 -> None
#     1650 Matlock Road, Mansfield TX 76063               -> 32.6057, -97.1148
#     Elmer W. Oliver Nature Park, Mansfield TX           -> 32.5859, -97.1025
# Before the split those four feeds ingested 0/34, 0/6, 0/3 and 1/16; after it,
# all fourteen sources in that batch ingest 100% and the batch went 194 -> 328.
import mapsee_ingest_ics as icsad

att = icsad.location_attempts(
    "Elmer W. Oliver Nature Park - 1650 Matlock Road  Mansfield TX 76063")
check(att[0].startswith("Elmer"), "the whole string is still tried first")
check(att[1] == "1650 Matlock Road  Mansfield TX 76063",
      "...then the ADDRESS half, which is the more precise answer")
check(att[2] == "Elmer W. Oliver Nature Park",
      "...then the venue, which for a park is often the only thing with a point")
check(len(att) == 3, "never more than three — every attempt is a second of Photon")
# Some sites write it the other way round, so the halves are told apart by which
# one starts with a house number rather than by position.
back = icsad.location_attempts("1650 Matlock Road - Oliver Nature Park")
check(back[1] == "1650 Matlock Road" and back[2] == "Oliver Nature Park",
      "the address half is found by its house number, not by its position")
check(icsad.location_attempts("Pickering Barn") == ["Pickering Barn"],
      "a location with no dash is one attempt, not three")
check(icsad.location_attempts("") == [] and icsad.location_attempts(None) == [],
      "...and an empty one is none")
# \s+-\s+ needs whitespace on BOTH sides, so a trailing dash never splits and
# the location is tried exactly as its publisher wrote it — one attempt, not two
# with an empty half.
# AND THE ANSWER IS CACHED AGAINST WHAT THE FEED SAID. Only hits are cached, so
# caching under the attempt that worked leaves the FAILED first attempt to be
# paid again every run — one wasted 1.1s Photon call per event per day, on an
# adapter where every CivicPlus location needs the split. Live consequence: the
# feeds job's ICS step went 2h08 -> 3h06 in a day and the job was cancelled on
# its 240-minute cap with The Events Calendar still to run, which is why Visit
# Issaquah did not appear.
def _fake_geocoder(hit_prefix):
    calls = []

    class _Sess:
        def get(self, url, params=None, timeout=None):
            calls.append(params["q"])

            class _R:
                status_code = 200

                def raise_for_status(self):
                    pass

                def json(self):
                    ok = params["q"].startswith(hit_prefix)
                    return {"features": [{"geometry": {"coordinates": [-97.11, 32.60]}}]
                            if ok else []}
            return _R()

    icsad._GEO_CACHE.clear()
    icsad.time.sleep = lambda *a, **k: None
    icsad.geocode_allowed = lambda: True
    return icsad.make_location_geocoder(_Sess(), ", Mansfield, TX"), calls


_g, _calls = _fake_geocoder("1650 Matlock Road")
_loc = "Elmer W. Oliver Nature Park - 1650 Matlock Road  Mansfield TX 76063"
check(_g(_loc) == (32.60, -97.11), "the split finds the address half")
check(len(_calls) == 2, "...at a cost of two lookups the first time")
_before = len(_calls)
check(_g(_loc) == (32.60, -97.11) and len(_calls) == _before,
      "...and none at all the second, because the FEED's string is the cache key")

check(icsad.location_attempts("Somewhere -  ") == ["Somewhere -"],
      "a dash with nothing after it does not split into an empty attempt")
# Two separators split once and leave the second dash leading the remainder.
check(icsad.location_attempts("City Hall -  - 1200 E. Broad St. Mansfield TX")[1]
      == "1200 E. Broad St. Mansfield TX",
      "...and a second dash is not carried into the query")

# ------------------- 3d. a city calendar is TWO calendars sharing a feed
# governance_heavy condemns a feed that is NOTHING but meetings, at two thirds.
# That leaves the MIXED ones, and they were measured live: of 1,150 events
# across eight civic towns, 64 were town-hall business — Baldwin Park's Main
# Calendar alone carried 45 of them onto the map beside "Art in the Park" and
# "Movies under the Stars".
#
# IT CANNOT BE THE SAME VOCABULARY AS THE FEED TEST, and that is the whole
# difficulty. The feed rule leans on a bare `board`, which per event deletes
# three real live events found in the same sample. So this is PHRASES: a board
# that meets is named as one.
def refused(t):
    return bool(osm.CIVIC_TITLE_RX.search(t) or osm.CIVIC_HOLIDAY_RX.match(t.strip()))


for t in ("City Council Regular Meeting", "Planning Commission Meeting",
          "Stakeholders Oversight Committee (SOC) Meeting",
          "Recreation and Community Services Commission",
          "Parks and Recreation Board", "Library Advisory Board",
          "Historic Preservation Commission", "Zoning Board of Appeals",
          "Public Hearing: Budget", "No Street Sweeping | Labor Day",
          "Labor Day: City Offices Closed", "City offices closed for Labor Day",
          "Christmas Day", "New Year's Day", "Independence Day"):
    check(refused(t), f"per-event: refuses {t!r}")

# THE THREE THAT PROVE THE PHRASES ARE NECESSARY. All live, all real, all
# deleted by a bare `board`.
for t in ("board game girlies! [20s&30s] - [Eastside Saturday]",
          "Board games and pizza at Zaucer Pizza in Redmond",
          "Board Games @ Servaes Brewing Co.",
          "Art in the Park", "El Grito de Dolores", "Movies under the Stars",
          "Kids Club", "Christmas Tree Lighting", "Independence Day Parade",
          "Fire Station Open House", "Police Department Coffee with a Cop",
          "Recreation Center Open House", "Summer Recreation Program",
          "Water Safety Class", "Library Story Time"):
    check(not refused(t), f"per-event: keeps {t!r}")

# ...and it is applied ONLY to sources discovery proposed as civic, because
# these phrases are about a town hall and nothing else in ics_sources.json is.
_ics_src = _io.open("mapsee_ingest_ics.py", encoding="utf-8").read()
check('is_civic = str(src.get("_found", "")).startswith("civic:")' in _ics_src,
      "the town-hall filter is gated on a civic source")
check("if is_civic and (CIVIC_TITLE_RX.search(title)" in _ics_src,
      "...and nothing else in the config is touched by it")
check("town-hall row(s) refused" in _ics_src,
      "...and what it refused is counted on the source's own line")

# ---------------------------------------------- 4. a town is not a venue
# The refusal the header is about: a city-wide calendar has no single point, and
# a `venue` block on one would pin every unaddressed event on the town hall.
place = {"name": "Issaquah", "city": "Issaquah", "region": "Washington",
         "country": "US", "kind": "city", "qid": "Q1", "population": 40051}
tribe = civ.to_candidate(place, {"adapter": "tribe", "labels": ["tribe"],
                                 "cal_url": "https://visitissaquahwa.com/calendar/"})
check(tribe and "venue" not in tribe, "a tribe candidate carries no venue block")
check(tribe and tribe["default_city"] == "Issaquah"
      and tribe["default_region"] == "Washington",
      "...it carries city-wide defaults instead")
check(tribe and tribe["base_url"] == "https://visitissaquahwa.com",
      "...and the base_url is the origin, not the calendar path")
check(tribe and tribe["within_days"] == 365,
      "a town calendar looks a year out, not the venue backend's 120")

ics_cand = civ.to_candidate(place, {"adapter": "ics", "labels": ["civicplus"],
                                    "cal_url": "https://issaquahwa.gov/calendar.aspx",
                                    "ics": "https://issaquahwa.gov/x.ics"})
check(ics_cand and ics_cand["geocode_suffix"] == ", Issaquah, Washington",
      "an ics candidate carries the suffix that is the only thing placing it")
check(civ.to_candidate(place, {"adapter": "ics", "labels": ["civicplus"],
                               "cal_url": "https://x/", "ics": None}) is None,
      "...and a platform that never served a feed proposes nothing")

# An adapter with no way to say "everything here is in this town" is NAMED, not
# quietly shaped into a venue it is not.
for adapter in ("jsonld", "squarespace", "localist", "gancio"):
    f = {"adapter": adapter, "labels": [adapter], "cal_url": "https://x/events"}
    check(civ.to_candidate(place, f) is None, f"{adapter} proposes nothing...")
    check(civ.why_no_candidate(f) == f"no-citywide-shape({adapter})",
          f"...and says why: no-citywide-shape({adapter})")

# ---------------------------------------------- 5. the DMO nomination
# Both halves required. Either alone is noise, and the two failures are
# different: a tourism word alone reaches the STATE board, the city's name alone
# reaches every department the city runs.
check(civ._looks_like_dmo("visitissaquahwa.com", "Issaquah"),
      "visitissaquahwa.com is nominated for Issaquah")
check(civ._looks_like_dmo("exploreasheville.com", "Asheville"),
      "...as is exploreasheville.com for Asheville")
check(civ._looks_like_dmo("neworleanscvb.com", "New Orleans"),
      "...and a two-word city closes up: neworleanscvb.com")
check(not civ._looks_like_dmo("visitflorida.com", "Kissimmee"),
      "the STATE board is not this town's calendar")
check(not civ._looks_like_dmo("issaquahwa.gov", "Issaquah"),
      "the city's own domain is not a tourism board")
check(not civ._looks_like_dmo("tripadvisor.com", "Asheville"),
      "...nor is tripadvisor, whatever words are in it")
check(not civ._looks_like_dmo("visitwherever.com", "Ely"),
      "a three-letter town is refused outright — it would match half the web")

# ---------------------------------------------- 6. Wikidata's coordinate order
# Point(lon lat). Reading it left to right puts Issaquah in the Southern Ocean.
lat, lon = civ._coord("Point(-122.043333333 47.535555555)")
check(abs(lat - 47.5355) < 0.01 and abs(lon + 122.0433) < 0.01,
      "Point(lon lat) is read lat-first, which is the reverse of how it is written")
check(civ._coord("") == (None, None), "...and an absent point is not a zero one")

# ---------------------------------------------- 7. the cursor counts ROWS
# Wikidata's OPTIONALs multiply rows: San Antonio comes back three times. LIMIT
# and OFFSET are over rows, so a cursor advanced by deduplicated CITIES
# under-advances and re-reads the tail of its own last batch for ever.
import inspect
src = inspect.getsource(civ.cities)
check("rows += 1" in src and "return list(out.values()), rows" in src,
      "cities() counts the rows it read, not the cities it kept")
curate = _io.open("catalog_curate.py", encoding="utf-8").read()
check("places, rows = civic.cities(" in curate,
      "...and the driver takes both numbers")
check('cursor["offset"] = offset + (rows if read >= len(places) else read)' in curate,
      "...and advances the cursor by rows only when the batch was read whole")

# ---------------------------------------------- report
bad = [l for ok, l in checks if not ok]
for ok, l in checks:
    print(("ok    " if ok else "FAIL  ") + l)
print(f"\n{len(checks)} cases, {len(bad)} failed")
sys.exit(1 if bad else 0)
