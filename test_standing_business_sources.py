"""Source-scoped business-window exclusions run through the real feed readers.

The 2026-11-03 partial census found 409 source-proven daily business/service
copies across five parents. Offers are not their own venues. Keep actual games,
dated variations and matching words on other sources. No network or database.
"""
import io
import json
import tempfile
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import mapsee_ingest_ics as ICS
import mapsee_ingest_tribe as TRIBE
import catalog_curate as CATALOG

ROOT = Path(__file__).parent
TRIBE_SOURCES = json.loads((ROOT / "tribe_sources.json").read_text(encoding="utf-8"))["sites"]
ICS_SOURCES = json.loads((ROOT / "ics_sources.json").read_text(encoding="utf-8"))
CASES = {
    "Gladesville RSL": ["2 for $32.50 Cocktails", "Beer Bucket Deal", "Glades Asian Platter"],
    "Cabramatta Bowling Club": ["Dine At Ocean Treasure", "Just Pho You!", "Members 25% Off", "Courtesy Bus"],
    "Royal St George Yacht Club": ["Catering Options", "Cafe + Bar"],
}
# The first ten titles are real games/events returned by these publishers.
KEEP = ["Bingo", "Mahjong", "Trivia Thursday", "Poker", "Happy Hours",
        "$16.5 Classic Cocktails", "Pick the Jokers", "Sunday Raffles",
        "Trivia", "Midweek Megastack", "Catering Options for Christmas Dinner",
        "Beer Bucket Deal - New Year's Eve", "Dine At Ocean Treasure Anniversary Dinner"]
checks = 0


def check(label, condition):
    global checks
    assert condition, label
    checks += 1


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        fixed = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)
        return fixed.astimezone(tz) if tz else fixed.replace(tzinfo=None)


class Session:
    def __init__(self, events):
        self.events = events
        self.urls = []

    def get(self, url, **_kwargs):
        self.urls.append(url)
        events = self.events

        class Response:
            status_code = 200

            def json(self):
                return {"events": events, "total_pages": 1}

        return Response()


def tribe_rows(titles, source):
    return [{"id": i + 1, "title": title, "start_date": "2026-11-04 00:00:00",
             "end_date": "2026-11-04 23:59:59", "all_day": True,
             "url": source["base_url"] + f"/event/fixture-{i}/", "venue": [],
             "description": "", "timezone": "UTC", "categories": []}
            for i, title in enumerate(titles)]


all_standing = [title for titles in CASES.values() for title in titles]
for name, excluded in CASES.items():
    source = next(s for s in TRIBE_SOURCES if s["name"] == name)
    session = Session(tribe_rows(all_standing + KEEP, source))
    with patch.object(TRIBE, "datetime", Clock), redirect_stdout(io.StringIO()) as log:
        events = TRIBE.ingest_site(session, source)
    names = [e.name for e in events]
    for title in excluded:
        check(f"{name}: excludes exact standing title {title}", title not in names)
    for title in KEEP:
        check(f"{name}: keeps occasion {title}", title in names)
    for title in set(all_standing) - set(excluded):
        check(f"{name}: other publisher's title has no global exclusion: {title}", title in names)
    check(f"{name}: only exact configured titles removed", len(events) == len(all_standing) + len(KEEP) - len(excluded))
    check(f"{name}: source API remains scoped to its configured host",
          session.urls == [source["base_url"] + "/wp-json/tribe/events/v1/events"])
    check(f"{name}: original publisher identity is retained",
          all(e.admission_publisher == source["base_url"].split("://", 1)[1] for e in events))
    check(f"{name}: filtered rows counted", f"{len(excluded)} title-filtered" in log.getvalue())

# A real unrelated configured publisher still reads exactly the same titles.
other = next(s for s in TRIBE_SOURCES if s["name"] == "Manly Warringah Rugby League Club")
with patch.object(TRIBE, "datetime", Clock), redirect_stdout(io.StringIO()):
    events = TRIBE.ingest_site(Session(tribe_rows(all_standing, other)), other)
check("unrelated Tribe source retains all nine same-title rows", [e.name for e in events] == all_standing)


class Store:
    def __init__(self):
        self.rows = []

    def upsert(self, event):
        self.rows.append(event)
        return "new"


titles = ["City Sightseeing San Antonio", "City Sightseeing San Antonio Sunset Tour", "Family Bingo Night"]
fixture = "\r\n".join(["BEGIN:VCALENDAR"] + [
    line for i, title in enumerate(titles) for line in
    ("BEGIN:VEVENT", f"UID:fixture-{i}", f"SUMMARY:{title}",
     "DTSTART;VALUE=DATE:20261104", "DTEND;VALUE=DATE:20261105",
     "LOCATION:111 Alamo Plaza, San Antonio, TX 78205", "GEO:29.425;-98.486", "END:VEVENT")
] + ["END:VCALENDAR", ""])
source = next(s for s in ICS_SOURCES if s["name"] == "San Antonio City Calendar")
other_ics = next(s for s in ICS_SOURCES if s["name"] == "Seattle Parks and Recreation")
for selected, expected in [(source, titles[1:]), (other_ics, titles)]:
    store = Store()
    with patch.object(ICS, "datetime", Clock), \
            patch.object(ICS, "_fetch_ics", return_value=(fixture, "200")), \
            patch.object(ICS, "make_location_geocoder", return_value=lambda *_args: None), \
            redirect_stdout(io.StringIO()):
        kept = ICS.ingest_ics(store, None, selected)
    check(f"{selected['name']}: exact source-scoped sightseeing filter",
          kept == len(expected) and [e.name for e in store.rows] == expected)
    check(f"{selected['name']}: surviving source IDs unchanged",
          [e.source_id for e in store.rows] == [f"fixture-{titles.index(t)}" for t in expected])
    check(f"{selected['name']}: surviving dates unchanged",
          all(e.start_local == "2026-11-04" and e.end_local == "2026-11-05" for e in store.rows))

# The 2026-11-03 primary-calendar review matched 765 stored rows by exact
# publisher UID/title/local date and exclusive configured calendar ownership:
# 597 historical anniversaries and 168 billing/tax/testing reminders. These
# source-specific preventions must preserve real activity calendars and titles.
SIDNEY_MAIN = "https://www.sidneyoh.com/common/modules/iCalendar/iCalendar.aspx?catID=14&feed=calendar"
HISTORY = "https://www.anacorteswa.gov/common/modules/iCalendar/iCalendar.aspx?catID=40&feed=calendar"
ADMIN = ["Sidney Utility Bills Due", "4th Quarter Business Estimate Tax Due",
         "Extended Tax Returns Due", "Third Quarter Estimate Tax Payment Due",
         "Second Quarter Estimate Tax Payment Due", "Sidney Income Tax Return Due",
         "First Quarter Estimate Tax Payment Due", "SW3 and W2 Forms Due",
         "4th Quarter Individual Income Tax Estimates Due", "Alert System Test",
         "Test Smoke Detectors"]
ACTIVITIES = ["Utility Bill Help Workshop", "Test Smoke Detectors Workshop", "TNR Clinic",
              "Family Storytime", "Anacortes Farmers Market: Winter Markets",
              "Sidney Utility Bills Due Benefit Concert"]
admin_fixture = "\r\n".join(["BEGIN:VCALENDAR"] + [
    line for i, title in enumerate(ADMIN + ACTIVITIES) for line in
    ("BEGIN:VEVENT", f"UID:admin-review-{i}", f"SUMMARY:{title}",
     "DTSTART;VALUE=DATE:20261104", "DTEND;VALUE=DATE:20261105",
     "LOCATION:Sidney, Ohio", "GEO:40.2842;-84.1555", "END:VEVENT")
] + ["END:VCALENDAR", ""])
sidney = next(s for s in ICS_SOURCES if s["url"] == SIDNEY_MAIN)
for selected, expected in [(sidney, ACTIVITIES), (other_ics, ADMIN + ACTIVITIES)]:
    store = Store()
    with patch.object(ICS, "datetime", Clock), \
            patch.object(ICS, "_fetch_ics", return_value=(admin_fixture, "200")), \
            patch.object(ICS, "make_location_geocoder", return_value=lambda *_args: None), \
            redirect_stdout(io.StringIO()):
        kept = ICS.ingest_ics(store, None, selected)
    check(f"{selected['name']}: finite administrative exclusion stays source scoped",
          kept == len(expected) and [e.name for e in store.rows] == expected)
    for title in ACTIVITIES:
        check(f"{selected['name']}: retains longer/different actual activity {title}",
              title in [e.name for e in store.rows])
    check(f"{selected['name']}: retained identities and dates unchanged",
          [e.source_id for e in store.rows] == [f"admin-review-{(ADMIN + ACTIVITIES).index(t)}" for t in expected]
          and all(e.start_local == "2026-11-04" and e.end_local == "2026-11-05" for e in store.rows))

check("Anacortes History cat40 alone is no longer configured",
      not any(s.get("url") == HISTORY for s in ICS_SOURCES))
declined = CATALOG._declined_keys()
check("production declined-key loader sees the retired ICS URL",
      CATALOG._canon(HISTORY) in declined)
for cat in (32, 14, 43, 55):
    url = f"https://www.anacorteswa.gov/common/modules/iCalendar/iCalendar.aspx?catID={cat}&feed=calendar"
    check(f"Anacortes activity calendar cat{cat} remains configured",
          any(s.get("url") == url for s in ICS_SOURCES))
    check(f"Anacortes activity calendar cat{cat} is not declined",
          CATALOG._canon(url) not in declined)
for cat in (27, 29):
    url = f"https://www.sidneyoh.com/common/modules/iCalendar/iCalendar.aspx?catID={cat}&feed=calendar"
    retained = next(s for s in ICS_SOURCES if s.get("url") == url)
    check(f"Sidney activity calendar cat{cat} keeps its original filter behavior",
          not retained.get("skip_title"))

# Exercise the real merge with a stale previously-verified source. All writes
# are confined to this scratch directory; the live config files are never used
# as merge destinations. A ledger 'ok' cannot resurrect the retired feed.
with tempfile.TemporaryDirectory() as directory:
    temp = Path(directory)
    (temp / "ics_sources.json").write_text("[]", encoding="utf-8")
    decline_note = json.loads((ROOT / "jsonld_sources.json").read_text(encoding="utf-8"))["_not_included"][HISTORY]
    (temp / "jsonld_sources.json").write_text(json.dumps({"sites": [], "_not_included": {HISTORY: decline_note}}), encoding="utf-8")
    stale = [{"type": "ics", "name": "Anacortes History stale verified", "url": url}
             for url in (HISTORY, HISTORY.replace("https://www.", "http://"))]
    verified = temp / "verified.json"
    verified.write_text(json.dumps(stale), encoding="utf-8")
    with patch.object(CATALOG, "HERE", directory), \
            redirect_stdout(io.StringIO()) as merge_log:
        CATALOG.cmd_merge(str(verified))
    check("production merge refuses both stale verified URL variants",
          json.loads((temp / "ics_sources.json").read_text(encoding="utf-8")) == []
          and merge_log.getvalue().count("declined on the record") == 2)

print(f"PASS {checks} standing-business/admin source checks; no network or database writes")
