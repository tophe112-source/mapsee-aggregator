#!/usr/bin/env python3
"""
test_ingest_jsonld.py — the generic schema.org Event importer, against JSON-LD
taken verbatim from a live WP Event Manager page (The Royal Room, Seattle).

Prints one line per case and exits non-zero on failure, like the other 16.

The generic adapter had read this family of sites correctly by ACCIDENT, and
accident is not a property you can keep. What is pinned here is what was
measured on all 95 of that site's event pages and what would have gone wrong:

  * SCHEMA.ORG SAYS `location`; WP EVENT MANAGER WRITES `Location`. Every one of
    the 95 pages capitalises it, along with `Organizer`. `.get("location")`
    returned None, the config's venue block filled the gap, and the pin was
    right — for exactly as long as the site went on saying nothing. Read the key
    case-insensitively and the placeholder rule below decides what it was worth.
  * A PLACEHOLDER IS NOT AN ADDRESS. All 95 carry {"name": "-", "address": "-"},
    which is what the CMS renders for a location nobody filled in. "-" is
    TRUTHY, so once the key is read it sails through `if not parts.get(k)`, the
    venue block never fills the gap, and "-" reaches the geocoder as a street.
    Same rule as the Squarespace default pin: a location with no address TEXT is
    not a location, and the answer is the config's venue, not a coordinate
    blocklist.
  * THE VENUE BLOCK FILLS, IT DOES NOT OVERRIDE. A site that grows a real
    address later must be believed over the config.
  * startDate IS NAIVE LOCAL TIME. "2026-08-19 19:30:00", no offset, on every
    page — and a space separator, not a `T`. It is only correct once the venue's
    coordinates give the sync a timezone; read as UTC a 7:30pm show is served at
    12:30pm. The round trip is asserted here because the two halves live in
    different files and neither alone can be wrong in a visible way.
  * A VENUE CALENDAR CARRIES THINGS THAT ARE NOT EVENTS. "CLOSED FOR
    MAINTENANCE" and "Closed for Private Event" are event_listing posts with
    real dates, because a notice is the only thing that CMS can put on a
    calendar. 5 of the 95. Nothing downstream can tell them from a gig, so they
    would reach the map as music and tell somebody a shut venue is open.
    skip_title is matched on the NAME, anchored: the other tell, a 00:00 start,
    would also throw away a New Year's Eve show.

Also pinned, from a second family, MODERN EVENTS CALENDAR, three things
measured 2026-10-04 that the adapter had wrong:

  * "price": "0" IS A COST NOBODY ENTERED. 39 of 39 sampled MEC event pages with
    that zero print no cost at all, and 188 of 388 upcoming events (224 of 455
    listed) on 20 configured MEC calendars carried it; every one read as an
    exact free offer ("Free to attend.", then offer:free and isAccessibleForFree
    downstream), "$10 drop-ins" yoga included. On a page with MEC's markup the
    zero is dropped; a typed cost is not. The row is sent with source_details {}
    so the sync clears what an earlier run stored, and MEC's own printed "Free"
    beside the one event on an event page still counts.
  * A TIMED startDate IS THE WALL CLOCK PRINTED AS IF IT WERE UTC. Whitehorn's
    6:45 pm yoga is "12:45:00-06:00"; Ateliertheater's 19:30 play is
    "21:30+02:00" before 25 Oct and "20:30+01:00" after. 14 of 15 pages were
    late by exactly the site's offset. Read back as the naive wall clock, the
    sync's venue timezone gives the right instant on both sides of the change.
  * ONE URL WITH SEVERAL DATES WAS ONE ROW. The Florrie gives every Tuesday's
    yoga the same url; the store's source_id lookup folded 91 events into 21
    rows, each keyed to its last date and timed at its first.

And, measured 2026-10-25, what kept two sites out and 31 configured hosts
read faster than they ask:

  * ROBOTS.TXT'S CRAWL-DELAY IS HONOURED, PER HOST AND ACROSS SITES. 31 of the
    835 configured sites sit on a host asking 2-150 s and were read at 1 s;
    Askim Kulturhus (10 s) was left out for it. A wait over 30 s is not slept,
    the page is not asked (GRAAP: 150 s), and what robots.txt refuses is not
    asked either.
  * A NAIVE STAMP CAN BE UTC. Tartu Kultuuriaken's 10:00 is the 12:00 its page
    prints; `naive_is_utc` + `timezone` reads it so, on both sides of a DST change.
  * A streetAddress printed as LINES is a street and a town, and a hall named
    as a list is its lines. Abroad, a street and a town with no geo are still
    not geocoded here: an unchecked Photon answer was the wrong street.

The live block below keeps the date it was captured with, because its SHAPE is
what is being pinned — but every call that runs it through to_event rolls that
date forward first. to_event drops anything before today, so a fixture dated the
afternoon it was written passes that afternoon and is wrong every day after:
test_osm_food.py went red every Saturday for the same reason. The two DST
assertions keep their literal dates deliberately, because _to_utc_if_naive has
no such filter and a timezone conversion is the one thing that must give the
same answer for ever.
"""
import re
import sys
from datetime import datetime, timedelta, timezone

import contextlib
import io
import json
from unittest.mock import patch

import mapsee_ingest_jsonld
from mapsee_ingest_jsonld import (
    _ld_get, _meaningful, _address_parts, _parse_ld, _is_event, to_event,
    _mec_unset_price, _mec_wall_clock,
)
from mapsee_admission import normalize_admission_facts
from mapsee_supabase_sync import _to_utc_if_naive
from mapsee_supabase_sync import to_row, needs_detail_sync
from mapsee_ingest import EventStore, make_fingerprint
import os
import tempfile

FAILURES = []


def check(label, got, want):
    ok = got == want
    print(("  ok   " if ok else "  FAIL ") + label + ("" if ok else f"\n         got {got!r}\n        want {want!r}"))
    if not ok:
        FAILURES.append(label)


def check_true(label, got):
    check(label, bool(got), True)


# --- fixtures: verbatim from https://theroyalroomseattle.com/event/... -------
# Trimmed only in `description`; every key, its spelling and its case are as the
# live pages emit them.
LIVE_LD = '''
{"@context":"http:\\/\\/schema.org\\/","@type":"Event","description":"<p><strong>Doors:<\\/strong> 6:30pm<\\/p>",
"name":"Trio Reunion","image":"https:\\/\\/theroyalroomseattle.com\\/wp-content\\/uploads\\/2026\\/05\\/trio.jpg",
"startDate":"2026-08-19 19:30:00","endDate":"","performer":"",
"eventAttendanceMode":"OfflineEventAttendanceMode","eventStatus":"EventScheduled",
"Organizer":{"@type":"Organization","name":""},
"Location":{"@type":"Place","name":"-","address":"-"}}
'''

# The config entry for the site, as jsonld_sources.json holds it.
VENUE = {
    "name": "The Royal Room",
    "address": "5000 Rainier Ave S",
    "city": "Seattle",
    "region": "WA",
    "postal_code": "98118",
    "country": "US",
    "lat": 47.5569023,
    "lon": -122.2842722,
}
SKIP_RX = re.compile(r"^\s*closed\b", re.I)

# Always comfortably in the future, whenever this is run.
_D = datetime.now(timezone.utc) + timedelta(days=180)
SOON = _D.strftime("%Y-%m-%d 19:30:00")
SOON_MIDNIGHT = _D.strftime("%Y-%m-%d 00:00:00")
SOON_EVENING = _D.strftime("%Y-%m-%d 20:00:00")


def no_geocode(*a, **k):
    raise AssertionError("the venue block should have placed this without a lookup")


# --- Modern Events Calendar: "price": "0" is an EMPTY cost field ------------
# Both blocks verbatim from live MEC Pro 7.36 pages on 2026-10-04 (description
# trimmed), dates rolled forward like LIVE_LD. Whitehorn's yoga costs "$10
# drop-ins" and its page prints no Cost line; MEC still writes "0". The Florrie's
# tea dance has a cost set, and the page prints "£5.00" beside the block's "5".
MEC_YOGA = {
    "@context": "http://schema.org", "@type": "Event",
    "eventStatus": "https://schema.org/EventScheduled",
    "startDate": "2026-10-06T12:45:00-06:00", "endDate": "2026-10-06T14:30:00-06:00",
    "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode",
    "location": {"@type": "Place", "name": "WCA Main Hall", "image": "",
                 "address": "228 Whitehorn Rd NE, Calgary, AB"},
    "organizer": {"@type": "Person", "name": "", "url": ""},
    "offers": {"url": "https://www.whitehorncommunity.com/events/yoga-with-vic/",
               "price": "0", "priceCurrency": "USD",
               "availability": "https://schema.org/InStock", "validFrom": "2026-10-06T00:00"},
    "performer": "",
    "description": "Join in on the weekly Yoga session at the hall! $55 for 8 sessions "
                   "(members) $65 for 8 sessions (non-members) $10 drop-ins",
    "image": "", "name": "Yoga with Vic",
    "url": "https://www.whitehorncommunity.com/events/yoga-with-vic/?occurrence=2026-10-06",
}
MEC_TEA_DANCE = {
    "@context": "http://schema.org", "@type": "Event",
    "eventStatus": "https://schema.org/EventScheduled",
    "startDate": "2026-10-30T13:00:00+00:00", "endDate": "2026-10-30T15:00:00+00:00",
    "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode",
    "location": {"@type": "Place", "name": "The Florrie", "address": "377 Mill Street, L8 4RF"},
    "offers": {"url": "https://www.theflorrie.org/events/afternoon-tea-dances-at-the-florrie/",
               "price": "5", "priceCurrency": "GBP",
               "availability": "https://schema.org/InStock", "validFrom": "2026-10-30T00:00"},
    "performer": "",
    "description": "Step into an afternoon of music, movement and good company.",
    "name": "Afternoon Tea Dance",
    "url": "https://www.theflorrie.org/events/afternoon-tea-dance/",
}
# What marks a page as MEC's: the plugin's own asset path (Whitehorn, verbatim).
MEC_HEAD = ("<link rel='stylesheet' id='mec-select2-style-css' href='https://www.whitehorncommunity.com"
            "/wp-content/plugins/modern-events-calendar/assets/packages/select2/select2.min.css"
            "?ver=7.36.4.1789088278' media='all' />")
MEC_VENUE = {"name": "Whitehorn Community Association", "address": "228 Whitehorn Rd NE",
             "city": "Calgary", "region": "AB", "country": "CA", "lat": 51.0887954, "lon": -113.9669642}


def _page(head, *blocks):
    return "<html><head>" + head + "</head><body>" + "".join(
        '<script type="application/ld+json">' + json.dumps(b) + "</script>" for b in blocks) + "</body></html>"


class _Pages:
    """A session that serves fixed pages; anything else is a 404."""
    def __init__(self, pages):
        self.pages = pages

    def get(self, url, timeout=None):
        page = self.pages.get(url)

        class R:
            text = page or ""

            def raise_for_status(self):
                if page is None:
                    raise RuntimeError("404 " + url)
        return R()


class _Rows:
    def __init__(self):
        self.events = []

    def upsert(self, ev):
        self.events.append(ev)


def _ingest(pages, site, rows=None):
    rows = _Rows() if rows is None else rows
    with patch.object(mapsee_ingest_jsonld.time, "sleep"), \
            contextlib.redirect_stdout(io.StringIO()):          # its per-site summary line
        mapsee_ingest_jsonld.ingest_site(rows, _Pages(pages), dict(
            {"name": "fixture", "category": "community", "venue": MEC_VENUE}, **site))
    return getattr(rows, "events", None)


def check_mec():
    print()
    print('Modern Events Calendar\'s "price": "0" is an empty cost field, not a free event')
    yoga = dict(MEC_YOGA, startDate=SOON)
    tea = dict(MEC_TEA_DANCE, startDate=SOON_EVENING)
    listing = "https://www.whitehorncommunity.com/calendar/"

    got = _ingest({listing: _page(MEC_HEAD, yoga)}, {"listing": [listing]})
    ev = got[0] if got else None
    check_true("the MEC event itself is still imported", ev is not None)
    check("a MEC zero carries no admission facts at all (an empty dict, not None)",
          ev.source_details if ev else "missing", {})
    rec = ev.as_record("now") if ev else {}
    row = to_row(rec, "fixture-host") if ev else {}
    check("...which the sync writes as source_details NULL, clearing a stored free claim",
          ("source_details" in row, row.get("source_details", "absent")), (True, None))
    check_true("...and sends even under --only-new, so an already-synced row is put right",
               ev is not None and needs_detail_sync(rec, {rec["fingerprint"]: False}))
    check_true("its description no longer opens with the free marker 0227 tags as offer:free",
               ev is not None and not ev.description.startswith("Free to attend."))
    check_true("the organiser's own '$10 drop-ins' is still there for the reader",
               ev is not None and "$10 drop-ins" in ev.description)
    check("the ticket link survives without the price",
          ev.ticket_url if ev else None, MEC_YOGA["offers"]["url"])

    got = _ingest({listing: _page(MEC_HEAD, tea)}, {"listing": [listing]})
    ev = got[0] if got else None
    check("a cost the organiser DID enter is still an exact paid offer",
          ev.source_details if ev else None,
          {"free": False, "offer": {"price": "5", "currency": "GBP",
                                     "url": MEC_TEA_DANCE["offers"]["url"],
                                     "availability": "https://schema.org/InStock"}})
    check_true("and still leads with the free-tag veto and the price",
               ev is not None and ev.description.startswith(
                   "Some admission options are not free. Admission: GBP 5."))

    detail = "https://www.whitehorncommunity.com/events/yoga-with-vic/"
    got = _ingest({listing: '<a href="' + detail + '">Yoga</a>',
                   detail: _page(MEC_HEAD, yoga)},
                  {"listing": [listing],
                   "link_pattern": r"https://www\.whitehorncommunity\.com/events/[a-z0-9\-]+/"})
    check("the event-page path drops MEC's zero too",
          [e.source_details for e in got], [{}])

    got = _ingest({listing: _page("", yoga)}, {"listing": [listing]})
    check("a zero on a page WITHOUT MEC's markup is read as before (exact free)",
          got[0].source_details.get("free") if got else None, True)

    blank = dict(yoga, offers=dict(MEC_YOGA["offers"], price=""))
    got = _ingest({listing: _page(MEC_HEAD, blank)}, {"listing": [listing]})
    check("MEC Lite's empty string was already unknown and stays so",
          [e.source_details for e in got], [None])

    def facts(offers):
        return normalize_admission_facts(_mec_unset_price({"offers": offers})["offers"])
    for zero in ("0", 0, 0.0, "0.00", " 0 "):
        check(f"  {zero!r} is MEC's empty field: no facts", facts({"price": zero, "priceCurrency": "EUR"}), None)
    check("a zero beside a positive price still vetoes free without inventing one",
          facts([{"price": "0"}, {"price": "10", "priceCurrency": "EUR"}]), {"free": False})
    for typed in ("5", "400 - 890 Kč", "kostenfrei", "Free", ""):
        check(f"  typed cost {typed!r} is left exactly as the organiser wrote it",
              _mec_unset_price({"offers": {"price": typed}}), {"offers": {"price": typed}})
    # What the comment above _mec_unset_price says about typed text, pinned so it
    # cannot go stale: a word in offers.price is UNKNOWN to the admission reader.
    for word in ("Free", "kostenfrei", "Gratis"):
        check(f"  typed {word!r} gives no admission facts (unknown, not free)",
              normalize_admission_facts({"price": word, "priceCurrency": "EUR"}), None)
    check("a boolean is not a zero price", _mec_unset_price({"offers": {"price": False}}),
          {"offers": {"price": False}})
    untouched = {"name": "x", "offers": {"price": "5"}}
    check_true("an event with nothing to remove is returned as the same object",
               _mec_unset_price(untouched) is untouched)
    check("the caller's Event is not mutated", MEC_YOGA["offers"]["price"], "0")

    print()
    print("MEC's own printed 'Free', beside the one event on an event page, is believed")
    detail = "https://www.whitehorncommunity.com/events/yoga-with-vic/"
    typed_zero = dict(yoga, offers=dict(MEC_YOGA["offers"], price="0.00"))
    free_line = '<dl><dd class="mec-events-event-cost">Free</dd></dl>'
    paid_line = '<dl><dd class="mec-events-event-cost">\u00a35.00</dd></dl>'
    site = {"listing": [listing], "link_pattern": re.escape(detail)}
    def via_detail(page):
        got = _ingest({listing: '<a href="' + detail + '">x</a>', detail: page}, site)
        return [(e.source_details or {}).get("free") for e in got]
    check("an event page whose Cost line says Free keeps the zero as free",
          via_detail(_page(MEC_HEAD + free_line, typed_zero)), [True])
    check("a Cost line with a price above zero does not",
          via_detail(_page(MEC_HEAD + paid_line, typed_zero)), [None])
    check("nor does a Cost line on a page holding two events",
          via_detail(_page(MEC_HEAD + free_line, typed_zero,
                           dict(typed_zero, name="Other", url=detail + "?x"))), [None, None])
    got = _ingest({listing: _page(MEC_HEAD + free_line, typed_zero)}, {"listing": [listing]})
    check("nor does one on a LISTING page", [e.source_details for e in got], [{}])


def check_mec_clock():
    print()
    print("MEC prints the wall clock as if it were UTC; read it back as the wall clock")
    # Verbatim pairs, 2026-10-04: what the page prints, what the block says.
    check("Whitehorn: '6:45 pm' is 12:45-06:00 in the block",
          _mec_wall_clock("2026-10-06T12:45:00-06:00"), "2026-10-06T18:45:00")
    check("Ateliertheater before the change: 19:30 is 21:30+02:00",
          _mec_wall_clock("2026-10-21T21:30:00+02:00"), "2026-10-21T19:30:00")
    check("Ateliertheater after it: the same 19:30 is 20:30+01:00",
          _mec_wall_clock("2026-11-03T20:30:00+01:00"), "2026-11-03T19:30:00")
    check("Basler Papiermuehle, a site set to UTC: 14:00 is 14:00+00:00",
          _mec_wall_clock("2026-10-17T14:00:00+00:00"), "2026-10-17T14:00:00")
    check("a midnight stamp falls back onto its own night (Berlin Dark, ?occurrence=2026-10-23)",
          _mec_wall_clock("2026-10-24T00:00:00+02:00"), "2026-10-23T22:00:00")
    for same in ("2026-10-07", "2026-10-07T19:00:00", "", None, "soon", 1791576000):
        check(f"  {same!r} has no offset to undo and passes through", _mec_wall_clock(same), same)
    vienna = (48.2041987, 16.3470737)
    check("the sync then gives Calgary's 18:45 the instant the page means",
          _to_utc_if_naive(_mec_wall_clock("2026-10-06T12:45:00-06:00"),
                           MEC_VENUE["lat"], MEC_VENUE["lon"]), "2026-10-07T00:45:00Z")
    check("...and Vienna's 19:30 the right instant in summer time",
          _to_utc_if_naive(_mec_wall_clock("2026-10-21T21:30:00+02:00"), *vienna),
          "2026-10-21T17:30:00Z")
    check("...and in winter time, an hour later in UTC for the same 19:30",
          _to_utc_if_naive(_mec_wall_clock("2026-11-03T20:30:00+01:00"), *vienna),
          "2026-11-03T18:30:00Z")

    listing = "https://www.whitehorncommunity.com/calendar/"
    day = _D.strftime("%Y-%m-%d")
    eve = (_D - timedelta(days=1)).strftime("%Y-%m-%d")
    timed = dict(MEC_YOGA, startDate=day + "T12:45:00-06:00", endDate=day + "T14:30:00-06:00")
    got = _ingest({listing: _page(MEC_HEAD, timed)}, {"listing": [listing]})
    ev = got[0] if got else None
    check("through ingest_site the start is the naive wall clock",
          ev.start_local if ev else None, day + "T18:45:00")
    check("and so is the end", ev.end_local if ev else None, day + "T20:30:00")
    late = dict(MEC_YOGA, name="Club Night", startDate=day + "T00:00:00+02:00", endDate="")
    got = _ingest({listing: _page(MEC_HEAD, late)}, {"listing": [listing]})
    ev = got[0] if got else None
    check("a midnight stamp is dated, and fingerprinted, on the night before",
          (ev.start_local, ev.fingerprint) if ev else None,
          (eve + "T22:00:00", make_fingerprint("Club Night", eve, "WCA Main Hall")))
    got = _ingest({listing: _page("", timed)}, {"listing": [listing]})
    check("a page WITHOUT MEC's markup keeps its offset exactly as written",
          got[0].start_local if got else None, day + "T12:45:00-06:00")


def check_shared_urls():
    print()
    print("one url shown with several dates is several rows, not one")
    listing = "https://www.theflorrie.org/whatson/"
    yoga_url = "https://www.theflorrie.org/events/yoga/"
    days = [(_D + timedelta(days=7 * i)).strftime("%Y-%m-%d") for i in range(3)]
    weekly = [dict(MEC_TEA_DANCE, name="Yoga", url=yoga_url, offers=None,
                   startDate=d + "T12:00:00+01:00", endDate="") for d in days]
    once = dict(MEC_TEA_DANCE, startDate=days[0] + "T14:00:00+00:00", endDate="")
    nourl = [dict({k: v for k, v in w.items() if k != "url"}, name="Tai Chi") for w in weekly[:2]]
    for head, label in ((MEC_HEAD, "MEC"), ("", "any other")):
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(os.path.join(tmp, "store.json"))
            _ingest({listing: _page(head, *weekly, once, *nourl)}, {"listing": [listing]}, rows=store)
            recs = [r for r in store.records.values() if r["name"] in ("Yoga", "Tai Chi")]
            check(f"  {label} page: three weeks of yoga and two of url-less tai chi are five rows",
                  sorted(r["name"] for r in recs), ["Tai Chi"] * 2 + ["Yoga"] * 3)
            check(f"  {label} page: no row was re-keyed onto another date",
                  store.stats.get("rekeyed", 0), 0)
            check_true(f"  {label} page: every row's id is the date it starts on",
                       recs and all(r["fingerprint"] == make_fingerprint(
                           r["name"], r["start_local"][:10], r["venue_name"]) for r in recs))
    rows = _ingest({listing: _page(MEC_HEAD, *weekly, once)}, {"listing": [listing]})
    check("a shared url is keyed url#date",
          sorted(e.source_id for e in rows if e.name == "Yoga"),
          [yoga_url + "#" + d for d in days])
    check("a url shown once keeps the id it always had",
          [e.source_id for e in rows if e.name != "Yoga"], [MEC_TEA_DANCE["url"]])


# --- Tartu Kultuuriaken: verbatim from /et/syndmus/magnus-kirt-tagasitulek ---
# ?event=216250, 2026-10-25 (image trimmed). The page prints "P 25. okt 2026 |
# 13:30 - 15:41"; the block says 11:30 with no offset, and its url is the bare
# event page every occurrence of the film shares.
TARTU_LD = {
    "@type": "Event", "name": "Magnus Kirt: Tagasitulek",
    "url": "https://kultuuriaken.tartu.ee/et/syndmus/magnus-kirt-tagasitulek",
    "startDate": "2026-10-25T11:30:00",
    "@id": "https://kultuuriaken.tartu.ee/et/syndmus/magnus-kirt-tagasitulek",
    "endDate": "2026-10-25T13:41:00",
    "location": {"@type": "Place", "name": ["Elektriteater", "Raekoja saal"],
                 "address": {"@type": "PostalAddress", "streetAddress": ["Kompanii 2", "Tartu"],
                             "addressCountry": "EE"}},
    "organizer": {"@type": "Organization", "@id": "Elektriteater", "name": "Elektriteater",
                  "url": "https://elektriteater.ee"},
}
TALLINN = mapsee_ingest_jsonld.ZoneInfo("Europe/Tallinn")


class _Clock:
    """perf_counter and sleep on one fake clock; a request takes no time."""
    def __init__(self):
        self.t = 1000.0
        self.slept = []

    def perf_counter(self):
        return self.t

    def sleep(self, seconds):
        self.slept.append(round(seconds, 3))
        self.t += seconds


class _Host(_Pages):
    """Pages plus a robots.txt body (None: 404; an int: that status), each
    request stamped with the fake clock, so the gaps can be read off."""
    def __init__(self, pages, robots, clock):
        super().__init__(pages)
        self.robots, self.clock, self.asked = robots, clock, []

    def get(self, url, timeout=None, allow_redirects=True, params=None):
        self.asked.append((url, self.clock.t))
        if url.endswith("/robots.txt"):
            body, code = ((self.robots, 200) if isinstance(self.robots, str)
                          else ("", self.robots if isinstance(self.robots, int) else 404))

            class R:
                content = body.encode()
                status_code = code
            return R()
        if isinstance(self.pages.get(url), Exception):
            raise self.pages[url]
        if isinstance(self.pages.get(url), int):
            status = self.pages[url]

            class Refused:
                status_code = status
                text = ""

                def raise_for_status(self):
                    raise RuntimeError(f"{status} Client Error for url: {url}")
            return Refused()
        return super().get(url, timeout)


def _paced(pages, robots_body, site, *, runs=1, deadline=None):
    """(host, clock, printed lines, events) for ingest_site over a fake host, with
    the real robots_txt.Robots reading `robots_body`. runs > 1 repeats the site on
    the same host and the same Robots, as two configs on one host would be."""
    clock = _Clock()
    host = _Host(pages, robots_body, clock)
    robots = mapsee_ingest_jsonld.robots_txt.Robots(host)
    rows = _Rows()
    with patch.object(mapsee_ingest_jsonld.time, "sleep", side_effect=clock.sleep), \
            patch.object(mapsee_ingest_jsonld.time, "perf_counter", side_effect=clock.perf_counter), \
            patch.object(mapsee_ingest_jsonld.time, "monotonic", return_value=0.0), \
            patch.dict(mapsee_ingest_jsonld._ANSWERED, clear=True), \
            patch.object(mapsee_ingest_jsonld, "_THROTTLED", set()), \
            contextlib.redirect_stdout(io.StringIO()) as out:
        for _ in range(runs):
            mapsee_ingest_jsonld.ingest_site(rows, host, dict(
                {"name": "paced", "category": "community", "venue": MEC_VENUE}, **site),
                robots=robots, deadline=deadline)
    return host, clock, out.getvalue(), rows.events


def _gaps(host):
    """Seconds between successive page requests (robots.txt itself not counted)."""
    times = [t for u, t in host.asked if not u.endswith("/robots.txt")]
    return [round(b - a, 3) for a, b in zip(times, times[1:])]


def check_pacing():
    print()
    print("a host's robots.txt Crawl-delay is honoured, and what it refuses is not asked")
    listing = "https://kulturhus.example/program/"
    pages = [listing + f"{n}-show/" for n in (1, 2, 3)]
    show = {"@type": "Event", "name": "Show", "startDate": SOON, "location": {"name": "Hall"}}
    served = {listing: "".join(f'<a href="{p}">x</a>' for p in pages),
              **{p: _page("", dict(show, name=f"Show {i}", url=p)) for i, p in enumerate(pages)}}
    site = {"listing": [listing], "link_pattern": r"https://kulturhus\.example/program/\d+-show/"}
    askim = "User-agent: *\nDisallow: /wp-admin/\nAllow: /wp-admin/admin-ajax.php\nCrawl-delay: 10\n"

    host, clock, out, got = _paced(served, askim, site)
    check("Askim's 'Crawl-delay: 10' puts 10 s between every request after the first",
          _gaps(host), [10.0, 10.0, 10.0])
    check("robots.txt is read once for the whole site", [u for u, _ in host.asked].count(
        "https://kulturhus.example/robots.txt"), 1)
    check("all three shows are kept", len(got), 3)
    host, _, _, _ = _paced(served, None, site)
    check("no robots.txt (404): the 1 s floor the adapter always kept", _gaps(host), [1.0, 1.0, 1.0])
    host, _, _, _ = _paced(served, "User-agent: *\nCrawl-delay: 2\n", dict(site, crawl_delay=5))
    check("a config crawl_delay of 5 lengthens the file's 2", _gaps(host), [5.0, 5.0, 5.0])
    host, _, _, _ = _paced(served, askim, dict(site, crawl_delay=5))
    check("...and never shortens the file's 10", _gaps(host), [10.0, 10.0, 10.0])
    host, _, _, _ = _paced(served, askim, site, runs=2)
    check("two configs on one host share its pace, across sites",
          _gaps(host), [10.0] * 7)
    check("...and its robots.txt", [u for u, _ in host.asked].count(
        "https://kulturhus.example/robots.txt"), 1)
    host, clock, _, _ = _paced(served, None, {"listing": [listing]})
    check("a site of one page waits once, the 1 s floor after its robots.txt read, and never after "
          "its last page (it used to sleep 1 s after every page)", clock.slept, [1.0])

    print()
    print("a wait longer than 30 s is not slept: the page is not asked")
    inline = {listing: _page("", dict(show, url=pages[0]), dict(show, name="Other", url=pages[1]))
              + "".join(f'<a href="{p}">x</a>' for p in pages)}
    host, clock, out, got = _paced(inline, "User-agent: *\nCrawl-delay: 150\n", site)
    check("GRAAP's 'Crawl-delay: 150': robots.txt was the host's first request, so not even the listing is asked",
          [u for u, _ in host.asked], ["https://kulturhus.example/robots.txt"])
    check("...nothing is kept and nothing is slept", (len(got), clock.slept), (0, []))
    check_true("...and the line says what was not asked and why",
               "page(s) not asked: the host asks 150 s between requests, over the 30 s this run will wait"
               in out)
    host, clock, _, _ = _paced(served, askim, site)
    check("the first page waits the Crawl-delay after robots.txt (it used to follow at once)",
          round(host.asked[1][1] - host.asked[0][1], 3), 10.0)
    host, clock, _, _ = _paced(served, "User-agent: *\nCrawl-delay: 30\n", site)
    check("30 s is still waited", _gaps(host), [30.0, 30.0, 30.0])

    print()
    print("a challenged robots.txt asks nothing, and a request that fails still holds the pace")
    host, _, out, got = _paced(served, "<html><title>Just a moment...</title>checking your browser</html>", site)
    check("a bot challenge on robots.txt: only robots.txt is asked", [u for u, _ in host.asked],
          ["https://kulturhus.example/robots.txt"])
    broken = dict(served, **{pages[0]: ConnectionError("reset by peer")})
    host, _, _, got = _paced(broken, askim, site)
    check("a page whose request raises still stamps the host: the next waits 10 s", _gaps(host), [10.0, 10.0, 10.0])

    print()
    print("a wait is not started when it would end past the deadline")
    try:
        _paced(served, askim, site, deadline=5.0)
        check("BudgetExpired is raised", "no exception", "BudgetExpired")
    except mapsee_ingest_jsonld.BudgetExpired as exc:
        check("BudgetExpired names the first page not asked, so the next run starts there",
              (exc.offset, exc.inline_offset), (0, 0))

    print()
    print("what robots.txt refuses is not asked")
    host, _, out, got = _paced(served, "User-agent: *\nDisallow: /program/2-\nCrawl-delay: 1\n", site)
    check("a Disallowed event page is skipped and the others are read",
          [u for u, _ in host.asked if "-show/" in u], [pages[0], pages[2]])
    check_true("...and the line says which rule refused it",
               "1 page(s) not asked, robots.txt (ok) refuses /program/2-show/: Disallow: /program/2-" in out)
    host, _, out, got = _paced(served, 503, site)
    check("an unreadable robots.txt (503): nothing else is asked of the host (RFC 9309)",
          [u for u, _ in host.asked], ["https://kulturhus.example/robots.txt"])
    check_true("...and the line says so", "robots.txt (unreachable) refuses /program/" in out)
    host, _, _, _ = _paced(served, "User-agent: MapseeAggregator\nDisallow: /\n", site)
    check("a group naming our product token refuses us everything",
          [u for u, _ in host.asked], ["https://kulturhus.example/robots.txt"])

    print()
    print("a host that answers 429 is asked nothing more this run")
    host, _, out, got = _paced(dict(served, **{pages[1]: 429}), None, site, runs=2)
    check("Tartu's 429 on the second page: the third is not asked, nor the host's next site",
          [u for u, _ in host.asked], ["https://kulturhus.example/robots.txt", listing, pages[0], pages[1]])
    check("...and each site's line says why",
          out.count("1 page(s) not asked: the host answered 429 this run"), 2)
    host, _, _, _ = _paced(dict(served, **{pages[1]: 404}), None, site)
    check("a 404 is one page's answer: the next page is still asked",
          [u for u, _ in host.asked][-1], pages[2])

    print()
    print("main reads each host's robots.txt once for the whole run")
    with tempfile.TemporaryDirectory() as tmp:
        cfg = os.path.join(tmp, "sites.json")
        with open(cfg, "w", encoding="utf-8") as fh:
            json.dump({"sites": [{"name": "a", "listing": [listing]},
                                 {"name": "b", "listing": [listing + "b"]}]}, fh)
        seen = []
        with patch.object(mapsee_ingest_jsonld, "ingest_site",
                          side_effect=lambda *a, **k: seen.append(k.get("robots")) or 0), \
                patch.object(mapsee_ingest_jsonld, "_save_geo_cache"), \
                contextlib.redirect_stdout(io.StringIO()):
            mapsee_ingest_jsonld.main(["--config", cfg, "--store", os.path.join(tmp, "events.json")])
    check_true("every site is handed the same robots_txt.Robots",
               len(seen) == 2 and seen[0] is seen[1]
               and isinstance(seen[0], mapsee_ingest_jsonld.robots_txt.Robots))
    for junk in ("10s", -1, True):
        try:
            _paced(served, None, dict(site, crawl_delay=junk))
            check(f"  crawl_delay {junk!r} is a config error", "accepted", "ValueError")
        except ValueError:
            check(f"  crawl_delay {junk!r} is a config error", "ValueError", "ValueError")


def check_naive_utc():
    print()
    print("Tartu Kultuuriaken prints a UTC instant with no offset; naive_is_utc reads it so")
    wall = mapsee_ingest_jsonld._utc_wall_clock
    check("'L 24. okt 2026 | 12:00' is 09:00 in the block, on the last day of summer time",
          wall("2026-10-24T09:00:00", TALLINN), "2026-10-24T12:00:00+03:00")
    check("'P 25. okt 2026 | 12:00 - 15:00' is 10:00, the first day of EET",
          wall("2026-10-25T10:00:00", TALLINN), "2026-10-25T12:00:00+02:00")
    check("'13:30 - 15:41' is 11:30", wall(TARTU_LD["startDate"], TALLINN), "2026-10-25T13:30:00+02:00")
    check("a space separator and no seconds are read too",
          wall("2026-10-25 14:00", TALLINN), "2026-10-25T16:00:00+02:00")
    check("a late UTC start is the next LOCAL day",
          wall("2026-12-31T22:30:00", TALLINN), "2027-01-01T00:30:00+02:00")
    for same in ("2026-10-25", "2026-10-25T12:00:00+02:00", "2026-10-25T10:00:00Z", "", None, "soon"):
        check(f"  {same!r} is no naive timed stamp and passes through", wall(same, TALLINN), same)
    check("the sync passes the offset through untouched, so the instant is the page's",
          _to_utc_if_naive("2026-10-25T13:30:00+02:00", 58.38, 26.72), "2026-10-25T13:30:00+02:00")

    page_url = TARTU_LD["url"] + "?event=216250"
    future = dict(TARTU_LD, startDate=_D.strftime("%Y-%m-%dT22:30:00"), endDate="")
    local_day = (_D + timedelta(days=1)).strftime("%Y-%m-%d")
    site = {"listing": ["https://kultuuriaken.tartu.ee/et/syndmused"],
            "link_pattern": r"/et/syndmus/[a-z0-9\-]+\?event=\d+", "venue": None,
            "naive_is_utc": True, "timezone": "Europe/Tallinn"}
    served = {site["listing"][0]: '<a href="/et/syndmus/magnus-kirt-tagasitulek?event=216250">x</a>',
              page_url: _page("", future)}
    with patch.object(mapsee_ingest_jsonld, "_geocode", return_value=(58.3779, 26.7290)):
        got = _ingest(served, site)
        plain = _ingest(served, {k: v for k, v in site.items() if k not in ("naive_is_utc", "timezone")})
    ev = got[0] if got else None
    check_true("through ingest_site, 22:30 UTC is the next day in Tartu, with Tartu's offset",
               ev is not None and ev.start_local[:10] == local_day
               and ev.start_local[-6:] in ("+02:00", "+03:00"))
    check("...the same instant the block names",
          datetime.fromisoformat(ev.start_local).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M")
          if ev else None, _D.strftime("%Y-%m-%dT22:30"))
    check("...and it is fingerprinted on the local day",
          ev.fingerprint if ev else None,
          make_fingerprint("Magnus Kirt: Tagasitulek", local_day, "Elektriteater, Raekoja saal"))
    check("without the option the naive stamp passes through as before (read as Tartu's clock)",
          plain[0].start_local if plain else None, future["startDate"])

    listing = "https://www.whitehorncommunity.com/calendar/"
    day = _D.strftime("%Y-%m-%d")
    timed = dict(MEC_YOGA, startDate=day + "T12:45:00-06:00", endDate=day + "T14:30:00-06:00")
    got = _ingest({listing: _page(MEC_HEAD, timed)},
                  {"listing": [listing], "naive_is_utc": True, "timezone": "America/Edmonton"})
    check("on a MEC page the option leaves MEC's own naive wall clock alone",
          got[0].start_local if got else None, day + "T18:45:00")
    for bad in ({"naive_is_utc": True}, {"naive_is_utc": True, "timezone": "Mars/Olympus_Mons"},
                {"naive_is_utc": True, "timezone": ""}):
        try:
            _ingest(served, dict(site, **bad) if "timezone" in bad
                    else {k: v for k, v in dict(site, **bad).items() if k != "timezone"})
            check(f"  {bad} is a config error", "accepted", "ValueError")
        except ValueError:
            check(f"  {bad} is a config error", "ValueError", "ValueError")


def check_address_lines():
    print()
    print("a streetAddress printed as lines is the street, then the town")
    loc = TARTU_LD["location"]
    check("Tartu's ['Kompanii 2', 'Tartu'] is a street and a town",
          {k: v for k, v in _address_parts(loc).items() if v},
          {"address": "Kompanii 2", "city": "Tartu", "country": "EE"})
    four = {"address": {"streetAddress": ["Ülikooli tänav 1", "Tartu", "Tartu maakond", "Eesti"],
                        "addressCountry": "EE"}}
    check("['Ülikooli tänav 1', 'Tartu', 'Tartu maakond', 'Eesti'] too",
          (_address_parts(four)["address"], _address_parts(four)["city"]), ("Ülikooli tänav 1", "Tartu"))
    named = {"address": {"streetAddress": ["Kompanii 2", "Raekoja plats"], "addressLocality": "Tartu"}}
    check("an addressLocality beside the lines is the town, not the second line",
          _address_parts(named)["city"], "Tartu")
    check("a one-line list is a street and no town",
          (_address_parts({"address": {"streetAddress": ["Vanemuise 6"]}})["address"],
           _address_parts({"address": {"streetAddress": ["Vanemuise 6"]}})["city"]), ("Vanemuise 6", None))
    check("a plain string streetAddress is read as before",
          _address_parts({"address": {"streetAddress": "Vanemuise 6"}})["address"], "Vanemuise 6")
    check("a hall named as a list reads as the site's own listing shows it",
          mapsee_ingest_jsonld._clean(loc["name"]), "Elektriteater, Raekoja saal")
    check("an empty list is no text", mapsee_ingest_jsonld._clean([]), None)

    print()
    print("abroad, a street and a town with no geo are not geocoded here (an unchecked answer was wrong)")
    queries = []

    def photon(_session, q):
        queries.append(q)
        return 58.3779, 26.7290
    tartu = dict(TARTU_LD, startDate=SOON)
    with patch.object(mapsee_ingest_jsonld, "_geocode", side_effect=photon):
        ev = to_event(tartu, TARTU_LD["url"], "community", None)
        check("Tartu's row asks Photon nothing", queries, [])
        check("...and is left unplaced, with its hall and town", (ev.latitude, ev.venue_name, ev.city) if ev else None,
              (None, "Elektriteater, Raekoja saal", "Tartu"))
        for country in (None, "US", "USA", "United States", "pr"):
            us = dict(tartu, location={"name": "Chop Suey", "address": {
                "streetAddress": "1325 E Madison St", "addressLocality": "Seattle",
                "addressRegion": "WA", "addressCountry": country}})
            ev = to_event(us, "u", "music", None)
            check(f"  country {country!r}: a street and a city are left for the sync's Census pass",
                  (ev is not None, ev.latitude if ev else "dropped", queries), (True, None, []))
        geo = dict(tartu, location=dict(TARTU_LD["location"], geo={"latitude": 58.38, "longitude": 26.72}))
        check_true("an Event abroad that carries its own geo asks nothing and keeps it",
                   (lambda e: e is not None and e.latitude == 58.38)(to_event(geo, "u", "community", None))
                   and queries == [])


def check_site_line():
    # The 10-21 ICS log flushed 8 KB covering 117.7 min and 54 sources, and no
    # line said which one took the time; JSON-LD's per-site lines are the same.
    print()
    print("a site's line says how long it took and what it spent on Photon")
    listing = "https://venue.example/whatson/"

    def talk(name, place):
        return {"@context": "https://schema.org", "@type": "Event", "name": name, "startDate": SOON,
                "url": listing + name.lower(), "location": {"@type": "Place", "name": place}}

    class PhotonPages(_Pages):
        queries = []

        def get(self, url, timeout=None, params=None):
            if params is None:
                return super().get(url, timeout)
            self.queries.append(params["q"])
            hit = params["q"].startswith("Qx Photon Test Hall")

            class R:
                def raise_for_status(self):
                    pass

                def json(self):
                    return {"features": [{"geometry": {"coordinates": [-122.3, 47.6]}}] if hit else []}
            return R()

    pages = PhotonPages({listing: _page("", talk("Talk", "Qx Photon Test Hall"),
                                        talk("Walk", "Qx Photon Nowhere"),
                                        talk("Again", "Qx Photon Test Hall"))})
    with patch.object(mapsee_ingest_jsonld.time, "sleep"), \
            patch.object(mapsee_ingest_jsonld, "geocode_allowed", return_value=True), \
            patch.dict(mapsee_ingest_jsonld._geo_cache, {}), \
            contextlib.redirect_stdout(io.StringIO()) as out:
        mapsee_ingest_jsonld.ingest_site(_Rows(), pages, {"name": "fixture", "category": "community",
                                                          "listing": [listing]})
    check_true("  'kept 2 events from 0 pages in 0.0s; 2 Photon lookup(s), 1 missed'",
               "[jsonld] fixture: kept 2 events from 0 pages in 0.0s; 2 Photon lookup(s), 1 missed"
               in out.getvalue() and len(pages.queries) == 2)
    with patch.object(mapsee_ingest_jsonld.time, "sleep"), \
            contextlib.redirect_stdout(io.StringIO()) as out:
        mapsee_ingest_jsonld.ingest_site(_Rows(), _Pages({listing: _page("", talk("Talk", "Hall"))}),
                                         {"name": "fixture", "category": "community", "listing": [listing],
                                          "venue": MEC_VENUE})
    check_true("  a site that sent Photon nothing says only its seconds",
               "kept 1 events from 0 pages in 0.0s\n" in out.getvalue() and "Photon" not in out.getvalue())
    with tempfile.TemporaryDirectory() as tmp:
        cfg = os.path.join(tmp, "sites.json")
        with open(cfg, "w", encoding="utf-8") as fh:
            json.dump({"sites": [{"name": "refuser", "listing": [listing]}]}, fh)
        with patch.object(mapsee_ingest_jsonld, "ingest_site", side_effect=RuntimeError("403 Forbidden")), \
                patch.object(mapsee_ingest_jsonld, "_save_geo_cache"), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            mapsee_ingest_jsonld.main(["--config", cfg, "--store", os.path.join(tmp, "events.json")])
    check_true("  a FAILED site's line says how long it took",
               "[jsonld] refuser FAILED: 403 Forbidden in 0.0s" in out.getvalue())


def check_meta_description_fallback():
    print()
    print("an opted-in single-event page can lend its own verified metadata description")
    page_url = "https://www.musiccenternw.org/events-1/centerpiece-jazz-band-oct-2026"
    title = "In Concert: CenterPiece Jazz Band - October 2026 — Music Center of the Northwest"
    description = "You are invited to join us as CenterPiece Jazz Band perform in a free concert!"
    event = {
        "@context": "https://schema.org", "@type": "Event", "name": title,
        "url": page_url, "startDate": SOON,
        "location": {"@type": "Place", "name": "Music Center of the Northwest",
                     "address": {"@type": "PostalAddress", "streetAddress": "901 N 96th St",
                                 "addressLocality": "Seattle", "addressRegion": "WA",
                                 "postalCode": "98103", "addressCountry": "US"}},
    }
    head = (f'<meta property="og:title" content="{title}">'
            f'<meta name="description" content="{description}">'
            f'<link rel="canonical" href="{page_url}">')
    page = _page(head, event)
    prepared = mapsee_ingest_jsonld._prepared(page, page_url, detail=True, meta_description=True)
    item = prepared[0][0] if prepared else {}
    check("matching event page metadata fills only the absent description",
          item.get("description"), description)
    ev = to_event(item, page_url, "music", no_geocode)
    row = to_row(ev.as_record("now"), "fixture-host") if ev else {}
    check_true("verified free wording reaches the ordinary description field",
               row.get("description", "").startswith(description))
    check("metadata prose does not manufacture a structured offer", row.get("source_details"), None)

    restricted_desc = "Free admission for students only; other visitors pay the regular price."
    restricted_page = _page(head.replace(description, restricted_desc), event)
    restricted_item = mapsee_ingest_jsonld._prepared(
        restricted_page, page_url, detail=True, meta_description=True)[0][0]
    restricted = to_event(restricted_item, page_url, "music", no_geocode)
    check_true("membership/audience restrictions remain visible and veto a public-free claim",
               restricted is not None and restricted.source_details == {"free": False, "restricted": True}
               and restricted.description.startswith("Not a free public admission offer.")
               and restricted_desc in restricted.description)

    paid = dict(event, offers={"@type": "Offer", "price": "20", "priceCurrency": "USD"})
    paid_item = mapsee_ingest_jsonld._prepared(_page(head, paid), page_url,
                                                detail=True, meta_description=True)[0][0]
    paid_ev = to_event(paid_item, page_url, "music", no_geocode)
    check("existing Event offers are never shadowed by page metadata",
          paid_item.get("description"), None)
    check("a paid offer continues to veto free", paid_ev.source_details if paid_ev else None,
          {"free": False, "offer": {"price": "20", "currency": "USD", "url": page_url}})

    existing_desc = dict(event, description="Publisher Event description.")
    existing_item = mapsee_ingest_jsonld._prepared(_page(head, existing_desc), page_url,
                                                    detail=True, meta_description=True)[0][0]
    check("existing Event descriptions are never overwritten",
          existing_item.get("description"), "Publisher Event description.")

    multi = _page(head, event, dict(event, name="Second Event"))
    check("multi-event pages cannot share one metadata description",
          [item.get("description") for item, _ in mapsee_ingest_jsonld._prepared(
              multi, page_url, detail=True, meta_description=True)], [None, None])
    check("listing-page metadata cannot be borrowed even with one Event",
          mapsee_ingest_jsonld._prepared(page, page_url, detail=False,
                                         meta_description=True)[0][0].get("description"), None)
    mismatch = _page(head.replace(title, "Another Event"), event)
    check("a non-matching og:title blocks metadata borrowing",
          mapsee_ingest_jsonld._prepared(mismatch, page_url, detail=True,
                                         meta_description=True)[0][0].get("description"), None)
    wrong_canonical = _page(head.replace(page_url, page_url + "-other"), event)
    check("a canonical URL naming another page blocks metadata borrowing",
          mapsee_ingest_jsonld._prepared(wrong_canonical, page_url, detail=True,
                                         meta_description=True)[0][0].get("description"), None)
    check("unconfigured sources remain unchanged",
          mapsee_ingest_jsonld._prepared(page, page_url, detail=True)[0][0].get("description"), None)


def main():
    item = _parse_ld(LIVE_LD)
    check_true("the live JSON-LD block parses", item is not None)
    check_true("it is recognised as an Event", _is_event(item))

    print()
    print("the key is `Location`, not `location` — read it, do not miss it")
    check("_ld_get finds the capitalised key",
          (_ld_get(item, "location") or {}).get("name"), "-")
    check("an exact lowercase key still wins", _ld_get({"a": 1, "A": 2}, "a"), 1)
    check("a genuinely absent key is still None", _ld_get(item, "offers"), None)

    print()
    print("a placeholder is not a location")
    for junk in ("-", "--", "n/a", "N/A", "TBA", "tbd", "None", " - ", "-."):
        check(f"  {junk!r} carries no information", _meaningful(junk), None)
    check("'NA' is Namibia, not 'not applicable' — the country survives",
          _address_parts({"address": {"streetAddress": "12 Nelson Mandela Ave",
                                      "addressLocality": "Windhoek",
                                      "addressCountry": "NA"}})["country"], "NA")
    check("a real street survives untouched",
          _meaningful("5000 Rainier Ave S"), "5000 Rainier Ave S")
    check("a street that merely starts with a dash survives",
          _meaningful("-5000 Rainier"), "-5000 Rainier")
    check("the live Location yields no address at all",
          _address_parts(_ld_get(item, "location")),
          {"address": None, "city": None, "region": None,
           "postal_code": None, "country": None})

    print()
    print("so the venue block is the only thing that places these events")
    ev = to_event(dict(item, startDate=SOON),
                  "https://theroyalroomseattle.com/event/trio-reunion/",
                  "music", no_geocode, VENUE, SKIP_RX)
    check_true("the event is kept", ev is not None)
    check("venue name comes from the config, not from '-'", ev.venue_name, "The Royal Room")
    check("street comes from the config", ev.address, "5000 Rainier Ave S")
    check("city comes from the config", ev.city, "Seattle")
    check("region comes from the config", ev.region, "WA")
    check("no field anywhere is left as the placeholder",
          [k for k, v in vars(ev).items() if v == "-"], [])
    check("it is pinned at the venue", (ev.latitude, ev.longitude),
          (VENUE["lat"], VENUE["lon"]))

    print()
    print("the venue block FILLS a gap; it never overrides what the page says")
    real = dict(item, startDate=SOON,
                Location={"@type": "Place", "name": "Chop Suey",
                          "address": "1325 E Madison St, Seattle, WA 98122"})
    ev2 = to_event(real, "u", "music", no_geocode, VENUE, SKIP_RX)
    check("a real venue name is believed over the config", ev2.venue_name, "Chop Suey")
    check("a real street is believed over the config", ev2.address, "1325 E Madison St")
    check("a real postcode is believed over the config", ev2.postal_code, "98122")

    print()
    print("startDate is naive local time — only the venue's coords make it an instant")
    check("the adapter passes the naive stamp through verbatim, space and all",
          ev.start_local, SOON)
    check("the block as captured carries no offset and no 'T'",
          item["startDate"], "2026-08-19 19:30:00")
    check("no end time is invented when the site ships none", ev.end_local, None)
    check("the sync turns 7:30pm on an August night in Seattle into the right instant",
          _to_utc_if_naive("2026-08-19 19:30:00", ev.latitude, ev.longitude),
          "2026-08-20T02:30:00Z")
    check("a winter date uses PST, not a fixed offset",
          _to_utc_if_naive("2026-12-10 19:30:00", VENUE["lat"], VENUE["lon"]),
          "2026-12-11T03:30:00Z")
    check_true("read without coordinates it would be 7 hours wrong — that is the bug",
               _to_utc_if_naive("2026-08-19 19:30:00", None, None) != "2026-08-20T02:30:00Z")

    print()
    print("a closure notice is not an event")
    for shut in ("CLOSED FOR MAINTENANCE", "Closed for Private Event", "closed today"):
        notice = dict(item, name=shut, startDate=SOON_MIDNIGHT)
        check(f"  {shut!r} is refused",
              to_event(notice, "u", "music", no_geocode, VENUE, SKIP_RX), None)
    for gig in ("Trio Reunion", "The Doors Closed Tribute", "Closing Night Gala"):
        keep = dict(item, name=gig, startDate=SOON_EVENING)
        check_true(f"  {gig!r} is kept",
                   to_event(keep, "u", "music", no_geocode, VENUE, SKIP_RX) is not None)
    check_true("without skip_title nothing is dropped — it is opt-in per site",
               to_event(dict(item, name="CLOSED FOR MAINTENANCE",
                             startDate=SOON_MIDNIGHT),
                        "u", "music", no_geocode, VENUE, None) is not None)

    print()
    print("one page is one occurrence: the id the sync upserts on")
    check("source_id is the event's own page, so two nights cannot collide",
          ev.source_id, "https://theroyalroomseattle.com/event/trio-reunion/")

    print()
    print("structured admission facts are exact, public and conservative")
    event_url = "https://example.org/event/admission/"
    def with_offers(offers, desc="", title="Free family art day"):
        return to_event(dict(item, name=title, startDate=SOON, offers=offers,
                             description=desc, url=event_url), event_url,
                        "community", no_geocode, VENUE)

    free_offer = {"@type": "Offer", "url": event_url,
                  "price": "0", "priceCurrency": "USD"}
    free_row_ev = with_offers(free_offer)
    check("one explicit public zero-price Offer gets exact free facts",
          free_row_ev.source_details if free_row_ev else None,
          {"free": True, "offer": {"price": "0", "currency": "USD", "url": event_url}})
    check_true("the exact free marker leads the description",
               free_row_ev is not None and free_row_ev.description.startswith("Free to attend."))
    mapped = to_row(free_row_ev.as_record("now"), "fixture-host")
    check("the JSON-LD facts survive the real adapter-to-row mapping",
          mapped.get("source_details"), free_row_ev.source_details)
    no_offer_ev = with_offers(None)
    check("adding admission facts does not change event identity",
          free_row_ev.fingerprint if free_row_ev else None,
          no_offer_ev.fingerprint if no_offer_ev else None)

    paid_offer = {"@type": "Offer", "url": "https://tickets.example.org/buy",
                  "price": "12.50", "priceCurrency": "USD"}
    paid_ev = with_offers(paid_offer)
    check("one exact positive Offer keeps its currency",
          paid_ev.source_details if paid_ev else None,
          {"free": False,
           "offer": {"price": "12.5", "currency": "USD",
                     "url": "https://tickets.example.org/buy"}})
    check_true("a known positive price leads with the free-tag veto",
               paid_ev is not None
               and paid_ev.description.startswith("Some admission options are not free."))
    mixed_ev = with_offers([free_offer, paid_offer])
    check("mixed free and paid Offers veto global free without a guessed price",
          mixed_ev.source_details if mixed_ev else None, {"free": False})
    check_true("mixed prices lead with the free-tag veto",
               mixed_ev is not None
               and mixed_ev.description.startswith("Some admission options are not free."))

    aggregate_free = with_offers({"@type": "AggregateOffer", "url": event_url,
                                  "lowPrice": "0", "highPrice": "0",
                                  "priceCurrency": "USD"})
    check_true("an all-zero AggregateOffer is explicit free admission",
               aggregate_free is not None and aggregate_free.source_details is not None
               and aggregate_free.source_details.get("free") is True)
    aggregate_mixed = with_offers({"@type": "AggregateOffer", "lowPrice": "0",
                                   "highPrice": "20", "priceCurrency": "USD"})
    check("an AggregateOffer range starting at zero is not globally free",
          aggregate_mixed.source_details if aggregate_mixed else None, {"free": False})

    for label, offer in (
        ("eligibility field", dict(free_offer, eligibleCustomerType="Members")),
        ("offer description", dict(free_offer, description="Free for members only")),
        ("age-limited offer description", dict(free_offer, description="Free for ages 18 and under")),
        ("offer name", dict(free_offer, name="Members only free admission")),
    ):
        restricted = with_offers(offer)
        check_true(f"{label} cannot claim globally free admission",
                   restricted is not None and restricted.source_details == {"free": False, "restricted": True}
                   and not (restricted.description or "").startswith("Free to attend."))

    check_true("missing and malformed offers remain unknown",
               with_offers(None).source_details is None
               and with_offers({"price": "not a price"}).source_details is None)
    participants = to_event(dict(item, startDate=SOON,
        performer=[{"@type": "PerformingGroup", "name": "Trio Reunion"},
                   {"@type": "Person", "name": "Person"}],
        Organizer={"@type": "Organization", "name": "The Royal Room", "url": "https://theroyalroomseattle.com/"},
        offers=dict(paid_offer, availability="https://schema.org/InStock", validFrom="2026-10-01T09:30:00-07:00")),
        event_url, "music", no_geocode, VENUE)
    check("named source participants survive alongside exact admission facts",
          participants.source_details.get("performers"), [{"type": "PerformingGroup", "name": "Trio Reunion"}])
    check("capitalized Organizer retains the actual public organization URL",
          participants.source_details.get("organizer"),
          {"type": "Organization", "name": "The Royal Room", "url": "https://theroyalroomseattle.com/"})
    check("source on-sale dates survive the real adapter-to-database mapping",
          to_row(participants.as_record("now"), "fixture-host")["source_details"]["offer"].get("valid_from"),
          "2026-10-01T09:30:00-07:00")
    invalid_organizer = to_event(dict(item, startDate=SOON,
        Organizer={"@type":"Organization", "name":"Venue", "url":"https://user:pass@example.org/"}),
        event_url, "music", no_geocode, VENUE)
    check("credentials in a publisher URL cannot create organizer metadata", invalid_organizer.source_details, None)

    nested = {"price": "0", "priceCurrency": "USD"}
    for _ in range(7):
        nested = {"offers": nested}
    check_true("deeply nested offer data is bounded and cannot claim free",
               with_offers(nested).source_details is None)

    check_mec()
    check_mec_clock()
    check_shared_urls()
    check_meta_description_fallback()
    check_pacing()
    check_naive_utc()
    check_address_lines()
    check_site_line()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: " + "; ".join(FAILURES))
        return 1
    print("all jsonld adapter checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
