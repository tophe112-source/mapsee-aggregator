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

print()
if fails:
    print(f"{len(fails)} check(s) FAILED")
    sys.exit(1)
print("all localist checks passed")
