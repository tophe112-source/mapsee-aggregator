#!/usr/bin/env python3
"""
test_curate_audit.py - the weekly audit's three faults that kept FEED_DOWN wrong.

FEED_DOWN (mapsee_health_check.configured_dead) lists every configured feed
whose curation_ledger.json row is `fail`, and on 2026-10-29 it listed 130, all
from the 2026-10-18 audit. Three things made that list wrong:

  * THE AUDIT NO LONGER FIT ITS JOB AND KEPT NOTHING WHEN CUT OFF. 2026-10-25
    (run 40179210416): 5,094 probes in 240 minutes, cancelled mid-jsonld at the
    job cap, the snapshot copied inside the cancelled step, the commit skipped.
    Now a wall-clock budget, the oldest ledger check first across every file,
    a save every few hundred probes and on any way out, and the workflow's
    snapshot and commit on always().
  * AN EMPTY CIVICPLUS CATEGORY WAS `fail`. A quiet category and a deleted one
    both answer an empty VCALENDAR (474 bytes for each on urbandale.org,
    2026-10-29); 82 of the 130 were CivicPlus answering exactly that. The
    site's /iCalendar.aspx list decides: listed -> empty, gone -> fail.
  * verify_opendata REFUSED A DATASET THE ADAPTER INGESTS. Calgary's
    `next_date_times` ("October 27 2026 1 - 4 p.m.") failed as "not a date
    column" while production kept 12 of 13 rows. It now reads dates with the
    adapter's own parser.

And the follow-ups the review of the round-1 triage asked for (2026-10-29):

  * A CALENDAR IS WHAT mapsee_ingest_ics READS. Forterra's BEGIN:VCALENDAR
    sits at character 8,120, after Elementor's CSS; the check looked at 400.
  * A VALID EMPTY CALENDAR IS `empty` ONLY WHERE A MISSING ONE ANSWERS
    SOMETHING ELSE, per platform, measured (EMPTY_CALENDAR_RULES), and The
    Events Calendar between seasons is decided by its REST route.
  * verify_tribe: the plugin's route answering an empty `events` list is a
    quiet calendar, `empty`; a 404 (rest_no_route or a page) is still `fail`.
    The 36 tribe sites kept `empty` by hand on 2026-10-29 answered that list.
  * CRAWL-DELAY between a host's requests, in the audit and in verify.
  * THE DEADLINE saves first, then gives stragglers a bounded grace.
  * The ledger survives a dump that dies; a Verdict survives copy and pickle.

And the final review of round 2 (2026-10-29), section G: a type with no
verifier (mylisting) is never written; robots.txt is obeyed before an entry's
first request (`refused`, nothing fetched) and before a jsonld link page; a
probe the pacing stopped decides nothing; a scoped ?ical=1 export is not
tested against the site's route; Mobilizon's and Modern Events Calendar's
quiet answers are `empty`; and the three mutations the review left alive.

Runs the REAL cmd_audit, verify_ics, verify_opendata and cmd_verify over temp
trees and stub sessions - no network, no database.

Run: python test_curate_audit.py
"""
import datetime
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import types
from contextlib import redirect_stdout

import yaml

import catalog_curate as cc
import mapsee_ingest_opendata as od

fails = []
ROOT = os.path.dirname(os.path.abspath(__file__))


def check(label, cond, detail=""):
    """`cond` may be a callable, so a regression that raises is graded as the
    failed assertion it is rather than ending the file in a traceback."""
    if callable(cond):
        try:
            cond = cond()
        except Exception as exc:  # noqa: BLE001
            cond, detail = False, f"raised {type(exc).__name__}: {exc}"
    print(f"{'ok  ' if cond else 'FAIL'}  {label}{'' if cond else '   ' + str(detail)}")
    if not cond:
        fails.append(label)


class Resp:
    def __init__(self, code, body, url, ctype="text/html"):
        self.status_code, self.text, self.url = code, body, url
        self.content = body.encode("utf-8")
        self.headers = {"content-type": ctype, "Content-Type": ctype}
        self.encoding = self.apparent_encoding = None

    def json(self):
        return json.loads(self.text)


class Session:
    """Bodies by URL; records every URL asked for. A body may be a callable."""

    def __init__(self, pages):
        self.pages = pages
        self.headers = {"User-Agent": cc.UA}
        self.asked = []

    def get(self, url, timeout=None, allow_redirects=True, params=None, **kw):
        self.asked.append(url)
        got = self.pages.get(url)
        if callable(got):
            got = got(params)
        if isinstance(got, Exception):
            raise got
        if got is None:
            return Resp(404, "not found", url)
        code, body, *ctype = got
        return Resp(code, body, url, *(ctype or ["text/calendar"]))


cc.VERIFY_BACKOFF_S = 0          # a retry here must cost a request, not a sleep
TODAY = "20261029"
os.environ["MAPSEE_TODAY"] = TODAY

# ============================================================================
# B2. An empty CivicPlus category: still listed is `empty`, gone is `fail`.
# ============================================================================
CITY = "https://www.city.example"
CP = CITY + "/common/modules/iCalendar/iCalendar.aspx?catID={}&feed=calendar"
EMPTY = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:iCalendar-Ruby\r\nCALSCALE:GREGORIAN\r\nEND:VCALENDAR\r\n"
PAST2 = ("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nDTSTART:20250601T100000\r\nSUMMARY:a\r\nEND:VEVENT\r\n"
         "BEGIN:VEVENT\r\nDTSTART:20250701T100000\r\nSUMMARY:b\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
# The shape of urbandale.org/iCalendar.aspx (2026-10-29): one absolute href per
# category, its name as the link text, entities in the names.
LISTING = "<html><body><ul>" + "".join(
    f'<li><a href="{CP.format(i).replace("&", "&amp;")}" title="{n}">{n}</a></li>'
    for i, n in ((14, "Community Events"), (23, "Sports &amp; Recreation"),
                 (34, "Library Events"), (39, "Concerts"))) + "</ul></body></html>"
ALLOW = (200, "User-agent: *\nDisallow: /admin/\n", "text/plain")

check("a CivicPlus feed URL yields its category id",
      cc.civicplus_cat_id(CP.format(34)) == "34", cc.civicplus_cat_id(CP.format(34)))
check("...case-insensitively, and wherever catID sits in the query",
      cc.civicplus_cat_id("https://x.gov/common/modules/icalendar/icalendar.aspx?feed=calendar&catid=7") == "7")
check("...and nothing else is CivicPlus",
      all(cc.civicplus_cat_id(u) is None for u in (
          "https://lib.libcal.com/ical_subscribe.php?cid=9035", "https://club.example/?ical=1",
          "https://x.example/events.ics", None, "")))

# The classification on its own: no fetch, no wording luck.
listed = {"cats": {"34": "Library Events", "23": "Sports & Recreation"}}
v = cc.civicplus_verdict("34", listed)
check("listed -> empty, naming the category",
      v.status == "empty" and cc._status_for(False, v) == "empty"
      and v == "0 vevents / 0 future; catID 34 still listed on /iCalendar.aspx as 'Library Events'", v)
v = cc.civicplus_verdict("99987", listed)
check("not listed -> fail", v.status == "fail" and cc._status_for(False, v) == "fail"
      and "catID 99987 no longer listed on /iCalendar.aspx" in v, v)
v = cc.civicplus_verdict("34", {"error": "answered 503"})
check("list unreadable -> fail, as before", cc._status_for(False, v) == "fail" and v.startswith("0 vevents"), v)
check("a stated status beats the note's shape both ways",
      cc._status_for(False, cc.Verdict("3 vevents / 0 future", "fail")) == "fail"
      and cc._status_for(False, cc.Verdict("0 vevents / 0 future", "empty")) == "empty")
check("...and a plain note is classified exactly as before",
      cc._status_for(False, "0 vevents / 0 future") == "fail"
      and cc._status_for(False, "3 vevents / 0 future") == "empty"
      and cc._status_for(True, "x") == "ok")

# Through the real verifier, the way the audit calls it.
cc._CIVICPLUS_LISTS.clear()
sess = Session({CITY + "/robots.txt": ALLOW, CITY + "/iCalendar.aspx": (200, LISTING, "text/html"),
                CP.format(34): (200, EMPTY), CP.format(99987): (200, EMPTY),
                CP.format(23): (200, EMPTY), CP.format(14): (200, PAST2)})


def audit_verdict(s, url):
    ok, note = cc._verify_with_retry(cc.VERIFIERS["ics"], s, {"type": "ics", "url": url})
    return ok, cc._status_for(ok, note), str(note)


r34 = audit_verdict(sess, CP.format(34))
check("an empty category the site still lists is `empty`",
      r34 == (False, "empty", "0 vevents / 0 future; catID 34 still listed on "
              "/iCalendar.aspx as 'Library Events'"), r34)
r99 = audit_verdict(sess, CP.format(99987))
check("an empty category the site no longer lists is `fail`",
      r99[1] == "fail" and "catID 99987 no longer listed" in r99[2], r99)
r23 = audit_verdict(sess, CP.format(23))
check("the category's name is read as the page writes it, entities decoded",
      r23[1] == "empty" and r23[2].endswith("as 'Sports & Recreation'"), r23)
check("the list is read ONCE for the origin, and robots.txt once",
      sess.asked.count(CITY + "/iCalendar.aspx") == 1 and sess.asked.count(CITY + "/robots.txt") == 1,
      sess.asked)
check("each feed is fetched once: an empty verdict is settled, not retried",
      all(sess.asked.count(CP.format(i)) == 1 for i in (34, 99987, 23)), sess.asked)
r14 = audit_verdict(sess, CP.format(14))
check("a CivicPlus feed WITH events keeps the old rule (2 past -> empty), no list consulted",
      r14[1] == "empty" and r14[2] == "2 vevents / 0 future", r14)

# robots.txt refuses the list: never fetched, and the feed stays `fail`.
cc._CIVICPLUS_LISTS.clear()
RO = "https://ro.example"
sess = Session({RO + "/robots.txt": (200, "User-agent: *\nDisallow: /iCalendar.aspx\n", "text/plain"),
                RO + "/iCalendar.aspx": (200, LISTING, "text/html"),
                RO + "/common/modules/iCalendar/iCalendar.aspx?catID=34&feed=calendar": (200, EMPTY)})
r = audit_verdict(sess, RO + "/common/modules/iCalendar/iCalendar.aspx?catID=34&feed=calendar")
check("a list robots.txt refuses is never requested, and the feed stays `fail`",
      r[1] == "fail" and "robots.txt refuses it" in r[2]
      and RO + "/iCalendar.aspx" not in sess.asked, (r, sess.asked))

# The list answers 503: `fail`, and no retry of the feed is triggered by it.
cc._CIVICPLUS_LISTS.clear()
BUSY = "https://busy.example"
BUSY_CP = BUSY + "/common/modules/iCalendar/iCalendar.aspx?catID=34&feed=calendar"
sess = Session({BUSY + "/robots.txt": ALLOW, BUSY + "/iCalendar.aspx": (503, "busy", "text/html"),
                BUSY_CP: (200, EMPTY)})
r = audit_verdict(sess, BUSY_CP)
check("a list that answers 503 leaves the feed `fail`, the feed asked once",
      r[1] == "fail" and "unreadable (answered 503)" in r[2] and sess.asked.count(BUSY_CP) == 1,
      (r, sess.asked))

# A platform the table does not know keeps its old verdict: an empty VCALENDAR
# is `fail`, with the old note, and nothing else is asked. (Until 2026-10-29
# this case used a ?ical=1 URL; that is The Events Calendar's export now, which
# has its own existence test below.)
cc._CIVICPLUS_LISTS.clear()
sess = Session({"https://club.example/calendar.ics": (200, EMPTY)})
r = audit_verdict(sess, "https://club.example/calendar.ics")
check("an empty VCALENDAR on a platform not in the table is `fail` with the old note, nothing else asked",
      r == (False, "fail", "0 vevents / 0 future") and sess.asked == ["https://club.example/calendar.ics"],
      (r, sess.asked))

# Discovery's half: an empty candidate is still never proposed.
cc._CIVICPLUS_LISTS.clear()
tmp = tempfile.mkdtemp(prefix="curate-audit-verify-")
keep = (cc.HERE, cc.LEDGER_FILE, cc._session)
cc.HERE, cc.LEDGER_FILE = tmp, os.path.join(tmp, "curation_ledger.json")
sess = Session({CITY + "/robots.txt": ALLOW, CITY + "/iCalendar.aspx": (200, LISTING, "text/html"),
                CP.format(34): (200, EMPTY)})
cc._session = lambda: sess
cand = os.path.join(tmp, "cand.json")
json.dump([{"type": "ics", "name": "City - Library Events", "url": CP.format(34),
            "category": "learning"}], open(cand, "w", encoding="utf-8"))
try:
    with redirect_stdout(io.StringIO()):
        cc.cmd_verify(cand)
    verified = json.load(open(os.path.join(tmp, "cand.verified.json"), encoding="utf-8"))
    led = cc._load_ledger()
finally:
    cc.HERE, cc.LEDGER_FILE, cc._session = keep
check("verify does not pass an empty category, listed or not", verified == [], verified)
check("...records it `empty`, which discovery skips like `fail`",
      lambda: led[cc._canon(CP.format(34))]["status"] == "empty"
      and cc._dead_recently(led, cc._canon(CP.format(34))))
shutil.rmtree(tmp, ignore_errors=True)
cc._CIVICPLUS_LISTS.clear()

# ============================================================================
# B3. verify_opendata reads dates the way mapsee_ingest_opendata does.
# ============================================================================
# Dated from the real clock, because the adapter's past-event drop reads it:
# the agreement check below would rot on a fixed date.
real = datetime.date.today()
ymd = lambda d: d.strftime("%Y%m%d")
human = lambda d, tail: f"{d.strftime('%B')} {d.day} {d.year} {tail}"
os.environ["MAPSEE_TODAY"] = ymd(real)
URL = "https://data.city.example/resource/n625-9k5x.json"
CAL = {"name": "City Events", "url": URL, "app_token": None, "category": "community",
       "limit": 1000, "type": "opendata",
       "map": {"title": "title", "start": "next_date_times", "venue": "address",
               "lat": "latitude", "lon": "longitude"}}


def rows_of(*starts, end=None):
    out = []
    for i, st in enumerate(starts):
        row = {"title": f"Event {i}", "next_date_times": st, "address": "1 Main St",
               "latitude": "51.04", "longitude": "-114.07"}
        if end:
            row["end_date"] = end
        out.append(row)
    return out


def verify_rows(rows, entry=CAL):
    sess = Session({URL: (200, json.dumps(rows), "application/json;charset=utf-8")})
    return cc.verify_opendata(sess, dict(entry))


def adapter_keeps(rows, entry=CAL):
    return [od.row_to_event(r, entry) is not None for r in rows]


past, soon, later = (real - datetime.timedelta(days=2), real + datetime.timedelta(days=12),
                     real + datetime.timedelta(days=26))
calgary = rows_of(human(past, "1 - 4 p.m."), human(soon, "10:45 a.m. - 1:15 p.m."),
                  human(later, "1:30 - 4:30 p.m."))
ok, note = verify_rows(calgary)
check("Calgary's free-text column ('October 27 2026 1 - 4 p.m.') verifies", ok, note)
check("...and the adapter keeps the same future rows the verifier counted",
      adapter_keeps(calgary) == [False, True, True], adapter_keeps(calgary))
pasts = rows_of(human(past, "1 - 4 p.m."), human(real - datetime.timedelta(days=9), "6 p.m."))
ok, note = verify_rows(pasts)
check("free text that parses but is all past: no future rows",
      not ok and note == f"no future rows (latest sampled {past.isoformat()})", note)
check("...and the adapter keeps none of them either", adapter_keeps(pasts) == [False, False])
junk = rows_of("October 27", "TBD", "Fall season")
ok, note = verify_rows(junk)
check("a value the adapter cannot date is still refused as not a date column",
      not ok and note == "'next_date_times' is not a date column (sample: 'October 27')", note)
iso = rows_of(soon.isoformat() + "T10:00:00.000")
check("an ISO timestamp column still verifies", verify_rows(iso)[0], verify_rows(iso))
running = rows_of(past.isoformat(), end=later.isoformat())
entry = dict(CAL, map=dict(CAL["map"], end="end_date"))
ok, note = verify_rows(running, entry)
check("an event that started before today and runs on is future, as the adapter keeps it",
      ok and adapter_keeps(running, entry) == [True], (note, adapter_keeps(running, entry)))
# AN ISO DATE WITH A CLOCK PYTHON CANNOT READ IS ACCEPTED BY BOTH, ON PURPOSE.
# _iso_parts("2026-11-27 7:00 PM") is ("2026-11-27 7:00 PM", None, "2026-11-27"):
# the date key is a date, the start is the raw value, and Postgres reads that
# value (PostgreSQL 16.15, 2026-10-29: 2026-11-27 19:00). Requiring a start
# Python parses would refuse a row the database keeps, so the verifier's
# comment now says this instead of the opposite (review of 2026-10-29).
clock_txt = f"{soon.isoformat()} 7:00 PM"
pm = rows_of(clock_txt)
ok, note = verify_rows(pm)
check("'YYYY-MM-DD 7:00 PM' verifies: its date key is a date",
      ok and od._iso_parts(clock_txt) == (clock_txt, None, soon.isoformat()), (note, od._iso_parts(clock_txt)))
check("...and the adapter keeps the row with the raw value as its start, for Postgres to read",
      lambda: adapter_keeps(pm) == [True] and od.row_to_event(pm[0], CAL).start_local == clock_txt)
check("...while a value with no date in its first ten characters is still refused",
      not verify_rows(rows_of("TBD"))[0] and not verify_rows(rows_of("Fall season"))[0])
os.environ["MAPSEE_TODAY"] = TODAY

# ============================================================================
# B1. The audit: a budget, the oldest check first, saves on the way, any way out.
# ============================================================================
class Clock:
    def __init__(self):
        self.t = 1_000_000.0
        self.lock = threading.Lock()

    def time(self):
        with self.lock:
            return self.t

    def advance(self, s):
        with self.lock:
            self.t += s


def tree(entries_a, entries_b, ledger):
    """A temp tree: a_sources.json (ics), b_sources.json (jsonld), a ledger."""
    tmp = tempfile.mkdtemp(prefix="curate-audit-")
    json.dump(entries_a, open(os.path.join(tmp, "a_sources.json"), "w"))
    json.dump({"sites": entries_b}, open(os.path.join(tmp, "b_sources.json"), "w"))
    json.dump(ledger, open(os.path.join(tmp, "curation_ledger.json"), "w"))
    return tmp


def row(day, status="ok", reason="seen"):
    return {"type": "ics", "name": "x", "status": status, "reason": reason, "checked": day}


A = [{"name": f"A{i}", "url": f"https://a{i}.example/cal.ics"} for i in (1, 2, 3)]
B = [{"name": f"B{i}", "listing": [f"https://b{i}.example/events/"]} for i in (1, 2)]
LEDGER = {cc._canon("https://a1.example/cal.ics"): row(20261018, "fail", "http 404 (old)"),
          cc._canon("https://a3.example/cal.ics"): row(20261011),
          cc._canon("https://b1.example/events/"): row(20261004)}
saved = (cc.HERE, cc.LEDGER_FILE, cc.CONFIG, dict(cc.VERIFIERS), cc._session, cc.time)


def restore():
    cc.HERE, cc.LEDGER_FILE, cc.CONFIG, verifiers, cc._session, cc.time = saved
    cc.VERIFIERS.clear()
    cc.VERIFIERS.update(verifiers)


def audit(tmp, calls, clock=None, per_probe=60.0, crash_at=None, peek=None, **kw):
    """The real cmd_audit over `tmp`, every probe a stub that records its name."""
    cc.HERE, cc.LEDGER_FILE = tmp, os.path.join(tmp, "curation_ledger.json")
    cc.CONFIG = {"ics": ("a_sources.json", "url"), "jsonld": ("b_sources.json", "listing"),
                 "ods": ("missing_sources.json", "url")}       # its file is absent

    def stub(s, e):
        calls.append(e["name"])
        if peek:
            peek(len(calls))
        if crash_at and len(calls) == crash_at:
            raise KeyboardInterrupt                        # what a cancelled step sends
        if clock:
            clock.advance(per_probe)
        return (False, "http 404") if e["name"] == "B2" else (True, "3 vevents / 2 future")

    cc.VERIFIERS["ics"] = cc.VERIFIERS["jsonld"] = stub
    cc._session = lambda: None
    if clock:
        cc.time = types.SimpleNamespace(time=clock.time, sleep=lambda s: None)
    out = io.StringIO()
    raised = None
    try:
        with redirect_stdout(out):
            cc.cmd_audit(**kw)
    except BaseException as exc:                                 # noqa: BLE001
        raised = exc
    finally:
        restore()
    led = json.load(open(os.path.join(tmp, "curation_ledger.json"), encoding="utf-8"))
    return out.getvalue(), led, raised


# --- the order, the budget, and what a run that stops does not touch ---------
tmp = tree(A, B, LEDGER)
calls, clock = [], Clock()
log, led, raised = audit(tmp, calls, clock=clock, max_minutes=2.5)
check("never-checked entries go first, in config order, then the oldest check",
      calls == ["A2", "B2", "B1"], calls)
check("the budget stops it: no probe is started after the deadline",
      len(calls) == 3 and raised is None, (calls, raised))
check("an entry the run did not reach keeps its old row, `fail` included",
      lambda: led[cc._canon("https://a1.example/cal.ics")] == LEDGER[cc._canon("https://a1.example/cal.ics")]
      and led[cc._canon("https://a3.example/cal.ics")]["checked"] == 20261011)
# The WHOLE row, status included: a run never marks a feed it did not probe.
# (The review of 2026-10-29 mutated unreached rows to `fail`, kept their date,
# and the check above let it through.)
check("...the WHOLE row: an unreached `ok` entry is still `ok`, reason and date unchanged",
      lambda: led[cc._canon("https://a3.example/cal.ics")] == LEDGER[cc._canon("https://a3.example/cal.ics")]
      and led[cc._canon("https://a3.example/cal.ics")]["status"] == "ok",
      led.get(cc._canon("https://a3.example/cal.ics")))
check("the entries it did reach carry this run's verdict and date",
      lambda: led[cc._canon("https://b2.example/events/")]["status"] == "fail"
      and led[cc._canon("https://b1.example/events/")]["checked"] == int(TODAY)
      and led[cc._canon("https://a2.example/cal.ics")]["status"] == "ok")
check("a run cut by the budget still prints the BROKEN summary the workflow reads",
      "\n1 entries are BROKEN" in log, log[-400:])
check("...and how far it got, of how many, and where the next run starts",
      "audit reached 3 of 5 configured entries; stopped at the 2.5-minute budget, "
      "and the next run starts with the 2 the ledger checked longest ago" in log, log[-400:])
check("a config file that is absent is reported and skipped, not the end of the audit",
      "::warning::audit: missing_sources.json not read" in log, log[:600])

# --- the next run resumes where this one stopped ------------------------------
calls = []
log, led, raised = audit(tmp, calls)
check("the next run starts with the entries the last one did not reach",
      calls[:2] == ["A3", "A1"] and len(calls) == 5, calls)
check("...and with no budget it reaches every entry",
      "audit reached 5 of 5 configured entries\n" in log + "\n" and "budget" not in log.split("audit reached")[-1],
      log[-300:])
shutil.rmtree(tmp, ignore_errors=True)

# --- saved on the way, not only at the end -----------------------------------
tmp = tree(A, B, LEDGER)
on_disk = {}


def peek(n):
    led_now = json.load(open(os.path.join(tmp, "curation_ledger.json"), encoding="utf-8"))
    on_disk[n] = sum(1 for r in led_now.values() if r.get("checked") == int(TODAY))


log, led, raised = audit(tmp, [], peek=peek, save_every=2)
check("the ledger on disk holds the first two probes before the third starts (save_every=2)",
      on_disk.get(3) == 2 and on_disk.get(5) == 4, on_disk)
shutil.rmtree(tmp, ignore_errors=True)

# --- whatever ends the run, what it probed is kept -----------------------------
tmp = tree(A, B, LEDGER)
calls = []
log, led, raised = audit(tmp, calls, crash_at=3)
check("an interrupt mid-run (a cancelled step) still saves every probe before it",
      isinstance(raised, KeyboardInterrupt)
      and sum(1 for r in led.values() if r.get("checked") == int(TODAY)) == 2
      and led[cc._canon("https://b1.example/events/")]["checked"] == 20261004, (raised, calls))
check("...and still prints both summary lines, saying it was interrupted",
      "\n1 entries are BROKEN" in log
      and "audit reached 2 of 5 configured entries; interrupted, and the next run starts with the 3" in log,
      log[-400:])
shutil.rmtree(tmp, ignore_errors=True)

# --- several at once, never two on one host -----------------------------------
HOSTS = ["one.example"] * 3 + ["two.example"] * 2 + ["three.example", "four.example"]
P = [{"name": f"P{i}", "url": f"https://{h}/cal{i}.ics"} for i, h in enumerate(HOSTS)]
tmp = tree(P, [], {})
lock = threading.Lock()
live, peak = {}, {"all": 0, "host": 0}
import time as _time


def busy_stub(s, e):
    host = e["url"].split("/")[2]
    with lock:
        live[host] = live.get(host, 0) + 1
        peak["host"] = max(peak["host"], live[host])
        peak["all"] = max(peak["all"], sum(live.values()))
    _time.sleep(0.05)
    with lock:
        live[host] -= 1
    return True, "3 vevents / 2 future"


cc.HERE, cc.LEDGER_FILE = tmp, os.path.join(tmp, "curation_ledger.json")
cc.CONFIG = {"ics": ("a_sources.json", "url"), "jsonld": ("b_sources.json", "listing")}
cc.VERIFIERS["ics"] = busy_stub
cc._session = lambda: None
out = io.StringIO()
try:
    with redirect_stdout(out):
        cc.cmd_audit(workers=3)
finally:
    restore()
led = json.load(open(os.path.join(tmp, "curation_ledger.json"), encoding="utf-8"))
check("three workers probe at once", peak["all"] >= 2, peak)
check("...and never two probes on one host at once", peak["host"] == 1, peak)
check("...and every entry is probed and recorded exactly once",
      len(led) == 7 and out.getvalue().count("[OK ]") == 7, out.getvalue()[-300:])
shutil.rmtree(tmp, ignore_errors=True)

# The budget holds with workers too: the two running finish, nothing new starts.
P4 = [{"name": f"Q{i}", "url": f"https://q{i}.example/cal.ics"} for i in range(4)]
tmp = tree(P4, [], {})
clock = Clock()
gate = threading.Barrier(2, timeout=5)
started = []


def gated(s, e):
    started.append(e["name"])
    clock.advance(60)                   # each probe's minute is spent before
    gate.wait()                         # either of the two may finish
    return True, "3 vevents / 2 future"


cc.HERE, cc.LEDGER_FILE = tmp, os.path.join(tmp, "curation_ledger.json")
cc.CONFIG = {"ics": ("a_sources.json", "url"), "jsonld": ("b_sources.json", "listing")}
cc.VERIFIERS["ics"] = gated
cc._session = lambda: None
cc.time = types.SimpleNamespace(time=clock.time, sleep=lambda s: None)
out = io.StringIO()
try:
    with redirect_stdout(out):
        cc.cmd_audit(workers=2, max_minutes=1.5)
except threading.BrokenBarrierError:
    pass
finally:
    restore()
check("with workers the budget starts nothing new, and records the probes it let finish",
      sorted(started) == ["Q0", "Q1"] and "audit reached 2 of 4 configured entries; stopped" in out.getvalue(),
      (started, out.getvalue()[-300:]))
shutil.rmtree(tmp, ignore_errors=True)

# --- the CLI passes the budget through -----------------------------------------
got = {}
keep_audit = cc.cmd_audit
cc.cmd_audit = lambda **kw: got.update(kw) or 0
try:
    cc.main(["catalog_curate.py", "audit", "--max-minutes", "200", "--workers", "4"])
finally:
    cc.cmd_audit = keep_audit
check("`audit --max-minutes 200 --workers 4` reaches cmd_audit",
      got == {"max_minutes": 200.0, "workers": 4}, got)

# --- the workflow's half: the budget fits, and what was probed is committed ----
wf = yaml.safe_load(open(os.path.join(ROOT, ".github", "workflows", "source-health.yml"),
                         encoding="utf-8"))
job = wf["jobs"]["audit"]
steps = job["steps"]
idx = {i for i, st in enumerate(steps) if "catalog_curate.py audit" in str(st.get("run", ""))}
probe_i = min(idx) if idx else -1
probe = steps[probe_i] if idx else {}
import re
mm = re.search(r"--max-minutes\s+(\d+)", str(probe.get("run", "")))
budget = int(mm.group(1)) if mm else None
step_cap = probe.get("timeout-minutes")
job_cap = job.get("timeout-minutes")
check("the probe step passes a budget", budget is not None, probe.get("run"))
check("...under its own step timeout, with room for the probes running at the deadline",
      lambda: step_cap - budget >= 10, (budget, step_cap))
check("...room for the whole grace the audit gives them, and five minutes to save and print",
      lambda: step_cap - budget >= cc.AUDIT_GRACE_S / 60 + 5, (budget, step_cap, cc.AUDIT_GRACE_S))
check("...and the step timeout under the job's, with room to commit",
      lambda: job_cap - step_cap >= 10, (step_cap, job_cap))
check("the snapshot is not taken inside the probe step",
      "$SNAPSHOT" not in str(probe.get("run", "")), probe.get("run"))
later = steps[probe_i + 1:] if idx else []
snap = [st for st in later if "$SNAPSHOT/curation_ledger.json" in str(st.get("run", ""))
        and "cp " in str(st.get("run", ""))]
check("a step after it takes the snapshot on always()",
      bool(snap) and "always()" in str(snap[0].get("if", "")), snap)
commit = [st for st in later if "catalog_curate.py reapply" in str(st.get("run", ""))]
check("the commit step runs on always() too",
      bool(commit) and "always()" in str(commit[0].get("if", "")), commit)
check("...and skips cleanly when there is no snapshot",
      bool(commit) and re.search(r'if \[ ! -f "\$SNAPSHOT/curation_ledger\.json" \]; then.*?exit 0',
                                 str(commit[0].get("run", "")), re.S) is not None)
check("...after the snapshot step", bool(snap and commit) and steps.index(snap[0]) < steps.index(commit[0]))

# ============================================================================
# C1. A calendar is what mapsee_ingest_ics reads, wherever in the body it is.
# ============================================================================
import copy
import pickle
import mapsee_gcal
import mapsee_ingest_ics as ics_adapter

FUT, PAST = "20261105T180000", "20250105T180000"


def vevent(i, start=FUT):
    return f"BEGIN:VEVENT\r\nUID:{i}\r\nDTSTART:{start}\r\nSUMMARY:Walk {i}\r\nEND:VEVENT\r\n"


# Forterra's shape (2026-10-29): WordPress prints Elementor CSS, then the
# export starts on the same line as </style>, 8,120 characters in.
CSS = '<style id="elementor-post-49753">' + ".e4e7b{color:#333}" * 460 + "/* End custom CSS */</style>"
FORTERRA = (CSS + "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//Forterra - ECPv6.18.2//NONSGML v1.0//EN\n"
            + "".join(vevent(i) for i in range(5)) + "END:VCALENDAR\n")
FT_URL = "https://forterra.example/events/?ical=1"
check("fixture: BEGIN:VCALENDAR past character 8,000 and on no line of its own",
      FORTERRA.find("BEGIN:VCALENDAR") > 8000
      and "BEGIN:VCALENDAR" not in [ln.strip() for ln in FORTERRA.splitlines()])
sess = Session({FT_URL: (200, FORTERRA, "text/calendar; charset=UTF-8")})
r = audit_verdict(sess, FT_URL)
check("a calendar after 8,120 characters of CSS is read, as production read Forterra's",
      r == (True, "ok", "5 vevents / 5 future") and sess.asked == [FT_URL], (r, sess.asked))
check("...and the verifier counts the VEVENTs the adapter's parser counts",
      len(ics_adapter.parse_ics(FORTERRA)) == 5)
BARE = "".join(vevent(i) for i in range(2))
sess = Session({"https://bare.example/cal.ics": (200, BARE)})
check("VEVENTs with no VCALENDAR wrapper are read, because the adapter reads them",
      audit_verdict(sess, "https://bare.example/cal.ics") == (True, "ok", "2 vevents / 2 future"))
sess = Session({"https://site.example/cal.ics": (200, "<html><body>Events</body></html>", "text/html")})
check("a page with neither is still not a calendar",
      audit_verdict(sess, "https://site.example/cal.ics") == (False, "fail", "http 200 / no VCALENDAR"))
TZ_ONLY = ("BEGIN:VCALENDAR\r\nBEGIN:VTIMEZONE\r\nTZID:Europe/Oslo\r\nBEGIN:DAYLIGHT\r\n"
           "DTSTART:20270328T020000\r\nEND:DAYLIGHT\r\nEND:VTIMEZONE\r\n" + vevent(1, PAST) + "END:VCALENDAR\r\n")
sess = Session({"https://tz.example/cal.ics": (200, TZ_ONLY)})
check("a VTIMEZONE's future DTSTART still counts for nothing",
      audit_verdict(sess, "https://tz.example/cal.ics") == (False, "empty", "1 vevents / 0 future"))
sess = Session({"https://parish.example/cal.ics": (200, BARE)})
check("webcal:// is asked as https://, as the adapter asks it",
      audit_verdict(sess, "webcal://parish.example/cal.ics")[0] is True
      and sess.asked == ["https://parish.example/cal.ics"], sess.asked)

# ============================================================================
# C2. A valid empty calendar, platform by platform, from what a missing one answers.
# ============================================================================
LIBCAL = "https://lib.libcal.com/ical_subscribe.php?cid=9035"
OA = "https://openagenda.com/agendas/7840068/events.v2.ics?relative%5b0%5d=current"
UNIBAS = "https://botgarten.unibas.ch/de/news-veranstaltungen/2514.ics"
MYCAL = "https://altar.example/?feed=my-calendar-ics"
VRTX = "https://www.nhm.uio.no/hva-skjer/botaniskhage/?vrtx=ical"
GCAL = "https://calendar.google.com/calendar/ical/club%40group.calendar.google.com/public/basic.ics"
EXAMPLES = {"CivicPlus": CP.format(34), "Google Calendar API": GCAL, "LibCal": LIBCAL,
            "OpenAgenda": OA, "unibas.ch": UNIBAS, "My Calendar": MYCAL, "Vortex": VRTX}
check("the table holds exactly the platforms measured, CivicPlus first",
      [rule[0] for rule in cc.EMPTY_CALENDAR_RULES] == list(EXAMPLES),
      [rule[0] for rule in cc.EMPTY_CALENDAR_RULES])
matrix = {(rule[0], name): bool(rule[1](url, True))
          for rule in cc.EMPTY_CALENDAR_RULES for name, url in EXAMPLES.items()}
check("each row recognises its own platform's URL and no other's",
      all(v == (a == b) for (a, b), v in matrix.items()),
      [k for k, v in matrix.items() if v != (k[0] == k[1])])
check("only CivicPlus decides by a second request; the others by what a missing calendar answers",
      [rule[0] for rule in cc.EMPTY_CALENDAR_RULES if callable(rule[3])] == ["CivicPlus"]
      and all(rule[3] == "empty" for rule in cc.EMPTY_CALENDAR_RULES[1:]))
check("a Google calendar counts only when it was read through the Calendar API",
      cc.empty_calendar_rule(GCAL, via_api=False) is None
      and cc.empty_calendar_rule(GCAL, via_api=True)[0] == "Google Calendar API")
check("a URL no row names has no rule, so it keeps the old `fail`",
      cc.empty_calendar_rule("https://club.example/calendar.ics") is None
      and cc.empty_calendar_rule("https://lib.libcal.com/calendar?cid=9035") is None)
for name in ("LibCal", "OpenAgenda", "unibas.ch", "My Calendar", "Vortex"):
    url = EXAMPLES[name]
    sess = Session({url: (200, EMPTY)})
    r = audit_verdict(sess, url)
    check(f"{name}: a valid empty calendar is `empty`, saying why, one request",
          r[:2] == (False, "empty") and f"a valid empty {name} calendar" in r[2]
          and "so this one exists" in r[2] and sess.asked == [url], (r, sess.asked))
    check(f"...{name}'s status is the Verdict's, not a wording _EMPTY_RX happens to match",
          cc._EMPTY_RX.search(r[2]) is None)
# The other half of the rule: what a MISSING calendar answers stays `fail`.
MISSING = {"LibCal": (200, "invalid calendar id", "text/html; charset=UTF-8"),
           "OpenAgenda": (404, "<!DOCTYPE html><html></html>", "text/html"),
           "unibas.ch": (200, "", "text/calendar; charset=utf-8"),
           "My Calendar": (404, "<error><code>wp_die</code></error>", "text/xml"),
           "Vortex": (404, "<!DOCTYPE html><html></html>", "text/html")}
for name, answer in MISSING.items():
    sess = Session({EXAMPLES[name]: answer})
    r = audit_verdict(sess, EXAMPLES[name])
    check(f"{name}: what a missing calendar answers (measured) is still `fail`",
          r[1] == "fail" and "no VCALENDAR" in r[2], r)

# Google through the REAL mapsee_gcal path: a 200 with no items is a calendar
# that exists; the API's 404 is GcalError, `fail`.
CID = mapsee_gcal.calendar_id(GCAL)
API = mapsee_gcal.api_url(CID)
had_key = os.environ.get(mapsee_gcal.KEY_ENV)
os.environ[mapsee_gcal.KEY_ENV] = "test-key"
try:
    sess = Session({API: (200, json.dumps({"summary": "Club", "timeZone": "Europe/Vienna", "items": []}),
                          "application/json")})
    r = audit_verdict(sess, GCAL)
    check("Google through the API: a 200 with no events is `empty`, the API's 200 being the proof",
          r[:2] == (False, "empty") and "Google Calendar API" in r[2] and sess.asked == [API], (r, sess.asked))
    err = {"error": {"code": 404, "message": "Not Found", "errors": [{"reason": "notFound"}]}}
    sess = Session({API: (404, json.dumps(err), "application/json")})
    r = audit_verdict(sess, GCAL)
    check("...and a calendar the API does not know (404) is `fail`",
          r[1] == "fail" and r[2].startswith("calendar api 404 notFound"), r)
finally:
    if had_key is None:
        os.environ.pop(mapsee_gcal.KEY_ENV, None)
    else:
        os.environ[mapsee_gcal.KEY_ENV] = had_key
sess = Session({GCAL: (200, EMPTY)})
check("...but an empty export read without the API proves nothing: `fail`, as before",
      audit_verdict(sess, GCAL) == (False, "fail", "0 vevents / 0 future"))

# ============================================================================
# C3. The Events Calendar between seasons: its REST route says it is alive.
# ============================================================================
TEC = "https://fest.example/events/?ical=1"
REST = cc.tribe_rest_url(TEC)
FEST_ROBOTS = "https://fest.example/robots.txt"
check("the existence test asks the route on the feed's origin, from today",
      REST == "https://fest.example/wp-json/tribe/events/v1/events?per_page=10&start_date=2026-10-29", REST)
ROBOTS_OK = (200, "User-agent: *\nDisallow: /wp-admin/\n", "text/plain")
NO_EVENTS = (200, json.dumps({"events": [], "total": 0, "total_pages": 0}), "application/json")
TWO_EVENTS = (200, json.dumps({"events": [{"id": 1, "start_date": "2026-11-02 18:00:00"},
                                          {"id": 2, "start_date": "2026-11-09 18:00:00"}],
                               "total": 2}), "application/json")


def tec(feed_answer, rest_answer=NO_EVENTS, robots=ROBOTS_OK, url=TEC):
    pages = {FEST_ROBOTS: robots, url: feed_answer}
    if rest_answer is not None:
        pages[REST] = rest_answer
    sess = Session(pages)
    return audit_verdict(sess, url), sess.asked


r, asked = tec((200, "", "text/html; charset=UTF-8"))
check("?ical=1 answering an empty 200, REST 200 with 0 upcoming: `empty`, and says so",
      r == (False, "empty", "http 200 / no VCALENDAR; The Events Calendar REST answers 200 with 0 upcoming")
      and asked == [TEC, FEST_ROBOTS, REST], (r, asked))
r, asked = tec((404, "", "text/html; charset=UTF-8"))
check("?ical=1 answering 404, REST 200 with 0 upcoming: `empty`",
      r == (False, "empty", "http 404 / no VCALENDAR; The Events Calendar REST answers 200 with 0 upcoming"), r)
r, asked = tec((200, "<!DOCTYPE html><html><body>Events</body></html>", "text/html"))
check("?ical=1 answering an HTML page, REST 200 with 0 upcoming: `empty`", r[1] == "empty", r)
r, asked = tec((200, EMPTY))
check("?ical=1 answering an empty VCALENDAR is decided by the REST route too",
      r == (False, "empty", "0 vevents / 0 future; The Events Calendar REST answers 200 with 0 upcoming"), r)
r, asked = tec((200, "", "text/html"), TWO_EVENTS)
check("REST lists upcoming events while ?ical=1 gives nothing: `fail`, naming the fix",
      r[1] == "fail" and "REST lists 2 upcoming: the tribe adapter would read it" in r[2], r)
r, asked = tec((200, "", "text/html"), (404, json.dumps({"code": "rest_no_route"}), "application/json"))
check("no REST route (no plugin): `fail`, as before", r[1] == "fail" and r[2].endswith("REST answers 404"), r)
r, asked = tec((200, "", "text/html"), (200, json.dumps({"code": "something else"}), "application/json"))
check("a 200 that is not The Events Calendar's JSON is not proof: `fail`",
      r[1] == "fail" and "not with The Events Calendar's JSON" in r[2], r)
r, asked = tec((200, "", "text/html"), robots=(200, "User-agent: *\nDisallow: /wp-json/\n", "text/plain"))
check("robots.txt refusing the route: never requested, `fail`",
      r[1] == "fail" and "REST not asked: robots.txt refuses it" in r[2] and REST not in asked, (r, asked))
r, asked = tec((200, "", "text/html"), robots=(200, "User-agent: *\nDisallow: /*start_date=\n", "text/plain"))
check("...asked about that exact URL, query string included",
      r[1] == "fail" and "Disallow: /*start_date=" in r[2] and REST not in asked, (r, asked))
r, asked = tec((503, "busy", "text/html"))
check("a 503 on ?ical=1 keeps its old note and its retries; the route is not asked",
      r == (False, "fail", "http 503 / no VCALENDAR") and asked.count(TEC) == 3 and REST not in asked,
      (r, asked))
r, asked = tec((200, FORTERRA, "text/calendar"))
check("a ?ical=1 export with events never asks the route", r[1] == "ok" and asked == [TEC], (r, asked))
sess = Session({"https://other.example/cal.ics": (404, "", "text/html")})
check("a 404 that is not a ?ical=1 export asks nothing more",
      audit_verdict(sess, "https://other.example/cal.ics") == (False, "fail", "http 404 / no VCALENDAR")
      and sess.asked == ["https://other.example/cal.ics"])
# A SCOPED EXPORT IS NOT THE SITE'S CALENDAR (review of 2026-10-29): the route
# answers for the whole site, or the network root of a multisite. The review's
# examples keep the old verdict and never ask the route.
SCOPED = ["https://www.bartramsgarden.org/?post_type=tribe_events&tribe_events_cat=volunteer&ical=1"
          "&eventDisplay=list",
          "https://www.casalcatalatolosa.cat/tag/concert/?ical=1",
          "https://www.lunel.com/category/toute-lactualite/evenement/?ical=1",
          "https://extension.wsu.edu/thurston/events/category/mg/?post_type=tribe_events&ical=1&eventDisplay=list"]
for url in SCOPED:
    root = cc.robots_txt.origin_of(url)
    sess = Session({root + "/robots.txt": ROBOTS_OK, url: (200, "", "text/html"),
                    cc.tribe_rest_url(url): TWO_EVENTS})
    r = audit_verdict(sess, url)
    check(f"a scoped export is not tested against the site's route: {url[8:52]}",
          r == (False, "fail", "http 200 / no VCALENDAR") and sess.asked == [url], (r, sess.asked))
for url in ("https://fest.example/events/?ical=1", "https://fest.example/events/list/?ical=1",
            "https://fest.example/?post_type=tribe_events&ical=1&eventDisplay=list",
            "https://fest.example/?ical=1"):
    check(f"...while the whole calendar at the origin still is: {url[8:]}", cc.tribe_feed_is_sitewide(url))
check("...and one event's own export is not the whole calendar",
      not cc.tribe_feed_is_sitewide("https://win-e-v.de/events/fahrradtour-11/?ical=1"))
check("...nor another post type's export",
      not cc.tribe_feed_is_sitewide("https://fest.example/?post_type=page&ical=1"))
slept = []
real_time = cc.time
cc.time = types.SimpleNamespace(time=real_time.time, sleep=slept.append)
try:
    r, asked = tec((200, "", "text/html"), robots=(200, "User-agent: *\nCrawl-delay: 5\n", "text/plain"))
finally:
    cc.time = real_time
check("outside the audit the route waits out the host's Crawl-delay first (verify)",
      r[1] == "empty" and slept == [5.0], (r, slept))

# ============================================================================
# C4. verify_tribe: the plugin answering an empty list is a quiet calendar.
# 2026-10-29: 36 configured sites kept `empty` by hand answered exactly that,
# and the 6 whose plugin was gone answered 404 (JSON rest_no_route or a page).
# ============================================================================
SITE = "https://quiet.example"
ROUTE = SITE + "/wp-json/tribe/events/v1/events"


def tribe_verdict(answer, entry=None):
    sess = Session({ROUTE: answer})
    ok, note = cc._verify_with_retry(cc.VERIFIERS["tribe"], sess, dict(entry or {}, type="tribe",
                                                                      base_url=SITE))
    return ok, cc._status_for(ok, note), str(note)


r = tribe_verdict(NO_EVENTS)
check("verify_tribe: 200 JSON with an empty `events` list is `empty`, and says why",
      r == (False, "empty", "0 events / 0 future; The Events Calendar REST answers 200 with 0 upcoming"), r)
r = tribe_verdict((404, json.dumps({"code": "rest_no_route", "message": "No route was found"}),
                   "application/json"))
check("verify_tribe: 404 rest_no_route (the plugin is gone) stays `fail`",
      r == (False, "fail", "http 404"), r)
r = tribe_verdict((404, "<html>The resource you are looking for has been removed</html>", "text/html"))
check("verify_tribe: a 404 page (the site left WordPress) stays `fail`", r == (False, "fail", "http 404"), r)
r = tribe_verdict((200, "<!DOCTYPE html><html></html>", "text/html"))
check("verify_tribe: 200 that is not JSON stays `fail`", r == (False, "fail", "not json"), r)
r = tribe_verdict((200, json.dumps({"code": "something else"}), "application/json"))
check("verify_tribe: 200 JSON without an `events` list is not the plugin: `fail`",
      r == (False, "fail", "0 events / 0 future"), r)
r = tribe_verdict(TWO_EVENTS)
check("verify_tribe: upcoming events are `ok`, as before",
      r == (True, "ok", "2 events, 2/2 on page 1 future"), r)
r = tribe_verdict((200, json.dumps({"events": [{"id": 1, "start_date": "2026-10-01 18:00:00"}], "total": 1}),
                   "application/json"))
check("verify_tribe: only past events in the window stays `empty` by its note, as before",
      r == (False, "empty", "1 events / 0 future"), r)

# ============================================================================
# D. Crawl-delay: between a host's requests, in the audit and in verify.
# ============================================================================
SLOW = "https://slow.example"
SLOW_CP = SLOW + "/common/modules/iCalendar/iCalendar.aspx?catID=34&feed=calendar"


def cp_pages(robots_body):
    return {SLOW + "/robots.txt": (200, robots_body, "text/plain"),
            SLOW + "/iCalendar.aspx": (200, LISTING.replace(CITY, SLOW), "text/html"),
            SLOW_CP: (200, EMPTY)}


cc._CIVICPLUS_LISTS.clear()
slept = []
cc.time = types.SimpleNamespace(time=real_time.time, sleep=slept.append)
try:
    sess = Session(cp_pages("User-agent: *\nCrawl-delay: 10\n"))
    r = audit_verdict(sess, SLOW_CP)
finally:
    cc.time = real_time
check("verify: the CivicPlus list read waits the host's Crawl-delay (10 s) after the feed",
      r[1] == "empty" and slept == [10.0]
      and sess.asked.index(SLOW + "/iCalendar.aspx") > sess.asked.index(SLOW_CP), (r, slept, sess.asked))
cc._CIVICPLUS_LISTS.clear()
slept = []
cc.time = types.SimpleNamespace(time=real_time.time, sleep=slept.append)
try:
    sess = Session(cp_pages("User-agent: *\nCrawl-delay: 60\n"))
    r = audit_verdict(sess, SLOW_CP)
finally:
    cc.time = real_time
check("a Crawl-delay over the 30 s cap is not waited out, and the list is not asked sooner",
      r[1] == "fail" and "Crawl-delay 60 s" in r[2] and slept == []
      and SLOW + "/iCalendar.aspx" not in sess.asked, (r, slept, sess.asked))
cc._CIVICPLUS_LISTS.clear()


class FakeTime:
    def __init__(self):
        self.t, self.slept = 5000.0, []

    def time(self):
        return self.t

    def sleep(self, s):
        self.slept.append(round(s, 3))
        self.t += s


ft = FakeTime()
cc.time = types.SimpleNamespace(time=ft.time, sleep=ft.sleep)
try:
    raw = Session({"https://h.example/robots.txt": (200, "User-agent: *\nCrawl-delay: 10\n", "text/plain"),
                   "https://h.example/a": (200, "x"), "https://h.example/b": (200, "x")})
    pace = cc._AuditPace({"h.example": 4.0})
    ps = cc._AuditSession(raw, pace)
    ps.get("https://h.example/a")
    first = (list(ft.slept), list(raw.asked))
    ft.t += 1.0
    ps.get("https://h.example/b")
    check("pace: a host's first request goes at once, robots.txt not yet read",
          first == ([], ["https://h.example/a"]), first)
    check("...its second waits out the larger delay (robots 10 s over the entry's 4 s), less time passed",
          ft.slept == [9.0] and raw.asked.count("https://h.example/robots.txt") == 1, (ft.slept, raw.asked))
    check("...and the scheduler reads when the host is next free from the same clock",
          pace.ready_at("h.example") == ft.t + 10.0 and pace.delay("h.example") == 10.0)
    ft.slept.clear()
    pace = cc._AuditPace({"h.example": 20.0})
    pace._asked["h.example"] = 10.0
    ps = cc._AuditSession(raw, pace)
    ps.get("https://h.example/a")
    ft.t += 1.0
    ps.get("https://h.example/b")
    check("...an entry's own crawl_delay wins when it is the larger", ft.slept == [19.0], ft.slept)
    ft.slept.clear()
    raw.asked.clear()
    pace = cc._AuditPace({"h.example": 45.0})
    pace._asked["h.example"] = 0.0
    ps = cc._AuditSession(raw, pace)
    ps.get("https://h.example/a")
    try:
        ps.get("https://h.example/b")
        refused = False
    except cc.AuditCrawlDelay:
        refused = True
    check("...a wait over the 30 s cap raises, and the request is not sent",
          refused and ft.slept == [] and raw.asked == ["https://h.example/a"], (refused, ft.slept, raw.asked))
    raw.asked.clear()
    pace = cc._AuditPace({"api.example": 2.0})
    ps = cc._AuditSession(raw, pace, exempt=True)
    ps.get("https://api.example/x")
    ps.get("https://api.example/y")
    check("...a documented API is never asked for robots.txt, but its entry's delay holds",
          "https://api.example/robots.txt" not in raw.asked and ft.slept == [2.0], (raw.asked, ft.slept))
finally:
    cc.time = real_time

# The scheduler: a host in its delay is skipped, not slept on. one.example's
# delay is its robots.txt's (read before its first probe, because it has two
# entries); four.example's is its entries' own `crawl_delay`.
QH = ["one.example", "one.example", "four.example", "four.example", "two.example", "three.example"]
QP = [{"name": f"R{i}", "url": f"https://{h}/cal{i}.ics", "crawl_delay": 0.4 if h == "four.example" else None}
      for i, h in enumerate(QH)]
tmp = tree(QP, [], {})
starts, reqs = {}, {}
lock = threading.Lock()


def paced_stub(s, e):
    with lock:
        starts[e["name"]] = _time.time()
    s.get(e["url"])
    with lock:
        reqs[e["name"]] = _time.time()
    return True, "1 vevents / 1 future"


shared = Session({"https://one.example/robots.txt": (200, "User-agent: *\nCrawl-delay: 0.4\n", "text/plain"),
                  "https://four.example/robots.txt": (200, "User-agent: *\n", "text/plain")})
cc.HERE, cc.LEDGER_FILE = tmp, os.path.join(tmp, "curation_ledger.json")
cc.CONFIG = {"ics": ("a_sources.json", "url"), "jsonld": ("b_sources.json", "listing")}
cc.VERIFIERS["ics"] = paced_stub
cc._session = lambda: shared
out = io.StringIO()
try:
    with redirect_stdout(out):
        cc.cmd_audit(workers=2)
finally:
    restore()
gaps = (starts.get("R1", 0) - reqs.get("R0", 0), starts.get("R3", 0) - reqs.get("R2", 0))
check("a host's next probe starts no sooner than its robots.txt Crawl-delay after its last request",
      gaps[0] >= 0.39, (gaps, starts, reqs))
check("...or its entries' own crawl_delay", gaps[1] >= 0.39, (gaps, starts, reqs))
check("...and the other hosts are probed meanwhile",
      max(starts.get("R4", 9e9), starts.get("R5", 9e9)) < min(starts.get("R1", 0), starts.get("R3", 0)), starts)
# Every host's robots.txt is read before its first request now (its Disallow
# decides whether to ask at all: section G), once per origin for the run.
check("...without a worker asleep on it, robots.txt read once per host, before its first request",
      "crawl-delay: 0 wait(s) inside probes, 0 s in all; robots.txt read for 4 origin(s)" in out.getvalue()
      and all(shared.asked.count(f"https://{h}/robots.txt") == 1 for h in set(QH))
      and all(shared.asked.index(f"https://{h}/robots.txt")
              < min(i for i, u in enumerate(shared.asked) if u.startswith(f"https://{h}/cal"))
              for h in set(QH)), (out.getvalue()[-400:], shared.asked))
shutil.rmtree(tmp, ignore_errors=True)

# Inside one probe, through the real verify_ics: the list read waits the
# host's robots.txt Crawl-delay after the feed, and robots.txt is read once.
cc._CIVICPLUS_LISTS.clear()
tmp = tree([{"name": "Slow City", "url": SLOW_CP}], [], {})
stamps = []


class StampSession(Session):
    def get(self, url, **kw):
        stamps.append((url, _time.time()))
        return super().get(url, **kw)


shared = StampSession(cp_pages("User-agent: *\nCrawl-delay: 0.3\n"))
cc.HERE, cc.LEDGER_FILE = tmp, os.path.join(tmp, "curation_ledger.json")
cc.CONFIG = {"ics": ("a_sources.json", "url"), "jsonld": ("b_sources.json", "listing")}
cc._session = lambda: shared
out = io.StringIO()
try:
    with redirect_stdout(out):
        cc.cmd_audit(workers=2)
finally:
    restore()
at = {u: t for u, t in stamps}
led = json.load(open(os.path.join(tmp, "curation_ledger.json"), encoding="utf-8"))
check("audit: the CivicPlus list read waits the robots.txt Crawl-delay after the feed",
      at.get(SLOW + "/iCalendar.aspx", 0) - at.get(SLOW_CP, 9e9) >= 0.29
      and led[cc._canon(SLOW_CP)]["status"] == "empty", (stamps, led))
check("...robots.txt read once for the origin, shared by the pacing and the list",
      shared.asked.count(SLOW + "/robots.txt") == 1, shared.asked)
shutil.rmtree(tmp, ignore_errors=True)
cc._CIVICPLUS_LISTS.clear()

# ============================================================================
# E. The deadline: save first, then a bounded grace, then the summary.
# ============================================================================
release = threading.Event()


def hang_or_quick(s, e):
    if e["name"] == "H":
        release.wait(10)
        return True, "late"
    return True, "1 vevents / 1 future"


keep_v, keep_s = cc.VERIFIERS["ics"], cc._session
cc.VERIFIERS["ics"] = hang_or_quick
cc._session = lambda: None
plan = [(0, i, "ics", "a_sources.json", "url", {"name": n, "url": f"https://{n.lower()}.example/c.ics"})
        for i, n in enumerate(("Q", "H", "N"))]
recorded, cuts = [], []
t0 = _time.time()
try:
    cut, abandoned = cc._audit_each(plan, _time.time() + 0.3, 1,
                                    lambda item, ok, note: recorded.append(item[5]["name"]),
                                    on_cut=lambda running: cuts.append((running, list(recorded))),
                                    grace=0.4)
finally:
    cc.VERIFIERS["ics"], cc._session = keep_v, keep_s
took = _time.time() - t0
check("at the deadline on_cut runs while the straggler is still running, after what came back",
      cuts == [(1, ["Q"])], cuts)
check("...the straggler is abandoned after the grace, unrecorded, and nothing new starts",
      cut is True and [i[5]["name"] for i in abandoned] == ["H"] and recorded == ["Q"], (cut, abandoned, recorded))
check("...within the grace, not when the straggler chooses", 0.6 <= took < 2.5, took)

tmp = tree([{"name": "Q", "url": "https://q.example/c.ics"}, {"name": "H", "url": "https://h.example/c.ics"}],
           [], {cc._canon("https://h.example/c.ics"): row(20261011, "ok", "seen")})
inside, saves = {"each": False}, []
real_each, real_save = cc._audit_each, cc._save_ledger


def each_wrapped(*a, **kw):
    inside["each"] = True
    try:
        return real_each(*a, **kw)
    finally:
        inside["each"] = False


def save_wrapped(led_now):
    saves.append((inside["each"], cc._canon("https://q.example/c.ics") in led_now))
    return real_save(led_now)


keep_grace = cc.AUDIT_GRACE_S
cc.HERE, cc.LEDGER_FILE = tmp, os.path.join(tmp, "curation_ledger.json")
cc.CONFIG = {"ics": ("a_sources.json", "url"), "jsonld": ("b_sources.json", "listing")}
cc.VERIFIERS["ics"] = hang_or_quick
cc._session = lambda: None
cc._audit_each, cc._save_ledger, cc.AUDIT_GRACE_S = each_wrapped, save_wrapped, 0.5
release.clear()
out = io.StringIO()
t0 = _time.time()
try:
    with redirect_stdout(out):
        cc.cmd_audit(workers=2, max_minutes=0.3 / 60)
finally:
    cc._audit_each, cc._save_ledger, cc.AUDIT_GRACE_S = real_each, real_save, keep_grace
    restore()
took = _time.time() - t0
log = out.getvalue()
led = json.load(open(os.path.join(tmp, "curation_ledger.json"), encoding="utf-8"))
check("...and the abandoned worker is a daemon thread, so it cannot hold python open",
      lambda: [t.daemon for t in threading.enumerate() if t.name.startswith("audit-")]
      and all(t.daemon for t in threading.enumerate() if t.name.startswith("audit-")))
check("the ledger is saved when the deadline cuts, while a probe is still running",
      (True, True) in saves, saves)
check("...the run returns within the grace instead of waiting on the straggler", took < 3.0, took)
check("...the straggler's entry keeps its whole old row",
      led[cc._canon("https://h.example/c.ics")] == row(20261011, "ok", "seen"), led)
check("...and both summary lines print, with what was abandoned",
      "\n0 entries are BROKEN" in log and "audit reached 1 of 2 configured entries; stopped at the" in log
      and "1 probe(s) still running 0.5 s after the budget were abandoned unrecorded: h.example/c.ics" in log,
      log[-600:])
release.set()
shutil.rmtree(tmp, ignore_errors=True)

# ============================================================================
# F. The ledger on disk, and a Verdict outside the thread it was made on.
# ============================================================================
tmp = tempfile.mkdtemp(prefix="curate-audit-dump-")
keep_lf = cc.LEDGER_FILE
cc.LEDGER_FILE = os.path.join(tmp, "curation_ledger.json")
real_dump = cc.json.dump


def dump_then_die(obj, fh, **kw):
    fh.write('{"a.example/cal.ics": {"status": "o')
    raise OSError("No space left on device")


try:
    cc._save_ledger({"a.example/cal.ics": row(20261018)})
    before = open(cc.LEDGER_FILE, encoding="utf-8").read()
    cc.json.dump = dump_then_die
    try:
        cc._save_ledger({"b.example/cal.ics": row(20261029)})
        died = False
    except OSError:
        died = True
    finally:
        cc.json.dump = real_dump
    after = open(cc.LEDGER_FILE, encoding="utf-8").read()
finally:
    cc.LEDGER_FILE = keep_lf
check("a dump that dies mid-write leaves the previous ledger whole and parseable",
      died and after == before and json.loads(after) == {"a.example/cal.ics": row(20261018)}, (died, after[:80]))
shutil.rmtree(tmp, ignore_errors=True)

v = cc.Verdict("0 vevents / 0 future; catID 34 still listed on /iCalendar.aspx as 'Library Events'", "empty")
for how, fn in (("copy.copy", copy.copy), ("copy.deepcopy", copy.deepcopy),
                ("pickle", lambda x: pickle.loads(pickle.dumps(x)))):
    check(f"a Verdict survives {how} with its status",
          lambda: isinstance(fn(v), cc.Verdict) and fn(v) == v and fn(v).status == "empty"
          and cc._status_for(False, fn(v)) == "empty")

# ============================================================================
# G. The final review of 2026-10-29: what the audit must not write.
# ============================================================================
# --- a type with no verifier is never probed and never written ----------------
# mylisting is in CONFIG and in no VERIFIERS entry: planned anyway, its probe
# raised KeyError and was recorded `fail` "KeyError: 'mylisting'", which
# FEED_DOWN then reported daily (production kept 556 of 592 from it, 10-28).
gap = sorted(set(cc.CONFIG) - set(cc.VERIFIERS))
check(f"CONFIG types the audit cannot verify, so it skips them: {gap}", gap == ["mylisting"], gap)
NV = [{"name": "Listed", "explore_url": "https://isle.example/events/?type=event"},
      {"name": "Unrowed", "explore_url": "https://isle2.example/events/"}]
NV_ROW = {"type": "mylisting", "name": "Listed", "status": "ok", "reason": "seen", "checked": 20261011}
tmp = tree([{"name": "A1", "url": "https://a1.example/cal.ics"}], [],
           {cc._canon(NV[0]["explore_url"]): dict(NV_ROW)})
json.dump({"sites": NV}, open(os.path.join(tmp, "c_sources.json"), "w"))
calls = []
cc.HERE, cc.LEDGER_FILE = tmp, os.path.join(tmp, "curation_ledger.json")
cc.CONFIG = {"ics": ("a_sources.json", "url"), "nover": ("c_sources.json", "explore_url")}
cc.VERIFIERS["ics"] = lambda s, e: (calls.append(e["name"]), (True, "1 vevents / 1 future"))[1]
cc._session = lambda: None
out = io.StringIO()
try:
    with redirect_stdout(out):
        cc.cmd_audit()
finally:
    restore()
led = json.load(open(os.path.join(tmp, "curation_ledger.json"), encoding="utf-8"))
log = out.getvalue()
check("a type with no verifier is not probed: its row is the whole old row, and no row is added",
      led.get(cc._canon(NV[0]["explore_url"])) == NV_ROW and cc._canon(NV[1]["explore_url"]) not in led
      and calls == ["A1"], (led, calls))
check("...said once, by type, and the entries it can verify are audited as before",
      log.count("no verifier for `nover` (c_sources.json)") == 1
      and "audit reached 1 of 1 configured entries" in log and "KeyError" not in log, log[-500:])
shutil.rmtree(tmp, ignore_errors=True)

# --- robots.txt is obeyed before an entry's first request ----------------------
RB = [{"name": "Deny", "url": "https://deny.example/cal0.ics"},
      {"name": "Down", "url": "https://down.example/cal1.ics"},
      {"name": "Open", "url": "https://open.example/cal2.ics"},
      {"name": "GCal", "url": GCAL}]
DOC = [{"name": "Doc", "base_url": "https://doc.example"}]
GROW = cc._canon(GCAL)
tmp = tree(RB, DOC, {cc._canon(RB[0]["url"]): row(20261011, "ok", "seen"),
                     GROW: row(20261011, "ok", "12 vevents / 12 future")})
calls = []


def fetching_stub(s, e):
    calls.append(e["name"])
    s.get(e.get("url") or e["base_url"] + "/api")
    return True, "1 vevents / 1 future"


import requests as _rq
shared = Session({"https://deny.example/robots.txt": (200, "User-agent: *\nDisallow: /cal\n", "text/plain"),
                  "https://down.example/robots.txt": _rq.ConnectionError("reset"),
                  "https://open.example/robots.txt": (200, "User-agent: *\nAllow: /\n", "text/plain"),
                  "https://doc.example/robots.txt": (200, "User-agent: *\nDisallow: /\n", "text/plain")})
for u in (RB[0]["url"], RB[1]["url"], RB[2]["url"], "https://doc.example/api"):
    shared.pages[u] = (200, "x")
cc.HERE, cc.LEDGER_FILE = tmp, os.path.join(tmp, "curation_ledger.json")
cc.CONFIG = {"ics": ("a_sources.json", "url"), "mobilizon": ("b_sources.json", "base_url")}
cc.VERIFIERS["ics"] = cc.VERIFIERS["mobilizon"] = fetching_stub
cc._session = lambda: shared
had_key = os.environ.pop(mapsee_gcal.KEY_ENV, None)
out = io.StringIO()
try:
    with redirect_stdout(out):
        cc.cmd_audit(workers=2)
finally:
    restore()
    if had_key is not None:
        os.environ[mapsee_gcal.KEY_ENV] = had_key
led = json.load(open(os.path.join(tmp, "curation_ledger.json"), encoding="utf-8"))
log = out.getvalue()
check("an explicit Disallow on the feed is recorded `refused` with the rule, the feed never fetched",
      led[cc._canon(RB[0]["url"])]["status"] == "refused"
      and led[cc._canon(RB[0]["url"])]["reason"] == "robots.txt: Disallow: /cal (GET /cal0.ics)"
      and RB[0]["url"] not in shared.asked and "Deny" not in calls, (led.get(cc._canon(RB[0]["url"])), calls))
check("...and said in the summary, apart from BROKEN",
      "1 entries robots.txt disallows: recorded `refused`, nothing it disallows fetched." in log
      and "\n0 entries are BROKEN" in log, log[-600:])
check("an unreachable robots.txt is not a verdict: the feed is probed as before",
      "Down" in calls and led[cc._canon(RB[1]["url"])]["status"] == "ok", (calls, led.get(cc._canon(RB[1]["url"]))))
check("an allowed feed is probed, robots.txt asked first",
      "Open" in calls and shared.asked.index("https://open.example/robots.txt") < shared.asked.index(RB[2]["url"]))
check("a documented API is never asked for robots.txt, and its Disallow: / does not refuse it",
      "https://doc.example/robots.txt" not in shared.asked and "Doc" in calls
      and led[cc._canon("https://doc.example")]["status"] == "ok", (shared.asked, calls))
check("a Google calendar without the Calendar API key is not probed and keeps its whole row",
      "GCal" not in calls and led[GROW] == row(20261011, "ok", "12 vevents / 12 future")
      and not any("google" in u for u in shared.asked), (calls, led.get(GROW)))
check("...counted as not decided, and still reached",
      "1 probe(s) not decided" in log and "audit reached 5 of 5 configured entries" in log, log[-600:])
shutil.rmtree(tmp, ignore_errors=True)

# Every request the adapter starts with is asked, not the first alone: a jsonld
# entry's SECOND listing disallowed refuses the entry (review of 2026-10-29).
ML = [{"name": "Two listings", "listing": ["https://open.example/agenda/", "https://deny.example/agenda/"]}]
tmp = tree([], ML, {cc._canon(ML[0]["listing"][0]): dict(row(20261011), type="jsonld")})
calls = []
shared = Session({"https://open.example/robots.txt": (200, "User-agent: *\nAllow: /\n", "text/plain"),
                  "https://deny.example/robots.txt": (200, "User-agent: *\nDisallow: /agenda\n", "text/plain")})
cc.HERE, cc.LEDGER_FILE = tmp, os.path.join(tmp, "curation_ledger.json")
cc.CONFIG = {"jsonld": ("b_sources.json", "listing")}
cc.VERIFIERS["jsonld"] = lambda s, e: (calls.append(e["name"]), (True, "1/1 sampled events future"))[1]
cc._session = lambda: shared
out = io.StringIO()
try:
    with redirect_stdout(out):
        cc.cmd_audit()
finally:
    restore()
led = json.load(open(os.path.join(tmp, "curation_ledger.json"), encoding="utf-8"))
got = led.get(cc._canon(ML[0]["listing"][0])) or {}
check("audit: a jsonld entry whose SECOND listing robots.txt disallows is `refused`, the rule named, never probed",
      got.get("status") == "refused" and got.get("reason") == "robots.txt: Disallow: /agenda (GET /agenda/)"
      and calls == [] and "https://open.example/agenda/" not in shared.asked, (got, calls, shared.asked))
shutil.rmtree(tmp, ignore_errors=True)

# --- a follow-up page robots.txt disallows is not fetched, and is counted -------
JL = "https://venue.example/agenda/"
FUT_BLOCK = ('<script type="application/ld+json">{"@type": "Event", "name": "Gig", '
             '"startDate": "2026-11-20T20:00"}</script>')
JL_PAGE = '<a href="/e/1">1</a><a href="/e/2">2</a><a href="/e/3">3</a>'
J_ROBOTS = (200, "User-agent: *\nDisallow: /e/2\n", "text/plain")
sess = Session({"https://venue.example/robots.txt": J_ROBOTS, JL: (200, JL_PAGE, "text/html"),
                "https://venue.example/e/1": (200, "<p>no block</p>", "text/html"),
                "https://venue.example/e/2": (200, FUT_BLOCK, "text/html"),
                "https://venue.example/e/3": (404, "gone", "text/html")})
ok, note = cc.verify_jsonld(sess, {"listing": [JL], "link_pattern": r"/e/\d"})
check("verify_jsonld: a link page robots.txt disallows is not fetched",
      "https://venue.example/e/2" not in sess.asked and sess.asked.count("https://venue.example/robots.txt") == 1,
      sess.asked)
check("...and the note says what was followed, what answered what, and what robots.txt refused",
      not ok and note == ("no schema.org Event blocks found; 3 link page(s) of 3: 2 fetched (1 answered "
                          "404), 1 disallowed by robots.txt"), note)
check("...without that wording triggering a retry or reading as `empty`",
      cc._status_for(False, note) == "fail" and not cc.re.search(r"http (?:403|429|5\d\d)", note))
sess = Session({"https://venue.example/robots.txt": J_ROBOTS, JL: (200, JL_PAGE, "text/html"),
                "https://venue.example/e/1": (200, FUT_BLOCK, "text/html")})
ok, note = cc.verify_jsonld(sess, {"listing": [JL], "link_pattern": r"/e/\d"})
check("...and a refused page alone does not fail an entry whose other pages have events",
      ok and note == "1/1 sampled events future", note)
# Every link page disallowed: robots.txt's answer, not a dead feed.
sess = Session({"https://venue.example/robots.txt": (200, "User-agent: *\nDisallow: /e/\n", "text/plain"),
                JL: (200, JL_PAGE, "text/html")})
ok, note = cc.verify_jsonld(sess, {"listing": [JL], "link_pattern": r"/e/\d"})
check("verify_jsonld: when robots.txt disallows EVERY link page, the entry is `refused` with the rule, not `fail`",
      cc._status_for(ok, note) == "refused"
      and note == ("robots.txt disallows every link page sampled (3 of 3): Disallow: /e/; 3 link page(s) "
                   "of 3: 0 fetched, 3 disallowed by robots.txt")
      and not any("/e/" in u for u in sess.asked), (note, sess.asked))
sess = Session({"https://venue.example/robots.txt": (200, "User-agent: *\nDisallow: /e/\n", "text/plain"),
                JL: (200, JL_PAGE.replace('href="/e/3"', 'href="https://tix.example/e/3"'), "text/html"),
                "https://tix.example/robots.txt": (200, "User-agent: *\nAllow: /\n", "text/plain"),
                "https://tix.example/e/3": (404, "gone", "text/html")})
ok, note = cc.verify_jsonld(sess, {"listing": [JL], "link_pattern": r"(?:https://tix\.example)?/e/\d"})
check("...but one page it could fetch makes it the feed's answer again (`fail`)",
      cc._status_for(ok, note) == "fail" and note.endswith("1 fetched (1 answered 404), 2 disallowed by robots.txt"),
      note)
# An unreachable robots.txt is not a Disallow here, as in the audit's gate: the
# page is fetched as before (the listing on that origin already was).
sess = Session({"https://venue.example/robots.txt": _rq.ConnectionError("reset"),
                JL: (200, JL_PAGE, "text/html"),
                "https://venue.example/e/1": (200, FUT_BLOCK, "text/html")})
ok, note = cc.verify_jsonld(sess, {"listing": [JL], "link_pattern": r"/e/\d"})
check("verify_jsonld: a link page whose robots.txt cannot be read is still fetched, not counted refused",
      ok and note == "1/1 sampled events future" and "https://venue.example/e/1" in sess.asked, (note, sess.asked))

# --- quiet answers that are not dead: a Mobilizon instance, an MEC agenda --------
class GqlSession(Session):
    """POSTs answered by the GraphQL `b` (beginsOn) variable; records each one."""

    def __init__(self, by_begins, code=200, ctype="application/json"):
        super().__init__({})
        self.by_begins, self.code, self.ctype, self.posts = by_begins, code, ctype, []

    def post(self, url, json=None, **kw):
        begins = (json or {}).get("variables", {}).get("b")
        self.posts.append((url, begins))
        got = self.by_begins.get(begins, self.by_begins.get("*"))
        body = got if isinstance(got, str) else __import__("json").dumps(got)
        return Resp(self.code, body, url, self.ctype)


def gql(total, titles=()):
    return {"data": {"searchEvents": {"total": total, "elements": [
        {"uuid": str(i), "title": t, "beginsOn": "2026-11-05T18:00:00Z"} for i, t in enumerate(titles)]}}}


MOB = {"type": "mobilizon", "base_url": "https://quiet.example"}
TODAY_B, YEAR_B = "2026-10-29T00:00:00Z", "2025-10-29T00:00:00Z"
sess = GqlSession({TODAY_B: gql(0), YEAR_B: gql(41, ["Vrijdenkers café"])})
r = cc._verify_with_retry(cc.VERIFIERS["mobilizon"], sess, MOB)
check("mobilizon: an instance answering 0 upcoming but 41 in the past year is `empty` (Bosvreugd, 10-29)",
      cc._status_for(*r) == "empty" and str(r[1]) == "0 future events; the instance answers, with 41 events "
      "since 2025-10-29" and sess.posts == [("https://quiet.example/api", TODAY_B),
                                            ("https://quiet.example/api", YEAR_B)], (r, sess.posts))
sess = GqlSession({TODAY_B: gql(0), YEAR_B: gql(0)})
r = cc._verify_with_retry(cc.VERIFIERS["mobilizon"], sess, MOB)
check("mobilizon: nothing upcoming and nothing in a year is still `fail`",
      cc._status_for(*r) == "fail" and r[1] == "0 future events; none since 2025-10-29 either", r)


class LookBackSession(GqlSession):
    """Today's search answers 0 upcoming; the look back answers `back`."""

    def __init__(self, back_code, back_body):
        super().__init__({TODAY_B: gql(0)})
        self.back_code, self.back_body = back_code, back_body

    def post(self, url, json=None, **kw):
        if (json or {}).get("variables", {}).get("b") == YEAR_B:
            self.posts.append((url, YEAR_B))
            body = self.back_body if isinstance(self.back_body, str) else __import__("json").dumps(self.back_body)
            return Resp(self.back_code, body, url, "application/json" if self.back_code == 200 else "text/html")
        return super().post(url, json=json, **kw)


# A look back that FAILS is no evidence the instance answers: it stays `fail`,
# or a broken instance would read quiet (review of 2026-10-29).
sess = LookBackSession(500, "<h1>Internal Server Error</h1>")
r = cc._verify_with_retry(cc.VERIFIERS["mobilizon"], sess, MOB)
check("mobilizon: 0 upcoming and a look back answering 500 is `fail`, never `empty`",
      cc._status_for(*r) == "fail" and r[1] == "0 future events; the look back answered http 500"
      and (sess.posts.count(("https://quiet.example/api", YEAR_B)) >= 1), (r, sess.posts))
sess = LookBackSession(200, {"errors": [{"message": "unknown field"}]})
r = cc._verify_with_retry(cc.VERIFIERS["mobilizon"], sess, MOB)
check("...and so is a look back answering a GraphQL error",
      cc._status_for(*r) == "fail" and r[1].startswith("0 future events; the look back answered graphql:"),
      (r, sess.posts))
sess = GqlSession({"*": "<!DOCTYPE html><title>404</title>"}, code=404, ctype="text/html")
r = cc._verify_with_retry(cc.VERIFIERS["mobilizon"], sess, MOB)
check("mobilizon: a former instance (ibb.info's /api: a 404 page) is `fail`, asked once",
      r == (False, "http 404") and len(sess.posts) == 1, (r, sess.posts))
sess = GqlSession({"*": {"errors": [{"message": "unknown field"}]}})
r = cc._verify_with_retry(cc.VERIFIERS["mobilizon"], sess, MOB)
check("mobilizon: a GraphQL error is `fail` and asks nothing more",
      cc._status_for(*r) == "fail" and r[1].startswith("graphql:") and len(sess.posts) == 1, (r, sess.posts))
sess = GqlSession({TODAY_B: gql(3, ["Linux Café"])})
r = cc._verify_with_retry(cc.VERIFIERS["mobilizon"], sess, MOB)
check("mobilizon: upcoming events are `ok`, one request, as before",
      r == (True, "3 upcoming, sample: Linux Café") and len(sess.posts) == 1, (r, sess.posts))

# Modern Events Calendar's empty list, in the two shapes measured on 10-29.
NEWROCK = ('<div class="mec-wrap mec-skin-list-container  " id="mec_skin_6270">\n'
           '<div class="mec-skin-list-events-container" id="mec_skin_events_6270">\n'
           '        Aucun événement trouvé !    </div>\n    \n</div>')
MEERGROEN = ('<div class="mec-wrap mec-skin-list-container  " id="mec_skin_mec1">\n\n    \n'
             '        <div class="mec-skin-list-events-container" id="mec_skin_events_mec1">\n'
             '        Geen evenementen gevonden!    </div>\n</div>\n'
             '<script data-mec-view-beacon="1" data-view="list" data-skin="list" data-count="0">'
             '(function(){})();</script>')
FULL = ('<div class="mec-skin-list-events-container" id="mec_skin_events_9">'
        '<div class="mec-wrapper"><article class="mec-event-article mec-clear">'
        '<h4 class="mec-event-title"><a href="/events/a/">A</a></h4></article></div></div>')
for name, page, text in (("New Rock", NEWROCK, "Aucun événement trouvé !"),
                         ("MEERGroen", MEERGROEN, "Geen evenementen gevonden!")):
    sess = Session({"https://mec.example/agenda/": (200, page, "text/html")})
    ok, note = cc.verify_jsonld(sess, {"listing": ["https://mec.example/agenda/"],
                                       "link_pattern": r"https://mec\.example/events/[a-z0-9-]+/"})
    check(f"jsonld: Modern Events Calendar's no-events list ({name}) is `empty`, quoting it",
          cc._status_for(ok, note) == "empty" and note == "no schema.org Event blocks found; Modern Events "
          f"Calendar prints its no-events text ('{text}'); no link matches link_pattern", note)
sess = Session({"https://mec.example/agenda/": (200, FULL, "text/html")})
ok, note = cc.verify_jsonld(sess, {"listing": ["https://mec.example/agenda/"]})
check("jsonld: an MEC list WITH events but no Event block is still `fail`",
      cc._status_for(ok, note) == "fail" and note == "no schema.org Event blocks found", note)
check("...and a page with no MEC container at all is not quiet",
      cc.mec_no_events("<p>Aucun événement trouvé !</p>") is None
      and cc.mec_no_events(NEWROCK + FULL) is None, cc.mec_no_events(NEWROCK + FULL))
check("...nor one whose MEC events sit outside an empty-looking container",
      cc.mec_no_events(NEWROCK + '<article class="mec-event-article">A</article>') is None)
check("...nor a container holding only whitespace, which a script may fill (review of 2026-10-29)",
      cc.mec_no_events('<div class="mec-skin-list-events-container" id="mec_skin_events_1">\n   \n  </div>')
      is None
      and cc.mec_no_events(NEWROCK + '<div class="mec-skin-grid-events-container" id="e2">  </div>') is None)
sess = Session({"https://mec.example/agenda/": (200, '<div class="mec-skin-list-events-container" id="x">'
                                                     '\n \n</div>', "text/html")})
ok, note = cc.verify_jsonld(sess, {"listing": ["https://mec.example/agenda/"]})
check("...so verify_jsonld files that page `fail`, not `empty`",
      cc._status_for(ok, note) == "fail" and note == "no schema.org Event blocks found", note)
check("...nor a page where a second view's container holds markup, whatever its classes",
      cc.mec_no_events(NEWROCK + '<div class="mec-skin-grid-events-container" id="mec_skin_events_7">'
                       '<div class="mec-event-grid-minimal"><a href="/events/b/">B</a></div></div>') is None)

# --- a Crawl-delay over the cap inside a probe decides nothing ---------------------
cc._CIVICPLUS_LISTS.clear()
CD_ROW = row(20261011, "ok", "4 vevents / 4 future")
tmp = tree([{"name": "Slow City", "url": SLOW_CP}], [], {cc._canon(SLOW_CP): dict(CD_ROW)})
shared = Session(cp_pages("User-agent: *\nCrawl-delay: 60\n"))
cc.HERE, cc.LEDGER_FILE = tmp, os.path.join(tmp, "curation_ledger.json")
cc.CONFIG = {"ics": ("a_sources.json", "url"), "jsonld": ("b_sources.json", "listing")}
cc._session = lambda: shared
out = io.StringIO()
try:
    with redirect_stdout(out):
        cc.cmd_audit()
finally:
    restore()
led = json.load(open(os.path.join(tmp, "curation_ledger.json"), encoding="utf-8"))
check("audit: a CivicPlus list read the 60 s Crawl-delay stops is not decided: the whole old row stays",
      led[cc._canon(SLOW_CP)] == CD_ROW and SLOW + "/iCalendar.aspx" not in shared.asked,
      (led.get(cc._canon(SLOW_CP)), shared.asked))
check("...the refusal is not kept as the origin's list, and the log says not decided",
      SLOW not in cc._CIVICPLUS_LISTS and "not decided: slow.example asks Crawl-delay 60 s" in out.getvalue(),
      (cc._CIVICPLUS_LISTS, out.getvalue()[-500:]))
shutil.rmtree(tmp, ignore_errors=True)
cc._CIVICPLUS_LISTS.clear()

# A retry after a 503 that the pacing will not send: not "AuditCrawlDelay" in
# place of the 503, but no verdict at all.
R503 = "https://busy503.example/cal.ics"
tmp = tree([{"name": "Busy", "url": R503, "crawl_delay": 45}], [], {cc._canon(R503): dict(CD_ROW)})
shared = Session({"https://busy503.example/robots.txt": (200, "User-agent: *\n", "text/plain"),
                  R503: (503, "busy", "text/html")})
cc.HERE, cc.LEDGER_FILE = tmp, os.path.join(tmp, "curation_ledger.json")
cc.CONFIG = {"ics": ("a_sources.json", "url"), "jsonld": ("b_sources.json", "listing")}
cc._session = lambda: shared
out = io.StringIO()
try:
    with redirect_stdout(out):
        cc.cmd_audit()
finally:
    restore()
led = json.load(open(os.path.join(tmp, "curation_ledger.json"), encoding="utf-8"))
check("audit: a retry the entry's 45 s crawl_delay will not send leaves the old row, not a `fail`",
      led[cc._canon(R503)] == CD_ROW and shared.asked.count(R503) == 1
      and "1 probe(s) not decided" in out.getvalue(), (led.get(cc._canon(R503)), shared.asked))
shutil.rmtree(tmp, ignore_errors=True)

# --- the three mutations the review's 36 left alive ------------------------------
# M07: an empty VCALENDAR after a long preamble is a calendar wherever it starts.
sess = Session({LIBCAL: (200, CSS + EMPTY, "text/calendar")})
r = audit_verdict(sess, LIBCAL)
check("an empty VCALENDAR after 8,000+ characters of CSS, on LibCal, is `empty`, not 'no VCALENDAR'",
      r[:2] == (False, "empty") and "a valid empty LibCal calendar" in r[2] and len(CSS) > 8000, r)
# M31: a challenge page in place of the CivicPlus list is not a list.
cc._CIVICPLUS_LISTS.clear()
CH = "https://walled.example"
CH_CP = CH + "/common/modules/iCalendar/iCalendar.aspx?catID=34&feed=calendar"
CH_PAGE = "<html><title>Just a moment...</title>" + LISTING.replace(CITY, CH) + "</html>"
sess = Session({CH + "/robots.txt": ALLOW, CH + "/iCalendar.aspx": (200, CH_PAGE, "text/html"),
                CH_CP: (200, EMPTY)})
r = audit_verdict(sess, CH_CP)
check("a bot challenge in place of the CivicPlus list is unreadable (`fail`), even if it carries links",
      r[1] == "fail" and "unreadable (a bot challenge)" in r[2], r)
cc._CIVICPLUS_LISTS.clear()
# M28 (`exempt = False` in the probe): the documented-API case above, through
# the real _audit_each, asserts robots.txt is never requested for doc.example.

print()
print(f"{'FAILURES: ' + ', '.join(fails) if fails else 'all checks passed'}")
sys.exit(1 if fails else 0)
