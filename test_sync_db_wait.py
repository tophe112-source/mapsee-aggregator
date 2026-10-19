#!/usr/bin/env python3
"""The sync waits out a database that is not answering, within a budget.

On 2026-10-19 four syncs gave up after three reads (about two minutes) during a
two-hour outage, the last of them 36 minutes before the instance came back, and
the day's rows were lost. wait_for_database() probes the cheapest read the
service key can make until it answers or MAPSEE_DB_WAIT_SECONDS is spent. The
cases below are about WHEN it waits: a 5xx or no answer, yes; a 4xx, no (that is
an answer, and ours to fix); and never past the budget.

    python test_sync_db_wait.py
"""
import io
import os
import sys
from contextlib import redirect_stdout

import mapsee_supabase_sync as m

FAILS = 0


def check(label, cond, detail=""):
    global FAILS
    print(f"{'ok  ' if cond else 'FAIL'}  {label}" + ("" if cond else f"   {detail!r}"))
    if not cond:
        FAILS += 1


class Resp:
    def __init__(self, status):
        self.status_code = status


class Session:
    """Answers from a script: an int is a status, an exception class is raised.
    The last entry repeats."""
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def get(self, url, headers=None, timeout=None):
        self.calls.append((url, headers, timeout))
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(item, type) and issubclass(item, BaseException):
            raise item("read timeout=15")
        return Resp(item)


class Clock:
    """Records the sleeps asked for: the budget is measured by them, not by a clock."""
    def __init__(self):
        self.slept = []

    def sleep(self, s):
        self.slept.append(s)


def run(script, budget):
    sess, clk = Session(script), Clock()
    out = io.StringIO()
    with redirect_stdout(out):
        ok = m.wait_for_database(sess, "https://x.supabase.co/", "k", budget_s=budget, sleep=clk.sleep)
    return ok, sess, clk, out.getvalue()


Timeout = m._TransportError

ok, sess, clk, out = run([200], 900)
check("an answering database costs one probe and no sleep", ok and len(sess.calls) == 1 and clk.slept == [], (ok, sess.calls, clk.slept))
check("the probe is one row of events with the service key and a deadline",
      sess.calls[0][0].endswith("/rest/v1/events?select=id&limit=1") and sess.calls[0][1]["apikey"] == "k"
      and sess.calls[0][2] == m.DB_WAIT_PROBE_TIMEOUT, sess.calls[0])
check("...and says nothing when nothing was waited for", out == "", out)

ok, sess, clk, out = run([Timeout, 503, 200], 900)
check("no answer, then a 5xx, then an answer: three probes, two sleeps that widen",
      ok and len(sess.calls) == 3 and clk.slept == [15, 30], (ok, len(sess.calls), clk.slept))
check("each failed probe is a workflow warning that names the cause by class, not by message (it can carry the URL)",
      out.count("::warning::") == 2 and "ReadTimeout" in out or "_TransportError" in out or "RequestException" in out, out)
check("...and 'read timeout=15' from the exception never reaches the log", "read timeout=15" not in out, out)
check("the recovery is reported with how long it took", "Database answered after 45s and 2 failed probe(s)" in out, out)

ok, sess, clk, out = run([401], 900)
check("a 4xx is an answer (the database is up; the problem is ours): no wait", ok and clk.slept == [], (ok, clk.slept))

ok, sess, clk, out = run([503], 100)
check("a database that never answers: the wait stops INSIDE the budget and returns False",
      ok is False and sum(clk.slept) <= 100, (ok, clk.slept))
check("...having used most of it (15+30 slept, the 60 s step would overrun)", clk.slept == [15, 30], clk.slept)
check("...and the last line says the sync proceeds and fails closed", "proceeding - the sync fails closed" in out, out)

ok, sess, clk, out = run([503], 10000)
check("the steps cap at the last one and repeat (the budget, not the table, ends the wait)",
      ok is False and clk.slept[:8] == [15, 30, 60, 120, 180, 240, 300, 300] and sum(clk.slept) <= 10000,
      clk.slept[:9])

ok, sess, clk, out = run([503], 0)
check("a budget of 0 means do not wait: no probe at all", ok is True and sess.calls == [], (ok, sess.calls))

old = os.environ.get("MAPSEE_DB_WAIT_SECONDS")
try:
    os.environ["MAPSEE_DB_WAIT_SECONDS"] = "0"
    sess, clk = Session([503]), Clock()
    with redirect_stdout(io.StringIO()):
        ok = m.wait_for_database(sess, "https://x.supabase.co", "k", sleep=clk.sleep)
    check("MAPSEE_DB_WAIT_SECONDS=0 switches the wait off", ok is True and sess.calls == [], sess.calls)
    os.environ["MAPSEE_DB_WAIT_SECONDS"] = "not a number"
    sess, clk = Session([503, 200]), Clock()
    with redirect_stdout(io.StringIO()):
        ok = m.wait_for_database(sess, "https://x.supabase.co", "k", sleep=clk.sleep)
    check("an unreadable MAPSEE_DB_WAIT_SECONDS falls back to the default, not to no wait",
          ok is True and clk.slept == [15], clk.slept)
    check("the default budget is 15 minutes", m.DB_WAIT_SECONDS_DEFAULT == 900, m.DB_WAIT_SECONDS_DEFAULT)
finally:
    if old is None:
        os.environ.pop("MAPSEE_DB_WAIT_SECONDS", None)
    else:
        os.environ["MAPSEE_DB_WAIT_SECONDS"] = old

src = open("mapsee_supabase_sync.py", encoding="utf-8").read()
main_src = src[src.index("def main() -> None:"):]
check("the sleeps go through _patch_sleep by default, which the main()-driving tests neutralise",
      "sleep = sleep or _patch_sleep" in src)
check("main() waits before its first read (after the session is made, before rekey_legacy)",
      0 < main_src.find("wait_for_database(geo, url, key)") < main_src.find("rekey_legacy(geo, url, key"),
      (main_src.find("wait_for_database(geo, url, key)"), main_src.find("rekey_legacy(geo, url, key")))

print()
if FAILS:
    print(f"{FAILS} FAILED")
    sys.exit(1)
print("all sync db-wait checks passed")
