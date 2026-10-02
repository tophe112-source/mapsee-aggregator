"""Ponce City Winery's source-specific venue and timezone corrections."""
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

print("ICS source overrides passed")
