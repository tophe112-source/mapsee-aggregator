#!/usr/bin/env python3
"""
test_indexnow.py — reading the new events, against a stubbed PostgREST.

Prints one line per case and exits non-zero on failure, like the other 18.

Pinned here is the failure of 2026-08-22 and the two rules it broke:

  * OFFSET PAGING GETS DEARER EVERY PAGE. `offset=6000` asks Postgres to
    produce and discard six thousand rows to return the next thousand, so a
    full walk costs quadratically in pages. It held while the catalog was
    small and died at exactly offset=6000 once a 26-hour window held more than
    6,000 new events. A keyset asks "created_at > the last one I saw" and costs
    the same on page seven as on page one.
  * A STATUS CODE ALONE DIAGNOSES NOTHING. The job reported "500 Server Error"
    and nothing else, because raise_for_status() never reads the body — so the
    one thing that says whether to take a SMALLER bite (57014) or to wait for
    the edge (an upstream 503) was discarded. Same lesson as
    mapsee_health_check reporting a statement timeout as "a source has gone
    quiet".

And the trap the fix itself could have introduced: created_at is NOT unique —
a merge lands hundreds of rows on one timestamp — so a keyset on created_at
alone either serves that timestamp for ever or skips its tail.
"""
import sys
import types

import mapsee_indexnow as ix

FAILURES = []


def check(label, got, want):
    ok = got == want
    print(("  ok   " if ok else "  FAIL ") + label + ("" if ok else f"\n         got {got!r}\n        want {want!r}"))
    if not ok:
        FAILURES.append(label)


def check_true(label, got):
    check(label, bool(got), True)


class _Resp:
    def __init__(self, code=200, rows=None, text=""):
        self.status_code, self._rows, self.text = code, rows if rows is not None else [], text
    def json(self):
        return self._rows


class _PG:
    """A PostgREST that pages by keyset and refuses OFFSET past a depth."""
    def __init__(self, rows, page_cap=None, fail_offset_at=None):
        self.rows, self.calls, self.page_cap = rows, [], page_cap
        self.fail_offset_at = fail_offset_at
    def get(self, url, params=None, headers=None, timeout=None):
        p = dict(params or {})
        self.calls.append(p)
        if self.fail_offset_at is not None and int(p.get("offset", 0)) >= self.fail_offset_at:
            return _Resp(500, text='{"code":"57014","message":"canceling statement due to statement timeout"}')
        limit = int(p["limit"])
        if self.page_cap and limit > self.page_cap:
            return _Resp(500, text='{"code":"57014","message":"canceling statement due to statement timeout"}')
        rows = self.rows
        expr = p.get("or")
        if expr:
            ct = expr.split('created_at.gt."', 1)[1].split('"', 1)[0]
            cid = expr.split('id.gt."', 1)[1].split('"', 1)[0]
            rows = [r for r in rows
                    if (r["created_at"], r["id"]) > (ct, cid)]
        return _Resp(200, rows[:limit])


def _rows(n, per_stamp=1):
    out = []
    for i in range(n):
        out.append({"id": f"id-{i:05d}",
                    "created_at": f"2026-08-21T{(i // per_stamp) % 24:02d}:00:00+00:00"})
    out.sort(key=lambda r: (r["created_at"], r["id"]))
    return out


def main():
    ix.PAGE = 400          # realistic: PAGE_MIN is three halvings below production's 1000

    print("a full walk, deeper than the offset version could reach")
    rows = _rows(650)
    pg = _PG(rows)
    got = ix.fetch_new_event_ids("https://db.example", "k", 26, pg)
    check("every id is returned", len(got), 650)
    check("in created_at order", got, [r["id"] for r in rows])
    check("no duplicates", len(set(got)), 650)
    check_true("and it never sent an offset", all("offset" not in c for c in pg.calls))
    check_true("it sent a keyset instead", any("or" in c for c in pg.calls[1:]))

    print()
    print("created_at is not unique — the id breaks the tie")
    dup = _rows(300, per_stamp=100)          # 100 rows share each timestamp
    pg2 = _PG(dup)
    got2 = ix.fetch_new_event_ids("https://db.example", "k", 26, pg2)
    check("no row is served twice", len(got2), len(set(got2)))
    check("and none of the shared timestamp is skipped", len(got2), 300)

    print()
    print("a statement timeout means a SMALLER bite, not the same one again")
    pg3 = _PG(_rows(300), page_cap=150)
    got3 = ix.fetch_new_event_ids("https://db.example", "k", 26, pg3)
    check("the walk still completes", len(got3), 300)
    check_true("by halving the page rather than re-asking the same question",
               min(int(c["limit"]) for c in pg3.calls) <= 150)
    check_true("and it never asked twice at a size that had just failed",
               len([c for c in pg3.calls if int(c["limit"]) == 400]) == 1)

    print()
    print("below the floor a timeout is no longer about page size, so it is REPORTED")
    pg4 = _PG(_rows(300), page_cap=10)     # nothing this job can ask will fit
    try:
        ix.fetch_new_event_ids("https://db.example", "k", 26, pg4)
        check("a hopeless timeout raises rather than returning a short list", False, True)
    except RuntimeError as e:
        check_true("it raises", True)
        check_true("carrying the postgres code, so the next reader knows why", "57014" in str(e))

    print()
    print("the server's own words reach the report")
    body = '{"code":"57014","message":"canceling statement due to statement timeout"}'
    msg = ix._explain(_Resp(500, text=body))
    check_true("the status is in it", "500" in msg)
    check_true("and so is the postgres code that says what to do", "57014" in msg)
    check("an empty body is said to be empty, not blank",
          "(empty body)" in ix._explain(_Resp(500, text="")), True)

    print()
    print("a cursor that cannot move stops, loudly")
    class _Stuck:
        def get(self, url, params=None, headers=None, timeout=None):
            return _Resp(200, [{"id": "same", "created_at": "2026-08-21T00:00:00+00:00"}] * int(params["limit"]))
    got4 = ix.fetch_new_event_ids("https://db.example", "k", 26, _Stuck())
    check("it returns what it had rather than looping for ever", got4, ["same"])

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: " + "; ".join(FAILURES))
        return 1
    print("all indexnow checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
