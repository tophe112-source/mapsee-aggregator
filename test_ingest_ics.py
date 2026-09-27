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


# --- several feeds on one host wait out that host's Crawl-delay ---------------
# LibCal libraries publish a calendar per branch, and their robots.txt asks for
# 10 s between requests. Nine branch feeds used to be nine back-to-back GETs.
paced_sources = [
    {"name": "branch A", "url": "https://lib.example.test/ical_subscribe.php?cid=1", "crawl_delay": 10},
    {"name": "branch B", "url": "https://lib.example.test/ical_subscribe.php?cid=2"},
    {"name": "elsewhere", "url": "https://city.example.test/cal.ics"},
    {"name": "branch C", "url": "https://LIB.example.test/ical_subscribe.php?cid=3"},
]
clock = [100.0]
sleeps = []
order = []


def _paced_ingest(_store, _session, src, **_budget):
    order.append((src["name"], clock[0]))
    clock[0] += 1.0                      # each fetch takes a second of wall clock
    return 1


def _sleep(s):
    sleeps.append(round(s, 3))
    clock[0] += s


with patch.object(ICS.json, "loads", return_value=paced_sources), \
     patch.object(ICS.requests, "Session", create=True), \
     patch.object(ICS, "EventStore", return_value=Store("unused.json")), \
     patch.object(ICS, "ingest_ics", side_effect=_paced_ingest), \
     patch.object(ICS, "_save_geo_cache"), patch.object(ICS, "_save_feed_cache"), \
     patch.object(ICS, "_load_cursor", return_value={}), patch.object(ICS, "_save_cursor"), \
     patch.object(ICS.time, "monotonic", side_effect=lambda: clock[0]), \
     patch.object(ICS.time, "sleep", side_effect=_sleep), \
     patch("builtins.open"):
    rc = ICS.main(["--config", "unused.json", "--store", "unused-store.json"])

starts = dict(order)
check("paced run succeeds and reads every feed", rc == 0 and len(order) == 4, (rc, order))
check("the second feed on a host waits out the delay a sibling declared",
      starts["branch B"] - starts["branch A"] >= 10, order)
check("a feed on another host does not wait", sleeps.count(0) == 0 and len(sleeps) == 2, sleeps)
check("the host match ignores case, and the wait counts time already spent elsewhere",
      starts["branch C"] - starts["branch B"] >= 10 and sleeps[1] < 10, (order, sleeps))
check("the delay table takes the longest delay per host",
      ICS._crawl_delays([{"url": "https://a.test/x", "crawl_delay": 2},
                         {"url": "https://a.test/y", "crawl_delay": "10"},
                         {"url": "https://b.test/z"}]) == {"a.test": 10.0})

# ...and a wait that would outlast the run's budget ends the run instead.
clock[0] = 100.0
order.clear()
sleeps.clear()
with patch.object(ICS.json, "loads", return_value=paced_sources[:2]), \
     patch.object(ICS.requests, "Session", create=True), \
     patch.object(ICS, "EventStore", return_value=Store("unused.json")), \
     patch.object(ICS, "ingest_ics", side_effect=_paced_ingest), \
     patch.object(ICS, "_save_geo_cache"), patch.object(ICS, "_save_feed_cache"), \
     patch.object(ICS, "_load_cursor", return_value={}), patch.object(ICS, "_save_cursor"), \
     patch.object(ICS.time, "monotonic", side_effect=lambda: clock[0]), \
     patch.object(ICS.time, "sleep", side_effect=_sleep), \
     patch("builtins.open"):
    rc = ICS.main(["--config", "unused.json", "--store", "unused-store.json", "--max-minutes", "0.1"])
check("a crawl-delay wait longer than the budget stops the run before the fetch",
      rc == 0 and [n for n, _ in order] == ["branch A"] and not sleeps, (order, sleeps))


if fails:
    raise SystemExit(f"{len(fails)} ICS test(s) failed: {', '.join(fails)}")
print("all ICS tests passed")
