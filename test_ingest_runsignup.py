"""Does the RunSignup adapter hand the geocoder a street it can place, date a
race on a day still to come, and read every race in a state that has outgrown
the API's cap?

Three ways a race was lost or written wrong between RunSignup and the map, all
measured on 2026-10-11, and all silent: each produced a well-formed row, or no
row, and a log line that looked like a normal day.

  THE PLACE IN `street`, THE NUMBER IN `street2`. "FDR Park (at the boathouse)"
  over "1500 Pattison Avenue". The adapter sent `street` as the address and the
  sync hands `address` alone to the Census batch geocoder, which cannot place a
  park's name; an unplaced row is dropped. In Ohio this shape was 110 of 415
  rows, and 2 were placed. Sent street2: 84 of 99, and Ohio's placed rows went
  from 237 to 307 with none lost. The venue label, and so the fingerprint, must
  NOT move with it, or every stored row comes back as a duplicate.

  THE WINDOW MATCHES ON OVERLAP. A race that began before today comes back for
  a window starting today: 45 of 453 Ohio races. Dated by the earliest
  sub-event, 32 rows went out on a day already gone, 12 of them placed: a 5k
  run yesterday beside a virtual week, a year-long walking series. And a real
  walk could hide behind its own past early-bird entry: NAMI's Ending the
  Silence 5K was dated 8/3 and is on 11/7. The other way round, a race whose
  walk and rides are over must not be dated by the gala it still sells
  (StRides Against Suicide & Overdose: 8/1, then a "Silent Auction Gala" 11/8).

  FLORIDA OUTGREW THE 1,000-RACE CAP. The API answers at most 1,000 rows for a
  query, sorted by name, with no total. Florida's 120 days held 1,042, so the
  sweep lost W to Z ("Zonta ABC Run/Walk", "Your Next Step is the Cure 5K").

Literal payloads trimmed from the live answers (only the fields the adapter
reads), a fake API that answers the way the live one was measured to, and the
sync's own _addr_parts. No network, no database; one temporary store file.
"""
import contextlib
import io
import json
import os
import sys
import tempfile
from datetime import date, datetime, timedelta

os.environ["MAPSEE_TODAY"] = "20261011"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mapsee_ingest_runsignup as RS  # noqa: E402
import mapsee_supabase_sync as SYNC  # noqa: E402
from mapsee_ingest import EventStore, make_fingerprint  # noqa: E402

fails = []


def check(label, cond, detail=""):
    print(f"{'ok  ' if cond else 'FAIL'} {label}{'' if cond else '   ' + str(detail)}")
    if not cond:
        fails.append(label)


CFG = dict(RS.DEFAULTS)
TODAY = "2026-10-11"


def race(race_id, name, next_date, next_end_date, street, street2, city, state, events):
    return {"race_id": race_id, "name": name, "next_date": next_date,
            "next_end_date": next_end_date, "is_draft_race": "F", "is_private_race": "F",
            "timezone": "America/New_York",
            "url": f"https://runsignup.com/Race/{state}/{race_id}",
            "address": {"street": street, "street2": street2, "city": city, "state": state,
                        "zipcode": "00000", "country_code": "US"},
            "events": events}


def ev(name, event_type, start_time, end_time=None, distance=None, volunteer="F"):
    return {"name": name, "event_type": event_type, "start_time": start_time,
            "end_time": end_time, "volunteer": volunteer, "distance": distance}


# --- the live payloads, trimmed --------------------------------------------

COLON = race(163588, "Philadelphia Walk to End Colon Cancer", "11/07/2026", "11/07/2026",
             "FDR Park (at the boathouse)", "1500 Pattison Avenue &, S Broad St",
             "Philadelphia", "PA",
             [ev("Walk to End Colon Cancer", "walking_only", "11/7/2026 08:30")])
SPOOKY = race(136338, "Spooky Shuffle 5K, Fun Run, Walk, and Paw", "10/17/2026", "10/17/2026",
              "Creekside Park & Arboretum", "123 Mill St", "Gahanna", "OH",
              [ev("5K", "running_race", "10/17/2026 09:00", distance="5K"),
               ev("Monster Mash Dash - Kids Fun Run", "running_race", "10/17/2026 08:50")])
CIDER = race(49478, "Hot Cider Hustle - Fort Wayne Half Marathon, 10K & 5K",
             "10/11/2026", "10/11/2026",
             "4000 Parnell Ave.", "Allen County War Memorial Coliseum", "Fort Wayne", "IN",
             [ev("Half Marathon", "running_only", "10/11/2026 08:30", distance="13.1 Miles"),
              ev("5K Run/Walk", "running_race", "10/11/2026 08:30", distance="5K")])
LARS = race(151610, "22nd Annual LARS Race Against Hunger & Homelessness (The Turkey Trot)",
            "11/26/2026", "11/26/2026",
            "Alice B. McCullough Field", "Montgomery St & 8th St", "Laurel", "MD",
            [ev("The Turkey Trot-5K Run", "running_race", "11/26/2026 08:00", distance="5K"),
             ev("The Turkey Trot-1M Walk", "walking_only", "11/26/2026 08:05", distance="1 Miles")])
FIT4FALL = race(9774, "Fit 4 Fall 5K Run/Walk", "10/10/2026", "10/18/2026",
                "200 Broad Street", None, "St. Joseph", "MI",
                [ev("5K Run", "running_only", "10/10/2026 09:00", distance="5K"),
                 ev("5K Walk", "walking_only", "10/10/2026 09:00", distance="5K"),
                 ev("Virtual 5K", "virtual_race", "10/10/2026 09:00", "10/18/2026 23:59", "5K")])
STRIDERS = race(195148, "2026 Tidewater Striders Walking Grand Prix", "01/01/2026", "12/06/2026",
                "no location", None, "Virginia Beach", "VA",
                [ev("2026 Tidewater Striders Walking Grand Prix", "walking_only",
                    "1/1/2026 08:00", "12/6/2026 17:00")])
NAMI = race(113719, "ENDING THE SILENCE 5K RUN/WALK 2026", "08/03/2026", "11/07/2026",
            "University of West Florida at The Rec Plex North 5k Trail (See Map)",
            "11000 University Parkway", "Pensacola", "FL",
            [ev("NAMI ENDING THE SILENCE 5K RUN/WALK IN PERSON EVENT", "running_race",
                "11/7/2026 10:00", "11/7/2026 13:00", "5K"),
             ev("NAMI ENDING THE SILENCE 5K VIRTUAL CHALLENGE", "virtual_race",
                "8/3/2026 00:00", "11/7/2026 13:00", "5K"),
             ev("EARLY BIRD SIGN UP FOR NAMI ENDING THE SILENCE 5K RUN/WALK VIRTUAL/IN PERSON EVENTS",
                "running_race", "8/3/2026 00:00", "9/1/2026 00:00", "5K")])
WIT = race(148193, "7th Annual WIT 5K on International Day of the Girl Child",
           "10/10/2026", "10/11/2026",
           "Alabama Shakespeare Festival", "1 Festival Drive", "Montgomery", "AL",
           [ev("7th Annual WIT 5K Run/Walk", "running_only", "10/11/2026 08:00",
               "10/11/2026 11:59", "3.1 Miles"),
            ev("WIT 1 Mile Fun Walk", "walking_only", "10/11/2026 08:00",
               "10/11/2026 11:59", "1 Miles"),
            ev("Virtual WIT 5K Run/Walk Race", "virtual_race", "10/10/2026 00:00",
               "10/11/2026 23:59", "3.1 Miles")])
# The walk, the run and the rides were all on 8/1. What is left on 11/8 is the
# gala, typed nonprofit_event, and it dated the race as an 11/8 fitness event at
# the park (street2 then placed it).
STRIDES = race(126866, "StRides Against Suicide & Overdose", "08/01/2026", "11/08/2026",
               "Rogers-Lakewood Park", "5501 N. Campbell St.", "Valparaiso", "IN",
               [ev("5K Run", "running_only", "8/1/2026 10:00", "8/1/2026 11:30", "5K"),
                ev("5K Walk", "walking_only", "8/1/2026 10:10", "8/1/2026 11:30", "5K"),
                ev("100 Mile Bike Ride", "bike_ride", "8/1/2026 06:30", "8/1/2026 13:00",
                   "100 Miles"),
                ev("Virtual 5K Walk/Run", "virtual_race", "8/1/2026 06:00", "8/1/2026 23:59", "5K"),
                ev("Silent Auction Gala [General Admission]", "nonprofit_event",
                   "11/8/2026 14:00", "11/8/2026 18:00"),
                ev("Silent Auction Gala [VIP Admission]", "nonprofit_event",
                   "11/8/2026 12:00", "11/8/2026 18:00")])
# No typed sub-event at all, only 'other': still dated by the next one.
SEMINARS = race(59894, "Epilepsy Alliance Ohio Professional Seminar Series",
                "07/30/2026", "12/10/2026",
                "Residence Inn by Marriott - Rookwood", "3815 Edwards Road", "Cincinnati", "OH",
                [ev("Epilepsy Alliance Ohio Seminar Series - \"Helping People Change\"", "other",
                    "7/30/2026 09:00", "7/30/2026 12:15"),
                 ev("Epilepsy Alliance Ohio Seminar Series - \"ADHD in Children Through Adults\"",
                    "other", "10/29/2026 09:00", "10/29/2026 12:15"),
                 ev("Epilepsy Alliance Ohio Seminar Series - \"Executive Functioning\"", "other",
                    "11/19/2026 09:00", "11/19/2026 12:15")])


def to_event(r):
    return RS.to_event(r, CFG)


# --- 1. the street the geocoder reads ---------------------------------------

e = to_event(COLON)
check("venue in street, numbered street2: the geocoder is sent street2",
      e is not None and e.address == "1500 Pattison Avenue &, S Broad St",
      e and e.address)
check("... and the pin's label is still the venue in street",
      e is not None and e.venue_name == "FDR Park (at the boathouse)", e and e.venue_name)
check("... and the fingerprint is the one already stored (venue from street, not street2)",
      e is not None and e.fingerprint == make_fingerprint(
          "Philadelphia Walk to End Colon Cancer", "2026-11-07",
          "FDR Park (at the boathouse)", "Philadelphia"),
      e and e.fingerprint)

e = to_event(SPOOKY)
check("Creekside Park & Arboretum / 123 Mill St: address is 123 Mill St",
      e is not None and e.address == "123 Mill St", e and e.address)

e = to_event(CIDER)
check("numbered street, venue in street2: street is already the line, kept",
      e is not None and e.address == "4000 Parnell Ave.", e and e.address)
check("... and its label stays the city, as before",
      e is not None and e.venue_name == "Fort Wayne", e and e.venue_name)

e = to_event(LARS)
check("venue in street, street2 with no number ('Montgomery St & 8th St'): street kept",
      e is not None and e.address == "Alice B. McCullough Field", e and e.address)

variant = json.loads(json.dumps(SPOOKY))
variant["address"].update(street="Ocean Grove Boardwalk", street2=None)
e = to_event(variant)
check("a venue name with no street2 at all: street kept",
      e is not None and e.address == "Ocean Grove Boardwalk", e and e.address)
variant["address"].update(street="6807 Fayetteville Rd", street2="105 Suite")
e = to_event(variant)
check("both lines numbered: street kept",
      e is not None and e.address == "6807 Fayetteville Rd", e and e.address)

# Through the store and into the sync's own geocoder input: what Census is sent.
with tempfile.TemporaryDirectory() as tmp:
    path = os.path.join(tmp, "races.json")
    store = EventStore(path)
    for r in (SPOOKY, CIDER):
        store.upsert(to_event(r))
    store.save()
    recs = {rec["name"]: rec for rec in json.load(open(path, encoding="utf-8"))["events"]}
parts = SYNC._addr_parts(recs.get(SPOOKY["name"]) or {})
check("the sync's _addr_parts sends Census ('123 Mill St', 'Gahanna', 'OH')",
      parts == ("123 Mill St", "Gahanna", "OH"), parts)
parts = SYNC._addr_parts(recs.get(CIDER["name"]) or {})
check("... and ('4000 Parnell Ave', 'Fort Wayne', 'IN') for a numbered street",
      parts == ("4000 Parnell Ave", "Fort Wayne", "IN"), parts)


# --- 2. a day still to come -------------------------------------------------

check("yesterday's in-person 5k with a virtual week running on: not written",
      to_event(FIT4FALL) is None, to_event(FIT4FALL) and to_event(FIT4FALL).start_local)
check("a season that began 1/1 (a year of walks): not written",
      to_event(STRIDERS) is None, to_event(STRIDERS) and to_event(STRIDERS).start_local)

e = to_event(NAMI)
check("NAMI Ending the Silence: dated by the walk on 11/7, not the early-bird entry on 8/3",
      e is not None and e.start_local == "2026-11-07T10:00:00", e and e.start_local)
check("... with the 11/7 date in its fingerprint",
      e is not None and e.fingerprint == make_fingerprint(
          NAMI["name"], "2026-11-07", NAMI["address"]["street"], "Pensacola"),
      e and e.fingerprint)

e = to_event(WIT)
check("a sub-event at 08:00 today is upcoming: kept, dated today",
      e is not None and e.start_local == "2026-10-11T08:00:00", e and e.start_local)

undated = json.loads(json.dumps(SPOOKY))
for sub in undated["events"]:
    sub["start_time"] = None
undated["next_date"] = "10/10/2026"
check("no sub-event time and a next_date already past: not written",
      to_event(undated) is None, to_event(undated) and to_event(undated).start_local)
undated["next_date"] = "10/17/2026"
e = to_event(undated)
check("no sub-event time and a future next_date: an all-day row on it, as before",
      e is not None and e.start_local == "2026-10-17", e and e.start_local)

e = to_event(STRIDES)
check("walk, run and rides all over, only a gala to come: not written",
      e is None, e and (e.start_local, e.category))
check("... and refused as begun_before_today",
      RS.refusal(STRIDES, CFG, date.fromisoformat(TODAY)) == "begun_before_today",
      RS.refusal(STRIDES, CFG, date.fromisoformat(TODAY)))
e = to_event(SEMINARS)
check("no typed sub-event at all ('other' only): dated by its next one, as before",
      e is not None and e.start_local == "2026-10-29T09:00:00", e and e.start_local)

LIVE = [COLON, SPOOKY, CIDER, LARS, FIT4FALL, STRIDERS, NAMI, WIT, STRIDES, SEMINARS]
written = [to_event(r) for r in LIVE]
past = [x.name for x in written if x and x.start_local[:10] < TODAY]
check("over all ten live payloads, no row dated before today", not past, past)

# The run's own log line names why, so a filter that swallows a state is visible.
with tempfile.TemporaryDirectory() as tmp:
    store = EventStore(os.path.join(tmp, "races.json"))
    real_fetch = RS.fetch
    RS.fetch = lambda session, cfg: LIVE
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            kept = RS.ingest(store, None, dict(CFG))
    finally:
        RS.fetch = real_fetch
log = out.getvalue()
check("ingest() keeps the seven upcoming races", kept == 7, (kept, log))
check("ingest() counts the three that had begun as 'begun_before_today'",
      "3 begun_before_today" in log, log)


# --- 3. a state over the cap ------------------------------------------------

class FakeAPI:
    """/rest/races as measured on 2026-10-11: a race matches when its dates
    OVERLAP [start_date, end_date] (search_start_date_only defaults to F), rows
    come sorted by name, and any one query stops at 1,000 with no total."""

    def __init__(self, races):
        self.races = races
        self.asked = []

    def get(self, url, params=None, timeout=None):
        p = dict(params or {})
        start = p["start_date"]
        start = date.fromisoformat(TODAY) if start == "today" else date.fromisoformat(start)
        end = date.fromisoformat(p["end_date"])
        self.asked.append((p.get("state"), p["start_date"], p["end_date"], int(p["page"])))
        hits = []
        for r in self.races:
            if p.get("state") and r["address"]["state"] != p["state"]:
                continue
            first = datetime.strptime(r["next_date"], "%m/%d/%Y").date()
            last = datetime.strptime(r["next_end_date"], "%m/%d/%Y").date()
            if first <= end and last >= start:
                hits.append(r)
        hits = sorted(hits, key=lambda r: r["name"].lower())[:RS.RESULT_CAP]
        per, page = int(p["results_per_page"]), int(p["page"])
        body = {"races": [{"race": r} for r in hits[(page - 1) * per: page * per]]}
        return FakeResponse(body)


class FakeResponse:
    status_code = 200

    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self.body


def florida(n_total, tail_names):
    """n_total races over the 120 days, a tenth of them weeks long (straddling
    any cut), the last few named as the live tail was."""
    out, day0 = [], date.fromisoformat(TODAY)
    for i in range(n_total):
        name = tail_names[i - (n_total - len(tail_names))] if i >= n_total - len(tail_names) \
            else f"FL Race {i:04d}"
        first = day0 + timedelta(days=(i * 7) % 120)
        last = first + timedelta(days=30 if i % 10 == 0 else 0)
        out.append(race(500000 + i, name, first.strftime("%m/%d/%Y"), last.strftime("%m/%d/%Y"),
                        "1 Main St", None, "Tampa", "FL",
                        [ev("5K", "running_race", first.strftime("%m/%d/%Y") + " 08:00")]))
    return out


TAIL = ["West Augustine CDC 2K Health and Wellness Walk", "Your Next Step is the Cure 5K",
        "Zonta ABC Run/Walk"]
api = FakeAPI(florida(1042, TAIL))
cfg = dict(CFG, states=["FL"], pause_s=0)
out = io.StringIO()
with contextlib.redirect_stdout(out):
    got = RS.fetch(api, cfg)
names = {r["name"] for r in got}
ids = [r["race_id"] for r in got]
check("Florida with 1,042 races in the window: all 1,042 are read", len(got) == 1042, len(got))
check("... including the W-Z tail the cap cut off by name",
      all(t in names for t in TAIL), sorted(set(TAIL) - names))
check("... each race once, though the weeks-long ones come back from both halves",
      len(ids) == len(set(ids)), len(ids) - len(set(ids)))
windows = sorted({(s, a, b) for s, a, b, _ in api.asked})
check("... by asking the whole window, then its two halves, which tile it",
      windows == [("FL", "2026-10-11", "2026-12-10"), ("FL", "2026-10-11", "2027-02-08"),
                  ("FL", "2026-12-11", "2027-02-08")], windows)
check("... and no NOTE about a lost tail", "NOTE" not in out.getvalue(), out.getvalue())

api = FakeAPI(florida(300, []))
with contextlib.redirect_stdout(io.StringIO()):
    got = RS.fetch(api, dict(CFG, states=["FL"], pause_s=0))
check("a state under the cap is asked once, two pages, unsplit",
      len(got) == 300 and len(api.asked) == 2, (len(got), api.asked))

one_day = [race(600000 + i, f"Same Day Race {i:04d}", "10/20/2026", "10/20/2026",
                "1 Main St", None, "Austin", "TX",
                [ev("5K", "running_race", "10/20/2026 08:00")]) for i in range(1001)]
api = FakeAPI(one_day)
out = io.StringIO()
with contextlib.redirect_stdout(out):
    got = RS.fetch(api, dict(CFG, states=["TX"], pause_s=0))
check("1,001 races on ONE day: the halving stops at a day and says so",
      "NOTE" in out.getvalue() and "TX" in out.getvalue(), out.getvalue()[-300:])
check("... having asked a bounded number of pages (7 halvings of 120 days)",
      len(api.asked) < 80, len(api.asked))

print()
print(f"{len(fails)} failure(s)" if fails else "all ok")
sys.exit(1 if fails else 0)
