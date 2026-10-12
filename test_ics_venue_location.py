"""A community centre's own calendar lands on the centre.

The map ties an event to its venue by one thing: the 4-decimal coordinate key
(about 11 m). A centre's OpenStreetMap listing is drawn at its OSM point, so
its events link to it only when they sit on exactly that point. These checks
pin the rule in mapsee_ingest_ics._names_venue and the sync behaviour it
depends on:

  * a LOCATION that NAMES the source's venue (its name, an alias, or the
    house number and street in `street_hint`) takes the venue pin;
  * a LOCATION that names anywhere else goes to the geocoder as before;
  * an empty LOCATION takes the pin as before;
  * the pin survives the sync: a venue block with no address is never sent to
    the Census re-geocode, and nothing in the row says "OpenStreetMap
    contributors" (mapsee_retire_perday_osm hides rows that do).

Calendar.google.com's robots.txt disallows the iCal export and the API key
lives only in CI, so Polish Home's real events cannot be read here. The
fixtures are Calendar API items shaped like the events its own site listed on
2026-10-12 (titles invented; the LOCATION forms are Google's usual "place,
street, city" and the hall's own names), run through mapsee_gcal.to_ics, the
same text the production reader hands the adapter. Nobody in them is real.
"""
import io
import json
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
check("its venue carries NO address (an address goes to the Census re-geocode, off the key)",
      not any(V.get(k) for k in ("address", "city", "region", "postal_code", "country")), V)
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


rows, log = run(POLISH, ITEMS)
for uid in ("pierogi", "dance", "street", "alias", "short"):
    r = rows.get(uid)
    check(f"a LOCATION naming the hall lands on its listing's point: {uid}",
          r is not None and (r.latitude, r.longitude) == LISTING
          and r.venue_name == "Polish Home Association – Dom Polski",
          r and (r.latitude, r.longitude, r.venue_name))
r = rows["pierogi"]
day = (r.start_utc or r.start_local)[:10]               # Google writes a one-off in UTC: the 15th
check("the row's identity is still the feed's own LOCATION text (no re-keying)",
      r.fingerprint == make_fingerprint("Pierogi Making Workshop", day,
                                        "Polish Home Association, 1714 18th Ave, Seattle, WA 98122, USA")
      and r.fingerprint != make_fingerprint("Pierogi Making Workshop", day, r.venue_name),
      (day, r.fingerprint))
check("a pinned row carries no address for the sync to re-geocode",
      not any((r.address, r.city, r.region, r.postal_code, r.country)),
      (r.address, r.city, r.region, r.postal_code, r.country))

# --- one that names another place is geocoded as now ------------------------
for uid in ("church", "south", "park"):
    r = rows.get(uid)
    check(f"a LOCATION naming somewhere else is geocoded, not pinned: {uid}",
          r is not None and (r.latitude, r.longitude) == GEOCODED, r and (r.latitude, r.longitude))
check("the geocoder was asked for exactly those three",
      sorted(geocode_calls) == sorted(loc for uid, _t, loc in ITEMS if uid in ("church", "south", "park")),
      geocode_calls)

# --- an empty LOCATION takes the pin, as before -----------------------------
r = rows.get("bare")
check("an event with no LOCATION takes the venue pin, as before",
      r is not None and (r.latitude, r.longitude) == LISTING, r and (r.latitude, r.longitude))
check("the log counts both kinds of pinning",
      "1 pinned to the source's venue (no LOCATION/GEO)" in log
      and "5 pinned to the source's venue (LOCATION names it)" in log, log.strip())

# --- the rule itself, case by case -----------------------------------------
VEN = {"name": "Polish Home Association – Dom Polski", "lat": 1.0, "lon": 1.0,
       "aliases": ["Seattle Polish Cultural Center"], "street_hint": "1714 18th Ave"}
for loc, want in [
    ("POLISH HOME", True),                              # case
    ("Dom Polski (upstairs)", True),                   # a bracketed room
    ("Room 2 at Polish Home", True),                   # more words around the name
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

# --- the four 2026-10-11 Seattle calendars and the renamed Urban League -----
SEATTLE = {
    "Ballard Homestead": (47.6770363, -122.3867766),
    "Estelita's Library": (47.6005203, -122.2973001),
    "Vashon Senior Center": (47.447596, -122.4620197),
    "Heron's Nest Outdoor Education Center": (47.5590902, -122.3537607),
}
for name, point in SEATTLE.items():
    s = BY_NAME.get(name) or {}
    v = s.get("venue") or {}
    check(f"{name} is pinned to its OSM listing's point, with no address",
          (v.get("lat"), v.get("lon")) == point and not v.get("address") and v.get("name") == name, v)
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


built, census = synced(POLISH)
pinned = [built[t] for t in ("Pierogi Making Workshop", "Saturday Folk Dance", "Autumn Lantern Workshop",
                             "Film Night", "Choir Rehearsal", "Independence Day Concert") if t in built]
check("all six pinned rows reach the table", len(pinned) == 6, sorted(built))
check("every pinned row keeps the listing's exact point through build_rows and to_row",
      all((row["lat"], row["lon"]) == LISTING for row in pinned), [(row["lat"], row["lon"]) for row in pinned])
check("so every pinned row shares the listing's 4-decimal key",
      all(f"{row['lat']:.4f},{row['lon']:.4f}" == f"{LISTING[0]:.4f},{LISTING[1]:.4f}" for row in pinned))
check("the Census batch was never asked (no address, nothing to send)", census.posts == [], census.posts)
check("the pinned rows carry the hall's name and no street",
      all(row["place_name"] == "Polish Home Association – Dom Polski" and not row.get("street_address")
          for row in pinned))
check("no row says 'OpenStreetMap contributors'",
      not any("OpenStreetMap contributors" in (row.get("description") or "") for row in built.values()))

# The control: the SAME venue with an address is moved by the Census pass. If
# this ever passes unchanged, the check above is measuring nothing.
with_address = dict(POLISH, venue=dict(POLISH.get("venue") or {}, address="1714 18th Ave",
                                       city="Seattle", region="WA"))
built_a, census_a = synced(with_address)
check("control: a venue WITH an address is re-geocoded off the pin",
      census_a.posts and built_a["Choir Rehearsal"]["lat"] == 47.6 and built_a["Choir Rehearsal"]["lon"] == -122.3,
      (len(census_a.posts), built_a.get("Choir Rehearsal", {}).get("lat")))

# --- the smoke run prints where each LOCATION goes ------------------------
with tempfile.TemporaryDirectory() as tmp:
    cfg = os.path.join(tmp, "one.json")
    Path(cfg).write_text(json.dumps([POLISH]), encoding="utf-8")
    meta = {"summary": "Polish Home", "timeZone": "America/Los_Angeles"}
    out = io.StringIO()
    with patch.object(mapsee_gcal, "api_key", return_value="test-key"), \
         patch.object(mapsee_gcal, "fetch_events", return_value=(meta, api_items(ITEMS))), \
         patch.object(ICS, "datetime", Clock), redirect_stdout(out):
        rc = mapsee_gcal.smoke(cfg, show="Polish Home")
    text = out.getvalue()
check("smoke reads it and lists its LOCATIONs, each marked venue or geocode",
      rc == 0 and "8 distinct LOCATIONs" in text
      and "venue   | Dom Polski - Main Hall" in text
      and "geocode | 1714 18th Ave S, Seattle, WA 98144, USA" in text
      and "5 pinned to the source's venue (LOCATION names it)" in text, text)

print(f"\n{len(fails)} failure(s)")
sys.exit(1 if fails else 0)
