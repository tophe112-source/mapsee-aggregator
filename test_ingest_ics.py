"""Focused regression checks for the ICS importer's durable checkpoints."""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mapsee_ingest_ics as ICS


fails = []


def check(label, condition, detail=""):
    print(f"{'ok  ' if condition else 'FAIL'} {label}{'' if condition else '   ' + str(detail)}")
    if not condition:
        fails.append(label)


class Store:
    def __init__(self, _path):
        self.records = {}
        self.saves = 0

    def save(self):
        self.saves += 1


sources = [
    {"name": "good", "url": "https://example.test/good.ics"},
    {"name": "bad", "url": "https://example.test/bad.ics"},
]
store = Store("unused.json")
cache_saves = []


def ingest(_store, _session, src, **_budget):
    if src["name"] == "bad":
        raise RuntimeError("feed refused")
    return 3


with patch.object(ICS.json, "loads", return_value=sources), \
     patch.object(ICS.requests, "Session", create=True), \
     patch.object(ICS, "EventStore", return_value=store), \
     patch.object(ICS, "ingest_ics", side_effect=ingest), \
     patch.object(ICS, "_save_geo_cache", side_effect=lambda _cache: cache_saves.append("geo")), \
     patch.object(ICS, "_save_feed_cache", side_effect=lambda _cache: cache_saves.append("feed")), \
     patch.object(ICS, "_load_cursor", return_value={}), \
     patch.object(ICS, "_save_cursor"), \
     patch("builtins.open"):
    rc = ICS.main(["--config", "unused.json", "--store", "unused-store.json"])

check("main succeeds when one source fails", rc == 0, rc)
check("event store checkpoints after every attempted source", store.saves == 2, store.saves)
check("both caches checkpoint after every attempted source",
      cache_saves == ["geo", "feed", "geo", "feed"], cache_saves)


# --- a source's `venue` pins the VEVENT that names no place at all -----------
# A neighbourhood council's annual yard sale has no LOCATION (it is the whole
# neighbourhood), and it was the one event on such a calendar that got dropped.
class VenueStore:
    def __init__(self):
        self.rows = []

    def upsert(self, ev):
        self.rows.append(ev)
        return ev.source_id


VENUE_ICS = (
    "BEGIN:VCALENDAR\r\n"
    "BEGIN:VEVENT\r\nUID:sale\r\nSUMMARY:Montlake Yard Sale\r\nDTSTART:20991010T170000Z\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nUID:meet\r\nSUMMARY:Board meeting\r\nDTSTART:20991011T170000Z\r\n"
    "LOCATION:Nowhere Hall\r\nEND:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)
VENUE = {"name": "Montlake neighborhood", "city": "Seattle", "region": "WA",
         "country": "US", "lat": 47.6414, "lon": -122.303}
geocode_calls = []


def _no_hit_geocoder(_session, _suffix):
    def geocode(loc):
        geocode_calls.append(loc)
        return None, None
    return geocode


with patch.object(ICS, "_fetch_ics", return_value=(VENUE_ICS, "200")), \
     patch.object(ICS, "make_location_geocoder", side_effect=_no_hit_geocoder):
    vstore = VenueStore()
    kept = ICS.ingest_ics(vstore, None, {"name": "montlake", "url": "x", "venue": VENUE})
    row = vstore.rows[0] if vstore.rows else None
    check("venue pins the VEVENT with no LOCATION", kept == 1 and row is not None
          and row.name == "Montlake Yard Sale" and row.latitude == 47.6414
          and row.longitude == -122.303 and row.venue_name == "Montlake neighborhood"
          and row.city == "Seattle" and row.region == "WA", (kept, row))
    check("a LOCATION that fails to geocode is still dropped, not pinned to the venue",
          geocode_calls == ["Nowhere Hall"] and len(vstore.rows) == 1,
          (geocode_calls, len(vstore.rows)))
    vstore = VenueStore()
    kept = ICS.ingest_ics(vstore, None, {"name": "montlake", "url": "x"})
    check("without venue the same VEVENT is unplaceable", kept == 0, kept)
    vstore = VenueStore()
    kept = ICS.ingest_ics(vstore, None, {"name": "montlake", "url": "x", "venue": {"name": "no pin"}})
    check("a venue without coordinates is ignored", kept == 0, kept)


if fails:
    raise SystemExit(f"{len(fails)} ICS test(s) failed: {', '.join(fails)}")
print("all ICS tests passed")
