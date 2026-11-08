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
site's own within_days are not touched. "No end" is no end the store keeps:
the server sends those rows with an endsOn ahead more than MAX_SPAN_DAYS
after the start, which EventStore.upsert drops, so a rule that read the raw
endsOn skipped 0 rows on 2026-11-08 while 269 such rows churned.

The query asks for START_TIME_ASC: without it searchEvents answers latest
start first, and on every instance bigger than limit x max_pages the adapter
read 4-24 months out and never the coming weeks (2026-11-07: ticketzon.it's
five pages spanned 2027-05-07..2028-11-30; 2026-11-08: 0 of the rows kept on
the seven biggest instances fell within 90 days). An older Mobilizon that does
not know the argument (HTU Events, 1 of 66) is asked again once without it and
read that way for the rest of the site. A page that held nothing but rows
already over is counted and does not spend max_pages, up to OVER_PAGES_MAX.

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
YEAR = NOW + timedelta(days=365)
check("an event with an end that has not come is not 'over' (a span the store keeps)",
      M.stale_or_far(ev("x", at(days=-300), at(minutes=1)), NOW, YEAR) is None)
# The end EventStore.upsert drops (mapsee_spam.implausible_end, over MAX_SPAN_DAYS
# after the start) is no end: the row reaches the sync endless, the 10-27 churn.
# 269 such rows in the 2026-11-08 full run's store, while the rule skipped 0.
check("an end ahead that the store drops is no end: begun 500 days ago, 'ending' in 100, is 'over'",
      M.stale_or_far(ev("x", at(days=-500), at(days=100)), NOW, YEAR) == "over")
check("...and so is one begun two days ago that 'ends' 450 days later",
      M.stale_or_far(ev("x", at(days=-2), at(days=448)), NOW, YEAR) == "over")
check("a span of exactly MAX_SPAN_DAYS is kept (implausible_end wants more), one day more is not",
      M.MAX_SPAN_DAYS == 400
      and M.stale_or_far(ev("x", at(days=-399), at(days=1)), NOW, YEAR) is None
      and M.stale_or_far(ev("x", at(days=-400), at(days=1)), NOW, YEAR) == "over")
check("an end the store drops does not touch an event that has not begun, or began within the day",
      M.stale_or_far(ev("x", at(days=3), at(days=900)), NOW, YEAR) is None
      and M.stale_or_far(ev("x", at(hours=-2), at(days=900)), NOW, YEAR) is None)
check("end_dropped is mapsee_spam.implausible_end's verdict on the raw fields",
      M.end_dropped(ev("x", at(days=-500), at(days=100)))
      and not M.end_dropped(ev("x", at(days=-30), at(days=5))) and not M.end_dropped(ev("x", at(days=-30))))
check("an unreadable beginsOn is left for to_event to refuse",
      M.stale_or_far(ev("x", "soon"), NOW, NOW + timedelta(days=365)) is None)

# ---- the order: START_TIME_ASC, and the fallback when an instance refuses it ----

check("the query asks for the coming events first", "sortBy:START_TIME_ASC" in M.QUERY, M.QUERY)
check("the query sent carries the order", sess.posts and "sortBy:START_TIME_ASC" in sess.posts[0]["query"],
      sess.posts[:1])
check("the fallback query is the same question with no order",
      "sortBy" not in M.QUERY_UNSORTED
      and M.QUERY_UNSORTED == M.QUERY.replace(", sortBy:START_TIME_ASC", ""), M.QUERY_UNSORTED)

HTU = [{"message": 'Unknown argument "sortBy" on field "searchEvents" of type "RootQueryType".',
        "locations": [{"line": 3, "column": 45}]}]        # HTU Events' answer, 2026-11-08
check("the measured refusal is about the order", M.sort_refused(HTU))
check("an unknown enum value is about the order",
      M.sort_refused([{"message": 'Argument "sortBy" has invalid value START_TIME_ASC.'}]))
check("an error about anything else is not",
      not M.sort_refused([{"message": 'Cannot query field "status" on type "Event".'}])
      and not M.sort_refused(None) and not M.sort_refused([]))


class Paged:
    """A Mobilizon that answers page p from `pages` (1-based), total `total`;
    with refuse_sort it errors on any query carrying sortBy, as HTU does; with
    `error` it errors on every query."""

    def __init__(self, pages, total=None, refuse_sort=False, error=None):
        self.pages, self.refuse_sort, self.error = pages, refuse_sort, error
        self.total = total if total is not None else sum(len(p) for p in pages)
        self.posts = []

    def post(self, url, data=None, timeout=None):
        payload = json.loads(data)
        self.posts.append(payload)
        if self.error:
            return Resp({"errors": self.error})
        if self.refuse_sort and "sortBy" in payload["query"]:
            return Resp({"errors": HTU, "data": None})
        p = payload["variables"]["p"]
        rows = self.pages[p - 1] if 0 < p <= len(self.pages) else []
        return Resp({"data": {"searchEvents": {"total": self.total, "elements": rows}}})


def walk(sess, **site_extra):
    site = {"name": "m", "base_url": "https://m.test", "crawl_delay": 0, "limit": 2, **site_extra}
    store = Store()
    with redirect_stdout(io.StringIO()) as out:
        kept = M.ingest_site(store, sess, site)
    return kept, store, out.getvalue()


three = [[ev("a1", at(days=1)), ev("a2", at(days=2))], [ev("b1", at(days=3)), ev("b2", at(days=4))],
         [ev("c1", at(days=5))]]
sess = Paged(three, refuse_sort=True)
kept, store, log = walk(sess)
sorted_posts = [i for i, q in enumerate(sess.posts) if "sortBy" in q["query"]]
check("an instance that refuses sortBy is asked the same page again, once, without it",
      sorted_posts == [0] and [q["variables"]["p"] for q in sess.posts] == [1, 1, 2, 3],
      [(q["variables"]["p"], "sortBy" in q["query"]) for q in sess.posts])
check("...and read whole the old way", kept == 5 and len(store.rows) == 5, (kept, [r.source_id for r in store.rows]))
check("...and the log says which site fell back",
      "m: sortBy refused" in log and "unsorted (the instance refused sortBy)" in log
      and "query errors" not in log, log)

kept, _, _ = walk(sess)
check("the fallback belongs to the site's walk: the next walk asks for the order again",
      "sortBy" in sess.posts[4]["query"] and kept == 5, [("sortBy" in q["query"]) for q in sess.posts])

sess = Paged(three)
kept, _, log = walk(sess)
check("an instance that accepts the order is never asked without it",
      all("sortBy" in q["query"] for q in sess.posts) and len(sess.posts) == 3 and kept == 5
      and "refused" not in log and "unsorted" not in log, (len(sess.posts), log))

sess = Paged(three, error=[{"message": "internal error"}])
kept, _, log = walk(sess)
check("an error about anything else ends the walk as before, no second ask",
      len(sess.posts) == 1 and kept == 0 and "query errors" in log and "refused" not in log, (sess.posts, log))

sess = Paged(three, error=HTU)
kept, _, log = walk(sess)
check("an instance that errors either way is asked twice, not forever",
      len(sess.posts) == 2 and kept == 0 and "query errors" in log, (len(sess.posts), log))


class RefusesThenBreaks(Paged):
    """Refuses sortBy on page 1, then answers page 2 with the same words."""

    def post(self, url, data=None, timeout=None):
        if json.loads(data)["variables"]["p"] == 2:
            self.posts.append(json.loads(data))
            return Resp({"errors": HTU})
        return super().post(url, data, timeout)


sess = RefusesThenBreaks(three, refuse_sort=True)
kept, _, log = walk(sess)
check("once fallen back, a later error is not taken for a refusal and asked twice",
      [(q["variables"]["p"], "sortBy" in q["query"]) for q in sess.posts] == [(1, True), (1, False), (2, False)]
      and kept == 2 and log.count("sortBy refused") == 1,
      [(q["variables"]["p"], "sortBy" in q["query"]) for q in sess.posts])

slept = []
real_sleep = M.time.sleep
M.time.sleep = slept.append
try:
    walk(Paged(three[:1], refuse_sort=True), crawl_delay=7)
finally:
    M.time.sleep = real_sleep
check("the second ask waits crawl_delay, like any other request to that host", slept == [7.0], slept)

# ---- fetch_page: the one page fetch, public for mapsee_spam_audit ----

API = "https://m.test/api"
SITE0 = {"name": "m", "crawl_delay": 0}


def page1(query):
    return {"query": query, "variables": {"b": at(), "l": 2, "p": 1}}


sess = Paged(three)
body, query, why = M.fetch_page(sess, API, page1(M.QUERY), SITE0)
check("fetch_page: an instance that accepts the order answers once, sorted",
      query == M.QUERY and why is None and len(sess.posts) == 1
      and len(body["data"]["searchEvents"]["elements"]) == 2, (query[:40], why, len(sess.posts)))

sess = Paged(three, refuse_sort=True)
asked = page1(M.QUERY)
with redirect_stdout(io.StringIO()) as out:
    body, query, why = M.fetch_page(sess, API, asked, SITE0)
check("fetch_page: a refusal of the order is asked once more without it, and says which query answered",
      query == M.QUERY_UNSORTED and why is None and not body.get("errors")
      and ["sortBy" in q["query"] for q in sess.posts] == [True, False]
      and "m: sortBy refused" in out.getvalue(), ([("sortBy" in q["query"]) for q in sess.posts], query[:40]))
check("...without changing the caller's payload", asked["query"] == M.QUERY, asked["query"][:60])

sess = Paged(three, refuse_sort=True)
body, query, why = M.fetch_page(sess, API, page1(M.QUERY_UNSORTED), SITE0)
check("fetch_page: a query already unsorted is sent as it is, once",
      query == M.QUERY_UNSORTED and len(sess.posts) == 1 and not body.get("errors"), len(sess.posts))


class Status:
    def __init__(self, code):
        self.code, self.posts = code, 0

    def post(self, url, data=None, timeout=None):
        self.posts += 1
        r = Resp({})
        r.status_code = self.code
        return r


class Raises:
    def post(self, url, data=None, timeout=None):
        raise OSError("connection reset")


for code in (403, 429):
    sess = Status(code)
    body, query, why = M.fetch_page(sess, API, page1(M.QUERY), SITE0)
    check(f"fetch_page: HTTP {code} is a refusal: no body, the reason, and never asked again",
          body is None and why == f"HTTP {code}" and sess.posts == 1 and query == M.QUERY, (why, sess.posts))
body, query, why = M.fetch_page(Raises(), API, page1(M.QUERY), SITE0)
check("fetch_page: a failed request says so", body is None and why == "failed: connection reset", why)
_, _, log = walk(Status(403))
check("ingest_site prints the page and the reason, as before", "m p1 HTTP 403" in log, log)

# ---- mapsee_spam_audit reads through fetch_page, so HTU is audited too ----

import mapsee_spam_audit as AUDIT  # noqa: E402

AUDIT_SITE = {"name": "HTU Events", "base_url": "https://m.test", "limit": 2, "crawl_delay": 0}
sess = Paged(three, refuse_sort=True)
with redirect_stdout(io.StringIO()):
    row = AUDIT.audit_site(sess, AUDIT_SITE, 5)
check("the spam audit reads an instance that refuses the order: every row, unsorted after one refusal",
      row["read"] == 5 and not row["error"]
      and ["sortBy" in q["query"] for q in sess.posts] == [True, False, False, False],
      (row["read"], row["error"], [("sortBy" in q["query"]) for q in sess.posts]))
sess = Paged(three)
with redirect_stdout(io.StringIO()):
    row = AUDIT.audit_site(sess, AUDIT_SITE, 5)
check("...and one that accepts it is audited sorted, as the adapter reads it",
      row["read"] == 5 and all("sortBy" in q["query"] for q in sess.posts), (row["read"], len(sess.posts)))
sess = Status(403)
with redirect_stdout(io.StringIO()):
    row = AUDIT.audit_site(sess, AUDIT_SITE, 5)
check("...and a 403 is audited as a refusal after one request", row["read"] == 0
      and row["error"] == "HTTP 403" and sess.posts == 1, (row, sess.posts))

# ---- rows already over at the head of an ascending walk ----

OVER = [ev(f"o{i}", at(days=-400 - i)) for i in range(6)]          # begun long ago, no end
UP = [ev(f"u{i}", at(days=10 + i)) for i in range(6)]
sess = Paged([OVER[0:2], OVER[2:4], UP[0:2], UP[2:4], UP[4:6]], total=1000)
kept, store, log = walk(sess, max_pages=2)
check("pages of nothing but rows already over do not spend max_pages",
      [q["variables"]["p"] for q in sess.posts] == [1, 2, 3, 4]
      and sorted(r.source_id for r in store.rows) == ["u0", "u1", "u2", "u3"], [r.source_id for r in store.rows])
check("...and are counted and printed",
      "; 4 already over (begun, no end ahead)" in log
      and "2 page(s) held nothing but rows already over (p1,2), 2 of them not counted against max_pages 2"
      in log, log)

sess = Paged([[OVER[0], UP[0]], [OVER[1], UP[1]], [UP[2], UP[3]]], total=1000)
kept, store, log = walk(sess, max_pages=2)
check("a page with anything kept on it counts, so the walk still ends at max_pages",
      len(sess.posts) == 2 and kept == 2 and "held nothing but" not in log
      and "; 2 already over" in log, (len(sess.posts), log))

forever = [[ev(f"z{p}-{i}", at(days=-500 - p)) for i in range(2)] for p in range(1, 40)]
sess = Paged(forever, total=10_000)
kept, store, log = walk(sess, max_pages=3)
check("a server that answers nothing but rows already over stops at max_pages + OVER_PAGES_MAX",
      len(sess.posts) == 3 + M.OVER_PAGES_MAX and kept == 0, len(sess.posts))
check("...and says how many such pages it read and how many were free",
      f"{3 + M.OVER_PAGES_MAX} page(s) held nothing but rows already over" in log
      and f"{M.OVER_PAGES_MAX} of them not counted" in log, log)

kept, store, log = walk(Paged([[ev("expo", at(days=-30), at(days=5)), ev("advert", at(days=-500), at(days=100))],
                                [ev("this-morning", at(hours=-2)), ev("soon", at(days=1))]]))
check("the running head of the order is kept as before and counted; an end the store drops is not running",
      kept == 3 and sorted(r.source_id for r in store.rows) == ["expo", "soon", "this-morning"]
      and "; 1 kept begun over a day ago with an end ahead" in log, log)
check("...and the log says how many were over only because the store drops their end",
      "; 1 already over (begun, no end ahead; 1 of them with an end ahead over 400 days after the start,"
      " which the store drops)" in log, log)
_, _, log = walk(Paged([[ev("old-no-end", at(days=-500)), ev("soon", at(days=1))]]))
check("...and does not count a row that had no end at all as one",
      "; 1 already over (begun, no end ahead)" in log and "which the store drops" not in log, log)
_, _, log = walk(Paged([[ev("soon", at(days=1))]]))
check("...and a walk with no running head says nothing about one", "begun over a day ago" not in log, log)

FAR = [ev(f"f{i}", at(days=500 + i)) for i in range(4)]
sess = Paged([[OVER[0], FAR[0]], [FAR[1], FAR[2]], [FAR[3]]], total=1000)
kept, store, log = walk(sess, max_pages=2)
check("a page with a row beyond the horizon is not an over-page (the unsorted order ends that way)",
      len(sess.posts) == 2 and "held nothing but" not in log, (len(sess.posts), log))

print()
print(f"{'FAILURES: ' + ', '.join(fails) if fails else 'all checks passed'}")
sys.exit(1 if fails else 0)
