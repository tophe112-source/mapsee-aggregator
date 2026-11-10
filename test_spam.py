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

# --- the daily run reads what arrived, the weekly one reads the table --------
# mapsee_spam_purge.main() over a fake PostgREST (requests stubbed, the clock
# pinned): no network, no database. Every request is kept, so a test can count
# the rows each run READ and ask what was sent.
print()
print("the purge's daily run reads what arrived since the last one")
import contextlib
import copy
import io
import json
import re
import tempfile
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlsplit

import mapsee_spam_purge as PURGE

_T0 = datetime(2026, 11, 10, 7, 40, tzinfo=timezone.utc)


def _iso(at):
    return at.strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _norm(v):
    return v.replace("Z", "+00:00") if isinstance(v, str) else v


class _Resp:
    def __init__(self, status=200, rows=None, text="", headers=None):
        self.status_code, self._rows, self.text, self.headers = status, rows, text, headers or {}

    def json(self):
        return self._rows


class _FakeDB:
    """eq / is.null / gt / gte / in filters, order and limit over a list of rows."""

    def __init__(self, rows):
        self.rows = {r["id"]: r for r in rows}
        self.gets, self.read, self.timeout_on, self.silent_on = [], 0, None, None

    def _match(self, url):
        out = list(self.rows.values())
        order, limit = None, None
        for k, v in parse_qsl(urlsplit(url).query, keep_blank_values=True):
            if k == "select":
                continue
            if k == "order":
                order = [c.split(".")[0] for c in v.split(",")]
            elif k == "limit":
                limit = int(v)
            elif v == "is.null":
                out = [r for r in out if r.get(k) is None]
            elif v.startswith("in.("):
                ids = set(v[4:-1].split(","))
                out = [r for r in out if r[k] in ids]
            else:
                op, val = v.split(".", 1)
                val = _norm(val)
                test = {"eq": lambda x: x == val, "gt": lambda x: x is not None and x > val,
                        "gte": lambda x: x is not None and x >= val}[op]
                out = [r for r in out if test(r.get(k))]
        if order:
            out.sort(key=lambda r: tuple(r[c] for c in order))
        return out[:limit] if limit else out

    def get(self, url, headers=None, timeout=None):
        self.gets.append(url)
        if self.silent_on and self.silent_on in url:
            raise type("ReadTimeout", (Exception,), {})("Read timed out. (read timeout=120)")
        if self.timeout_on and self.timeout_on in url:
            return _Resp(500, text='{"code":"57014","message":"canceling statement due to statement timeout"}')
        rows = self._match(url)
        self.read += len(rows)
        return _Resp(200, copy.deepcopy(rows))

    def delete(self, url, headers=None, timeout=None):
        hit = self._match(url)
        for r in hit:
            del self.rows[r["id"]]
        return _Resp(204, headers={"Content-Range": f"*/{len(hit)}"})

    def patch(self, url, headers=None, json=None, timeout=None):
        hit = self._match(url)
        for r in hit:
            self.rows[r["id"]].update(json)
        return _Resp(204, headers={"Content-Range": f"*/{len(hit)}"})


_AD_LINK = "Tickets / info: https://papafagla.com/x"


def _row(i, created, start_days=10, *, ad=False, hidden=False, far_end=False, source="mapsee"):
    start = _T0 + timedelta(days=start_days, minutes=i % 600)
    return {"id": f"{i:08d}", "external_source": source,
            "title": f"{_AD} {i}" if ad else f"Quiz night {i}",
            "description": _AD_LINK if ad else "A quiz.\nTickets / info: https://www.meetup.com/x",
            "starts_at": _iso(start), "ends_at": _iso(start + timedelta(days=900 if far_end else 0, hours=2)),
            "created_at": _iso(created), "hidden_at": _iso(created) if hidden else None}


def _table():
    """6,000 imported rows that arrived 20-200 days ago, 40 in the hour before
    the first run, 500 community rows: 14 adverts (2 hidden), 4 ends to clear,
    one advert that began six years ago, one community 'advert'."""
    rows = []
    for i in range(6000):
        rows.append(_row(i, _T0 - timedelta(days=20 + i % 180, minutes=i), start_days=-30 + i % 150,
                         ad=i % 500 == 7, hidden=i % 20 == 3, far_end=i % 1500 == 11))
    rows[3]["title"], rows[3]["description"] = f"{_AD} hidden", _AD_LINK          # i % 20 == 3: hidden
    rows[23]["title"], rows[23]["description"] = f"{_AD} hidden too", _AD_LINK
    rows[9] = _row(9, _T0 - timedelta(days=40), start_days=-6 * 365, ad=True)     # before the floor
    rows += [_row(10000 + i, _T0 - timedelta(minutes=1 + i)) for i in range(40)]
    rows += [_row(20000 + i, _T0 - timedelta(days=30), source=None, ad=i == 0) for i in range(500)]
    return rows


def _arrivals(day, n=300, ads=3):
    """A day's imports, created between two runs: `ads` visible adverts, one
    hidden advert, one end to clear, one advert that began six years ago and
    five community rows."""
    base = 100000 * day
    at = lambda i: _T0 + timedelta(days=day - 2, hours=2, seconds=i * 60)       # noqa: E731
    rows = [_row(base + i, at(i), ad=i < ads, far_end=i == 50) for i in range(n)]
    rows.append(_row(base + n, at(n), ad=True, hidden=True))
    rows.append(_row(base + n + 1, at(n + 1), start_days=-6 * 365, ad=True))
    rows += [_row(base + n + 2 + i, at(n + 2 + i), source=None) for i in range(5)]
    return rows


def _purge(db, now, *argv, hash_=None):
    """(exit code, output, distinct rows this run read: its own 'read N row(s)')."""
    saved = (PURGE.requests, PURGE._now, PURGE.predicate_hash, sys.argv, dict(os.environ))
    out = io.StringIO()
    PURGE.requests, PURGE._now = db, (lambda: now)
    if hash_:
        PURGE.predicate_hash = lambda here=None: hash_
    os.environ.update(SUPABASE_URL="https://db.test", SUPABASE_SERVICE_ROLE_KEY="k")
    sys.argv = ["mapsee_spam_purge.py", *argv]
    try:
        with contextlib.redirect_stdout(out):
            try:
                code = PURGE.main()
            except SystemExit as ex:
                code = ex.code
    finally:
        PURGE.requests, PURGE._now, PURGE.predicate_hash, sys.argv = saved[:4]
        os.environ.clear()
        os.environ.update(saved[4])
    said = re.search(r"read (\d+) row\(s\)", out.getvalue())
    n = int(said.group(1)) if said else 0
    db.total = getattr(db, "total", 0) + n
    return code, out.getvalue(), n


def _ads(db, visible_only=False):
    """The adverts a full walk could still delete: imported, inside the floor."""
    return sorted(r["id"] for r in db.rows.values() if r["title"].startswith(_AD)
                  and r["external_source"] == "mapsee" and r["starts_at"] > "2022"
                  and not (visible_only and r["hidden_at"]))


def _never(db):
    """Adverts no walk may touch: a community row, or one that began before the floor."""
    return sorted(r["id"] for r in db.rows.values() if r["title"].startswith(_AD)
                  and (r["external_source"] != "mapsee" or r["starts_at"] < "2022"))


def _far_ends(db):
    return sorted(r["id"] for r in db.rows.values()
                  if r["ends_at"] and implausible_end(r["starts_at"], r["ends_at"]) is not None)


_tmp = tempfile.mkdtemp()
_state = os.path.join(_tmp, "spam_purge_state.json")
_inc, _full = _FakeDB(_table()), _FakeDB(_table())       # --state every day / the full walk every day

# Day 1: no state, so the first scheduled run is the walk every run used to be.
_c1, _o1, _r1 = _purge(_inc, _T0, "--apply", "--state", _state)
_cf, _of, _rf = _purge(_full, _T0, "--apply")
check("no state: the first run is the FULL walk, and says why",
      _c1 == 0 and "Walk: FULL - no state at" in _o1 and "(full walk)" in _o1, _o1[:300])
check("day 1 reads the whole table either way (6,039 imported rows inside the floor)",
      (_r1, _rf) == (6039, 6039) and "read 6039 row(s)" in _o1, (_r1, _rf))
check("...deletes the same 14 adverts, hidden ones included, and clears the same 4 ends",
      "Deleted 14 advertisement(s); cleared the end date on 4 row(s)" in _o1
      and _inc.rows.keys() == _full.rows.keys() and _far_ends(_inc) == _far_ends(_full) == [], _o1[-400:])
check("...and never the advert that began before the floor, nor a community row",
      _ads(_inc) == [] and _never(_inc) == ["00000009", "00020000"], (_ads(_inc), _never(_inc)))
_s1 = json.load(open(_state))
check("a complete --apply run writes the state: both stamps are when it BEGAN",
      (_s1["full_at"], _s1["watermark"], _s1["predicate"]) ==
      ("2026-11-10T07:40:00Z", "2026-11-10T07:40:00Z", PURGE.predicate_hash()), _s1)

# Day 2: 300 imports arrived. The daily run reads them; the old walk reads everything.
for _db in (_inc, _full):
    for _r in _arrivals(2):
        _db.rows[_r["id"]] = copy.deepcopy(_r)
_c2, _o2, _r2 = _purge(_inc, _T0 + timedelta(days=1), "--apply", "--state", _state)
_cf, _of, _rf = _purge(_full, _T0 + timedelta(days=1), "--apply")
check("day 2 is INCREMENTAL: rows created since the last run began, less 3 h",
      _c2 == 0 and "Walk: INCREMENTAL - rows created since 2026-11-10T04:40:00Z" in _o2, _o2[:300])
check("ROWS READ, day 2: 6,326 by the full walk, 340 by the daily run (300 new + the 40 of the overlap)",
      (_rf, _r2) == (6326, 340), (_rf, _r2))
_daily = [u for u in _inc.gets if "created_at=" in u][0]
check("the daily read: scoped, visible rows only (events_created_at_idx's predicate), inside the floor, by created_at",
      "?external_source=eq.mapsee&hidden_at=is.null&starts_at=gte.2021-11-" in _daily
      and "&created_at=gte.2026-11-10T04%3A40%3A00Z&" in _daily
      and "&select=id,title,description,starts_at,ends_at,created_at&order=created_at.asc,id.asc&limit=500" in _daily,
      _daily)
check("the daily run deletes the 3 new visible adverts and clears the new end, as the full walk does",
      "Deleted 3 advertisement(s); cleared the end date on 1 row(s)" in _o2
      and _ads(_inc, visible_only=True) == _ads(_full, visible_only=True) and _far_ends(_inc) == [], _o2[-300:])
check("WHAT WAITS: the one new advert that is already hidden (off the map) is the only difference",
      sorted(_inc.rows.keys() - _full.rows.keys()) == ["00200300"], sorted(_inc.rows.keys() - _full.rows.keys()))
check("a newly imported advert that began before the floor is not reached by created_at either",
      "00200301" in _inc.rows and "00200301" in _full.rows)
_s2 = json.load(open(_state))
check("the watermark moves, the full walk's stamp does not",
      (_s2["full_at"], _s2["watermark"]) == ("2026-11-10T07:40:00Z", "2026-11-11T07:40:00Z"), _s2)

# Days 3-7: the same, each day's arrivals.
_reads = []
for _d in range(3, 8):
    for _db in (_inc, _full):
        for _r in _arrivals(_d, n=250, ads=2):
            _db.rows[_r["id"]] = copy.deepcopy(_r)
    _c, _o, _r_inc = _purge(_inc, _T0 + timedelta(days=_d - 1), "--apply", "--state", _state)
    _, _, _r_full = _purge(_full, _T0 + timedelta(days=_d - 1), "--apply")
    _reads.append((_r_inc, _r_full, "INCREMENTAL" in _o, _ads(_inc, True) == _ads(_full, True)))
check("days 3-7 stay incremental, 250 rows a day, with the full walk's visible deletes",
      all(r[0] == 250 and r[2] and r[3] for r in _reads), _reads)
check("...while the daily full walk keeps reading the table: 6,573 to 7,565 rows",
      [r[1] for r in _reads] == [6573, 6821, 7069, 7317, 7565], [r[1] for r in _reads])

# Day 8: a week after the last full walk.
_c8, _o8, _r8 = _purge(_inc, _T0 + timedelta(days=7), "--apply", "--state", _state)
_, _, _rf8 = _purge(_full, _T0 + timedelta(days=7), "--apply")
check("day 8 is FULL again: the last complete full walk is 7.0 days old",
      _c8 == 0 and "Walk: FULL - the last complete full walk began 2026-11-10T07:40:00Z, 7.0 days ago" in _o8
      and (_r8, _rf8) == (7568, 7562), (_o8[:200], _r8, _rf8))
check("...and it deletes the 6 hidden adverts the daily runs left: first full + daily + weekly == full every day",
      "Deleted 6 advertisement(s)" in _o8 and _inc.rows == _full.rows, _o8[-300:])
check("ROWS READ over the 8 days: 15,197 with --state against 55,272 by a daily full walk (-72.5%)",
      (_inc.total, _full.total) == (15197, 55272), (_inc.total, _full.total))
check("...and on the 6 incremental days: 1,590 against 41,671 (-96.2%)",
      (_r2 + sum(r[0] for r in _reads), _rf + sum(r[1] for r in _reads)) == (1590, 41671),
      (_r2 + sum(r[0] for r in _reads), _rf + sum(r[1] for r in _reads)))
check("a full walk a day early would be too soon: 6.4 days is still incremental, 6.5 is full",
      [PURGE.plan_walk(_s2, None, _s2["predicate"], _T0 + timedelta(days=d))[0] for d in (6.4, 6.5)]
      == ["incremental", "full"])

# The rules change: the next run is the full walk, whatever the state's age.
_c9, _o9, _r9 = _purge(_inc, _T0 + timedelta(days=8), "--apply", "--state", _state, hash_="other-rules")
check("a change to mapsee_spam.py or the purge is a FULL walk the next day, and the state takes the new rules",
      "Walk: FULL - the rules changed" in _o9 and json.load(open(_state))["predicate"] == "other-rules", _o9[:200])
check("predicate_hash reads exactly the two files that decide a delete, the same on CRLF and LF",
      PURGE._PREDICATE_FILES == ("mapsee_spam.py", "mapsee_spam_purge.py") and len(PURGE.predicate_hash()) == 64)
_hd = tempfile.mkdtemp()
for _name, _body in (("mapsee_spam.py", b"a\nb\n"), ("mapsee_spam_purge.py", b"c\n")):
    open(os.path.join(_hd, _name), "wb").write(_body)
_h_lf = PURGE.predicate_hash(_hd)
open(os.path.join(_hd, "mapsee_spam.py"), "wb").write(b"a\r\nb\r\n")
_h_crlf = PURGE.predicate_hash(_hd)
open(os.path.join(_hd, "mapsee_spam.py"), "wb").write(b"a\nB\n")
check("...a CRLF checkout hashes as LF, and one changed byte is a different hash",
      _h_lf == _h_crlf and PURGE.predicate_hash(_hd) != _h_lf)

# What must NOT move the state, and what a bad state means.
_before = open(_state).read()
for _r in _arrivals(10, n=250, ads=2):
    _inc.rows[_r["id"]] = copy.deepcopy(_r)
_c, _o, _ = _purge(_inc, _T0 + timedelta(days=9), "--state", _state, hash_="other-rules")
check("a REPORT follows the same plan, writes nothing and leaves the state alone",
      _c == 0 and "INCREMENTAL" in _o and "Report only" in _o and open(_state).read() == _before
      and len(_ads(_inc, True)) == 2, _o[:200])
_c, _o, _ = _purge(_inc, _T0 + timedelta(days=9), "--apply", "--state", _state, "--max-seconds", "0", hash_="other-rules")
check("a walk the budget cut short does not move the state", "State: NOT updated" in _o and open(_state).read() == _before, _o[-300:])
_c, _o, _ = _purge(_inc, _T0 + timedelta(days=9), "--apply", "--state", _state, "--max-share", "0.001", hash_="other-rules")
check("a run the share ceiling refuses exits 1, deletes nothing and does not move the state",
      _c == 1 and "REFUSING TO WRITE" in _o and open(_state).read() == _before
      and len(_ads(_inc, True)) == 2, _o[-300:])
_inc.timeout_on = "created_at=gte."
_c, _o, _r = _purge(_inc, _T0 + timedelta(days=9), "--apply", "--state", _state, hash_="other-rules")
_inc.timeout_on = None
check("a created_at read that times out (57014) falls back to the full walk in the same run, loudly",
      _c == 0 and "::warning::Spam purge: the created_at read timed out" in _o and "(full walk)" in _o
      and "Deleted 3 advertisement(s)" in _o and _ads(_inc) == [], _o[-400:])
check("...which is recorded as the full walk it was",
      json.load(open(_state))["full_at"] == "2026-11-19T07:40:00Z")
for _r in _arrivals(11, n=250, ads=2):
    _inc.rows[_r["id"]] = copy.deepcopy(_r)
_inc.silent_on = "created_at=gte."
_c, _o, _r = _purge(_inc, _T0 + timedelta(days=10), "--apply", "--state", _state, hash_="other-rules")
_inc.silent_on = None
check("...and so does one that never answers inside the client's 120 s",
      _c == 0 and "the created_at read timed out" in _o and "(full walk)" in _o and _ads(_inc) == [], _o[-300:])
_nostate = _FakeDB(_table())
_c, _o, _r = _purge(_nostate, _T0, "--apply")
check("without --state nothing changes: the full walk, no plan line, no file",
      _c == 0 and "Walk:" not in _o and _r == 6039 and not os.path.exists(os.path.join(_tmp, "none.json")), _o[:200])
_good = json.load(open(_state))
_plan = lambda st, why=None, now=_T0 + timedelta(days=10), **kw: PURGE.plan_walk(  # noqa: E731
    st, why, "other-rules", now, **kw)[0]
check("--full walks the table whatever the state says", _plan(_good, force_full=True) == "full" and _plan(_good) == "incremental")
check("a state stamped in the future, or whose watermark is before its full walk, is not believed",
      _plan(dict(_good, watermark="2026-12-25T00:00:00Z")) == "full"
      and _plan(dict(_good, watermark="2026-11-01T00:00:00Z")) == "full")
_bad = os.path.join(_tmp, "bad.json")
_whys = []
for _body in ("{not json", "[]", json.dumps(dict(_good, version=0)), json.dumps(dict(_good, predicate="")),
              json.dumps(dict(_good, watermark="yesterday")), json.dumps({k: v for k, v in _good.items() if k != "full_at"})):
    open(_bad, "w").write(_body)
    _whys.append(PURGE.load_state(_bad))
check("an unreadable, wrong-version or incomplete state is None with a reason, never an exception",
      all(st is None and why for st, why in _whys) and PURGE.load_state(os.path.join(_tmp, "absent.json"))[0] is None,
      _whys)

print()
print(f"{'FAILURES: ' + ', '.join(fails) if fails else 'all checks passed'}")
sys.exit(1 if fails else 0)
