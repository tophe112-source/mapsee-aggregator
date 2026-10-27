#!/usr/bin/env python3
"""
test_sync_unreadable_time.py — one row whose time Postgres cannot read must cost
that row, not its batch.

THE FAILURE THIS PREVENTS. A starts_at or ends_at that timestamptz refuses fails
the whole 50-row INSERT it rides in, and upsert() then re-sends all 50 one by
one to find it: 51 statements, 49 of them one-row transactions, to skip one
row the 400 was always going to skip. The cached logs 10-19..10-26
hold 54 of them, every one in the daily feeds sync, every one a JSON-LD
calendar printing its template's garbage ("2026-10-6T23-23-50-00",
"2026-12-06T00+01:00", "29-10-2026T23:59:59").

WHICH WAY IT MAY FAIL. Naming a stamp Postgres would have read drops a real
event, silently and every day. Leaving a refusable one to the database costs
what it cost before. So the checks below are mostly about what must NOT be
named, against verdicts recorded on a real Postgres (fixtures/pg_timestamptz.json),
because Python's own parser disagrees with it in both directions.

Run: python test_sync_unreadable_time.py
"""
import contextlib
import io
import json
import sys
import types
from datetime import datetime

import mapsee_supabase_sync as S

FAILS = 0
HOST = "00000000-0000-0000-0000-000000000001"


def check(label, cond, detail=""):
    global FAILS
    print(f"{'ok  ' if cond else 'FAIL'}  {label}" + ("" if cond else f"   {detail!r}"))
    if not cond:
        FAILS += 1


# Every literal Postgres refused in the cached logs 10-19..10-26, one per
# logged row (54), exactly as the 400 quoted it.
LOGGED = (["2026-10-22T17-17-30-00", "2026-10-29T17-17-30-00", "2026-9-10T17-17-30-00",
           "2026-10-6T23-23-50-00", "2026-10-22T23-23-50-00", "2026-10-25T12-12-15-00",
           "2026-2-8T12-12-15-00", "2026-10-26T20-20-00-00", "2026-6-22T20-20-00-00",
           "2026-10-27T19-19-00-00", "2026-6-23T19-19-00-00", "2026-10-28T13-13-00-00",
           "2026-9-30T13-13-00-00", "2026-9-10T20-20-00-00", "2026-8-23T19-19-45-00",
           "2026-10-25T19-19-00-00", "2026-10-30T19-19-00-00", "2026-11-25T19-19-00-00",
           "2026-11-30T19-19-00-00", "2026-12-30T19-19-00-00", "2026-9-25T19-19-00-00",
           "2026-10-29T00-00-00-00", "2026-11-5T22-22-00-00", "2026-10-5T16-16-00-00",
           "2026-10-6T16-16-00-00", "2026-10-7T16-16-00-00", "2026-10-7T16-16-00-00",
           "2026-10-9T21-21-00-00", "2026-10-28T19-19-50-00", "2026-10-28T19-19-50-00",
           "2026-10-7T18-18-30-00", "2026-10-7T19-19-50-00", "2026-10-30T17-17-00-00"]
          + ["2026-10-24T00+02:00", "2026-10-31T00+01:00", "2026-11-01T00+01:00",
             "2026-11-07T00+01:00", "2026-11-09T00+01:00", "2026-11-12T00+01:00",
             "2026-11-13T00+01:00", "2026-11-22T00+01:00", "2026-11-24T00+01:00",
             "2026-11-26T00+01:00", "2026-11-27T00+01:00", "2026-11-28T00+01:00",
             "2026-12-01T00+01:00", "2026-12-03T00+01:00", "2026-12-06T00+01:00"]
          + ["2026-12-12T15:0017:00:00", "2026-12-31T1600:00", "2026-23-10 21:00",
             "2026-23-10 21:00", "29-10-2026T23:59:59", "29-10-2026T23:59:59"])

# --- 1. what the logs refused -------------------------------------------------
check("the 54 logged refusals are all here", len(LOGGED) == 54, len(LOGGED))
named = [s for s in LOGGED if S.pg_refused_stamp(s)]
check("52 of the 54 are named before upsert", len(named) == 52, len(named))
check("...the two left are the DD-MM-YYYY pair, which only DateStyle refuses",
      [s for s in LOGGED if not S.pg_refused_stamp(s)] == ["29-10-2026T23:59:59"] * 2)
# A single-digit offset is not a refusal at all: Postgres reads "+1:00".
check("a single-digit offset is left alone (Postgres reads it)",
      S.pg_refused_stamp("2026-10-27T10:00:00+1:00") is None)

# --- 2. NOTHING POSTGRES READS IS NAMED ---------------------------------------
fx = json.load(open("fixtures/pg_timestamptz.json", encoding="utf-8"))
wrong = [s for s in fx["accepted"] if S.pg_refused_stamp(s)]
check(f"none of the {len(fx['accepted'])} stamps Postgres 16 accepted is named", not wrong, wrong[:5])
missed = [s for s in fx["refused_named"] if not S.pg_refused_stamp(s)]
check(f"all {len(fx['refused_named'])} recorded refusals of a named shape are named", not missed, missed[:5])
check("...and the refusals left to the database are left to it",
      not [s for s in fx["refused_left"] if S.pg_refused_stamp(s)])
check("the near-misses are in the fixture: mm:ss.f past 24, a 3-digit hour of 0, a 1-digit month",
      {"2026-10-27T25:0024.1", "2026-10-27T000:00", "2026-9-30T13:00:00"} <= set(fx["accepted"]))


# Python is not the oracle, in either direction, which is why the fixture exists.
def py_reads(s):
    try:
        datetime.fromisoformat(s)
        return True
    except ValueError:
        return False


check("Python reads a bare hour Postgres refuses, and refuses a 1-digit month Postgres reads",
      py_reads("2026-12-06T00+01:00") and not py_reads("2026-9-30T13:00:00")
      and S.pg_refused_stamp("2026-12-06T00+01:00") and not S.pg_refused_stamp("2026-9-30T13:00:00"))
check("not a string, None or empty is never named",
      not any(S.pg_refused_stamp(v) for v in (None, "", 20261027, "Sat, 25 Oct 2026 19:00:00 GMT")))

# --- 3. build_rows drops it, counts it, and keeps the rest --------------------
SEATTLE = (47.6062, -122.3321)


def rec(fp, start, end=None):
    r = {"fingerprint": fp, "name": f"Event {fp}", "category": "music", "source": "jsonld",
         "latitude": SEATTLE[0], "longitude": SEATTLE[1], "start_local": start}
    if end:
        r["end_local"] = end
    return r


store = {"events": [rec("good", "2026-11-01T19:00:00+01:00"),
                    rec("bad-start", "2026-10-6T23-23-50-00"),
                    rec("bad-end", "2026-11-01T19:00:00+01:00", "2026-11-01T21-21-00-00"),
                    rec("theatre", "2026-12-06T00+01:00"),
                    rec("dmy", "2026-11-01T19:00:00+01:00", "29-10-2026T23:59:59")]}
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rows = S.build_rows("unused.json", HOST, store_data=store)
out = buf.getvalue()
ids = [r["external_id"] for r in rows]
check("a row with a refused start, or a refused end, is dropped before upsert",
      ids == ["good", "dmy"], ids)
check("...and counted, one line for all of them", "Dropped 3 row(s) whose start or end Postgres "
      "cannot read" in out, out)
check("...with the publisher's value and the title, as the 400 printed them",
      "unreadable [ends_at '2026-11-01T21-21-00-00': an hour with no minutes] 'Event bad-end'" in out, out)


# --- 4. what upsert does with a refusal build_rows left to the database ---------
class Resp:
    def __init__(self, code, body=None):
        self.status_code = code
        self._body = body or {}
        self.text = json.dumps(self._body)

    def json(self):
        return self._body


# Postgres's own words for the stamps below, from PG 16.
REFUSAL = {"29-10-2026T23:59:59": 'date/time field value out of range: "29-10-2026T23:59:59"',
           "31-10-2026T20:00:00": 'date/time field value out of range: "31-10-2026T20:00:00"'}


def fake_requests(trigger_literal=None):
    """A PostgREST that refuses a batch on its FIRST row with a refused stamp,
    quoting it, as the 22008 does. trigger_literal: a 400 quoting a literal that
    no row carries (a trigger's own cast)."""
    calls = {"posts": 0, "sizes": []}

    def post(url, headers=None, data=None, timeout=None):
        batch = json.loads(data)
        calls["posts"] += 1
        if calls["posts"] > 500:                    # a re-queue that never shrinks
            raise RuntimeError("upsert is looping")
        calls["sizes"].append(len(batch))
        if trigger_literal and any(r.get("title") == "poison" for r in batch):
            return Resp(400, {"code": "22007", "message":
                              f'invalid input syntax for type timestamp with time zone: "{trigger_literal}"'})
        for r in batch:
            for c in ("starts_at", "ends_at"):
                if r.get(c) in REFUSAL:
                    return Resp(400, {"code": "22008", "message": REFUSAL[r[c]]})
        return Resp(201)

    mod = types.ModuleType("requests")
    mod.post = post
    return mod, calls


def batch(n, bad_at=(), literal="29-10-2026T23:59:59", poison_at=()):
    return [{"title": "poison" if i in poison_at else f"row {i}", "external_id": f"r{i}",
             "starts_at": "2026-10-29T18:00:00Z",
             "ends_at": literal if i in bad_at else "2026-10-29T21:00:00Z"} for i in range(n)]


real_requests = sys.modules.get("requests")
try:
    fake, calls = fake_requests()
    sys.modules["requests"] = fake
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        sent, skipped, lost = S.upsert(batch(50, bad_at={17}), "https://x.test", "k")
    check("one refused stamp in a 50-row batch costs 2 statements, not 51",
          calls["posts"] == 2 and calls["sizes"] == [50, 49], calls)
    check("...the 49 land and the one is skipped", (sent, skipped, lost) == (49, 1, 0), (sent, skipped, lost))
    check("...and its line names the database's reason and the row",
          "skipped [400 date/time field value out of range: \"29-10-2026T23:59:59\"] 'row 17'" in buf.getvalue(),
          buf.getvalue())

    fake, calls = fake_requests()
    sys.modules["requests"] = fake
    rows = batch(50, bad_at={3, 40})
    rows[40]["ends_at"] = "31-10-2026T20:00:00"
    with contextlib.redirect_stdout(io.StringIO()):
        got = S.upsert(rows, "https://x.test", "k")
    check("two different refused stamps: one statement each, then the rest once",
          calls["posts"] == 3 and got == (48, 2, 0), (calls, got))

    fake, calls = fake_requests()
    sys.modules["requests"] = fake
    with contextlib.redirect_stdout(io.StringIO()):
        got = S.upsert(batch(50, bad_at={1, 2, 3}), "https://x.test", "k")
    check("rows sharing one refused stamp go together", calls["posts"] == 2 and got == (47, 3, 0),
          (calls, got))

    fake, calls = fake_requests()
    sys.modules["requests"] = fake
    with contextlib.redirect_stdout(io.StringIO()):
        got = S.upsert(batch(3, bad_at={0, 1, 2}), "https://x.test", "k")
    check("a batch of nothing but that stamp is skipped whole, with nothing re-sent",
          calls["posts"] == 1 and got == (0, 3, 0), (calls, got))

    # A quoted literal no row carries is a trigger's cast, not a row's stamp: the
    # rows are still the suspect, so row by row, exactly as before.
    fake, calls = fake_requests(trigger_literal="never-sent")
    sys.modules["requests"] = fake
    with contextlib.redirect_stdout(io.StringIO()):
        got = S.upsert(batch(10, poison_at={4}), "https://x.test", "k")
    check("a refusal quoting a literal no row carries isolates row by row, as before",
          calls["posts"] == 11 and got == (9, 1, 0), (calls, got))
finally:
    if real_requests is not None:
        sys.modules["requests"] = real_requests
    else:
        sys.modules.pop("requests", None)

print(f"\n{'all ok' if not FAILS else f'{FAILS} FAILED'}")
sys.exit(1 if FAILS else 0)
