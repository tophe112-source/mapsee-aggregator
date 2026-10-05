#!/usr/bin/env python3
"""
test_cancelled_events.py — a cancelled event must not reach the map, and must
leave it once the source calls it off.

Prints one line per case and exits non-zero, like the other gate scripts.

There are two halves to this and they fail differently:

  INGEST. Every adapter that CAN see a cancellation must act on it. Three could
  not: the ICS parser threw RFC 5545's STATUS property away before anything
  could read it, the generic JSON-LD adapter never looked at schema.org's
  eventStatus (which mapsee_ingest_festivals has honoured since it was written),
  and the Meetup query did not ask for Event.status at all.

  AFTER INGEST, which is where nearly all of them actually happen. An upsert
  cannot delete and --only-new means a scheduled run can only ADD, so a row
  cancelled the week after we took it stays on the map. Measured 2026-09-13 over
  502 Meetup-sourced rows sampled from mapsee.me's live sitemaps and re-probed
  at their own source URLs: 47 cancelled, 18 deleted (HTTP 404) — 12.9%.
  mapsee_prune_cancelled is the only thing that can see those, and what is
  pinned here is mostly what it must REFUSE to act on.
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mapsee_ingest_ics as ICS
import mapsee_ingest_meetup as MEETUP
import mapsee_prune_cancelled as PRUNE
from mapsee_ingest_jsonld import to_event as jsonld_to_event

FAILS = []


def check(label, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {label}" + ("" if ok else f"   got {got!r}, want {want!r}"))
    if not ok:
        FAILS.append(label)


def check_true(label, cond, detail=""):
    check(label, bool(cond), True) if cond else check(label, detail or False, True)


SOON = (datetime.now(timezone.utc) + timedelta(days=9)).strftime("%Y%m%d")
SOON_ISO = (datetime.now(timezone.utc) + timedelta(days=9)).strftime("%Y-%m-%dT19:00:00Z")


# --------------------------------------------------------------------------- #
# ICS — STATUS:CANCELLED
# --------------------------------------------------------------------------- #
def _vevent(summary, status=None):
    lines = ["BEGIN:VEVENT", f"SUMMARY:{summary}", f"DTSTART:{SOON}T190000Z",
             "GEO:47.6062;-122.3321", "LOCATION:Central Library",
             f"UID:{summary.replace(' ', '-')}"]
    if status:
        lines.append(f"STATUS:{status}")
    lines.append("END:VEVENT")
    return "\n".join(lines)


ICS_BODY = "BEGIN:VCALENDAR\n" + "\n".join([
    _vevent("Storytime for Toddlers"),
    _vevent("Cancelled Craft Night", "CANCELLED"),
    _vevent("Lowercase Cancellation", "cancelled"),
    _vevent("Maybe Book Club", "TENTATIVE"),
    _vevent("Confirmed Lecture", "CONFIRMED"),
]) + "\nEND:VCALENDAR\n"


class FakeStore:
    def __init__(self):
        self.seen = []

    def upsert(self, ev):
        self.seen.append(ev.name)
        return "added"


print("ICS: RFC 5545 STATUS is the only cancellation signal a calendar can send")
parsed = ICS.parse_ics(ICS_BODY)
check("parse_ics keeps STATUS (it used to drop the property entirely)",
      parsed[1].get("STATUS", ("", {}))[0], "CANCELLED")

store = FakeStore()
with patch.object(ICS, "_fetch_ics", return_value=(ICS_BODY, "200")):
    kept = ICS.ingest_ics(store, None, {"name": "lib", "url": "https://x.test/f.ics"})
check("a cancelled VEVENT never reaches the store", "Cancelled Craft Night" in store.seen, False)
check("...case-insensitively, because feeds are not careful",
      "Lowercase Cancellation" in store.seen, False)
check("TENTATIVE is NOT a cancellation — municipal software publishes everything that way",
      "Maybe Book Club" in store.seen, True)
check("CONFIRMED is kept", "Confirmed Lecture" in store.seen, True)
check("a VEVENT with no STATUS at all is kept — absence is the normal state",
      "Storytime for Toddlers" in store.seen, True)
check("the kept count matches what was stored", kept, len(store.seen))


# --------------------------------------------------------------------------- #
# JSON-LD — schema.org eventStatus
# --------------------------------------------------------------------------- #
print()
print("JSON-LD: schema.org eventStatus, the same test the festival adapter makes")
VENUE = {"name": "The Royal Room", "address": "5000 Rainier Ave S", "city": "Seattle",
         "region": "WA", "postal_code": "98118", "country": "US",
         "lat": 47.5589, "lon": -122.2839}


def no_geocode(*a, **k):
    raise AssertionError("a cancelled event must be refused before anything is geocoded")


BASE = {"@type": "Event", "name": "Trio Reunion", "startDate": SOON_ISO}


def ld(**kw):
    return jsonld_to_event(dict(BASE, **kw), "https://x.test/e/1", "music", no_geocode, VENUE, None)


check_true("a scheduled event is imported", ld(eventStatus="https://schema.org/EventScheduled"))
check("EventCancelled is refused", ld(eventStatus="https://schema.org/EventCancelled"), None)
check("EventPostponed is refused — the date we hold is the one NOT happening",
      ld(eventStatus="https://schema.org/EventPostponed"), None)
check("a bare, unprefixed value is refused too", ld(eventStatus="EventCancelled"), None)
check_true("no eventStatus at all is still imported", ld())


# --------------------------------------------------------------------------- #
# Meetup — Event.status
# --------------------------------------------------------------------------- #
print()
print("Meetup: Event.status, a NON_NULL EventStatus enum (live introspection 2026-09-13)")
check_true("the query actually asks for it — a field we never request cannot help",
           "\n      status\n" in MEETUP.QUERY)

MEET = {"id": "1", "title": "Sunday Salsa Level 2", "dateTime": SOON_ISO,
        "venue": {"name": "Studio", "lat": 47.6, "lon": -122.3}}
for state in ("CANCELLED", "cancelled", "CANCELLED_PERM", "AUTOSCHED_CANCELLED",
              "DRAFT", "BLOCKED", "TEMPLATE", "PROPOSED", "PAST"):
    check(f"  status {state} is refused", MEETUP.to_event(dict(MEET, status=state)), None)
check_true("  status ACTIVE is imported", MEETUP.to_event(dict(MEET, status="ACTIVE")))
check_true("  status AUTOSCHED is imported", MEETUP.to_event(dict(MEET, status="AUTOSCHED")))
# A DENYLIST, so a state Meetup invents tomorrow arrives instead of silently
# emptying the feed. Meetup has renamed fields on us twice; an allowlist would
# have turned the next rename into a total outage.
check_true("  an UNKNOWN future state is let in, not dropped",
           MEETUP.to_event(dict(MEET, status="SOME_NEW_STATE_2027")))
check_true("  a missing status is let in", MEETUP.to_event(dict(MEET)))


# --------------------------------------------------------------------------- #
# The pruner — mostly about what it must REFUSE to do
# --------------------------------------------------------------------------- #
print()
print("prune: what the SOURCE PAGE has to say before a row comes off the map")
check("JSON-LD EventCancelled",
      PRUNE.page_verdict('{"@type":"Event","eventStatus":"https://schema.org/EventCancelled"}'),
      "cancelled")
check("JSON-LD EventPostponed",
      PRUNE.page_verdict('"eventStatus": "https://schema.org/EventPostponed"'), "cancelled")
check("JSON-LD EventScheduled",
      PRUNE.page_verdict('"eventStatus":"https://schema.org/EventScheduled"'), "live")
check("microdata, itemprop first",
      PRUNE.page_verdict('<meta itemprop="eventStatus" content="https://schema.org/EventCancelled">'),
      "cancelled")
check("microdata, content first — half the CMSs that emit it do",
      PRUNE.page_verdict('<meta content="https://schema.org/EventCancelled" itemprop="eventStatus">'),
      "cancelled")
check("no markup at all is LIVE, not unknown — most event pages carry none, and "
      "calling them unknown is the same as not running",
      PRUNE.page_verdict("<h1>Doors 8pm</h1>"), "live")

print()
print("  ...and PROSE is never evidence. These sentences are on real, live listings:")
for prose in ("Free cancellation up to 24 hours before the show.",
              "Cancellation policy: refunds are issued within 7 days.",
              "THIS EVENT HAS BEEN CANCELLED",
              "In the event of a cancelled game, tickets are honoured for the replay.",
              "Cancelled orders are refunded automatically."):
    check(f"    {prose[:52]!r}", PRUNE.page_verdict(f"<p>{prose}</p>"), "live")

print()
print("  a mixed CALENDAR page cannot condemn our row — we cannot tell which event is ours")
check("one cancelled among several scheduled -> unknown",
      PRUNE.page_verdict('"eventStatus":"https://schema.org/EventCancelled"'
                         '"eventStatus":"https://schema.org/EventScheduled"'
                         '"eventStatus":"https://schema.org/EventScheduled"'), "unknown")
check("a page where EVERY event is off is still cancelled",
      PRUNE.page_verdict('"eventStatus":"https://schema.org/EventCancelled"'
                         '"eventStatus":"https://schema.org/EventPostponed"'), "cancelled")

print()
print("  a host answering dead all at once is a statement about US, not about the world")
many_dead = {f"https://tix.test/e/{i}": ("cancelled" if i < 9 else "live") for i in range(10)}
check("9 of 10 on one host -> the host is refusing, nothing is hidden",
      "tix.test" in PRUNE.refusing_hosts(many_dead), True)
mixed = {f"https://venue.test/e/{i}": ("cancelled" if i < 3 else "live") for i in range(10)}
check("3 of 10 is a venue having a bad month -> those three are hidden",
      "venue.test" in PRUNE.refusing_hosts(mixed), False)
# prune_links uses a floor of 3; this one HIDES rows rather than editing text, and
# a real venue can cancel three nights running, so the floor is higher here.
few = {f"https://small.test/e/{i}": "cancelled" for i in range(4)}
check("4 dead on a host we have only 4 rows for is below the floor -> they are hidden",
      "small.test" in PRUNE.refusing_hosts(few), False)

print()
print("  a page robots.txt refuses is never FETCHED (the owner's line, 2026-09-30)")


class _Robots:
    def __init__(self, allowed):
        self.allowed, self.asked = allowed, []

    def check(self, url):
        self.asked.append(url)
        return {"allowed": self.allowed}


def _must_not_fetch(*a, **k):
    raise AssertionError("fetched a page robots.txt did not allow")


class _Resp:
    def __init__(self, status, body, ctype="text/html"):
        self.status, self._body, self.headers = status, body, {"content-type": ctype}

    def read(self, n=-1):
        return self._body if n is None or n < 0 else self._body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


for allowed, why in ((False, "a Disallow"), (None, "a challenge instead of a robots.txt")):
    rb = _Robots(allowed)
    try:
        with patch.object(PRUNE.urllib.request, "urlopen", _must_not_fetch):
            got = PRUNE.cancellation_verdict("https://www.santafelibrary.org/event/storytime", robots=rb)
    except AssertionError as ex:
        got = str(ex)
    check(f"{why}: verdict 'robots', and no request for the page", got, "robots")
check("'robots' is not evidence: a host full of them is not 'refusing', and nothing is hidden",
      PRUNE.refusing_hosts({f"https://lib.test/e/{i}": "robots" for i in range(9)}), set())

rb = _Robots(True)
page = b'<script type="application/ld+json">{"eventStatus":"https://schema.org/EventCancelled"}</script>'
with patch.object(PRUNE.urllib.request, "urlopen", lambda req, timeout=20: _Resp(200, page)):
    got = PRUNE.cancellation_verdict("https://www.meetup.com/g/events/1/", robots=rb)
check("an allowed page is read as before, and a cancelled one still acts", got, "cancelled")
check("...after asking robots.txt about that exact page", rb.asked, ["https://www.meetup.com/g/events/1/"])
with patch.object(PRUNE.urllib.request, "urlopen", lambda req, timeout=20: _Resp(200, page)):
    check("no robots given (a direct call) reads the page as it always did",
          PRUNE.cancellation_verdict("https://www.meetup.com/g/events/1/"), "cancelled")

print("  ...and the stdlib session it asks through answers the way RFC 9309 reads it")
import io
import urllib.error


def _serve(answer):
    def urlopen(req, timeout=15):
        if isinstance(answer, Exception):
            raise answer
        return answer
    return urlopen


def _http_error(code, body=b""):
    return urllib.error.HTTPError("https://h.test/robots.txt", code, "x", {}, io.BytesIO(body))


CHALLENGE = b"<html><title>Just a moment...</title>Checking your browser</html>"
for label, answer, want in (
        ("a file that says Disallow: / refuses", _Resp(200, b"User-agent: *\nDisallow: /\n", "text/plain"), False),
        ("an empty Disallow allows", _Resp(200, b"User-agent: *\nDisallow:\n", "text/plain"), True),
        ("404: no file, no rules, allowed", _http_error(404), True),
        ("503: unreachable, assume Disallow", _http_error(503), False),
        ("no answer at all: unreachable, assume Disallow", urllib.error.URLError("timed out"), False),
        ("a 403 bot challenge in place of the file: permission unknown", _http_error(403, CHALLENGE), None)):
    with patch.object(PRUNE.urllib.request, "urlopen", _serve(answer)):
        ans = PRUNE.robots_txt.Robots(PRUNE._RobotsSession()).check("https://h.test/event/1")
    check(f"    {label}", ans["allowed"], want)

print()
print("  the link it reads is the one the sync writes")
desc = ("Blurb about the show.\n\n\U0001F4CD 1 Main St\n\n"
        "Tickets / info: https://www.meetup.com/g/events/1/\n\n\U0001F50E More on this show: https://g.test/s")
m = PRUNE.TICKETS_LINE.search(desc)
check("Tickets / info is found", m.group(1) if m else None, "https://www.meetup.com/g/events/1/")
check("a row with no Tickets / info line has nothing to probe",
      PRUNE.TICKETS_LINE.search("Just a blurb.\n\n\U0001F4CD 1 Main St"), None)


print()
print("EventStore: a TITLE that says the event is off, or that the building is shut")
# Live titles from the 2026-09-27 feed corpus (837 feeds, 87,572 rows): 37
# cancellations written into the title and 293 closure notices, all well-formed.
from mapsee_ingest import EventStore, NormalizedEvent, notice_reason  # noqa: E402

for title, want in [
    ("CANCELLED - Tech Tips Tuesday", "cancelled in the title"),
    ("Cancelled: Facebook Live: Mystery Series Week", "cancelled in the title"),
    ("POSTPONED: Redland Coast Sporting Hall of Fame", "cancelled in the title"),
    ("CANCELED - Game On! Mondays", "cancelled in the title"),
    ("Chess Club CANCELLED", "cancelled in the title"),
    ("Baby and Me Lapsit Time - CANCELLED", "cancelled in the title"),
    ("Game On: Nintendo Switch Fun at Bragtown- Cancelled!", "cancelled in the title"),
    ("Library closed for Thanksgiving", "closure notice"),
    ("Library Closed: Veterans Day", "closure notice"),
    ("NATURE CENTER CLOSED", "closure notice"),
    ("CLOSED", "closure notice"),
    ("Closed for the Holidays", "closure notice"),
    ("Museum Closed for an Event", "closure notice"),
    # ...and what must stay: full game tables, a group, events about closing,
    # accessible screenings, and comedy about cancelling.
    ("Table 12 - The Dimming - CLOSED", None),
    ("Adventures of the Silly Scoundrels (Closed)", None),
    ("Currently Closed to New Players", None),
    ("Closing Reception: Spring Show", None),
    ("Closed Captioned Movie Night", None),
    ("Cancelled Plans: An Improv Night", None),
    ("Everything Is Cancelled", None),
    ("Cancel Culture Comedy Hour", None),
    # the same word in the catalog's other languages, live titles
    ("Entfällt: Zumba mit Lorena", "cancelled in the title"),
    ("FÄLLT AUS: Musikbühne Meiendorf", "cancelled in the title"),
    ("***ABGESAGT***  Pflanzen-Flohmarkt   ***ABGESAGT***", "cancelled in the title"),
    ("ANNULE : Mapathon en ligne 2026-2027", "cancelled in the title"),
    ("Club ados - ANNULE", "cancelled in the title"),
    ("(CANCELADO) Habitar la voz", "cancelled in the title"),
    ("【開催中止】蔭平発電所見学会", "cancelled in the title"),
    # ...and look-alikes that are events
    ("Reporte anual de la biblioteca", None),
    ("Suspendidos en el tiempo: exposición", None),
    ("Annual Meeting of the Friends", None),
    ("Abgesang: ein Liederabend", None),
]:
    check(f"notice_reason({title!r})", notice_reason(title), want)

_now = datetime.now(timezone.utc) + timedelta(days=3)
_stamp = _now.strftime("%Y-%m-%dT%H:%M:%SZ")


def _ev(name, sid):
    return NormalizedEvent(source="ics:test", source_id=sid, name=name, start_utc=_stamp,
                           start_local=_stamp[:19], venue_name="Main Library",
                           latitude=47.6, longitude=-122.3)


import tempfile  # noqa: E402

with tempfile.TemporaryDirectory() as d:
    store = EventStore(os.path.join(d, "s.json"))
    real = _ev("Family Storytime", "a")
    check("the storytime itself is stored", store.upsert(real), "added")
    gone = _ev("CANCELLED - Family Storytime", "b")
    check("its cancellation notice is refused, not merged into it", store.upsert(gone), "notice")
    shut = _ev("Library Closed: Thanksgiving Day", "c")
    check("a closure notice is refused", store.upsert(shut), "notice")
    check("both are counted, apart from advertisements",
          (store.stats["notices"], store.stats["rejected"], len(store.records)), (2, 0, 1))
    check("per source", store.notices_by_source, {"ics:test": 2})


# A HOST'S CRAWL-DELAY IS A FLOOR. Barcelona's guia.barcelona.cat asks 10 s
# (2026-10-05); every probe used to sleep the same 0.35 s. A URL whose host is
# not due yet is skipped this run, never asked sooner, and the walk goes on.
class _DelayRobots:
    def check(self, url):
        slow = "slow.test" in url
        return {"allowed": True, "crawl_delay": 10 if slow else None}


_now = [1000.0]
_asked = []


def _fake_verdict(url, robots=None):
    _asked.append(url)
    return "live"


def _sleep(s):
    _now[0] += s


_order = ["https://slow.test/a", "https://slow.test/b", "https://fast.test/1",
          "https://fast.test/2", "https://slow.test/c"]
_v, _hit, _paced = PRUNE.probe_all(_order, _DelayRobots(), 0.35, 0, _now[0], verdict=_fake_verdict,
                                   clock=lambda: _now[0], sleep=_sleep)
check("a 10 s Crawl-delay host is asked once in 1.4 s, its next pages left for a later run",
      (_asked, dict(_paced)), (["https://slow.test/a", "https://fast.test/1", "https://fast.test/2"],
                               {"slow.test": 2}))
_now[0] += 20
_asked.clear()
PRUNE.probe_all(["https://slow.test/b", "https://slow.test/c"], _DelayRobots(), 0.35, 0, _now[0],
                verdict=_fake_verdict, clock=lambda: _now[0], sleep=_sleep)
check("...and asked again once its delay has passed", _asked, ["https://slow.test/b"])

print()
if FAILS:
    print(f"{len(FAILS)} FAILED: " + "; ".join(FAILS))
    sys.exit(1)
print("all cancellation checks passed")
