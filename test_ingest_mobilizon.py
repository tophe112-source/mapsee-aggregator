#!/usr/bin/env python3
"""
test_ingest_mobilizon.py - what a Mobilizon answer may put in the store.

searchEvents(beginsOn: now) answers more than the upcoming: the 2026-10-26
civic store held 240 Mobilizon rows that began more than a day before the run
with no endsOn (median 796 days old, the oldest 6,835), which the sync dates
into the past, the janitor deletes and the next --only-new sync inserts again,
every day; and 522 rows starting more than 365 days out, 441 of them one weekly
meeting to 2038. Neither kind can be shown. Both are counted and not kept; an
event still running, one that began within the day, a cancellation and a
site's own within_days are not touched.

No network: the GraphQL answer is a literal. Dated from today, because the
rules are.

Run: python test_ingest_mobilizon.py
"""
import io
import json
import sys
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone

import mapsee_ingest_mobilizon as M

fails = []


def check(label, cond, detail=""):
    print(f"{'ok  ' if cond else 'FAIL'}  {label}{'' if cond else '   ' + str(detail)}")
    if not cond:
        fails.append(label)


NOW = datetime.now(timezone.utc)


def at(**delta):
    return (NOW + timedelta(**delta)).strftime("%Y-%m-%dT%H:%M:%SZ")


def ev(uuid, begins, ends=None, status="CONFIRMED"):
    return {"uuid": uuid, "title": f"Event {uuid}", "beginsOn": begins, "endsOn": ends, "status": status,
            "physicalAddress": {"description": "Maison de quartier", "locality": "Lausanne",
                                "geom": "6.63;46.52"}}


ANSWER = [
    ev("near", at(days=9)),
    ev("edge", at(days=364)),
    ev("far", at(days=400)),
    ev("far-2038", at(days=4000), at(days=4000, hours=2)),
    ev("old-no-end", at(days=-796)),
    ev("old-ended", at(days=-3), at(days=-2)),
    ev("running", at(days=-30), at(days=5)),          # an exhibition with its end ahead
    ev("this-morning", at(hours=-2)),                  # began within the day, no end
    ev("far-cancelled", at(days=500), status="CANCELLED"),
]


class Resp:
    status_code = 200

    def __init__(self, body):
        self.body = body

    def json(self):
        return self.body


class Session:
    def __init__(self, elements):
        self.body = {"data": {"searchEvents": {"total": len(elements), "elements": elements}}}
        self.posts = []

    def post(self, url, data=None, timeout=None):
        self.posts.append(json.loads(data))
        return Resp(self.body)


class Store:
    def __init__(self):
        self.rows, self.cancelled = [], []

    def upsert(self, nev):
        self.rows.append(nev)
        return "new"

    def cancel(self, nev, reason):
        self.cancelled.append(nev)
        return "untrusted"


def run(site_extra=None):
    site = {"name": "m", "base_url": "https://m.test", "crawl_delay": 0, **(site_extra or {})}
    store, sess = Store(), Session(ANSWER)
    with redirect_stdout(io.StringIO()) as out:
        kept = M.ingest_site(store, sess, site)
    return kept, store, sess, out.getvalue()


kept, store, sess, log = run()
ids = sorted(r.source_id for r in store.rows)
check("kept: the upcoming within a year, one still running, one begun this morning",
      ids == ["edge", "near", "running", "this-morning"], ids)
check("ingest_site's count is the rows it kept", kept == 4, kept)
check("the query still asks from now (the filter is ours, not the server's)",
      sess.posts and sess.posts[0]["variables"]["b"][:10] == NOW.strftime("%Y-%m-%d"), sess.posts[:1])
check("a cancellation is still handed to store.cancel, however far",
      [r.source_id for r in store.cancelled] == ["far-cancelled"], [r.source_id for r in store.cancelled])
check("the log counts what was over and what was too far",
      "; 2 already over (begun, no end ahead)" in log and "; 2 beyond the 365-day horizon" in log, log)

kept, store, _, log = run({"within_days": 5000})
check("a site's within_days moves its horizon",
      sorted(r.source_id for r in store.rows) == ["edge", "far", "far-2038", "near", "running", "this-morning"]
      and "beyond the" not in log, ([r.source_id for r in store.rows], log))

check("the default horizon is a year", M.HORIZON_DAYS == 365, M.HORIZON_DAYS)
check("an event with an end that has not come is never 'over'",
      M.stale_or_far(ev("x", at(days=-4000), at(minutes=1)), NOW, NOW + timedelta(days=365)) is None)
check("an unreadable beginsOn is left for to_event to refuse",
      M.stale_or_far(ev("x", "soon"), NOW, NOW + timedelta(days=365)) is None)

print()
print(f"{'FAILURES: ' + ', '.join(fails) if fails else 'all checks passed'}")
sys.exit(1 if fails else 0)
