#!/usr/bin/env python3
"""
test_ingest_revize.py - the ways a town's Revize calendar is not what it looks
like.

Every fixture is the SHAPE of a row read live from the City of Manassas, VA's
calendar_data_handler.php on 2026-10-04 (descriptions trimmed). The expensive
cases answer 200 and look healthy: a community-centre timetable that exists
only as RRULEs, an UNTIL at midnight that the town's own page reads strictly,
an EXDATE typed on the wrong day that the description corrects, a Friday Open
Gym whose 08:30 start is a typed AM, an office-closure "holiday" and a voting
deadline on the Community Events calendar, a $30 concert whose prose says "the
land of the free", and a session on the Community Center calendar with no
location that must NOT be pinned to the centre. Sections 9-13 replay what the
2026-10-05 review found: Photon's far answers (Pacific County's centroid, a
memorial in Tacoma, Massapequa NY for a street ending "NE"), a roofer for the
cemetery, amounts that are awards or income ceilings, Christmas sessions on
days the city says it is closed, the city named host of other people's events,
a timetable no door received, and the city's own "registration ... required".

    python test_ingest_revize.py
"""
import json
import os
import re
import sys
from datetime import datetime, timedelta
from urllib.parse import quote

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                             # noqa: BLE001
        pass

os.environ["MAPSEE_TODAY"] = "20261004"
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import mapsee_ingest_revize as RV  # noqa: E402

fails = []


def check(label, cond, detail=""):
    print(f"{'ok  ' if cond else 'FAIL'} {label}{'' if cond else '   ' + str(detail)}")
    if not cond:
        fails.append(label)


NOW = datetime(2026, 10, 4)
HORIZON = NOW + timedelta(days=90)
SITE = {
    "key": "manassas", "name": "City of Manassas, VA", "origin": "https://www.manassasva.gov",
    "webspace": "manassasva", "revize_base": "//cms9.revize.com",
    "calendar_page": "https://www.manassasva.gov/calendar.php", "timezone": "America/New_York",
    "city": "Manassas", "region": "VA", "country": "US", "geocode_suffix": ", Manassas, VA",
    "calendars": {"Community Center": "community", "Community Events": "community",
                  "Harris Pavilion": "community"},
    "venues": [{"match": "8750 Sudley", "name": "Manassas Community Center", "address": "8750 Sudley Rd",
                "postal_code": "20110", "lat": 38.766816, "lon": -77.484091, "coords_exact": False},
               {"match": "9501 Dean Park", "name": "Boys & Girls Club, Dean Park Lane",
                "address": "9501 Dean Park Ln", "lat": 38.742868, "lon": -77.491569, "coords_exact": True}],
    "clock_fixes": [{"id": "1115", "from": "08:30", "to": "20:30", "why": "test"}],
}
CC = "8750 Sudley Rd, Manassas, VA 20110"


def row(**over):
    base = {"title": "Toddler Drop-In", "primary_calendar_name": "Community Center",
            "calendar_displays": ["14"], "start": "2026-07-01T10:30:00", "end": "2026-07-01T12:30:00",
            "url": "https://www.google.com/maps/place/City+of+Manassas+Community+Center/@38.76,-77.48,17z",
            "location": CC, "image": "", "rid": "1114", "id": "1114",
            "desc": quote("Bring your little ones Mondays, Tuesdays, Thursdays and Fridays from "
                          "10:30 a.m.-12:30 p.m. Parental supervision is required."),
            "rrule": ("DTSTART:20260701T103000\nRDATE:20260701T103000\n"
                      "RRULE:FREQ=WEEKLY;INTERVAL=1;BYDAY=MO,TU,TH,FR\nEXDATE:20260701T103000\n"
                      "EXDATE:20261012T103000\nEXDATE:20261123T103000\nEXDATE:20261124T103000\n"
                      "EXDATE:20261126T103000\nEXDATE:20261127T103000"),
            "color": "#3787d8", "duration": "02:00", "options": ""}
    base.update(over)
    return {k: v for k, v in base.items() if v is not None}


GEO_CALLS = []


def geocode(loc):
    GEO_CALLS.append(loc)
    return {"9419 Battle St, Manassas, VA 20110": (38.7497, -77.4744),
            "Manassas City Library, 10104 Dumfries Road, Manassas, VA 20110": (38.7329, -77.4685),
            }.get(loc, (None, None))


def build(rows, site=SITE):
    return RV.build_events(site, rows, NOW, HORIZON, geocode)


def days(events):
    return [e.start_local[:10] for e in events]


# ------------------------------------------------------------ 1. the rule subset
print("1. RRULE expansion: the subset Revize writes, exactly")


def expand(text, until="20270201T000000"):
    return RV.expand_rule(RV.parse_rule_block(text), RV._dt(until))


got = [d for d in expand(row()["rrule"]) if d >= NOW]
# 63 = python-dateutil's answer for the same string and window (checked by hand
# 2026-10-04; dateutil is not a pipeline dependency, so it is not imported here).
check("a weekly MO,TU,TH,FR timetable expands (Toddler Drop-In: 63 sessions 10-04..02-01)",
      len(got) == 63, len(got))
check("EXDATEs are honoured (Columbus Day and Thanksgiving week absent)",
      not {d.strftime("%m-%d") for d in got} & {"10-12", "11-23", "11-24", "11-26", "11-27"})
check("a Wednesday is never a Mon/Tue/Thu/Fri session", all(d.weekday() in (0, 1, 3, 4) for d in got))
# Lesson 2: UNTIL is an instant. STEM Looks Like Me! - its text lists Oct 17, 24 only.
stem = expand("DTSTART:20261017T140000\nRDATE:20261017T140000\n"
              "RRULE:FREQ=WEEKLY;INTERVAL=1;BYDAY=SA;UNTIL=20261031T000000")
check("UNTIL at midnight excludes that day's 14:00 session (the town's page agrees)",
      [d.strftime("%m-%d") for d in stem] == ["10-17", "10-24"], stem)
second_tue = expand("DTSTART:20250128T180000\nRDATE:20250128T180000\n"
                    "RRULE:FREQ=MONTHLY;INTERVAL=1;BYSETPOS=2;BYDAY=TU\nEXDATE:20251111T180000")
check("BYSETPOS=2;BYDAY=TU is the second Tuesday, EXDATE removes one",
      all(d.weekday() == 1 and 8 <= d.day <= 14 for d in second_tue[1:])
      and datetime(2025, 11, 11, 18) not in second_tue and datetime(2026, 10, 13, 18) in second_tue)
check("INTERVAL=2 on a monthly rule skips a month",
      [d.month for d in expand("DTSTART:20260106T180000\nRRULE:FREQ=MONTHLY;INTERVAL=2;BYSETPOS=1;BYDAY=TU",
                               "20261231T000000")] == [1, 3, 5, 7, 9, 11])
check("YEARLY BYMONTH=11;BYSETPOS=4;BYDAY=TH is Thanksgiving (2026-11-26)",
      datetime(2026, 11, 26) in expand("DTSTART:20251127T000000\nRRULE:FREQ=YEARLY;BYSETPOS=4;BYDAY=TH;BYMONTH=11"))
check("BYSETPOS=-1 is the last Monday of May (2026-05-25)",
      datetime(2026, 5, 25, 13, 23) in expand("DTSTART:20250526T132300\nRRULE:FREQ=YEARLY;BYSETPOS=-1;BYDAY=MO;BYMONTH=5"))
check("HOURLY;INTERVAL=4;COUNT=2 is a matinee and an evening show",
      expand("DTSTART:20260207T140000\nRDATE:20260207T140000\nRRULE:FREQ=HOURLY;INTERVAL=4;COUNT=2")
      == [datetime(2026, 2, 7, 14), datetime(2026, 2, 7, 18)])
check("COUNT counts sessions, not weeks (Sunday Voting: 2)",
      len(expand("DTSTART:20261018T120000\nRDATE:20261018T120000\nRRULE:FREQ=WEEKLY;INTERVAL=1;BYDAY=SU;COUNT=2")) == 2)
check("INTERVAL=2 weekly is fortnightly (Lego Club)",
      [d.day for d in expand("DTSTART:20260711T110000\nRRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=SA", "20260830T000000")]
      == [11, 25, 8, 22])
try:
    RV.parse_rule_block("DTSTART:20260101T100000\nRRULE:FREQ=WEEKLY;BYHOUR=10,14")
    check("a rule part it does not know is refused, not guessed (BYHOUR)", False)
except RV.RuleError:
    check("a rule part it does not know is refused, not guessed (BYHOUR)", True)
ev, ref, notes = build([row(rrule="DTSTART:20260701T103000\nRRULE:FREQ=WEEKLY;BYWEEKNO=20")])
check("...and build_events counts it and writes nothing", not ev and notes.get("rule not understood") == 1, notes)

# ------------------------------------------------------------ 2. DST and identity
print()
print("2. wall clock, DST and identity")
ev, ref, notes = build([row()])
by_day = {e.start_local[:10]: e for e in ev}
check("the timetable's sessions in 90 days are rows (Toddler Drop-In: 47)", len(ev) == 47, len(ev))
check("10:30 EDT on 2026-10-30 is 14:30Z", by_day["2026-10-30"].start_utc == "2026-10-30T14:30:00Z",
      by_day["2026-10-30"].start_utc)
check("10:30 EST on 2026-11-02 is 15:30Z (DST ended 1 Nov)",
      by_day["2026-11-02"].start_utc == "2026-11-02T15:30:00Z", by_day["2026-11-02"].start_utc)
check("the wall clock stays 10:30-12:30 across the change",
      all(e.start_local[11:16] == "10:30" and e.end_local[11:16] == "12:30" for e in ev))
check("source_id is <site>:<row>:<occurrence>", by_day["2026-10-05"].source_id == "manassas:1114:20261005T1030",
      by_day["2026-10-05"].source_id)
ev2, _, _ = build([row()])
check("a re-read writes identical ids and fingerprints",
      [(e.source_id, e.fingerprint) for e in ev] == [(e.source_id, e.fingerprint) for e in ev2])
tours = [row(id=str(1150 + i), rid=str(1150 + i), title="Cemetery Tours", primary_calendar_name="Community Events",
             start=f"2026-10-16T{h}:00", end=f"2026-10-16T{h2}:00", rrule=None, url="",
             location="9419 Battle St, Manassas, VA 20110", desc="Cemetery Tours $10 per ticket")
         for i, (h, h2) in enumerate((("18:30", "19:30"), ("19:30", "20:30")))]
ev, _, _ = build(tours)
check("the 6:30 and 7:30 tours are two rows with two fingerprints",
      len(ev) == 2 and ev[0].fingerprint != ev[1].fingerprint, [e.fingerprint for e in ev])
check("...and each is one session, not a joined stretch",
      [(e.start_local[11:16], e.end_local[11:16]) for e in ev] == [("18:30", "19:30"), ("19:30", "20:30")])
old = row(id="9", rid="9", rrule=None, start="2026-09-01T10:00:00", end="2026-09-01T11:00:00")
far = row(id="10", rid="10", rrule=None, start="2027-02-01T10:00:00", end="2027-02-01T11:00:00")
ev, _, _ = build([old, far])
check("a past row and one beyond the horizon are not written", not ev, days(ev))

# ------------------------------------------------------------ 3. text that corrects the rule
print()
print("3. the description's own corrections")
zumba = row(id="1164", rid="1164", title="Zumba 18+", start="2026-09-03T17:15:00", end="2026-09-03T18:00:00",
            desc=quote(" Zumba 18+ Mondays and Thursdays 5:15-6pm (No program 9/7, 10/12, 11/23 and 11/26) "),
            rrule=("DTSTART:20260903T171500\nRDATE:20260903T171500\n"
                   "RRULE:FREQ=WEEKLY;INTERVAL=1;BYDAY=MO,TH;UNTIL=20261130T000000\n"
                   "EXDATE:20260907T171500\nEXDATE:20261012T171500\nEXDATE:20261113T171500\n"
                   "EXDATE:20261126T171500"))
ev, _, notes = build([zumba])
check("the EXDATE typo (11/13, a Friday) does not leave Monday 11/23 in: the text says no program",
      "2026-11-23" not in days(ev) and notes.get("skipped: the description says no session that day") == 1,
      days(ev))
check("...and every other Monday/Thursday to 11/19 stays (13 sessions)", len(ev) == 13, len(ev))
check("no_program_days reads 'No program 9/7, 10/12, 11/23 and 11/26'",
      RV.no_program_days("(No program 9/7, 10/12, 11/23 and 11/26)") == {(9, 7), (10, 12), (11, 23), (11, 26)})
gym = row(id="1115", rid="1115", title="Open Gym", start="2026-07-10T08:30:00", end="2026-07-10T22:00:00",
          location="9501 Dean Park Ln, Manassas, VA 20110", desc="",
          rrule="DTSTART:20260710T083000\nRDATE:20260710T083000\nRRULE:FREQ=WEEKLY;INTERVAL=1;BYDAY=FR\n"
                "EXDATE:20260703T083000")
ev, ref, notes = build([gym])
check("the typed-AM Open Gym is fixed by id: 20:30-22:00 every Friday",
      len(ev) == 13 and all((e.start_local[11:16], e.end_local[11:16]) == ("20:30", "22:00") for e in ev),
      [(e.start_local, e.end_local) for e in ev[:2]])
check("...pinned to the Boys & Girls Club, not the community centre",
      ev and ev[0].venue_name == "Boys & Girls Club, Dean Park Lane" and ev[0].coords_exact)
ev, ref, _ = build([dict(gym, start="2026-07-10T19:30:00",
                         rrule=gym["rrule"].replace("T083000", "T193000"), end="2026-07-10T22:00:00")])
check("once the town edits the row (19:30), the fix no longer moves it",
      len(ev) == 13 and ev[0].start_local[11:16] == "19:30", ev[:1] and ev[0].start_local)
ev, ref, _ = build([dict(gym, id="2000", rid="2000")])
check("an unfixed 13.5 h row is refused as a span, never written as a session",
      not ev and ref.get("a 12-24 h span, not a session") == 13, ref)

# ------------------------------------------------------------ 4. what is not an event
print()
print("4. refusals, each counted")
ce = dict(primary_calendar_name="Community Events", rrule=None, url="")
cases = [
    ("closure", row(id="641", rid="641", title="Christmas Holidays", start="2026-12-24T00:00:00",
                    end="2026-12-25T23:55:00", location="", desc="Christmas holidays observance, all City offices closed.", **ce)),
    ("governance", row(id="720", rid="720", title="Airport Executive Committee", start="2026-10-06T08:30:00",
                       end="2026-10-06T10:00:00", desc="", **ce)),
    ("governance", row(id="770", rid="770", title="Friends of Manassas City Library Meeting",
                       start="2026-11-02T18:00:00", end="2026-11-02T19:00:00", desc="", **ce)),
    ("civic service, not an event", row(id="1161", rid="1161",
                                        title="Deadline to Register to Vote / Update Your Voter Information",
                                        start="2026-10-23T08:30:00", end="2026-10-23T17:00:00", desc="", **ce)),
    ("civic service, not an event", row(id="1159", rid="1159", title="Sunday Voting", start="2026-10-18T12:00:00",
                                        end="2026-10-18T17:00:00", desc="", **ce)),
    ("civic service, not an event", row(id="1052", rid="1052", title="Household Hazardous Waste Drop Off (No Shredding)",
                                        start="2026-11-07T08:00:00", end="2026-11-07T12:00:00", desc="", **ce)),
    ("take-home kit, not a gathering", row(id="137", rid="137", title="Culinary Spice Club",
                                           start="2026-11-01T00:00:00", end=None, allDay=True,
                                           desc="Each month, pick up a culinary spice kit from the Manassas City Library.", **ce)),
    ("registration required", row(id="50", rid="50", title="Pottery Wheel Course", start="2026-10-20T18:00:00",
                                  end="2026-10-20T20:00:00", desc="Registration is required. Six weeks.", **ce)),
]
for want, r in cases:
    ev, ref, _ = build([r])
    check(f"{r['title'][:44]!r} -> {want}", not ev and ref.get(want) == 1, (len(ev), ref))
story = row(id="148", rid="148", title="Toddler Story Time", primary_calendar_name="Community Events", url="",
            location="Manassas City Library, 10104 Dumfries Road, Manassas, VA 20110",
            desc="Join us for a lively and interactive toddler play time. No registration required.",
            start="2026-10-08T11:00:00", end="2026-10-08T11:30:00", rrule=None)
ev, ref, _ = build([story])
check("'No registration required' is not a refusal", len(ev) == 1 and not ref, ref)
meet = row(id="6", rid="6", title="School Board Meeting", primary_calendar_name="Public Meetings",
           start="2026-10-13T18:00:00", end="2026-10-13T19:00:00", rrule=None)
GEO_CALLS.clear()
ev, ref, notes = build([meet])
check("a calendar the config does not name is never read (Public Meetings)",
      not ev and not ref and not notes and not GEO_CALLS)

# ------------------------------------------------------------ 5. price wording
print()
print("5. 'free' only in the town's words; a price says 'not free'")
# 0227's negation, VERBATIM from ../mapsee/tools/measure_deals.py (FREE_NEG), and
# its "Admission: free" alternative of FREE. The full predicate is compared
# below whenever ../mapsee is checked out beside this repo.
FREE_NEG = re.compile(r"\b(?:not|isn'?t|is\s+not)\s+(?:a\s+)?free\b|\bnon[- ]gratuit|\bpas\s+gratuit|\bno\s+es\s+gratis", re.I)
FREE_ADMISSION = re.compile(r"\b(?:admission|entry|entrance|attendance|cost|price|cover(?:\s+charge)?)\s*(?:is|:|-|–)?\s*free\b", re.I)
lines = {
    "lego": RV.price_line("Lego Club", "Open to kids ages 5-13, this free program sparks creativity. *No Sign Up Is Required*"),
    "talk": RV.price_line("Artist Talk & Demonstration", "October 10, 2 pm Free Celebrate Hispanic Heritage Month"),
    "chorale": RV.price_line("Manassas Chorale 'Let Freedom Ring!'",
                             "celebrates the land of the free and the home of the brave. $30, $28 adult; "
                             "$26.25, $24.55 military; free GMU student and youth"),
    "tour": RV.price_line("Cemetery Tours", "$10 per ticket Explore City Cemetery"),
    "silent": RV.price_line("Toddler Drop-In", "Parental supervision is required."),
    "subset": RV.price_line("Gallery Night", "Free for members; everyone welcome."),
    "play": RV.price_line("Toddler Time", "Free play with blocks and balls."),
}
check("'this free program' -> 'Admission: free.'", lines["lego"] == ("🎟 Admission: free.", True), lines["lego"])
check("a bare 'Free' in the listing -> 'Admission: free.'", lines["talk"][1] is True)
check("'Admission: free.' is a phrase 0227 tags", FREE_ADMISSION.search(lines["lego"][0]) is not None)
check("$30 concert with 'land of the free' and free student seats says 'not free'",
      lines["chorale"][1] is False and FREE_NEG.search(lines["chorale"][0]) is not None, lines["chorale"])
check("$10 tour -> 'Price as listed: $10 (not free).'", lines["tour"] == ("🎟 Price as listed: $10 (not free).", False),
      lines["tour"])
check("silence says nothing", lines["silent"] == (None, None))
check("'Free for members' is not a free event", lines["subset"] == (None, None), lines["subset"])
check("'free play' is not a price", lines["play"] == (None, None), lines["play"])
ev, _, _ = build(tours[:1])
check("the stored description carries the fee line first",
      ev and ev[0].description.startswith("🎟 Price as listed: $10 (not free)."), ev[:1] and ev[0].description[:60])
TWIN = os.path.join(HERE, "..", "mapsee", "tools", "measure_deals.py")
if os.path.exists(TWIN):
    import importlib.util
    _spec = importlib.util.spec_from_file_location("_measure_deals_twin", TWIN)
    MD = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(MD)
    check("the vendored FREE_NEG equals ../mapsee/tools/measure_deals.py's",
          (MD.FREE_NEG.pattern, MD.FREE_NEG.flags) == (FREE_NEG.pattern, FREE_NEG.flags))
    verdicts = {}
    for k, (title, desc) in {"chorale": ("Manassas Chorale", "the land of the free. $30 adult; free GMU student"),
                             "lego": ("Lego Club", "this free program"),
                             "tour": ("Cemetery Tours", "$10 per ticket")}.items():
        line, _ = RV.price_line(title, desc)
        verdicts[k] = "free" in MD.classify({"title": title, "description": f"{line}\n\n{desc}"})[0]
    check("0227's whole predicate: the concert and the tour are not free, Lego Club is",
          verdicts == {"chorale": False, "lego": True, "tour": False}, verdicts)
else:
    print("skip 0227's whole predicate is not run: ../mapsee is not checked out here")

# ------------------------------------------------------------ 6. placement
print()
print("6. placement from the row's own location, never by a guess")
taichi = row(id="1175", rid="1175", title="Tai Chi for Arthritis Prevention (55+)", location="", url="",
             start="2026-10-06T13:30:00", end="2026-10-06T14:30:00", rrule=None,
             desc="Tuesdays & Thursdays | 1:30 - 2:30 pm. In partnership Prince William County Area Agency on Aging.")
GEO_CALLS.clear()
ev, _, notes = build([taichi])
check("no location on the Community Center calendar is unplaced, not pinned to the centre",
      not ev and notes.get("unplaced: no location") == 1 and not GEO_CALLS, notes)
town = row(id="1163", rid="1163", title="Fall Festival", primary_calendar_name="Community Events",
           location="Manassas, VA 20110", start="2026-11-03T10:00:00", end="2026-11-03T14:00:00", rrule=None, url="")
ev, _, notes = build([town])
check("a location that is only the town is unplaced, and the geocoder is not asked",
      not ev and notes.get("unplaced: location names only the town") == 1 and not GEO_CALLS, notes)
ev, _, _ = build([row(rrule=None, start="2026-10-05T10:30:00", end="2026-10-05T12:30:00")])
e = ev[0]
check("a bare address in the venue book is named and pinned (8750 Sudley Rd)",
      (e.venue_name, e.address, e.city, e.region, e.postal_code, e.latitude) ==
      ("Manassas Community Center", "8750 Sudley Rd", "Manassas", "VA", "20110", 38.766816), vars(e))
check("an interpolated book point is not coords_exact (the sync's Census pass may refine it)", e.coords_exact is False)
check("a Google Maps link is never the event link; the calendar page is",
      e.ticket_url == "https://www.manassasva.gov/calendar.php", e.ticket_url)
ev, _, _ = build(tours[:1])
check("an address not in the book is geocoded from the row's own text, the street kept for the sync",
      ev and GEO_CALLS[-1] == "9419 Battle St, Manassas, VA 20110" and ev[0].address == "9419 Battle St"
      and ev[0].latitude == 38.7497)
check("'www.' links get a scheme", RV.event_link({"url": "www.regionalwebtv.com/manassassb"}, SITE)
      == "https://www.regionalwebtv.com/manassassb")
sl = RV.split_location("9431 West St. Manassas, VA 20110", "Manassas")
check("a street written without a comma before the town keeps its number",
      (sl["street"], sl["city"], sl["postal"]) == ("9431 West St.", "Manassas", "20110"), sl)
sl = RV.split_location("Harris Pavilion, 9201 Center St, Manassas, VA 20110", "Manassas")
check("venue, street, town, state and ZIP split", (sl["venue"], sl["street"], sl["city"], sl["region"]) ==
      ("Harris Pavilion", "9201 Center St", "Manassas", "VA"), sl)
allday = row(id="88", rid="88", title="Harvest Day", primary_calendar_name="Community Events", allDay=True,
             start="2026-10-17T00:00:00", end=None, duration=None, rrule=None, url="", location=CC, desc="")
ev, _, _ = build([allday])
check("an all-day row is a date, not midnight", ev and ev[0].start_local == "2026-10-17" and ev[0].start_utc is None,
      ev[:1] and ev[0].start_local)

# ------------------------------------------------------------ 7. robots and refusals
print()
print("7. robots.txt before the request; a 403 is a refusal")


class FakeRobots:
    def __init__(self, allowed):
        self.allowed = allowed

    def check(self, url):
        return {"allowed": self.allowed, "status": "ok", "rule": "Disallow: /", "crawl_delay": None}


class FakeResp:
    def __init__(self, code, text="[]"):
        self.status_code, self.text, self.headers = code, text, {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeSession:
    def __init__(self, resp):
        self.resp, self.calls = resp, []

    def get(self, url, **kw):
        self.calls.append(url)
        return self.resp


RV.MIN_GAP_SECONDS = 0
s = FakeSession(FakeResp(200))
try:
    RV.fetch_site(s, FakeRobots(False), SITE, None)
    check("robots.txt Disallow: the site is skipped with no request", False)
except RV.Refused:
    check("robots.txt Disallow: the site is skipped with no request", s.calls == [], s.calls)
s = FakeSession(FakeResp(403, "Forbidden"))
try:
    RV.fetch_site(s, FakeRobots(True), SITE, None)
    check("a 403 is a refusal, asked once and not retried", False)
except RV.Refused:
    check("a 403 is a refusal, asked once and not retried", len(s.calls) == 1)
s = FakeSession(FakeResp(200, "<html>One moment, please...</html>"))
try:
    RV.fetch_site(s, FakeRobots(True), SITE, None)
    check("a challenge page is not parsed as a calendar", False)
except RV.Refused:
    check("a challenge page is not parsed as a calendar", True)
check("the handler URL is built on the town's own host",
      RV.handler_url(SITE) == "https://www.manassasva.gov/_assets_/plugins/revizeCalendar/calendar_data_handler.php"
      "?webspace=manassasva&relative_revize_url=//cms9.revize.com&protocol=https:")

# ------------------------------------------------------------ 8. the shipped config
print()
print("8. the shipped config")
cfg = json.load(open(os.path.join(HERE, "revize_sources.json"), encoding="utf-8"))
sites = cfg["sites"]
LENS = {"running", "fitness", "sports", "music", "food", "community", "party", "market", "outdoors", "arts",
        "theater", "kids", "learning", "volunteer", "other"}
keys = [s["key"] for s in sites]
check("site keys are unique - they namespace source_id", len(keys) == len(set(keys)), keys)
check("Manassas is configured", "manassas" in keys)
bad = [s["key"] for s in sites for c in s["calendars"] if re.search(r"meeting|council|closure|closed|holiday", c, re.I)]
check("no meetings or closures calendar is ever read", not bad, bad)
check("every calendar maps to a real lens key", all(v in LENS for s in sites for v in s["calendars"].values()))
rules = list(cfg.get("category_by_title") or []) + [r for s in sites for r in s.get("category_by_title") or ()]
check("every title rule compiles and names a real lens key",
      rules and all(re.compile(rx) and lens in LENS for rx, lens in rules), rules)
check("every site has a real IANA zone", all(RV._tz(s.get("timezone")) is not None for s in sites))
vb = [v.get("name") for s in sites for v in s.get("venues") or () if v.get("lat") is not None and not v.get("_source")]
check("every pinned venue carries its provenance", not vb, vb)
check("Manassas Park (robots.txt Disallow: /) is declined, not configured",
      any("Manassas Park" in n.get("name", "") and "Disallow" in n.get("why", "") for n in cfg["_not_included"])
      and not any("manassasparkva" in s.get("origin", "") for s in sites))
fx = [f for s in sites for f in s.get("clock_fixes") or ()]
check("every clock fix names the value it corrects and why", all(f.get("from") and f.get("to") and f.get("why") for f in fx))

# ------------------------------------------------------------ 9. a geocoder answer is not believed far away
print()
print("9. the town's radius, state codes vs street quadrants, names of no place")
MAN = RV.with_defaults(next(s for s in sites if s["key"] == "manassas"), cfg)
OLY = RV.with_defaults(next(s for s in sites if s["key"] == "olympia"), cfg)
PAC = RV.with_defaults(next(s for s in sites if s["key"] == "pacific-wa"), cfg)
BLA = RV.with_defaults(next(s for s in sites if s["key"] == "bladensburg"), cfg)
# Photon's real answers on 2026-10-04/05 (the review's replay): Pacific County's
# centroid for "Eastroom, Pacific, WA", Massapequa NY for a bare "2600 East Bay
# Drive NE", Portland OR for "222 Columbia St NW".
FAR = {"Eastroom": (46.5335, -123.7680), "2600 East Bay Drive NE": (40.688, -73.427),
       "Olympia Center, 222 Columbia St NW": (45.5163, -122.6795)}
asked = []


def far_geo(loc):
    asked.append(loc)
    return FAR.get(loc, (None, None))


def one(site, title, loc, start="2026-10-20T18:00:00", end="2026-10-20T19:00:00", cal=None, **kw):
    cal = cal or next(iter(site["calendars"]))
    return row(id="77", rid="77", title=title, primary_calendar_name=cal, location=loc, url="",
               start=start, end=end, rrule=None, desc=kw.get("desc", ""))


for site, loc in ((PAC, "Eastroom"), (OLY, "2600 East Bay Drive NE"), (OLY, "Olympia Center, 222 Columbia St NW")):
    ev, _, notes = RV.build_events(site, [one(site, "Community Bingo", loc)], NOW, HORIZON, far_geo)
    check(f"a geocoder answer far from the town is unplaced ({loc!r})",
          not ev and notes.get("unplaced: the geocoder answered outside the town's radius") == 1, notes)
sl = RV.split_location("2600 East Bay Drive NE", "Olympia", "WA")
check("'NE' after a street is a quadrant, not Nebraska", sl["region"] is None and sl["street"] == "2600 East Bay Drive NE", sl)
sl = RV.split_location("Olympia Center, 222 Columbia St NW", "Olympia", "WA")
check("'NW' stays in the street", (sl["venue"], sl["street"], sl["region"]) == ("Olympia Center", "222 Columbia St NW", None), sl)
sl = RV.split_location("1700 San Francisco Ave NE, Olympia, WA", "Olympia", "WA")
check("...while ', WA' after the town is the state", (sl["street"], sl["city"], sl["region"]) ==
      ("1700 San Francisco Ave NE", "Olympia", "WA"), sl)
sl = RV.split_location("4825 Edmonston Road, Hyattsville, Maryland", "Bladensburg", "MD")
check("a spelled-out state is a state", (sl["city"], sl["region"]) == ("Hyattsville", "MD"), sl)
check("'Mayfield Ct' is a court, not Connecticut", RV.split_location("9400 Mayfield Ct", "Manassas", "VA")["region"] is None)


class StubICS:
    """Stands in for mapsee_ingest_ics.make_location_geocoder: records the suffix each query gets."""
    sent = []

    @staticmethod
    def make_location_geocoder(session, suffix):
        return lambda loc: (StubICS.sent.append(loc + suffix), (None, None))[1]


import mapsee_ingest_ics as _ICS  # noqa: E402
_real_mlg = _ICS.make_location_geocoder
_ICS.make_location_geocoder = StubICS.make_location_geocoder
g = RV.make_geocoder(None, OLY)
for q in ("2600 East Bay Drive NE", "Olympia Center, 222 Columbia St NW", "2600 East Bay Dr NE, Olympia",
          "1700 San Francisco Ave NE, Olympia, WA"):
    g(q)
_ICS.make_location_geocoder = _real_mlg
check("a street with a quadrant and no town gets the town's suffix",
      StubICS.sent[:2] == ["2600 East Bay Drive NE, Olympia, WA", "Olympia Center, 222 Columbia St NW, Olympia, WA"],
      StubICS.sent)
check("a location naming its town gets only the state; one ending in a state is sent as written",
      StubICS.sent[2:] == ["2600 East Bay Dr NE, Olympia, WA", "1700 San Francisco Ave NE, Olympia, WA"], StubICS.sent)
for site, loc, want in ((BLA, "bladensburg, maryland", "location names only the town"),
                        (BLA, "Various Locations in Bladensburg", "location names no single place"),
                        (OLY, "Downtown Olympia", "location names a district, not a place")):
    asked.clear()
    ev, _, notes = RV.build_events(site, [one(site, "Arts Walk", loc)], NOW, HORIZON, far_geo)
    check(f"{loc!r} is unplaced ({want}) and the geocoder is not asked",
          not ev and notes.get(f"unplaced: {want}") == 1 and not asked, (notes, asked))

# ------------------------------------------------------------ 10. the shipped venue book
print()
print("10. the shipped venue book pins what Photon gets wrong")


def place(site, loc, answer=None):
    """Build one row; the geocoder answers `answer` (default: the town's centre) and records each query."""
    asked.clear()
    cal = "Community Events" if site is MAN else None
    ev, _, notes = RV.build_events(site, [one(site, "Cemetery Tours", loc, cal=cal)], NOW, HORIZON,
                                   lambda q: (asked.append(q), answer or tuple(site["centre"]))[1])
    return ev[0] if ev else None, notes


# Photon's answer for the cemetery's address was a roofer 2.5 km away (node 14084814418).
e, _ = place(MAN, "9317 Center St, Manassas, VA 20110", answer=(38.7414433, -77.506958))
check("the cemetery tours are pinned at the cemetery (OSM relation 11859971), not the roofer Photon finds",
      e and (e.latitude, e.longitude, e.coords_exact) == (38.7503501, -77.4803433, True) and not asked,
      e and (e.latitude, e.longitude))
e, _ = place(MAN, "Manassas City Library, 10104 Dumfries Rd, Manassas, VA 20110")
check("the library is pinned exact on its building, so the Census pass cannot move it 370 m",
      e and (e.latitude, e.coords_exact, e.venue_name) == (38.7372343, True, "Manassas City Library") and not asked)
e, _ = place(MAN, "Downtown Manassas, VA 20110")
check("'Downtown Manassas' is the book's deliberate downtown point, not the post office",
      e and (e.latitude, e.longitude) == (38.750553, -77.473818) and e.coords_exact is False and e.address is None
      and not asked, e and vars(e))
e, _ = place(MAN, "Manassas City Library, Community Center, 8750 Sudley Road")
check("a row naming both is the address's place (the community centre)", e and e.venue_name == "Manassas Community Center")
e, _ = place(PAC, "Gymnasium")
check("Pacific's 'Gymnasium' is geocoded by the book's query, the community centre's address",
      e and asked == ["305 Milwaukee Blvd. S., Pacific, WA 98047"] and e.venue_name == "Pacific Community Center",
      asked)
check("every site carries a centre for the radius guard", all(s.get("centre") for s in sites))

# ------------------------------------------------------------ 11. price words: an amount that is not a price
print()
print("11. an amount that is not a ticket is not a price")
cases = {
    "Armory (x9)": (("Armory Arts Intervention: The Village Scroll",
                     "The City of Olympia has been selected to receive a $75,000 Our Town award from the National "
                     "Endowment for the Arts. Event is free, open to the public."), ("🎟 Admission: free.", True)),
    "Auto Showcase": (("Car, Truck & Bike Auto Showcase",
                       "Show off your ride & more! This event is FREE to the public. A $20 registration fee will be "
                       "applied for showcase participants."), ("🎟 Admission: free.", True)),
    "Lazy Art Night": (("Lazy Art Night: \"ArtsGiving\"",
                        "(Free & open to the public) Bring a wrapped piece of your own handmade art (valued at $15-30)."),
                       ("🎟 Admission: free.", True)),
    "Factory of Fear": (("Factory of Fear - Outbreak", "Tickets are $25 Patrons can receive $3 off admission on Thursday"),
                        ("🎟 Price as listed: $25 (not free).", False)),
    "VITA": (("Housing: VITA Free Tax Preparation Services",
              "offers free tax preparation services for individuals and families earning less than $69,000 in 2025."),
             None),
}
for label, ((t, d), want) in cases.items():
    got = RV.price_line(t, d)
    if want is None:
        check(f"{label}: an income ceiling is never a price", "not free" not in (got[0] or ""), got)
    else:
        check(f"{label}: {want[0]}", got == want, got)
vita = row(id="795", rid="795", title="Housing: VITA Free Tax Preparation Services", primary_calendar_name="Community Events",
           start="2026-10-24T10:00:00", end="2026-10-24T14:00:00", rrule=None, url="",
           location="13370 Minnieville Rd, Woodbridge, VA 22192", desc=cases["VITA"][0][1])
ev, ref, _ = build([vita])
check("...and the VITA tax service is refused as a civic service", not ev and ref.get("civic service, not an event") == 1, ref)

# ------------------------------------------------------------ 12. the town's closures, its host, its doors
print()
print("12. closures apply to the town's own sessions; the host is the town only there; titles pick the door")
xmas = row(id="641", rid="641", title="Christmas Holidays", primary_calendar_name="Community Events", url="",
           start="2026-12-24T00:00:00", end="2026-12-25T23:55:00", rrule=None, location="", duration="47:55",
           desc="Christmas holidays observance, all City offices closed.")
newyear = row(id="87", rid="87", title="New Year's Day Holiday", primary_calendar_name="Community Events", url="",
              start="2026-01-01T00:00:00", end=None, allDay=True, duration=None, location="",
              rrule="DTSTART:20260101T000000\nRDATE:20260101T000000\nRRULE:FREQ=YEARLY;BYMONTH=1;BYMONTHDAY=1",
              desc="New Year's Day holiday, all City offices closed.")
run_thu = row(id="604", rid="604", title="Run Club", start="2025-11-20T19:00:00", end="2025-11-20T20:00:00",
              location="Manassas Community Center, 8750 Sudley Road, Manassas, VA 20110",
              desc="Meet at Marsteller Park Track each Thursday at 7 p.m. Email manassasrunclub@gmail.com",
              rrule="DTSTART:20251120T190000\nRDATE:20251120T190000\nRRULE:FREQ=WEEKLY;INTERVAL=1;BYDAY=TH")
ev, ref, notes = RV.build_events(MAN, [row(), xmas, newyear, run_thu], NOW, HORIZON, geocode)
td = {e.start_local[:10] for e in ev if e.name == "Toddler Drop-In"}
check("Toddler Drop-In is not written on 12-24, 12-25 or 01-01, the days the city says it is closed",
      not td & {"2026-12-24", "2026-12-25", "2027-01-01"} and "2026-12-22" in td, sorted(td)[-6:])
check("...and each skipped session is counted against the closure",
      notes.get("skipped: the town is closed that day (Christmas Holidays)") == 2
      and notes.get("skipped: the town is closed that day (New Year's Day Holiday)") == 1, notes)
check("the closure rows themselves are refused, not written", ref.get("closure") == 2, ref)
check("Run Club, a club the city only lists, still meets on 12-24", any(e.name == "Run Club" and
      e.start_local.startswith("2026-12-24") for e in ev))
by_name = {}
for e in ev:
    by_name.setdefault(e.name, e)
check("the city is the host of its own Community Center session", by_name["Toddler Drop-In"].promoter == "City of Manassas, VA")
check("...but not of a club on that calendar", by_name["Run Club"].promoter is None)
ev, _, _ = RV.build_events(MAN, [tours[0]], NOW, HORIZON, geocode)
check("...nor of a Community Events listing (host_name falls back to mapsee.me)", ev and ev[0].promoter is None)
cats = {}
for t in ("Run Club", "Open Gym", "Higher Level Basketball", "Zumba 18+", "Walk With Ease (55+)",
          "The Day Before Christmas - Musical", "Toddler Drop-In"):
    ev, _, _ = RV.build_events(MAN, [row(title=t, id="5", rid="5", rrule=None, start="2026-10-05T10:30:00",
                                         end="2026-10-05T11:30:00")], NOW, HORIZON, geocode)
    cats[t] = ev[0].category if ev else None
check("titles pick the door: running, sports, fitness, theater; the rest stay community",
      cats == {"Run Club": "running", "Open Gym": "sports", "Higher Level Basketball": "sports", "Zumba 18+": "fitness",
               "Walk With Ease (55+)": "fitness", "The Day Before Christmas - Musical": "theater",
               "Toddler Drop-In": "community"}, cats)
try:
    import mapsee_supabase_sync as SY
    p, _ = SY.derive_categories({"name": "Run Club", "category": cats["Run Club"],
                                 "description": "Lace up those sneakers and join the Run Club!"})
    check("...and the sync keeps Run Club on the running door", p == "running", p)
except Exception as exc:                                          # noqa: BLE001
    print(f"skip the sync is not importable here: {type(exc).__name__}")

# ------------------------------------------------------------ 13. the town's own wording for a course
print()
print("13. registration in the city's words; a teen club; a kit made on site; a rule refused, not guessed")
for t, d in (("Line Dancing 18+", "Learn easy-to-follow steps to line dancing. Registration with the City of Manassas "
                                  "Community Center required!"),
             ("Tween STEAM Club", "Registration with the Manassas City Library is required."),
             ("'In the Kitchen' Cooking Class", "Sign up is required at cityofmanassas.recdesk.com")):
    check(f"{t!r} is registration-only", RV.refusal(t, d, SITE) == "registration required", RV.refusal(t, d, SITE))
check("'*No Sign Up Is Required*' stays", RV.refusal("Lego Club", "*No Sign Up Is Required*", SITE) is None)
check("'Registration is not required.' stays",
      RV.refusal("Toddler Story Time", "Registration is not required. Attendance is first-come.", SITE) is None)
check("'Teen Advisory Group (TAG)' is a library teen club, not governance",
      RV.refusal("Teen Advisory Group (TAG)", "Join TAG, make your voice heard, meet new friends", SITE) is None)
check("'Make it there or take it home!' is a gathering with a kit",
      RV.refusal("Earth Day!", "Pick up a \"Take and Make\" activity kit. Make it there or take it home!", SITE) is None)
check("a title that says Canceled anywhere is refused",
      RV.refusal("Community Bingo Canceled", "", SITE) == "cancelled")
for text in ("DTSTART:20260101T100000\nRRULE:FREQ=YEARLY;BYMONTHDAY=1",
             "DTSTART:20260101T100000\nRRULE:FREQ=MONTHLY;BYDAY=-1WE;BYMONTHDAY=15;BYSETPOS=1",
             "DTSTART:20260101T100000\nRRULE:FREQ=WEEKLY;BYDAY=MO,TU;BYSETPOS=1"):
    try:
        expand(text)
        check(f"{text.split('RRULE:')[1]!r} is refused, not mis-expanded", False)
    except RV.RuleError:
        check(f"{text.split('RRULE:')[1]!r} is refused, not mis-expanded", True)

print()
print(f"{'FAILURES: ' + ', '.join(fails) if fails else 'all checks passed'}")
sys.exit(1 if fails else 0)
