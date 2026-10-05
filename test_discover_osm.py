#!/usr/bin/env python3
"""
test_discover_osm.py — finding venue calendars from the map.

Prints one line per case and exits non-zero on failure, like the other 17.
No network: every fixture is markup taken verbatim from the live site named.

What is pinned is what the first real sweeps got wrong:

  * DETECTING A SITE BUILDER IS NOT DETECTING A CALENDAR. Squarespace and Wix
    match on EVERY page of every site built with them, including a hand-written
    "What's On" with nothing behind it. 4 of the 5 candidates that failed the
    first London verification were Wix sites found this way, and White Bear
    Theatre's does not use Wix Events at all. A builder now has to show its
    events app; a calendar PLUGIN (tribe, my-calendar, wp-event-manager) still
    counts on its own, because that is a thing that has feeds.
  * A CHALLENGE DOES NOT ANSWER 403. SiteGround answers 202 and the WAF in front
    of theblackaltar.org answers a clean 200 with a spinner. A 200 is the
    dangerous one: HTML arrives where JSON was expected, and the honest readings
    of that are "broken feed" and "calendar with nothing on", neither of which is
    what happened.
  * A REFUSAL IS NOT A FACT ABOUT THE SITE. theblackaltar.org served one probe
    and challenged the next, seconds apart from the same IP. "No events page" is
    stable and worth parking in the ledger for the TTL; a challenge or a timeout
    is not, and parking it would retire a working calendar over a bad moment —
    with the metro cursor meaning nobody looks again for months.
  * WIX ROUTES EVENTS THROUGH A PAGE OF ITS OWN, not /event/<slug>/, and WHICH
    page is the site's: /event-info/ on the older sites, /event-details/ on the
    newer, /events/ where it was renamed. Writing /event-info/ for all of them
    broke 13 of the 15 configured Wix entries (2026-10-04), so the route is
    read off the site's own links — ALL of the path in front of the slug, since
    a free site lives at <user>.wixsite.com/<site>/ and the adapter resolves
    the template against the host. And a Wix event's own page is one event:
    13 of those 15 had one as their listing, so the homepage that linked it is
    proposed instead.
  * A REFUSED OVERPASS ASK ENDS THE RUN, so it is asked eight times: 6 of the
    community walk's 13 runs ended on a metro refused four times running. But
    inside a patience, since an ask can hang 200 s and eight of those would
    run past the caller's deadline.
  * THE WALK PROBES FOUR VENUES AT ONCE AND NEVER TWO ON ONE HOST. It is
    time-bound (950-1,450 probes per 55-minute run, one at a time), and venues
    share hosts more than it looks: 19 of Winnipeg's 30 are on one library
    site. Every write stays on the main thread, the deadline still binds, and
    an unreachable venue is parked a week under its own status so a metro cut
    short does not pay for it again the next day.
  * ROBOTS.TXT BINDS DISCOVERY TOO, every request and every redirect hop.
    The walk fetched halifaxpubliclibraries.ca/explore/... on 10 of 11
    branch probes, a path that host disallows. And a page linked from every
    branch is read once a run, not once a branch (19 of Hamilton's 80
    requests were repeats).
  * A PLATFORM CAN IMPLY A FEED URL. My Calendar publishes iCal at a fixed path
    and never links to it, so scraping for an .ics href finds nothing and a
    readable site looks unreadable.
"""
import json
import re
import sys
from urllib.parse import urljoin

import catalog_discover_osm as osm

FAILURES = []


def check(label, got, want):
    ok = got == want
    print(("  ok   " if ok else "  FAIL ") + label + ("" if ok else f"\n         got {got!r}\n        want {want!r}"))
    if not ok:
        FAILURES.append(label)


def check_true(label, got):
    check(label, bool(got), True)


# --- fixtures ---------------------------------------------------------------
# volunteerparktrust.org/events — a real Squarespace EVENTS COLLECTION.
SQSP_REAL = '''<html><body class="collection-type-events collection-5f0a">
<script src="https://static1.squarespace.com/static/vta/x.js"></script>
<article class="eventlist-event"><time class="eventlist-meta-date">Sep 6</time></article>
</body></html>'''

# tramshed.org/whatson — Squarespace, and its What's On is a hand-built page.
SQSP_PROSE = '''<html><body class="collection-type-page">
<script src="https://static1.squarespace.com/static/vta/x.js"></script>
<h1>What's On</h1><p>See our shows.</p></body></html>'''

# whitebeartheatre.co.uk/whatson — Wix, no Wix Events app anywhere on it.
WIX_PROSE = '''<html><head><link href="https://static.parastorage.com/s/x.css"></head>
<body><h1>What's On</h1></body></html>'''

# a Wix site that DOES run Wix Events.
WIX_EVENTS = '''<html><head><link href="https://static.parastorage.com/s/x.css"></head>
<body><a href="/event-info/live-jazz-night">Live Jazz</a>
<script>{"slug":"live-jazz-night","title":"Live Jazz"}</script></body></html>'''

# ---- Wix Events, as the live pages ship it (2026-10-04), trimmed ----------
# Every page of a site with the app carries the app's id in its site
# structure; the events themselves are objects in the warmup data, each with
# a `slug` and a `scheduling`.
_WIX_HEAD = ('<html><head><link href="https://static.parastorage.com/s/x.css">'
             '<script>{"siteStructureApi":"wixArtifactId:com.wixpress.wix-events-web"}'
             '</script></head><body>')


def _wix_ev(slug, start="2026-10-14T19:30:00.000Z", ext=None):
    e = {"id": slug, "slug": slug, "title": slug.replace("-", " "),
         "scheduling": {"config": {"startDate": start}},
         "location": {"name": "Hall", "address": "1 High St"},
         "registration": {"type": 3 if ext else 4}}
    if ext:
        e["registration"]["external"] = {"registration": ext}
    return e


def _wix_page(*events, body="", extra=None):
    warm = {"appsWarmupData": {"140603ad-af8d-84a5-2c80-a0f60cb47351": {
        "widgetcomp-k1": {"events": {"events": list(events)}}}}}
    if extra:
        warm["appsWarmupData"]["blog"] = extra
    # Wix escapes every slash in its JSON, which is the form the route has to
    # be read through.
    data = json.dumps(warm, separators=(",", ":")).replace("/", "\\/")
    return (_WIX_HEAD + body + '<script type="application/json" id="wix-warmup-data">'
            + data + "</script></body></html>")


# seamonsterlounge.com/buy-tickets-in-advance — an older site: the share
# links say /event-info/, and so does every event page that answers.
WIX_SEA_MONSTER = _wix_page(
    _wix_ev("hot-rod-2026-10-14-19-30"), _wix_ev("crack-sabbath-16"),
    body='<a href="https://twitter.com/intent/tweet?url=https://www.seamonsterlounge.com'
         '/event-info/hot-rod-2026-10-14-19-30&amp;text=Check">Share</a>')

# freiheitshalle-munich.com — the events page renamed, so neither default.
WIX_RENAMED = _wix_page(
    _wix_ev("neon-kunst-fur-kids"), _wix_ev("herbstmarkt"),
    body='<a href="https://www.freiheitshalle-munich.com/events/neon-kunst-fur-kids">Neon</a>'
         '<a href="https://www.freiheitshalle-munich.com/events/herbstmarkt">Markt</a>')

# heartcentre.org.uk/what-s-on — every event sells through TicketSource, whose
# URLs reuse the slug under the centre's account name.
_TS = "https://www.ticketsource.com/heartcentreheadingley/tnl-the-fergus-quill-trio/2026-11-24/19:15/t-x"
WIX_TICKETER = _wix_page(
    _wix_ev("tnl-the-fergus-quill-trio", ext=_TS),
    body=f'<a href="{_TS}">Tickets</a>')

# A site in two languages, its events page renamed: the language prefix is
# part of the link the site wrote, and so of the route.
WIX_LANG = _wix_page(
    _wix_ev("atelier-yoga"),
    body='<a href="https://www.yoga-vision.org/fr/evenements/atelier-yoga">Atelier</a>')

# centrecultureltheux.wixsite.com/cctheux — a FREE Wix site, which lives under
# a path on Wix's host. 13 events, 12 upcoming (2026-10-04); its own links say
# /cctheux/event-details/<slug>, which answers 200 with an Event block, and
# /event-details/<slug> off the host root is a 404. The registration form is
# a page under the event's own.
_CCT = "https://centrecultureltheux.wixsite.com/cctheux"
WIX_FREE = _wix_page(
    _wix_ev("larmes"), _wix_ev("alma"), _wix_ev("poumons"),
    body=f'<a href="{_CCT}/event-details/larmes">Larmes</a>'
         f'<a href="{_CCT}/event-details/alma/form">S\'inscrire</a>'
         '<a href="/cctheux/event-details/poumons">Poumons</a>')

# A blog widget ships its posts as {"slug": ...} too, routed through /post/.
WIX_BLOG = _wix_page(
    _wix_ev("fondue-abend-8"),
    body='<a href="/post/our-summer">Post</a><a href="/post/our-summer">Post</a>'
         '<a href="/event-details/fondue-abend-8">Fondue</a>',
    extra={"posts": [{"slug": "our-summer", "title": "Our summer"}]})

# THE SHAPE 13 OF 15 CONFIGURED ENTRIES CAME FROM. studios27.fr's homepage
# features its programme and links straight to each event's own page, which
# ships that one event and nothing else.
WIX_HOME_FEATURED = _wix_page(
    _wix_ev("sortir-des-cliches"), _wix_ev("tournoi-eleves"), _wix_ev("stage-toussaint"),
    body='<a href="https://www.studios27.fr/event-details/sortir-des-cliches">Sortir</a>')
WIX_ONE_EVENT = _wix_page(_wix_ev("sortir-des-cliches"))

# teatrodelburatto.com — the link that says "programma" leads to a page with
# the app's id on it and no events at all; the homepage carries the season.
WIX_HOME_PROGRAMME = _wix_page(
    _wix_ev("bu-1-4"), _wix_ev("pinocchio"),
    body='<a href="/programma">Programma</a>')
WIX_NO_EVENTS = _WIX_HEAD + "<h1>Il giardino delle storie</h1></body></html>"
# ...and a Wix page with no events widget whose page carries its own Event
# block, which the adapter reads off the listing with no slug at all.
WIX_OWN_BLOCK = (_WIX_HEAD + '<script type="application/ld+json">{"@context":'
                 '"https://schema.org","@type":"Event","name":"Fiera","startDate":'
                 '"2026-10-20T10:00"}</script></body></html>')


# theroyalroomseattle.com — WP Event Manager, a calendar PLUGIN.
WPEM = '''<html><head><link href="/wp-content/plugins/wp-event-manager/assets/css/a.css"></head>
<body><div class="wpem-event-title">Trio Reunion</div></body></html>'''

# theblackaltar.org/events-calendar/ — My Calendar, which links no feed at all.
MYCAL = '''<html><head><link href="/wp-content/plugins/my-calendar/css/a.css"></head>
<body><div class="mc-navigation-button">Next</div></body></html>'''

# What that site's WAF answers with, on a 200.
CHALLENGE_200 = '''<html><head><title>One moment, please...</title></head>
<body><div id="text">Please wait while your request is being verified...</div></body></html>'''
CHALLENGE_SG = '''<html><head><meta http-equiv="refresh"
 content="0;/.well-known/sgcaptcha/?r=%2Frobots.txt"></meta></head></html>'''


def main():
    print("a site BUILDER must show its events app; a calendar PLUGIN need not")
    check("a real Squarespace events collection is a find",
          osm.fingerprint(SQSP_REAL)[0], ["squarespace"])
    check("a hand-written What's On on Squarespace is not",
          osm.fingerprint(SQSP_PROSE)[0], [])
    check("a Wix site with no Wix Events is not",
          osm.fingerprint(WIX_PROSE)[0], [])
    check("a Wix site running Wix Events is",
          osm.fingerprint(WIX_EVENTS)[0], ["wix"])
    check("WP Event Manager counts on its own — it IS an events system",
          osm.fingerprint(WPEM)[0], ["wp-event-manager"])
    check("so does My Calendar", osm.fingerprint(MYCAL)[0], ["my-calendar"])

    print()
    print("an offsite link is a routing signal, and the LINK is the signal")
    # `offsite:` parks a venue as dead for the ledger's 90-day TTL, so a link
    # that cannot be anybody's calendar must not trigger it. Found by recording
    # the link and reading 24 of them: Eventbrite's WordPress plugin puts a
    # credit in the FOOTER, which is on every page of every site using it.
    check("a plugin's footer credit is not a calendar",
          osm._offsite("https://eventbrite.com/l/wordpress?ref=wpfooter"), None)
    check("...nor is a bare brand link with no path",
          osm._offsite("https://www.eventbrite.co.uk/"), None)
    check("...nor the same without the trailing slash",
          osm._offsite("https://www.eventbrite.co.uk"), None)
    check("an organizer profile still is",
          osm._offsite("https://www.eventbrite.be/o/pilar-18004751158"), "eventbrite")
    check("...and so is a single event",
          osm._offsite("https://www.eventbrite.ie/e/angel-lane-tickets-2021"), "eventbrite")
    check("...and a DICE venue page, which is the one shape that routes",
          osm._offsite("https://dice.fm/venue/the-vera-project-wmmg"), "dice.fm")

    # The id is in the link and nowhere else, so the link is what has to survive
    # the probe — recording the host alone made 46 venues a statistic.
    _HTML = ('<html><body><a href="https://www.eventbrite.com/o/hall-105655500371">'
             'Events</a><a href="/about">About</a></body></html>')

    class _R:
        status_code, url, text = 200, "https://hall.example/", _HTML
        headers = {"Content-Type": "text/html"}

    class _S:
        def get(self, u, timeout=None, allow_redirects=True, **kw):
            return _R()

    _f = osm.find_calendar(_S(), "https://hall.example/")
    check("an offsite find reports its platform", _f["status"], "offsite:eventbrite")
    check("...and keeps the link the id lives in",
          _f["offsite_url"], "https://www.eventbrite.com/o/hall-105655500371")

    # A community centre's timetable on its city's booking platform. The link
    # says "Register" or "Drop-in schedule", which CAL_LINK_RX does not read, so
    # the host is the signal for these platforms and these only.
    _PM = "https://cityofsurrey.perfectmind.com/23615/Clients/BookMe4?widgetId=b4059e75-9755-401f-a7b5-d7c75361420d"

    def _page(html):
        class _R2:
            status_code, url, text = 200, "https://centre.example/", html
            headers = {"Content-Type": "text/html"}

        class _S2:
            def get(self, u, timeout=None, allow_redirects=True, **kw):
                return _R2()
        return osm.find_calendar(_S2(), "https://centre.example/")

    _f = _page(f'<html><body><a href="{_PM}">Drop-in schedule</a><a href="/about">About</a></body></html>')
    check("a booking-platform link reads as offsite even when its text is not 'events'",
          (_f["status"], _f["offsite_url"]), ("offsite:perfectmind.com", _PM))
    check("...and an ActiveNet portal names its platform too",
          _page('<a href="https://anc.apm.activecommunities.com/seattle/activity/search">Register</a>')["status"],
          "offsite:activecommunities.com")
    check("an ordinary link that says Register is still not a calendar",
          _page('<a href="https://shop.example/register">Register</a>')["status"], "no-calendar")
    check("ActiveNet's bare marketing home is a brand link, not a timetable",
          osm._offsite("https://www.activecommunities.com/"), None)

    print()
    print("a challenge is a refusal, whatever status code it wears")
    check_true("the 200-with-a-spinner is caught", osm.CHALLENGE_RX.search(CHALLENGE_200))
    check_true("SiteGround's 202 captcha is caught", osm.CHALLENGE_RX.search(CHALLENGE_SG))
    check("an ordinary events page is not mistaken for one",
          bool(osm.CHALLENGE_RX.search(SQSP_REAL)), False)

    print()
    print("the adapter a find is proposed to")
    check("My Calendar goes to ics, not to the page's own Event block",
          osm.adapter_for(["my-calendar", "jsonld-event"]), "ics")
    check("The Events Calendar outranks everything under it",
          osm.adapter_for(["tribe", "jsonld-event", "ics"]), "tribe")
    check("a bare Event block still goes to jsonld",
          osm.adapter_for(["jsonld-event"]), "jsonld")
    check("nothing detected proposes nothing", osm.adapter_for([]), None)

    print()
    print("the candidate is shaped for the config file it will land in")
    v = {"name": "Jamboree", "url": "https://jamboreevenue.co.uk", "kind": "music_venue",
         "lat": 51.5074, "lon": -0.1278, "street": "566 Cable St", "city": "London",
         "postal_code": "E1W 3HB", "country": "GB"}
    wix = osm.to_candidate(v, {"adapter": "jsonld", "labels": ["wix"],
                               "cal_url": "https://jamboreevenue.co.uk/events"})
    check("a Wix find with no route read takes the newer default, not /event/<slug>/",
          (wix["url_template"], wix["link_pattern"]),
          ("/event-details/{}", r'"slug":"([a-zA-Z0-9-]+)"'))
    check("...and a route read off the site is the one it ships with",
          osm.to_candidate(v, {"adapter": "jsonld", "labels": ["wix"],
                               "cal_url": "https://jamboreevenue.co.uk/events",
                               "extra": {"wix_route": "/event-info/{}"}})["url_template"],
          "/event-info/{}")
    check("a free wixsite.com find with no route read keeps the site's own path",
          osm.to_candidate(v, {"adapter": "jsonld", "labels": ["wix"],
                               "cal_url": _CCT})["url_template"],
          "/cctheux/event-details/{}")
    wp = osm.to_candidate(v, {"adapter": "jsonld", "labels": ["wp-event-manager"],
                              "cal_url": "https://jamboreevenue.co.uk/events/"})
    check_true("a WordPress find gets a /event/<slug>/ pattern",
               re.search(r"event", wp["link_pattern"]) and "url_template" not in wp)
    check("a music venue is proposed as music", wix["category"], "music")
    check("a church hall is proposed as community, not as worship",
          osm.to_candidate(dict(v, kind="place_of_worship"),
                           {"adapter": "tribe", "labels": ["tribe"],
                            "cal_url": "https://x.org/events/"})["category"], "community")

    print()
    print("a Wix site's event route is read off its own links, not assumed")
    check("an older site's share links say /event-info/",
          osm.wix_route(WIX_SEA_MONSTER, "https://www.seamonsterlounge.com/buy-tickets-in-advance"),
          "/event-info/{}")
    check("a renamed events page is read as readily as either default",
          osm.wix_route(WIX_RENAMED, "https://www.freiheitshalle-munich.com/"), "/events/{}")
    check("a ticketer that reuses the slug is not the site's route",
          osm.wix_route(WIX_TICKETER, "https://www.heartcentre.org.uk/what-s-on"), None)
    check("...which is what it would have read without the host test",
          osm.wix_route(WIX_TICKETER), "/heartcentreheadingley/{}")
    check("...nor the ticketer written without a scheme, in an event's blurb",
          osm.wix_route(_wix_page(_wix_ev("tnl-the-fergus-quill-trio"),
                                  body="<p>Book at ticketsource.com/heartcentreheadingley/"
                                       "tnl-the-fergus-quill-trio</p>"),
                        "https://www.heartcentre.org.uk/what-s-on"), None)
    check("a language prefix is kept: it is in the link the site wrote",
          osm.wix_route(WIX_LANG, "https://www.yoga-vision.org/"), "/fr/evenements/{}")
    check("a free Wix site's route keeps its path, absolute links and relative",
          osm.wix_route(WIX_FREE, _CCT), "/cctheux/event-details/{}")
    slug = "larmes"
    check("...so the adapter's urljoin lands on the page the site linked",
          urljoin(_CCT, osm.wix_route(WIX_FREE, _CCT).format(slug)),
          f"{_CCT}/event-details/{slug}")
    check("a registration form under the event's page still reads the route",
          osm.wix_route(_wix_page(_wix_ev("alma"),
                                  body='<a href="/event-details/alma/form">Go</a>'),
                        "https://x.org/"), "/event-details/{}")
    check("the default under a free site's path, and only there",
          (osm.wix_default_route(_CCT + "/"), osm.wix_default_route(_CCT + "/agenda"),
           osm.wix_default_route("https://www.studios27.fr/agenda")),
          ("/cctheux/event-details/{}", "/cctheux/event-details/{}", "/event-details/{}"))
    check("a blog post's slug is not an event's, however often it is linked",
          osm.wix_route(WIX_BLOG, "https://www.brausyndikat.ch/"), "/event-details/{}")
    check("the events are read out of the warmup data, posts left behind",
          sorted(e["slug"] for e in osm.wix_events(WIX_BLOG)), ["fondue-abend-8"])
    check("a page with no events and no route says nothing",
          osm.wix_route(WIX_NO_EVENTS, "https://x.org/"), None)

    def _site(pages):
        class _R3:
            def __init__(self, u):
                self.status_code, self.url, self.text = (200, u, pages[u]) if u in pages else (404, u, "")
                self.headers = {"Content-Type": "text/html"}

        class _S3:
            asked = []

            def get(self, u, timeout=None, allow_redirects=True, **kw):
                self.asked.append((u, timeout))
                return _R3(u)
        sess = _S3()
        return sess, osm.find_calendar(sess, next(iter(pages)))

    sess, f = _site({"https://www.studios27.fr/": WIX_HOME_FEATURED,
                     "https://www.studios27.fr/event-details/sortir-des-cliches": WIX_ONE_EVENT})
    check("a walk that lands on ONE event's page proposes the homepage that listed it",
          (f["status"], f["cal_url"], f["extra"].get("wix_route")),
          ("ok", "https://www.studios27.fr/", "/event-details/{}"))
    check("...and spends no request asking /event-details/ to be an index",
          [u for u, _ in sess.asked],
          ["https://www.studios27.fr/", "https://www.studios27.fr/event-details/sortir-des-cliches"])
    check("a probe's connect timeout is its own, shorter than the read",
          sess.asked[0][1], (osm.PROBE_CONNECT_S, 18))
    _, f = _site({"https://www.teatrodelburatto.com/": WIX_HOME_PROGRAMME,
                  "https://www.teatrodelburatto.com/programma": WIX_NO_EVENTS})
    check("a Wix page with no events loses to a homepage that has them",
          f["cal_url"], "https://www.teatrodelburatto.com/")
    _, f = _site({"https://www.teatrodelburatto.com/": WIX_HOME_PROGRAMME,
                  "https://www.teatrodelburatto.com/programma": WIX_OWN_BLOCK})
    check("...unless it carries an Event block of its own, which is read as it is",
          f["cal_url"], "https://www.teatrodelburatto.com/programma")
    sess, f = _site({_CCT: _wix_page(_wix_ev("larmes"), _wix_ev("alma"),
                                     body=f'<a href="{_CCT}/event-details/larmes">Agenda</a>'),
                     f"{_CCT}/event-details/larmes": _wix_page(_wix_ev("larmes"))})
    c = osm.to_candidate({"name": "Centre Culturel Theux", "kind": "arts_centre"}, f)
    check("a free Wix site end to end: its page as the listing, its path in the route",
          (c["listing"], c["url_template"]), ([_CCT], "/cctheux/event-details/{}"))
    _, f = _site({"https://www.seamonsterlounge.com/":
                  _WIX_HEAD + '<a href="/buy-tickets-in-advance">Upcoming events</a></body></html>',
                  "https://www.seamonsterlounge.com/buy-tickets-in-advance": WIX_SEA_MONSTER})
    check("a real Wix listing stays the listing, with the route it showed",
          (f["cal_url"], f["extra"].get("wix_route")),
          ("https://www.seamonsterlounge.com/buy-tickets-in-advance", "/event-info/{}"))
    c = osm.to_candidate({"name": "Sea Monster Lounge", "kind": "music_venue"}, f)
    check("...and the candidate carries it", c["url_template"], "/event-info/{}")

    print()
    print("the venue block comes from the survey — it is what places the events")
    check("the surveyed point is carried through",
          (wix["venue"]["lat"], wix["venue"]["lon"]), (51.5074, -0.1278))
    check("so is the address OSM had", wix["venue"]["address"], "566 Cable St")
    check("a venue with no address still ships its coordinates",
          osm.to_candidate({"name": "X", "url": "https://x.org", "kind": "theatre",
                            "lat": 1.5, "lon": 2.5},
                           {"adapter": "tribe", "labels": ["tribe"],
                            "cal_url": "https://x.org/events/"})["venue"],
          {"name": "X", "lat": 1.5, "lon": 2.5})
    check("an ics find with no feed found is not proposed at all",
          osm.to_candidate(v, {"adapter": "ics", "labels": ["my-calendar"],
                               "cal_url": "https://x.org/events/", "ics": None}), None)

    print()
    print("an unanswered Overpass call is an UNREAD metro, not an empty one")

    class _Resp:
        def __init__(self, code=200, payload=None):
            self.status_code, self._p, self.headers = code, payload or {}, {}
        def json(self):
            return self._p
        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(f"http {self.status_code}")

    class _Dead:
        def post(self, *a, **k):
            raise ConnectionError("Connection aborted")

    class _Empty:
        def post(self, *a, **k):
            return _Resp(200, {"elements": []})

    class _One:
        def post(self, *a, **k):
            return _Resp(200, {"elements": [
                {"lat": 1.0, "lon": 2.0,
                 "tags": {"name": "Hall", "amenity": "theatre",
                          "website": "https://hall.example"}}]})

    osm.OVERPASS_BACKOFF_S = 0          # exercise the retry, not the waiting
    check("a refused endpoint returns None — nobody asked this bbox",
          osm.overpass_venues(_Dead(), "0,0,1,1", "X", quiet=True), None)
    check("an answered-but-empty bbox returns [] — asked, nothing there",
          osm.overpass_venues(_Empty(), "0,0,1,1", "X", quiet=True), [])
    got = osm.overpass_venues(_One(), "0,0,1,1", "X", quiet=True)
    check("a venue with a website comes back with its surveyed point",
          (got[0]["name"], got[0]["url"], got[0]["lat"]),
          ("Hall", "https://hall.example", 1.0))

    # 134 asks over 13 runs: each failed about half the time, and the 6 that
    # failed four times running each ended their run. Busy is worth waiting out.
    class _Busy:
        def __init__(self, busy):
            self.busy, self.posts = busy, 0

        def post(self, *a, **k):
            self.posts += 1
            if self.posts <= self.busy:
                return _Resp(504)
            return _One().post()

    b = _Busy(osm.OVERPASS_ATTEMPTS - 1)
    check("a busy endpoint answering on the last ask is a metro READ",
          (len(osm.overpass_venues(b, "0,0,1,1", "X", quiet=True) or []), b.posts),
          (1, osm.OVERPASS_ATTEMPTS))
    b = _Busy(99)
    check("...and one busy for every ask is still UNREAD, after a bounded number",
          (osm.overpass_venues(b, "0,0,1,1", "X", quiet=True), b.posts),
          (None, osm.OVERPASS_ATTEMPTS))
    check_true("more than the four asks that ended 6 of 13 runs",
               osm.OVERPASS_ATTEMPTS > 4)

    # An ask that HANGS for its 200 s read timeout, on a clock that is not the
    # wall's: eight of them would be 32 minutes on one metro, outside the
    # caller's deadline. The patience bounds it below what four asks cost.
    class _Clock:
        now = 0.0

        def monotonic(self):
            return self.now

        def sleep(self, s):
            self.now += s

    class _Hang:
        def __init__(self, clock):
            self.clock, self.posts = clock, 0

        def post(self, *a, **k):
            self.posts += 1
            self.clock.now += 200
            raise TimeoutError("read timed out")

    real_time, real_backoff = osm.time, osm.OVERPASS_BACKOFF_S
    clock = _Clock()
    osm.time, osm.OVERPASS_BACKOFF_S = clock, 5
    try:
        h = _Hang(clock)
        got = osm.overpass_venues(h, "0,0,1,1", "X", quiet=True)
        check("an endpoint that hangs is UNREAD inside the patience, not after eight hangs",
              (got, h.posts, clock.now <= osm.OVERPASS_PATIENCE_S + 200),
              (None, 3, True))
        check_true("...which is under the 865 s that four hangs and their waits cost",
                   clock.now < 4 * 200 + 5 + 15 + 45)

        class _Quick(_Busy):
            def post(self, *a, **k):
                clock.now += 9
                return _Busy.post(self, *a, **k)

        clock.now = 0.0
        b = _Quick(osm.OVERPASS_ATTEMPTS - 1)
        got = osm.overpass_venues(b, "0,0,1,1", "X", quiet=True)
        check("...while quick 504s (9 s each, the measured shape) still get all eight asks",
              (len(got or []), b.posts), (1, osm.OVERPASS_ATTEMPTS))
    finally:
        osm.time, osm.OVERPASS_BACKOFF_S = real_time, real_backoff

    print()
    print("a walk can be PINNED to named kinds, and the pin is read off OSM_SELECTORS")
    keys = osm.kind_keys()
    check("every selector kind resolves to its key",
          (keys.get("library"), keys.get("museum"), keys.get("club"), keys.get("books")),
          ("amenity", "tourism", "club", "shop"))
    check("a pinned query asks only for the kinds named",
          osm._overpass_query("0,0,1,1", ["library", "community_centre"]),
          '[out:json][timeout:180];(nwr["amenity"~"^(community_centre|library)$"](0,0,1,1););out tags center;')
    check("an unpinned query is the whole union, unchanged",
          osm._overpass_query("0,0,1,1").count("nwr["), len(osm.OSM_SELECTORS))
    try:
        osm._overpass_query("0,0,1,1", ["pub"])
        check_true("an unknown kind is refused out loud, not swept as nothing", False)
    except ValueError:
        check_true("an unknown kind is refused out loud, not swept as nothing", True)

    print()
    print("a probe that succeeds and yields nothing must SAY what it yielded nothing for")
    check("an unshapeable adapter is named, not counted as a success",
          osm.why_no_candidate({"adapter": "mylisting", "labels": ["mylisting"], "cal_url": "u"}),
          "no-config-shape(mylisting)")
    check("an ics find with no feed is named",
          osm.why_no_candidate({"adapter": "ics", "labels": ["trumba"], "cal_url": "u", "ics": None}),
          "ics-without-feed(trumba)")
    check("a detected platform with no adapter is named",
          osm.why_no_candidate({"adapter": None, "labels": ["wix"], "cal_url": "u"}),
          "no-adapter(wix)")
    check("venuepilot without ids says so — the ids are the whole config",
          osm.why_no_candidate({"adapter": "venuepilot", "labels": ["venuepilot"],
                                "cal_url": "u", "extra": {}}),
          "venuepilot-without-accountIds")
    check("every adapter adapter_for can NAME, to_candidate can now SHAPE",
          sorted({a for _, _, a in osm.PLATFORM_SIGNS} | {"jsonld", "ics"}) ,
          sorted(osm.SHAPEABLE | {"mylisting"}))

    print()
    print("gancio and venuepilot are ordinary config entries now")
    gv = {"name": "Klub", "url": "https://k.org", "kind": "nightclub", "lat": 52.4, "lon": 4.9,
          "city": "Amsterdam", "country": "NL"}
    g = osm.to_candidate(gv, {"adapter": "gancio", "labels": ["gancio"],
                              "cal_url": "https://k.org/events"}, "Amsterdam, NL")
    check("gancio is keyed on its origin", g["base_url"], "https://k.org")
    check("and carries the metro as a default city", g["default_city"], "Amsterdam")
    vp = osm.to_candidate(gv, {"adapter": "venuepilot", "labels": ["venuepilot"],
                               "cal_url": "https://k.org/shows",
                               "extra": {"account_ids": [2906, 11]}}, "Portland, US")
    check("venuepilot carries the ids lifted off the page", vp["account_ids"], [2906, 11])
    check("no ids, no candidate — the API has nothing to be asked",
          osm.to_candidate(gv, {"adapter": "venuepilot", "labels": ["venuepilot"],
                                "cal_url": "u", "extra": {}}, "x"), None)

    print()
    print("webcal:// is https:// wearing a hat")
    check("a webcal feed is normalised", osm._https("webcal://x.org/cal.ics"),
          "https://x.org/cal.ics")
    check("case does not matter", osm._https("WEBCAL://x.org/c.ics"), "https://x.org/c.ics")
    check("https is left alone", osm._https("https://x.org/c.ics"), "https://x.org/c.ics")
    check("None stays None", osm._https(None), None)
    wc = osm.to_candidate({"name": "St Declan's", "url": "https://x.org", "kind": "place_of_worship",
                           "lat": -33.9, "lon": 151.1},
                          {"adapter": "ics", "labels": ["ics"], "cal_url": "https://x.org/events",
                           "ics": "webcal://x.org/events/?ical=1"}, "Sydney, AU")
    check_true("and the candidate never carries the webcal scheme",
               wc["url"].startswith("https://"))

    print()
    print("the metro walk is global, and international first")
    ms = osm.metros()
    check_true("there are metros to walk", len(ms) > 200)
    check_true("it does not start in the US — that is the covered half",
               ms[0]["country"] != "US")
    check_true("every metro has a usable bbox",
               all(len(m["bbox"].split(",")) == 4 for m in ms))
    check_true("the US is in there too", any(m["country"] == "US" for m in ms))
    south = osm._bbox_from("-33.8688,151.2093", 25.0)
    check_true("a southern-hemisphere bbox is ordered s,w,n,e",
               float(south.split(",")[0]) < float(south.split(",")[2]))

    # ------------------------------------------------------------------
    # THE WALL-CLOCK BUDGET, AND WHERE IT HAS TO BE CHECKED.
    #
    # This sweep is the whole of `curate-catalog`'s runtime: measured
    # 2026-08-26, socrata took 6 seconds, ckan 101, mobilizon 3, and osm the
    # remaining 88 — into the job's 90-minute timeout-minutes, which CANCELS
    # the step and skips every step after it.
    #
    # The first budget was checked only at the top of the METRO loop and never
    # fired once: that run printed nothing at all from this backend before it
    # was killed. One metro is an Overpass call plus a LIVE FETCH PER VENUE,
    # and a dense metro is hundreds of them, so a single iteration of the loop
    # you can see outlasts the whole budget. Bounding the loop is not the same
    # as bounding the work.
    #
    # The other half is the cursor: a metro abandoned part-way through is
    # UNREAD, exactly as one Overpass never answered for is, and the cursor
    # must stay before it. Re-probing costs little because the ledger already
    # holds every dead end this pass found.
    import catalog_curate as C
    import hashlib as _hl0, os as _os0
    _ledger_before = (_hl0.sha256(open(C.LEDGER_FILE, "rb").read()).hexdigest()
                      if _os0.path.exists(C.LEDGER_FILE) else None)
    probed, now = [], [1000.0]
    # _save_ledger IS STUBBED, AND THAT IS NOT OPTIONAL. `_discover_osm` writes
    # curation_ledger.json as a side effect at the end of every call, with
    # whatever dict it was handed — so calling it from a test with `{}` REPLACES
    # the repo's 5,861-row ledger with an empty one, and a `git add -A` then
    # commits that. Which is exactly what happened on 2026-08-26 (recovered from
    # 29fe5de). A test that drives real machinery has to intercept every write
    # that machinery does, not only the ones it is asserting on.
    _ledger_writes = []
    real = (C.time.time, osm.overpass_venues, osm.find_calendar, osm.metros,
            C._save_ledger)
    C._save_ledger = lambda led: _ledger_writes.append(len(led))
    C.time.time = lambda: now[0]
    osm.metros = lambda: [{"name": f"M{i}", "country": "GB", "bbox": (0, 0, 1, 1)}
                          for i in range(3)]
    osm.overpass_venues = lambda sess, bbox, lab: [
        {"url": f"https://{lab}-{j}.example", "name": f"v{j}"} for j in range(50)]
    def _fc(sess, url):                      # the live fetch, so also the clock
        probed.append(url); now[0] += 1.0
        return {"status": "no-calendar"}
    osm.find_calendar = _fc
    try:
        cur = {"metro": 0}
        # ONE worker: the exact count below is the sequential contract. The
        # pool's own bound (at most workers-1 probes past it) is pinned in
        # "probing several venues at once", further down.
        C._discover_osm(C._session(), set(), {}, 500, cur, metros_per_run=3,
                        deadline=1000.0 + 20, workers=1)   # 20 venues of budget
        check_true("the budget stops the sweep INSIDE a metro, not only between "
                   f"metros ({len(probed)} venues probed of 150)",
                   20 <= len(probed) <= 21)
        check("...and a part-read metro leaves the cursor before it",
              cur["metro"], 0)
        probed.clear(); now[0] = 1000.0
        cur2 = {"metro": 0}
        C._discover_osm(C._session(), set(), {}, 500, cur2, metros_per_run=3,
                        deadline=1000.0 + 10_000)
        check("with budget to spare it reads every metro", len(probed), 150)
        check("...and the cursor wraps cleanly", cur2["metro"], 0)
    finally:
        (C.time.time, osm.overpass_venues, osm.find_calendar, osm.metros,
         C._save_ledger) = real
    check_true("the sweep wrote its ledger (to the stub, not to the repo)",
               len(_ledger_writes) == 2)

    # THE GUARD ITSELF. If a future edit drops that stub, this is what says so
    # before the commit rather than after the push.
    import hashlib as _hl, os as _os
    _lp = C.LEDGER_FILE
    check_true("...and the real curation_ledger.json is untouched on disk",
               (not _os.path.exists(_lp)) or _ledger_before == _hl.sha256(
                   open(_lp, "rb").read()).hexdigest())

    # ------------------------------------------------------------------
    # THE SWEEP ORDER, AND THE CURSOR THAT HAS TO SURVIVE AN EDIT TO IT.
    # ------------------------------------------------------------------
    # metros() promises the budget goes "where the catalog is thinnest" and for
    # its whole life delivered very nearly the opposite: metros_global.json is
    # in the order the countries were ADDED, which starts GB (48 sources), CA
    # (36), AU (46), FR (33). Measured from the live cursor at three metros a
    # run, Brazil — the one country with a purpose-built adapter — was 39 days
    # out, immediately before 80 US metros took the next 27.
    real_sources = dict(osm.CATALOG_SOURCES)
    try:
        osm.CATALOG_SOURCES = {"US": 576, "GB": 48, "BR": 14, "HK": 1}
        ms = osm.metros(path_global="does-not-exist.json", path_us="nope.txt")
        check("no config, no metros", ms, [])
    finally:
        osm.CATALOG_SOURCES = real_sources

    ms = osm.metros()
    order = [m["country"] for m in ms]
    def _first(cc):
        return order.index(cc) if cc in order else 10**6
    check_true("the thinnest catalog is swept FIRST, not the richest "
               f"({order[0]} before {order[-1]})",
               _first("HK") < _first("GB") < _first("US"))
    check_true("...and Brazil, which has its own adapter, comes before the US",
               _first("BR") < _first("US"))
    check_true("...and the US is last, on the same rule and not a special case",
               order[-1] == "US")
    # A country nobody has measured has no sources, so it sorts first — the same
    # rule said the other way round. Without this a metro added for a country the
    # catalog has never reached would sweep in a year rather than next.
    unknown = [m for m in ms if m["country"] not in osm.CATALOG_SOURCES]
    check_true("an unmeasured country is treated as empty, so it sweeps first",
               not unknown or order.index(unknown[0]["country"]) == 0)
    gb = [m["name"] for m in ms if m["country"] == "GB"]
    check("a tie inside one country keeps the config's own order", gb[0], "London")

    # THE CURSOR NAMES A METRO; IT DOES NOT COUNT TO ONE. This is the live bug:
    # the cursor said 49 — Paris — on a ledger already holding 1,041 probes of
    # .fr hosts, because metros_global.json was broadened underneath it and an
    # insertion re-aimed a POSITION at a country already read.
    real = (osm.overpass_venues, osm.find_calendar, osm.metros, C._save_ledger)
    C._save_ledger = lambda led: None
    osm.overpass_venues = lambda sess, bbox, lab: []
    osm.find_calendar = lambda sess, url: {"status": "no-calendar"}
    try:
        before = [{"name": n, "country": "GB", "bbox": (0, 0, 1, 1)}
                  for n in ("Alpha", "Beta", "Gamma")]
        osm.metros = lambda: before
        cur = {}
        C._discover_osm(C._session(), set(), {}, 500, cur, metros_per_run=1)
        check("one metro read leaves the cursor NAMING the next", 
              cur["metro_key"], "GB:Beta")
        # Somebody adds a metro at the top. A position would now mean Alpha.
        after = [{"name": "Inserted", "country": "GB", "bbox": (0, 0, 1, 1)}] + before
        osm.metros = lambda: after
        C._discover_osm(C._session(), set(), {}, 500, cur, metros_per_run=1)
        check("...and after the list is edited it resumes where it SAID, "
              "not where it counted", cur["metro_key"], "GB:Gamma")
        # An unknown name is a fresh start at the top, which under the order
        # above is the country the catalog has least of.
        cur2 = {"metro_key": "GB:Deleted"}
        C._discover_osm(C._session(), set(), {}, 500, cur2, metros_per_run=1)
        check("a cursor naming a metro that is gone restarts at the thinnest",
              cur2["metro_key"], "GB:Alpha")
    finally:
        (osm.overpass_venues, osm.find_calendar, osm.metros,
         C._save_ledger) = real

    # ------------------------------------------------------------------
    # PROBING SEVERAL VENUES AT ONCE, AND WHAT MUST NOT CHANGE BECAUSE OF IT.
    #
    # The community walk is time-bound: 950-1,450 live probes per 55-minute
    # run at 2.3-3.5 s each, almost all of it waiting on other people's
    # servers, so it read 3-7 metros a run and the US (DC, Seattle) sat two to
    # three weeks out. Four at once is only polite if no host ever sees two of
    # them: two OSM venues CAN share a site (a city site hosting several
    # centres). And every write stays on the main thread.
    # ------------------------------------------------------------------
    print("\nprobing several venues at once")
    import threading as _th
    import time as _tm

    class _MainOnlyLedger(dict):
        """Records which thread wrote each row."""
        def __init__(self, *a):
            super().__init__(*a)
            self.writers = set()

        def __setitem__(self, k, v):
            self.writers.add(_th.current_thread() is _th.main_thread())
            super().__setitem__(k, v)

    real = (osm.overpass_venues, osm.find_calendar, osm.metros, C._save_ledger)
    C._save_ledger = lambda led: None
    osm.metros = lambda: [{"name": "Solo", "country": "GB", "bbox": (0, 0, 1, 1)}]
    try:
        # 1. It actually runs four at once, and that is what buys the time.
        lock, now_in, peak, per_host, host_peak = _th.Lock(), [0], [0], {}, [0]
        order = []
        venues = ([{"url": f"https://site{j}.example/", "name": f"s{j}"} for j in range(12)]
                  + [{"url": f"https://www.city.example/centre-{j}", "name": f"c{j}"}
                     for j in range(6)])
        osm.overpass_venues = lambda sess, bbox, lab: list(venues)

        asked_urls = []

        def _slow(sess, url):
            h = C._probe_host(url)
            with lock:
                order.append(h)
                asked_urls.append(url)
                now_in[0] += 1
                peak[0] = max(peak[0], now_in[0])
                per_host[h] = per_host.get(h, 0) + 1
                host_peak[0] = max(host_peak[0], per_host[h])
            _tm.sleep(0.1)
            with lock:
                now_in[0] -= 1
                per_host[h] -= 1
            return {"status": "no-calendar"}
        osm.find_calendar = _slow
        led = _MainOnlyLedger()
        t0 = _tm.monotonic()
        found, sk = C._discover_osm(C._session(), set(), led, 500, {}, metros_per_run=1,
                                    workers=4)
        took = _tm.monotonic() - t0
        check("four workers have four probes in flight at once", peak[0], 4)
        check("...but never two on one host: six centres on one city site go "
              "one after another", host_peak[0], 1)
        check("...and every venue is still probed exactly once",
              (len(asked_urls), len(set(asked_urls)), len(led)), (18, 18, 18))
        check_true(f"...in well under the sequential time ({took:.2f}s for 18 probes "
                   "of 0.1 s; 1.8 s one at a time, 0.6 s the floor)", took < 1.2)
        check("the longest same-host queue starts first, though the map lists it last",
              order[0], "city.example")
        check("every ledger write happened on the main thread", led.writers, {True})
        check("www. is not a second site", C._probe_host("https://WWW.City.example/x"),
              "city.example")

        # 2. The deadline still binds: no probe STARTS after it, and the ones
        # in flight when it passes are recorded, not lost.
        clock, started = [1000.0], []

        def _tick(sess, url):
            with lock:
                started.append(url)
                clock[0] += 1.0
            _tm.sleep(0.01)
            return {"status": "no-calendar"}
        osm.find_calendar = _tick
        osm.overpass_venues = lambda sess, bbox, lab: [
            {"url": f"https://d{j}.example", "name": f"d{j}"} for j in range(50)]
        real_time = C.time.time
        C.time.time = lambda: clock[0]
        try:
            led2, cur = {}, {}
            C._discover_osm(C._session(), set(), led2, 500, cur, metros_per_run=1,
                            deadline=1000.0 + 20, workers=4)
        finally:
            C.time.time = real_time
        check_true(f"with four workers the deadline overshoots by at most three "
                   f"probes in flight ({len(started)} started, budget 20)",
                   20 <= len(started) <= 23)
        check("...every probe that did run is in the ledger", len(led2), len(started))
        check("...and the cut-short metro stays UNREAD", cur.get("metro_key"), "GB:Solo")

        # 3. An unreachable venue is parked a WEEK, under its own status,
        # and the tally says which timeout it was.
        notes = {"https://a.example": "ConnectTimeout", "https://b.example": "ReadTimeout",
                 "https://c.example": None}
        asked = []

        def _unreach(sess, url):
            asked.append(url)
            n = notes[url]
            return {"status": "unreachable", **({"note": n} if n else {})}
        osm.find_calendar = _unreach
        osm.overpass_venues = lambda sess, bbox, lab: [
            {"url": u, "name": u[8:9]} for u in notes]
        led3 = {"c.example": {"checked": 20260901, "status": "ok", "type": "jsonld",
                              "reason": "12 events / 5 future"}}
        _f, sk3 = C._discover_osm(C._session(), set(), led3, 500, {}, metros_per_run=1)
        check("an unreachable venue is parked under its own status",
              (led3.get("a.example") or {}).get("status"), "unreachable")
        check("...as an osm-venue row, dated today",
              ((led3.get("a.example") or {}).get("type"),
               (led3.get("a.example") or {}).get("checked")), ("osm-venue", C._today_int()))
        check("...which says which exception it was",
              (led3.get("b.example") or {}).get("reason"), "osm probe: unreachable (ReadTimeout)")
        check("...but never over a row a verify wrote for the same URL",
              led3["c.example"]["status"], "ok")
        check("the tally splits unreachable by exception",
              {k: v for k, v in sk3.items() if k.startswith("unreachable")},
              {"unreachable:ConnectTimeout": 1, "unreachable:ReadTimeout": 1,
               "unreachable": 1})
        check("...and _dead_recently does not read it, so verify is unchanged",
              C._dead_recently(led3, "a.example"), False)
        asked.clear()
        _f, sk4 = C._discover_osm(C._session(), set(), led3, 500, {}, metros_per_run=1)
        check("the next day's walk does not re-pay a parked unreachable venue",
              sorted(asked), ["https://c.example"])
        check("...and says so in the tally", sk4.get("unreachable-recently"), 2)
        led3["a.example"]["checked"] = int(
            (C._as_date(C._today_int()) - __import__("datetime").timedelta(
                days=C.UNREACHABLE_TTL_DAYS)).strftime("%Y%m%d"))
        asked.clear()
        C._discover_osm(C._session(), set(), led3, 500, {}, metros_per_run=1)
        check("...and asks it again once the week is up",
              sorted(asked), ["https://a.example", "https://c.example"])

        # 4. One site on two map objects is probed once; a probe that raises
        # costs that venue, not the walk.
        asked.clear()

        def _boom(sess, url):
            asked.append(url)
            if "bad" in url:
                raise ValueError("unparseable page")
            return {"status": "no-calendar"}
        osm.find_calendar = _boom
        osm.overpass_venues = lambda sess, bbox, lab: [
            {"url": "https://twice.example/", "name": "hall"},
            {"url": "http://www.twice.example", "name": "hall again"},
            {"url": "https://bad.example", "name": "bad"},
            {"url": "https://fine.example", "name": "fine"}]
        led5 = {}
        _f, sk5 = C._discover_osm(C._session(), set(), led5, 500, {}, metros_per_run=1)
        check("one site on two map objects is probed once",
              sorted(asked), ["https://bad.example", "https://fine.example",
                              "https://twice.example/"])
        check("...and the second is tallied as the same site", sk5.get("same-site"), 1)
        check("a probe that raises is tallied, and the walk goes on",
              (sk5.get("probe-error:ValueError"), "fine.example" in led5), (1, True))
        check("...without parking the venue it could not read", "bad.example" in led5, False)
    finally:
        (osm.overpass_venues, osm.find_calendar, osm.metros, C._save_ledger) = real

    # 5. THE GATE, which is what makes "one host, one request a second" true
    # for every request a probe makes, follow-ups and redirect hops included.
    t = [0.0]
    slept = []

    def _sleep(s):
        slept.append(s)
        t[0] += s
    g = C._HostGate(1.0, clock=lambda: t[0], sleep=_sleep)
    for u in ("https://x.example/", "https://www.x.example/events", "http://x.example/cal",
              "https://y.example/"):
        g.wait(u)
    check("three requests to one host start a second apart; another host waits for nothing",
          slept, [1.0, 1.0])
    g.slow("https://x.example/robots.txt", 2.5)
    slept.clear()
    g.wait("https://x.example/next")
    check("...and a robots.txt Crawl-delay longer than the gap widens it for that host",
          slept, [2.5])
    import requests as _rq
    from requests.adapters import BaseAdapter as _BA

    class _Hop(_BA):
        def send(self, request, **kw):
            r = _rq.models.Response()
            r.request, r.url, r.encoding = request, request.url, "utf-8"
            if "old.example" in request.url:
                r.status_code, r.headers["Location"] = 301, "https://new.example/home"
            else:
                r.status_code = 200
            r._content = b"ok"
            return r

        def close(self):
            pass

    class _Spy:
        def __init__(self):
            self.seen = []

        def wait(self, url):
            self.seen.append(C._probe_host(url))
    ps = C._PacedSession()
    ps.mount("https://", _Hop())
    ps.gate = _Spy()
    ps.get("https://old.example/")
    check("a redirect hop is paced on the host it lands on", ps.gate.seen,
          ["old.example", "new.example"])


    # 6. A LATE WAKE DOES NOT SHORTEN THE NEXT GAP. Paced on the last request
    # actually released, so a worker that oversleeps pushes the one after it
    # back; with reserved slots the next on-time slot followed it 0.974-0.999 s
    # apart on a 1.0 s gap (measured live).
    import threading as _th6
    import time as _tm6
    first = [True]
    flock = _th6.Lock()

    def _oversleep(s):
        with flock:
            late, first[0] = first[0], False
        _tm6.sleep(s + (0.08 if late else 0.0))
    g6 = C._HostGate(0.1, clock=_tm6.monotonic, sleep=_oversleep)
    rel, rlock = [], _th6.Lock()

    def _go():
        g6.wait("https://one.example/")
        with rlock:
            rel.append(_tm6.monotonic())
    ths = [_th6.Thread(target=_go) for _ in range(4)]
    for th in ths:
        th.start()
    for th in ths:
        th.join()
    rel.sort()
    gaps = [b - a for a, b in zip(rel, rel[1:])]
    check_true(f"a worker that wakes late pushes the next one back (smallest gap "
               f"{min(gaps):.3f} s on a 0.1 s gate)", min(gaps) >= 0.095)

    # 7. ROBOTS.TXT BINDS EVERY REQUEST THE WALK MAKES, redirect hops included,
    # and the walk reads each page once. Live 2026-10-04 (review of the pool):
    # 10 of Halifax's 11 library-branch probes fetched /explore/?post-type=...,
    # which halifaxpubliclibraries.ca disallows, and one hop landed on
    # facebook.com, which disallows everything; 21 of the metro's 70 requests
    # repeated a URL already fetched that run.
    print("\nrobots.txt and the run's cache")
    from requests.adapters import BaseAdapter as _BA7
    import requests as _rq7
    wire = []
    robots_files = {
        "refused.example": "User-agent: *\nDisallow: /\n",
        "hop.example": "User-agent: *\nAllow: /\n",
        "walled.example": "User-agent: *\nDisallow: /page\n",
        "lib.example": "User-agent: *\nDisallow: /explore/\n",
        "cf.example": "<html><title>Just a moment...</title></html>",
        "slow.example": "User-agent: *\nCrawl-delay: 120\n",
    }
    branch = (b'<html><a href="/explore/?post-type=events">Events</a>'
              b'<a href="/whats-on/">What\'s on</a></html>')

    class _Web(_BA7):
        def send(self, request, **kw):
            u = request.url
            host = C._probe_host(u)
            wire.append(u)
            if host == "down.example":
                raise _rq7.exceptions.ConnectionError("refused")
            r = _rq7.models.Response()
            r.request, r.url, r.encoding, r.status_code = request, u, "utf-8", 200
            if u.endswith("/robots.txt"):
                r._content = robots_files.get(host, "").encode()
                if host not in robots_files:
                    r.status_code = 404
            elif host == "hop.example":
                r.status_code, r.headers["Location"] = 301, "https://walled.example/page"
                r._content = b""
            elif host == "lib.example" and "/branch-" in u:
                r._content = branch
            else:
                r._content = b"<html>nothing here</html>"
            return r

        def close(self):
            pass

    class _WebSession(C._PacedSession):
        def __init__(self):
            super().__init__()
            self.mount("https://", _Web())

    real7 = (osm.overpass_venues, osm.metros, C._save_ledger, C._PacedSession,
             C.OSM_HOST_GAP_S)
    C._save_ledger = lambda led: None
    C._PacedSession = _WebSession
    C.OSM_HOST_GAP_S = 0.01
    osm.metros = lambda: [{"name": "Web", "country": "GB", "bbox": (0, 0, 1, 1)}]
    osm.overpass_venues = lambda sess, bbox, lab: [
        {"url": u, "name": u.split("/")[2]} for u in (
            "https://refused.example/", "https://hop.example/",
            "https://lib.example/branch-1", "https://lib.example/branch-2",
            "https://down.example/", "https://cf.example/", "https://slow.example/",
            "https://open.example/")]
    try:
        led7, cur7 = {}, {}
        _f7, sk7 = C._discover_osm(C._session(), set(), led7, 500, cur7,
                                   metros_per_run=1, workers=4)
    finally:
        (osm.overpass_venues, osm.metros, C._save_ledger, C._PacedSession,
         C.OSM_HOST_GAP_S) = real7
    sent = [u for u in wire if not u.endswith("/robots.txt")]
    check("a homepage robots.txt disallows is never fetched",
          [u for u in sent if "refused.example" in u], [])
    check("...and is parked `refused`, which _dead_recently reads for DEAD_TTL",
          ((led7.get("refused.example") or {}).get("status"),
           C._dead_recently(led7, "refused.example")), ("refused", True))
    check("...with the rule that refused it",
          "Disallow: /" in (led7.get("refused.example") or {}).get("reason", ""), True)
    check("a redirect to a page its own host's robots.txt disallows is not followed",
          ([u for u in sent if "walled.example" in u],
           (led7.get("hop.example") or {}).get("status")), ([], "refused"))
    check("...though the redirect's target host was asked for its robots.txt",
          "https://walled.example/robots.txt" in wire, True)
    check("a disallowed follow-up page is skipped, never sent",
          [u for u in sent if "/explore/" in u], [])
    check("...and the venue is still read for what it was allowed to see",
          (led7.get("lib.example/branch-1") or {}).get("status"), "fail")
    check("two branches linking one page fetch it once; the second is the cache's",
          sent.count("https://lib.example/whats-on/"), 1)
    check("each origin's robots.txt is read once for the whole run",
          wire.count("https://lib.example/robots.txt"), 1)
    check("an unreachable robots.txt is a week's `unreachable`, not a refusal",
          ((led7.get("down.example") or {}).get("status"),
           [u for u in wire if "down.example" in u and not u.endswith("robots.txt")]),
          ("unreachable", []))
    check("a challenge on robots.txt is a challenge: tallied, not written",
          ("cf.example" in led7, sk7.get("bot-challenge")), (False, 1))
    check("a Crawl-delay past the cap skips the page rather than asking it sooner",
          ([u for u in sent if "slow.example" in u], "slow.example" in led7,
           sk7.get("robots-crawl-delay")), ([], False, 1))
    check("an origin with no robots.txt (404) is read as it always was",
          ((led7.get("open.example") or {}).get("status"),
           "https://open.example/" in sent), ("fail", True))
    check("every request that was refused is in the tally, by reason",
          {k: v for k, v in sk7.items() if k.startswith("robots-refused")},
          {"robots-refused-request:RobotsDisallowed": 4,
           "robots-refused-request:RobotsUnreachable": 1,
           "robots-refused-request:RobotsChallenged": 1,
           "robots-refused-request:RobotsCrawlDelay": 1})

    # CIVIC DISCOVERY ASKS TOO. Until 2026-10-05 `_discover_civic` handed a
    # city's site to find_calendar on the plain session, so nothing read
    # robots.txt there. Wikidata (the cities list) is a documented API and
    # keeps the plain session; the probe gets the walk's.
    import catalog_discover_civic as _civ7
    wire.clear()
    seen_sess = []

    def _probe7(sess, place, timeout=18):
        seen_sess.append(sess)
        return [], osm.find_calendar(sess, place["url"])

    realc = (_civ7.cities, _civ7.dmo_nominations, _civ7.probe, C._save_ledger,
             C._PacedSession, C.OSM_HOST_GAP_S)
    _civ7.cities = lambda sess, country, limit=40, offset=0: (
        [{"qid": f"Q{i}", "name": h, "city": h, "url": f"https://{h}/", "_row": i,
          "population": 1000 - i}
         for i, h in enumerate(("refused.example", "open.example"))], 2)
    _civ7.dmo_nominations = lambda sess, places: {}
    _civ7.probe = _probe7
    C._save_ledger = lambda led: None
    C._PacedSession = _WebSession
    C.OSM_HOST_GAP_S = 0.01
    try:
        led8 = {}
        _f8, sk8 = C._discover_civic(C._session(), set(), led8, 500, {}, cities_per_run=2)
    finally:
        (_civ7.cities, _civ7.dmo_nominations, _civ7.probe, C._save_ledger,
         C._PacedSession, C.OSM_HOST_GAP_S) = realc
    sent8 = [u for u in wire if not u.endswith("/robots.txt")]
    check("civic: the probe's session asks robots.txt (not the plain session)",
          all(isinstance(x, C._PacedSession) and x.robots is not None for x in seen_sess)
          and len(seen_sess) == 2, True)
    check("civic: a town homepage robots.txt disallows is never fetched",
          [u for u in sent8 if "refused.example" in u], [])
    check("civic: ...and is parked `refused` with its rule, for DEAD_TTL",
          ((led8.get("refused.example") or {}).get("status"),
           "Disallow: /" in (led8.get("refused.example") or {}).get("reason", ""),
           C._dead_recently(led8, "refused.example")), ("refused", True, True))
    check("civic: a town with no robots.txt is read as it always was",
          ("https://open.example/" in sent8, sk8.get("robots-refused-request:RobotsDisallowed")),
          (True, 1))

    # The cache keys redirect hops too: two branch URLs that redirect to one
    # page fetch that page once. And an error is not kept.
    hops = []

    class _Hops(_BA7):
        def send(self, request, **kw):
            hops.append(request.url)
            if "flaky" in request.url and hops.count(request.url) == 1:
                raise _rq7.exceptions.ConnectTimeout("once")
            r = _rq7.models.Response()
            r.request, r.url, r.encoding, r.status_code = request, request.url, "utf-8", 200
            if "/b/" in request.url:
                r.status_code, r.headers["Location"] = 302, "https://x.example/hours"
            r._content = b"hours"
            return r

        def close(self):
            pass
    ps8 = C._PacedSession()
    ps8.mount("https://", _Hops())
    ps8.cache = C._ResponseCache()
    a = ps8.get("https://x.example/b/1")
    b = ps8.get("https://x.example/b/2")
    check("two URLs that redirect to one page fetch that page once",
          hops.count("https://x.example/hours"), 1)
    check("...and both callers get it, at the URL it lives at",
          (a.text, str(a.url), b.text, str(b.url)),
          ("hours", "https://x.example/hours", "hours", "https://x.example/hours"))
    ps8.get("https://x.example/hours")
    check("...and a page first reached as a redirect hop is not fetched again "
          "when it is asked for by name", hops.count("https://x.example/hours"), 1)
    ps8.get("https://x.example/b/1", allow_redirects=False)
    check("...though a redirect asked for without following it is its own answer",
          hops.count("https://x.example/b/1"), 2)
    try:
        ps8.get("https://x.example/flaky")
    except _rq7.exceptions.ConnectTimeout:
        pass
    check("a request that failed is asked again, not answered with the failure",
          ps8.get("https://x.example/flaky").text, "hours")

    # 8. A METRO WHERE MOST PROBES RAISED WAS NOT READ. Review, 2026-10-04:
    # with find_calendar raising on every venue, the pool tallied 30 errors,
    # wrote nothing and moved a 3-metro cursor from GB:M0 to GB:M3; the code
    # before the pool raised and left it alone.
    print("\nwhen the probe code itself is broken")
    real8 = (osm.overpass_venues, osm.find_calendar, osm.metros, C._save_ledger)
    saves8 = []
    C._save_ledger = lambda led: saves8.append(len(led))
    # FOUR metros and a run of three, so a cursor that marched would land on
    # GB:M3 and not wrap back round to where it started.
    osm.metros = lambda: [{"name": f"M{i}", "country": "GB", "bbox": (0, 0, 1, 1)}
                          for i in range(4)]
    osm.overpass_venues = lambda sess, bbox, lab: [
        {"url": f"https://{lab[:2]}-{j}.example", "name": f"v{j}"} for j in range(10)]

    def _typeerror(sess, url):
        raise TypeError("a refactor broke the parser")
    osm.find_calendar = _typeerror
    cur8, led8, raised = {"metro_key": "GB:M0"}, {}, None
    try:
        C._discover_osm(C._session(), set(), led8, 500, cur8, metros_per_run=3)
    except RuntimeError as exc:
        raised = str(exc)
    finally:
        (osm.overpass_venues, osm.find_calendar, osm.metros, C._save_ledger) = real8
    check_true(f"every probe raising stops the walk loudly ({raised})",
               bool(raised) and "10 of 10" in raised)
    check("...without moving the cursor past the metro", cur8["metro_key"], "GB:M0")
    check("...after saving the ledger, which says nothing about those venues",
          (saves8, led8), ([0], {}))

    # 9. TWO VENUES, ONE CALENDAR: the first in MAP order proposes it, however
    # the probes finish. Both of Halifax's Captain William Spry and Dartmouth
    # North centres propose halifax.ca's events calendar.
    real9 = (osm.overpass_venues, osm.find_calendar, osm.metros, C._save_ledger)
    C._save_ledger = lambda led: None
    osm.metros = lambda: [{"name": "Two", "country": "GB", "bbox": (0, 0, 1, 1)}]
    osm.overpass_venues = lambda sess, bbox, lab: [
        {"url": "https://first.example/", "name": "First Centre", "lat": 1.0, "lon": 1.0},
        {"url": "https://second.example/", "name": "Second Centre", "lat": 2.0, "lon": 2.0}]

    slow9 = ["first"]

    def _shared(sess, url):
        if slow9[0] in url:
            _tm6.sleep(0.15)                  # this one finishes LAST
        return {"status": "ok", "adapter": "tribe", "labels": ["tribe"],
                "cal_url": "https://city.example/events/"}
    osm.find_calendar = _shared
    named = []
    try:
        for slow9[0] in ("first", "second"):
            f9, sk9 = C._discover_osm(C._session(), set(), {}, 500, {}, metros_per_run=1,
                                      workers=4)
            named.append([c["name"] for c in f9.values()])
    finally:
        (osm.overpass_venues, osm.find_calendar, osm.metros, C._save_ledger) = real9
    check("two venues proposing one calendar: the first on the map names it, "
          "whichever probe finishes first", named, [["First Centre"], ["First Centre"]])
    check("...and the collision is tallied", sk9.get("same-calendar"), 1)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: " + "; ".join(FAILURES))
        return 1
    print("all osm discovery checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
