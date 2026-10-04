#!/usr/bin/env python3
"""
test_ingest_perfectmind.py - the ways a PerfectMind drop-in widget is not what
it looks like.

Every fixture is the SHAPE of something read live off *.perfectmind.com on
2026-10-03 (Kamloops, Brampton, Surrey, Moose Jaw, Vaughan). The expensive
cases are the ones that answer 200 and look healthy: a pager that stops at the
first empty window, a childminding booking inside a drop-in calendar, an hourly
slot grid, a pool's closure notice, and a $0.00 pass-holder price that is not
free to everyone.

    python test_ingest_perfectmind.py
"""
import contextlib
import io
import json
import os
import re
import sys
import tempfile
import uuid
from datetime import date, datetime, timedelta

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                             # noqa: BLE001
        pass

os.environ["MAPSEE_TODAY"] = "20261003"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mapsee_ingest_perfectmind as PM  # noqa: E402
import robots_txt  # noqa: E402

fails = []


def check(label, cond, detail=""):
    print(f"{'ok  ' if cond else 'FAIL'} {label}{'' if cond else '   ' + str(detail)}")
    if not cond:
        fails.append(label)


TODAY = date(2026, 10, 3)
HORIZON = TODAY + timedelta(days=90)
TENANT = {"name": "City of Kamloops drop-in", "host": "cityofkamloops.perfectmind.com",
          "org_path": "/23702", "widget_path": "Reports", "widgets": ["w1"],
          "timezone": "America/Vancouver", "country": "CA", "region": "BC", "city": "Kamloops",
          "category": "community"}
CAL = {"id": "c1", "name": "Skating", "category": "Drop-in", "widget": "w1", "key": "sports"}
ADDR = {"AddressTag": "Memorial Arena", "Street": "740 Victoria St", "City": "Kamloops",
        "PostalCode": "V2C 2B6", "Latitude": 50.676288, "Longitude": -120.323154}


def row(**over):
    """One ClassesV2 row, trimmed from Kamloops' live answer of 2026-10-03."""
    base = {"EventId": "b60c7b75-37fd-693a-3f78-c81d38d39048", "EventName": "Family Stick, Puck and Ring",
            "Details": "This program is for children ages 12 and under to play with their adult "
                       "guardians. All participants are required to wear a helmet.",
            "OccurrenceDate": "20261010", "FormattedStartTime": "10:45 AM", "FormattedEndTime": "11:45 AM",
            "DurationInMinutes": 60, "PriceRange": "$0.00 - $7.50", "AllDayEvent": False,
            "HasAlternativeLocation": False, "AlternativeLocation": None, "MinAge": None, "MaxAge": None,
            "AgeRestrictions": "", "Facility": "Memorial Ice", "OrgName": "City of Kamloops",
            "Address": dict(ADDR), "Location": "Memorial Arena", "BookingType": 2}
    base.update(over)
    return base


def build(rows, now=datetime(2026, 10, 3, 0, 0), tenant=TENANT):
    return PM.build_events(tenant, [(CAL, r) for r in rows], now, HORIZON)


# ------------------------------------------------------------ 1. the pager
print("the pager is the page's own loadItems, or it undercounts")


class Resp:
    def __init__(self, status=200, body=None, text=None):
        self.status_code = status
        self.text = text if text is not None else json.dumps(body)
        self.content = self.text.encode("utf-8")

    def json(self):
        return json.loads(self.text)


class Scripted:
    """Answers each request from a list, recording what was asked."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.asked = []
        self.headers = {}

    def request(self, method, url, data=None, headers=None, timeout=None):
        self.asked.append((method, url, dict(data or {}), dict(headers or {})))
        a = self.answers.pop(0) if self.answers else Resp(body={"classes": [], "nextKey": "0001-01-01"})
        return a

    def get(self, url, timeout=None, allow_redirects=True):
        return Resp(404, text="<html>not found</html>")


def page(dates, next_key):
    return Resp(body={"classes": [row(OccurrenceDate=d) for d in dates],
                      "classesMaxEndDateString": "11/10/2026 08:15 PM", "nextKey": next_key})


EMPTY = Resp(body={"classes": [], "classesMaxEndDateString": "17/10/2026 12:00 AM", "nextKey": "0001-01-01"})


def client(session, **kw):
    kw.setdefault("pace", 0)
    return PM.Client(TENANT, session=session, sleep=lambda s: None, **kw)


# The live Kamloops Skating sequence, request for request (cap_smoke 2026-10-03).
s = Scripted([page(["20261003", "20261011"], "2026-10-11"), page(["20261013", "20261016"], "2026-10-16"),
              EMPTY, page(["20261017", "20261025"], "2026-10-25"), page(["20261026", "20261030"], "2026-10-30"),
              EMPTY, page(["20261031"], "2026-10-31"), EMPTY, EMPTY])
rows, why, stop = PM.walk_calendar(client(s), "u", "c1", "w1", TODAY, HORIZON)
asked = [(d["page"], d["dateString"], d.get("after")) for _m, _u, d, _h in s.asked]
check("first ask is page 0 from today with no cursor", asked[0] == (0, "2026-10-03", None), asked[0])
check("the cursor is the previous nextKey and dateString is the day after it",
      asked[1] == (0, "2026-10-12", "2026-10-11"), asked[1])
check("an empty answer moves the page on and keeps the cursor",
      asked[3] == (1, "2026-10-17", "2026-10-16"), asked[3])
check("...and the page is NOT reset by the next full answer, as in loadItems",
      asked[4] == (1, "2026-10-26", "2026-10-25"), asked[4])
check("two empty answers in a row end the list", why == "two empty answers in a row" and len(s.asked) == 9,
      (why, len(s.asked)))
check("every row of every window is kept", len(rows) == 9, len(rows))
check("nothing about it needed a token", not any("__RequestVerificationToken" in d for _m, _u, d, _h in s.asked))

s = Scripted([page(["20261003"], "2026-10-03"), page(["20261004"], "2026-10-03")])
rows, why, _ = PM.walk_calendar(client(s), "u", "c1", "w1", TODAY, HORIZON)
check("a cursor that does not advance stops rather than looping to the cap",
      why.startswith("nextKey did not advance") and len(s.asked) == 2, (why, len(s.asked)))
s = Scripted([page(["20261003"], "2027-01-02")])
rows, why, _ = PM.walk_calendar(client(s), "u", "c1", "w1", TODAY, HORIZON)
check("a nextKey past the horizon ends the walk", why == "horizon reached" and len(s.asked) == 1, why)
s = Scripted([page(["20261003"], "0001-01-01")])
rows, why, _ = PM.walk_calendar(client(s), "u", "c1", "w1", TODAY, HORIZON)
check("the 0001-01-01 sentinel is the end of the schedule", why == "end of schedule", why)

# --------------------------------------------------- 2. token, refusals, caps
print()
print("no token unless the tenant insists; a refusal is an answer")
s = Scripted([Resp(500, text="<html>The required anti-forgery form field is not present.</html>"),
              Resp(200, text='<input name="__RequestVerificationToken" type="hidden" value="TOK" />'),
              page(["20261003"], "0001-01-01")])
c = client(s)
rows, why, _ = PM.walk_calendar(c, "u", "c1", "w1", TODAY, HORIZON)
check("a 500 is answered by reading the start page once for the token",
      [m for m, *_ in s.asked] == ["POST", "GET", "POST"] and c.token_fallbacks == 1, s.asked)
check("...and the retry carries it in the form and the header",
      s.asked[2][2].get("__RequestVerificationToken") == "TOK"
      and s.asked[2][3].get("__RequestVerificationToken") == "TOK")
check("...and the rows arrive", len(rows) == 1, len(rows))
for code in (401, 403, 429):
    s = Scripted([Resp(code, text="no")])
    rows, why, stop = PM.walk_calendar(client(s), "u", "c1", "w1", TODAY, HORIZON)
    check(f"HTTP {code} ends the TENANT and is asked exactly once",
          isinstance(stop, PM.Stop) and len(s.asked) == 1, (why, len(s.asked)))
s = Scripted([Resp(200, text="<html><title>Just a moment...</title></html>")])
rows, why, stop = PM.walk_calendar(client(s), "u", "c1", "w1", TODAY, HORIZON)
check("a bot challenge is a refusal, not a parse error", isinstance(stop, PM.Stop) and "challenge" in why, why)
s = Scripted([Resp(503, text="busy"), page(["20261003"], "0001-01-01")])
c = client(s)
rows, why, stop = PM.walk_calendar(c, "u", "c1", "w1", TODAY, HORIZON)
check("a 503 costs one pause and one more try", len(rows) == 1 and c.retries == 1, (why, c.retries))
s = Scripted([Resp(503, text="busy"), Resp(503, text="busy")])
rows, why, stop = PM.walk_calendar(client(s), "u", "c1", "w1", TODAY, HORIZON)
check("...and a second one costs the CALENDAR, not the tenant", stop is None and why.startswith("stopped early"), why)
s = Scripted([page(["20261003"], "2026-10-03"), page(["20261004"], "2026-10-04"), page(["20261005"], "2026-10-05")])
rows, why, stop = PM.walk_calendar(client(s, max_requests=2), "u", "c1", "w1", TODAY, HORIZON)
check("the request cap stops the tenant LOUDLY and keeps what was read",
      isinstance(stop, PM.Stop) and "CAP HIT" in why and len(rows) == 2, (why, len(rows)))
ticks = iter([0.0, 0.0, 100.0, 100.0])
c = PM.Client(TENANT, session=Scripted([page(["20261003"], "2026-10-03"), page(["20261004"], "2026-10-04")]),
              deadline=50.0, pace=0, sleep=lambda s: None, clock=lambda: next(ticks, 100.0))
rows, why, stop = PM.walk_calendar(c, "u", "c1", "w1", TODAY, HORIZON)
check("no request starts after the run deadline", isinstance(stop, PM.Stop) and "deadline" in why
      and len(rows) == 1, (why, len(rows)))
paced = []
c = PM.Client(TENANT, session=Scripted([page(["20261003"], "2026-10-03"), page(["20261004"], "0001-01-01")]),
              pace=1.1, sleep=paced.append, clock=lambda: 10.0)
PM.walk_calendar(c, "u", "c1", "w1", TODAY, HORIZON)
check("requests to one host are paced PACE_SECONDS apart", [round(x, 3) for x in paced] == [1.1], paced)

# ------------------------------------------------------- 3. which calendars
print()
print("BookingType 2 only, each calendar once, private bookings never")


class Tenant(Scripted):
    def __init__(self, cats, robots_body=None):
        super().__init__([])
        self.cats, self.robots_body = cats, robots_body

    def request(self, method, url, data=None, headers=None, timeout=None):
        self.asked.append((method, url, dict(data or {}), dict(headers or {})))
        if "GetCategoriesDataV2" in url:
            return Resp(body=self.cats[data["widgetId"]])
        return Resp(body={"classes": [row(EventId=data["calendarId"])], "nextKey": "0001-01-01"})

    def get(self, url, timeout=None, allow_redirects=True):
        if self.robots_body is None:
            return Resp(404, text="<html>404</html>")
        return Resp(200, text=self.robots_body)


def cal(cid, name, bt=2):
    return {"Id": cid, "Name": name, "BookingTypeInfo": {"BookingType": bt}}


CATS = {"w1": [{"Name": "Drop-in", "Calendars": [cal("a", "Skating"), cal("b", "Swim Lessons", 3),
                                                  cal("c", "Hall Bookings", 4), cal("d", "Fitness")]},
               {"Name": "Childminding", "Calendars": [cal("e", "ARC Childminding")]}],
        "w2": [{"Name": "Arenas", "Calendars": [cal("a", "Skating"), cal("f", "Birthday Parties")]}]}
t2 = dict(TENANT, widgets=["w1", "w2"])
sess = Tenant(CATS)
rep = PM.read_tenant(t2, TODAY, HORIZON, client=client(sess), robots=robots_txt.Robots(sess))
check("only BookingType 2 calendars are walked", sorted(rep["calendars"]) == ["a", "d"], sorted(rep["calendars"]))
check("a calendar repeated by a second widget is read once", rep.get("duplicate_calendars") == 1,
      rep.get("duplicate_calendars"))
check("courses (3) and rental lists (4) are counted, not read",
      rep["refused_calendars"].get("BookingType 3 (not drop-in)") == ["Drop-in / Swim Lessons"]
      and rep["refused_calendars"].get("BookingType 4 (not drop-in)") == ["Drop-in / Hall Bookings"],
      rep["refused_calendars"])
check("childminding and birthday-party calendars are refused by the shared rule",
      sorted(rep["refused_calendars"].get("private-booking calendar", [])) ==
      ["Arenas / Birthday Parties", "Childminding / ARC Childminding"], rep["refused_calendars"])
check("the categories ask carries no token", "__RequestVerificationToken" not in sess.asked[0][2])
# Every per-tenant exclusion the research had to write by hand is caught by the
# shared rule now, so no config needs to repeat it.
research = [("Fitness and Wellness", "ARC External Trainers"), ("Childminding", "ARC Childminding"),
            ("Drop In (Pre-registration Recommended)", "*Childminding"),
            ("Drop In (Pre-registration Recommended)", "*Court Reservations"), ("Appointments", "Skate Vault"),
            ("General Interest", "Birthday Parties"), ("General Interest", "Facility Visits and Guided Tours"),
            ("Fitness and Wellness", "Fitness Orientations"), ("Child Minding", "Child Minding Reservations"),
            ("Youth", "Equipment Lending"), ("Youth", "Youth Orientations")]
missed = [n for c_, n in research if not PM.calendar_refusal(c_, n, {})]
check("all 11 calendars the research excluded by hand are refused by the shared rule", not missed, missed)
kept = [("Drop In (No Registration Required)", "Group Fitness"), ("Drop-In Programs", "Swimming"),
        ("General Interest", "Drop In 0-12"), ("Drop In", "Pottery"), ("Drop in Programs", "Winmar Toddler Turf"),
        ("Drop-In and Try-It Programs", "Flower City Senior Centre")]
wrong = [n for c_, n in kept if PM.calendar_refusal(c_, n, {})]
check("...and no ordinary drop-in calendar is", not wrong, wrong)
sess = Tenant(CATS, robots_body="User-agent: *\nDisallow: /23702/Reports/BookMe4V2/\n")
rep = PM.read_tenant(TENANT, TODAY, HORIZON, client=client(sess), robots=robots_txt.Robots(sess))
check("a tenant whose robots.txt starts refusing is skipped before any request",
      rep["stopped"] and "robots.txt" in rep["stopped"] and not sess.asked, rep["stopped"])

# ------------------------------------------------------------- 4. which rows
print()
print("a row has to be something you can turn up to")
REFUSED = {
    "Main Pool Closed - Tots pool, Hot Tub & Sauna ONLY": "closure notice",
    "Lane Swim - CLOSED for Programs": "closure notice",
    "NAC Bulkhead Move - MAIN POOL UNAVAILABLE": "closure notice",
    "CANCELLED Winmar Toddler Turf": "cancelled",
    "'Cancelled' Group Cycle": "cancelled",
    "Drop In Child Minding - Toddler": "childminding",
    "Bouncy Castle Birthday Party - Children": "private booking or rental",
    "User Group Rental - Main/Tots Pool Closed": "closure notice",
    "Weight Room External Training Session": "private booking or rental",
    "Skates/Helmet Appointment": "private booking or rental",
    "Rochester Fire Department Training (Attendance Only)": "private booking or rental",
    "Youth (11-17) Fitness Orientation": "orientation",
    "Equipment Lending (Winter Break)": "private booking or rental",
    "Parks Board Committee Meeting": "governance meeting",
    "Spring Break Day Camp": "registered course or camp",
    "Seattle Virtual Speed Dating on Zoom": "online only",
    "Society Pickleball (Society Members Only)": "members only",
    "Family Bowling Lane #1": "private booking or rental",
}
for title, reason in REFUSED.items():
    got = PM.row_refusal(title, "", TENANT)
    check(f"refused as {reason}: {title}", got == reason, got)
KEPT = ["Boot Camp", "Bootcamp Group Fitness Drop-In (14+ Years)", "Public Swim/Lessons - School Bookings may occur",
        "Halloween Skate Party", "Drop-In Lane Swim", "55+ Euchre Tournament", "Oakville Museum Guided Tour",
        "Free After You-th Program Drop-In (12 to 18 Years)", "Family Stick, Puck and Ring"]
for title in KEPT:
    check(f"kept: {title}", PM.row_refusal(title, "", TENANT) is None, PM.row_refusal(title, "", TENANT))
check("a tenant's own exclude_title_rx applies", PM.row_refusal("Snoezelen Nook", "", dict(
    TENANT, exclude_title_rx="snoezelen")) == "excluded by the tenant's config")

# --------------------------------------------------- 5. the price is a contract
print()
print("the fee line is a contract with ../mapsee 0227's offer tagger")
# The phrases 0227's re_free reads as free that this adapter could ever write.
TAGGED_FREE = re.compile(r"(?<![-\w/])free\s+(?:drop[- ]in|admission|entry|to\s+attend)\b|\bno\s+cost\b", re.I)
for src, want_free in (("No fee", True), ("$0.00", True), ("$0.00 - $0.00", True), ("Free", True),
                       ("$0.00 - $7.50", False), ("$13.55", False), ("$231.60 - $238.55", False),
                       (None, False), ("", False)):
    line, free = PM.price_line(src)
    check(f"{src!r} -> {line!r}", free == want_free and bool(TAGGED_FREE.search(line)) == want_free, line)
    if not want_free:
        check(f"...and the word free appears nowhere in it", "free" not in line.lower(), line)
check("a range is stated verbatim, the $0 pass-holder end included",
      PM.price_line("$0.00 - $7.50")[0] == "🎟 Drop-in fee: $0.00 - $7.50.")
ev = build([row(PriceRange="No fee")])[0][0]
check("the fee line OPENS the description, ahead of the prose the sync may trim",
      ev.description.startswith("🎟 Free drop-in: no fee."), ev.description[:60])
long = build([row(Details="x " * 900)])[0][0]
check("...and the provenance line closes it, short enough for _cap_prose to keep",
      long.description.endswith("(Xplor Recreation / PerfectMind).")
      and len(long.description.rsplit("\n\n", 1)[1]) <= 200)
check("ages: an upper bound of 99/100 means none", PM.age_text({"MinAge": 54, "MaxAge": 99}) == "Ages 54+")
check("ages: a children's range reads as one", PM.age_text({"MinAge": 1, "MaxAge": 5}) == "Ages 1 to 5")

# ----------------------------------------------------------- 6. reading a row
print()
print("titles, clocks and zones")
for raw, a, b, want in (
        ("Billiards Drop-In (55+ Years) | Bob Callahan Flower City Seniors Centre 11:00-2:15pm", "11:00", "14:15",
         "Billiards Drop-In (55+ Years)"),
        ("Mah Jong Drop-In (55+ Years) I Bob Callahan Flower City Senior Centre 10:00AM-12:00PM", "10:00", "12:00",
         "Mah Jong Drop-In (55+ Years)"),
        ("Lane Swim Drop-In ( 6+ Years) | Cassie Campbell 7:00-800am", "07:00", "08:00", "Lane Swim Drop-In ( 6+ Years)"),
        ("Adult Swim Drop-In (18+ Years) | Ellen Mitchell 3:45:-4:45pm", "15:45", "16:45", "Adult Swim Drop-In (18+ Years)"),
        ("Yoga Fusion Drop-In (14+ Years) | |Earnscliffe 8:30am-9:25am", "08:30", "09:25", "Yoga Fusion Drop-In (14+ Years)"),
        ("Delbrook Youth Centre (Grades 5+) Tuesdays 4:00-8:00pm", "16:00", "20:00", "Delbrook Youth Centre (Grades 5+)"),
        ("Length/Public Swim - Limited Pool Space 8am-10am", "06:00", "12:00",
         "Length/Public Swim - Limited Pool Space 8am-10am"),
        ("Zumba Level I 7:00-8:00pm", "19:00", "20:00", "Zumba Level I"),
        ("Open Gym | Basketball & Volleyball", "19:00", "20:00", "Open Gym | Basketball & Volleyball")):
    got = PM.clean_title(raw, a, b)
    check(f"title tail: {raw[-34:]!r} -> {want[-30:]!r}", got == want, got)
check("without the row's times nothing is cut (the refusal rules read the raw title)",
      PM.clean_title("Billiards Drop-In | Gore 11:00-2:15pm") == "Billiards Drop-In | Gore 11:00-2:15pm")
check("12:00 PM is noon and 12:15 AM is a quarter past midnight",
      (PM._clock("12:00 PM"), PM._clock("12:15 AM"), PM._clock("07:05 pm")) == ("12:00", "00:15", "19:05"))
check("garbage is not a time", PM._clock("TBA") is None and PM._clock("13:00 PM") is None)
e = build([row()])[0][0]
check("start_local is the wall clock", e.start_local == "2026-10-10T10:45:00", e.start_local)
check("start_utc is the real instant (PDT, UTC-7)", e.start_utc == "2026-10-10T17:45:00Z", e.start_utc)
check("the zone is the tenant's", e.timezone == "America/Vancouver")
regina = dict(TENANT, timezone="America/Regina")
e2 = build([row(OccurrenceDate="20261210")], tenant=regina)[0][0]
check("Regina keeps UTC-6 in December (no DST)", e2.start_utc == "2026-12-10T16:45:00Z", e2.start_utc)
late = build([row(FormattedStartTime="11:00 PM", FormattedEndTime="12:30 AM", DurationInMinutes=90)])[0][0]
check("an end past midnight lands on the next day when the duration agrees",
      late.end_local == "2026-10-11T00:30:00", late.end_local)
odd = build([row(FormattedStartTime="11:00 AM", FormattedEndTime="10:00 AM", DurationInMinutes=60)])[0][0]
check("...and is dropped when it does not", odd.end_local is None, odd.end_local)
allday = build([row(AllDayEvent=True)])[0][0]
check("an all-day row is a bare date, not midnight", allday.start_local == "2026-10-10" and allday.start_utc is None)
check("the occurrence's own landing page is the link",
      e.ticket_url.startswith("https://cityofkamloops.perfectmind.com/23702/Reports/BookMe4LandingPages/Class?")
      and "classId=b60c7b75" in e.ticket_url and "occurrenceDate=20261010" in e.ticket_url, e.ticket_url)
check("the venue is the building; the city runs it", (e.venue_name, e.promoter) == ("Memorial Arena", "City of Kamloops"))

# ----------------------------------------------------- 7. the coordinate
print()
print("the facility's point is the fact; a missing one is borrowed or refused")
check("own point, marked exact", (e.latitude, e.longitude, e.coords_exact) == (50.676288, -120.323154, True))
nopt = dict(ADDR, Latitude=None, Longitude=None)
aq = row(EventId="aq", Location="Surrey Sport and Leisure Complex - Aquatics",
         Address=dict(nopt, Street="16555 Fraser Highway #100", AddressTag="SSLC Aquatics"))
arena = row(EventId="ar", EventName="Public Skate", Location="Surrey Sport and Leisure Complex - Arenas",
            Address=dict(ADDR, Street="16555 Fraser Highway #100", Latitude=49.152785, Longitude=-122.764036))
evs, refused, notes, stats = build([aq, arena])
got = {x.venue_name: (x.latitude, x.longitude) for x in evs}
check("a facility with no point borrows its street-mate's (Surrey's Aquatics/Arenas)",
      got.get("Surrey Sport and Leisure Complex - Aquatics") == (49.152785, -122.764036) and stats["borrowed"] == 1,
      (got, stats))
same = row(EventId="s2", OccurrenceDate="20261011", Address=dict(nopt))
evs, refused, _n, stats = build([row(), same])
check("...or the point of another row at the same Location", len(evs) == 2 and stats["borrowed"] == 1, refused)
wb = row(Location="Woodbridge Pool & Memorial Arena", Address=dict(nopt, Street="5020 Highway #7    "))
vaughan = dict(TENANT, venues={"Woodbridge Pool & Memorial Arena": {"lat": 43.7811688, "lon": -79.590402}})
evs, refused, _n, _s = build([wb], tenant=vaughan)
check("...or the tenant's venues book (Vaughan's Woodbridge)",
      len(evs) == 1 and (evs[0].latitude, evs[0].longitude) == (43.7811688, -79.590402), refused)
check("...and the street keeps no trailing padding", evs and evs[0].address == "5020 Highway #7")
# Surrey's Clayton Community Centre: the widget's point is 1,096 m south of the
# building OpenStreetMap and its own street address agree on.
clay = row(Location="Clayton Community Centre", Address=dict(ADDR, Latitude=49.123192, Longitude=-122.703017))
surrey = dict(TENANT, venues={"Clayton Community Centre": {"lat": 49.132993, "lon": -122.704564}})
evs, _r, _n, stats = build([clay], tenant=surrey)
check("a checked venue in the book overrides the widget's own point, and is counted",
      (evs[0].latitude, evs[0].longitude) == (49.132993, -122.704564) and stats["placed"] == {"venues book": 1},
      (evs[0].latitude, stats))
evs, refused, _n, _s = build([wb])
check("nothing to borrow is refused and counted", not evs and refused == {"no coordinates": 1}, refused)
evs, refused, _n, _s = build([row(Address=dict(ADDR, Latitude=0, Longitude=0), Location="Nowhere")])
check("0,0 is not a place", refused == {"no coordinates": 1}, refused)

# ----------------------------------------------------- 8. identity
print()
print("identity: a re-run writes the same rows, two sessions are two rows")
a1 = build([row(), row(FormattedStartTime="02:00 PM", FormattedEndTime="03:00 PM")])[0]
a2 = build([row(), row(FormattedStartTime="02:00 PM", FormattedEndTime="03:00 PM")])[0]
check("the same rows read twice give the same fingerprints and source ids",
      [(x.fingerprint, x.source_id) for x in a1] == [(x.fingerprint, x.source_id) for x in a2])
check("two sessions of one title on one day are two fingerprints", len({x.fingerprint for x in a1}) == 2)
check("the source id is tenant:EventId:date:clock",
      a1[0].source_id == "cityofkamloops:b60c7b75-37fd-693a-3f78-c81d38d39048:20261010:1045", a1[0].source_id)
evs, refused, _n, _s = build([row(), row()])
check("the same occurrence read twice is one row, and counted",
      len(evs) == 1 and refused.get("same occurrence read twice") == 1, refused)
evs, refused, _n, _s = build([row(), row(EventId="other")])
check("two ids at one title, place and instant fold as an exact duplicate",
      len(evs) == 1 and refused.get("exact duplicate (same title, place and time)") == 1, refused)
with tempfile.TemporaryDirectory() as d:
    store = PM.EventStore(os.path.join(d, "s.json"))
    for x in build([row(), row(OccurrenceDate="20261011")])[0]:
        store.upsert(x)
    for x in build([row(), row(OccurrenceDate="20261011")])[0]:
        store.upsert(x)
    check("an EventStore re-run updates in place: no new rows, no re-keys",
          len(store.records) == 2 and store.stats["rekeyed"] == 0 and store.stats["updated"] == 2, store.stats)

# ------------------------------------------------------- 9. booking grids
print()
print("an hourly slot grid is one row per day")
grid = [row(EventId=f"g{h}", EventName="Yara Centre Track Drop in", OccurrenceDate="20261005",
            FormattedStartTime=f"{h % 12 or 12:02d}:00 {'AM' if h < 12 else 'PM'}",
            FormattedEndTime=f"{(h + 1) % 12 or 12:02d}:00 {'AM' if h + 1 < 12 else 'PM'}",
            PriceRange="$0.00 - $11.75") for h in range(6, 22)]
evs, refused, notes, stats = build(grid)
check("16 hourly slots -> one row", len(evs) == 1 and stats["grid_folded"] == 15, (len(evs), stats))
g = evs[0]
check("...opening with the first slot and closing with the last",
      (g.start_local, g.end_local) == ("2026-10-05T06:00:00", "2026-10-05T22:00:00"), (g.start_local, g.end_local))
check("...saying how many slots it stands for, the fee line intact",
      g.description.startswith("🕒 16 drop-in time slots on this day: 06:00–22:00.")
      and "🎟 Drop-in fee: $0.00 - $11.75." in g.description, g.description[:120])
gappy = [x for x in grid if not ("08:00 AM" <= x["FormattedStartTime"] < "12:00 PM"
                                and x["FormattedStartTime"].endswith("AM"))]
evs, *_ = build(gappy)
check("a turf booked out from 08:00 to 12:00 says so: two stretches, not one",
      len(evs) == 1 and evs[0].description.startswith("🕒 12 drop-in time slots on this day: 06:00–08:00, 12:00–22:00."),
      [e.description[:70] for e in evs])
check("...keyed on the DAY, so tomorrow's 06:30 start cannot orphan it",
      g.source_id == "cityofkamloops:grid:yara centre track drop in|50.67629,-120.32315|2026-10-05"
      and g.fingerprint == PM.make_fingerprint("Yara Centre Track Drop in", "2026-10-05", "Memorial Arena", "Kamloops"),
      g.source_id)
evs, refused, notes, stats = build(grid, now=datetime(2026, 10, 5, 13, 0))
check("decided before the past filter: at 13:00 the day row is still there",
      len(evs) == 1 and evs[0].start_local == "2026-10-05T06:00:00", [x.start_local for x in evs])
three = [row(EventId=f"l{h}", EventName="Drop-In Lane Swim", OccurrenceDate="20261005",
             FormattedStartTime=f"{h:02d}:00 AM", FormattedEndTime=f"{h:02d}:45 AM") for h in (6, 9, 11)]
evs, *_ = build(three)
check("a lane swim three times a day keeps its three real times", len(evs) == 3, len(evs))
# Brampton's Cassie Campbell lane swim, 2026-10-03: seven sessions in three
# stretches. Six or more in a day, and still a timetable.
lane = [row(EventId=f"c{i}", EventName="Lane Swim Drop-In ( 6+ Years)", OccurrenceDate="20261005",
            FormattedStartTime=a, FormattedEndTime=b)
        for i, (a, b) in enumerate([("06:00 AM", "07:00 AM"), ("07:00 AM", "08:00 AM"), ("08:00 AM", "09:00 AM"),
                                    ("11:35 AM", "12:35 PM"), ("12:35 PM", "01:35 PM"), ("01:35 PM", "02:35 PM"),
                                    ("09:30 PM", "10:30 PM")])]
evs, *_ = build(lane)
check("seven lane swims in three stretches are a timetable, not a grid", len(evs) == 7, len(evs))
evs, refused, *_ = build([row(OccurrenceDate="20261003", FormattedStartTime="12:00 AM",
                              FormattedEndTime="01:00 AM")], now=datetime(2026, 10, 3, 9, 0))
check("a session that has ended is past", refused == {"past": 1}, refused)
evs, refused, *_ = build([row(OccurrenceDate="20270101")])
check("a session on the horizon day is beyond it", refused == {"beyond horizon": 1}, refused)

# --------------------------------------------------------- 10. category
print()
print("the calendar names the activity")
for (cat_name, cal_name), want in {
        ("Drop-In Programs", "Swimming"): "fitness", ("Drop-In Programs", "Aquafit"): "fitness",
        ("Drop-In Programs", "Group Fitness: Cardio"): "fitness", ("Drop-In Activities", "Fitness Centre"): "fitness",
        ("Drop in Programs", "Track"): "fitness", ("Drop-In Activities", "Skating & Shinny Hockey"): "sports",
        ("Drop in Programs", "Gym"): "sports", ("Drop-In", "Drop-in basketball"): "sports",
        ("Sports", "ARC Gymnasium"): "sports", ("Arenas", "ARC Skating Rinks"): "sports",
        ("General Interest", "Drop In 0-12"): "kids", ("Drop-In Programs", "Sensory Room / Indoor Playground"): "kids",
        ("Drop in Programs", "Winmar Toddler Turf"): "kids", ("Drop In", "Pottery"): "arts",
        ("Drop-in", "Museum Programs"): "arts", ("Drop-In Programs", "Activities for Age 55+"): "community",
        ("Drop-In and Try-It Programs", "Youth Hub Drop-Ins"): "community",
        ("**Drop-In Schedules", "Parkgate Society Schedules"): "community"}.items():
    got = PM.category_for_calendar(cat_name, cal_name, TENANT)
    check(f"{cat_name} / {cal_name} -> {want}", got == want, got)
check("a tenant may pin a calendar's key", PM.category_for_calendar(
    "Drop-In", "Drop-in Activities", dict(TENANT, calendar_categories={"Drop-in Activities": "sports"})) == "sports")
check("every key this can produce is a real lens key", all(k in PM.VALID_CATEGORIES for k, _rx in PM._CALENDAR_CATEGORY))

# ------------------------------------------------------------ 11. the CLI
print()
print("the CLI: one tenant refusing never costs the others")


class Fleet(Tenant):
    def __init__(self):
        super().__init__(CATS)

    def request(self, method, url, data=None, headers=None, timeout=None):
        if "refuser" in url:
            return Resp(403, text="Forbidden")
        return super().request(method, url, data, headers, timeout)


real_session, PM.PACE_SECONDS = PM.requests.Session, 0
PM.requests.Session = Fleet
try:
    with tempfile.TemporaryDirectory() as d:
        conf = {"tenants": [dict(TENANT, host="refuser.perfectmind.com", name="Refuser"),
                            dict(TENANT, widgets=["w1"])]}
        open(os.path.join(d, "c.json"), "w", encoding="utf-8").write(json.dumps(conf))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = PM.main(["--config", os.path.join(d, "c.json"), "--store", os.path.join(d, "s.json"),
                          "--workers", "2", "--max-minutes", "1"])
        saved = json.load(open(os.path.join(d, "s.json"), encoding="utf-8"))["events"]
finally:
    PM.requests.Session = real_session
check("exit 0, like every sibling, whatever one tenant answered", rc == 0, rc)
check("the refusal is in the log, said once, as a refusal",
      buf.getvalue().count("Refuser: STOPPED - HTTP 403 - refused; not retried") == 1, buf.getvalue()[-400:])
check("...and the other tenant's rows reached the store",
      len(saved) == 1 and saved[0]["sources"][0]["source_id"].startswith("cityofkamloops:"),
      [r["sources"][0]["source_id"] for r in saved])

# ------------------------------------------------------ 12. the shipped config
print()
print("the shipped config")
cfg = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "perfectmind_sources.json"),
                     encoding="utf-8"))
ts = cfg["tenants"]
check("fifteen tenants", len(ts) == 15, len(ts))
hosts = [t["host"] for t in ts]
check("hosts are unique - they namespace source_id", len(hosts) == len(set(hosts)), hosts)
check("every host is a perfectmind.com tenant", all(h.endswith(".perfectmind.com") for h in hosts))
bad = [t["host"] for t in ts if not t.get("widgets") or not all(re.fullmatch(r"[0-9a-f-]{36}", w) for w in t["widgets"])]
check("every tenant names its widget ids", not bad, bad)
check("widget_path is Clients or Reports", all(t.get("widget_path") in ("Clients", "Reports") for t in ts))
tzbad = [t["host"] for t in ts if PM._tz(t.get("timezone")) is None or "/" not in t.get("timezone", "")]
check("every tenant has a real IANA zone (start_utc depends on it)", not tzbad, tzbad)
check("every tenant states country and region",
      all(t.get("country") in ("CA", "US") and t.get("region") for t in ts))
check("every default category is a lens key", all(t.get("category") in PM.VALID_CATEGORIES for t in ts))
check("every tenant records how it was found", all(t.get("_found") for t in ts))
vb = [n for t in ts for n, v in (t.get("venues") or {}).items() if not v.get("_coords")]
check("every hand-placed venue carries its provenance", not vb, vb)
declined = " ".join(cfg.get("_not_included", {}))
check("nothing configured is also declined", not any(h in declined for h in hosts))
check("the declines say why", all(isinstance(v, str) and len(v) > 40 for v in cfg["_not_included"].values()))

print()
print(f"{'FAILURES: ' + ', '.join(fails) if fails else 'all checks passed'}")
sys.exit(1 if fails else 0)
