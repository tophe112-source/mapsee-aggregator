#!/usr/bin/env python3
"""
test_ingest_jsonld.py — the generic schema.org Event importer, against JSON-LD
taken verbatim from a live WP Event Manager page (The Royal Room, Seattle).

Prints one line per case and exits non-zero on failure, like the other 16.

The generic adapter had read this family of sites correctly by ACCIDENT, and
accident is not a property you can keep. What is pinned here is what was
measured on all 95 of that site's event pages and what would have gone wrong:

  * SCHEMA.ORG SAYS `location`; WP EVENT MANAGER WRITES `Location`. Every one of
    the 95 pages capitalises it, along with `Organizer`. `.get("location")`
    returned None, the config's venue block filled the gap, and the pin was
    right — for exactly as long as the site went on saying nothing. Read the key
    case-insensitively and the placeholder rule below decides what it was worth.
  * A PLACEHOLDER IS NOT AN ADDRESS. All 95 carry {"name": "-", "address": "-"},
    which is what the CMS renders for a location nobody filled in. "-" is
    TRUTHY, so once the key is read it sails through `if not parts.get(k)`, the
    venue block never fills the gap, and "-" reaches the geocoder as a street.
    Same rule as the Squarespace default pin: a location with no address TEXT is
    not a location, and the answer is the config's venue, not a coordinate
    blocklist.
  * THE VENUE BLOCK FILLS, IT DOES NOT OVERRIDE. A site that grows a real
    address later must be believed over the config.
  * startDate IS NAIVE LOCAL TIME. "2026-08-19 19:30:00", no offset, on every
    page — and a space separator, not a `T`. It is only correct once the venue's
    coordinates give the sync a timezone; read as UTC a 7:30pm show is served at
    12:30pm. The round trip is asserted here because the two halves live in
    different files and neither alone can be wrong in a visible way.
  * A VENUE CALENDAR CARRIES THINGS THAT ARE NOT EVENTS. "CLOSED FOR
    MAINTENANCE" and "Closed for Private Event" are event_listing posts with
    real dates, because a notice is the only thing that CMS can put on a
    calendar. 5 of the 95. Nothing downstream can tell them from a gig, so they
    would reach the map as music and tell somebody a shut venue is open.
    skip_title is matched on the NAME, anchored: the other tell, a 00:00 start,
    would also throw away a New Year's Eve show.

The live block below keeps the date it was captured with, because its SHAPE is
what is being pinned — but every call that runs it through to_event rolls that
date forward first. to_event drops anything before today, so a fixture dated the
afternoon it was written passes that afternoon and is wrong every day after:
test_osm_food.py went red every Saturday for the same reason. The two DST
assertions keep their literal dates deliberately, because _to_utc_if_naive has
no such filter and a timezone conversion is the one thing that must give the
same answer for ever.
"""
import re
import sys
from datetime import datetime, timedelta, timezone

from mapsee_ingest_jsonld import (
    _ld_get, _meaningful, _address_parts, _parse_ld, _is_event, to_event,
)
from mapsee_supabase_sync import _to_utc_if_naive

FAILURES = []


def check(label, got, want):
    ok = got == want
    print(("  ok   " if ok else "  FAIL ") + label + ("" if ok else f"\n         got {got!r}\n        want {want!r}"))
    if not ok:
        FAILURES.append(label)


def check_true(label, got):
    check(label, bool(got), True)


# --- fixtures: verbatim from https://theroyalroomseattle.com/event/... -------
# Trimmed only in `description`; every key, its spelling and its case are as the
# live pages emit them.
LIVE_LD = '''
{"@context":"http:\\/\\/schema.org\\/","@type":"Event","description":"<p><strong>Doors:<\\/strong> 6:30pm<\\/p>",
"name":"Trio Reunion","image":"https:\\/\\/theroyalroomseattle.com\\/wp-content\\/uploads\\/2026\\/05\\/trio.jpg",
"startDate":"2026-08-19 19:30:00","endDate":"","performer":"",
"eventAttendanceMode":"OfflineEventAttendanceMode","eventStatus":"EventScheduled",
"Organizer":{"@type":"Organization","name":""},
"Location":{"@type":"Place","name":"-","address":"-"}}
'''

# The config entry for the site, as jsonld_sources.json holds it.
VENUE = {
    "name": "The Royal Room",
    "address": "5000 Rainier Ave S",
    "city": "Seattle",
    "region": "WA",
    "postal_code": "98118",
    "country": "US",
    "lat": 47.5569023,
    "lon": -122.2842722,
}
SKIP_RX = re.compile(r"^\s*closed\b", re.I)

# Always comfortably in the future, whenever this is run.
_D = datetime.now(timezone.utc) + timedelta(days=180)
SOON = _D.strftime("%Y-%m-%d 19:30:00")
SOON_MIDNIGHT = _D.strftime("%Y-%m-%d 00:00:00")
SOON_EVENING = _D.strftime("%Y-%m-%d 20:00:00")


def no_geocode(*a, **k):
    raise AssertionError("the venue block should have placed this without a lookup")


def main():
    item = _parse_ld(LIVE_LD)
    check_true("the live JSON-LD block parses", item is not None)
    check_true("it is recognised as an Event", _is_event(item))

    print()
    print("the key is `Location`, not `location` — read it, do not miss it")
    check("_ld_get finds the capitalised key",
          (_ld_get(item, "location") or {}).get("name"), "-")
    check("an exact lowercase key still wins", _ld_get({"a": 1, "A": 2}, "a"), 1)
    check("a genuinely absent key is still None", _ld_get(item, "offers"), None)

    print()
    print("a placeholder is not a location")
    for junk in ("-", "--", "n/a", "N/A", "TBA", "tbd", "None", " - ", "-."):
        check(f"  {junk!r} carries no information", _meaningful(junk), None)
    check("'NA' is Namibia, not 'not applicable' — the country survives",
          _address_parts({"address": {"streetAddress": "12 Nelson Mandela Ave",
                                      "addressLocality": "Windhoek",
                                      "addressCountry": "NA"}})["country"], "NA")
    check("a real street survives untouched",
          _meaningful("5000 Rainier Ave S"), "5000 Rainier Ave S")
    check("a street that merely starts with a dash survives",
          _meaningful("-5000 Rainier"), "-5000 Rainier")
    check("the live Location yields no address at all",
          _address_parts(_ld_get(item, "location")),
          {"address": None, "city": None, "region": None,
           "postal_code": None, "country": None})

    print()
    print("so the venue block is the only thing that places these events")
    ev = to_event(dict(item, startDate=SOON),
                  "https://theroyalroomseattle.com/event/trio-reunion/",
                  "music", no_geocode, VENUE, SKIP_RX)
    check_true("the event is kept", ev is not None)
    check("venue name comes from the config, not from '-'", ev.venue_name, "The Royal Room")
    check("street comes from the config", ev.address, "5000 Rainier Ave S")
    check("city comes from the config", ev.city, "Seattle")
    check("region comes from the config", ev.region, "WA")
    check("no field anywhere is left as the placeholder",
          [k for k, v in vars(ev).items() if v == "-"], [])
    check("it is pinned at the venue", (ev.latitude, ev.longitude),
          (VENUE["lat"], VENUE["lon"]))

    print()
    print("the venue block FILLS a gap; it never overrides what the page says")
    real = dict(item, startDate=SOON,
                Location={"@type": "Place", "name": "Chop Suey",
                          "address": "1325 E Madison St, Seattle, WA 98122"})
    ev2 = to_event(real, "u", "music", no_geocode, VENUE, SKIP_RX)
    check("a real venue name is believed over the config", ev2.venue_name, "Chop Suey")
    check("a real street is believed over the config", ev2.address, "1325 E Madison St")
    check("a real postcode is believed over the config", ev2.postal_code, "98122")

    print()
    print("startDate is naive local time — only the venue's coords make it an instant")
    check("the adapter passes the naive stamp through verbatim, space and all",
          ev.start_local, SOON)
    check("the block as captured carries no offset and no 'T'",
          item["startDate"], "2026-08-19 19:30:00")
    check("no end time is invented when the site ships none", ev.end_local, None)
    check("the sync turns 7:30pm on an August night in Seattle into the right instant",
          _to_utc_if_naive("2026-08-19 19:30:00", ev.latitude, ev.longitude),
          "2026-08-20T02:30:00Z")
    check("a winter date uses PST, not a fixed offset",
          _to_utc_if_naive("2026-12-10 19:30:00", VENUE["lat"], VENUE["lon"]),
          "2026-12-11T03:30:00Z")
    check_true("read without coordinates it would be 7 hours wrong — that is the bug",
               _to_utc_if_naive("2026-08-19 19:30:00", None, None) != "2026-08-20T02:30:00Z")

    print()
    print("a closure notice is not an event")
    for shut in ("CLOSED FOR MAINTENANCE", "Closed for Private Event", "closed today"):
        notice = dict(item, name=shut, startDate=SOON_MIDNIGHT)
        check(f"  {shut!r} is refused",
              to_event(notice, "u", "music", no_geocode, VENUE, SKIP_RX), None)
    for gig in ("Trio Reunion", "The Doors Closed Tribute", "Closing Night Gala"):
        keep = dict(item, name=gig, startDate=SOON_EVENING)
        check_true(f"  {gig!r} is kept",
                   to_event(keep, "u", "music", no_geocode, VENUE, SKIP_RX) is not None)
    check_true("without skip_title nothing is dropped — it is opt-in per site",
               to_event(dict(item, name="CLOSED FOR MAINTENANCE",
                             startDate=SOON_MIDNIGHT),
                        "u", "music", no_geocode, VENUE, None) is not None)

    print()
    print("one page is one occurrence: the id the sync upserts on")
    check("source_id is the event's own page, so two nights cannot collide",
          ev.source_id, "https://theroyalroomseattle.com/event/trio-reunion/")

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: " + "; ".join(FAILURES))
        return 1
    print("all jsonld adapter checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
