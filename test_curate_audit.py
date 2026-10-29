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
        self.headers = {"content-type": ctype}

    def json(self):
        return json.loads(self.text)


class Session:
    """Bodies by URL; records every URL asked for. A body may be a callable."""

    def __init__(self, pages):
        self.pages = pages
        self.headers = {"User-Agent": cc.UA}
        self.asked = []

    def get(self, url, timeout=None, allow_redirects=True, params=None):
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

# Every other platform's verdict is untouched: an empty non-CivicPlus VCALENDAR.
cc._CIVICPLUS_LISTS.clear()
sess = Session({"https://club.example/?ical=1": (200, EMPTY)})
r = audit_verdict(sess, "https://club.example/?ical=1")
check("an empty VCALENDAR that is not CivicPlus is `fail` with the old note, nothing else asked",
      r == (False, "fail", "0 vevents / 0 future") and sess.asked == ["https://club.example/?ical=1"],
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

print()
print(f"{'FAILURES: ' + ', '.join(fails) if fails else 'all checks passed'}")
sys.exit(1 if fails else 0)
