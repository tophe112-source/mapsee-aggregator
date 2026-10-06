"""Glen Echo Park: the year is ours, the room is the book's, the fee is the
park's, and a studio's opening hours are not an event.

WHY THIS EXISTS. Every case below is a way the 2026-10-04 build came out
well-formed and wrong, or would if the regex were loosened:

  * THE YEAR. Neither page states one ("October 4", "Oct 04, 2:45 pm", and a
    datetime="00Z"). It comes from the /events-calendar/YYYYMM we asked for,
    and a card naming another month, or a detail page disagreeing with its
    card, is refused rather than re-dated.
  * "NO REGISTRATION REQUIRED." The NPS civil-rights walking tour says it,
    and the first cut refused both dates as registration-only. And "$12/adult
    advance online only" is the TICKET, not an online-only dance.
  * THE CLOCK. The park's written time line wins over the <time> field: the
    Junior Ranger swearing-in (7685, 7686) says "11:00am" and its <time> says
    10:00, copied from the tour before it. The first build took the <time> and
    put both an hour early. A Google-Docs <p dir="ltr"> line and a
    "Lesson | Social Dance" schedule are lines too; a schedule with a gap is
    not one session; a card or <time> pair naming two days is refused.
  * THE ROOM. "Park" as a prefix swallowed "Park View Gallery"; the Back Room
    filed under "Spanish Ballroom" merged two Ballroom Time postings on
    2026-12-27 (same lesson, same social) into one row.
  * THE FEE. "$15/adult, $5/teen, FREE for ages 12 and under" must never read
    as a free event to ../mapsee's 0227 tagger; "Admission: FREE" must.
  * OPEN HOURS. 240 of 430 cards in Oct-Dec 2026 are nine studios' and
    galleries' daily hours; they are refused from the listing, unfetched.
  * ONE TIMEOUT. The first build let a ReadTimeout on one detail page end the
    whole run; it now costs that page, and five failures in a row end it.

No network: every page is a literal fragment of the real markup.
"""
import os
import sys

os.environ["MAPSEE_TODAY"] = "20261004"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import json
from datetime import date

import mapsee_ingest_glenecho as G
from mapsee_ingest import make_fingerprint

fails = []


def check(label, cond, detail=""):
    print(f"{'ok  ' if cond else 'FAIL'} {label}{'' if cond else '   ' + str(detail)}")
    if not cond:
        fails.append(label)


HERE = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(HERE, "glenecho_sources.json"), encoding="utf-8"))
SITE = CFG["sites"][0]


def card_html(nid, title, when, line, until=None, ptag="<p>"):
    tl = f"{ptag}<strong>{line}</strong></p>" if line is not None else ""
    if until:
        when = f'{when}</time> - <time datetime="00Z">{until}'

    a = (f'<a class="use-ajax" href="/events-calendar/event-detail/{nid}" data-dialog-type="modal" '
         f'data-dialog-options=\'{{"title":"{title}"}}\'>')
    return (f'<div class="calendar-box flex-container">\n<div class="flex-item flex-calendar1"></div>\n'
            f'<div class="flex-item flex-calendar2">\n<div><strong>{a}{title}</a><br />'
            f'<time datetime="00Z">{when}</time>\n</strong></div>\n{tl}\n'
            f'<p><p>Blurb…</p> </p>\n<p>{a}<img src="/themes/basic/images/arrow-button.jpg">'
            f'<span id="field-button-text">&nbsp;Learn More</span></a></p>\n</div>\n</div>')


def detail_html(when, line, fields, desc="<p>A dance.</p>", links="", until=None):
    rows = "\n".join(f"<div><strong>{k}</strong>: {v}</div>" for k, v in fields.items())
    if until:
        when = f'{when}</time> - <time datetime="00Z">{until}'
    return ('<html><body><a href="https://www.simpletix.com/e/SITE-HEADER">Tickets</a>'
            '<div class="views-element-container">\n'
            f'<div><p><strong><time datetime="00Z">{when}</time>\n</strong></p>\n'
            f'<p><strong>{line}</strong></p>\n'
            '<p>  <img src="/sites/default/files/styles/medium/public/2026-08/x.png?itok=a" /></p>\n'
            f'<p><strong>Description</strong></p>\n{desc}\n{links}\n{rows}\n</div>\n</div>\n'
            '<!-- /#content -->'
            '<a href="https://www.facebook.com/FOOTER">Learn More</a></body></html>')


WALTZ_LINKS = ('<p><strong><a href="http://www.waltztimedances.org/" target="_blank">Learn More &gt;&gt;</a>'
               '</strong> | <strong><a href="https://www.simpletix.com/e/waltz-295045" target="_blank">'
               'Purchase Tickets &gt;&gt;</a></strong> | <strong><a href="https://glenechopark.org/parkmap">'
               'Park Map &gt;&gt;</a></strong></p>')

# --------------------------------------------------------------------------- #
print("-- the listing: the year is the URL's")
page = "".join([
    card_html("7734", "WALTZ DANCE", "October 4", "2:45pm - 6:00pm"),
    card_html("7734", "WALTZ DANCE", "October 4", "2:45pm - 6:00pm"),
    card_html("9638", "JENNIE THE CAT: A HALLOWEEN ADVENTURE", "October 4", "<em><b>showtimes vary</b></em>"),
    card_html("7638", "HARVEST MOON VIENNESE BALL", "October 24", None),
    card_html("1111", "STRAY", "November 2", "7:00pm - 9:00pm"),
])
cards, notes = G.parse_listing(page, 2026, 10)
check("one card per node id, in page order", [c["id"] for c in cards] == ["7734", "9638", "7638"],
      [c["id"] for c in cards])
check("the card's date takes the REQUESTED year", cards[0]["date"] == date(2026, 10, 4), cards[0]["date"])
check("a card naming another month is refused, not re-dated",
      notes.get("card month is not the page's month") == 1, notes)
check("the time line is read off the card", cards[0]["time_line"] == "2:45pm - 6:00pm", cards[0]["time_line"])
check("a card with no time line has None", cards[2]["time_line"] is None, cards[2])
dec, _ = G.parse_listing(card_html("9", "CONTRA DANCE", "January 2", "7:30pm - 11:00pm"), 2027, 1)
check("a January page is January of the year asked for", dec and dec[0]["date"] == date(2027, 1, 2), dec)
ltr = '<b id="docs-internal-guid-a5356ece">2:45pm: Lesson | 3:30pm - 6:00pm: Social Dance</b>'
two, n2 = G.parse_listing("".join([
    card_html("9658", "BALLROOM DANCE", "December 27", ltr, until="December 27", ptag='<p dir="ltr">'),
    card_html("9700", "WINTER DANCE WEEKEND", "December 27", "7:00pm - 10:00pm", until="December 28"),
]), 2026, 12)
check("a Google-Docs <p dir=\"ltr\"> time line is still the time line",
      two and two[0]["time_line"] == "2:45pm: Lesson | 3:30pm - 6:00pm: Social Dance", two)
check("'December 27 - December 27' is one day and kept; '... - December 28' is refused, counted",
      [c["id"] for c in two] == ["9658"]
      and n2 == {"card spans days (December 27 - December 28), not guessed": 1}, (two, n2))
check("months_between crosses New Year",
      G.months_between(date(2026, 10, 4), date(2027, 1, 2)) == [(2026, 10), (2026, 11), (2026, 12), (2027, 1)])

# --------------------------------------------------------------------------- #
print("-- time lines")
for raw, want in [("2:45pm - 6:00pm", ("range", (14, 45), (18, 0))),
                  ("2:45 - 5:30pm", ("range", (14, 45), (17, 30))),
                  ("11 - 1pm", ("range", (11, 0), (13, 0))),
                  ("7:30pm -  10:30pm", ("range", (19, 30), (22, 30))),
                  ("12:00 pm - 5:00 pm", ("range", (12, 0), (17, 0))),
                  ("7:30pm - 12:30am", ("range", (19, 30), (0, 30))),
                  ("9:30am", ("start", (9, 30), None)),
                  ("showtimes vary", ("varies", None, None)),
                  (None, ("none", None, None)),
                  ("November 26, 2026", ("other", None, None)),
                  # a lesson then the dance, as one contiguous span (9658, 7638)
                  ("2:45pm: Lesson | 3:30pm - 6:00pm: Social Dance", ("schedule", (14, 45), (18, 0))),
                  ("7:30- 8:15 pm Lesson | 8:15 pm - 8:40pm Check-in for Ball | 8:40 pm - 11:45pm: "
                   "Social Dance to Live Conbrio Orchestra!", ("schedule", (19, 30), (23, 45))),
                  # a gap is two sessions, and one row never spans a gap
                  ("10:00am - 11:00am: Class | 2:00pm - 3:00pm: Jam", ("other", None, None)),
                  ("3:00pm: Jam | 1:00pm - 2:00pm: Class", ("other", None, None)),
                  ("7:00pm - 8:00pm: Lesson | 8:00pm: Dance", ("start", (19, 0), None))]:
    got = G.parse_time_line(raw)
    check(f"time line {raw!r}", (got["kind"], got["start"], got["end"]) == want, got)

# --------------------------------------------------------------------------- #
print("-- a detail page")
waltz = detail_html("Oct 04, 2:45 pm", "2:45pm - 6:00pm",
                    {"Presenter": "Waltz Time", "Location": "Spanish Ballroom",
                     "Admission": "$15 per person, $5 full-time students and children",
                     "Email": '<a href="mailto:info@waltztimedances.org">info@waltztimedances.org</a>'},
                    "<p><strong>2:45pm - 3:30pm: Lesson | 3:30pm - 6:00pm: Social Dance</strong></p>"
                    "<p>Waltz Time presents a waltz dance with live music by FOOT TRAFFIC. "
                    "Free lesson before the dance!</p>", WALTZ_LINKS)
d = G.parse_detail(waltz)
check("the <time> gives month, day and clock", (d["month"], d["day"], d["clock"]) == (10, 4, (14, 45)), d)
check("Label: value rows are read", d["fields"].get("Location") == "Spanish Ballroom", d["fields"])
check("the links line is not in the description", "Park Map" not in d["description"], d["description"])
check("the booking link is the CONTENT's Tickets anchor, not the site header's",
      G.booking_link(d["links"], "https://glenechopark.org") == "https://www.simpletix.com/e/waltz-295045",
      d["links"])
ev, why = G.build_event(cards[0], d, SITE, "https://glenechopark.org/events-calendar/event-detail/7734")
check("the waltz is a row", ev is not None, why)
check("start and end in America/New_York, EDT in October",
      (ev.start_local, ev.end_local, ev.start_utc) ==
      ("2026-10-04T14:45:00-04:00", "2026-10-04T18:00:00-04:00", "2026-10-04T18:45:00Z"),
      (ev.start_local, ev.end_local, ev.start_utc))
check("source_id is the node id", (ev.source, ev.source_id) == ("glenecho", "7734"))
check("placed from the BOOK, exact", (ev.latitude, ev.longitude, ev.coords_exact) ==
      (38.965599, -77.139068, True), (ev.latitude, ev.longitude))
check("the fee leads the description and says (not free)",
      ev.description.startswith("🎟 Admission: $15 per person, $5 full-time students and children (not free)."),
      ev.description[:120])
check("live music adds the music layer to a community dance",
      ev.category == "community" and "music" in ev.categories, (ev.category, ev.categories))

nov = dict(cards[0], date=date(2026, 11, 7), id="7999")
dn = G.parse_detail(waltz.replace("Oct 04, 2:45 pm", "Nov 07, 7:30 pm").replace("2:45pm - 6:00pm", "7:30pm - 12:30am"))
ev2, _ = G.build_event(dict(nov, time_line="7:30pm - 12:30am"), dn, SITE, "u")
check("after the clocks change it is EST, and 12:30am ends the NEXT day",
      (ev2.start_utc, ev2.end_local) == ("2026-11-08T00:30:00Z", "2026-11-08T00:30:00-05:00"),
      (ev2.start_utc, ev2.end_local))
bad, why = G.build_event(dict(cards[0], date=date(2026, 10, 5)), d, SITE, "u")
check("a detail page whose date disagrees with its card is refused", bad is None and "disagrees" in why, why)

# THE CLOCK: 7685, the Junior Ranger swearing-in. Card and page say 11:00am;
# the <time> says 10:00 am, copied from the tour it follows.
jr = G.parse_detail(detail_html("Nov 07, 10:00 am", "11:00am",
                                {"Location": "Glen Echo Park", "Admission": "FREE"},
                                "<p>on the first Saturday of each month at 11:00am</p>"))
jn = {}
ejr, _ = G.build_event({"id": "7685", "title": "JUNIOR RANGER PROGRAM SWEARING-IN EVENT",
                        "date": date(2026, 11, 7), "time_line": "11:00am"}, jr, SITE, "u", jn)
check("the card's '11:00am' wins over a <time> of 10:00 am",
      ejr.start_local == "2026-11-07T11:00:00-05:00", ejr.start_local)
check("and the disagreement is counted", jn == {G.CLOCK_DISAGREES: 1}, jn)
ewz, _ = G.build_event(cards[0], d, SITE, "u", jn)
check("an agreeing <time> is not counted", jn == {G.CLOCK_DISAGREES: 1}, jn)
bt = G.parse_detail(detail_html("Dec 27, 2:45 pm", "x", {"Location": "Ballroom Backroom", "Admission": "$20"},
                                until="Dec 27, 6:00 pm"))
check("the detail page's second <time> is read as the end", (bt["clock"], bt["end_clock"]) == ((14, 45), (18, 0)),
      bt)
ebt, _ = G.build_event({"id": "9658", "title": "BALLROOM DANCE", "date": date(2026, 12, 27),
                        "time_line": None}, bt, SITE, "u")
check("with no time line, the <time> pair is the session",
      (ebt.start_local, ebt.end_local) == ("2026-12-27T14:45:00-05:00", "2026-12-27T18:00:00-05:00"),
      (ebt.start_local, ebt.end_local))
ebs, _ = G.build_event({"id": "9658", "title": "BALLROOM DANCE", "date": date(2026, 12, 27),
                        "time_line": "2:45pm: Lesson | 3:30pm - 6:00pm: Social Dance"}, bt, SITE, "u")
check("a schedule line is one span, and is quoted",
      (ebs.start_local, ebs.end_local) == ("2026-12-27T14:45:00-05:00", "2026-12-27T18:00:00-05:00")
      and "Times: 2:45pm: Lesson | 3:30pm - 6:00pm: Social Dance." in ebs.description,
      (ebs.start_local, ebs.end_local))
span = G.parse_detail(detail_html("Dec 27, 7:00 pm", "x", {"Location": "Spanish Ballroom"}, until="Dec 28, 1:00 am"))
_, why = G.build_event({"id": "1", "title": "X", "date": date(2026, 12, 27), "time_line": None}, span, SITE, "u")
check("a <time> pair naming two days is refused, not guessed", why and "spans days" in why, why)
conc = G.parse_detail(detail_html("Oct 23, 7:00 pm", "7:00pm - 9:30pm", {"Location": "Spanish Ballroom"},
                                  "<p>An off-Broadway star sings.</p>"))
ec, _ = G.build_event({"id": "9564", "title": "4P'S DC REUNION CONCERT WITH THE SEAN FLEMING BAND",
                       "date": date(2026, 10, 23), "time_line": "7:00pm - 9:30pm"}, conc, SITE, "u")
check("a CONCERT is music, not community", ec.category == "music", (ec.category, ec.categories))

jennie = G.parse_detail(detail_html(
    "Oct 04, 10:30 am", "<em><b>showtimes vary</b></em>",
    {"Presenter": "the Puppet Co.", "Location": "the Puppet Co. Playhouse ",
     "Admission": " $17.93 ($16 plus $1.93 credit card fees); under age 2, no ticket required"}))
ej, _ = G.build_event(cards[1], jennie, SITE, "u")
check("'showtimes vary' is a DATE with no clock, not the <time>'s 10:30",
      (ej.start_local, ej.start_utc, ej.end_local) == ("2026-10-04", None, None), ej.start_local)
check("and says so", "Showtimes vary" in ej.description, ej.description)
check("children's theatre is kids with a theater layer, in the book's room name",
      (ej.category, ej.categories, ej.venue_name) == ("kids", ["theater"], "Puppet Co. Playhouse"),
      (ej.category, ej.categories, ej.venue_name))

# --------------------------------------------------------------------------- #
print("-- the fee, as 0227 will read it")
for raw, line, free in [
        ("FREE", "🎟 Admission: free.", True),
        ("Free!", "🎟 Admission: free.", True),
        ("$15/adult, $5/teen, FREE for ages 12 and under. $12/adult advance online only.",
         "🎟 Admission: $15/adult, $5/teen, FREE for ages 12 and under. $12/adult advance online only (not free).", False),
        ("FREE for members", "🎟 Admission: FREE for members (not free for everyone).", False),
        ("See description for specific pricing", "🎟 Admission: See description for specific pricing (not free).", False),
        ("", None, False)]:
    check(f"admission {raw!r}", G.admission_line(raw) == (line, free), G.admission_line(raw))
MD = os.path.join(HERE, "..", "mapsee", "tools", "measure_deals.py")
if os.path.exists(MD):
    import importlib.util
    spec = importlib.util.spec_from_file_location("measure_deals", MD)
    md = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(md)
    tag = lambda desc: "free" in md.classify({"title": "X", "description": desc})[0]
    check("0227 (Python twin) reads 'Admission: free.' as free", tag("🎟 Admission: free.\n\nA tour."))
    check("0227 never reads the waltz (a 'Free lesson' in the blurb) as free", not tag(ev.description),
          ev.description[:200])
    check("0227 never reads 'FREE for ages 12 and under' as a free event",
          not tag(G.admission_line("$15/adult, $5/teen, FREE for ages 12 and under.")[0]))
else:
    print("skip 0227 parity: ../mapsee/tools/measure_deals.py not checked out")

# --------------------------------------------------------------------------- #
print("-- refusals, and what must NOT be refused")
check("'No registration required.' is not a registration-only course",
      G.refusal("GLEN ECHO PARK CIVIL RIGHTS AND HISTORY TOUR",
                "This 45-minute ranger-led tour starts at the Carousel. No registration required.\nFREE") is None)
check("'RSVP (Preferred, not required)' is not either",
      G.refusal("FALL FROLIC", "RSVP (Preferred, not required) >>") is None)
check("'Registration is required.' is refused",
      G.refusal("POTTERY BASICS", "Six Tuesdays. Registration is required.") == "registration required")
check("'$12/adult advance online only' is the ticket, not an online-only dance",
      G.refusal("BUMPER CAR SQUARES", "$12/adult advance online only.") is None)
check("'CANCELLED TODAY- CAPITAL BLUES DANCE' is refused from the title",
      G.refusal("CANCELLED TODAY- CAPITAL BLUES DANCE", "") == "cancelled")
vd = G.parse_detail(detail_html("Oct 10, 10:30 am", "10:30am - 11:30am",
                                {"Location": "Virtual", "Admission": "FREE"}))
_, why = G.build_event({"id": "9746", "title": "ACO ARTIST DISCUSSION", "date": date(2026, 10, 10),
                        "time_line": "10:30am - 11:30am"}, vd, SITE, "u")
check("Location 'Virtual' is online-only, not an unplaceable room", why == "online only", why)
names = SITE["open_hours_titles"]
check("a studio's own name is its opening hours", G.is_open_hours("SILVERWORKS", names))
check("a gallery's name + ' |' + exhibition is its opening hours",
      G.is_open_hours("PARK VIEW GALLERY | INHERITED THREADS", names))
check("an artist talk is not", not G.is_open_hours("ACO ARTIST DISCUSSION WITH ALEX AND OLMSTED", names))
check("a critique session in Photoworks is not", not G.is_open_hours("COFFEE AND CRITIQUE", names))

# --------------------------------------------------------------------------- #
print("-- the venue book")
check("'the Puppet Co. Playhouse ' is the book's Puppet Co. Playhouse",
      G.place("the Puppet Co. Playhouse ", SITE)[0] == "Puppet Co. Playhouse")
check("the street address after the bar is not part of the room",
      G.place("Adventure Theatre | 7300 MacArthur Blvd / Glen Echo, MD 20812", SITE)[0] == "Adventure Theatre MTC")
check("a numbered room keeps the page's words, on its building's point",
      G.place("Arcade Building Classrooms 202 & 203", SITE)[0] == "Arcade Building Classrooms 202 & 203")
# With a one-word alias, as the first draft of the book had ("the Park"): a
# prefix match on it put the Park View Gallery on the park's centre pin.
parky = dict(SITE, places=dict(SITE["places"], **{"Glen Echo Park": dict(
    SITE["places"]["Glen Echo Park"], aliases=["the Park"])}))
check("'Park View Gallery' is NOT swallowed by a one-word key ('the Park')",
      G.place("Park View Gallery", parky) is None and G.place("Park View Gallery", SITE) is None)
check("misspellings the site uses are aliases",
      G.place("Bumper Car Pavillion", SITE)[0] == "Bumper Car Pavilion" and
      G.place("Ballroom Backroom", SITE)[0] == "Ballroom Back Room")
back = G.parse_detail(waltz.replace("Spanish Ballroom", "Ballroom Backroom"))
eb, _ = G.build_event(dict(cards[0], id="9658"), back, SITE, "u")
check("two dances at one time in the Ballroom and its Back Room are two fingerprints",
      eb.fingerprint != ev.fingerprint, eb.venue_name)
check("the fingerprint is widened by the clock; a date-only row's is make_fingerprint's",
      ej.fingerprint == make_fingerprint(ej.name, "2026-10-04", "Puppet Co. Playhouse")
      and ev.fingerprint != make_fingerprint(ev.name, ev.start_local, ev.venue_name))
out = [k for k, v in SITE["places"].items() if not k.startswith("_") and not k.startswith("Clara")
       and not (38.9640 < v["lat"] < 38.9690 and -77.1410 < v["lon"] < -77.1365)]
check("every building is inside the park's box", not out, out)

# --------------------------------------------------------------------------- #
print("-- the HTTP client and the run")


class Resp:
    def __init__(self, code, text="", headers=None):
        self.status_code, self.text, self.headers = code, text, headers or {}


class Session:
    def __init__(self, pages):
        self.pages, self.asked = pages, []

    def get(self, url, **kw):
        self.asked.append(url)
        got = self.pages.get(url, (404, ""))
        return Resp(*got) if isinstance(got, tuple) else got


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


B = "https://glenechopark.org"
listing = "".join([card_html("7734", "WALTZ DANCE", "October 4", "2:45pm - 6:00pm"),
                   card_html("8342", "SILVERWORKS", "October 4", "11:00am - 5:00pm"),
                   card_html("8845", "CANCELLED TODAY- CAPITAL BLUES DANCE", "October 8", "8:15pm - 11:30pm")])
pages = {f"{B}/events-calendar/202610": (200, listing),
         f"{B}/events-calendar/event-detail/7734": (200, waltz)}
sess, clk = Session(pages), Clock()
site = dict(SITE, within_days=20)
cl = G.Client(sess, 1.0, None, clock=clk, sleep=clk.sleep)
rows = []
rep = G.read_site(site, cl, date(2026, 10, 4), rows.append)
check("open hours and a cancelled dance cost no detail request",
      sess.asked == [f"{B}/events-calendar/202610", f"{B}/events-calendar/event-detail/7734"], sess.asked)
check("they are counted", rep["refused"] == {"gallery/studio open hours": 1, "cancelled": 1}, rep["refused"])
check("one row out, one request per second", len(rows) == 1 and clk.t >= 1.0, (len(rows), clk.t))

# ONE TIMEOUT. requests' ReadTimeout is an OSError; so is a bare reset.
try:
    from requests.exceptions import ReadTimeout as _Timeout
except ImportError:                    # the gate installs requests; this is belt and braces
    _Timeout = TimeoutError


class Flaky(Session):
    def __init__(self, pages, bad):
        super().__init__(pages)
        self.bad = bad

    def get(self, url, **kw):
        if any(url.endswith("/" + b) for b in self.bad):
            self.asked.append(url)
            raise _Timeout("read timed out")
        return super().get(url, **kw)


three = "".join(card_html(n, "WALTZ DANCE", f"October {dd}", "2:45pm - 6:00pm")
                for n, dd in (("7734", 4), ("7735", 11), ("7736", 18)))
tpages = {f"{B}/events-calendar/202610": (200, three),
          f"{B}/events-calendar/event-detail/7735": (200, waltz.replace("Oct 04", "Oct 11")),
          f"{B}/events-calendar/event-detail/7736": (200, waltz.replace("Oct 04", "Oct 18"))}
sess, rows = Flaky(tpages, ["7734"]), []
try:
    rep = G.read_site(site, G.Client(sess, 1.0, None, clock=clk, sleep=clk.sleep), date(2026, 10, 4), rows.append)
except Exception as exc:                # the first build: the timeout escaped and ended the run
    rep = {"stopped": f"RAISED {type(exc).__name__}", "notes": {}}
check("a timeout on one detail page costs that page, not the run",
      [r.source_id for r in rows] == ["7735", "7736"] and rep["stopped"] is None, (rep["stopped"], len(rows)))
check("and it is counted", rep["notes"].get("network error, page skipped") == 1
      and rep["notes"].get("detail page not read") == 1, rep["notes"])
sess, rows = Flaky(tpages, ["7734", "7735", "7736"]), []
try:
    rep = G.read_site(site, G.Client(sess, 1.0, None, clock=clk, sleep=clk.sleep, max_failures=2),
                      date(2026, 10, 4), rows.append)
except Exception as exc:
    rep = {"stopped": f"RAISED {type(exc).__name__}", "notes": {}}
check("max_failures in a row end the run; no request after",
      "not answering" in (rep["stopped"] or "") and len(sess.asked) == 3, (rep["stopped"], sess.asked))
sess, rows = Session(dict(tpages, **{f"{B}/events-calendar/event-detail/7734": (200, waltz)})), []
sess.pages[f"{B}/events-calendar/event-detail/7735"] = (200, waltz)       # same title and start
three_same = "".join(card_html(n, "WALTZ DANCE", "October 4", "2:45pm - 6:00pm") for n in ("7734", "7735"))
sess.pages[f"{B}/events-calendar/202610"] = (200, three_same)
rep = G.read_site(site, G.Client(sess, 1.0, None, clock=clk, sleep=clk.sleep), date(2026, 10, 4), rows.append)
check("same title, presenter and start on two nodes is named, and both are kept",
      len(rows) == 2 and rep["notes"].get("same title, presenter and start as another node (a double posting?)") == 1,
      rep["notes"])

sess = Session({f"{B}/events-calendar/202610": (403, "Forbidden")})
rep = G.read_site(site, G.Client(sess, 1.0, None, clock=clk, sleep=clk.sleep), date(2026, 10, 4), rows.append)
check("a 403 ends the site as a refusal, with no retry",
      (rep["stopped"] or "").startswith("REFUSED") and len(sess.asked) == 1, (rep["stopped"], sess.asked))
sess = Session({f"{B}/events-calendar/202610": (200, "<title>Just a moment...</title>")})
rep = G.read_site(site, G.Client(sess, 1.0, None, clock=clk, sleep=clk.sleep), date(2026, 10, 4), rows.append)
check("a bot challenge is a refusal", (rep["stopped"] or "").startswith("REFUSED"), rep["stopped"])
sess = Session({f"{B}/events-calendar/202610": (302, "", {"Location": "https://elsewhere.example/x"})})
cl = G.Client(sess, 1.0, None, clock=clk, sleep=clk.sleep)
check("a redirect off the site is not followed", cl.get(f"{B}/events-calendar/202610") is None
      and len(sess.asked) == 1, sess.asked)


class NoRobots:
    def check(self, url):
        return {"allowed": False, "status": "ok", "rule": "Disallow: /", "crawl_delay": None}


sess = Session(pages)
rep = G.read_site(site, G.Client(sess, 1.0, None, robots=NoRobots(), clock=clk, sleep=clk.sleep),
                  date(2026, 10, 4), rows.append)
check("robots.txt Disallow means nothing is fetched", sess.asked == [] and "robots" in (rep["stopped"] or ""),
      (sess.asked, rep["stopped"]))
sess = Session(pages)
rep = G.read_site(site, G.Client(sess, 1.0, clk.t - 1, clock=clk, sleep=clk.sleep), date(2026, 10, 4),
                  rows.append)
check("past the deadline no request starts", sess.asked == [] and "deadline" in (rep["stopped"] or ""),
      rep["stopped"])

check("the config's horizon is bounded", 0 < SITE["within_days"] <= 120 and SITE["max_events"] <= 1000)

# --------------------------------------------------------------------------- #
print("-- a called-off dance is a tombstone with the live dance's fingerprint")
# 2026-10-05 (the owner): "we don't want users to go to an event that is
# closed". SYNTHETIC: a dance retitled in place (the 5 real notices that day
# were cards of their own - see called_off's comment). The row we wrote stays
# on the map unless the run tombstones the fingerprint THAT row has.
check("'CANCELLED TODAY- X' names X", G.called_off("CANCELLED TODAY- CAPITAL BLUES DANCE") == "CAPITAL BLUES DANCE")
check("'X - POSTPONED' names X", G.called_off("Swing Night - POSTPONED") == "Swing Night")
check("'Cancelled: X' names X", G.called_off("Cancelled: Contra Dance") == "Contra Dance")
check("'RESCHEDULED' is not a tombstone (the card may sit on the new date)",
      G.called_off("RESCHEDULED - Contra Dance") is None)
check("the word alone names nothing", G.called_off("CANCELLED") is None and G.called_off("Cancelled -") is None)
check("a closure names no dance", G.called_off("Ballroom closed") is None)
check("a mid-title 'cancelled' is not stripped", G.called_off("The Cancelled Plans Comedy Hour") is None)

import tempfile
from mapsee_ingest import EventStore

blues = detail_html("Oct 08, 8:15 pm", "8:15pm - 11:30pm",
                    {"Presenter": "Capital Blues", "Location": "Spanish Ballroom", "Admission": "$15"})
tmp = tempfile.mkdtemp()
live_store = EventStore(os.path.join(tmp, "live.json"))
off_store = EventStore(os.path.join(tmp, "off.json"))
live_pages = {f"{B}/events-calendar/202610": (200, card_html("8845", "CAPITAL BLUES DANCE", "October 8",
                                                             "8:15pm - 11:30pm")),
              f"{B}/events-calendar/event-detail/8845": (200, blues)}
off_pages = dict(live_pages, **{f"{B}/events-calendar/202610": (200, card_html(
    "8845", "CANCELLED TODAY- CAPITAL BLUES DANCE", "October 8", "8:15pm - 11:30pm"))})
rep_live = G.read_site(site, G.Client(Session(live_pages), 1.0, None, clock=clk, sleep=clk.sleep),
                       date(2026, 10, 4), live_store.upsert, live_store.cancel)
sess = Session(off_pages)
rep_off = G.read_site(site, G.Client(sess, 1.0, None, clock=clk, sleep=clk.sleep),
                      date(2026, 10, 4), off_store.upsert, off_store.cancel)
live_fp = next(iter(live_store.records), None)
check("the live dance is one row", len(live_store.records) == 1 and not live_store.tombstones)
check("the called-off card writes no row and one tombstone",
      not off_store.records and len(off_store.tombstones) == 1 and rep_off["cancelled"] == 1,
      (len(off_store.records), off_store.tombstones))
check("THE TOMBSTONE IS THE LIVE ROW'S FINGERPRINT (events.external_id)",
      live_fp is not None and list(off_store.tombstones) == [live_fp], (live_fp, list(off_store.tombstones)))
tomb = next(iter(off_store.tombstones.values()), {})
check("same source and source_id as the live row, reason is the card's title",
      tomb.get("source") == "glenecho" and tomb.get("source_id") == "8845"
      and tomb.get("reason") == "CANCELLED TODAY- CAPITAL BLUES DANCE", tomb)
check("it costs the one detail request that names the room",
      sess.asked == [f"{B}/events-calendar/202610", f"{B}/events-calendar/event-detail/8845"], sess.asked)
both = EventStore(os.path.join(tmp, "both.json"))
live_card = G.parse_listing(live_pages[f"{B}/events-calendar/202610"][1], 2026, 10)[0][0]
both.upsert(G.build_event(live_card, G.parse_detail(blues), site, "u")[0])
G.read_site(site, G.Client(Session(off_pages), 1.0, None, clock=clk, sleep=clk.sleep),
            date(2026, 10, 4), both.upsert, both.cancel)
check("a live record of the dance in the same store loses to the tombstone",
      not both.records and list(both.tombstones) == [live_fp], (list(both.records), list(both.tombstones)))

print("-- complete reads, and only those, are marked")
check("a read of every card is complete; its window is the read's own",
      rep_live["complete"] and rep_off["complete"]
      and rep_live["window"] == (date(2026, 10, 4), date(2026, 10, 24)), (rep_live["complete"], rep_live["window"]))
sess, rows = Flaky(tpages, ["7734"]), []
rep = G.read_site(site, G.Client(sess, 1.0, None, clock=clk, sleep=clk.sleep), date(2026, 10, 4), rows.append)
check("one detail page lost: NOT complete (its dance would read as gone)", rep["complete"] is False, rep["notes"])
rep = G.read_site(site, G.Client(Session(live_pages), 1.0, clk.t - 1, clock=clk, sleep=clk.sleep),
                  date(2026, 10, 4), rows.append)
check("stopped by the deadline: NOT complete", rep["complete"] is False, rep["stopped"])
rep = G.read_site(site, G.Client(Session({f"{B}/events-calendar/202610": (200, "<html>new theme</html>")}),
                                 1.0, None, clock=clk, sleep=clk.sleep), date(2026, 10, 4), rows.append)
check("a month that matches no card (new markup): NOT complete", rep["complete"] is False, rep["notes"])
off_missing = {f"{B}/events-calendar/202610": off_pages[f"{B}/events-calendar/202610"]}
rep = G.read_site(site, G.Client(Session(off_missing), 1.0, None, clock=clk, sleep=clk.sleep),
                  date(2026, 10, 4), rows.append, lambda ev, why: None)
check("a called-off card whose page was not read: NOT complete", rep["complete"] is False, rep["notes"])


class MainSession(Session):
    headers = {}


import requests as _requests
import robots_txt as _robots_txt


class AllowAll:
    def __init__(self, *a, **k):
        pass

    def check(self, url):
        return {"allowed": True, "status": "ok", "rule": None, "crawl_delay": None}


cfg_path, store_path = os.path.join(tmp, "cfg.json"), os.path.join(tmp, "store.json")
json.dump({"sites": [dict(site, pause=0)]}, open(cfg_path, "w", encoding="utf-8"))
_saved = (_requests.Session, _robots_txt.Robots)
_requests.Session, _robots_txt.Robots = (lambda: MainSession(off_pages)), AllowAll
try:
    G.main(["--config", cfg_path, "--store", store_path, "--max-minutes", "0"])
    saved = json.load(open(store_path, encoding="utf-8"))
    _requests.Session = lambda: MainSession({})
    G.main(["--config", cfg_path, "--store", os.path.join(tmp, "store2.json"), "--max-minutes", "0"])
    saved2 = json.load(open(os.path.join(tmp, "store2.json"), encoding="utf-8"))
finally:
    _requests.Session, _robots_txt.Robots = _saved
cr = (saved.get("complete_reads") or {}).get("glenecho") or {}
check("main: a complete run saves the tombstone and marks 'glenecho' complete over its window",
      [t.get("fingerprint") for t in saved.get("tombstones") or []] == [live_fp]
      and cr.get("from", "").startswith("2026-10-04") and cr.get("to", "").startswith("2026-10-24"), (saved.get("tombstones"), cr))
check("main: a run whose month page failed marks nothing complete", not saved2.get("complete_reads"),
      saved2.get("complete_reads"))

print()
print(f"{'FAILURES: ' + ', '.join(fails) if fails else 'all checks passed'}")
sys.exit(1 if fails else 0)
