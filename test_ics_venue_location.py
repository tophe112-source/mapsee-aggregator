"""A community centre's own calendar lands on the centre.

The map ties an event to its venue by one thing: the 4-decimal coordinate key
(about 11 m). A centre's OpenStreetMap listing is drawn at its OSM point, so
its events link to it only when they sit on exactly that point. These checks
pin the rule in mapsee_ingest_ics._names_venue and the sync behaviour it
depends on:

  * a LOCATION that NAMES the source's venue (its name, an alias, or the
    house number and street in `street_hint`) takes the venue pin, and keeps
    its own text as the row's place name;
  * a LOCATION that names anywhere else goes to the geocoder as before, and so
    does one whose publisher GEO puts it somewhere else;
  * an empty LOCATION takes the pin as before, unless the block says
    `pin_unnamed: false` (the five calendars added here, until their bare
    titles have been read through smoke-gcal);
  * the pin survives the sync: a venue block with no street address is never
    sent to the Census re-geocode (city, region and country alone do not send
    it), and nothing in the row says "OpenStreetMap contributors"
    (mapsee_retire_perday_osm hides rows that do).

Calendar.google.com's robots.txt disallows the iCal export and the API key
lives only in CI, so Polish Home's real events cannot be read here. The
fixtures are Calendar API items shaped like the events its own site listed on
2026-10-12 (titles invented; the LOCATION forms are Google's usual "place,
street, city" and the hall's own names), run through mapsee_gcal.to_ics, the
same text the production reader hands the adapter. Nobody in them is real.
"""
import io
import json
import re
import os
import sys
import tempfile
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mapsee_gcal
import mapsee_ingest_ics as ICS
import mapsee_supabase_sync as sync
from mapsee_ingest import EventStore, make_fingerprint

fails = []


def check(label, condition, detail=""):
    print(f"{'ok  ' if condition else 'FAIL'} {label}{'' if condition else '   ' + str(detail)}")
    if not condition:
        fails.append(label)


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        fixed = datetime(2026, 10, 12, 12, tzinfo=timezone.utc)
        return fixed.astimezone(tz) if tz else fixed.replace(tzinfo=None)


class Collect:
    def __init__(self):
        self.rows = []

    def upsert(self, ev):
        self.rows.append(ev)
        return "new"

    def cancel(self, ev, reason):
        return "cancelled"


# Read through getattr so a checkout without the rule FAILS line by line instead of crashing.
names_venue = getattr(ICS, "_names_venue", lambda _venue, _loc: False)

GEOCODED = (47.0, -122.0)                 # where the stand-in geocoder puts everything
geocode_calls = []


def fake_geocoder(_session, _suffix):
    def geocode(loc):
        geocode_calls.append(loc)
        return GEOCODED
    return geocode


CONFIG = json.loads((Path(__file__).parent / "ics_sources.json").read_text(encoding="utf-8"))
BY_NAME = {s["name"]: s for s in CONFIG if isinstance(s, dict) and s.get("name")}
POLISH = BY_NAME.get("Polish Home Association – Dom Polski")
CID = "d83853455e5cebb55a95bc30f0719941cc219c65f48a1cd6228d3f1ed34a7e09@group.calendar.google.com"
# The live OSM listing (way/229910960) is drawn here; read 2026-10-12.
LISTING = (47.6169525, -122.30845)

# --- the configured source --------------------------------------------------
check("Polish Home is configured", POLISH is not None)
POLISH = POLISH or {"name": "missing", "url": mapsee_gcal.ical_url(CID), "venue": {}}  # fail on, not crash
check("its url is the calendar's export address, built by mapsee_gcal.ical_url",
      POLISH["url"] == mapsee_gcal.ical_url(CID) and mapsee_gcal.calendar_id(POLISH["url"]) == CID,
      POLISH.get("url"))
check("it is a community source, limit 300, found by hand",
      POLISH.get("category") == "community" and POLISH.get("limit") == 300
      and POLISH.get("_found") == "hand: owner's example 2026-10-12 (Pretty Google Calendar on polishhome.org/calendar/)",
      {k: POLISH.get(k) for k in ("category", "limit", "_found")})
V = POLISH.get("venue") or {}
check("its venue is the OSM listing's own point",
      (V.get("lat"), V.get("lon")) == LISTING and V.get("name") == "Polish Home Association – Dom Polski", V)
check("its venue carries NO street address (an address goes to the Census re-geocode, off the key)",
      not V.get("address"), V)
check("...but does carry its town, for the row's locality",
      (V.get("city"), V.get("region"), V.get("country")) == ("Seattle", "WA", "US"), V)
# Its LOCATION-less titles were read in CI on 2026-10-12 (smoke-gcal run 38314170414:
# 142 of 148, the hall's own programme), so pin_unnamed is gone; the film festival,
# which screens elsewhere, is kept off the pin, and its one room LOCATION is pinned.
check("its bare titles were read in CI, so its LOCATION-less events take the pin (no pin_unnamed)",
      "pin_unnamed" not in V and "smoke-gcal" in (POLISH.get("_note") or ""), V)
_skip = re.compile(POLISH.get("venue_skip") or "(?!)", re.I)
check("venue_skip keeps the Seattle Polish Film Festival off the pin, and nothing of the hall's own",
      all(_skip.search(t) for t in ("Seattle Polish Film Festival", "SPFF Opening Ceremony with Marek Probosz"))
      and not any(_skip.search(t) for t in ("Friday Dinner", "Pierogi making", "Polish School",
                                              "Vivat Musica Choir practice", "Domówka u Syreny")),
      POLISH.get("venue_skip"))
_lcr = [o for o in POLISH.get("location_overrides") or [] if o.get("location") == "Lower Conference Room"]
check("'Lower Conference Room' (a room in the building) is pinned to the listing by override, with no address",
      len(_lcr) == 1 and (_lcr[0]["venue"].get("lat"), _lcr[0]["venue"].get("lon")) == LISTING
      and not _lcr[0]["venue"].get("address"), _lcr)
# The held-back rule itself, on a copy of the block as it was before the read.
HELD = dict(POLISH, venue=dict(V, pin_unnamed=False))
check("its street is a hint, only matched", V.get("street_hint") == "1714 18th Ave", V.get("street_hint"))
check("no Google API key is stored with it",
      not any(k in json.dumps(POLISH).lower() for k in ("google_api", "apikey", "key=")), POLISH)

# --- a LOCATION that names the venue lands on the pin ------------------------
# Google writes a picked place as "<place>, <street>, <city>, <state> <zip>, USA".
ITEMS = [
    ("pierogi", "Pierogi Making Workshop", "Polish Home Association, 1714 18th Ave, Seattle, WA 98122, USA"),
    ("dance", "Saturday Folk Dance", "Dom Polski - Main Hall"),
    ("street", "Autumn Lantern Workshop", "1714 18th Avenue, Seattle, WA 98122"),
    ("alias", "Film Night", "Seattle Polish Cultural Center"),
    ("short", "Choir Rehearsal", "Polish Home"),
    ("church", "Harvest Mass", "St. Margaret of Scotland Church, 3221 14th Ave W, Seattle, WA 98119, USA"),
    ("south", "Coat Drive Pickup", "1714 18th Ave S, Seattle, WA 98144, USA"),
    ("park", "Fall Clean-up", "Seven Hills Park, 1500 Pike St, Seattle"),
    ("bare", "Independence Day Concert", ""),
]


def api_items(rows):
    out = []
    for i, (uid, title, loc) in enumerate(rows):
        item = {"id": uid, "iCalUID": f"{uid}@google.com", "status": "confirmed", "summary": title,
                "start": {"dateTime": f"2026-10-{14 + i:02d}T18:00:00-07:00"},
                "end": {"dateTime": f"2026-10-{14 + i:02d}T20:00:00-07:00"}}
        if loc:
            item["location"] = loc
        out.append(item)
    return out


def run(src, rows):
    """The production path: API items -> to_ics -> ingest_ics."""
    geocode_calls.clear()
    meta = {"summary": "Polish Home", "timeZone": "America/Los_Angeles"}
    store = Collect()
    out = io.StringIO()
    with patch.object(mapsee_gcal, "api_key", return_value="test-key"), \
         patch.object(mapsee_gcal, "fetch_events", return_value=(meta, api_items(rows))), \
         patch.object(ICS, "datetime", Clock), \
         patch.object(ICS, "make_location_geocoder", side_effect=fake_geocoder), \
         redirect_stdout(out):
        ICS.ingest_ics(store, None, src)
    return {r.source_id.split("@")[0]: r for r in store.rows}, out.getvalue()


def run_ics(src, vevents):
    """A plain iCal feed (GEO, STATUS) through ingest_ics. vevents: (uid, title, LOCATION, extra lines)."""
    geocode_calls.clear()
    body = ["BEGIN:VCALENDAR", "VERSION:2.0"]
    for i, (uid, title, loc, extra) in enumerate(vevents):
        body += ["BEGIN:VEVENT", f"UID:{uid}", f"SUMMARY:{title}", f"DTSTART:202610{20 + i:02d}T180000Z"]
        body += ([f"LOCATION:{loc}"] if loc else []) + list(extra) + ["END:VEVENT"]
    text = "\r\n".join(body + ["END:VCALENDAR"]) + "\r\n"
    store = Collect()
    out = io.StringIO()
    with patch.object(ICS, "_fetch_ics", return_value=(text, "200")), \
         patch.object(ICS, "datetime", Clock), \
         patch.object(ICS, "make_location_geocoder", side_effect=fake_geocoder), \
         redirect_stdout(out):
        ICS.ingest_ics(store, None, src)
    return {r.source_id: r for r in store.rows}, out.getvalue()


FEED_LOC = {uid: loc for uid, _t, loc in ITEMS}
rows, log = run(HELD, ITEMS)
for uid in ("pierogi", "dance", "street", "alias", "short"):
    r = rows.get(uid)
    check(f"a LOCATION naming the hall lands on its listing's point, keeping its own text: {uid}",
          r is not None and (r.latitude, r.longitude) == LISTING and r.venue_name == FEED_LOC[uid],
          r and (r.latitude, r.longitude, r.venue_name))
r = rows["pierogi"]
day = (r.start_utc or r.start_local)[:10]               # Google writes a one-off in UTC: the 15th
check("the row's identity is still the feed's own LOCATION text (no re-keying)",
      r.fingerprint == make_fingerprint("Pierogi Making Workshop", day,
                                        "Polish Home Association, 1714 18th Ave, Seattle, WA 98122, USA")
      and r.fingerprint != make_fingerprint("Pierogi Making Workshop", day, "Polish Home Association – Dom Polski"),
      (day, r.fingerprint))
check("a pinned row carries no street address for the sync to re-geocode, only its town",
      not r.address and (r.city, r.region, r.country) == ("Seattle", "WA", "US"),
      (r.address, r.city, r.region, r.postal_code, r.country))

# --- one that names another place is geocoded as now ------------------------
for uid in ("church", "south", "park"):
    r = rows.get(uid)
    check(f"a LOCATION naming somewhere else is geocoded, not pinned: {uid}",
          r is not None and (r.latitude, r.longitude) == GEOCODED, r and (r.latitude, r.longitude))
check("the geocoder was asked for exactly those three",
      sorted(geocode_calls) == sorted(loc for uid, _t, loc in ITEMS if uid in ("church", "south", "park")),
      geocode_calls)

# --- an empty LOCATION: not pinned while pin_unnamed is false ------------
check("with pin_unnamed false, an event with no LOCATION is dropped as unplaceable (its title is unread)",
      "bare" not in rows and "1 unplaceable (no LOCATION/GEO)" in log
      and "1 of them name no place and the venue block says pin_unnamed false" in log
      and "pinned to the source's venue (no LOCATION/GEO)" not in log, log.strip())
check("...while the named ones are still pinned",
      "5 pinned to the source's venue (LOCATION names it)" in log, log.strip())
# ...and once the titles have been read and the flag dropped, it takes the pin, as before.
OPEN = dict(POLISH, venue={k: v for k, v in (POLISH.get("venue") or {}).items() if k != "pin_unnamed"})
rows_open, log_open = run(OPEN, ITEMS)
r = rows_open.get("bare")
check("an event with no LOCATION takes the venue pin, as before, and the venue's name",
      r is not None and (r.latitude, r.longitude) == LISTING
      and r.venue_name == "Polish Home Association – Dom Polski", r and (r.latitude, r.longitude, r.venue_name))
check("the log counts both kinds of pinning",
      "1 pinned to the source's venue (no LOCATION/GEO)" in log_open
      and "5 pinned to the source's venue (LOCATION names it)" in log_open, log_open.strip())

# --- the rule itself, case by case -----------------------------------------
VEN = {"name": "Polish Home Association – Dom Polski", "lat": 1.0, "lon": 1.0,
       "aliases": ["Seattle Polish Cultural Center"], "street_hint": "1714 18th Ave"}
for loc, want in [
    ("POLISH HOME", True),                              # case
    ("Dom Polski (upstairs)", True),                   # a bracketed room
    ("Polish Home Ballroom", False),                    # more words and none of its own kind words
    ("Polish Home Association Ballroom", True),        # ...unless it carries one
    ("Polish Home Association booth, Seattle Center", False),     # a booth is another place
    ("Polish Home Garden", False),                     # so is a garden it does not name
    ("1714 18th Ave #2", True),                        # a unit after the street
    ("Polish", False), ("Home", False),               # half a name
    ("Polish Cultural Center", False),                 # another Polish place
    ("Polski Sklep", False),                           # a shop
    ("1714 E 18th Ave", False),                        # another street
    ("17140 18th Ave", False),                         # another house
    ("18th Ave", False),                               # a street, no house
    ("2nd Floor, Polish Home", True),                  # a floor is not a house number
    ("Polish Home Association, 2000 Other St, Seattle", False),   # its name, but ANOTHER address
    ("Out of Building, Polish Home", False),           # LibCal's outreach: says NOT here
    ("All Locations, Dom Polski", False),
    ("Offsite - Polish Home", False),
    ("Online Event, Polish Home", False),
    ("Polish Home, Seattle, WA, USA", True),           # a bare country is not "elsewhere"
    ("", False), (None, False),
]:
    check(f"names the venue? {loc!r} -> {want}", names_venue(VEN, loc) is want)
# One distinctive word is often a place: "Ballard" is the neighbourhood the centre is named for.
BALLARD = {"name": "Ballard Community Center", "lat": 1.0, "lon": 1.0}
for loc, want in [
    ("Ballard Community Center", True),
    ("Ballard Center - Gym", True),
    ("Room 2, Ballard Community Centre", True),
    ("Ballard", False),                                # the neighbourhood
    ("Pottery Studio, Ballard", False),
    ("Ballard Library", False),                        # another building in it
]:
    check(f"a one-word name needs its own generic word: {loc!r} -> {want}", names_venue(BALLARD, loc) is want)
# Two words are often a place too: a kind of place the venue's name lacks is another place,
# and so is one named in a part before it.
for venue, loc, want in [
    ("Queen Anne Community Center", "Queen Anne Library", False),
    ("Queen Anne Community Center", "Queen Anne Community Center - Gym", True),
    ("Lake City Community Center", "Lake City Library", False),
    ("Green Lake Community Center", "Green Lake Park, Seattle", False),
    ("South Park Community Center", "South Park Library", False),
    ("South Park Community Center", "Marra Farm, South Park", False),
    ("South Park Community Center", "Teen Room, South Park Community Center", True),
    ("Osborne Library", "Fort Rouge Leisure Centre - MPR, Osborne Library", False),
    ("Chester Library", "Perkinson Center for the Arts and Education, Chester Library", False),
    ("Thame Town Hall (town centre)", "Thame Town Football Club, Meadow View Park", False),
    ("Thame Town Hall (town centre)", "Market Square, Town Centre", False),   # a bracket is a descriptor
    ("Thame Town Hall (town centre)", "Thame Town Hall, High Street", True),
    ("MAECare, Cranmer Bank", "Moor Allerton Library, Cranmer Bank", False),  # after the comma is a street
    ("MAECare, Cranmer Bank", "MAECare, Cranmer Bank", True),
    ("Biblioteca, Universidad de Monterrey", "Centro Roberto Garza Sada, Universidad de Monterrey", False),
    ("Biblioteca, Universidad de Monterrey", "Auditorio Luis Elizondo, Universidad de Monterrey", False),
    ("稲永ビジターセンター（藤前干潟）", "藤前活動センター（藤前干潟）", False),
    ("稲永ビジターセンター（藤前干潟）", "稲永ビジターセンター", True),
    # a venue's own kind words are generic for it: the branch is "Atlee", the library "Lee Road"
    ("Atlee Branch Library", "Atlee Meeting Room, Atlee Branch", True),
    ("Atlee Branch Library", "Atlee Branch", True),
    ("Atlee Branch Library", "Atlee", False),
    ("Lee Road Library", "Meeting Room A, Lee Road", True),
    ("Jurong Regional Library", "Jurong Library - Activity Zone (Basement 1), Jurong Library", True),
    ("Burke Centre Library", "Burke Centre Meeting Room 117, Burke Centre Library", True),
    ("Temple University Japan", "City Campus - Room 303, Temple University Japan - City Campus", True),
    # more distinctive words than the name: only with one of the name's own kind words
    ("Vashon Senior Center", "Vashon Senior Center Dining Room", True),
    ("Vashon Senior Center", "Vashon Senior Living", False),
]:
    check(f"{venue!r}: names it? {loc!r} -> {want}",
          names_venue({"name": venue, "lat": 1.0, "lon": 1.0}, loc) is want)
ONE = {"name": "Vrankrijk", "lat": 1.0, "lon": 1.0}
check("a one-word name must be the whole part: 'Vrankrijk, Spuistraat 216'",
      names_venue(ONE, "Vrankrijk, Spuistraat 216"))
check("...and not a word in somebody else's name: 'Vrankrijk Bar'",
      not names_venue(ONE, "Vrankrijk Bar"))
check("accents, possessives and generic words are ignored",
      names_venue({"name": "Heron's Nest Outdoor Education Center", "lat": 1, "lon": 1},
                       "Herón’s Nest Outdoor Education Centre"))
check("a neighbourhood-centroid venue does not swallow an office with its own address",
      not names_venue({"name": "Lyndale neighborhood", "lat": 1, "lon": 1},
                      "Lyndale Neighborhood Association, 3537 Nicollet Ave S"))
check("a venue's own `address` matches as a street too",
      names_venue({"name": "Ang Mo Kio Library", "address": "4300 Ang Mo Kio Avenue 6", "lat": 1, "lon": 1},
                  "Programme Zone, 4300 Ang Mo Kio Ave 6, Singapore 569842"))
check("a venue with no coordinates never matches",
      not names_venue({"name": "Polish Home"}, "Polish Home"))

# --- a source without a venue is untouched; an exact override still wins -----
plain = {k: v for k, v in POLISH.items() if k != "venue"}
rows, _ = run(plain, ITEMS[:1])
check("without a venue block the same LOCATION is geocoded",
      (rows["pierogi"].latitude, rows["pierogi"].longitude) == GEOCODED)
override = dict(POLISH, location_overrides=[{"location": "Polish Home", "venue": {
    "name": "Override hall", "lat": 10.0, "lon": 20.0}}])
rows, _ = run(override, [("short", "Choir Rehearsal", "Polish Home")])
check("an exact location_overrides rule still beats the name rule",
      (rows["short"].latitude, rows["short"].venue_name) == (10.0, "Override hall"))
skip = dict(POLISH, venue_skip="online")
rows, _ = run(skip, [("zoom", "Polish lessons online", "Polish Home")])
check("venue_skip keeps a named LOCATION off the pin when the title says elsewhere",
      (rows["zoom"].latitude, rows["zoom"].longitude) == GEOCODED)

# --- the publisher's own GEO, and a cancelled VEVENT ------------------------
far = "GEO:47.6200;-122.3200"                           # about 1.1 km from the hall
near = "GEO:47.6172;-122.3086"                          # about 30 m from it
rows, log = run_ics(POLISH, [
    ("geo-far", "Polish Home Picnic", "Polish Home", [far]),
    ("geo-near", "Polish Home Social", "Polish Home", [near]),
    ("geo-zero", "Polish Home Supper", "Polish Home", ["GEO:0;0"]),
    ("gone", "Polish Home Dance", "Polish Home", ["STATUS:CANCELLED"]),
])
check("a named LOCATION whose GEO is elsewhere keeps the publisher's GEO",
      (rows["geo-far"].latitude, rows["geo-far"].longitude) == (47.62, -122.32),
      (rows["geo-far"].latitude, rows["geo-far"].longitude))
check("a named LOCATION whose GEO is within 100 m takes the listing's exact point",
      (rows["geo-near"].latitude, rows["geo-near"].longitude) == LISTING)
check("GEO 0;0 is no GEO: the name pins it",
      (rows["geo-zero"].latitude, rows["geo-zero"].longitude) == LISTING)
check("a cancelled VEVENT writes no row and is not counted as pinned",
      "gone" not in rows and "2 pinned to the source's venue (LOCATION names it)" in log, log.strip())
check("nothing here asked the geocoder", geocode_calls == [], geocode_calls)

# --- the four 2026-10-11 Seattle calendars and the renamed Urban League -----
SEATTLE = {
    "Ballard Homestead": (47.6770363, -122.3867766),
    "Estelita's Library": (47.6005203, -122.2973001),
    "Vashon Senior Center": (47.447596, -122.4620197),
    "Heron's Nest Outdoor Education Center": (47.5590902, -122.3537607),
}
# Read through smoke-gcal on 2026-10-12 (run 38314170414): Ballard Homestead's 12 bare
# titles are its own concerts and workshops; the others are still held.
READ = {"Ballard Homestead"}
for name, point in SEATTLE.items():
    s = BY_NAME.get(name) or {}
    v = s.get("venue") or {}
    check(f"{name} is pinned to its OSM listing's point, with no street address",
          (v.get("lat"), v.get("lon")) == point and not v.get("address") and v.get("name") == name, v)
    if name in READ:
        check(f"{name}: its bare titles were read in CI, so pin_unnamed is gone and its _note says so",
              "pin_unnamed" not in v and "smoke-gcal" in (s.get("_note") or ""), v)
    else:
        check(f"{name}: its LOCATION-less events wait for smoke-gcal (pin_unnamed false, said in _note)",
              v.get("pin_unnamed") is False and "smoke-gcal" in (s.get("_note") or ""), v)
    check(f"{name} credits the point in its config, not in rows",
          "Map point © OpenStreetMap (ODbL)" in (s.get("_venue_source") or "")
          and "way/" in (s.get("_venue_source") or ""), s.get("_venue_source"))
    check(f"{name}: the venue's own name lands on the pin", names_venue(v, name))
check("Polish Home credits its point the same way",
      "Map point © OpenStreetMap (ODbL)" in (POLISH.get("_venue_source") or "")
      and "way/229910960" in (POLISH.get("_venue_source") or ""))
check("no venue block says 'OpenStreetMap contributors' (mapsee_retire_perday_osm keys on it)",
      not any("OpenStreetMap contributors" in json.dumps(s.get("venue") or {}) for s in CONFIG
              if isinstance(s, dict)))
ul = [s for s in CONFIG if isinstance(s, dict) and s.get("url") == "https://urbanleague.org/?ical=1&eventDisplay=list"]
check("the Urban League feed carries its own name, not a community centre's",
      len(ul) == 1 and ul[0]["name"] == "Urban League of Metropolitan Seattle (Seattle)"
      and "Rainier Beach Community Center (Seattle)" not in BY_NAME, ul and ul[0]["name"])

# --- the pin survives the sync ---------------------------------------------
class CensusSession:
    """Answers every address the Census batch is sent with a point elsewhere."""
    def __init__(self):
        self.posts = []

    def post(self, url, files=None, data=None, timeout=None):
        body = files["addressFile"][1]
        self.posts.append(body)
        lines = [ln.split(",") for ln in body.strip().splitlines()]

        class R:
            status_code = 200
            text = "\n".join(f'{row[0]},"x",Match,Exact,"x","-122.3,47.6",1,L' for row in lines)
        return R()


def synced(src):
    rows, _ = run(src, ITEMS)
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "store.json")
        store = EventStore(path)
        for r in rows.values():
            store.upsert(r)
        with redirect_stdout(io.StringIO()):
            store.save()
            session = CensusSession()
            out = sync.build_rows(path, "host-id", geo_session=session)
    return {row["title"]: row for row in out}, session


built, census = synced(OPEN)
pinned = [built[t] for t in ("Pierogi Making Workshop", "Saturday Folk Dance", "Autumn Lantern Workshop",
                             "Film Night", "Choir Rehearsal", "Independence Day Concert") if t in built]
check("all six pinned rows reach the table", len(pinned) == 6, sorted(built))
check("every pinned row keeps the listing's exact point through build_rows and to_row",
      all((row["lat"], row["lon"]) == LISTING for row in pinned), [(row["lat"], row["lon"]) for row in pinned])
check("so every pinned row shares the listing's 4-decimal key",
      all(f"{row['lat']:.4f},{row['lon']:.4f}" == f"{LISTING[0]:.4f},{LISTING[1]:.4f}" for row in pinned))
check("the Census batch was never asked (no street address, nothing to send; the city alone sends nothing)",
      census.posts == [], census.posts)
check("a named row's place is the feed's own text; the bare one's is the hall's name",
      built["Saturday Folk Dance"]["place_name"] == "Dom Polski - Main Hall"
      and built["Independence Day Concert"]["place_name"] == "Polish Home Association – Dom Polski",
      (built["Saturday Folk Dance"]["place_name"], built["Independence Day Concert"]["place_name"]))
check("every pinned row carries its town and no 📍 street line",
      all(row.get("locality") == "Seattle" and not row.get("street_address") for row in pinned),
      [(row.get("locality"), row.get("street_address")) for row in pinned])
check("no row says 'OpenStreetMap contributors'",
      not any("OpenStreetMap contributors" in (row.get("description") or "") for row in built.values()))

# The control: the SAME venue with an address is moved by the Census pass. If
# this ever passes unchanged, the check above is measuring nothing.
with_address = dict(OPEN, venue=dict(OPEN.get("venue") or {}, address="1714 18th Ave",
                                     city="Seattle", region="WA"))
built_a, census_a = synced(with_address)
check("control: a venue WITH an address is re-geocoded off the pin",
      census_a.posts and built_a["Choir Rehearsal"]["lat"] == 47.6 and built_a["Choir Rehearsal"]["lon"] == -122.3,
      (len(census_a.posts), built_a.get("Choir Rehearsal", {}).get("lat")))

# --- the smoke run prints where each LOCATION goes ------------------------
with tempfile.TemporaryDirectory() as tmp:
    cfg = os.path.join(tmp, "one.json")
    Path(cfg).write_text(json.dumps([HELD]), encoding="utf-8")
    meta = {"summary": "Polish Home", "timeZone": "America/Los_Angeles"}
    out = io.StringIO()
    with patch.object(mapsee_gcal, "api_key", return_value="test-key"), \
         patch.object(mapsee_gcal, "fetch_events", return_value=(meta, api_items(ITEMS))), \
         patch.object(ICS, "datetime", Clock), redirect_stdout(out):
        rc = mapsee_gcal.smoke(cfg, show="Polish Home")
    text = out.getvalue()
check("smoke reads it and lists its LOCATIONs, each marked with where the adapter puts it",
      rc == 0 and "8 distinct LOCATIONs:" in text
      and "venue       | Dom Polski - Main Hall" in text
      and "geocode     | 1714 18th Ave S, Seattle, WA 98144, USA" in text
      and "5 pinned to the source's venue (LOCATION names it)" in text
      and "1 unplaceable (no LOCATION/GEO)" in text, text)
# The labels are the adapter's own decisions, not a second opinion: compare with ingest, item by item.
rows, _ = run(HELD, ITEMS)
for uid, _title, loc in ITEMS:
    r = rows.get(uid)
    did = ("unplaceable" if r is None else "venue" if (r.latitude, r.longitude) == LISTING
           else "geocode" if (r.latitude, r.longitude) == GEOCODED else "?")
    check(f"smoke's label for {loc!r} is where ingest put it ({did})", ICS._where(HELD, loc) == did,
          ICS._where(HELD, loc))
check("smoke marks an exact override 'override', an online room 'online'",
      ICS._where(override, "Polish Home") == "override" and ICS._where(POLISH, "Online") == "online"
      and ICS._where(OPEN, "") == "venue" and ICS._where(HELD, "") == "unplaceable"
      and ICS._where(POLISH, "") == "venue",
      (ICS._where(override, "Polish Home"), ICS._where(POLISH, "Online"), ICS._where(OPEN, "")))

print(f"\n{len(fails)} failure(s)")
sys.exit(1 if fails else 0)
