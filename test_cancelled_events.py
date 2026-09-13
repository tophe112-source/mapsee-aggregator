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
print("  the link it reads is the one the sync writes")
desc = ("Blurb about the show.\n\n\U0001F4CD 1 Main St\n\n"
        "Tickets / info: https://www.meetup.com/g/events/1/\n\n\U0001F50E More on this show: https://g.test/s")
m = PRUNE.TICKETS_LINE.search(desc)
check("Tickets / info is found", m.group(1) if m else None, "https://www.meetup.com/g/events/1/")
check("a row with no Tickets / info line has nothing to probe",
      PRUNE.TICKETS_LINE.search("Just a blurb.\n\n\U0001F4CD 1 Main St"), None)


print()
if FAILS:
    print(f"{len(FAILS)} FAILED: " + "; ".join(FAILS))
    sys.exit(1)
print("all cancellation checks passed")
