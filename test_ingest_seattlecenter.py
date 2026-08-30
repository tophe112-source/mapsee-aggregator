"""Seattle Center: the date is not on the card, the coordinate is not on the
page, and a matinee is not the evening show.

WHY THIS EXISTS. This is the repo's second HTML scraper, and every fact it
needs is offered to it TWICE with the obvious copy wrong. Each of the three is
silent — the row comes out well-formed, on a real street, at a plausible hour,
and nothing downstream can tell:

  • THE DATE. The listing groups cards under a `date-bar__date` heading and the
    card itself carries only a clock. Inheriting the heading gives a date with
    NO YEAR, and this calendar runs seven months ahead, so every January show
    would be stamped eleven months in the past and dropped by the horizon
    filter without a word. The detail page states it in full, and that is the
    only thing the adapter reads for it.

  • THE COORDINATE. Locations are Google Maps links carrying two pairs, and
    `@lat,lon` — the one a naive regex finds first — is the viewport centre,
    a constant 165m west of the place on every link on this site. Worse, on a
    DETAIL page the first maps link usually belongs to a different event
    entirely, because of the related-events rail. Hence the venue book; these
    tests pin that the adapter never reads a coordinate out of a page.

  • THE SHOWING. Freak the Mighty plays a 2:00 p.m. matinee and a 7:30 p.m.
    evening performance on the same Saturday, filed by the site as separate
    listings because they are separate performances. make_fingerprint truncates
    to YYYY-MM-DD by design — it is the cross-source key — so name|date|place is
    byte-identical for the pair and EventStore, which dedupes on the fingerprint
    PRIMARY, silently drops one. Measured on the live sample before the fix: 59
    events, 57 fingerprints, both losses a Saturday matinee.

No network: every case is a literal fragment of the real markup.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import json

import mapsee_ingest_seattlecenter as SC

fails = []


def check(label, cond, detail=""):
    print(f"{'ok  ' if cond else 'FAIL'} {label}{'' if cond else '   ' + str(detail)}")
    if not cond:
        fails.append(label)


# --------------------------------------------------------------------------- #
# fixtures - trimmed from the real pages, keeping the shapes that matter
# --------------------------------------------------------------------------- #
SITE = {
    "name": "Seattle Center",
    "category": "community",
    "default_city": "Seattle",
    "default_region": "WA",
    "default_country": "US",
    "skip_places": ["Climate Pledge Arena at Seattle Center"],
    "places": {
        "bagley wright theatre": {"address": "155 Mercer St", "lat": 47.6239152,
                                  "lon": -122.3536861, "coords_exact": True,
                                  "category": "theater"},
        "grounds public space": {"address": "305 Harrison St", "lat": 47.6219473,
                                 "lon": -122.3517443},
    },
}


def detail(name="Freak the Mighty", date_="Saturday, August 29, 2026",
           time_="2:00 p.m.", place_="Bagley Wright Theatre", cost="Entry Charge",
           maps=True, tickets=True):
    """One detail page. `maps` adds the related-events rail whose location link
    belongs to SOME OTHER event — which is what the real pages do."""
    rail = ('<a href="https://www.google.com/maps/place/Seattle+Center/'
            '@47.6221,-122.3561767,17z/data=!3m1!4b1!4m5!3m4!1s0x0:0x0'
            '!8m2!3d47.6221!4d-122.353988" class="event-list__location-link">Location</a>'
            ) if maps else ""
    tix = ('<a href="https://www.seattlerep.org/tickets">Tickets</a>'
           '<a href="https://www.seattlerep.org/plays/events">More Information</a>'
           ) if tickets else ""
    return f"""
    <html><head>
      <meta name="description" content="A world premiere musical." />
      <meta property="og:image" content="https://seattlecenter.com/hero.jpg" />
    </head><body>
      <h1>{name}</h1>
      <div class="row"><div class="col"><div class="event__label">Date</div></div>
        <div class="col"><div class="event__detail"> {date_}</div></div></div>
      <div class="row"><div class="col"><div class="event__label">Time</div></div>
        <div class="col"><div class="event__detail">\n{time_}       </div></div></div>
      <div class="row"><div class="col"><div class="event__label">Place</div></div>
        <div class="col"><div class="event__detail">{place_}</div></div></div>
      <div class="row"><div class="col"><div class="event__label">Cost</div></div>
        <div class="col"><div class="event__detail">{cost}</div></div></div>
      {rail}{tix}
      <a href="http://seattlecenter.org/support/">Support Us</a>
      <a href="https://x.com/seattlecenter">Seattle Center X (formerly Twitter)</a>
    </body></html>"""


# --------------------------------------------------------------------------- #
# the date comes from the detail page, with its year
# --------------------------------------------------------------------------- #
check("the full date parses, year included",
      SC.parse_detail_date("Saturday, August 29, 2026") == "2026-08-29",
      SC.parse_detail_date("Saturday, August 29, 2026"))
check("a zero-padded day parses",
      SC.parse_detail_date("Tuesday, September 08, 2026") == "2026-09-08")
# The listing's heading, fed to the DATE parser, must not resolve to anything.
# If it ever does, somebody has taught the adapter to accept a yearless date and
# the January bug is back.
check("a listing heading is NOT accepted as a date - it has no year",
      SC.parse_detail_date("August 29") is None,
      SC.parse_detail_date("August 29"))
check("an unparseable date is refused rather than guessed from today",
      SC.parse_detail_date("Next Saturday") is None)

# --------------------------------------------------------------------------- #
# "All Day" is not midnight
# --------------------------------------------------------------------------- #
check("an evening time parses to 24h", SC.parse_detail_time("8:00 p.m.") == "20:00:00")
check("a morning time parses to 24h", SC.parse_detail_time("10:00 a.m.") == "10:00:00")
check("noon is midday, not midnight", SC.parse_detail_time("12:00 p.m.") == "12:00:00")
check("'All Day' yields NO clock, so the row is not stamped 00:00",
      SC.parse_detail_time("All Day") is None, SC.parse_detail_time("All Day"))
allday = SC.localize("2026-08-29", None)
check("an all-day row carries a bare date and no instant",
      allday == ("2026-08-29", None), allday)

# The instant is derived through the campus timezone, so it survives DST. August
# is -07:00 in Seattle and January is -08:00; a fixed offset gets one of them
# wrong for half the calendar's range.
summer = SC.localize("2026-08-29", "19:30:00")
winter = SC.localize("2026-01-14", "19:30:00")
check("a summer show converts at -07:00",
      summer == ("2026-08-29T19:30:00-07:00", "2026-08-30T02:30:00Z"), summer)
check("a winter show converts at -08:00, so DST is not hard-coded",
      winter == ("2026-01-14T19:30:00-08:00", "2026-01-15T03:30:00Z"), winter)

# --------------------------------------------------------------------------- #
# a matinee is not the evening show
# --------------------------------------------------------------------------- #
mat = SC.to_event("events/event-calendar/freak-the-mighty-x47869",
                  detail(time_="2:00 p.m."), SITE)
eve = SC.to_event("events/event-calendar/freak-the-mighty-x47870",
                  detail(time_="7:30 p.m."), SITE)
check("both showings parse", mat is not None and eve is not None)
check("the same show twice in one day is TWO events, not one",
      mat and eve and mat.fingerprint != eve.fingerprint,
      f"{mat and mat.fingerprint} vs {eve and eve.fingerprint}")
# The bare helper is what every other adapter uses, and it is what loses them —
# pinned so the reason this adapter does something different stays visible.
from mapsee_ingest import make_fingerprint  # noqa: E402
check("...which the shared date-keyed helper alone would NOT have separated",
      make_fingerprint("Freak the Mighty", "2026-08-29T14:00:00-07:00", "Bagley Wright Theatre")
      == make_fingerprint("Freak the Mighty", "2026-08-29T19:30:00-07:00", "Bagley Wright Theatre"))
# Two all-day rows on one date have no clock to tell apart and must still agree
# with the plain helper, so cross-source matching is untouched for them.
a1 = SC.to_event("events/event-calendar/walk-x1",
                 detail(name="Sculpture Walk", time_="All Day", place_="Grounds/Public Space"), SITE)
check("an all-day row keeps the plain cross-source fingerprint",
      a1 and a1.fingerprint == make_fingerprint("Sculpture Walk", "2026-08-29", "Grounds/Public Space"))

# --------------------------------------------------------------------------- #
# placement never comes from the page
# --------------------------------------------------------------------------- #
# The fixture's maps rail carries 47.6221,-122.3561767 (the `@` centre) and
# 47.6221,-122.353988 (the `!3d!4d` place) — Climate Pledge Arena, ~300m from
# the Bagley Wright. Neither may reach the event.
check("the coordinate is the book's, not the one in the page's map link",
      mat and (mat.latitude, mat.longitude) == (47.6239152, -122.3536861),
      mat and (mat.latitude, mat.longitude))
check("a surveyed building is marked exact so the Census pass cannot move it",
      mat and mat.coords_exact is True)
check("a campus-level pin is NOT marked exact", a1 and a1.coords_exact is False)
check("the city is set, so locality is not NULL", mat and mat.city == "Seattle")

unknown = SC.to_event("events/event-calendar/x", detail(place_="Some New Room"), SITE)
check("an unknown Place is skipped rather than pinned somewhere plausible",
      unknown is None)
check("a Place that is only whitespace is incomplete, not placed",
      SC.to_event("events/event-calendar/x", detail(place_=" "), SITE) is None)

# The book is matched loosely, because the site writes one room several ways.
check("'X at Seattle Center' and 'The X' find the same book entry",
      SC._norm_place("The Bagley Wright Theatre at Seattle Center")
      == SC._norm_place("Bagley Wright Theatre") == "bagley wright theatre",
      SC._norm_place("The Bagley Wright Theatre at Seattle Center"))
check("skip_places matches just as loosely, so a renamed arena stays skipped",
      SC.to_event("events/event-calendar/j-cole",
                  detail(name="J. Cole", place_="The Climate Pledge Arena"), SITE) is None)

# --------------------------------------------------------------------------- #
# category is a DEFAULT, and a specific-but-wrong one blocks the classifier
# --------------------------------------------------------------------------- #
# CLAUDE.md's rule: a PURE calendar states its own key, a MIXED one states
# `community` and lets derive_categories promote. Measured on the live sample,
# giving the general rooms specific keys made things WORSE — "Summer Fitness:
# Workout Wednesdays: Yoga" on the Exhibition Hall lawn stopped reaching
# fitness, because `outdoors` is not promotable and `community` is.
check("a dedicated theatre states its own key", mat and mat.category == "theater")
check("a general room falls back to the site default so promotions still fire",
      a1 and a1.category == "community", a1 and a1.category)

# --------------------------------------------------------------------------- #
# the rest of the record
# --------------------------------------------------------------------------- #
check("the booking link is the 'Tickets' anchor, not site chrome",
      mat and mat.ticket_url == "https://www.seattlerep.org/tickets", mat and mat.ticket_url)
nofree = SC.to_event("events/event-calendar/x", detail(tickets=False), SITE)
check("with nothing to book, the row still points somewhere - its own page",
      nofree and nofree.ticket_url.startswith("https://seattlecenter.com/events/"),
      nofree and nofree.ticket_url)
check("'Free Event' survives into the description, where a reader sees it",
      (SC.to_event("events/event-calendar/x", detail(cost="Free Event"), SITE) or
       type("x", (), {"description": ""})).description.startswith("Free Event"))
check("source_id is per-OCCURRENCE, so one night cannot overwrite the last",
      mat and eve and mat.source_id != eve.source_id
      and mat.source_id == "freak-the-mighty-x47869")

# --------------------------------------------------------------------------- #
# pagination: the year walk, and the stop
# --------------------------------------------------------------------------- #
import datetime as _dt  # noqa: E402

walk = SC._year_walk(["August 24", "December 20", "January 03", "March 07"],
                     _dt.date(2026, 8, 24))
check("the listing's yearless headings roll over at New Year",
      walk == [_dt.date(2026, 8, 24), _dt.date(2026, 12, 20),
               _dt.date(2027, 1, 3), _dt.date(2027, 3, 7)], walk)
check("...and that walk is only used to stop paginating - to_event never sees it",
      "_year_walk" not in SC.to_event.__code__.co_names)

# --------------------------------------------------------------------------- #
# the config itself
# --------------------------------------------------------------------------- #
CFG = json.load(open("seattlecenter_sources.json", encoding="utf-8"))
site = CFG["sites"][0]
check("the config parses and names the calendar page",
      site["listing"] == "https://seattlecenter.com/events/event-calendar")
check("every place in the book is placeable",
      all(SC.place(k, site) for k in site["places"]),
      [k for k in site["places"] if not SC.place(k, site)])
check("every place carries coordinates, so no run spends the geocode budget",
      all(v.get("lat") and v.get("lon") for v in site["places"].values()))
# Every venue sits on the Seattle Center campus; anything outside this box is a
# typo or a copied coordinate, and a pin in the wrong city is what this whole
# file is about.
out = [k for k, v in site["places"].items()
       if not (47.615 < v["lat"] < 47.630 and -122.360 < v["lon"] < -122.345)]
check("every pin is inside the Seattle Center campus box", not out, out)
check("the horizon is bounded, so 630 detail fetches cannot happen by accident",
      0 < site["within_days"] <= 120 and site["max_pages"] <= 60)

print()
print(f"{'FAILURES: ' + ', '.join(fails) if fails else 'all checks passed'}")
sys.exit(1 if fails else 0)
