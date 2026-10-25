"""Galaxy Digital: only a dated shift with a meeting place and a sign-up page
reaches the map, at the place the organiser named, and nobody's phone number
comes with it.

WHY THIS EXISTS. Each case is a way the 2026-10-25 build could come out
well-formed and wrong:

  * STANDING ROLES. "Flexible", "Is Ongoing", "Runs Until" and a date range
    name no shift (0 of the 7 "Flexible" and 0 of the 5 ranged pages read had
    one): they are refused from the listing, unread, and so is a one-off
    outside the window. A test asserts their pages are never requested.
  * THE SHIFT TABLE. A full shift ("0 of 5") and a midnight placeholder
    ("12:00am to 3:00am", 6 of the 723 rows read) are refused and `seen`; two
    rows with one begins/ends are one occasion; a weekday that disagrees with
    its date is unreadable, never re-dated; an end on the next day is kept.
  * WHO MAY JOIN. A background check (12 of Portland's 48 pages) or a
    programme the config skips (SUN Community Schools: the 13th club, which
    asks no check) is refused and `seen`. "Is Family Friendly" with "14 and
    older" (2 of Portland's 30) is not the kids door's.
  * THE PLACE. A street address goes to the sync's Census batch with its city
    and state; a corner or a trail is written only with the config's measured
    pin, exactly as keyed; no Location, a virtual one, another state, a ZIP
    outside the portal or a pin far from it is refused.
  * PEOPLE. The Site Supervisor block is never read; a sentence with an
    e-mail address, a phone number or a named contact goes (72 sentences on
    59 of the 80 pages read), with a "Questions?" lead-in; a style sheet pasted into a
    description is not text, and a source line break inside a sentence does
    not leave "please call" behind.
  * IDENTITY. "<title> HH:MM" | day | venue, the recipe EventStore's notice
    rule can prove, so "CANCELLED: <title>" tombstones the shift.
  * COMPLETE means every listing page and every page that had to be read was
    read: a 403, a challenge, robots.txt, a 5xx, a redirect off the portal, a
    deadline or a listing cap each make the read NOT complete.
  * main() on the REAL config, because a config that parses is not a config
    that loads (docs/agents/adapters-and-sources.md).

No network: the fixtures are the portal's real markup (Portland's table
listing, Mecklenburg's card grid, a need page's blocks), trimmed, with
invented needs, places and people.
"""
import os
import sys

os.environ["MAPSEE_TODAY"] = "20261025"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import json
import shutil
import tempfile
from datetime import date

import mapsee_ingest_galaxydigital as G
from mapsee_ingest import EventStore, notice_fingerprint

fails = []


def check(label, cond, detail=""):
    print(f"{'ok  ' if cond else 'FAIL'} {label}{'' if cond else '   ' + str(detail)}")
    if not cond:
        fails.append(label)


HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixtures", "galaxydigital")
CFG = json.load(open(os.path.join(HERE, "galaxydigital_sources.json"), encoding="utf-8"))
PORTAL = next(p for p in CFG["portals"] if p["slug"] == "portland")
BASE = PORTAL["base"]
TODAY = date(2026, 10, 25)


def fx(name):
    return open(os.path.join(FIX, name), encoding="utf-8").read()


def need_url(nid):
    return f"{BASE}/need/detail/?need_id={nid}"


ROBOTS = "User-agent: SemrushBot\nDisallow: /\n\nUser-agent: bingbot\nCrawl-delay: 10\n\nUser-agent: *\nDisallow:\n"
NEEDS = ["900001", "900002", "900003", "900006", "900007", "900008", "900009"]


# --------------------------------------------------------------------------- #
# A fake web

class Resp:
    def __init__(self, code, text="", headers=None):
        self.status_code = code
        self.text = text
        self.content = text.encode("utf-8")
        self.headers = headers or {}


class Web:
    """Routes URL -> Resp. Every request costs 0.2 s of a fake clock that
    sleep() also advances, and is logged with the time it was sent."""

    def __init__(self, overrides=None):
        self.routes = default_routes()
        self.routes.update(overrides or {})
        self.log, self.times = [], []
        self.now = 0.0
        self.headers = {}

    def sleep(self, s):
        self.now += max(0.0, s)

    def get(self, url, timeout=None, allow_redirects=False):
        self.log.append(url)
        self.times.append(self.now)
        self.now += 0.2
        r = self.routes.get(url)
        if r is None:
            return Resp(404, "not found")
        if isinstance(r, Exception):
            raise r
        return r


def default_routes():
    r = {BASE + "/robots.txt": Resp(200, ROBOTS),
         BASE + "/need/": Resp(200, fx("listing-table-0.html")),
         BASE + "/need/index/12": Resp(200, fx("listing-table-12.html"))}
    r.update({need_url(n): Resp(200, fx(f"need-{n}.html")) for n in NEEDS})
    return r


def run(web, portal=None, deadline=None, robots="real", pause=1.1):
    from robots_txt import Robots
    rows, seen = [], []
    rob = Robots(web) if robots == "real" else robots
    client = G.Client(web, pause, deadline, robots=rob, clock=lambda: web.now, sleep=web.sleep)
    rep = G.read_portal(portal or PORTAL, client, TODAY, rows.append, seen.append)
    return rep, rows, seen


def by_id(rows):
    return {r.source_id: r for r in rows}


# --------------------------------------------------------------------------- #
# Dates and clocks

for raw, want in [("Happens On Oct 30, 2026", ("once", date(2026, 10, 30))),
                  ("Multiple Shifts Available", ("shifts", None)),
                  ("Is Ongoing", ("standing", None)), ("Flexible", ("standing", None)),
                  ("Runs Until Feb 28, 2027", ("standing", None)),
                  ("Nov 9, 2026 through Nov 10, 2026", ("standing", None)),
                  ("Happens On Feb 30, 2027", ("once", None)), ("", ("unknown", None))]:
    check(f"listing date {raw!r}", G.parse_listing_date(raw) == want, G.parse_listing_date(raw))

for raw, want in [("Tue Oct 27, 2026 @ 9:00am to 11:00am", (date(2026, 10, 27), (9, 0), date(2026, 10, 27), (11, 0))),
                  ("Sun Oct 25, 2026 @ 9:30am to 12:00pm", (date(2026, 10, 25), (9, 30), date(2026, 10, 25), (12, 0))),
                  ("Fri Dec 4, 2026 @ 9:00pm to Sat Dec 5, 2026 @ 1:00am",
                   (date(2026, 12, 4), (21, 0), date(2026, 12, 5), (1, 0))),
                  ("Thu Nov 18, 2026 @ 9:00am to 11:00am", None),          # Nov 18 2026 is a Wednesday
                  ("Tue Oct 27, 2026 @ 11:00am to 9:00am", None),
                  ("Fri Dec 4, 2026 @ 9:00pm to Sun Dec 6, 2026 @ 1:00am", None),   # more than a day
                  ("Tue Oct 27, 2026", None), (None, None)]:
    check(f"begins {raw!r}", G.parse_begins(raw) == want, G.parse_begins(raw))

for raw, want in [("10 a.m. to 1 p.m.", ((10, 0), (13, 0))), ("4- 7pm", ((16, 0), (19, 0))),
                  ("9:30am to 12pm", ((9, 30), (12, 0))), ("11:00am-12:00", ((11, 0), (12, 0))),
                  ("8:00am-10:00am", ((8, 0), (10, 0))), ("5am-11:30am", ((5, 0), (11, 30))),
                  ("4:30 to 8:15pm", ((16, 30), (20, 15))), ("noon to 3pm", ((12, 0), (15, 0))),
                  ("10AM-1:30PM", ((10, 0), (13, 30))),
                  ("9:00am - 12:15pm, 11:45am - 3:00pm or 9am - 3pm", ((9, 0), (15, 0))),
                  ("9-12", None), ("all day", None), ("3pm to 1pm", None)]:
    check(f"clock {raw!r}", G.parse_clock_text(raw) == want, G.parse_clock_text(raw))

check("wall clock in the portal's zone: 9:00 PDT is 16:00Z, 9:00 PST is 17:00Z",
      G.localize(date(2026, 10, 27), (9, 0), "America/Los_Angeles") == ("2026-10-27T09:00:00-07:00", "2026-10-27T16:00:00Z")
      and G.localize(date(2026, 11, 3), (9, 0), "America/Los_Angeles")[1] == "2026-11-03T17:00:00Z")
check("no clock is a bare date, never midnight", G.localize(date(2026, 10, 29), None, "America/New_York")
      == ("2026-10-29", None))

# --------------------------------------------------------------------------- #
# Listings: Portland's table and Mecklenburg's card grid

L0 = G.parse_listing(fx("listing-table-0.html"), BASE)
check("table listing: 5 needs with id, title and date text, and the next page",
      [n["need_id"] for n in L0["needs"]] == ["900001", "900002", "900003", "900004", "900005"]
      and L0["needs"][1] == {"need_id": "900002", "title": "Bluff Planting Day", "date_text": "Happens On Nov 8, 2026"}
      and L0["next"] == BASE + "/need/index/12", L0)
L1 = G.parse_listing(fx("listing-table-12.html"), BASE)
check("table listing: the last page has no next", len(L1["needs"]) == 4 and L1["next"] is None, L1)
LG = G.parse_listing(fx("listing-grid.html"), "https://volunteer.parkandrec.mecklenburgcountync.gov")
check("card grid: 3 needs, the date from the card, no next",
      [(n["need_id"], G.parse_listing_date(n["date_text"])[0]) for n in LG["needs"]]
      == [("910001", "once"), ("910002", "standing"), ("910003", "shifts")] and LG["next"] is None
      and LG["needs"][0]["title"].startswith("GARDEN HELPERS"), LG)

# --------------------------------------------------------------------------- #
# A need page

D1 = G.parse_detail(fx("need-900001.html"))
check("detail: title, programme, location lines, requirements",
      (D1["title"], D1["program"], D1["agency"], D1["location"], D1["min_age"], D1["family_friendly"], D1["outdoors"])
      == ("Willow Creek Nature Patch Stewardship Party", "Nature Patches", None,
          ["Willow Creek Park", "1234 NE Example St", "Portland, OR", "97212"], 6, True, True), D1)
check("detail: 9 shift rows read by their column headings", len(D1["shifts"]) == 9
      and D1["shifts"][1] == {"begins": "Tue Oct 27, 2026 @ 9:00am to 11:00am", "open": 3, "total": 5}, D1["shifts"][:2])
everything = json.dumps(D1)
check("people: the Site Supervisor (name, e-mail, phone) is never read",
      not any(x in everything for x in ("Pat Placeholder", "pat.placeholder", "555-0199")), everything[:300])
check("people: a requirement naming a person is not copied anywhere but the requirements",
      "Dana Fictional" not in D1["text"])
text = D1["text"]
check("people: the e-mail, the phone and the named contact are dropped (3 sentences)",
      D1["dropped"] == 3 and not any(x in text for x in ("Jane Example", "jane.example", "503-555", "Robin Sample",
                                                         "parks@example.org")), (D1["dropped"], text))
check("people: the 'Questions?' lead-in goes with the contact under it", "Questions?" not in text, text)
check("people: a source line break inside a sentence leaves no 'please call' behind", "please call" not in text, text)
check("people: a date (2026-11-07) and a clock (9:00am - 11:00am) are not phone numbers",
      "2026-11-07" in text and "9:00am - 11:00am" in text, text)
check("people: 'please contact us' is not a person", "please contact us." in text, text)
check("text: a pasted style sheet is not text", "{" not in text and "text-decoration" not in text, text)
check("text: list items become lines", "- Bring a water bottle.\n- Gloves and tools are provided." in text, text)

for s, want in [("Contact Kerri Example at kerri@example.gov or 980-555-7878 (Quest main line)", True),
                ("Text Carla if you can't find the crew, 503-555-7464.", True),
                ("If you are interested, please reach out to Kyra Example by phone or email.", True),
                ("For questions contact Jane Doe.", True),
                ("Contact: the parks office", True),
                ("We meet at 9:00am - 12:00pm on 2026-11-07.", False),
                ("Please contact PDX 311 .", False),
                ("please contact the event coordinator listed in the description before arriving.", False),
                ("Bring your own gloves; if you need a pair, please contact the Developed Park Coordinator.", False)]:
    check(f"gives_contact {s[:48]!r}", G.gives_contact(s) is want)

for lines, want in [(["Irving Park", "3498 NE 7th Ave", "Portland, OR", "97212"],
                     ("Irving Park", "3498 NE 7th Ave", "Portland", "OR", "97212")),
                    (["2020 Cornwall Ave, Bellingham, WA 98225", "Bellingham, WA", "98225"],
                     (None, "2020 Cornwall Ave", "Bellingham", "WA", "98225")),
                    (["NE Sacramento St and 66th", "Portland, OR", "97213"], ("NE Sacramento St and 66th", None, "Portland", "OR", "97213")),
                    (["5737 SW Miles & SW #58th", "Portland, OR", "97219"], (None, None, "Portland", "OR", "97219")),
                    (["6001-6399 SE Lincoln St", "Portland, OR", "97215"], ("6001-6399 SE Lincoln St", None, "Portland", "OR", "97215")),
                    (["SW Clay and SW Park", "97205"], ("SW Clay and SW Park", None, None, None, "97205"))]:
    got = G.parse_location(lines)
    check(f"location {lines[0]!r}", (got["name"], got["street"], got["city"], got["region"], got["postal"]) == want,
          (got["name"], got["street"], got["city"], got["region"], got["postal"]))

# --------------------------------------------------------------------------- #
# Placement

where, why = G.place(D1, PORTAL)
check("place: a street address is left to the Census, with its city and state, no coordinates made up",
      why is None and not where["pinned"] and "lat" not in where and where["venue"] == "Willow Creek Park"
      and where["loc"]["street"] == "1234 NE Example St", (where, why))
D2 = G.parse_detail(fx("need-900002.html"))
where2, why2 = G.place(D2, PORTAL)
check("place: a corner is written with the config's measured pin, keyed on its exact Location text",
      why2 is None and where2["pinned"] and (where2["lat"], where2["lon"]) == (45.541347, -122.595442)
      and where2["venue"] == "Rose City Bluff", (where2, why2))
D7 = G.parse_detail(fx("need-900007.html"))
check("place: a trail with no pin in the config is refused", (G.place(D7, PORTAL)[1] or "").startswith(
    "meeting place is not a street address"), G.place(D7, PORTAL))
check("place: no Location section is refused", G.place(G.parse_detail(fx("need-900009.html")), PORTAL)[1]
      == "no meeting place on the page")
check("place: a virtual Location is refused",
      G.place(dict(D1, location=["Virtual", "Portland, OR", "97212"]), PORTAL)[1] == "virtual")
check("place: another state is refused",
      (G.place(dict(D1, location=["1 Main St", "Vancouver, WA", "98660"]), PORTAL)[1] or "").startswith("meeting place outside the portal's state"))
check("place: a ZIP outside the portal's zip3 is refused",
      (G.place(dict(D1, location=["1 Main St", "Portland, OR", "97850"]), PORTAL)[1] or "").startswith("meeting place outside the portal's area"))
far = dict(PORTAL, location_overrides=[{"location": "NE Sacramento St and 66th, Portland, OR, 97213",
                                        "lat": 44.0, "lon": -121.3}])
check("place: an override pin far from the portal's centre is refused",
      G.place(D2, far)[1] == "override pin far from the portal's area", G.place(D2, far))
check("place: a street address without city and state is refused",
      G.place(dict(D1, location=["1234 NE Example St", "97212"]), PORTAL)[1] == "street address without a city and state")

# --------------------------------------------------------------------------- #
# The read

web = Web()
rep, rows, seen = run(web)
ids = sorted(by_id(rows))
check("read: 5 rows - 3 open shifts of the weekly need, the pinned one-off, the 14+ one-off",
      ids == ["portland:900001:2026-10-27:0900", "portland:900001:2026-11-10:0900", "portland:900001:2026-12-04:2100",
              "portland:900002:2026-11-08:0930", "portland:900008:2026-10-29:1630"], ids)
check("read: complete, 2 listing pages and 7 need pages, robots.txt first",
      rep["complete"] and rep["pages"] == {"listing": 2, "needs": 7} and web.log[0] == BASE + "/robots.txt"
      and rep["requests"] == 10, (rep["complete"], rep["pages"], web.log[:2], rep["requests"]))
check("read: the standing role and the one-off outside the window are never requested",
      need_url("900004") not in web.log and need_url("900005") not in web.log, web.log)
gaps = [b - a for a, b in zip(web.times, web.times[1:])]
check("read: no two requests to the portal less than `pause` (1.1 s) apart, robots.txt included",
      min(gaps) >= 1.1 - 1e-9, gaps)
r = rep["refused"]
check("read: every refusal is counted by its reason",
      r.get("need: a standing role (ongoing, flexible or a date range), not read") == 1
      and r.get("need: requires a background check (a vetted role)") == 1
      and r.get("need: a programme the config skips (skip_programs)") == 1
      and r.get("need: meeting place is not a street address (no pin in the config)") == 1
      and r.get("need: no meeting place on the page") == 1
      and r.get("shift: full") == 1 and r.get("shift: starts at midnight (a placeholder clock)") == 1
      and r.get("shift: unreadable shift time") == 1, r)
check("read: the past shift and the one beyond 90 days are outside the window, not rows",
      rep["notes"].get("shift: outside the window") == 2
      and rep["notes"].get("need: one-off outside the window, not read") == 1, rep["notes"])
check("read: 8 refused shifts are seen (full, midnight, background check x2, SUN x2, trail, no place)",
      len(set(seen)) == 8 and all(seen), seen)
R = by_id(rows)
full_fp = G.fingerprint_for(D1["title"], date(2026, 11, 3), (9, 0), "Willow Creek Park")
check("read: the full shift's seen fingerprint is the one its live row had", full_fp in seen)
check("read: two rows with one begins/ends are one occasion, open if either is",
      sum(1 for k in R if ":2026-11-10:" in k) == 1)

ev = R["portland:900001:2026-10-27:0900"]
check("row: times in the portal's zone, UTC from them",
      (ev.start_local, ev.start_utc, ev.end_local, ev.end_utc, ev.timezone)
      == ("2026-10-27T09:00:00-07:00", "2026-10-27T16:00:00Z", "2026-10-27T11:00:00-07:00", "2026-10-27T18:00:00Z",
          "America/Los_Angeles"), (ev.start_local, ev.start_utc, ev.end_local, ev.end_utc))
check("row: a Census row carries the street, city, state and ZIP and no coordinates",
      (ev.venue_name, ev.address, ev.city, ev.region, ev.postal_code, ev.country, ev.latitude, ev.coords_exact)
      == ("Willow Creek Park", "1234 NE Example St", "Portland", "OR", "97212", "US", None, False))
check("row: volunteer, with outdoors and kids (family friendly, 6 and older)",
      ev.category == "volunteer" and ev.categories == ["outdoors", "kids"], (ev.category, ev.categories))
check("row: the link is the need's page, where Respond is", ev.ticket_url == need_url("900001"))
check("row: the department runs it, its programme in brackets",
      ev.promoter == "Portland Parks & Recreation"
      and ev.description.startswith("Volunteer shift with Portland Parks & Recreation (Nature Patches).\n"
                                    "Meeting place: Willow Creek Park, 1234 NE Example St.\nAges 6 and older.\n"
                                    "Family friendly.\nSign up with Respond on its page at volunteer.portland.gov"),
      ev.description[:300])
check("row: within the sync's 800-character description cap", all(len(x.description) <= 800 for x in rows))
check("row: no person in any row", not any(s in json.dumps([x.__dict__ for x in rows], default=str)
                                           for s in ("Pat Placeholder", "pat.placeholder", "Jane Example", "503-555",
                                                     "Robin Sample", "Dana Fictional")))
night = R["portland:900001:2026-12-04:2100"]
check("row: an end on the next day is kept", (night.end_local, night.end_utc)
      == ("2026-12-05T01:00:00-08:00", "2026-12-05T09:00:00Z"), (night.end_local, night.end_utc))
pin = R["portland:900002:2026-11-08:0930"]
check("row: a pinned row is exact, has no street for the Census, and keeps the meeting text",
      (pin.latitude, pin.longitude, pin.coords_exact, pin.address, pin.venue_name, pin.city, pin.region)
      == (45.541347, -122.595442, True, None, "Rose City Bluff", "Portland", "OR")
      and "Meeting place: NE Sacramento St and 66th." in pin.description, pin.__dict__)
check("row: a one-off's clock ('9:30am to 12pm')", (pin.start_local, pin.end_local)
      == ("2026-11-08T09:30:00-08:00", "2026-11-08T12:00:00-08:00"))
teen = R["portland:900008:2026-10-29:1630"]
check("row: family friendly but 14 and older is not the kids door's, and the row does not say so",
      "kids" not in teen.categories and "Ages 14 and older." in teen.description
      and "Family friendly." not in teen.description, (teen.categories, teen.description[:200]))
import mapsee_supabase_sync as Y
check("row: the sync's own categories agree (volunteer; outdoors and kids / none for the 14+ crew)",
      Y.derive_categories(dict(ev.__dict__)) == ("volunteer", ["outdoors", "kids"])
      and Y.derive_categories(dict(teen.__dict__)) == ("volunteer", None),
      (Y.derive_categories(dict(ev.__dict__)), Y.derive_categories(dict(teen.__dict__))))

# Identity: the notice rule proves the recipe.
tmp = tempfile.mkdtemp(prefix="gd-store-")
try:
    store = EventStore(os.path.join(tmp, "s.json"))
    store.upsert(ev)
    shift = next(s for s in G.shifts_of(D1)[0] if s["day"] == date(2026, 10, 27))
    dead = G.build_event(PORTAL, "900001", need_url("900001"), dict(D1, title="CANCELLED: " + D1["title"]), where, shift)
    check("identity: notice_fingerprint proves '<title> HH:MM' | day | venue",
          notice_fingerprint(dead, D1["title"]) == ev.fingerprint)
    check("identity: 'CANCELLED: <title>' tombstones the shift it names",
          store.upsert(dead) == "notice" and ev.fingerprint in store.tombstones)
finally:
    shutil.rmtree(tmp, ignore_errors=True)
check("identity: a date-only one-off uses the plain recipe",
      G.fingerprint_for("X", date(2026, 10, 29), None, "Y") != G.fingerprint_for("X", date(2026, 10, 29), (0, 0), "Y"))

# --------------------------------------------------------------------------- #
# Refusals and failures

web = Web(overrides={need_url("900003"): Resp(403, "Forbidden")})
rep, rows, _ = run(web)
check("403: a refusal ends the portal's read; nothing after it is requested; not complete",
      rep["stopped"].startswith("REFUSED - HTTP 403") and web.log[-1] == need_url("900003")
      and rep["complete"] is False, (rep["stopped"], web.log[-2:]))
check("403: rows decided before it are written", len(rows) == 4, len(rows))

web = Web(overrides={BASE + "/need/": Resp(200, "<html><title>Just a moment...</title>checking your browser</html>")})
rep, rows, _ = run(web)
check("challenge: a bot challenge is a refusal, nothing written, not complete",
      rep["stopped"].startswith("REFUSED - bot challenge") and rows == [] and not rep["complete"], rep["stopped"])


class NoRobots:
    _files = {}

    def check(self, url):
        return {"allowed": False, "status": "ok", "rule": "Disallow: /need/", "crawl_delay": None}


web = Web()
rep, rows, _ = run(web, robots=NoRobots())
check("robots: a disallowed listing is never requested, nothing written, not complete",
      web.log == [] and rows == [] and rep["complete"] is False and rep["stopped"].startswith("REFUSED - robots"),
      (web.log, rep["stopped"]))

web = Web(overrides={need_url("900001"): Resp(502, "Bad gateway")})
rep, rows, _ = run(web)
check("5xx: one page lost costs its rows and the completeness, not the others",
      len(rows) == 2 and rep["complete"] is False and rep["notes"].get("need page not read") == 1, (len(rows), rep["notes"]))

web = Web(overrides={need_url("900002"): Resp(302, "", {"Location": "https://elsewhere.example.com/x"})})
rep, rows, _ = run(web)
check("redirect: one off the portal is not followed and the read is not complete",
      "https://elsewhere.example.com/x" not in web.log and rep["complete"] is False and len(rows) == 4,
      (web.log[-3:], rep["complete"]))

web = Web(overrides={need_url("900001"): OSError("timed out")})
rep, rows, _ = run(web)
check("network: a timeout costs one page", len(rows) == 2 and rep["notes"].get("network error, page skipped") == 1
      and not rep["complete"], rep["notes"])

web = Web()
rep, rows, _ = run(web, deadline=5.0)
check("deadline: no request starts after it; not complete; pages left are counted",
      rep["stopped"] == "run deadline reached" and rep["complete"] is False
      and max(web.times) < 5.0 and rep["notes"].get("need pages left for the next run", 0) > 0, (rep["stopped"], web.times))

web = Web()
rep, rows, _ = run(web, portal=dict(PORTAL, max_listing_pages=1))
check("listing cap: the second page unread makes the read not complete",
      BASE + "/need/index/12" not in web.log and rep["complete"] is False
      and rep["notes"].get("listing cut at max_listing_pages (1)") == 1, rep["notes"])

web = Web(overrides={BASE + "/need/": Resp(200, "<html><body>a new theme</body></html>")})
rep, rows, _ = run(web)
check("markup: a listing that matches no need is not complete and writes nothing",
      rep["complete"] is False and rows == [] and rep["listed"] == 0)

# --------------------------------------------------------------------------- #
# The config

slugs = [p["slug"] for p in CFG["portals"]]
check("config: 3 portals (Portland, Olympia, Mecklenburg), unique slugs; WWU parked in _not_included",
      slugs == ["portland", "olympia", "mecklenburg"]
      and "https://volunteer.wwu.edu" in CFG["_not_included"], slugs)
ok = True
for p in CFG["portals"]:
    ok = ok and p["base"].startswith("https://") and p["category"] == "volunteer" and G._zone(p["timezone"]) is not None \
        and p["states"] and p["zip3"] and len(p["center"]) == 2 and p.get("promoter") \
        and p["_found"].startswith("manual:2026-10-25") and "_measured" in p and float(p["pause"]) >= 1.0
check("config: every portal has https, volunteer, a real zone, state, zip3, centre, promoter, _found, _measured, pause >= 1 s", ok)
keys = [G.location_key([o["location"]]) for o in PORTAL["location_overrides"]]
check("config: override keys are unique", len(keys) == len(set(keys)), keys)
bad = [o["location"] for o in PORTAL["location_overrides"]
       if G._km((o["lat"], o["lon"]), tuple(PORTAL["center"])) > PORTAL["max_km"] or "_measured" not in o
       or G.parse_location([x.strip() for x in o["location"].split(",")])["street"] is not None]
check("config: every pin is measured, inside max_km, and for a place the Census cannot answer (no street)",
      bad == [], bad)

# --------------------------------------------------------------------------- #
# main() on the real config

tmp = tempfile.mkdtemp(prefix="gd-main-")
try:
    store_p = os.path.join(tmp, "store.json")
    webs = []

    def factory():
        w = Web()
        webs.append(w)
        return w

    code = G.main(["--config", os.path.join(HERE, "galaxydigital_sources.json"), "--store", store_p,
                   "--only", "portland", "--max-minutes", "0"], session_factory=factory, sleep=lambda s: None)
    data = json.load(open(store_p, encoding="utf-8"))
    check("main: exits 0 and writes the 5 rows", code == 0 and len(data["events"]) == 5,
          (code, len(data.get("events", []))))
    check("main: --only reads one portal, robots.txt first, with our User-Agent",
          len(webs) == 1 and webs[0].log[0] == BASE + "/robots.txt" and webs[0].headers.get("User-Agent") == G.USER_AGENT,
          (len(webs), webs[0].log[:1], webs[0].headers))
    check("main: a complete read is marked per portal (id_prefix 'portland:')",
          (data.get("complete_reads") or {}).get("galaxydigital|portland:", {}).get("id_prefix") == "portland:",
          data.get("complete_reads"))
    check("main: the 8 refused shifts are 'seen'", len(data.get("seen") or []) == 8, data.get("seen"))
    dry = os.path.join(tmp, "dry")
    os.mkdir(dry)
    G.main(["--config", os.path.join(HERE, "galaxydigital_sources.json"), "--store", os.path.join(dry, "s.json"),
            "--only", "portland", "--dry-run"], session_factory=Web, sleep=lambda s: None)
    check("main: --dry-run writes no store", os.listdir(dry) == [], os.listdir(dry))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
print(f"{'FAILED' if fails else 'passed'}: {len(fails)} failure(s)")
sys.exit(1 if fails else 0)
