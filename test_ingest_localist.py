"""Does a Localist calendar arrive whole, and does the walk end where the API says?

It did not. ingest() read `range(1, 11)`: ten pages of 100, whatever the
calendar held. One pp=1 request per configured tenant on 2026-10-11 (days=90)
found 18 of 56 above that, 10,274 occurrence wraps never read. events.in.gov
had 3,099 over 31 pages and page 10 ended on 2026-10-26, so every Indiana DNR
hike, bird walk and First Day Hike after that date was missing, every night,
and the log said "kept 498" as if that were the calendar.

Pinned here, against wraps saved from events.dncr.nc.gov and events.in.gov
(trimmed to the fields the adapter reads) and a fake session that serves the
API's own `page` block:

  * a source's `max_pages` is honoured, and a source without one gets
    DEFAULT_MAX_PAGES, not ten;
  * the walk ends on the page the API names as the last (page.total), and a
    response without the block ends on its first short page, never asking for
    one more;
  * a walk the cap cuts short says how many pages it left;
  * every request asks for distinct=true and pp=100;
  * the publisher's Crawl-delay (1 s on all 57 tenants) is slept between pages;
  * a page that fails keeps the pages already read.

Reading Indiana to its end also read its town halls to their end: 69
council, commission and courthouse-holiday rows became 151 of its 870, all
on the community door, against 95 hike and walk titles. So, as in ics and
tribe:

  * a source whose `_found` starts with civic: refuses the town-hall phrases
    and the bare holiday, and a source without it refuses nothing;
  * `skip_title` refuses what its regex finds (NC DNCR's meetings), and the
    configured one keeps DNCR's programmed holiday;
  * a canceled meeting is still tombstoned, filter or no filter.

And what it costs. Localist's own `free` and `ticket_cost` were never read, so
a free hike reached ../mapsee's offer:free (0227, which reads only the title
and description) only if its blurb said so. Judged by 0227's own predicate on
the row the sync stores (offers_0227):

  * a flagged-free event leads with "Free to attend." and is tagged free, past
    the sync's 800-character cut;
  * a flagged-free event whose ticket_cost prices it, names who it is free
    for, or whose blurb charges a fee is NOT tagged, and a priced row whose
    blurb says FREE for some is no longer tagged;
  * an unflagged event gets no line and is NOT tagged;
  * a price is said as its amounts, so the line carries no discount or intro
    of its own; no row gains or loses one, or its fingerprint.

No network, no database.
"""
import copy
import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stdout
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mapsee_ingest_localist as L  # noqa: E402
from catalog_discover_osm import CIVIC_HOLIDAY_RX  # noqa: E402
from mapsee_ingest import EventStore  # noqa: E402

fails = []

# The API's page size ceiling, and the cap a source without max_pages gets
# (ten, read as range(1, 11), before there was a name for it).
PER_PAGE = 100
DEFAULT = getattr(L, "DEFAULT_MAX_PAGES", 10)


def check(label, cond, detail=""):
    print(f"{'ok  ' if cond else 'FAIL'} {label}{'' if cond else '   ' + str(detail)}")
    if not cond:
        fails.append(label)


# --------------------------------------------------------------------------- #
# fixtures, verbatim from the API on 2026-10-11 (description cut at 160 chars,
# fields the adapter does not read dropped)
# --------------------------------------------------------------------------- #
# events.dncr.nc.gov, days=90, pp=100, distinct=true: page 1 of 3.
NIGHT_HIKE = {"event": {
    "id": 53690431037426, "title": "In the Park After Dark - Night Hike", "status": "live",
    "first_date": "2026-10-16",
    "event_instances": [{"event_instance": {"id": 53690431037427,
                                            "start": "2026-10-16T19:00:00-04:00",
                                            "end": "2026-10-16T20:15:00-04:00", "all_day": False}}],
    "geo": {"latitude": "35.963597", "longitude": "-75.632255", "street": "300 West Carolista Drive",
            "city": "Nags Head", "state": "NC", "country": "US", "zip": "27959"},
    "location_name": "Jockey's Ridge State Park - Main Access and Visitor Center",
    "description_text": "Experience the park after dark!  Get out and take a fun and informative hike "
                        "to the ridge with Ranger Austin.  We will follow our newly built boardwalk "
                        "to the...",
    "filters": {"event_types": [{"name": "Guided Tours"}, {"name": "Outdoor & Recreation"},
                                {"name": "Nature & Wildlife Programs"}]},
    "photo_url": "https://localist-images.azureedge.net/photos/53690455518342/huge/82fea9a3d7d218ba5fc654ed8676ace1c63b5c02.jpg",
    "localist_url": "https://events.dncr.nc.gov/event/in-the-park-after-dark-night-hike-1198",
    "url": "", "ticket_cost": None, "free": False}}

# The same calendar WITHOUT distinct: "Bird Hike" arrives once per occurrence,
# on page 4 (2026-11-02) and again on page 7 (2026-12-07) of 8.
BIRD_HIKE_NOV = {"event": {
    "id": 51498322628188, "title": "Bird Hike", "status": "live", "first_date": "2026-01-05",
    "event_instances": [{"event_instance": {"id": 51498322639463,
                                            "start": "2026-11-02T09:00:00-05:00",
                                            "end": "2026-11-02T10:00:00-05:00", "all_day": False}}],
    "geo": {"latitude": "34.696366", "longitude": "-76.684506", "street": "2303 East Fort Macon Road",
            "city": "Atlantic Beach", "state": "NC", "country": "US", "zip": "28512"},
    "location_name": "Fort Macon State Park",
    "description_text": "Meet at the Visitor Center and take a leisurely hike to identify birds "
                        "native to the area.",
    "filters": {"event_types": [{"name": "Exhibits & Tours"}, {"name": "Guided Tours"}]},
    "photo_url": "https://localist-images.azureedge.net/photos/51498322692722/huge/a41ac61cdfa5b4f5292c4706ebe667b6fb19c9ec.jpg",
    "localist_url": "https://events.dncr.nc.gov/event/bird-hike-4104",
    "url": "", "ticket_cost": "", "free": False}}
BIRD_HIKE_DEC = copy.deepcopy(BIRD_HIKE_NOV)
BIRD_HIKE_DEC["event"]["event_instances"] = [{"event_instance": {
    "id": 51498322640488, "start": "2026-12-07T09:00:00-05:00",
    "end": "2026-12-07T10:00:00-05:00", "all_day": False}}]

DNCR = {"name": "NC Natural and Cultural Resources", "base_url": "https://events.dncr.nc.gov",
        "days": 90}

# events.in.gov, days=90: page 8 of 15 with distinct=true. Without it the
# calendar was 31 pages, and the old walk's page 10 ended at 08:00 that same
# morning, so this hike was never read.
MOON_HIKE = {"event": {
    "id": 52152346174485, "title": "October Moon Hike, Mounds State Park", "status": "live",
    "first_date": "2026-10-26",
    "event_instances": [{"event_instance": {"id": 52152346175510,
                                            "start": "2026-10-26T19:00:00-04:00",
                                            "end": "2026-10-26T20:00:00-04:00", "all_day": False}}],
    "geo": {"latitude": "40.095788", "longitude": "-85.620107", "street": "4306 Mounds Road",
            "city": "Anderson", "state": "IN", "country": "US", "zip": "46017"},
    "location_name": "Mounds State Park",
    "description_text": "Full Moon Hikes are moderate, 1-hour hikes that may involve stairs or rough "
                        "terrain. Leashed, well behaved dogs and cats welcome. Please only red "
                        "light...",
    "filters": {"event_types": [{"name": "Events"}]},
    "photo_url": "https://localist-images.azureedge.net/photos/52152256370714/huge/326c94c3ef9a610fe5900667825f260fe838c474.jpg",
    "localist_url": "https://events.in.gov/event/october-moon-hike-mounds-state-park",
    "url": "on.in.gov/moundssp", "ticket_cost": None, "free": False}}


# --------------------------------------------------------------------------- #
# a fake tenant
# --------------------------------------------------------------------------- #
class _Resp:
    def __init__(self, body, status=200):
        self.status_code = status
        self._body = body

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class Tenant:
    """Serves `pages` (lists of wraps) with the page block Localist sends:
    {"current", "size", "total" (pages), "total_items", "next_page",
    "previous_page"}. `fail_on` raises on that page, as a read timeout does."""

    def __init__(self, pages, *, block=True, fail_on=None):
        self.pages = pages
        self.block = block
        self.fail_on = fail_on
        self.calls = []

    def get(self, url, params=None, timeout=None, **kw):
        params = dict(params or {})
        self.calls.append(params)
        n = int(params.get("page") or 1)
        if n == self.fail_on:
            import requests
            raise requests.exceptions.ReadTimeout("Read timed out. (read timeout=25)")
        events = self.pages[n - 1] if n <= len(self.pages) else []
        body = {"events": events, "date": {"first": "2026-10-11", "last": "2027-01-09"}}
        if self.block:
            total = len(self.pages)
            body["page"] = {"current": n, "size": PER_PAGE, "total": total,
                            "total_items": sum(len(p) for p in self.pages),
                            "next_page": n + 1 if n < total else None,
                            "previous_page": n - 1 if n > 1 else None}
        return _Resp(body)

    @property
    def asked(self):
        return [c.get("page") for c in self.calls]


def hikes(n_pages, per_page, *, short_last=0):
    """n_pages of distinct events cloned from the real night hike: own id,
    own title, own date, so every one is its own row."""
    pages, k = [], 0
    for p in range(n_pages):
        rows = []
        for _ in range(short_last if (short_last and p == n_pages - 1) else per_page):
            k += 1
            w = copy.deepcopy(NIGHT_HIKE)
            e = w["event"]
            e["id"] = 9_000_000 + k
            e["title"] = f"Guided Hike {k}"
            day = f"2026-{10 + (k // 2800) % 3:02d}-{1 + (k % 28):02d}"
            e["first_date"] = day
            e["event_instances"][0]["event_instance"]["start"] = f"{day}T09:00:00-04:00"
            e["event_instances"][0]["event_instance"]["end"] = f"{day}T11:00:00-04:00"
            rows.append(w)
        pages.append(rows)
    return pages


def run(src, sess):
    """ingest() into a fresh scratch store, with time.sleep recorded, not slept."""
    store = EventStore(os.path.join(tempfile.mkdtemp(), "localist.json"))
    out = io.StringIO()
    slept = []
    with patch("time.sleep", side_effect=lambda s: slept.append(s)), redirect_stdout(out):
        try:
            kept = L.ingest(store, sess, src)
        except Exception as exc:                            # noqa: BLE001
            kept = f"raised {type(exc).__name__}: {exc}"
    return kept, store, out.getvalue(), slept


# --------------------------------------------------------------------------- #
# 1. a source's own max_pages: events.in.gov's 31 pages, read to the end
# --------------------------------------------------------------------------- #
pages = hikes(31, 2)
pages[10].append(MOON_HIKE)                                 # page 11, one past the old cap
indiana = Tenant(pages)
kept, store, log, _ = run({"name": "State of Indiana Events (Indiana)",
                           "base_url": "https://events.in.gov", "days": 90, "max_pages": 40},
                          indiana)
check("max_pages=40 reads all 31 of Indiana's pages", indiana.asked == list(range(1, 32)), indiana.asked)
check("...and keeps every event on them", kept == 63, kept)
check("...and never asks for page 32", 32 not in indiana.asked, indiana.asked)
moon = [r for r in store.records.values() if r["name"] == "October Moon Hike, Mounds State Park"]
check("the Mounds State Park moon hike on page 11 arrives, placed",
      len(moon) == 1 and moon[0]["latitude"] == 40.095788
      and moon[0]["start_local"] == "2026-10-26T19:00:00-04:00", moon)

# --------------------------------------------------------------------------- #
# 2. no max_pages: DEFAULT_MAX_PAGES, not ten
# --------------------------------------------------------------------------- #
# Cornell answered 1,473 occurrence wraps on 15 pages (2026-10-11).
check("DEFAULT_MAX_PAGES is more than the old ten", DEFAULT > 10, DEFAULT)
cornell = Tenant(hikes(15, 2))
kept, _, log, _ = run({"name": "Cornell", "base_url": "https://events.cornell.edu", "days": 90}, cornell)
check("a source with no max_pages reads Cornell's 15 pages whole",
      cornell.asked == list(range(1, 16)), cornell.asked)

# --------------------------------------------------------------------------- #
# 3. the cap is loud: pages left unread are counted
# --------------------------------------------------------------------------- #
big = Tenant(hikes(DEFAULT + 5, 2))
kept, _, log, _ = run({"name": "Too Big", "base_url": "https://events.example.edu", "days": 90}, big)
check("the default cap stops the walk", len(big.asked) == DEFAULT, big.asked)
check("...and says how many pages it left",
      f"5 of {DEFAULT + 5} pages unread" in log and "max_pages" in log, log)
kept, _, log, _ = run(dict(DNCR, max_pages=3), Tenant(hikes(3, 2)))
check("a walk that reaches the last page exactly says nothing about a cap", "unread" not in log, log)

# --------------------------------------------------------------------------- #
# 4. the real last page
# --------------------------------------------------------------------------- #
# DNCR's distinct walk: 239 wraps, page.total 3, the last page short.
dncr = Tenant(hikes(3, 100, short_last=39))
kept, _, log, _ = run(DNCR, dncr)
check("page.total=3 means three requests", dncr.asked == [1, 2, 3], dncr.asked)
check("...and all 239 events", kept == 239, kept)

# No page block at all: the short page is the last; no empty fourth request.
bare = Tenant(hikes(3, 100, short_last=39), block=False)
kept, _, log, _ = run(DNCR, bare)
check("without a page block the short page ends the walk", bare.asked == [1, 2, 3], bare.asked)
check("...with every event kept", kept == 239, kept)

# --------------------------------------------------------------------------- #
# 5. what is asked for
# --------------------------------------------------------------------------- #
check("every request asks for distinct=true",
      all(str(c.get("distinct")).lower() == "true" for c in dncr.calls), dncr.calls[:1])
check("...for pp=100 over the source's own days",
      all(c.get("pp") == 100 and c.get("days") == 90 for c in dncr.calls), dncr.calls[:1])

# --------------------------------------------------------------------------- #
# 6. Crawl-delay between pages, and only between them
# --------------------------------------------------------------------------- #
_, _, _, slept = run(DNCR, Tenant(hikes(3, 100, short_last=39)))
check("Crawl-delay 1 s slept between pages (two sleeps for three pages)", slept == [1.0, 1.0], slept)
_, _, _, slept = run(dict(DNCR, crawl_delay=5), Tenant(hikes(3, 100, short_last=39)))
check("a source's own crawl_delay wins", slept == [5, 5] or slept == [5.0, 5.0], slept)
_, _, _, slept = run(DNCR, Tenant([[NIGHT_HIKE]]))
check("a one-page calendar never sleeps", slept == [], slept)

# --------------------------------------------------------------------------- #
# 7. a failed page keeps the pages already read
# --------------------------------------------------------------------------- #
flaky = Tenant(hikes(5, 2), fail_on=4)
kept, _, log, _ = run(DNCR, flaky)
check("a read timeout on page 4 keeps pages 1-3", kept == 6, kept)
check("...and says which page failed", "p4 failed" in log and "ReadTimeout" in log, log)
check("...and asks for nothing after it", flaky.asked == [1, 2, 3, 4], flaky.asked)

# --------------------------------------------------------------------------- #
# 8. distinct or not, the row is the same: the earliest occurrence, keyed on
#    the event's own first_date (the collapse stays for a server that ignores
#    the parameter)
# --------------------------------------------------------------------------- #
filler = hikes(1, 100)[0]
occ = Tenant([[BIRD_HIKE_NOV] + filler[:99], [BIRD_HIKE_DEC]])
_, s_occ, _, _ = run(DNCR, occ)
_, s_one, _, _ = run(DNCR, Tenant([[BIRD_HIKE_NOV]]))


def _bird(store):
    return [(r["name"], r["start_local"], r["fingerprint"], r["latitude"])
            for r in store.records.values() if r["name"] == "Bird Hike"]


check("two occurrence wraps on two pages collapse to the one distinct=true returns",
      _bird(s_occ) == _bird(s_one) and len(_bird(s_one)) == 1, (_bird(s_occ), _bird(s_one)))
check("...on its November date", _bird(s_one) and _bird(s_one)[0][1] == "2026-11-02T09:00:00-05:00",
      _bird(s_one))

# --------------------------------------------------------------------------- #
# 9. the config: every max_pages is a whole number of pages, and every tenant
#    the 2026-10-11 measurement put above the default carries one
# --------------------------------------------------------------------------- #
here = os.path.dirname(os.path.abspath(__file__))
cfg = json.load(open(os.path.join(here, "localist_sources.json"), encoding="utf-8"))
bad = [s.get("name") for s in cfg
       if "max_pages" in s and (not isinstance(s["max_pages"], int) or isinstance(s["max_pages"], bool)
                                or s["max_pages"] < 1)]
check("every configured max_pages is a positive integer", not bad, bad)
measured_over = [s.get("name") for s in cfg
                 if any(k.startswith("measured_") for k in s)
                 and max(int(v) for k, v in s.items() if k.startswith("measured_"))
                 > 100 * int(s.get("max_pages") or DEFAULT)]
check("no tenant's measured distinct events exceed what its max_pages can read",
      not measured_over, measured_over)

# --------------------------------------------------------------------------- #
# 10. a state calendar is a town hall's too
# --------------------------------------------------------------------------- #
# events.in.gov, distinct=true, 2026-10-11: page 10, 12 and 7 of 15. Every
# Indiana county and town posts into this one calendar, and its only
# event_type is "Events", so all three reached the community door.
TOWN_COUNCIL = {"event": {
    "id": 49673925425232, "title": "Town Council Meeting", "status": "live", "first_date": "2026-07-01",
    "event_instances": [{"event_instance": {"id": 52434972581783,
                                            "start": "2026-11-04T17:30:00-05:00",
                                            "end": "2026-11-04T18:30:00-05:00", "all_day": False}}],
    "geo": {"latitude": "40.579681", "longitude": "-85.872959", "street": "210 North Jefferson Street",
            "city": "Converse", "state": "IN", "country": "US", "zip": "46919"},
    "location_name": "Converse Town Hall",
    "description_text": "The public is welcome to attend our town council meeting.",
    "filters": {"event_types": [{"name": "Events"}]},
    "photo_url": "https://localist-images.azureedge.net/photos/34245778578292/huge/a4ac721492f784826e99f556686251105e92faa4.jpg",
    "localist_url": "https://events.in.gov/event/town-council-meeting-7773",
    "url": None, "ticket_cost": None, "free": False}}
THANKSGIVING = {"event": {
    "id": 52764096586293, "title": "Thanksgiving Day Holiday", "status": "live", "first_date": "2026-11-26",
    "event_instances": [{"event_instance": {"id": 52764096587318, "start": "2026-11-26T00:00:00-05:00",
                                            "end": None, "all_day": True}}],
    "geo": {"latitude": "40.433885", "longitude": "-84.978747", "street": "120 North Court Street",
            "city": "Portland", "state": "IN", "country": "US", "zip": "47371"},
    "location_name": "Jay County Courthouse",
    "description_text": "Jay County Courthouse and Annexes will be closed on Thursday November 26th and "
                        "Friday November 27th 2026 for Thanksgiving holiday.",
    "filters": {"event_types": [{"name": "Events"}]},
    "photo_url": "https://localist-images.azureedge.net/photos/34245717485962/huge/f972ca543964516d9b4f75a3367ec90b346218bc.jpg",
    "localist_url": "https://events.in.gov/event/thanksgiving-day-holiday",
    "url": "https://jaycounty.net/plugins/live_stream/view.php", "ticket_cost": None, "free": False}}
BZA_DEADLINE = {"event": {
    "id": 51355946045671,
    "title": "Board of Zoning Appeals Deadline for Appeals, Variances, and Special Exceptions",
    "status": "live", "first_date": "2025-12-26",
    "event_instances": [{"event_instance": {"id": 51355946057970, "start": "2026-10-26T16:30:00-04:00",
                                            "end": None, "all_day": False}}],
    "geo": {"latitude": "38.204526", "longitude": "-86.122896", "street": "245 Atwood Street",
            "city": "Corydon", "state": "IN", "country": "US", "zip": "47112"},
    "location_name": "Harrison County Planning Commission",
    "description_text": "", "filters": {"event_types": []},
    "photo_url": "https://localist-images.azureedge.net/photos/34245778578292/huge/a4ac721492f784826e99f556686251105e92faa4.jpg",
    "localist_url": "https://events.in.gov/event/board-of-zoning-appeals-deadline-for-appeals-variances-and-special-exceptions",
    "url": None, "ticket_cost": None, "free": False}}

# events.dncr.nc.gov, distinct=true, 2026-10-11: two of the eleven DNCR types
# "Public Meetings" (page 1 and page 3), and its one holiday title, which is a
# programme at a state park and not a closure (page 1).
QUARTERLY = {"event": {
    "id": 54188509364031, "title": "Fall 2026 Quarterly Meeting", "status": "live",
    "first_date": "2026-10-22",
    "event_instances": [{"event_instance": {"id": 54188509365056, "start": "2026-10-22T10:00:00-04:00",
                                            "end": "2026-10-22T14:00:00-04:00", "all_day": False}}],
    "geo": {"latitude": "35.78292", "longitude": "-78.637155", "street": "109 East Jones Street",
            "city": "Raleigh", "state": "NC", "country": "US", "zip": "27601"},
    "location_name": "DNCR",
    "description_text": "The N.C. American Indian Heritage Commission will hold a regular quarterly "
                        "business meeting on Thursday, October 22, 2026.",
    "filters": {"event_types": [{"name": "Organizational"}, {"name": "Public Meetings"}]},
    "photo_url": "https://localist-images.azureedge.net/photos/52623009451167/huge/b625146ed7a879184596e1784080de9367485784.jpg",
    "localist_url": "https://events.dncr.nc.gov/event/fall-2026-quarterly-meeting",
    "url": "", "ticket_cost": "", "free": False}}
NCLWF_BOARD = {"event": {
    "id": 51764793806574, "title": "NCLWF Board Meeting (Q4-2026)", "status": "live",
    "first_date": "2026-12-15",
    "event_instances": [{"event_instance": {"id": 51764797089996, "start": "2026-12-15T13:00:00-05:00",
                                            "end": "2026-12-15T17:00:00-05:00", "all_day": False}}],
    "geo": {"latitude": "35.782138", "longitude": "-78.641055", "street": "121 West Jones Street",
            "city": "Raleigh", "state": "NC", "country": "US", "zip": "27601"},
    "location_name": " Nature Research Center",
    "description_text": "This meeting will be held via video conference and will have a physical "
                        "location on the 4th floor of the Nature Research Center located at 121 West "
                        "Jones Street",
    "filters": {"event_types": [{"name": "Organizational"}, {"name": "Public Meetings"},
                                {"name": "Board & Committee Meetings"}]},
    "photo_url": "https://localist-images.azureedge.net/photos/51764793779949/huge/bb9beed49044f5b421c565d100a0eb8e52098add.jpg",
    "localist_url": "https://events.dncr.nc.gov/event/copy-of-nclwf-board-meeting-q3-2026",
    "url": "https://nclwf.nc.gov/events", "ticket_cost": "Free", "free": True}}
INDIGENOUS_DAY = {"event": {
    "id": 53737117371556, "title": "Indigenous Peoples Day", "status": "live", "first_date": "2026-10-12",
    "event_instances": [{"event_instance": {"id": 53737117372581, "start": "2026-10-12T11:00:00-04:00",
                                            "end": "2026-10-12T14:00:00-04:00", "all_day": False}}],
    "geo": {"latitude": "36.395044", "longitude": "-80.266541", "street": "1790 Hanging Rock Park Road",
            "city": "Danbury", "state": "NC", "country": "US", "zip": "27016"},
    "location_name": "Hanging Rock State Park",
    "description_text": "Join us for a day of fun, history, and celebration! Enjoy interactive exhibits, "
                        "historical artifacts, flintknapping demonstrations, games, and free canoeing on ",
    "filters": {"event_types": [{"name": "Celebrations & Commemorations"}, {"name": "Festivals & Fairs"}]},
    "photo_url": "https://localist-images.azureedge.net/photos/53737117470894/huge/3f91d1be6cc1dc5dbbf339033a4ccd0160680aaa.jpg",
    "localist_url": "https://events.dncr.nc.gov/event/indigenous-peoples-day",
    "url": "", "ticket_cost": "", "free": False}}


def _names(store):
    return sorted(r["name"] for r in store.records.values())


INDIANA_CIVIC = {"name": "State of Indiana Events (Indiana)", "base_url": "https://events.in.gov",
                 "days": 90, "_found": "civic:state -> localist (hand-curated)"}
town = [[TOWN_COUNCIL, THANKSGIVING, BZA_DEADLINE, MOON_HIKE]]
kept, store, log, _ = run(INDIANA_CIVIC, Tenant(town))
check("a civic: source refuses the council meeting, the courthouse holiday and the BZA deadline",
      _names(store) == ["October Moon Hike, Mounds State Park"], _names(store))
check("...keeps the moon hike, and counts the refusals",
      kept == 1 and "3 town-hall row(s) refused" in log, (kept, log))
campus = {k: v for k, v in INDIANA_CIVIC.items() if k != "_found"}
kept, store, log, _ = run(campus, Tenant(town))
check("a source without the civic: marker refuses none of them (never a campus)",
      kept == 4 and "refused" not in log, (kept, log))

# A canceled meeting stays a cancellation: a row stored before the filter
# existed is still on the map, and only the tombstone takes it off.
dead_council = copy.deepcopy(TOWN_COUNCIL)
dead_council["event"]["status"] = "canceled"
kept, store, log, _ = run(INDIANA_CIVIC, Tenant([[dead_council, MOON_HIKE]]))
check("a canceled council meeting on a civic: source is still tombstoned",
      len(store.tombstones) == 1 and "1 canceled (tombstoned)" in log
      and "refused" not in log, (store.tombstones, log))

# skip_title, as ics and tribe read it: a case-insensitive search.
dncr_cfg = next((s for s in cfg if "events.dncr.nc.gov" in (s.get("base_url") or "")), {})
dncr_pages = [[QUARTERLY, NCLWF_BOARD, INDIGENOUS_DAY, NIGHT_HIKE, BIRD_HIKE_NOV]]
kept, store, log, _ = run(dncr_cfg, Tenant(dncr_pages))
check("DNCR's configured skip_title refuses its two meetings",
      not ({"Fall 2026 Quarterly Meeting", "NCLWF Board Meeting (Q4-2026)"} & set(_names(store))),
      _names(store))
check("...keeps Indigenous Peoples Day at Hanging Rock and both hikes, and counts the two",
      _names(store) == ["Bird Hike", "In the Park After Dark - Night Hike", "Indigenous Peoples Day"]
      and "2 title-filtered" in log, (_names(store), log))
check("...because DNCR is not civic: (the holiday rule would refuse that programme)",
      not str(dncr_cfg.get("_found", "")).startswith("civic:")
      and CIVIC_HOLIDAY_RX.match(INDIGENOUS_DAY["event"]["title"]) is not None, dncr_cfg.get("_found"))
kept, _, log, _ = run(dict(DNCR, skip_title="(unclosed"), Tenant(dncr_pages))
check("an invalid skip_title fails the source loudly, naming it",
      isinstance(kept, str) and "ValueError" in kept and "invalid skip_title" in kept, kept)

# The config: events.in.gov carries the marker that turns the filter on.
ind_cfg = next((s for s in cfg if "events.in.gov" in (s.get("base_url") or "")), {})
check("events.in.gov is configured civic:", str(ind_cfg.get("_found", "")).startswith("civic:"),
      ind_cfg.get("_found"))

# --------------------------------------------------------------------------- #
# 11. what it costs, judged by ../mapsee's 0227 on the row the sync stores
# --------------------------------------------------------------------------- #
# Every Localist event carries `free` and `ticket_cost`, and neither was read:
# 3,592 of 25,197 events say free=true (2026-10-11), and 0227 tags offer:free
# from a row's title and description only. Each case below goes through
# ingest() and mapsee_supabase_sync.to_row (its 800-character cut, its machine
# tail) and is judged by 0227's own predicate (offers_0227: measure_deals.py's
# patterns, vendored verbatim and compared with ../mapsee when it is here).
# Fixtures: the walk of 2026-10-11, fields the adapter reads, blurbs cut where
# marked.
import offers_0227 as O  # noqa: E402
import mapsee_supabase_sync as SY  # noqa: E402
from mapsee_admission import admission_description  # noqa: E402

print()
print("what it costs, as 0227 reads it")
_drift = O.drift()
if _drift is None:
    print("skip offers_0227 is not compared: ../mapsee is not checked out here (MAPSEE_DIR names it)")
else:
    check("offers_0227's patterns and verdicts equal ../mapsee/tools/measure_deals.py's", _drift == [], _drift)

# events.dcnr.pa.gov, free=true: a 1,876-character blurb, longer than the
# sync keeps (cut here at 1,016, still past it).
NIGHT_SCAVENGER = {"event": {
    "id": 54091846175818, "title": "Night Hike Scavenger Hunt", "status": "live", "first_date": "2026-10-23",
    "event_instances": [{"event_instance": {"id": 54091846176843, "start": "2026-10-23T18:30:00-04:00",
                                            "end": "2026-10-23T20:00:00-04:00", "all_day": False}}],
    "geo": {"latitude": "40.463421", "longitude": "-75.241791", "street": "1542 Mountain View Drive",
            "city": "Quakertown", "state": "PA", "country": "US", "zip": "18951"},
    "location_name": "Nockamixon State Park",
    "description_text": "The Sun has gone down, and it's too dark to explore--or is it? A whole new world of fascinating "
                        "objects, critters, and phenomena is revealed at night, and we're going to see what there is "
                        "to find. Kids and their grownups are invited to join the park naturalist for a nighttime "
                        "scavenger hunt. We'll talk about some of the things we're looking for, then take a one-mile "
                        "hike with stops along the way to find those things! **Registration is required for this "
                        "program. Please register each person attending, both kids and grownups, by clicking the "
                        "\"Register\" button.** This program is best suited for children ages 8 and up, but younger "
                        "kids are welcome if they can manage a 1-mile hike and sit quietly during a night sounds "
                        "listening stop. All children must be accompanied by an adult guardian, though we will be "
                        "splitting up (while remaining in view) for some activities. Please wear sturdy shoes and "
                        "bring water and a flashlight or headlamp. We will meet at the Environmental Education "
                        "Center, which is accessed by a wooded path from Parking Lot 12.",
    "filters": {"event_types": []},
    "photo_url": "https://localist-images.azureedge.net/photos/54091846257749/huge/d7560c0b4e49b055633cda708e0580a7c140abe6.jpg",
    "localist_url": "https://events.dcnr.pa.gov/event/night-hike-scavenger-hunt",
    "url": "", "ticket_cost": "Free - Registration Required", "free": True}}
# events.dcnr.pa.gov, free=true, blurb cut after the $5 that is a souvenir,
# not a fee for the walk.
BARK_WALK = {"event": {
    "id": 54035803389021, "title": "B.A.R.K. Ranger Walk", "status": "live", "first_date": "2026-09-26",
    "event_instances": [{"event_instance": {"id": 54035803404386, "start": "2026-10-11T12:00:00-04:00",
                                            "end": None, "all_day": False}}],
    "geo": {"latitude": "40.4585", "longitude": "-77.168198", "street": "2511 Little Buffalo Road",
            "city": "Newport", "state": "PA", "country": "US", "zip": "17074"},
    "location_name": "Little Buffalo State Park",
    "description_text": "A B.A.R.K. Ranger is a pet that follows the 4 principles of B.A.R.K.: Bag your pet's waste. "
                        "Always leash your pet. Respect Wildlife. Know where you can go. Join the park resource "
                        "ranger on a hike to complete the B.A.R.K. Ranger pamphlet with your pet. After the hike, the "
                        "resource ranger will sign your pamphlet, and you will receive a certificate showing that you "
                        "have successfully completed the program. If the park office is open, a commemorative tag "
                        "can be purchased for $5 to go on your pet's collar or leash.",
    "filters": {"event_types": []},
    "photo_url": "https://localist-images.azureedge.net/photos/54035803490409/huge/caeae66d878de7f5af3fd9532f736e88b4f4ae1c.jpg",
    "localist_url": "https://events.dcnr.pa.gov/event/bark-ranger-walk-9730",
    "url": "", "ticket_cost": "FREE", "free": True}}
# events.dncr.nc.gov, free=true, and the cost field prices the adults.
FALL_FESTIVAL = {"event": {
    "id": 52267025269801, "title": "Fall Festival", "status": "live", "first_date": "2026-11-14",
    "event_instances": [{"event_instance": {"id": 53885345259586, "start": "2026-11-14T10:00:00-05:00",
                                            "end": "2026-11-14T16:00:00-05:00", "all_day": False}}],
    "geo": {"latitude": "35.301887", "longitude": "-78.322773", "street": "5466 Harper House Road",
            "city": "Four Oaks", "state": "NC", "country": "US", "zip": "27524"},
    "location_name": "Bentonville Battlefield State Historic Site",
    "description_text": "Bring the family and celebrate fall at Bentonville Battlefield! See demonstrations of "
                        "historic trades and activities. Also don’t miss wagon rides, old timey kids games, crafts "
                        "and activities, community groups, and much more! Bring a blanket or chair and listen to "
                        "live music. Food trucks will be onsite!",
    "filters": {"event_types": [{"name": "Celebrations & Commemorations"}, {"name": "Festivals & Fairs"}]},
    "photo_url": "https://localist-images.azureedge.net/photos/53885372860649/huge/4dfc2aed9d1500a9d9a2dc86e102e1a9251d4093.jpg",
    "localist_url": "https://events.dncr.nc.gov/event/fall-festival",
    "url": "", "ticket_cost": "Adults $5, Children 8 and under are free ", "free": True}}
# events.oregonstate.edu, free=true, free for some, and the blurb says FREE.
CORNHOLE = {"event": {
    "id": 53870830029431, "title": "Cornhole Tournament", "status": "live", "first_date": "2026-10-16",
    "event_instances": [{"event_instance": {"id": 53870830032505, "start": "2026-10-16T18:00:00-07:00",
                                            "end": "2026-10-16T22:00:00-07:00", "all_day": False}}],
    "geo": {"latitude": "44.563137", "longitude": "-123.278729", "street": "425 Southwest 26th Street",
            "city": "Corvallis", "state": "OR", "country": "US", "zip": "97331"},
    "location_name": "Dixon Recreation Center",
    "description_text": "This is a FREE event. No IM PASS required. Register online through IMLeagues by 2 p.m. on "
                        "Oct. 16. This event takes place on Oct. 16 from 6-10 p.m. All participants must create an "
                        "IMLeagues account to register for intramural leagues, tournaments and events for that term.",
    "filters": {"event_types": [{"name": "Sporting Event"}]},
    "photo_url": "https://localist-images.azureedge.net/photos/54039446041456/huge/c714d277d38025d3f54761b07a9eef5987d59773.jpg",
    "localist_url": "https://events.oregonstate.edu/event/cornhole-tournament",
    "url": "http://www.imleagues.com/spa/intramural/f68be0657e2047dc89b8877bb233effa/home",
    "ticket_cost": "Free for students and Rec Sports members", "free": True}}
# events.in.gov, free=true, and the blurb charges (cut to its first and fee
# sentences).
DEER_WORKSHOP = {"event": {
    "id": 54106905996706, "title": "Basic Deer Processing Workshop, West Lafayette", "status": "live",
    "first_date": "2026-10-19",
    "event_instances": [{"event_instance": {"id": 54106928991873, "start": "2026-10-19T18:00:00-04:00",
                                            "end": "2026-10-19T21:00:00-04:00", "all_day": False}}],
    "geo": {"latitude": "40.431312", "longitude": "-87.037944", "street": "1007 North 725 West",
            "city": "West Lafayette", "state": "IN", "country": "US", "zip": "47906"},
    "location_name": "John S Wright Center",
    "description_text": "Join Indiana DNR for this basic deer processing workshop on Oct. 19 from 6 p.m. – 9 p.m. ET "
                        "at the John S Wright Center in West Lafayette. Registration is required to attend. There "
                        "will be a small $10 dollar fee for this event, which will be charged at the door. See you "
                        "there!",
    "filters": {"event_types": [{"name": "Events"}]},
    "photo_url": "https://localist-images.azureedge.net/photos/54106905877920/huge/41e9b974f5d63cebe91e1e7d3a16c6a247a5073a.jpg",
    "localist_url": "https://events.in.gov/event/basic-deer-processing-workshop-w-lafayette",
    "url": "https://on.IN.gov/learn2hunt", "ticket_cost": None, "free": True}}
# calendar.ucsd.edu, free=false: a priced game whose blurb says FREE (for
# undergraduates), which 0227 read as a free event.
UCSD_GAME = {"event": {
    "id": 54126634030882, "title": "Men's Basketball: UC San Diego vs. USC (Exhibition)", "status": "live",
    "first_date": "2026-10-17",
    "event_instances": [{"event_instance": {"id": 54126634031907, "start": "2026-10-17T19:00:00-07:00",
                                            "end": "2026-10-17T21:00:00-07:00", "all_day": False}}],
    "geo": {"latitude": "32.885191", "longitude": "-117.23981", "street": "9860 Hopkins Drive",
            "city": "San Diego", "state": "CA", "country": "US", "zip": "92093"},
    "location_name": "LionTree Arena",
    "description_text": "The UC San Diego men's basketball team returns to LionTree Arena in an exhibition game against "
                        "USC on Saturday, Oct. 17. Tip-off is scheduled for 7 p.m., and tickets are now on sale at the "
                        "Triton Box Office. Admission is FREE for UC San Diego undergraduate students. Go Tritons!",
    "filters": {"event_types": [{"name": "Sports and Recreation"}]},
    "photo_url": "https://localist-images.azureedge.net/photos/54126641994427/huge/28b4d959e4b3860ee17e4d72b3dddf5f2cbd6e3d.jpg",
    "localist_url": "https://calendar.ucsd.edu/event/mens-basketball-uc-san-diego-vs-usc-exhibition",
    "url": "https://ucsdtritons.com/sports/2021/10/15/mens-basketball-gameday",
    "ticket_cost": "Tickets: Adult - $22+; Youth (3-12), Senior (65+), Fac/Staff - $17", "free": False}}
# calendar.ucsd.edu, free=false: a cost field that names a member discount
# (blurb cut to two of its sentences, leaving out its own "Members Save 20%
# Off Tickets", so a discount could only come from the cost line).
OCEANS_AT_NIGHT = {"event": {
    "id": 54064237800884, "title": "Oceans at Night: Halloween", "status": "live", "first_date": "2026-10-22",
    "event_instances": [{"event_instance": {"id": 54064298897342, "start": "2026-10-22T18:30:00-07:00",
                                            "end": "2026-10-22T21:30:00-07:00", "all_day": False}}],
    "geo": {"latitude": "32.865974", "longitude": "-117.250736", "street": "2300 Expedition Way",
            "city": "San Diego", "state": "CA", "country": "US", "zip": "92037"},
    "location_name": "Birch Aquarium",
    "description_text": "Grab your ghoul-friends and join us for a night of skele-fun at Oceans at Night: Halloween, "
                        "our 21+ Halloween celebration! Discover the weird and wonderful research happening at "
                        "Scripps Oceanography!",
    "filters": {"event_types": [{"name": "Social"}]},
    "photo_url": "https://localist-images.azureedge.net/photos/54064299271117/huge/6f520e031e8dc1dbe7e43c7c4649fd52c5465b17.jpg",
    "localist_url": "https://calendar.ucsd.edu/event/oceans-at-night-halloween",
    "url": "https://aquarium.ucsd.edu/events/oceans-night-halloween",
    "ticket_cost": "$59.95, Members receive 20% discount", "free": False}}

HOST = "00000000-0000-0000-0000-000000000000"
# The words are the contract with 0227, so they are pinned here, not read back.
FREE_LINE = "🎟 Free to attend."
RESTRICTED_LINE = "🎟 Not a free public admission offer. Eligibility restrictions apply."


def stored(wrap, src=DNCR):
    """(title, description, record) as the sync writes the one row a wrap makes."""
    _, store, _, _ = run(src, Tenant([[wrap]]))
    recs = list(store.records.values())
    if len(recs) != 1:
        return None, None, None
    row = SY.to_row(recs[0], HOST)
    return row["title"], row["description"], recs[0]


def uncosted(wrap):
    """The same wrap with neither field the adapter now reads: the row it wrote before."""
    w = copy.deepcopy(wrap)
    w["event"]["free"] = False
    w["event"]["ticket_cost"] = None
    return w


def prose(description):
    """The part of a stored description 0227 reads (before the 📍 tail)."""
    return (description or "").split("\n\n📍")[0]


for label, wrap in (("a flagged-free night hike (cost field 'Free - Registration Required')", NIGHT_SCAVENGER),
                    ("a flagged-free walk whose blurb sells a $5 souvenir tag", BARK_WALK)):
    title, desc, _ = stored(wrap)
    check(f"{label} leads with 'Free to attend.'", (desc or "").startswith(FREE_LINE + "\n\n"),
          (desc or "")[:80])
    t0, d0, _ = stored(uncosted(wrap))
    check("...and 0227 tags the stored row free, where the row it wrote before was NOT",
          O.tagged_free(title, desc) and not O.tagged_free(t0, d0), ((desc or "")[:120], (d0 or "")[:120]))
title, desc, _ = stored(NIGHT_SCAVENGER)
check("the free line survives the sync's 800-character cut of a 1,016-character blurb",
      len(NIGHT_SCAVENGER["event"]["description_text"]) > 800 and desc.startswith(FREE_LINE)
      and prose(desc).endswith("…"), (len(prose(desc)), prose(desc)[-40:]))

title, desc, _ = stored(FALL_FESTIVAL)
check("a flagged-free festival whose cost field says 'Adults $5' leads with its price, not free",
      desc.startswith("🎟 Price as listed: $5 (not free for everyone).\n\n"), desc[:80])
check("...and 0227 does NOT tag it free", not O.tagged_free(title, desc), desc[:120])

title, desc, _ = stored(CORNHOLE)
check("a flag the cost field limits ('Free for students and Rec Sports members') says so",
      desc.startswith(RESTRICTED_LINE + "\n\n"), desc[:90])
t0, d0, _ = stored(uncosted(CORNHOLE))
check("...and 0227 does NOT tag it, though its blurb says 'This is a FREE event.' (the row written before WAS)",
      not O.tagged_free(title, desc) and O.tagged_free(t0, d0), (desc[:120], d0[:120]))
check("the restricted line is mapsee_admission's own words",
      getattr(L, "RESTRICTED_LINE", None) == RESTRICTED_LINE
      == "🎟 " + admission_description(None, {"free": False, "restricted": True}),
      getattr(L, "RESTRICTED_LINE", None))

title, desc, _ = stored(DEER_WORKSHOP, dict(INDIANA_CIVIC))
check("a flagged-free workshop whose blurb charges 'a small $10 dollar fee' gets no cost line",
      bool(desc) and not desc.startswith("🎟"), (desc or "")[:80])
check("...and 0227 does NOT tag it free", not O.tagged_free(title, desc), (desc or "")[:120])

title, desc, _ = stored(NIGHT_HIKE)
check("an unflagged row (free=false, no ticket_cost) gets no cost line", not desc.startswith("🎟"), desc[:60])
check("...and 0227 does NOT tag it free", not O.tagged_free(title, desc), desc[:120])

title, desc, _ = stored(UCSD_GAME)
check("a priced game ($22+, $17) leads with its price", desc.startswith("🎟 Price as listed: $22, $17 (not free).\n\n"),
      desc[:80])
t0, d0, _ = stored(uncosted(UCSD_GAME))
check("...and 0227 does NOT tag it, though its blurb says 'Admission is FREE for UC San Diego undergraduate "
      "students' (the row written before WAS)", not O.tagged_free(title, desc) and O.tagged_free(t0, d0),
      (desc[:160], d0[:120]))

title, desc, _ = stored(OCEANS_AT_NIGHT)
line = desc.split("\n\n", 1)[0]
check("a price is said as its amounts: '$59.95, Members receive 20% discount' -> '$59.95'",
      line == "🎟 Price as listed: $59.95 (not free).", line)
check("...so the line carries no offer tag of its own (quoted whole, it read as a discount)",
      O.tags("", line) == set() and "discount" in O.tags("", "$59.95, Members receive 20% discount"),
      O.tags("", line))

ALL_COSTS = (NIGHT_SCAVENGER, BARK_WALK, FALL_FESTIVAL, CORNHOLE, UCSD_GAME, OCEANS_AT_NIGHT, NIGHT_HIKE,
             BIRD_HIKE_NOV, MOON_HIKE)
gained = []
moved = []
for wrap in ALL_COSTS:
    title, desc, rec = stored(wrap)
    t0, d0, rec0 = stored(uncosted(wrap))
    extra = (O.tags(title, desc) - {"free"}) ^ (O.tags(t0, d0) - {"free"})
    if extra:
        gained.append((wrap["event"]["title"], extra))
    if (rec["fingerprint"], rec["sources"]) != (rec0["fingerprint"], rec0["sources"]):
        moved.append(wrap["event"]["title"])
check("no row gains or loses discount, intro, pwyw, sale or happy_hour by its cost line", not gained, gained)
check("...and no row's fingerprint or source id moves: the line is in the description only", not moved, moved)

_, _, log, _ = run(DNCR, Tenant([[NIGHT_SCAVENGER, BARK_WALK, FALL_FESTIVAL, CORNHOLE, NIGHT_HIKE]]))
check("the log counts what the cost lines said",
      "cost: 2 free, 1 priced, 1 restricted, 0 flagged free but contradicted" in log, log)

print()
if fails:
    print(f"{len(fails)} check(s) FAILED")
    sys.exit(1)
print("all localist checks passed")
