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
  * WIX ROUTES EVENTS THROUGH /event-info/, not /event/<slug>/. Proposing the
    WordPress shape gives a link_pattern that cannot fire.
  * A PLATFORM CAN IMPLY A FEED URL. My Calendar publishes iCal at a fixed path
    and never links to it, so scraping for an .ics href finds nothing and a
    readable site looks unreadable.
"""
import re
import sys

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
    check("a Wix find routes through /event-info/, not /event/<slug>/",
          (wix["url_template"], wix["link_pattern"]),
          ("/event-info/{}", r'"slug":"([a-zA-Z0-9-]+)"'))
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

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: " + "; ".join(FAILURES))
        return 1
    print("all osm discovery checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
