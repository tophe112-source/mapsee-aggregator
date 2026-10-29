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
    run past the caller's deadline. A refused metro is struck at most once a
    UTC day, because two crons walk one cursor since 2026-10-23: three
    refusals in two days do not skip it, a third day's does.
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
  * A CENTRE'S CALENDAR IS NOT ALWAYS AN IFRAME, AND IT BELONGS ON THE CENTRE
    (2026-10-12). Polish Home was filed no-calendar on a page whose Google
    calendar sits in Pretty Google Calendar's inline script, beside the site
    owner's own API key, which no output may carry. A centre's find is
    proposed on the OSM point with no street address (its street a hint the
    ics adapter only matches, its other names aliases), so the events that
    name it share the centre's four-decimal key, and only those: a calendar
    found on a centre's site is not always the centre's programme, so an
    event that names no place waits for a person (`pin_unnamed: false`). A
    discovered personal address is held, and is in no committed or public
    output; the programme words, the third follow, the platform link on a
    followed page and the probe version in the ledger are pinned too.
  * A HOMEPAGE THE WALK ASKED IS WRITTEN DOWN (2026-10-23). 2,002 of 7,044
    probes in three daily runs left no ledger row, so a resumed metro asked
    them again (Berlin's second visit: about 500 of 2,443 probes). A find, a
    4xx, a Crawl-delay past the cap and an unusable find park the homepage a
    week (`found`, `probed`), never over a verify's row, and a home whose
    calendar is configured is not asked again; a challenge, a 429 and a 5xx
    are still not written. A jsonld listing that shows no event is a
    no-calendar verdict, not a candidate that fails verify (154 such failures
    in six daily runs). An older probe's verdict lapses at 60 days, not at once.
    And a parked home costs nothing else (review, 2026-10-24): every find is
    written out, past --limit too (3 of 5 were cut with --limit 2 and four
    workers), a held calendar configured by hand answers its home, and a
    lapsed offsite row keeps its organizer link through a 4xx or a timeout.
  * A CALENDAR IS NAMED IN THE WALKS' OTHER LANGUAGES TOO (2026-10-25). Of 56
    readable non-US homepages filed no-calendar, 14 named theirs only in a
    word the walk did not read (Wydarzenia, Akce, Dogodki, Viðburðir, Hva
    skjer, Tapahtumakalenteri). Those words are a FALLBACK, read only when the
    old ones found nothing, because in the same pass they moved 14 of 255
    configured calendars to a key no config holds. A waste, booking or meeting
    calendar is not followed; "prevent" is not "event"; a page that declares
    its charset only in a <meta> is decoded so; and Event Organiser's export
    is found like My Calendar's.
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
    # says "Register", which CAL_LINK_RX does not read (it has read "Drop-in
    # schedule" since 2026-10-12), so the host is the signal for these
    # platforms and these only.
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
    check("a Crawl-delay past the cap skips the page rather than asking it sooner, and "
          "parks it `probed` for the week, which verify never reads",
          ([u for u in sent if "slow.example" in u], (led7.get("slow.example") or {}).get("status"),
           C._dead_recently(led7, "slow.example"), sk7.get("robots-crawl-delay")),
          ([], "probed", False, 1))
    check("...with the delay it asked for in the reason",
          "Crawl-delay: 120" in (led7.get("slow.example") or {}).get("reason", ""), True)
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

    jsonld_evidence()
    homepage_park(C)
    homepage_park_edges(C)
    centre_calendars(C)
    overpass_strikes(C)
    intl_calendar_words()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: " + "; ".join(FAILURES))
        return 1
    print("all osm discovery checks passed")
    return 0


# ---- a community centre's own calendar (2026-10-12) ------------------------
# polishhome.org, trimmed. The walk followed /calendar/ on 2026-10-11 and filed
# the centre no-calendar: Pretty Google Calendar keeps the id in an inline
# script, beside the SITE OWNER's own Google API key ("google_api"). The real
# key is replaced by FAKE_SITE_KEY, and no output of the walk may contain it.
PH_ID = "d83853455e5cebb55a95bc30f0719941cc219c65f48a1cd6228d3f1ed34a7e09@group.calendar.google.com"
FAKE_SITE_KEY = "AIzaSyFAKE-site-owners-key-not-real-0000"
PH_HOME = """<html><body><nav><a href="https://polishhome.org/calendar/">Calendar</a>
<a href="https://polishhome.org/about-us/">About Us</a></nav>
<a
					href="/aec-events/"
					class="sowb-button ow-icon-placement-left ow-button-hover" 	>
		<span>MORE EVENTS</span></a>
<a href="https://polishhome.org/blog/aec_events/domowka-u-syreny/" title="Domowka u Syreny">Domowka u Syreny</a>
</body></html>"""
PH_CAL = """<html><head>
<script id="pgcal_helpers-js" src="https://polishhome.org/wp-content/plugins/pretty-google-calendar/public/js/helpers.js?ver=2.2.1"></script>
</head><body><h1 class="entry-title">Calendar</h1>
 <div id='pgcalendar-0b319af336f45404d0e731b9f8f3c058bda1fd75a1190e981aa1aad7acf93aca' class='pgcal-container'>loading...</div>
 <div class='pgcal-branding'>Powered by <a href='https://wordpress.org/plugins/pretty-google-calendar/'>Pretty Google Calendar</a></div>
<script id="pgcal_loader-js-after">
document.addEventListener('DOMContentLoaded', function() {
      function pgcal_inlineScript(settings) {
        var ajaxurl = 'https://polishhome.org/wp-admin/admin-ajax.php';
        pgcal_render_calendar(settings, ajaxurl, pgcal_ajax_nonce);
      }
      pgcal_inlineScript({"gcal":"%s","cal_ids":"","locale":"en","list_type":"listCustom","views":"dayGridMonth, listCustom","fc_args":"{}","google_api":"%s"});
    });
</script></body></html>""" % (PH_ID, FAKE_SITE_KEY)
# The OSM element: amenity=community_centre, way/229910960.
PH_VENUE = {"name": "Polish Home Association", "url": "https://polishhome.org",
            "kind": "community_centre", "lat": 47.6169525, "lon": -122.30845,
            "street": "1714 18th Avenue", "city": "Seattle",
            "aliases": ["Seattle Polish Cultural Center", "Dom Polski"]}
# Its tags as Overpass returns them (the element's own email left out).
PH_TAGS = {"name": "Polish Home Association", "office": "association", "amenity": "community_centre",
           "name:en": "Polish Home Association", "name:pl": "Dom Polski", "website": "https://polishhome.org",
           "alt_name": "Seattle Polish Cultural Center;Dom Polski", "building": "yes",
           "addr:city": "Seattle", "addr:state": "WA", "addr:street": "18th Avenue", "addr:country": "US",
           "addr:postcode": "98122", "addr:housenumber": "1714", "name:etymology:wikidata": "Q36"}
# A Simple Calendar page: its events link to Google with eid=base64("<event
# id> <calendar id>"), the domain shortened to @m. Markup from
# artisticdynamicassociation.eu (2026-10-12), the calendar id made up.
SIMCAL = ('<link rel="stylesheet" href="/wp-content/plugins/google-calendar-events/assets/generated/'
          'default-calendar-grid.min.css?ver=4.3.1"><div class="simcal-calendar" data-calendar-id="1723">'
          '<li class="simcal-event"><span class="simcal-event-title">Open studio evening</span>'
          '<a href="https://www.google.com/calendar/event?eid=XzYwcjMyZTlrODkyMzZiOWo4ZDJqYWI5azZwMTM2Ymlw'
          'NzUyamFiYTY3NHJqMmdobDY0czM2aGk1OGsgZXhhbXBsZWFydHNhc3NvY2lhdGlvbkBt&#038;ctz=Europe/Vienna" '
          'target="_blank" >Weitere Details anzeigen</a></li></div>')



def overpass_strikes(C):
    """A metro Overpass refuses is struck at most once a UTC day (review,
    2026-10-24). Since 2026-10-23 the 03:20 and 09:30 crons walk one `osm`
    slot, so a strike a run skipped a metro after about 24 h (day 1 14:30,
    day 2 08:40, day 2 14:30), and a skipped metro waits 45-130 days for the
    ring to wrap. The threshold stays three, and three means three days."""
    import contextlib
    import io
    import os
    from unittest.mock import patch
    print()
    print("an Overpass refusal is one strike a day, however many runs ask")
    answer = [None]
    real = (osm.overpass_venues, osm.metros, C._save_ledger)
    C._save_ledger = lambda led: None
    osm.metros = lambda: [{"name": n, "country": "DE", "bbox": (0, 0, 1, 1)}
                          for n in ("Hamburg", "Bremen")]
    osm.overpass_venues = lambda sess, bbox, lab, **kw: answer[0]

    def walk(cur, day, venues=None):
        answer[0] = venues
        said = io.StringIO()
        with patch.dict(os.environ, {"MAPSEE_TODAY": day}), contextlib.redirect_stdout(said):
            C._discover_osm(C._session(), set(), {}, 500, cur, metros_per_run=1, workers=1)
        return said.getvalue()
    try:
        cur = {"metro_key": "DE:Hamburg"}
        walk(cur, "20261022")
        walk(cur, "20261023")
        said = walk(cur, "20261023")
        check("three refusals in two calendar days leave the metro UNREAD, not skipped",
              (cur["metro_key"], cur["stuck"].get("DE:Hamburg"), "SKIPPING" in said),
              ("DE:Hamburg", {"n": 2, "day": 20261023}, False))
        check_true("...and the second refusal of a day says it was not counted",
                   "already counted today" in said)
        said = walk(cur, "20261024")
        check("a refusal on a third day still skips it, loudly",
              (cur["metro_key"], "DE:Hamburg" in cur["stuck"], "SKIPPING" in said),
              ("DE:Bremen", False, True))
        old = {"metro_key": "DE:Hamburg", "stuck": {"DE:Hamburg": 1}}
        walk(old, "20261024")
        check("a bare count from before is a strike on an earlier day",
              (old["metro_key"], old["stuck"].get("DE:Hamburg")),
              ("DE:Hamburg", {"n": 2, "day": 20261024}))
        gap = {"metro_key": "DE:Hamburg"}
        walk(gap, "20261022")
        walk(gap, "20261023", venues=[])
        check("an answer clears the count, so 'running' still means running",
              (gap["metro_key"], gap["stuck"]), ("DE:Bremen", {}))
    finally:
        (osm.overpass_venues, osm.metros, C._save_ledger) = real


def _days_ago(C, n):
    """The ledger's YYYYMMDD for `n` days before the walk's today."""
    import datetime
    return int((C._as_date(C._today_int()) - datetime.timedelta(days=n)).strftime("%Y%m%d"))


class _Site:
    """A whole site from a dict of pages; anything else is unreachable."""

    def __init__(self, pages):
        self.pages, self.asked = pages, []

    def get(self, u, timeout=None, allow_redirects=True, **kw):
        self.asked.append(u)
        if u not in self.pages:
            raise ConnectionError(u)
        page = self.pages[u]

        class _R:
            status_code, url, text = 200, u, page
            headers = {"Content-Type": "text/html"}
        return _R()


def jsonld_evidence():
    """A jsonld find reaches verify only when the page that becomes its
    listing shows the adapter an event (2026-10-23: 154 'no schema.org Event
    blocks found' in 6 daily runs; 35 of 35 readable configured OSM jsonld
    sources without an Event block pass the same test)."""
    print()
    print("a jsonld find is proposed only when its listing shows the adapter an event")
    v = {"name": "Royal Room", "url": "https://rr.example", "kind": "music_venue",
         "lat": 47.5, "lon": -122.3}
    home = '<a href="/events/">Events</a>'

    def probe(pages):
        site = _Site(dict({"https://rr.example": home}, **pages))
        return site, osm.find_calendar(site, "https://rr.example")

    site, f = probe({"https://rr.example/events/": WPEM})
    check("a plugin's listing with no Event block and no event link has no evidence",
          (f["status"], f["adapter"], f["extra"].get("events_evidence")), ("ok", "jsonld", False))
    check("...so it is not proposed, and the probe says why",
          (osm.to_candidate(v, f), osm.why_no_candidate(f)),
          (None, "jsonld-without-events(wp-event-manager)"))
    check("...at no extra request", site.asked, ["https://rr.example", "https://rr.example/events/"])
    linked = WPEM.replace("</body>", '<a href="https://rr.example/event/trio-reunion/">Trio</a></body>')
    _s, f = probe({"https://rr.example/events/": linked})
    c = osm.to_candidate(v, f)
    check("one that links its events on its own host is proposed",
          (f["extra"].get("events_evidence"), c and c["listing"]), (True, ["https://rr.example/events/"]))
    check("...by the link_pattern it ships with, the one verify follows",
          bool(c and re.search(c["link_pattern"], linked)), True)
    _s, f = probe({"https://rr.example/events/": WPEM.replace(
        "</body>", '<a href="/event/trio-reunion/">Trio</a></body>')})
    check("a relative event link is not evidence: verify matches the pattern on the page as written",
          f["extra"].get("events_evidence"), False)
    _s, f = probe({"https://rr.example/events/": WPEM.replace(
        "</body>", '<script type="application/ld+json">{"@context":"https://schema.org",'
                   '"@type":"Event","name":"Trio","startDate":"2026-11-20T20:00"}</script></body>')})
    check("an Event block on the listing is evidence on its own",
          (f["extra"].get("events_evidence"), bool(osm.to_candidate(v, f))), (True, True))

    print("  ...read off the page that BECOMES the listing, not the one in hand")
    _s, f = probe({"https://rr.example/events/": WIX_SEA_MONSTER})
    check("a Wix listing whose warmup data carries events", f["extra"].get("events_evidence"), True)
    _s, f = probe({"https://rr.example/events/": WIX_NO_EVENTS})
    check("a Wix page with no events, from a homepage with none, has none",
          (f["extra"].get("events_evidence"), osm.why_no_candidate(f)),
          (False, "jsonld-without-events(wix)"))
    site = _Site({"https://www.teatrodelburatto.com/": WIX_HOME_PROGRAMME,
                  "https://www.teatrodelburatto.com/programma": WIX_NO_EVENTS})
    f = osm.find_calendar(site, "https://www.teatrodelburatto.com/")
    check("the homepage a Wix page with no events gives way to is read for its own events",
          (f["cal_url"], f["extra"].get("events_evidence")), ("https://www.teatrodelburatto.com/", True))
    shows = '<a href="/shows/trio-reunion">Trio</a><a href="/shows/jazz-night">Jazz</a>'
    site = _Site({"https://rr.example": '<a href="/shows/trio-reunion">Events</a>',
                  "https://rr.example/shows/trio-reunion": WPEM, "https://rr.example/shows/": shows})
    f = osm.find_calendar(site, "https://rr.example")
    check("a parent _prefer_listing walked up to is evidence: the label came from an event page",
          (f["cal_url"], f["extra"].get("events_evidence")), ("https://rr.example/shows/", True))
    # ok-homepage: the plugin is on the homepage, and the listing is the first
    # page the walk followed, whose body is gone by the time it is proposed.
    plugin_home = WPEM.replace("</body>", '<a href="/whats-on/">Events</a></body>')
    season = '<p>Our season</p><a href="https://rr.example/events/trio-reunion">Trio</a>'
    for page, want in ((season, True), ("<p>Our season, in prose</p>", False)):
        site = _Site({"https://rr.example": plugin_home, "https://rr.example/whats-on/": page})
        f = osm.find_calendar(site, "https://rr.example")
        check(f"  the first followed page as the listing, {'with' if want else 'without'} an event link",
              (f["status"], f["cal_url"], f["extra"].get("events_evidence")),
              ("ok-homepage", "https://rr.example/whats-on/", want))
    for extra, want in (('<a href="https://rr.example/event/trio-reunion">Trio</a>', True), ("", False)):
        site = _Site({"https://rr.example": plugin_home.replace("</body>", extra + "</body>")})
        f = osm.find_calendar(site, "https://rr.example")
        check(f"  the homepage as the listing when nothing it links answers, "
              f"{'with' if want else 'without'} an event link",
              (f["status"], f["cal_url"], f["extra"].get("events_evidence")),
              ("ok-homepage", "https://rr.example", want))
    check("a find built by hand, with no evidence read, is shaped as before",
          bool(osm.to_candidate(v, {"adapter": "jsonld", "labels": ["wp-event-manager"],
                                    "cal_url": "https://rr.example/events/"})), True)


def homepage_park(C):
    """Every homepage the walk asks is written down, except where the answer
    was about the moment rather than the site. Measured 2026-10-23: 2,002 of
    7,044 probes in runs 118, 121 and 123 left no ledger row, and Berlin's
    second visit spent about 500 of 2,443 probes re-asking the first's homes."""
    import contextlib
    import io
    print()
    print("a homepage the walk asked is parked a week, whatever it answered")
    ics = {"status": "ok", "adapter": "ics", "labels": ["ics"]}
    answers = {
        "https://found.example": dict(ics, cal_url="https://found.example/events/", ics="/events.ics"),
        "https://configured.example": dict(ics, cal_url="https://configured.example/events/", ics="/cal.ics"),
        "https://gone.example": {"status": "http404"},
        "https://forbidden.example": {"status": "http403"},
        "https://plugin.example": {"status": "ok", "adapter": "mylisting", "labels": ["mylisting"],
                                   "cal_url": "https://plugin.example/explore/"},
        "https://emptyld.example": {"status": "ok", "adapter": "jsonld", "labels": ["wp-event-manager"],
                                    "cal_url": "https://emptyld.example/events/",
                                    "extra": {"events_evidence": False}},
        "https://busy.example": {"status": "http429"},
        "https://down.example": {"status": "http503"},
        "https://wall.example": {"status": "bot-challenge"},
        "https://audited.example": {"status": "http404"},
        "https://rooted.example": dict(ics, cal_url="https://rooted.example/", ics="/all.ics"),
    }
    seen = {C._canon("https://configured.example/cal.ics")}
    led = {"audited.example": {"checked": _days_ago(C, 100), "status": "fail", "type": "jsonld",
                               "reason": "http 404"},
           "rooted.example": {"checked": _days_ago(C, 100), "status": "empty", "type": "tribe",
                              "reason": "3 events / 0 future"}}
    verify_rows = {k: dict(r) for k, r in led.items()}

    def walk():
        asked = []
        real = (osm.overpass_venues, osm.find_calendar, osm.metros, C._save_ledger)
        C._save_ledger = lambda led: None
        osm.metros = lambda: [{"name": "Parkton", "country": "GB", "bbox": (0, 0, 1, 1)}]
        osm.overpass_venues = lambda sess, bbox, lab, **kw: [{"url": u, "name": u[8:]} for u in answers]
        osm.find_calendar = lambda sess, url: asked.append(url) or dict(answers[url])
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                found, sk = C._discover_osm(C._session(), set(seen), led, 500, {},
                                            metros_per_run=1, workers=1)
        finally:
            (osm.overpass_venues, osm.find_calendar, osm.metros, C._save_ledger) = real
        return sorted(asked), found, sk

    _a, found, sk = walk()
    status = {k[:-8]: led[k].get("status") for k in led}
    check("a find, a 4xx and an unusable find are written down; a 429, a 5xx and a challenge are not",
          status, {"found": "found", "configured": "found", "gone": "probed", "forbidden": "probed",
                   "plugin": "probed", "emptyld": "fail", "audited": "fail", "rooted": "empty"})
    check("...a `found` row names its candidate's key, proposed or already configured",
          (led["found.example"].get("cal_key"), led["configured.example"].get("cal_key"),
           sorted(found), sk.get("configured")),
          ("found.example/events.ics", "configured.example/cal.ics",
           ["found.example/events.ics", "rooted.example/all.ics"], 1))
    check("...a `probed` row says what it was",
          [led[k]["reason"] for k in ("gone.example", "plugin.example")],
          ["osm probe: http404", "osm probe: found-but-unusable: no-config-shape(mylisting)"])
    check("...and neither is dead to verify, which reads only fail, empty and refused",
          [C._dead_recently(led, k) for k in ("found.example", "gone.example", "plugin.example")],
          [False, False, False])
    check("a jsonld find with nothing on its listing is a no-calendar verdict, parked for DEAD_TTL",
          ({k: led["emptyld.example"].get(k) for k in ("reason", "probe", "cal_url")},
           C._dead_recently(led, "emptyld.example"),
           sk.get("found-but-unusable: jsonld-without-events(wp-event-manager)")),
          ({"reason": "osm probe: no-calendar", "probe": osm.PROBE_VERSION,
            "cal_url": "https://emptyld.example/events/"}, True, 1))
    check("a verify's row under the homepage's key is never overwritten, whatever the probe found",
          {k: led[k] for k in verify_rows}, verify_rows)

    asked, _f, sk = walk()
    check("the next run asks only what was not written down, and the verify rows' homes",
          asked, ["https://audited.example", "https://busy.example", "https://down.example",
                  "https://rooted.example", "https://wall.example"])
    check("...and says why it skipped the rest",
          (sk.get("probed-recently"), sk.get("configured"), sk.get("known-dead")), (4, 1, 1))

    for k, r in led.items():
        if r.get("status") in ("found", "probed"):
            r["checked"] = _days_ago(C, C.UNREACHABLE_TTL_DAYS)
    asked, _f, sk = walk()
    check("after the week every one is asked again, except a home whose calendar is configured",
          [u[8:-8] for u in asked],
          ["audited", "busy", "down", "forbidden", "found", "gone", "plugin", "rooted", "wall"])
    check("...which stays answered for as long as it is", (sk.get("configured"), sk.get("probed-recently")),
          (1, None))


def homepage_park_edges(C):
    """What a parked homepage must not cost. Review, 2026-10-24: with
    --limit 2 and 4 workers the walk found 5 calendars, parked all 5 homes
    and cmd_discover wrote 2, so 3 were never verified and the resumed metro
    skipped them; a jsonld park wrote over a verify's row; and a 403 on a
    lapsed offsite verdict erased the organizer link `ledger --offsite`
    lists."""
    import contextlib
    import io
    import os
    import tempfile
    print()
    print("a parked homepage costs no calendar, no verify row and no offsite link")

    def walk(venues, answer, led, seen=(), declined=()):
        asked = []
        real = (osm.overpass_venues, osm.find_calendar, osm.metros, C._save_ledger, C._not_included)
        C._save_ledger = lambda led: None
        C._not_included = lambda fname: set(declined) if fname == "ics_sources.json" else set()
        osm.metros = lambda: [{"name": "Parkton", "country": "GB", "bbox": (0, 0, 1, 1)}]
        osm.overpass_venues = lambda sess, bbox, lab, **kw: [{"url": u, "name": u[8:]} for u in venues]
        osm.find_calendar = lambda sess, url: asked.append(url) or dict(answer(url))
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                _f, sk = C._discover_osm(C._session(), set(seen), led, 500, {},
                                         metros_per_run=1, workers=1)
        finally:
            (osm.overpass_venues, osm.find_calendar, osm.metros, C._save_ledger,
             C._not_included) = real
        return asked, sk

    # THE WALK'S FINDS ARE WRITTEN OUT WHOLE. It stops starting probes at the
    # limit and the four already running settle, every one parking its home.
    venues = [f"https://v{i}.example" for i in range(8)]
    led, tmp = {}, tempfile.mkdtemp(prefix="osm-limit-")
    out = os.path.join(tmp, "cand-osm.json")

    def discover(limit):
        real = (osm.overpass_venues, osm.find_calendar, osm.metros, C._save_ledger, C._load_ledger,
                C._config_keys, C.curated_categories, C._load_cursor, C._save_cursor,
                C.OSM_PROBE_WORKERS)
        C._save_ledger = lambda l: None
        C._load_ledger = lambda: led
        C._config_keys = lambda: set()
        C.curated_categories = lambda session=None: ["music"]
        C._load_cursor = lambda: {}
        C._save_cursor = lambda cur: None
        C.OSM_PROBE_WORKERS = 4
        osm.metros = lambda: [{"name": "Parkton", "country": "GB", "bbox": (0, 0, 1, 1)}]
        osm.overpass_venues = lambda sess, bbox, lab, **kw: [{"url": u, "name": u[8:]} for u in venues]
        osm.find_calendar = lambda sess, url: {"status": "ok", "adapter": "ics", "labels": ["ics"],
                                               "cal_url": url + "/events/", "ics": "/events.ics"}
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                C.cmd_discover(limit=limit, out=out, backend="osm", metros=1)
        finally:
            (osm.overpass_venues, osm.find_calendar, osm.metros, C._save_ledger, C._load_ledger,
             C._config_keys, C.curated_categories, C._load_cursor, C._save_cursor,
             C.OSM_PROBE_WORKERS) = real
        return [C._canon(C._key_of(c)) for c in json.load(open(out, encoding="utf-8"))]

    first = discover(2)
    parked = sorted(r["cal_key"] for r in led.values() if r.get("status") == "found")
    check_true(f"the walk can settle past --limit 2 with four probes running ({len(first)} found)",
               2 < len(first) <= 2 + 4 - 1)
    check("...and every calendar whose home it parked is written out for verify",
          sorted(first), parked)
    second = discover(500)
    check("...so the resumed metro and the first run between them propose every calendar once",
          sorted(first + second), sorted(f"v{i}.example/events.ics" for i in range(8)))

    # A home whose calendar is DECLINED stays answered, as a configured one does.
    led = {"declined.example": {"checked": _days_ago(C, C.UNREACHABLE_TTL_DAYS), "status": "found",
                                "type": "osm-venue", "cal_key": "declined.example/cal.ics",
                                "reason": "osm probe: found ics", "name": "declined.example"}}
    asked, sk = walk(["https://declined.example"], lambda u: {"status": "no-calendar"}, led,
                     declined={"declined.example/cal.ics"})
    check("a home whose calendar is declined is not asked again, past its week",
          (asked, sk.get("configured")), ([], 1))
    asked, sk = walk(["https://declined.example"], lambda u: {"status": "no-calendar"}, led)
    check("...until it is no longer declined", (asked, sk.get("configured")),
          (["https://declined.example"], None))

    # A SITE DECLINED WHOLE stays declined under any page of it: Lotto Arena
    # (reverted 2026-10-29), whose event pages answer through a third party's
    # sign-in. The walk found it as one event's page, and a later walk could
    # propose another; its homepage key in `_not_included` stops the probe,
    # whether OSM's `website` names the root or a page of the site.
    check_true("Lotto Arena is declined on the record as a site, homepage and sitemap",
               {"lotto-arena.be", "lotto-arena.be/sitemap.xml"}
               <= C._not_included("jsonld_sources.json"))
    asked, sk = walk(["https://www.lotto-arena.be/", "https://lotto-arena.be/nl/kalender"],
                     lambda u: {"status": "ok", "adapter": "jsonld", "labels": ["jsonld-event"],
                                "cal_url": "https://www.lotto-arena.be/evenement/next-show-a1"},
                     {}, declined={"lotto-arena.be"})
    check("a venue whose homepage is declined is never probed, at the root or a page of it",
          (asked, sk.get("declined")), ([], 2))
    asked, sk = walk(["https://decl.example/about"], lambda u: {"status": "no-calendar"}, {},
                     declined={"decl.example/events"})
    check("...but a declined CALENDAR page does not decline the rest of its site",
          (asked, sk.get("declined")), (["https://decl.example/about"], None))

    # A find whose row is HELD for a ruling the owner owes (not a personal
    # address: Los Ranchos de Albuquerque's CivicPlus feeds, 2026-10-29) is not
    # proposed again either, as a held personal-address calendar is not.
    led = {"heldtown.example/events.ics": {"status": "held", "checked": 20261029, "type": "ics",
                                           "reason": "held pending the owner's ruling"}}
    asked, sk = walk(["https://heldtown.example"],
                     lambda u: {"status": "ok", "adapter": "ics", "labels": ["ics"],
                                "cal_url": u + "/events/", "ics": "/events.ics"}, led)
    check("a found calendar held for a ruling the owner owes is not proposed again",
          (asked, sk.get("held")), (["https://heldtown.example"], 1))

    # A jsonld find with nothing on is parked only where the row is the walk's.
    stale = {"checked": _days_ago(C, C.DEAD_TTL + 10), "status": "fail", "type": "jsonld",
             "reason": "http 404"}
    led = {"verified.example": dict(stale)}
    _a, sk = walk(["https://verified.example"],
                  lambda u: {"status": "ok-homepage", "adapter": "jsonld", "labels": ["wp-event-manager"],
                             "cal_url": u + "/whats-on/", "extra": {"events_evidence": False}}, led)
    check("a jsonld find with no event never writes over a verify's row under the home's key",
          (led["verified.example"], sk.get("found-but-unusable: jsonld-without-events(wp-event-manager)")),
          (stale, 1))

    # A LAPSED OFFSITE VERDICT KEEPS ITS LINK through an answer about the
    # moment or an unusable find; a fresh verdict still replaces it.
    off = {"checked": _days_ago(C, C.PROBE_REASK_DAYS + 1), "status": "fail", "type": "osm-venue",
           "probe": 1, "reason": "osm probe: offsite:eventbrite", "name": "Hall",
           "offsite_url": "https://www.eventbrite.com/o/hall-123"}
    kept = []
    for answer in ({"status": "http403"}, {"status": "unreachable", "note": "ConnectTimeout"},
                   {"status": "robots-crawl-delay"},
                   {"status": "ok", "adapter": "mylisting", "labels": ["mylisting"],
                    "cal_url": "https://off.example/explore/"},
                   {"status": "ok", "adapter": "jsonld", "labels": ["wp-event-manager"],
                    "cal_url": "https://off.example/events/", "extra": {"events_evidence": False}}):
        led = {"off.example": dict(off)}
        walk(["https://off.example"], lambda u, a=answer: a, led)
        kept.append(led["off.example"] == off)
    check("a lapsed offsite row survives a 4xx, a timeout, a Crawl-delay and an unusable find",
          kept, [True] * 5)
    led = {"off.example": dict(off)}
    walk(["https://off.example"], lambda u: {"status": "no-calendar"}, led)
    check("...but a fresh no-calendar verdict replaces it",
          (led["off.example"]["reason"], "offsite_url" in led["off.example"]),
          ("osm probe: no-calendar", False))
    led = {"off.example": dict(off)}
    walk(["https://off.example"], lambda u: {"status": "ok", "adapter": "ics", "labels": ["ics"],
                                             "cal_url": u + "/events/", "ics": "/events.ics"}, led)
    check("...and so does a find of a calendar of its own",
          (led["off.example"]["status"], "offsite_url" in led["off.example"]), ("found", False))


def centre_calendars(C):
    import contextlib
    import dataclasses
    import io
    import os
    import tempfile
    from unittest.mock import patch

    import mapsee_gcal as G
    import mapsee_ingest_ics as ICS
    import mapsee_supabase_sync as SYNC

    print()
    print("a centre's own calendar, in a form that is not an iframe")
    site = _Site({"https://polishhome.org": PH_HOME, "https://polishhome.org/calendar/": PH_CAL,
                  "https://polishhome.org/aec-events/": "<html>Future Events</html>"})
    f = osm.find_calendar(site, "https://polishhome.org")
    check("Polish Home's Pretty Google Calendar is found (it was filed no-calendar)",
          (f["status"], f["labels"], f["ics"]), ("ok", ["gcal-embed"], G.ical_url(PH_ID)))
    check("...on the page the walk had already followed", f["cal_url"], "https://polishhome.org/calendar/")
    c = osm.to_candidate(PH_VENUE, f, "Seattle, United States")
    check("...proposed to the ics adapter under its export URL",
          (c["type"], c["url"], c["category"]), ("ics", G.ical_url(PH_ID), "community"))
    check("a centre's calendar carries the OSM point and NO street address, so the sync keeps it exact",
          c.get("venue"), {"name": "Polish Home Association", "city": "Seattle",
                           "lat": 47.6169525, "lon": -122.30845, "street_hint": "1714 18th Avenue",
                           "aliases": ["Seattle Polish Cultural Center", "Dom Polski"], "pin_unnamed": False})
    check("...a street_hint only with a house number: a bare street names a whole road",
          "street_hint" in osm.to_candidate(dict(PH_VENUE, street="18th Avenue"), f)["venue"], False)

    class _Overpass:
        def post(self, *a, **k):
            class _R:
                status_code, headers = 200, {}

                def json(self):
                    return {"elements": [{"type": "way", "id": 229910960, "tags": PH_TAGS,
                                          "center": {"lat": 47.61694865, "lon": -122.30845050772166}}]}

                def raise_for_status(self):
                    pass
            return _R()
    got = osm.overpass_venues(_Overpass(), "0,0,1,1", "Seattle", quiet=True)[0]
    check("Overpass gives the centre its other names: alt_name split, name:pl, never name:en "
          "that repeats it or name:etymology", (got["street"], got["aliases"]),
          ("1714 18th Avenue", ["Seattle Polish Cultural Center", "Dom Polski"]))
    check("...at most eight, each one once",
          len(osm.osm_aliases({"name": "Hall", "alt_name": ";".join(f"Hall {i}" for i in range(12)),
                               "short_name": "hall 1"})), 8)

    # The contract with the ics adapter's LOCATION-names-the-venue rule
    # (centres-ics, mapsee_ingest_ics._names_venue). A Google-picked place
    # writes the house number, which that rule matches only through the
    # block's street; before street_hint, every one of these went to Photon.
    names = getattr(ICS, "_names_venue", None)
    if names is None:
        print("  (skip) the ics adapter's LOCATION-names-the-venue rule has not landed on this "
              "tree; these six checks, and one below, run once it has")
    else:
        for loc, want in (("Polish Home Association, 1714 18th Ave, Seattle, WA 98122, USA", True),
                          ("Polish Home Association \u2013 Dom Polski, 1714 18th Ave", True),
                          ("1714 18th Ave, Seattle, WA 98122", True),
                          ("Dom Polski - Main Hall", True),
                          ("1800 18th Ave, Seattle, WA 98122", False),
                          ("1714 18th Ave S, Seattle, WA 98144", False)):
            check(f"  the discovered block {'names' if want else 'does not name'} {loc!r}",
                  names(c["venue"], loc), want)
    check("...a library's and a social centre's too",
          [bool((osm.to_candidate(dict(PH_VENUE, kind=k), f) or {}).get("venue"))
           for k in ("library", "social_centre")], [True, True])
    check("...but not a church's, a club's or a theatre's: pinned by hand, from the events",
          [(osm.to_candidate(dict(PH_VENUE, kind=k), f) or {}).get("venue")
           for k in ("place_of_worship", "club", "theatre")], [None, None, None])
    _tribe = {"adapter": "tribe", "labels": ["tribe"], "cal_url": "https://polishhome.org/events/"}
    check("a centre's block has no street address for any adapter",
          "address" in osm.to_candidate(PH_VENUE, _tribe)["venue"], False)
    check("...and only the ics one holds back what names no place: tribe reads its own venues",
          "pin_unnamed" in osm.to_candidate(PH_VENUE, _tribe)["venue"], False)
    check("...while a theatre's keeps the street OSM had",
          osm.to_candidate(dict(PH_VENUE, kind="theatre"), _tribe)["venue"].get("address"), "1714 18th Avenue")
    check("the site owner's key is in nothing the probe or the candidate holds",
          FAKE_SITE_KEY in json.dumps([f, c]), False)
    fs = osm.find_calendar(_Site({"https://ada.example": '<a href="/calendar/">Kalender</a>',
                                  "https://ada.example/calendar/": SIMCAL}), "https://ada.example")
    check("a Simple Calendar page is found through its event links",
          (fs["status"], G.calendar_id(fs["ics"] or "")), ("ok", "exampleartsassociation@gmail.com"))

    print()
    print("the walk, end to end, never says the site's key")
    real = (osm.overpass_venues, osm.find_calendar, osm.metros, C._save_ledger)
    real_fc = osm.find_calendar
    C._save_ledger = lambda led: None
    osm.metros = lambda: [{"name": "Seattle", "country": "US", "country_name": "United States",
                           "bbox": (0, 0, 1, 1)}]
    osm.overpass_venues = lambda sess, bbox, lab, **kw: [dict(PH_VENUE)]
    osm.find_calendar = lambda sess, url: real_fc(
        _Site({"https://polishhome.org": PH_HOME, "https://polishhome.org/calendar/": PH_CAL}), url)
    led, out = {"polishhome.org": {"checked": _days_ago(C, C.PROBE_REASK_DAYS),
                                   "name": "Polish Home Association",
                                   "reason": "osm probe: no-calendar", "status": "fail",
                                   "type": "osm-venue"}}, io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            found, sk = C._discover_osm(C._session(), set(), led, 500, {}, metros_per_run=1, workers=1)
    finally:
        (osm.overpass_venues, osm.find_calendar, osm.metros, C._save_ledger) = real
    check("a no-calendar row an older probe wrote 60 days ago no longer parks the centre",
          [cc.get("url") for cc in found.values()], [G.ical_url(PH_ID)])
    check("...and the homepage's row now says what this probe saw, and which calendar",
          {k: led["polishhome.org"].get(k) for k in ("status", "reason", "probe", "cal_key", "checked")},
          {"status": "found", "reason": "osm probe: found ics", "probe": osm.PROBE_VERSION,
           "cal_key": C._canon(G.ical_url(PH_ID)), "checked": C._today_int()})
    check("...so neither the walk nor verify treats it as dead", C._dead_recently(led, "polishhome.org"), False)
    check("...and a group calendar is proposed as it is, with no _home", "_home" in next(iter(found.values())), False)

    def _again(ledger, seen=frozenset()):
        asked = []
        C._save_ledger = lambda led: None
        osm.metros = lambda: [{"name": "Seattle", "country": "US", "country_name": "United States",
                               "bbox": (0, 0, 1, 1)}]
        osm.overpass_venues = lambda sess, bbox, lab, **kw: [dict(PH_VENUE)]
        osm.find_calendar = lambda sess, url: asked.append(url) or real_fc(
            _Site({"https://polishhome.org": PH_HOME, "https://polishhome.org/calendar/": PH_CAL}), url)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                again, sk_again = C._discover_osm(C._session(), set(seen), ledger, 500, {},
                                                  metros_per_run=1, workers=1)
        finally:
            (osm.overpass_venues, osm.find_calendar, osm.metros, C._save_ledger) = real
        return asked, again, sk_again
    # A `found` home is parked for a week: a metro the deadline cut short is
    # resumed the next run, and Berlin's second visit spent ~500 of its 2,443
    # probes re-asking homes the first had answered (2026-10-23).
    fresh = dict(led["polishhome.org"])
    led2 = {"polishhome.org": dict(fresh)}
    asked, again, sk2 = _again(led2)
    check("a `found` home is not asked again within the week, and the row is left as it was",
          (asked, again, sk2.get("probed-recently"), led2["polishhome.org"]), ([], {}, 1, fresh))
    led2 = {"polishhome.org": dict(fresh, checked=_days_ago(C, C.UNREACHABLE_TTL_DAYS))}
    asked, again, _sk = _again(led2)
    check("...after the week it is asked again, and the week starts again",
          (asked, list(again), led2["polishhome.org"]["checked"]),
          (["https://polishhome.org"], [C._canon(G.ical_url(PH_ID))], C._today_int()))
    # Once its calendar is configured the home is answered for as long as it
    # is: the community walk's post-probe `configured` tally was 91-170 a run.
    led2 = {"polishhome.org": dict(fresh, checked=_days_ago(C, 200))}
    asked, again, sk2 = _again(led2, seen={C._canon(G.ical_url(PH_ID))})
    check("a `found` home whose calendar is configured is never asked again, however old",
          (asked, again, sk2.get("configured"), sk2.get("probed-recently")), ([], {}, 1, None))
    check("...and the key is in none of the log, the candidates or the ledger",
          [FAKE_SITE_KEY in s for s in (out.getvalue(), json.dumps(found), json.dumps(led))],
          [False, False, False])

    print()
    print("the venue block reaches the config, and the ics adapter pins with it")
    tmp = tempfile.mkdtemp(prefix="osm-merge-")
    json.dump([], open(os.path.join(tmp, "ics_sources.json"), "w", encoding="utf-8"), indent=1)
    held = dict(osm.to_candidate(PH_VENUE, fs, "Vienna, Austria"), name="An Arts Association")
    curated = {"type": "ics", "name": "A curated club calendar", "category": "community",
               "url": G.ical_url("someclub@gmail.com")}
    path = os.path.join(tmp, "cand.verified.json")
    json.dump([c, held, curated], open(path, "w", encoding="utf-8"))
    keep_here, log = C.HERE, io.StringIO()
    C.HERE = tmp
    try:
        with contextlib.redirect_stdout(log):
            C.cmd_merge(path)
    finally:
        C.HERE = keep_here
    cfg = json.load(open(os.path.join(tmp, "ics_sources.json"), encoding="utf-8"))
    check("merge writes the centre's calendar with its venue block",
          [(e["name"], e.get("venue")) for e in cfg if e["url"] == G.ical_url(PH_ID)],
          [("Polish Home Association", c["venue"])])
    check("a DISCOVERED calendar whose id is a personal address is held for the owner",
          [e["name"] for e in cfg if e["name"] == "An Arts Association"], [])
    check("...and merge says so", "[HOLD]" in log.getvalue() and "An Arts Association" in log.getvalue(), True)
    check("...by name only: the step summary is public, and the URL carries the address",
          ("@" in log.getvalue(), "%40" in log.getvalue()), (False, False))
    check("...while one a person curated (no _found) is written as before",
          [e["name"] for e in cfg if e["name"] == "A curated club calendar"], ["A curated club calendar"])

    src = next(e for e in cfg if e["url"] == G.ical_url(PH_ID))
    when = "20261114T030000Z"
    feed = "\r\n".join([
        "BEGIN:VCALENDAR", "VERSION:2.0",
        "BEGIN:VEVENT", "DTSTART:" + when, "UID:bazaar@google.com", "SUMMARY:Polish Fall Bazaar", "END:VEVENT",
        "BEGIN:VEVENT", "DTSTART:" + when, "UID:away@google.com", "SUMMARY:Choir at the cathedral",
        "LOCATION:St. James Cathedral\\, Seattle", "END:VEVENT",
        "BEGIN:VEVENT", "DTSTART:" + when, "UID:picked@google.com", "SUMMARY:Pierogi night",
        "LOCATION:Polish Home Association\\, 1714 18th Ave\\, Seattle\\, WA 98122\\, USA", "END:VEVENT",
        "END:VCALENDAR", ""])

    class _Store:
        def __init__(self):
            self.events = []

        def upsert(self, ev):
            self.events.append(ev)
            return "new"

    def _ingest(source):
        st, said = _Store(), io.StringIO()
        with patch.object(ICS, "_fetch_ics", return_value=(feed, "200", None)), \
             patch.object(ICS, "make_location_geocoder",
                          side_effect=lambda _s, _x: (lambda _loc: (47.6070, -122.3260))), \
             contextlib.redirect_stdout(said):
            ICS.ingest_ics(st, None, source)
        return {e.name: e for e in st.events}, said.getvalue()

    # AS MERGED: the block holds back what names no place (pin_unnamed false).
    by, said = _ingest(src)
    check("as merged, an event that names no place is NOT drawn on the centre",
          "Polish Fall Bazaar" in by, False)
    check("...and the log says how many a person's review would add",
          "1 of them name no place and the venue block says pin_unnamed false" in said, True)
    if names is not None:
        pk = by.get("Pierogi night")
        check("...while one whose LOCATION names the centre by its street lands on its point",
              pk and (pk.latitude, pk.longitude, pk.address), (47.6169525, -122.30845, None))
    # REVIEWED: a person read the calendar, it is the building's own, and
    # they deleted the key.
    by, said = _ingest(dict(src, venue={k: v for k, v in src["venue"].items() if k != "pin_unnamed"}))
    check("reviewed (the key deleted), the log holds nothing back", "pin_unnamed" in said, False)
    bz = by.get("Polish Fall Bazaar")
    check("an event that names no place lands ON the centre's point",
          bz and (bz.latitude, bz.longitude, bz.venue_name), (47.6169525, -122.30845, "Polish Home Association"))
    check("...with no street, so the sync never re-geocodes it",
          bz and (bz.address, SYNC._addr_parts(dataclasses.asdict(bz))), (None, None))
    check("...and shares the centre's four-decimal key",
          bz and (round(bz.latitude, 4), round(bz.longitude, 4)),
          (round(PH_VENUE["lat"], 4), round(PH_VENUE["lon"], 4)))
    ch = by.get("Choir at the cathedral")
    check("an event that names another place is still geocoded there, not pinned",
          ch and (ch.latitude, ch.longitude), (47.6070, -122.3260))

    print()
    print("a centre calls its programme Programs, Classes, Activities, Termine, What's On")
    for word, want in (("Programs", True), ("Classes", True), ("Activités", True), ("Activiteiten", True),
                       ("Gottesdiensttermine", True), ("Termine", True), ("Diary", True),
                       ("Centre Schedule", True), ("What&#8217;s On", True), ("What's on", True),
                       ("Gemeindetermine", True), ("Kirchengemeindetermine", True),
                       ("Actividades", True), ("Atividades", True), ("Aktivitäten", True), ("Attività", True),
                       ("Determine eligibility", False),
                       ("determine eligibility", False), ("radioactivity", False), ("Our history", False)):
        site = _Site({"https://hall.example": f'<a href="/p1">{word}</a>',
                      "https://hall.example/p1": "<html>nothing machine-readable</html>"})
        osm.find_calendar(site, "https://hall.example")
        check(f"  {word!r} {'is' if want else 'is not'} followed", site.asked[1:], ["https://hall.example/p1"] if want else [])
    order = osm._follow_order({"https://h.example/classes": False, "https://h.example/about/events-policy": True,
                               "https://h.example/events": True, "https://h.example/programs": False,
                               "https://h.example/calendar": True})
    check("a calendar word outranks a programme word, however short its path",
          order, ["https://h.example/events", "https://h.example/calendar",
                  "https://h.example/about/events-policy", "https://h.example/classes",
                  "https://h.example/programs"])
    check("\"What's On\" with its apostrophe, and the what-s-on slug, are calendar words, not programme words",
          osm._follow_order({"https://h.example/classes": False,
                             **osm._cal_links("https://h.example", '<a href="/p">What&#8217;s On</a>'
                                              '<a href="/what-s-on/">Visit</a>')[0]}),
          ["https://h.example/p", "https://h.example/what-s-on/", "https://h.example/classes"])
    check("three of a site's own links are followed, not two", osm.MAX_FOLLOW, 3)
    links = "".join(f'<a href="/{p}">{p}</a>' for p in ("events", "calendar", "whats-on", "programs"))
    site = _Site({"https://hall.example": links})
    osm.find_calendar(site, "https://hall.example")
    check("...the calendar words first", site.asked[1:],
          ["https://hall.example/events", "https://hall.example/calendar", "https://hall.example/whats-on"])

    print()
    print("a calendar page can be where the ticketing link is")
    EB = "https://www.eventbrite.com/e/painting-workshop-tickets-185868657157"
    ORG = "https://www.eventbrite.com/o/the-hall-events-18586865715"
    site = _Site({"https://hall.example": '<a href="/whats-on.html">Whats On</a>',
                  "https://hall.example/whats-on.html": f'<p>The hall</p><a href="{ORG}">All our events</a>'})
    f2 = osm.find_calendar(site, "https://hall.example")
    check("an organiser's Eventbrite page linked from the followed page routes the venue",
          (f2["status"], f2["offsite_url"]), ("offsite:eventbrite", ORG))
    for one in (EB, "https://www.facebook.com/events/1739734338159382/",
                "https://www.meetup.com/hall-group/events/305123456/", "https://dice.fm/event/abc-night"):
        site = _Site({"https://hall.example": '<a href="/whats-on.html">Whats On</a>',
                      "https://hall.example/whats-on.html": f'<p>Workshop</p><a href="{one}">Events: book</a>'})
        f3 = osm.find_calendar(site, "https://hall.example")
        check(f"  ...but ONE event on a followed page is not where its calendar is: {one[:40]}",
              (f3["status"], f3.get("offsite_url")), ("no-calendar", None))
    site = _Site({"https://hall.example": f'<a href="{EB}">Events</a>'})
    check("...while on the homepage itself an event link still routes, as it always did",
          osm.find_calendar(site, "https://hall.example")["status"], "offsite:eventbrite")
    site = _Site({"https://hall.example": '<a href="/whats-on.html">Whats On</a>'
                                          '<a href="https://www.meetup.com/hall-group/events/">Events</a>',
                  "https://hall.example/whats-on.html": f'<a href="{ORG}">Book</a>'})
    check("...the homepage's own link still wins",
          osm.find_calendar(site, "https://hall.example")["offsite_url"], "https://www.meetup.com/hall-group/events/")
    share = ("https://www.facebook.com/sharer/sharer.php?u=https%3a%2f%2fhall.example%2fCommunity%2fEvents")
    site = _Site({"https://hall.example": '<a href="/Community/Events">Events</a>',
                  "https://hall.example/Community/Events": f'<a href="{share}">Share</a>'})
    check("a share button is not where the calendar is",
          osm.find_calendar(site, "https://hall.example")["status"], "no-calendar")
    check("...nor a post, nor the page widget",
          [osm._offsite(u) for u in ("https://www.facebook.com/1423854946413991/posts/1739734338159382",
                                     "https://www.facebook.com/plugins/page.php?href=x",
                                     "https://www.facebook.com/sharer.php?u=x")], [None, None, None])
    check("...while a page's events still are", osm._offsite("https://www.facebook.com/hall/events"), "facebook.com")
    site = _Site({"https://hall.example": '<a href="/whats-on.html">Whats On</a>',
                  "https://hall.example/whats-on.html": f'<a href="{ORG}">Book</a>' + PH_CAL})
    check("...and a calendar on that page beats its ticketing link",
          osm.find_calendar(site, "https://hall.example")["status"], "ok")

    import catalog_discover_civic as civic
    seen_kw = []
    keep_fc = civic.osm.find_calendar
    civic.osm.find_calendar = lambda sess, url, **kw: seen_kw.append(kw) or {"status": "no-calendar"}
    try:
        civic.probe(None, {"name": "Hallton", "url": "https://hallton.example"})
        civic.probe_dmo(None, {"name": "Hallton", "url": "https://hallton.example"}, "https://visit.example")
    finally:
        civic.osm.find_calendar = keep_fc
    check("the civic walk (a city's site, not measured) keeps its two follows",
          [kw.get("max_follow") for kw in seen_kw], [2, 2])

    print()
    print("amenity=social_centre is a centre too")
    check("it is a selector kind, under amenity", osm.kind_keys().get("social_centre"), "amenity")
    check("...proposed as community", osm.KIND_CATEGORY.get("social_centre"), "community")
    try:
        _q = osm._overpass_query("1,2,3,4", ["community_centre", "library", "social_centre"])
    except ValueError as exc:                 # an unknown kind is a loud error
        _q = str(exc)
    check("...and asked for by the pinned community walk",
          'nwr["amenity"~"^(community_centre|library|social_centre)$"]' in _q, True)

    print()
    print("the ledger says which probe gave up, so a better one asks again")
    old_row = {"checked": C._today_int(), "name": "Hall", "reason": "osm probe: no-calendar",
               "status": "fail", "type": "osm-venue"}
    aged = _days_ago(C, C.PROBE_REASK_DAYS)
    led = {"old.example": dict(old_row, checked=aged), "new.example": dict(old_row, probe=osm.PROBE_VERSION),
           "off.example": dict(old_row, reason="osm probe: offsite:eventbrite", checked=aged),
           "ver.example": {"checked": C._today_int(), "status": "fail", "type": "jsonld",
                           "reason": "no schema.org Event blocks found"}}
    led.update({"offnew.example": dict(old_row, reason="osm probe: offsite:eventbrite", probe=osm.PROBE_VERSION),
                "ref.example": dict(old_row, reason="osm probe: robots.txt Disallow: /", status="refused"),
                "young.example": dict(old_row, checked=_days_ago(C, C.PROBE_REASK_DAYS - 1)),
                "youngoff.example": dict(old_row, reason="osm probe: offsite:eventbrite",
                                         checked=_days_ago(C, 12))})
    check("a no-calendar row an older probe wrote has lapsed at 60 days", C._dead_recently(led, "old.example"), False)
    check("...one this probe wrote has not", C._dead_recently(led, "new.example"), True)
    check("...an offsite verdict an older probe wrote has lapsed too: the probe reads more before "
          "it says offsite", C._dead_recently(led, "off.example"), False)
    check("...but not one this probe wrote, a robots.txt refusal or a verify's",
          [C._dead_recently(led, k) for k in ("offnew.example", "ref.example", "ver.example")], [True, True, True])
    # Re-asked v1 rows verified 1.0% (99 of 9,898) against 3.0-4.7% per probe
    # on new ground (2026-10-23), so the new ground goes first.
    check("...nor an older probe's verdict younger than 60 days: new ground goes first",
          [C._dead_recently(led, k) for k in ("young.example", "youngoff.example")], [True, True])
    real = (osm.overpass_venues, osm.find_calendar, osm.metros, C._save_ledger)
    asked = []
    C._save_ledger = lambda led: None
    osm.metros = lambda: [{"name": "Hallton", "country": "GB", "bbox": (0, 0, 1, 1)}]
    osm.overpass_venues = lambda sess, bbox, lab, **kw: [
        {"url": f"https://{h}", "name": h, "kind": "community_centre"}
        for h in ("old.example", "new.example", "young.example", "home.example", "redir.example")]
    GEMBED = "https://calendar.google.com/calendar/embed?src=someone%40gmail.com"

    def _fc(sess, url):
        asked.append(url)
        # home.example's probe read nothing but its homepage; redir.example's
        # "Calendar" link redirected to a personal calendar's Google page
        return {"status": "no-calendar",
                "cal_url": url if "home." in url else GEMBED if "redir." in url else url + "/whats-on"}
    osm.find_calendar = _fc
    try:
        C._discover_osm(C._session(), set(), led, 500, {}, metros_per_run=1, workers=1)
    finally:
        (osm.overpass_venues, osm.find_calendar, osm.metros, C._save_ledger) = real
    check("the walk re-asks the lapsed centre and only that one (and the new ones)", asked,
          ["https://old.example", "https://home.example", "https://redir.example"])
    check("a page that carries an address is never written down as the page read",
          ("cal_url" in led["redir.example"], C._public_url(GEMBED)),
          (False, "https://calendar.google.com/(an address)"))
    check("...and writes down which probe it was, and the page it read",
          {k: led["old.example"].get(k) for k in ("probe", "cal_url", "reason")},
          {"probe": osm.PROBE_VERSION, "cal_url": "https://old.example/whats-on",
           "reason": "osm probe: no-calendar"})
    check("...but not the homepage as that page: it is the row's own key",
          ("cal_url" in led["home.example"], led["home.example"].get("probe")), (False, osm.PROBE_VERSION))

    print()
    print("verify asks the walk's question, so a lapsed row does not drop the walk's find")
    hall = {"type": "tribe", "name": "Hall", "category": "community", "base_url": "https://hall.example.org",
            "_found": "osm:community_centre -> tribe"}
    lapsed = {"checked": _days_ago(C, C.PROBE_REASK_DAYS), "name": "Hall", "status": "fail",
              "reason": "osm probe: no-calendar", "type": "osm-venue"}
    vled = {"hall.example.org": dict(lapsed)}
    tmp2 = tempfile.mkdtemp(prefix="osm-verify-")
    cpath = os.path.join(tmp2, "cand.json")

    def _verify(cands, ledger, api_key=None, ok=(True, "12 vevents / 9 future")):
        json.dump(cands, open(cpath, "w", encoding="utf-8"))
        said = io.StringIO()
        with patch.object(C, "_load_ledger", return_value=ledger), \
             patch.object(C, "_save_ledger", lambda led: None), \
             patch.object(C, "_config_keys", return_value=set()), \
             patch.object(C, "_robots_gate", return_value=None), \
             patch.object(G, "api_key", return_value=api_key), \
             patch.dict(C.VERIFIERS, {"tribe": lambda s, e: ok, "ics": lambda s, e: ok}), \
             contextlib.redirect_stdout(said):
            C.cmd_verify(cpath)
        return (said.getvalue(),
                json.load(open(os.path.splitext(cpath)[0] + ".verified.json", encoding="utf-8")))
    said, passed = _verify([hall], vled)
    check("a lapsed no-calendar row under the candidate's own key is not 'known-dead'",
          ("known-dead" in said, [e["name"] for e in passed]), (False, ["Hall"]))
    said, _p = _verify([hall], {"hall.example.org": dict(lapsed, probe=osm.PROBE_VERSION)})
    check("...while one this probe wrote still is", "known-dead" in said, True)
    said, _p = _verify([hall], {"hall.example.org": dict(lapsed, checked=_days_ago(C, 10))})
    check("...and so is an older probe's under 60 days old, as the walk would have said",
          "known-dead" in said, True)

    print()
    print("a personal address is held, and is in nothing committed or public")
    ada = _Site({"https://ada.example": '<a href="/calendar/">Kalender</a>',
                 "https://ada.example/calendar/": SIMCAL})
    real = (osm.overpass_venues, osm.find_calendar, osm.metros, C._save_ledger)
    C._save_ledger = lambda led: None
    osm.metros = lambda: [{"name": "Vienna", "country": "AT", "country_name": "Austria", "bbox": (0, 0, 1, 1)}]
    osm.overpass_venues = lambda sess, bbox, lab, **kw: [
        {"name": "An Arts Association", "url": "https://ada.example", "kind": "community_centre",
         "lat": 48.2, "lon": 16.37}]
    osm.find_calendar = lambda sess, url: real_fc(ada, url)
    hled, wlog = {}, io.StringIO()
    try:
        with contextlib.redirect_stdout(wlog):
            hfound, _sk = C._discover_osm(C._session(), set(), hled, 500, {}, metros_per_run=1, workers=1)
    finally:
        (osm.overpass_venues, osm.find_calendar, osm.metros, C._save_ledger) = real
    hc = list(hfound.values())
    check("the walk proposes it for verify to judge, with the homepage the owner will need",
          [(x["name"], x.get("_home")) for x in hc], [("An Arts Association", "https://ada.example")])
    said, passed = _verify(hc, hled, api_key="test-key")
    hkey = C._held_key(hc[0])
    check("verify judges it and holds it: not passed on to merge", ("[HELD]" in said, passed), (True, []))
    check("...its ledger row is keyed by a hash and names the venue, never the id",
          {k: hled.get(hkey, {}).get(k) for k in ("status", "name", "home", "metro")},
          {"status": "held", "name": "An Arts Association", "home": "https://ada.example",
           "metro": "Vienna, Austria"})
    blob = json.dumps(hled) + said + wlog.getvalue()
    check("...and the address is in no ledger key or value, nor the walk's or verify's log",
          ("@gmail" in blob.lower(), "%40gmail" in blob.lower(), "exampleartsassociation" in blob.lower()),
          (False, False, False))
    with patch.object(C, "_load_ledger", return_value=hled), contextlib.redirect_stdout(io.StringIO()) as lh:
        C.cmd_ledger_held()
    check("`ledger --held` is the owner's list: the venue, its homepage, no address",
          ("An Arts Association" in lh.getvalue(), "https://ada.example" in lh.getvalue(), "@" in lh.getvalue()),
          (True, True, False))
    said, _p = _verify(hc, hled, api_key="test-key")
    check("verify does not ask again for one the owner already has", "held for the owner" in said, True)
    check("the homepage's `found` row names the held calendar by its hash, never its address",
          (hled.get("ada.example") or {}).get("cal_key"), hkey)
    # Past the homepage's week, so the walk reaches the held list at all.
    hled["ada.example"]["checked"] = _days_ago(C, C.UNREACHABLE_TTL_DAYS)
    osm.metros = lambda: [{"name": "Vienna", "country": "AT", "country_name": "Austria", "bbox": (0, 0, 1, 1)}]
    osm.overpass_venues = lambda sess, bbox, lab, **kw: [
        {"name": "An Arts Association", "url": "https://ada.example", "kind": "community_centre",
         "lat": 48.2, "lon": 16.37}]
    osm.find_calendar = lambda sess, url: real_fc(ada, url)
    C._save_ledger = lambda led: None
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            again, sk2 = C._discover_osm(C._session(), set(), hled, 500, {}, metros_per_run=1, workers=1)
    finally:
        (osm.overpass_venues, osm.find_calendar, osm.metros, C._save_ledger) = real
    check("...nor does the walk propose it again", (again, sk2.get("held")), ({}, 1))
    # The owner adds it by hand: the config names its URL, the homepage's row
    # only the hash, and the home is answered from then on, not asked weekly.
    hled["ada.example"]["checked"] = _days_ago(C, C.UNREACHABLE_TTL_DAYS)
    hasked = []
    osm.metros = lambda: [{"name": "Vienna", "country": "AT", "country_name": "Austria", "bbox": (0, 0, 1, 1)}]
    osm.overpass_venues = lambda sess, bbox, lab, **kw: [
        {"name": "An Arts Association", "url": "https://ada.example", "kind": "community_centre",
         "lat": 48.2, "lon": 16.37}]
    osm.find_calendar = lambda sess, url: hasked.append(url) or real_fc(ada, url)
    C._save_ledger = lambda led: None
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            _f3, sk3 = C._discover_osm(C._session(), {C._canon(hc[0]["url"])}, hled, 500, {},
                                       metros_per_run=1, workers=1)
    finally:
        (osm.overpass_venues, osm.find_calendar, osm.metros, C._save_ledger) = real
    check("once the owner configures a held calendar by hand, its home is answered, not asked weekly",
          (hasked, sk3.get("configured"), sk3.get("probed-recently")), ([], 1, None))
    fled = {}
    said, _p = _verify(hc, fled, api_key="test-key", ok=(False, "0 vevents / 0 future"))
    check("a held calendar that fails verify is parked under the same hashed key",
          ({k: fled.get(hkey, {}).get(k) for k in ("status", "home")}, C._dead_recently(fled, hkey)),
          ({"status": "fail", "home": "https://ada.example"}, True))
    check("...still with no address in the ledger", "gmail" in json.dumps(fled).lower(), False)

    print()
    print("a widened walk keeps its place")
    cur = {"osm": {"metro_key": "NZ:Auckland"},
           "osm|community_centre,library": {"metro": 205, "metro_key": "US:Kansas City", "stuck": {}},
           "osm|club": {"metro_key": "GB:Leeds"}}
    with contextlib.redirect_stdout(io.StringIO()):
        slot = C._pinned_slot(cur, ("community_centre", "library", "social_centre"))
    check("the new slot starts where the narrower walk had got to",
          (slot, cur.get(slot, {}).get("metro_key")), ("osm|community_centre,library,social_centre", "US:Kansas City"))
    check("...the narrower one is left as it was", cur["osm|community_centre,library"]["metro_key"], "US:Kansas City")
    cur.setdefault(slot, {})["metro_key"] = "US:Omaha"
    C._pinned_slot(cur, ("community_centre", "library", "social_centre"))
    check("...and once it has its own place, that is the place", cur[slot]["metro_key"], "US:Omaha")
    fresh = {"osm|club": {"metro_key": "GB:Leeds"}, "osm": {"metro_key": "NZ:Auckland"}}
    C._pinned_slot(fresh, ("community_centre", "social_centre"))
    check("a walk that widens nothing starts at the top, as before",
          fresh.get("osm|community_centre,social_centre"), None)


class _ByteSite(_Site):
    """A site whose pages are BYTES, answered the way requests answers them:
    text/html with no charset in the header is decoded as ISO-8859-1."""

    def get(self, u, timeout=None, allow_redirects=True, **kw):
        import requests
        from requests.structures import CaseInsensitiveDict
        self.asked.append(u)
        if u not in self.pages:
            raise ConnectionError(u)
        r = requests.Response()
        r.status_code, r.url, r._content = 200, u, self.pages[u]
        r.headers = CaseInsensitiveDict({"Content-Type": "text/html"})
        r.encoding = requests.utils.get_encoding_from_headers(r.headers)
        return r


def intl_calendar_words():
    """The walks' other languages, as the non-US no-calendar sample named its
    calendars (2026-10-25: 14 of 56 readable homepages), read as a fallback
    so no find the old words make moves (14 of 255 configured calendars did,
    read in one pass); the calendars that are not a programme; "prevent";
    a charset only the page declares; Event Organiser."""
    print()
    print("a calendar is named in the walks' other languages too")
    for href, text, src in (
            ("/wydarzenia/", "Wydarzenia", "biblioteka.wroc.pl"),
            ("/index.php?page=kalendarz-wydarzen", "Kalendarz wydarzeń", "kultura.miekinia.pl"),
            ("/akce", "Aktuální akce", "knihovna.ricany.cz"),
            ("/si/lokalno/dogodki/", "Dogodki", "rogaska-slatina.si"),
            ("/mesecni-koledar/", "KOLEDAR", "knjiznica-radovljica.si"),
            ("/is/mannlif/vidburdir", "Viðburðir", "olfus.is"),
            ("/hva-skjer", "Hva skjer", "deichman.no"),
            ("/tapahtumakalenteri", "Tapahtumakalenteri", "hnmky.fi"),
            ("/esityskalenteri", "Esityskalenteri", "nukketeatterisampo.fi"),
            ("/kalendarium/", "Evenemang", "confidencen.se"),
            ("/main/p1.html", "イベントカレンダー", "city.kyoto.lg.jp's link text"),
            ("/kurashi/p2.html", "催し・イベント", "a city's link text")):
        site = _Site({"https://hall.example": f'<a href="{href}">{text}</a>',
                      "https://hall.example" + href: "<html>nothing machine-readable</html>"})
        osm.find_calendar(site, "https://hall.example")
        check(f"  {text!r} ({src}) is followed", site.asked[1:], ["https://hall.example" + href])
    for href, text in (("/redakce", "Redakce"), ("/reakce", "Reakce na podněty"),
                       ("/objava/1207279", "SVET ZA PREVENTIVO IN VZGOJO V CESTNEM PROMETU"),
                       ("/documents/bousai.pdf", "Leaflet for Disaster Prevention Information")):
        site = _Site({"https://hall.example": f'<a href="{href}">{text}</a>'})
        osm.find_calendar(site, "https://hall.example")
        check(f"  {text!r} is not", site.asked[1:], [])

    TEC = '<link rel="stylesheet" href="/wp-content/plugins/the-events-calendar/x.css">'
    home = ('<a href="/kalender/">Kalender</a>'
            '<a href="/wydarzenia/">Wydarzenia</a>')
    site = _Site({"https://hall.example": home,
                  "https://hall.example/kalender/": "<html>a hand-made table</html>",
                  "https://hall.example/wydarzenia/": TEC})
    f = osm.find_calendar(site, "https://hall.example")
    check("the fallback is read once the old words found nothing",
          (site.asked[1:], f["status"], f["adapter"]),
          (["https://hall.example/kalender/", "https://hall.example/wydarzenia/"], "ok", "tribe"))
    site = _Site({"https://hall.example": home,
                  "https://hall.example/kalender/": TEC,
                  "https://hall.example/wydarzenia/": TEC})
    f = osm.find_calendar(site, "https://hall.example")
    check("...and never once they found something, so a configured find keeps its key",
          (site.asked[1:], f["cal_url"]), (["https://hall.example/kalender/"], "https://hall.example/kalender/"))
    em = '<link rel="stylesheet" href="/wp-content/plugins/events-manager/x.css">'
    site = _Site({"https://muzeum.example": em + '<a href="/wydarzenia/">Wydarzenia</a>',
                  "https://muzeum.example/?ical=1": "BEGIN:VCALENDAR\r\nEND:VCALENDAR",
                  "https://muzeum.example/wydarzenia/?ical=1": "BEGIN:VCALENDAR\r\nEND:VCALENDAR",
                  "https://muzeum.example/wydarzenia/": em})
    f = osm.find_calendar(site, "https://muzeum.example")
    check("...nor once the homepage shows its platform (muzeumwarszawy.pl's /?ical=1 stays its key)",
          (f["status"], f["ics"]), ("ok-homepage", "https://muzeum.example/?ical=1"))
    links = "".join(f'<a href="/{p}">{p}</a>' for p in ("events", "wydarzenia", "akce", "dogodki"))
    site = _Site({"https://hall.example": links})
    osm.find_calendar(site, "https://hall.example")
    check("...after the old words' links, and one page at most (60 requests of 790 found nothing usable)",
          site.asked[1:], ["https://hall.example/events", "https://hall.example/akce"])
    site = _Site({"https://hall.example": links.replace('<a href="/events">events</a>', "")})
    osm.find_calendar(site, "https://hall.example")
    check("...one, even when the old words left all three follows", site.asked[1:], ["https://hall.example/akce"])
    site = _Site({"https://hall.example": links + '<a href="/kalender">Kalender</a>'})
    osm.find_calendar(site, "https://hall.example", max_follow=2)
    check("...and none when the old words take the civic walk's two", site.asked[1:],
          ["https://hall.example/events", "https://hall.example/kalender"])

    print()
    print("a waste, booking or meeting calendar is not a programme")
    for href, text, src in (
            ("/Buergerservice/Muellkalender", "Müllkalender", "ebensee.at"),
            ("/tenester/avfall-og-gjenvinning/", "Avfall og gjenvinning Hentekalender, gjenvinningsstasjon",
             "surnadal.kommune.no"),
            ("/motekalender-etter-28-04-23/", "Politiske saker", "tynset.kommune.no"),
            ("/juhlat-merimelojien-majalla/majan-varauskalenteri/", "Majan varauskalenteri", "merimelojat.fi"),
            ("/koripallo/harjoittelutilakalenteri", "Harjoittelutilakalenteri", "hnmky.fi"),
            ("/buchungskalender/", "Buchungskalender", "igl-hamburg.de"),
            ("/objava/57198", "Koledar odvoza odpadkov", "zelezniki.si"),
            ("/wp-booking-calendar/", "Booking", "bakara.pl")):
        site = _Site({"https://hall.example": f'<a href="{href}">{text}</a>'})
        osm.find_calendar(site, "https://hall.example")
        check(f"  {src}'s {text[:30]!r} is not followed", site.asked[1:], [])
    for href, text in (("/Veranstaltungskalender_17", "Veranstaltungskalender"), ("/termine", "Termine"),
                       ("/events-calendar", "Events calendar"), ("/robins-calendar", "Robins Calendar")):
        site = _Site({"https://hall.example": f'<a href="{href}">{text}</a>'})
        osm.find_calendar(site, "https://hall.example")
        check(f"  ...while {text!r} still is", site.asked[1:], ["https://hall.example" + href])
    site = _Site({"https://hall.example": '<a href="/Buergerservice/Muellkalender">Müllkalender</a>'
                                          '<a href="/Unser_Ebensee/Veranstaltungen">Veranstaltungen</a>'
                                          '<a href="/meineseite/veranstaltungen/detail">Meine Veranstaltungen</a>'})
    osm.find_calendar(site, "https://hall.example", max_follow=2)
    check("ebensee.at's Müllkalender no longer spends one of the civic walk's two follows",
          site.asked[1:], ["https://hall.example/Unser_Ebensee/Veranstaltungen",
                           "https://hall.example/meineseite/veranstaltungen/detail"])
    site = _Site({"https://hall.example": '<a href="https://city.perfectmind.com/23/Clients/BookMe4">'
                                          'Booking calendar</a>'})
    check("...and a booking platform's link is still a routing signal",
          osm.find_calendar(site, "https://hall.example")["status"], "offsite:perfectmind.com")

    print()
    print("a page that declares its charset only in a <meta> is read in it")
    body = ('<html><head><meta charset="utf-8"></head><body>'
            '<a href="/main/p1.html">イベントカレンダー</a></body></html>').encode("utf-8")
    site = _ByteSite({"https://city.example": body, "https://city.example/main/p1.html": b"<html></html>"})
    r = site.get("https://city.example")
    check("requests reads such a page as Latin-1 (city.kyoto.lg.jp's header names no charset)",
          ("イベント" in r.text, r.encoding), (False, "ISO-8859-1"))
    check("...so it is decoded as the page declares",
          ("イベント" in osm._read_as_declared(r).text, r.encoding), (True, "utf-8"))
    site.asked = []
    osm.find_calendar(site, "https://city.example")
    check("...and its Japanese link is followed", site.asked, ["https://city.example", "https://city.example/main/p1.html"])
    sjis = ('<meta http-equiv="Content-Type" content="text/html; charset=Shift_JIS">'
            '<a href="/p">催し</a>').encode("shift_jis")
    check("an http-equiv Shift_JIS page too",
          "催し" in osm._read_as_declared(_ByteSite({"https://c.example": sjis}).get("https://c.example")).text, True)

    import requests
    from requests.structures import CaseInsensitiveDict

    def resp(content, ctype):
        x = requests.Response()
        x._content, x.status_code = content, 200
        x.headers = CaseInsensitiveDict({"Content-Type": ctype})
        x.encoding = requests.utils.get_encoding_from_headers(x.headers)
        return x
    check("a charset in the header wins",
          osm._read_as_declared(resp(body, "text/html; charset=iso-8859-1")).encoding, "iso-8859-1")
    check("...no <meta> leaves it as it was",
          osm._read_as_declared(resp(b"<html>x</html>", "text/html")).encoding, "ISO-8859-1")
    check("...and nor does a charset Python cannot name",
          osm._read_as_declared(resp(b'<meta charset="x-no-such-zz">', "text/html")).encoding, "ISO-8859-1")
    fake = _Site({"https://h.example": "<html></html>"}).get("https://h.example")
    check("a response with no bytes (this file's own fakes) is returned as it is",
          osm._read_as_declared(fake) is fake, True)

    print()
    print("Event Organiser is found the way My Calendar is")
    # kre-museum.jp's homepage, trimmed: the plugin's stylesheet is on every page.
    EO = ('<html><head><link rel="stylesheet" href="//kre.example/wp-content/plugins/event-organiser/'
          'css/eventorganiser-front-end.css"></head><body><a href="/about/">About</a></body></html>')
    check("its stylesheet is the sign", osm.fingerprint(EO)[0], ["event-organiser"])
    check("...and its adapter is ics", osm.adapter_for(["event-organiser"]), "ics")
    site = _Site({"https://kre.example": EO,
                  "https://kre.example/?feed=eo-events": "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nEND:VCALENDAR"})
    f = osm.find_calendar(site, "https://kre.example")
    check("its whole-calendar export is proved and proposed (toyota-sentan.jp, kre-museum.jp)",
          (f["status"], f["ics"]), ("ok-homepage", "https://kre.example/?feed=eo-events"))
    site = _Site({"https://kre.example": EO})
    f = osm.find_calendar(site, "https://kre.example")
    check("...and an export that does not answer is not",
          (f["ics"], osm.why_no_candidate(f)), (None, "ics-without-feed(event-organiser)"))

    print()
    print("Event Organiser's export is the site root's, whichever page the venue names")
    # depana.org, 2026-10-25: the root's export is 443 VEVENTs, a subpage's
    # spelling of it a VCALENDAR with none, which a probe would take for a feed.
    VCAL = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:1\r\nEND:VEVENT\r\nEND:VCALENDAR"
    EMPTY = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nEND:VCALENDAR"
    net = {"https://net.example/a/": EO, "https://net.example/b/": EO,
           "https://net.example/?feed=eo-events": VCAL,
           "https://net.example/a/?feed=eo-events": EMPTY,
           "https://net.example/b/?feed=eo-events": EMPTY}
    keys = []
    for home in ("https://net.example/a/", "https://net.example/b/"):
        f = osm.find_calendar(_Site(net), home)
        cand = osm.to_candidate({"name": home, "kind": "place_of_worship"}, f)
        keys.append(cand and cand["url"])
    check("two venues on subpages of one site propose its one export, from the root",
          keys, ["https://net.example/?feed=eo-events"] * 2)
    site = _Site(dict(net))
    osm.find_calendar(site, "https://net.example/a/")
    check("...and the subpage's spelling is never asked",
          [u for u in site.asked if "feed=" in u], ["https://net.example/?feed=eo-events"])
    MC = ('<html><link rel="stylesheet" href="/wp-content/plugins/my-calendar/css/x.css"></html>')
    site = _Site({"https://mc.example/pcfwp/": MC,
                  "https://mc.example/pcfwp/?feed=my-calendar-ics": VCAL})
    check("My Calendar's is still the homepage's (photofri.ch/pcfwp/ is configured so)",
          osm.find_calendar(site, "https://mc.example/pcfwp/")["ics"],
          "https://mc.example/pcfwp/?feed=my-calendar-ics")

    print()
    print("a plugin that outranks the others for its export does not when the export does not answer")
    LD = ('<script type="application/ld+json">{"@context":"https://schema.org","@type":"Event",'
          '"name":"Konzert","startDate":"2026-11-20T19:00","location":{"@type":"Place",'
          '"name":"Hall","address":"Main St 1"}}</script>')
    MEC = '<div class="mec-event-article"></div>'
    v = {"name": "Hall", "url": "https://hall.example", "kind": "arts_centre", "lat": 1.0, "lon": 2.0}

    def hall(extra, plugin=EO, feed=None, feed_url="https://hall.example/?feed=eo-events"):
        page = "<html>" + plugin + extra + '<a href="/events/">Events</a></html>'
        pages = {"https://hall.example": page, "https://hall.example/events/": page}
        if feed:
            pages[feed_url] = feed
        f = osm.find_calendar(_Site(pages), "https://hall.example")
        return f, osm.to_candidate(v, f)
    f, cand = hall(LD)
    check("Event Organiser with the page's own Event block, no export: a jsonld candidate",
          (f["labels"], f["adapter"], cand and cand["type"]),
          (["event-organiser", "jsonld-event"], "jsonld", "jsonld"))
    f, cand = hall(MEC)
    check("...with Modern Events, the adapter Modern Events names (jsonld), as before Event Organiser was read",
          f["adapter"], "jsonld")
    f, cand = hall(LD, plugin=MC, feed_url="https://hall.example/?feed=my-calendar-ics")
    check("...and My Calendar the same",
          (f["adapter"], cand and cand["type"]), ("jsonld", "jsonld"))
    f = osm.find_calendar(_Site({"https://hall.example": "<html>" + EO + LD + "</html>"}),
                          "https://hall.example")
    check("...and the same on a homepage that links no calendar page",
          (f["status"], f["adapter"], (osm.to_candidate(v, f) or {}).get("type")),
          ("ok-homepage", "jsonld", "jsonld"))
    f, cand = hall(LD, feed=VCAL)
    check("an export that answers still outranks the page's Event block",
          (f["adapter"], cand and cand["url"]), ("ics", "https://hall.example/?feed=eo-events"))
    check("a plugin alone keeps its ics-without-feed reason",
          osm.settled_adapter(["event-organiser"], None), "ics")
    check("...and so does one beside another ics platform with no feed",
          osm.settled_adapter(["event-organiser", "trumba"], None), "ics")


if __name__ == "__main__":
    sys.exit(main())
