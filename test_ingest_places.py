"""Do the two newest adapters refuse to place an event they cannot place?

Both mapsee_ingest_squarespace and mapsee_ingest_luma get coordinates handed to
them, which makes them the two adapters most able to put an event confidently in
the wrong place. Each has one trap that produces a plausible, silent, wrong
answer, and each is checked here:

  SQUARESPACE ships a default map pin - 40.7207559,-74.0007613, lower Manhattan -
  on every event whose location was never filled in. On the first site wired up
  that was 17 of 22 events, all of them actually in Seattle. Nothing downstream
  can catch it: the coordinates are well-formed, they geocode, they sync, and the
  events land in New York. The rule under test is that a location with no address
  TEXT is not a location, whatever coordinates came with it.

  LUMA reports a US postal code only inside `full_address`, where a bare
  five-digit search finds the STREET NUMBER first: "15600 NE 8th St, Bellevue, WA
  98007" yielded 15600, which is a real postal code in Pennsylvania.

Pure functions and literal payloads: no network, no store, no database.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mapsee_ingest_squarespace as SQ
import mapsee_ingest_luma as LU

fails = []


def check(label, cond, detail=""):
    print(f"{'ok  ' if cond else 'FAIL'} {label}{'' if cond else '   ' + str(detail)}")
    if not cond:
        fails.append(label)


# --------------------------------------------------------------------------- #
# Squarespace: the default pin must never become a location
# --------------------------------------------------------------------------- #
UNSET = {"mapLat": 40.7207559, "mapLng": -74.0007613,
         "addressTitle": "", "addressLine1": "", "addressLine2": "", "addressCountry": ""}
REAL = {"mapLat": 47.6303122, "mapLng": -122.31431, "addressTitle": "Volunteer Park",
        "addressLine1": "1247 15th Avenue East", "addressLine2": "Seattle, WA, 98112",
        "addressCountry": ""}
VENUE = {"name": "Volunteer Park", "address": "1247 15th Ave E", "city": "Seattle",
         "region": "WA", "postal_code": "98112", "country": "US",
         "lat": 47.6303122, "lon": -122.31431}

check("unset location is not a real address", SQ.has_real_address(UNSET) is False)
check("filled location is a real address", SQ.has_real_address(REAL) is True)

p = SQ.resolve_place(UNSET, VENUE)
check("unset location falls back to the config venue, NOT Manhattan",
      p is not None and round(p["lat"], 4) == 47.6303 and p["city"] == "Seattle", p)

p = SQ.resolve_place(REAL, VENUE)
check("a real location is parsed from the event itself",
      p is not None and p["city"] == "Seattle" and p["region"] == "WA"
      and p["postal_code"] == "98112" and round(p["lat"], 4) == 47.6303, p)

check("unset location with NO venue fallback is unplaceable, not Manhattan",
      SQ.resolve_place(UNSET, None) is None, SQ.resolve_place(UNSET, None))

# Belt and braces: an address typed but the pin never moved off the default.
STALE_PIN = dict(REAL, mapLat=40.7207559, mapLng=-74.0007613)
p = SQ.resolve_place(STALE_PIN, VENUE)
check("default pin is rejected even when an address IS present",
      p is not None and round(p["lat"], 4) == 47.6303, p)

# A site outside the US must still survive the addressLine2 parser.
p = SQ.resolve_place({"addressTitle": "Rich Mix", "addressLine1": "35-47 Bethnal Green Rd",
                      "addressLine2": "London", "mapLat": 51.5237, "mapLng": -0.0725}, None)
check("non-US address parses without a state code",
      p is not None and p["city"] == "London" and p["region"] is None, p)

check("epoch ms converts to the site's local wall clock",
      SQ._times(1786154400459, "America/Los_Angeles") == ("2026-08-07T19:00:00", "2026-08-08T02:00:00Z"),
      SQ._times(1786154400459, "America/Los_Angeles"))
check("a missing timestamp yields nothing rather than 1970",
      SQ._times(None, "America/Los_Angeles") == (None, None))


# --------------------------------------------------------------------------- #
# Squarespace, off ?format=json: the page glues the street to the city, and a
# guess at where one ends is a wrong street on a real map. Only the tail is
# unambiguous, so the split happens ONLY when the config's own city confirms it.
# --------------------------------------------------------------------------- #
MAPQ = "1247 15th Avenue East Seattle, WA, 98112 United States"

loc = SQ._split_maplink(MAPQ, VENUE)
check("the config's city recovers the street from the glued address",
      loc.get("addressLine1") == "1247 15th Avenue East"
      and loc.get("addressLine2") == "Seattle, WA, 98112"
      and loc.get("addressCountry") == "United States", loc)

# The whole point: with no city to confirm the boundary, "1247 15th Avenue East
# Seattle" could be a street in Seattle or a street in East Seattle. Refusing is
# the only answer that cannot put a pin on the wrong road.
loc = SQ._split_maplink(MAPQ, None)
check("with no config city it emits NO street rather than guessing one",
      "addressLine1" not in loc, loc)
check("...and resolve_place then falls back to the venue block",
      SQ.resolve_place(dict(loc, addressTitle="Volunteer Park"), VENUE)["address"]
      == "1247 15th Ave E")

loc = SQ._split_maplink("1247 15th Avenue East Seattle, WA, 98112 United States",
                        {"city": "Tacoma"})
check("a config city that does not match the address is not forced on",
      "addressLine1" not in loc, loc)

check("a location field holding prose, not an address, yields nothing",
      SQ._split_maplink("Meet at the big cedar by the reservoir", VENUE) == {})

# The site's own UTC arithmetic, read out of the calendar-export link it renders
# for humans — no timezone inference anywhere in the path.
ART = ('<article class="eventlist-event eventlist-event--upcoming">'
       '<a href="http://www.google.com/calendar/event?action=TEMPLATE&text=X'
       '&dates=20260814T133000Z/20260814T143000Z">Google Calendar</a></article>')
start_ms, end_ms = SQ._gcal_ms(ART)
check("the export link gives the exact instant, in epoch ms",
      SQ._times(start_ms, "America/Los_Angeles") == ("2026-08-14T06:30:00", "2026-08-14T13:30:00Z"),
      SQ._times(start_ms, "America/Los_Angeles"))
check("and the end instant with it", end_ms == start_ms + 3600 * 1000, (start_ms, end_ms))

check("an all-day event keeps its DAY rather than being dropped",
      SQ._gcal_ms('<a href="?dates=20260814/20260815">x</a>')[0] is not None)
check("an article with no export link yields no time, not a wrong one",
      SQ._gcal_ms("<article>no link here</article>") == (None, None))

# The parser as a whole, against the markup shape the page actually ships.
PAGE = ('<article class="eventlist-event eventlist-event--upcoming">'
        '<h1 class="eventlist-title"><a href="/events/work-party" '
        'class="eventlist-title-link">2nd Saturday Work Party</a></h1>'
        '<a href="?dates=20260814T170000Z/20260814T190000Z">Google Calendar</a>'
        '<li class="eventlist-meta-item eventlist-meta-address">Volunteer Park '
        '<a href="http://maps.google.com?q=1247 15th Avenue East Seattle, WA, 98112 '
        'United States" class="eventlist-meta-address-maplink">(map)</a></li>'
        '</article>'
        '<article class="eventlist-event eventlist-event--past">'
        '<h1 class="eventlist-title"><a href="/events/old" '
        'class="eventlist-title-link">Last Year</a></h1>'
        '<a href="?dates=20250814T170000Z/20250814T190000Z">Google Calendar</a></article>')
items = SQ.parse_articles(PAGE, VENUE)
check("past articles are left on the page", len(items) == 1, [i["title"] for i in items])
check("the title, link and venue all come off the markup",
      items and items[0]["title"] == "2nd Saturday Work Party"
      and items[0]["fullUrl"] == "/events/work-party"
      and items[0]["location"].get("addressTitle") == "Volunteer Park", items)
check("the (map) link is not mistaken for the venue name",
      "map" not in (items[0]["location"].get("addressTitle") or "").lower(), items[0]["location"])


# --------------------------------------------------------------------------- #
# Luma: the postal code must not be the street number
# --------------------------------------------------------------------------- #
def postal(full):
    m = LU._POSTAL_RX.search(full)
    return m.group(1) if m else None


check("postal comes from the state, not the street number",
      postal("15600 NE 8th St, Bellevue, WA 98007, USA") == "98007",
      postal("15600 NE 8th St, Bellevue, WA 98007, USA"))
check("plain address still parses", postal("2205 7th Ave, Seattle, WA 98121, USA") == "98121")
check("zip+4 keeps the five", postal("1 Main St, Seattle, WA 98112-1234, USA") == "98112")
check("no US postal code yields None", postal("35-47 Bethnal Green Rd, London, UK") is None)


def luma_event(**kw):
    base = {"api_id": "evt-1", "name": "Thing", "visibility": "public",
            "location_type": "offline", "timezone": "America/Los_Angeles",
            "start_at": "2099-08-12T00:30:00.000Z", "end_at": "2099-08-12T03:30:00.000Z",
            "url": "thing", "coordinate": {"latitude": 47.6, "longitude": -122.3},
            "geo_address_info": {"city": "Seattle", "region_short": "WA",
                                 "country_code": "US",
                                 "full_address": "2205 7th Ave, Seattle, WA 98121, USA"}}
    base.update(kw)
    return base


src = {"name": "t", "category": "community"}
ev = LU.to_event(luma_event(), src)
check("a public offline event converts", ev is not None and ev.city == "Seattle"
      and ev.postal_code == "98121" and ev.timezone == "America/Los_Angeles", ev)
check("UTC and local are both recorded and differ",
      ev is not None and ev.start_utc.endswith("Z") and ev.start_local == "2099-08-11T17:30:00",
      (ev.start_local, ev.start_utc) if ev else None)

check("online-only events are dropped (nothing to map)",
      LU.to_event(luma_event(location_type="online"), src) is None)
check("non-public events are dropped",
      LU.to_event(luma_event(visibility="private"), src) is None)
check("an event with no coordinates is dropped, not pinned at 0,0",
      LU.to_event(luma_event(coordinate={}), src) is None)
check("past events are dropped",
      LU.to_event(luma_event(start_at="2001-01-01T00:00:00.000Z"), src) is None)

# The address field carries either a street or a venue name; a leading digit is
# the tell. Getting it wrong costs a label, never a location.
ev = LU.to_event(luma_event(geo_address_info=dict(
    luma_event()["geo_address_info"], address="Dragonfly Bookshop")), src)
check("a non-numeric address is treated as a venue name",
      ev is not None and ev.venue_name == "Dragonfly Bookshop" and ev.address is None, ev)
ev = LU.to_event(luma_event(geo_address_info=dict(
    luma_event()["geo_address_info"], address="1275 Kinnear Rd")), src)
check("a numeric address is treated as a street",
      ev is not None and ev.address == "1275 Kinnear Rd" and ev.venue_name is None, ev)

# The Discover feed is requested with discover_place_api_id; place_api_id is
# accepted, ignored and answered with the CALLER's city. Guard the spelling.
import inspect
_src = inspect.getsource(LU.iter_entries)
check("Discover is queried with discover_place_api_id",
      '"discover_place_api_id"' in _src, _src)
check("the ignored place_api_id spelling is never sent",
      '"place_api_id"' not in _src, _src)

print()
print(f"{'FAILURES: ' + ', '.join(fails) if fails else 'all checks passed'}")
sys.exit(1 if fails else 0)
