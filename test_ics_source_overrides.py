"""Source-specific ICS location and timezone corrections."""
import json
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import patch

import mapsee_ingest_ics as ICS
from mapsee_ingest import make_fingerprint


class Store:
    def __init__(self):
        self.rows = []

    def upsert(self, event):
        self.rows.append(event)
        return event.source_id


FIXTURE = "\r\n".join((
    "BEGIN:VCALENDAR",
    "BEGIN:VEVENT",
    "UID:ponce-oct10",
    "SUMMARY:City Winery Tour + Tasting",
    "DTSTART;TZID=UTC:20261010T150000",
    "DTEND;TZID=UTC:20261010T170000",
    "LOCATION:City Winery",
    "END:VEVENT",
    "BEGIN:VEVENT",
    "UID:ponce-other-title",
    "SUMMARY:City Winery Happy Hour",
    "DTSTART;TZID=UTC:20261012T150000",
    "LOCATION:City Winery",
    "END:VEVENT",
    "BEGIN:VEVENT",
    "UID:ponce-other-location",
    "SUMMARY:City Winery Tour + Tasting",
    "DTSTART;TZID=UTC:20261012T150000",
    "LOCATION:Citizen Supply",
    "END:VEVENT",
    "BEGIN:VEVENT",
    "UID:ponce-utc-z",
    "SUMMARY:City Winery Tour + Tasting",
    "DTSTART:20261013T150000Z",
    "LOCATION:City Winery",
    "END:VEVENT",
    "BEGIN:VEVENT",
    "UID:ponce-all-day",
    "SUMMARY:City Winery Tour + Tasting",
    "DTSTART;VALUE=DATE:20261014",
    "LOCATION:City Winery",
    "END:VEVENT",
    "END:VCALENDAR",
    "",
))

config = json.loads((Path(__file__).parent / "ics_sources.json").read_text(encoding="utf-8"))
source = next(s for s in config if s["name"] == "Ponce City Market Events (Atlanta)")
geocode_calls = []


class AuditClock(datetime):
    @classmethod
    def now(cls, tz=None):
        fixed = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)
        return fixed.astimezone(tz) if tz else fixed.replace(tzinfo=None)


def fake_geocoder(_session, _suffix):
    def geocode(location):
        geocode_calls.append(location)
        return 40.0, -80.0
    return geocode


with patch.object(ICS, "_fetch_ics", return_value=(FIXTURE, "200")), \
     patch.object(ICS, "datetime", AuditClock), \
     patch.object(ICS, "make_location_geocoder", side_effect=fake_geocoder):
    store = Store()
    kept = ICS.ingest_ics(store, None, source)

assert kept == 5, kept
target = next(row for row in store.rows if row.source_id == "ponce-oct10")
assert (target.start_local, target.start_utc, target.end_local, target.end_utc) == (
    "2026-10-10T15:00:00-04:00", "2026-10-10T19:00:00Z",
    "2026-10-10T17:00:00-04:00", "2026-10-10T21:00:00Z",
), (target.start_local, target.start_utc, target.end_local, target.end_utc)
assert target.source_id == "ponce-oct10"
assert target.fingerprint == make_fingerprint("City Winery Tour + Tasting", "2026-10-10", "City Winery")
assert (target.venue_name, target.address, target.city, target.region, target.postal_code,
        target.country, target.latitude, target.longitude) == (
    "City Winery Atlanta", "650 North Avenue NE", "Atlanta", "GA", "30308", "US",
    33.7716908, -84.3668292,
)
assert target.source_details == {"organizer": {
    "type": "Organization", "name": "City Winery Atlanta",
    "url": "https://citywinery.com/pages/locations/atlanta",
}}
assert geocode_calls == ["Citizen Supply"], geocode_calls

by_id = {row.source_id: row for row in store.rows}
assert by_id["ponce-other-title"].start_utc == "2026-10-12T15:00:00Z"
assert by_id["ponce-other-location"].start_utc == "2026-10-12T15:00:00Z"
assert by_id["ponce-utc-z"].start_utc == "2026-10-13T15:00:00Z"
assert by_id["ponce-all-day"].start_local == "2026-10-14"
assert by_id["ponce-all-day"].start_utc is None

# The same event outside the configured Ponce source keeps its published UTC time.
other_source = {"name": "unrelated", "url": "https://example.test/calendar.ics"}
with patch.object(ICS, "_fetch_ics", return_value=(FIXTURE.split("BEGIN:VEVENT", 2)[0] +
        "BEGIN:VEVENT\r\nUID:other-source\r\nSUMMARY:City Winery Tour + Tasting\r\n"
        "DTSTART;TZID=UTC:20261010T150000\r\nLOCATION:City Winery\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n", "200")), \
     patch.object(ICS, "datetime", AuditClock), \
     patch.object(ICS, "make_location_geocoder", side_effect=fake_geocoder):
    unrelated_store = Store()
    assert ICS.ingest_ics(unrelated_store, None, other_source) == 1
unrelated = unrelated_store.rows[0]
assert unrelated.start_utc == "2026-10-10T15:00:00Z"
assert unrelated.source_details is None

with patch.object(ICS, "_fetch_ics", return_value=(FIXTURE.replace("UID:ponce-oct10\r\n", ""), "200")), \
     patch.object(ICS, "datetime", AuditClock), \
     patch.object(ICS, "make_location_geocoder", side_effect=fake_geocoder):
    missing_uid = Store()
    ICS.ingest_ics(missing_uid, None, source)
assert missing_uid.rows[0].source_id == target.fingerprint

# utc_is_wall_clock: a feed that stamps its local wall clock as UTC (Nottingham
# Carers Hub's TZID=UTC, osmcal.org's Z) is read as floating local time, which
# the sync zones from the row's coordinates. Same date, so the same identity.
WALL = "\r\n".join((
    "BEGIN:VCALENDAR",
    "BEGIN:VEVENT", "UID:wall-tzid-utc", "SUMMARY:Quilting & Textile Group",
    "DTSTART;TZID=UTC:20261005T130000", "DTEND;TZID=UTC:20261005T150000",
    "LOCATION:Nottingham Women's Centre", "END:VEVENT",
    "BEGIN:VEVENT", "UID:wall-z", "SUMMARY:Hamburger Mappertreffen",
    "DTSTART:20261013T190000Z", "LOCATION:Variable, Karolinenstrasse 23", "END:VEVENT",
    "BEGIN:VEVENT", "UID:wall-real-zone", "SUMMARY:State of the Map Asia",
    "DTSTART;TZID=Asia/Tokyo:20261006T100000", "LOCATION:Tokyo", "END:VEVENT",
    "BEGIN:VEVENT", "UID:wall-date", "SUMMARY:Open day",
    "DTSTART;VALUE=DATE:20261007", "LOCATION:Somewhere", "END:VEVENT",
    "END:VCALENDAR", "",
))


def ingest_wall(src):
    with patch.object(ICS, "_fetch_ics", return_value=(WALL, "200")), \
         patch.object(ICS, "datetime", AuditClock), \
         patch.object(ICS, "make_location_geocoder", side_effect=fake_geocoder):
        s = Store()
        assert ICS.ingest_ics(s, None, src) == 4
    return {row.source_id: row for row in s.rows}


plain = ingest_wall({"name": "wall plain", "url": "https://example.test/wall.ics"})
wall = ingest_wall({"name": "wall flagged", "url": "https://example.test/wall.ics",
                    "utc_is_wall_clock": True})
assert (plain["wall-tzid-utc"].start_utc, plain["wall-z"].start_utc) == (
    "2026-10-05T13:00:00Z", "2026-10-13T19:00:00Z")          # unflagged: unchanged
row = wall["wall-tzid-utc"]
assert (row.start_local, row.start_utc, row.end_local, row.end_utc) == (
    "2026-10-05T13:00:00", None, "2026-10-05T15:00:00", None), (
    row.start_local, row.start_utc, row.end_local, row.end_utc)
assert (wall["wall-z"].start_local, wall["wall-z"].start_utc) == ("2026-10-13T19:00:00", None)
assert wall["wall-real-zone"].start_utc == plain["wall-real-zone"].start_utc == "2026-10-06T01:00:00Z"
assert wall["wall-date"].start_local == "2026-10-07" and wall["wall-date"].start_utc is None
for uid in plain:
    assert wall[uid].fingerprint == plain[uid].fingerprint, uid  # identity does not move

# wall_clock_tzids: a platform's default zone over a group's own wall clock
# (Restarters' Europe/London on Repair Café Waremme, in Belgium). Only the named
# zone is read as floating; a real zone and a Z stamp keep their meaning.
DEFAULT_ZONE = "\r\n".join((
    "BEGIN:VCALENDAR",
    "BEGIN:VEVENT", "UID:waremme", "SUMMARY:Repair Café Waremme",
    "DTSTART;TZID=Europe/London:20261024T093000", "DTEND;TZID=Europe/London:20261024T123000",
    "LOCATION:Rue de Grand-Axhe 45, 4300 Waremme, Belgique", "END:VEVENT",
    "BEGIN:VEVENT", "UID:jette", "SUMMARY:Repair Café Jette",
    "DTSTART;TZID=Europe/Brussels:20261024T140000", "LOCATION:Jette", "END:VEVENT",
    "BEGIN:VEVENT", "UID:z", "SUMMARY:Online upload day",
    "DTSTART:20261025T100000Z", "LOCATION:Namur", "END:VEVENT",
    "END:VCALENDAR", "",
))
with patch.object(ICS, "_fetch_ics", return_value=(DEFAULT_ZONE, "200")), \
     patch.object(ICS, "datetime", AuditClock), \
     patch.object(ICS, "make_location_geocoder", side_effect=fake_geocoder):
    s = Store()
    assert ICS.ingest_ics(s, None, {"name": "restarters be", "url": "https://example.test/be.ics",
                                    "wall_clock_tzids": ["Europe/London"]}) == 3
zoned = {row.source_id: row for row in s.rows}
assert (zoned["waremme"].start_local, zoned["waremme"].start_utc, zoned["waremme"].end_local) == (
    "2026-10-24T09:30:00", None, "2026-10-24T12:30:00"), zoned["waremme"].start_local
assert zoned["jette"].start_utc == "2026-10-24T12:00:00Z"            # a real zone is kept
assert zoned["z"].start_utc == "2026-10-25T10:00:00Z"                # Z is UTC unless utc_is_wall_clock

# Communico's exact Norman Mayer room LOCATION gets its published postal
# address and uses the known branch pin, while wall-clock times and event
# identity remain as supplied. The same rule applies across titles at this room;
# another library branch remains untouched.
NEW_ORLEANS = "\r\n".join((
    "BEGIN:VCALENDAR",
    "BEGIN:VEVENT", "UID:storytime-oct13", "SUMMARY:Storytime",
    "DTSTART;TZID=America/Chicago:20261013T103000",
    "DTEND;TZID=America/Chicago:20261013T113000",
    "LOCATION:Norman Mayer Library - Children's Area",
    "GEO:29.997569;-90.0615029", "END:VEVENT",
    "BEGIN:VEVENT", "SUMMARY:Toddler Play",
    "DTSTART;TZID=America/Chicago:20261020T103000",
    "LOCATION:Norman Mayer Library - Children's Area",
    "GEO:29.997569;-90.0615029", "END:VEVENT",
    "BEGIN:VEVENT", "UID:other-branch", "SUMMARY:Storytime",
    "DTSTART;TZID=America/Chicago:20261013T103000",
    "LOCATION:Algiers Regional Library - Children's Area",
    "GEO:29.949;-90.066", "END:VEVENT",
    "END:VCALENDAR", "",
))
new_orleans_source = next(s for s in config if s["name"].startswith("New Orleans Public Library - East ") and "Norman Mayer" in s["name"])
geocode_calls.clear()
with patch.object(ICS, "_fetch_ics", return_value=(NEW_ORLEANS, "200")), \
     patch.object(ICS, "datetime", AuditClock), \
     patch.object(ICS, "make_location_geocoder", side_effect=fake_geocoder):
    store = Store()
    assert ICS.ingest_ics(store, None, new_orleans_source) == 3
new_orleans = {row.source_id: row for row in store.rows}
storytime = new_orleans["storytime-oct13"]
storytime_location = "Norman Mayer Library - Children's Area"
assert storytime.source_id == "storytime-oct13"
assert (storytime.start_local, storytime.start_utc, storytime.end_local, storytime.end_utc) == (
    "2026-10-13T10:30:00-05:00", "2026-10-13T15:30:00Z",
    "2026-10-13T11:30:00-05:00", "2026-10-13T16:30:00Z",
), (storytime.start_local, storytime.start_utc, storytime.end_local, storytime.end_utc)
assert storytime.fingerprint == make_fingerprint("Storytime", "2026-10-13", storytime_location)
assert (storytime.venue_name, storytime.address, storytime.city, storytime.region,
        storytime.postal_code, storytime.country, storytime.latitude, storytime.longitude) == (
    storytime_location, "3001 Gentilly Boulevard", "New Orleans", "LA", "70122", "US",
    29.997569, -90.0615029,
)
assert storytime.source_details is None
toddler = new_orleans[make_fingerprint("Toddler Play", "2026-10-20", storytime_location)]
assert (toddler.address, toddler.city, toddler.region, toddler.postal_code,
        toddler.latitude, toddler.longitude) == (
    "3001 Gentilly Boulevard", "New Orleans", "LA", "70122", 29.997569, -90.0615029,
)
other_branch = new_orleans["other-branch"]
assert (other_branch.venue_name, other_branch.address, other_branch.city,
        other_branch.region, other_branch.postal_code, other_branch.latitude,
        other_branch.longitude) == (
    "Algiers Regional Library - Children's Area", None, None, None, None, 29.949, -90.066,
)
assert geocode_calls == [], geocode_calls

# Seattle City Calendar carries the city's governance sessions beside its
# programme: about 142 of 461 future VEVENTs on 2026-10-23, 7 of the 11 seen on
# the map zero-length 'Select Budget Committee' rows. skip_title plus the civic
# rules (switched on by its civic: _found) refuse them; the programme stays.
import re
seattle = [s for s in json.loads(Path("ics_sources.json").read_text(encoding="utf-8"))
           if s.get("name") == "Seattle City Calendar"]
assert len(seattle) == 1, seattle
seattle = seattle[0]
assert str(seattle.get("_found", "")).startswith("civic:"), seattle.get("_found")
seattle_skip = re.compile(seattle["skip_title"], re.I)

def seattle_refuses(title):
    return bool(seattle_skip.search(title) or ICS.CIVIC_TITLE_RX.search(title)
                or ICS.CIVIC_HOLIDAY_RX.match(title.strip()))

for title in ("Select Budget Committee", "Council Briefing", "Seattle Transit Advisory Board",
              "Construction Codes Advisory Board", "Landmarks Preservation Board"):
    assert seattle_refuses(title), title
for title in ("Waterfront Park Firepit Socials", "Green Lake Litter Patrol",
              "Dia de los Muertos Celebration", "Family Movie Night"):
    assert not seattle_refuses(title), title

print("ICS source overrides passed")
