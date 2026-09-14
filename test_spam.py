"""The gate that keeps advertisements out of the catalogue, pinned both ways.

A filter has two ways to fail and only one of them is visible. Letting spam
through is the bug that gets reported; refusing something real is the bug that
never does, because the symptom is a source quietly getting thinner and nobody
knows what they did not see. So the "must not fire" half of this file is longer
than the "must fire" half on purpose, and every entry in it is a shape that
actually occurs in the catalogue: date ranges, years, times, postcodes, room
numbers, and the whole subject matter — psychics, tarot, prayer, healing — that
a lazier version of this filter would have taken out along with the scams.

The live sample is real. Every REJECT title below was read off gamenight.host's
public feed on 2026-09-03, and every KEEP title in the first block was read off
the SAME feed on the same day — which is the point: the instance carries genuine
listings and scam listings side by side, so a source-level ban would have thrown
away a police commission meeting and a mahjong night to catch a marabout.

    python test_spam.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mapsee_ingest import EventStore, NormalizedEvent
from mapsee_spam import MAX_SPAN_DAYS, implausible_end, spam_reason, span_days

fails = []


def check(label, cond, detail=""):
    print(f"{'ok  ' if cond else 'FAIL'} {label}{'' if cond else '   ' + str(detail)}")
    if not cond:
        fails.append(label)


def rejects(label, **kw):
    r = spam_reason(**kw)
    check(label, r is not None, "was allowed through")
    return r


def keeps(label, **kw):
    r = spam_reason(**kw)
    check(label, r is None, f"refused as: {r}")


# --- MUST FIRE: read off the live feed, 2026-09-03 ---------------------------
print("advertisements, from gamenight.host's live feed")
rejects("the reported Kirkland listing",
        name="Shatru Nashak Sudarshan Chakra Maran Mantra ॐ ☎ +91 9965500027")
rejects("a French dialling code",
        name="VRAI PORTEFEUILLE MAGIQUE +229 01 56 42 27 95")
rejects("a 00-prefixed number and a second one after it",
        name="0022991113322 ou +22953077815:VOICI UN DES PLUS PUISSANT MARABOUT DU BENIN")
rejects("a number with no separators at all",
        name="Marabout consultation gratuite, Voyance gratuite +2290160492000")
rejects("bulk accounts", name="Buy Gmail Accounts SEO")
rejects("a scam phrase with no number anywhere",
        name="Retour d'Affection Rapide en 24h, expert medium sonagnon")
rejects("...and one in French with an apostrophe we do not control",
        name="Comment faire un retour d’affection rapide et immediat avec le puissant medium")
rejects("a clean title with the pitch in the body",
        name="Free consultation this Saturday",
        description="Pandit Rahul Sharma, Black Magic Specialist. Love problem solution.")

print()
print("an end nobody could have meant is a missing fact, NOT a verdict")
# The rule that first read this as spam had to be split, and the audit is what
# forced it: Extinction Rebellion France publish "Organiser un evenement a La
# Perm" over 487 days, one instance away from a 447-day advert for Roman blinds.
# No threshold separates those, because the span is not what makes one an advert.
keeps("a ten-year span is not, by itself, spam", name="Community gathering",
      start="2026-04-11T17:30:00Z", end="2036-04-23T20:30:00Z")
check("...but the end is refused as a fact",
      implausible_end("2026-04-11T17:30:00Z", "2036-04-23T20:30:00Z") == 3665,
      implausible_end("2026-04-11T17:30:00Z", "2036-04-23T20:30:00Z"))
check("a real 16-month listing keeps its event, loses only its end",
      implausible_end("2025-08-30", "2026-12-30") == 487
      and spam_reason("Organiser un evenement a La Perm",
                      start="2025-08-30", end="2026-12-30") is None)
check("a year-long exhibition keeps BOTH",
      implausible_end("2026-01-01", "2026-11-30") is None)
check("the span is measured in whole days",
      span_days("2026-01-01", "2027-01-01") == 365, span_days("2026-01-01", "2027-01-01"))
check("a missing end is not a long event (it is an unknown one)",
      span_days("2026-01-01", None) is None)
check("an unparseable date is not a long event either",
      span_days("2026-01-01", "next Tuesday") is None)
check("...and an unreadable end is left alone rather than dropped",
      implausible_end("2026-01-01", "next Tuesday") is None)

# --- MUST NOT FIRE: the same feed, the same day ------------------------------
print()
print("real listings from the SAME instance, which a source ban would have taken")
keeps("a games night", name="Mahjong at Encorepreneur Cafe")
keeps("a public meeting with a date in the title",
      name="Board of Police Commissioners Meeting 9/14/2026")
keeps("a craft guild", name="Suminigashi September with the Mixed-Media Collage Artists Guild")
keeps("a naloxone training",
      name="Community Narcan (Naloxone) Training with McLean County Recovery Oriented Systems of Care")
keeps("a political meeting", name="ANTIFA 101 - *Part 3* & Getting Active In Nashville In 2026")

print()
print("numbers that are not phone numbers")
# The naive phone regex reads every one of these as a phone number, because a
# space and a hyphen are both plausible separators. Each of these is a real
# title shape and each one used to be a false positive waiting to happen.
keeps("an ISO date range", name="Summer Season 2026-05-16 - 2026-05-18")
keeps("door times", name="Doors 19:00 - 23:00")
keeps("a year", name="New Year's Eve 2026")
keeps("a UK postcode", name="Car boot sale, BS1 5TR")
keeps("a US ZIP+4", name="Block party 98034-1234")
keeps("a room and a date", name="Room 204, 2026-05-16")
keeps("a race distance and a year", name="Bathinda 10K 2026")
keeps("a long numeric run split by slashes", name="Bus 8/12/2026 to 9/14/2026")

print()
print("subject matter is not spam")
# The version of this filter that greps for "spiritual" takes all of these, and
# every one of them is a gathering somebody is trying to find.
keeps("a tarot night", name="Tarot & Psychic Night at the Anchor")
keeps("an astronomy talk", name="Talk: astrologer, astronomer, and the road between them")
keeps("a prayer meeting", name="Wednesday Evening Prayer Meeting")
keeps("a reiki workshop", name="Introduction to Reiki and Energy Healing")
keeps("a fortune-telling stall at a fete", name="Summer Fete: cakes, tombola, fortune teller")
keeps("a magic show", name="Close-up Magic with the Magic Circle")

print()
print("an organiser's own contact details are not an advertisement")
keeps("a number in the BODY, where it belongs",
      name="Yoga in the Park",
      description="Bring a mat. Questions? Call the studio on +1 206 555 0134.")

# --- MUST NOT FIRE: the 29 real events in the whole-table report -----------
print()
print("the real events the first full report flagged, with the source each came through")
# The first full read-only walk (744,093 rows, 2026-09-14) matched 256 rows and 29
# of them were real events. Every digit below is a 0, so this public file does
# not republish anybody's number: the rules read the SHAPE, and the check after
# this block proves every one still reaches the phone rule. Where the report cut
# a title at 70 characters inside the number, the number is completed in the
# same shape ("completed"). The two REIA descriptions matched "buy ... accounts";
# the report kept no description text, so those two are written to that shape.
REAL_WITH_NUMBERS = [
    # (rows, source, title)
    (8, "meetup", "Beach volleyball para begginers . Write me a WhatsApp +00000000000"),
    (9, "meetup", "Training Intermediate Level. WhatsApp me +00000000000 to confirm your"),
    (4, "meetup", "Training BEGGINER Level. WhatsApp me +00000000000 to confirm your assi"),
    (1, "meetup", 'The "Awkward Social" Simulator | Enroll applicable | Contact no +00 0000000000'),  # completed
    (1, "meetup", 'The "Awkward Social" Simulator | Entry fee applicable | Contact no 0000000000'),  # completed
    (1, "meetup", "The Storytelling Slam | Entry fee applicable | Contact no +00 00000000"),
    (2, "ods:OpenAgenda France",
     "L'industrie recrute en apprentissage dès septembre ! Prenez RDV au 0000000000"),  # completed
    (1, "ods:OpenAgenda France", "INFORMATION COLLECTIVE : DESSINATEUR CAO-DAO_ SE0000000000"),
]
REAL_ACCOUNT_TALK = [
    # (rows, source, title, description)
    (1, "meetup", "Alamo REIA Monthly Main Meeting: All About IRAs & Retirement Accounts",
     "Learn how to buy property with your retirement accounts."),
    (1, "meetup", "Greater Houston REIA Monthly Main Meeting",
     "Members buy homes using retirement accounts."),
]
for _rows, _src, _title in REAL_WITH_NUMBERS:
    keeps(f"{_src}, x{_rows}: {_title[:52]}", name=_title, source=_src)
for _rows, _src, _title, _body in REAL_ACCOUNT_TALK:
    keeps(f"{_src}, x{_rows}: {_title[:52]}", name=_title, description=_body, source=_src)
check("...which is all 29 of the real rows",
      sum(r[0] for r in REAL_WITH_NUMBERS) + sum(r[0] for r in REAL_ACCOUNT_TALK) == 29)

print()
print("the SOURCE is what keeps them: the same titles from Mobilizon are refused")
for _rows, _src, _title in REAL_WITH_NUMBERS:
    check(f"from mobilizon: {_title[:52]}",
          spam_reason(_title, source="mobilizon") == "phone number in title",
          spam_reason(_title, source="mobilizon"))
for _rows, _src, _title, _body in REAL_ACCOUNT_TALK:
    check(f"the account talk is in the description, where it no longer counts: {_title[:30]}",
          spam_reason(_title, _body, source="mobilizon") is None)
rejects("...while the same trade in a TITLE is refused from any source",
        name="Buy verified PayPal accounts", source="meetup")

print()
print("which sources a number in the title counts against, and what unknown means")
_AD = "Consultation gratuite +229 01 56 42 27 95"     # a number and no listed phrase
check("a number-only advert is refused from Mobilizon",
      spam_reason(_AD, source="mobilizon") == "phone number in title")
check("...and from Gancio", spam_reason(_AD, source="gancio") == "phone number in title")
check("...however the source is spelt", spam_reason(_AD, source=" Mobilizon ") == "phone number in title")
check("from Meetup a number alone is not enough", spam_reason(_AD, source="meetup") is None)
check("...nor from an OpenAgenda feed", spam_reason(_AD, source="ods:OpenAgenda France") is None)
check("...nor from an ICS calendar", spam_reason(_AD, source="ics:Visit Somewhere") is None)
check("UNKNOWN counts like open registration: no source at all is refused",
      spam_reason(_AD) == "phone number in title")
check("...and so is an empty source", spam_reason(_AD, source="") == "phone number in title")
check("a listed phrase is refused from EVERY source, Meetup included",
      spam_reason("Marabout retour affectif rapide", source="meetup") == "scam phrase in title")

# --- the gate is wired to the choke point ------------------------------------
print()
print("EventStore refuses, counts, and does not merge")
import tempfile

with tempfile.TemporaryDirectory() as d:
    store = EventStore(os.path.join(d, "s.json"))
    real = NormalizedEvent(source="mobilizon", source_id="a", name="Mahjong at Encorepreneur Cafe",
                           start_utc="2026-09-22T18:00:00Z", venue_name="Encorepreneur Cafe",
                           city="Portland", category="party")
    real.fingerprint = "fp-real"
    check("a real event is added", store.upsert(real) == "added")

    ad = NormalizedEvent(source="mobilizon", source_id="b",
                         name="RETOUR AFFECTIF +229 01 56 42 27 95",
                         start_utc="2026-09-22T18:00:00Z", venue_name="Encorepreneur Cafe",
                         city="Portland", category="community")
    # THE SAME FINGERPRINT the real event has. A scam listing pinned to a real
    # venue on a real night shares (name-ish, date, venue, city) often enough
    # that this is not a contrived case — and if the gate ran after the dedupe,
    # branch 2 of upsert would fold the advertisement's description and link
    # INTO the mahjong night rather than rejecting it.
    ad.fingerprint = "fp-real"
    check("an advertisement is refused", store.upsert(ad) == "rejected")
    check("...and did not merge into the real event",
          store.records["fp-real"]["name"] == "Mahjong at Encorepreneur Cafe",
          store.records["fp-real"]["name"])
    check("...and did not leave a source ref behind",
          len(store.records["fp-real"].get("sources", [])) == 1,
          store.records["fp-real"].get("sources"))
    check("the store holds exactly one row so far", len(store.records) == 1, len(store.records))

    check("the refusal is counted", store.stats["rejected"] == 1, store.stats)
    check("...against the source that sent it",
          store.rejected_by_source.get("mobilizon") == 1, store.rejected_by_source)
    check("...with a reason worth reading", bool(store.reject_reasons), store.reject_reasons)
    check("...and a sample naming the title",
          any("RETOUR AFFECTIF" in s for s in store.reject_samples), store.reject_samples)

    # An implausible end costs the END, not the row.
    long_ev = NormalizedEvent(source="mobilizon", source_id="c", name="Ten-year listing",
                              start_utc="2026-04-11T17:30:00Z", end_utc="2036-04-23T20:30:00Z",
                              city="Kirkland", category="community")
    long_ev.fingerprint = "fp-long"
    check("a ten-year listing is still stored", store.upsert(long_ev) == "added")
    check("...with its end dropped, so cleanup can reach it",
          store.records["fp-long"]["end_utc"] is None, store.records["fp-long"]["end_utc"])
    check("...in BOTH spellings, or the sync reads the surviving one",
          store.records["fp-long"]["end_local"] is None, store.records["fp-long"]["end_local"])
    check("...and it is counted against its source",
          store.stats["unbounded"] == 1 and store.unbounded_by_source.get("mobilizon") == 1,
          (store.stats, store.unbounded_by_source))
    check("...and NOT counted as a refusal, which is a different conversation",
          store.stats["rejected"] == 1, store.stats)

    # A refused row must not be remembered as seen: the next run has to be free
    # to accept it if the rule changes, and source_to_fp is what would otherwise
    # quietly re-point (mobilizon, b) at the real event's fingerprint for ever.
    check("a refused row leaves no identity behind",
          ("mobilizon", "b") not in store.source_to_fp, store.source_to_fp)

    # upsert passes the adapter's source: a Meetup session with a WhatsApp number
    # is stored, and the same title through Mobilizon is refused.
    volley = NormalizedEvent(source="meetup", source_id="v1", name=REAL_WITH_NUMBERS[0][2],
                             start_utc="2026-09-20T09:00:00Z", venue_name="Beach",
                             city="Barcelona", category="fitness")
    volley.fingerprint = "fp-volley"
    check("a Meetup session with a WhatsApp number is stored", store.upsert(volley) == "added")
    same = NormalizedEvent(source="mobilizon", source_id="v2", name=REAL_WITH_NUMBERS[0][2],
                           start_utc="2026-09-21T09:00:00Z", venue_name="Beach",
                           city="Barcelona", category="fitness")
    same.fingerprint = "fp-same"
    check("...and the same title through Mobilizon is refused", store.upsert(same) == "rejected")

# --- the purge will not run away with itself ---------------------------------
print()
print("the backfill's tripwire")
# mapsee_spam_purge.py deletes on a content judgement, on a schedule, with
# nobody watching. The failure that needs catching is NOT a spam wave — it is a
# rule in mapsee_spam.py widened until it matches ordinary listings, which looks
# from the outside exactly like a very effective run. This is graded here rather
# than in the purge because the tripwire has to work on the day something else
# has already gone wrong.
from mapsee_spam_purge import too_many

check("an ordinary run writes", too_many(120, 8000, 0.05, 200) is False)
check("a run matching half the catalogue does not",
      too_many(4000, 8000, 0.05, 200) is True)
check("exactly at the ceiling is still allowed (it is a > , not a >=)",
      too_many(400, 8000, 0.05, 200) is False)
check("a tiny sample is never judged — 3 of 5 is noise, not a signal",
      too_many(3, 5, 0.05, 200) is False)
check("...and the sample floor is what decides that, not the share",
      too_many(199, 199, 0.05, 200) is False and too_many(199, 200, 0.05, 200) is True)
check("nothing read, nothing blocked", too_many(0, 0, 0.05, 200) is False)

print()
print("the report names where a match came from")
# The events table has no source column, so the purge reads the host of the
# "Tickets / info:" line the sync writes into every imported description. It
# is the thing to read before allowing deletes: forty rows from one spammed
# instance is the purge catching up, forty spread across ticketing sites is a
# rule that has started matching ordinary listings.
from mapsee_spam_purge import source_host

_body = "A night out.\n\nTickets / info: https://www.gamenight.host/events/abc"
check("the host of the sync's own link line", source_host(_body) == "gamenight.host",
      source_host(_body))
check("...not the first URL somebody typed into the body",
      source_host("Call https://scam.example/now\n\nTickets / info: https://mobilizon.fr/events/1")
      == "mobilizon.fr")
check("a row with no link says so rather than guessing",
      source_host("no link here") == "(no link)" and source_host(None) == "(no link)")

print()
print("the walk survives its own cursor")
# PostgREST returns starts_at as "...+00:00" and a bare "+" in a query string
# is a space: the first live run read 500 rows and died on window two with a 400.
from mapsee_spam_purge import window_query

_q = window_query("https://example.supabase.co/rest/v1/events", "external_source=eq.mapsee",
                  "2026-08-21T08:00:00+00:00", 500)
check("a '+00:00' cursor is sent as %2B, not read as a space",
      "starts_at=gte.2026-08-21T08%3A00%3A00%2B00%3A00&" in _q, _q)
check("...and every window still carries the scope", "?external_source=eq.mapsee&" in _q, _q)

print()
print("the walk reaches every row, including an instant bigger than a page")
# The first live report read 2,285 rows and stopped for good at an instant 500+
# rows share (2026-09-05T23:00:00+00:00), so nothing after it - every upcoming
# row - was ever judged. This drives walk() over a fake table ordered the way
# PostgREST orders it: a 1,203-row instant, with ordinary rows either side.
from mapsee_spam_purge import walk

_rows = [{"id": f"a{_i:05d}", "starts_at": f"2026-09-01T{_i % 24:02d}:00:00+00:00"}
         for _i in range(1300)]
_rows += [{"id": f"c{_i:05d}", "starts_at": "2026-09-05T23:00:00+00:00"} for _i in range(1203)]
_rows += [{"id": f"d{_i:05d}", "starts_at": "2026-09-06T08:00:00+00:00"} for _i in range(499)]
_rows += [{"id": f"e{_i:05d}", "starts_at": f"2026-10-{1 + _i % 28:02d}T12:00:00+00:00"}
          for _i in range(777)]
_table = sorted(_rows, key=lambda r: (r["starts_at"], r["id"]))


def _window(op, at, page=500):
    return [r for r in _table if (r["starts_at"] >= at if op == "gte" else r["starts_at"] > at)][:page]


def _instant(at, after, page=500):
    return [r for r in _table if r["starts_at"] == at and r["id"] > after][:page]


_seen = set()
_ended, _ = walk(_window, _instant, "2021-09-15T05:41:32Z", 500, lambda: False,
                 lambda r: _seen.add(r["id"]))
check("every row is reached, across a 1,203-row instant", len(_seen) == len(_table),
      f"{len(_seen)} of {len(_table)}")
check("...and the walk says it reached the end", _ended == "end of the table", _ended)
_ticks = {"n": 0}


def _budget():
    _ticks["n"] += 1
    return _ticks["n"] > 3


_ended2, _at2 = walk(_window, _instant, "2021-09-15T05:41:32Z", 500, _budget, lambda r: None)
check("a walk stopped by its budget says so, and where",
      "NOT examined" in _ended2 and _at2 in _ended2, _ended2)

print()
print("the purge infers a row's source from its link, because the table stores none")
from mapsee_spam_purge import instance_hosts, row_source

_inst = instance_hosts()
check("a retired spam host is known as a Mobilizon instance (from _not_included)",
      _inst.get("gamenight.host") == "mobilizon", len(_inst))
check("...and so is a configured one", _inst.get("mobilizon.fr") == "mobilizon")
check("a Meetup link reads as meetup",
      row_source("x\n\nTickets / info: https://www.meetup.com/g/events/1/", _inst) == "meetup")
check("an OpenAgenda link reads as ods",
      row_source("Tickets / info: https://openagenda.com/fr/a/events/b", _inst) == "ods")
check("an advertiser's own page is unknown",
      row_source("Tickets / info: https://papafagla.com/x", _inst) is None)
check("...and so is a row with no link left", row_source("no link here", _inst) is None)
check("so the purge keeps a Meetup session and refuses a number-only advert on a spam page",
      spam_reason(REAL_WITH_NUMBERS[0][2],
                  source=row_source("Tickets / info: https://www.meetup.com/x", _inst)) is None
      and spam_reason(_AD, source=row_source("Tickets / info: https://papafagla.com/x", _inst))
      == "phone number in title")

# --- the cap is a value, not a magic number ----------------------------------
print()
print("the cap itself")
check("a year-long exhibition keeps its end", MAX_SPAN_DAYS >= 366, MAX_SPAN_DAYS)
check("...and nothing survives long enough to outlive mapsee_cleanup",
      MAX_SPAN_DAYS <= 800, MAX_SPAN_DAYS)
check("exactly at the cap is still a fact", implausible_end("2026-01-01", None) is None)

print()
print(f"{'FAILURES: ' + ', '.join(fails) if fails else 'all checks passed'}")
sys.exit(1 if fails else 0)
