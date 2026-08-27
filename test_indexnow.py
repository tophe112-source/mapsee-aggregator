#!/usr/bin/env python3
"""
test_indexnow.py — reading the new events, against a stubbed PostgREST.

Prints one line per case and exits non-zero on failure, like the other 19.

Pinned here are two production failures of this one function, a year apart in
character and the same in shape: a walk whose COST was fixed and whose LENGTH
was not.

  * 2026-08-22 — OFFSET PAGING GETS DEARER EVERY PAGE. `offset=6000` asks
    Postgres to produce and discard six thousand rows to return the next
    thousand, so a full walk costs quadratically in pages. It held while the
    catalog was small and died at exactly offset=6000 once a 26-hour window
    held more than 6,000 new events. A keyset asks "created_at > the last one
    I saw" and costs the same on page seven as on page one.
  * 2026-08-26 — AND THEN THE NUMBER OF PAGES RAN AWAY. The window held
    ~261,600 indexable rows, the walk was ~262 requests against the anon
    role's ~3s ceiling, and one slow page killed the job — which then exited
    before submitting a single /c/ landing page, for mapsee.me and for six
    other domains that never needed this query at all. Three rules fell out
    and all three are below.

  * A STATUS CODE ALONE DIAGNOSES NOTHING. The 2026-08-22 job reported "500
    Server Error" and nothing else, because raise_for_status() never reads the
    body — so the one thing that says whether to take a SMALLER bite (57014)
    or to wait for the edge (an upstream 503) was discarded. Same lesson as
    mapsee_health_check reporting a statement timeout as "a source has gone
    quiet".

And the trap the first fix could have introduced: created_at is NOT unique —
a merge lands hundreds of rows on one timestamp — so a keyset on created_at
alone either serves that timestamp for ever or skips its tail.
"""
import sys
import time

import mapsee_indexnow as ix

# The retry backoff is real seconds in production and dead weight here: these
# cases deliberately drive it to exhaustion several times over, which cost the
# CI gate 32 seconds of sleeping to prove nothing. What is under test is WHICH
# request goes out next, never how long we waited.
time.sleep = lambda _s: None

FAILURES = []


def check(label, got, want):
    ok = got == want
    print(("  ok   " if ok else "  FAIL ") + label +
          ("" if ok else f"\n         got {got!r}\n        want {want!r}"))
    if not ok:
        FAILURES.append(label)


def check_true(label, got):
    check(label, bool(got), True)


class _Resp:
    def __init__(self, code=200, rows=None, text=""):
        self.status_code, self._rows, self.text = code, rows if rows is not None else [], text
    def json(self):
        return self._rows


_TIMEOUT = '{"code":"57014","message":"canceling statement due to statement timeout"}'


class _PG:
    """A PostgREST that pages by DESCENDING keyset and refuses OFFSET.

    `page_cap` refuses any limit above it (a size that is genuinely too big).
    `slow_for` refuses the first N requests whatever their size — which is what
    a busy moment actually looks like, and the case the retry exists for.
    """
    def __init__(self, rows, page_cap=None, fail_offset_at=None, slow_for=0):
        self.rows, self.calls, self.page_cap = rows, [], page_cap
        self.fail_offset_at, self.slow_for = fail_offset_at, slow_for
    def get(self, url, params=None, headers=None, timeout=None):
        p = dict(params or {})
        self.calls.append(p)
        if self.slow_for > 0:
            self.slow_for -= 1
            return _Resp(500, text=_TIMEOUT)
        if self.fail_offset_at is not None and int(p.get("offset", 0)) >= self.fail_offset_at:
            return _Resp(500, text=_TIMEOUT)
        limit = int(p["limit"])
        if self.page_cap and limit > self.page_cap:
            return _Resp(500, text=_TIMEOUT)
        rows = self.rows
        expr = p.get("or")
        if expr:
            ct = expr.split('created_at.lt."', 1)[1].split('"', 1)[0]
            cid = expr.split('id.lt."', 1)[1].split('"', 1)[0]
            rows = [r for r in rows if (r["created_at"], r["id"]) < (ct, cid)]
        return _Resp(200, rows[:limit])


def _rows(n, per_stamp=1):
    """Newest first, which is the order this function now walks in."""
    out = []
    for i in range(n):
        out.append({"id": f"id-{i:05d}",
                    "created_at": f"2026-08-21T{(i // per_stamp) % 24:02d}:00:00+00:00"})
    out.sort(key=lambda r: (r["created_at"], r["id"]), reverse=True)
    return out


def walk(pg, **kw):
    return ix.fetch_new_event_ids("https://db.example", "k", 26, pg, **kw)


def main():
    ix.PAGE = 400          # realistic: PAGE_MIN is three halvings below production's 1000
    BIG = 10 ** 6          # "no cap" for the cases that are not about the cap

    print("a full walk, deeper than the offset version could reach")
    rows = _rows(650)
    pg = _PG(rows)
    got, complete = walk(pg, max_urls=BIG)
    check("every id is returned", len(got), 650)
    check("newest first", got, [r["id"] for r in rows])
    check("no duplicates", len(set(got)), 650)
    check_true("the walk reports itself complete", complete)
    check_true("and it never sent an offset", all("offset" not in c for c in pg.calls))
    check_true("it sent a keyset instead", any("or" in c for c in pg.calls[1:]))

    print()
    print("NEWEST FIRST, because the cap has to keep the fresh half")
    check_true("every page is ordered descending",
               all(c["order"] == "created_at.desc,id.desc" for c in pg.calls))

    print()
    print("created_at is not unique — the id breaks the tie")
    dup = _rows(300, per_stamp=100)          # 100 rows share each timestamp
    got2, _ = walk(_PG(dup), max_urls=BIG)
    check("no row is served twice", len(got2), len(set(got2)))
    check("and none of the shared timestamp is skipped", len(got2), 300)

    print()
    print("THE CAP: a run announces the newest N and says what it left")
    all_rows = _rows(650)
    got3, complete3 = walk(_PG(all_rows), max_urls=250)
    check("it stops at the ceiling", len(got3), 250)
    check("and what it kept is the NEWEST 250, not the oldest",
          got3, [r["id"] for r in all_rows[:250]])
    check("a capped walk is not a complete one, and says so", complete3, False)

    print()
    print("a busy moment wants the SAME page again, not a smaller one")
    # The page size was never the problem — measured, a keyset page costs 0.4s
    # flat from page 1 to page 60 — so halving 1000 -> 125 turns one walk into
    # eight times the requests and eight times the exposure. PAGE_MIN's own
    # comment always said this; it just had no other lever to reach for.
    pg4 = _PG(_rows(300), slow_for=1)
    got4, complete4 = walk(pg4, max_urls=BIG)
    check("the walk still completes", len(got4), 300)
    check_true("it re-asked the identical page", complete4)
    check("...at the same size, not a halved one",
          [int(c["limit"]) for c in pg4.calls[:2]], [400, 400])

    print()
    print("a size that genuinely will not fit still gets halved")
    pg5 = _PG(_rows(300), page_cap=150)
    got5, _ = walk(pg5, max_urls=BIG)
    check("the walk still completes", len(got5), 300)
    check_true("by eventually halving the page",
               min(int(c["limit"]) for c in pg5.calls) <= 150)
    check_true("having first re-asked at the failing size (retry before shrink)",
               len([c for c in pg5.calls if int(c["limit"]) == 400]) > 1)

    print()
    print("OUT OF LEVERS: what it has is still worth announcing")
    # This used to raise, and main() used to sys.exit(1) on the raise — ending
    # the process before a single /c/ landing page was submitted, for SEVEN
    # domains. The events are the time-critical half; the landing pages are the
    # independent half. Losing one must not lose the other.
    pg6 = _PG(_rows(300), page_cap=10)     # nothing this job can ask will fit
    got6, complete6 = walk(pg6, max_urls=BIG)
    check("it returns rather than raising", isinstance(got6, list), True)
    check("and says the walk was cut short", complete6, False)
    check_true("having actually tried the floor before giving up",
               any(int(c["limit"]) == ix.PAGE_MIN for c in pg6.calls))

    print()
    print("IndexNow announces a SUBSET of the sitemap, never a superset")
    # sitemapEvents() gained `pin_only: is.false` when ../mapsee 0194 landed and
    # this walk did not, so it spent months announcing /e/ pages for drinking
    # fountains that the sitemap deliberately withholds. Nothing could report
    # that: both ends answer 200 and the URLs are real.
    pg8 = _PG(_rows(10))
    walk(pg8, max_urls=BIG)
    check_true("every page filters furniture out",
               all(c.get("pin_only") == "is.false" for c in pg8.calls))
    check_true("...and it is the same three other predicates the sitemap uses",
               all(c.get("is_private") == "eq.false" and c.get("hidden_at") == "is.null"
                   and str(c.get("starts_at", "")).startswith("gte.")
                   for c in pg8.calls))

    # ../mapsee's migrations are applied by hand, so a database can be missing
    # the column. A 400 is not retryable and would cost the whole walk.
    class _NoColumn:
        def __init__(self, rows): self.rows, self.calls, self.n = rows, [], 0
        def get(self, url, params=None, headers=None, timeout=None):
            p = dict(params or {}); self.calls.append(p)
            if "pin_only" in p:
                return _Resp(400, text='{"code":"PGRST204","message":'
                                       '"column events.pin_only does not exist"}')
            return _Resp(200, self.rows[:int(p["limit"])])
    pg9 = _NoColumn(_rows(5))
    got9, complete9 = walk(pg9, max_urls=BIG)
    check("a database without the column still gets its URLs announced", len(got9), 5)
    check_true("...having dropped the predicate once rather than every page",
               len([c for c in pg9.calls if "pin_only" in c]) == 1)

    print()
    print("the server's own words reach the report")
    msg = ix._explain(_Resp(500, text=_TIMEOUT))
    check_true("the status is in it", "500" in msg)
    check_true("and so is the postgres code that says what to do", "57014" in msg)
    check("an empty body is said to be empty, not blank",
          "(empty body)" in ix._explain(_Resp(500, text="")), True)

    print()
    print("a cursor that cannot move stops, loudly")
    class _Stuck:
        def get(self, url, params=None, headers=None, timeout=None):
            return _Resp(200, [{"id": "same", "created_at": "2026-08-21T00:00:00+00:00"}]
                              * int(params["limit"]))
    got7, complete7 = walk(_Stuck(), max_urls=BIG)
    check("it returns what it had rather than looping for ever", got7, ["same"])
    check("and does not call that a complete walk", complete7, False)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: " + "; ".join(FAILURES))
        return 1
    print("all indexnow checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
