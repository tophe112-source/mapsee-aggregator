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
        C._discover_osm(C._session(), set(), {}, 500, cur, metros_per_run=3,
                        deadline=1000.0 + 20)              # 20 venues of budget
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

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: " + "; ".join(FAILURES))
        return 1
    print("all osm discovery checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
