#!/usr/bin/env python3
"""Standing-program rows stay stable and reuse the existing recurring roller."""
import json
import os
import sys
import tempfile
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

os.environ["MAPSEE_TODAY"] = "20261001"
os.environ["GEOCODE_CACHE"] = str(Path(tempfile.gettempdir()) / "mapsee-programs-test-geocache.json")
import mapsee_ingest_programs as P  # noqa: E402
from mapsee_ingest import EventStore  # noqa: E402
from mapsee_supabase_sync import build_rows  # noqa: E402

checks = []


def check(condition, label):
    checks.append((bool(condition), label))


class NoNetwork:
    def get(self, *args, **kwargs):
        raise AssertionError("standing sites with fixed coordinates must not be geocoded")


vmfa = {
    "name": "Virginia Museum of Fine Arts Free Admission",
    "standing": True,
    "timezone": "America/New_York",
    "category": "arts",
    "title_prefix": "Free museum visit",
    "blurb": "General admission is always free for everyone.",
    "url": "https://www.vmfa.museum/about-us",
    "admission": 0,
    "sites": [{
        "name": "Virginia Museum of Fine Arts",
        "address": "200 N Arthur Ashe Boulevard, Richmond, VA 23220",
        "city": "Richmond", "region": "Virginia", "country": "United States",
        "lat": 37.5560585, "lon": -77.4748956,
        "_coords": "osm:way/146689449 (Nominatim exact-name museum polygon)",
        "recurring_days": {
            "Monday": ["10:00", "17:00"], "Tuesday": ["10:00", "17:00"],
            "Wednesday": ["10:00", "21:00"], "Thursday": ["10:00", "21:00"],
            "Friday": ["10:00", "21:00"], "Saturday": ["10:00", "17:00"],
            "Sunday": ["10:00", "17:00"],
        },
        "notes": "Open 365 days a year. Sat–Tue 10 am–5 pm; Wed–Fri 10 am–9 pm. "
                 "Usual hours; check the official page for any exceptional changes.",
    }],
}

first = P.program_events(vmfa, NoNetwork())
check(len(first) == 1, "one venue emits one standing record")
if first:
    e = first[0]
    expected = {"0": ["10:00", "17:00"], "1": ["10:00", "17:00"],
                "2": ["10:00", "21:00"], "3": ["10:00", "21:00"],
                "4": ["10:00", "21:00"], "5": ["10:00", "17:00"],
                "6": ["10:00", "17:00"]}
    check(e.recurring_days == expected, "weekday hours map to the roller's 0=Mon…6=Sun keys")
    check(e.start_local == "2026-10-01T10:00:00-04:00" and e.end_local == "2026-10-01T21:00:00-04:00",
          "first open window is local Thursday hours")
    check(e.start_utc == "2026-10-01T14:00:00Z" and e.end_utc == "2026-10-02T01:00:00Z",
          "initial UTC bounds use the declared Eastern timezone")
    check(e.coords_exact, "surveyed museum coordinates opt out of sync geocoding")
    check(e.source_details == {"free": True, "offer": {"price": "0", "url": vmfa["url"]}},
          "explicit configured zero admission is normalized by the shared admission helper")
    check(e.description.startswith("Free to attend."), "verified zero admission reaches row wording")
    first_id = e.fingerprint
else:
    first_id = None

os.environ["MAPSEE_TODAY"] = "20261002"
second = P.program_events(vmfa, NoNetwork())
check(len(second) == 1 and second[0].fingerprint == first_id,
      "a new date keeps one stable identity instead of adding a daily copy")
check(second[0].start_local == "2026-10-02T10:00:00-04:00" if second else False,
      "initial window advances to Friday in the declared timezone")

with tempfile.TemporaryDirectory(prefix="mapsee-standing-program-") as temp:
    path = Path(temp) / "events.json"
    store = EventStore(str(path))
    for event in first + second:
        store.upsert(event)
    store.save()
    rows = build_rows(str(path), "test-host")
    check(len(store.records) == 1 and len(rows) == 1,
          "two dry-runs upsert as one event and one sync row")
    if rows:
        recurring = rows[0]["recurring_hours"]
        check(recurring == {"tz": "America/New_York", "days": expected},
              "build_rows emits timezone-aware weekly hours for the DB roller")

os.environ["MAPSEE_TODAY"] = "20261001"
kansas_city = {
    "name": "Kansas City dated museum hours", "timezone": "America/Chicago",
    "days": "Thursday", "horizon_days": 1, "title_prefix": "Free museum visit",
    "blurb": "Free admission for everyone.", "admission": 0,
    "sites": [{"name": "Nelson-Atkins Museum of Art",
                "address": "4525 Oak Street, Kansas City, MO 64111",
                "city": "Kansas City", "region": "Missouri", "country": "United States",
                "lat": 39.045865206378, "lon": -94.581999251442, "coords_exact": True,
                "start": "10:00", "end": "21:00"}],
}
kc_events = P.program_events(kansas_city, NoNetwork())
check(len(kc_events) == 1 and kc_events[0].start_utc == "2026-10-01T15:00:00Z"
      and kc_events[0].end_utc == "2026-10-02T02:00:00Z",
      "Kansas City 10 am–9 pm uses Central Daylight Time")
with tempfile.TemporaryDirectory(prefix="mapsee-kc-program-") as temp:
    path = Path(temp) / "events.json"
    store = EventStore(str(path))
    store.upsert(kc_events[0])
    store.save()
    rows = build_rows(str(path), "test-host")
    check(len(rows) == 1 and rows[0]["starts_at"] == "2026-10-01T15:00:00Z"
          and rows[0]["ends_at"] == "2026-10-02T02:00:00Z",
          "build_rows preserves the correct Kansas City local window")


def fails_closed(program):
    try:
        P.program_events(program, NoNetwork())
    except (TypeError, ValueError):
        return True
    return False


check(fails_closed(dict(vmfa, timezone="")), "missing declared timezone fails closed")
check(fails_closed(dict(vmfa, timezone="Mars/Phobos")), "invalid declared timezone fails closed")
check(fails_closed(dict(vmfa, timezone="America/Chicago")), "timezone inconsistent with fixed coordinates fails closed")
check(fails_closed(dict(vmfa, standing="true")), "non-boolean standing mode fails closed")
check(fails_closed(dict(vmfa, days="Monday")), "dated weekday rules are rejected in standing mode")
check(fails_closed(dict(vmfa, nth=1)), "monthly rules are rejected in standing mode")
check(fails_closed(dict(vmfa, sites=[dict(vmfa["sites"][0], exclude_dates=["2026-12-25"])])),
      "one-off closure rules are rejected instead of pretending the weekly roller supports them")
check(fails_closed(dict(vmfa, sites=[dict(vmfa["sites"][0], recurring_days={})])),
      "missing weekly hours fails closed")
check(fails_closed(dict(vmfa, sites=[dict(vmfa["sites"][0], recurring_days={"Friday": ["21:00", "10:00"]})])),
      "closing before opening fails closed")
check(fails_closed(dict(vmfa, sites=[dict(vmfa["sites"][0], lat=None)])),
      "standing site without fixed coordinates fails closed")
without_price = {key: value for key, value in vmfa.items() if key != "admission"}
without_price_events = P.program_events(without_price, NoNetwork())
check(len(without_price_events) == 1 and without_price_events[0].source_details is None,
      "omitting explicit admission evidence does not infer a free price")

os.environ["MAPSEE_TODAY"] = "20261002"
legacy = {
    "name": "Legacy dated program", "days": "Friday", "horizon_days": 1,
    "timezone": "America/New_York", "title_prefix": "Legacy", "blurb": "Public art visit",
    "sites": [{"name": "Venue", "address": "1 Main St", "city": "Richmond",
                "region": "Virginia", "country": "United States", "lat": 37.55, "lon": -77.47}],
}
old_rows = P.program_events(legacy, NoNetwork())
check(len(old_rows) == 1 and old_rows[0].recurring_days is None,
      "omitting standing preserves the existing dated-event path")
check(not old_rows[0].coords_exact, "dated programs keep their existing geocode behavior unless explicitly opted out")
check(old_rows[0].source_details is None, "dated programs do not infer admission without an explicit fact")
exact_legacy = dict(legacy, sites=[dict(legacy["sites"][0], coords_exact=True)])
exact_rows = P.program_events(exact_legacy, NoNetwork())
check(len(exact_rows) == 1 and exact_rows[0].coords_exact,
      "dated programs pass through the explicit coords_exact source-coordinate flag")
priced_legacy = dict(legacy, admission=0)
priced_rows = P.program_events(priced_legacy, NoNetwork())
check(len(priced_rows) == 1 and priced_rows[0].source_details == {
    "free": True, "offer": {"price": "0"}},
      "dated programs normalize only an explicit configured zero through the shared helper")

visit_program = dict(priced_legacy, listing_type="visit_window")
visit_rows = P.program_events(visit_program, NoNetwork())
check(len(visit_rows) == 1 and visit_rows[0].source_details == {
    "free": True, "offer": {"price": "0"}, "listing_type": "visit_window"},
      "visit_window is merged with verified admission facts")
if visit_rows and priced_rows:
    visit = visit_rows[0]
    baseline = priced_rows[0]
    check((visit.fingerprint, visit.start_local, visit.start_utc, visit.latitude, visit.longitude,
           visit.address, visit.name) ==
          (baseline.fingerprint, baseline.start_local, baseline.start_utc, baseline.latitude,
           baseline.longitude, baseline.address, baseline.name),
          "visit_window leaves dated event identity, time, place and coordinates unchanged")
    with tempfile.TemporaryDirectory(prefix="mapsee-visit-window-") as temp:
        path = Path(temp) / "events.json"
        store = EventStore(str(path))
        store.upsert(visit)
        store.save()
        built = build_rows(str(path), "test-host", geo_session=None)
        check(len(store.records) == 1 and len(built) == 1
              and built[0]["source_details"] == {
                  "free": True, "offer": {"price": "0"}, "listing_type": "visit_window"}
              and built[0]["external_id"] == baseline.fingerprint
              and (built[0]["lat"], built[0]["lon"], built[0]["street_address"]) ==
                  (baseline.latitude, baseline.longitude, baseline.address),
              "EventStore and build_rows preserve the marker, offer, identity and place")

without_admission = dict(legacy, listing_type="visit_window")
without_admission_rows = P.program_events(without_admission, NoNetwork())
check(len(without_admission_rows) == 1 and without_admission_rows[0].source_details == {
    "listing_type": "visit_window"},
      "visit_window can be persisted without inventing admission facts")
for invalid in (None, "event", "Place", {}, True):
    check(fails_closed(dict(legacy, listing_type=invalid)),
          f"invalid listing_type {invalid!r} fails closed")

new_visit_configs = {
    "Cleveland Museum of Art Free Permanent Collection",
    "Nelson-Atkins Museum of Art Free Admission",
    "Virginia Museum of Fine Arts Free General Admission",
    "Free self-guided visit to Elizabeth Fort",
    "Free visit to Galway City Museum",
    "Free visit to Christchurch Art Gallery",
    "MUSA Guadalajara free museum visit",
    "Museo del Palacio free exhibition visit",
    "FUGA free exhibition visit: Made in Hungary",
    "Darshan Museum Pune free public visit",
}
established_visit_configs = {
    "Domenica al museo",
    "Centre des monuments nationaux: premier dimanche",
    "Musées nationaux à Paris: premier dimanche",
    "Museums on Us",
}
expected_visit_configs = new_visit_configs | established_visit_configs
live_programs = json.loads(Path("program_sources.json").read_text(encoding="utf-8"))
actual_visit_configs = {p.get("name") for p in live_programs if p.get("listing_type") == "visit_window"}
check(actual_visit_configs == expected_visit_configs,
      "the fourteen curated museum, fort and gallery visits opt into visit_window")
unmarked_event_configs = {"Seattle Free Summer Meals", "Free Guided Gallery Tour: Tour for Tots",
                          "Tauranga Civic Choir", "Harp over the Harbour"}
check(all("listing_type" not in p for p in live_programs if p.get("name") in unmarked_event_configs),
      "meals, a scheduled gallery tour and performances retain Event defaults")
check(all(p.get("listing_type") == "visit_window" for p in live_programs
          if p.get("name") in established_visit_configs),
      "the four existing free-admission calendars opt into visit_window")

tagged_events = [event for program in live_programs if program.get("name") in expected_visit_configs
                 for event in P.program_events(program, NoNetwork())]
check(all(any(event.source_details and event.source_details.get("listing_type") == "visit_window"
              for event in tagged_events if event.source.startswith(
                  "program:" + program["name"].lower().replace(" ", "-")))
          for program in live_programs if program.get("name") in expected_visit_configs),
      "each of the fourteen live visit configs emits a tagged row")
new_tagged_events = [event for program in live_programs if program.get("name") in new_visit_configs
                     for event in P.program_events(program, NoNetwork())]
check(all(event.source_details.get("free") is True
          and event.source_details.get("offer", {}).get("price") == "0"
          for event in new_tagged_events),
      "the ten new configs retain their explicit zero-price evidence")
with tempfile.TemporaryDirectory(prefix="mapsee-live-visit-windows-") as temp:
    path = Path(temp) / "events.json"
    store = EventStore(str(path))
    for event in tagged_events:
        store.upsert(event)
    store.save()
    built = build_rows(str(path), "test-host", geo_session=None)
    check(len(store.records) == len(tagged_events) == len(built)
          and all(row["source_details"].get("listing_type") == "visit_window" for row in built),
          "all live visit records keep cardinality and listing metadata through build_rows")

standing_visit = P.program_events(dict(vmfa, listing_type="visit_window"), NoNetwork())
check(len(standing_visit) == 1 and standing_visit[0].recurring_days == first[0].recurring_days
      and standing_visit[0].source_details == {
          "free": True, "offer": {"price": "0", "url": vmfa["url"]},
          "listing_type": "visit_window"},
      "the VMFA standing row retains weekly hours and admission alongside its visit marker")

failed = [label for ok, label in checks if not ok]
for ok, label in checks:
    print(("ok  " if ok else "FAIL") + "  " + label)
print(f"\n{len(checks) - len(failed)}/{len(checks)} passed")
sys.exit(1 if failed else 0)
