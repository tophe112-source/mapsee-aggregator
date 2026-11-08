#!/usr/bin/env python3
"""
test_ingest_ods.py — reading an Opendatasoft dataset past the 10,000 offset cap.

The Explore v2.1 records endpoint serves offset + limit up to 10,000 and no
further. "OpenAgenda France" kept 9,982-9,990 rows after exactly 100 requests on
each of six runs (2026-11-01..07, CI logs): the tail of its 90-day window was
never read, and nothing said so. mapsee_ingest_ods now reads a source with a
`window` in consecutive date windows when its first page says the query holds
10,000 or more. These cases pin what that read must and must not do:

  - every row in the window is read, and nothing outside it (1, 2, 8)
  - a window still over the cap is split again; one day is the floor (3, 6)
  - a row is stored once, however many windows serve it (4)
  - the walk stops at its bounds, earliest rows first, and never at a 400 (5, 9)
  - a 400 to a windowed query falls back to the plain read; a 429 does not (9)
  - an under-cap source, or one without `window`, asks exactly what it asked
    before (1, 7)
  - the credit line survives the sync's description cap (10)
  - the real config fits the step's timeout at its bounds, and the rows it
    adds fit the civic SYNC step's timeout on the worst landing day, a
    Wednesday full refresh (11) - this fails until that step has 90 minutes

FakeODS is the portal: it answers 400 past the cap, as the portal does, and it
fails the test on a `where` clause it cannot parse rather than matching
everything. No network.

Run: python test_ingest_ods.py
"""
import contextlib
import io
import json
import os
import re
import sys
import tempfile
import urllib.parse
from datetime import datetime, timedelta, timezone

import mapsee_ingest_ods as ODS
from mapsee_ingest import EventStore

ODS.PAUSE_S = 0                       # the fake has no rate to keep

# The fake portal's clock, and the date the adapter is told it is. They agree
# here; section 8 makes them disagree on purpose.
NOW = datetime(2026, 11, 8, 15, 0, tzinfo=timezone.utc)
TODAY = "20261108"
CAP = 10000
ORIGINAL_WHERE = ("firstdate_begin>=now() and firstdate_begin<=now(days=90) "
                  "and location_countrycode in ('FR')")


def _midnight(iso_day):
    return datetime.fromisoformat(iso_day).replace(tzinfo=timezone.utc)


_CLAUSES = [
    (re.compile(r"^(\w+)>=now\(\)$"), lambda m: (m.group(1), ">=", NOW)),
    (re.compile(r"^(\w+)<=now\(days=(\d+)\)$"),
     lambda m: (m.group(1), "<=", NOW + timedelta(days=int(m.group(2))))),
    (re.compile(r"^(\w+)>=date'(\d{4}-\d{2}-\d{2})'$"),
     lambda m: (m.group(1), ">=", _midnight(m.group(2)))),
    (re.compile(r"^(\w+)<date'(\d{4}-\d{2}-\d{2})'$"),
     lambda m: (m.group(1), "<", _midnight(m.group(2)))),
    # Understood so that a CLOSED upper bound is caught by the rows it reads
    # twice (section 4), not by the fake failing to parse it.
    (re.compile(r"^(\w+)<=date'(\d{4}-\d{2}-\d{2})'$"),
     lambda m: (m.group(1), "<=", _midnight(m.group(2)))),
    (re.compile(r"^(\w+) in \(((?:'[^']*',?)+)\)$"),
     lambda m: (m.group(1), "in", set(re.findall(r"'([^']*)'", m.group(2))))),
]


def _parse(clause):
    for rx, build in _CLAUSES:
        m = rx.match(clause.strip())
        if m:
            return build(m)
    raise AssertionError(f"FakeODS cannot parse where clause {clause!r}")


def _holds(row, pred):
    col, op, val = pred
    v = row["_dt"] if col == "firstdate_begin" else row.get(col)
    if op == ">=":
        return v >= val
    if op == "<=":
        return v <= val
    if op == "<":
        return v < val
    return v in val


class _Resp:
    def __init__(self, body, status=200):
        self._b, self.status_code = body, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"{self.status_code} Client Error")

    def json(self):
        return self._b


class FakeODS:
    """The Explore v2.1 /records endpoint over an in-memory dataset."""

    def __init__(self, rows, fail_at=None, reject=None):
        self.rows, self.fail_at, self.reject = rows, fail_at, reject
        self.calls, self.urls, self.refused = [], [], 0
        self._cache = {}

    def get(self, url, timeout=None):
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        where, order = q["where"][0], q.get("order_by", [None])[0]
        limit, offset = int(q["limit"][0]), int(q["offset"][0])
        self.calls.append({"where": where, "offset": offset, "limit": limit, "order_by": order})
        self.urls.append(url)
        if self.fail_at is not None and len(self.calls) == self.fail_at:
            return _Resp({"error_code": "TooManyRequests"}, 429)
        if self.reject is not None and self.reject(where):
            return _Resp({"error_code": "ODSQLError", "message": "unsupported query"}, 400)
        if limit > 100 or offset + limit > CAP:
            self.refused += 1
            return _Resp({"error_code": "InvalidRESTParameterError",
                          "message": "offset + limit must be lower than or equal to 10000"}, 400)
        match = self._match(where, order)
        return _Resp({"total_count": len(match),
                      "results": [{k: v for k, v in r.items() if k != "_dt"}
                                  for r in match[offset:offset + limit]]})

    def _match(self, where, order):
        if (where, order) not in self._cache:
            preds = [_parse(c) for c in where.split(" and ")]
            out = [r for r in self.rows if all(_holds(r, p) for p in preds)]
            if order:
                assert order == "firstdate_begin", order
                out.sort(key=lambda r: (r["_dt"], r["uid"]))
            self._cache[(where, order)] = out
        return self._cache[(where, order)]


def _row(uid, dt, country="FR"):
    return {"uid": uid, "_dt": dt, "firstdate_begin": dt.isoformat(),
            "title_fr": f"Atelier {uid}", "longdescription_fr": "Entrée libre.",
            "location_name": f"Salle {uid[-2:]}", "location_countrycode": country,
            "location_coordinates": {"lon": 2.35, "lat": 48.85},
            "canonicalurl": f"https://openagenda.test/e/{uid}"}


def dataset(spread, extras=True):
    """Rows by day: `spread` is [(first_day, last_day, count), ...] from NOW.
    Within a band the rows are spaced evenly from 06:00 to 22:00 of each day.
    `extras` adds rows the query must NEVER return: past, past the horizon,
    and in another country — plus one row on each midnight boundary, which a
    half-open window must serve exactly once."""
    rows, n = [], 0
    for first, last, count in spread:
        days = last - first + 1
        for i in range(count):
            d = first + i % days
            minute = 360 + (i // days) % 960
            dt = (NOW.replace(hour=0, minute=0) + timedelta(days=d, minutes=minute))
            if dt <= NOW:
                dt = NOW + timedelta(minutes=1 + i % 500)
            rows.append(_row(f"u{n:06d}", dt))
            n += 1
    if extras:
        rows.append(_row("past1", NOW - timedelta(days=3)))
        rows.append(_row("past2", NOW - timedelta(minutes=1)))
        rows.append(_row("late1", NOW + timedelta(days=95)))
        rows.append(_row("be1", NOW + timedelta(days=4), country="BE"))
        for d in range(1, 91):
            rows.append(_row(f"mid{d:02d}", NOW.replace(hour=0, minute=0) + timedelta(days=d)))
    return rows


def expected(rows):
    return {r["uid"] for r in rows if r["location_countrycode"] == "FR"
            and NOW <= r["_dt"] <= NOW + timedelta(days=90)}


def src(**kw):
    s = {"name": "T", "domain": "x.test", "dataset": "d", "category": "community",
         "within_days": 90, "countries": ["FR"], "max": 60000,
         "map": {"id": "uid", "title": "title_fr", "description": "longdescription_fr",
                 "start": "firstdate_begin", "venue": "location_name",
                 "country": "location_countrycode", "geo": "location_coordinates",
                 "url": "canonicalurl"}}
    s.update(kw)
    return s


def run(rows, source, fail_at=None, today=TODAY, reject=None):
    os.environ["MAPSEE_TODAY"] = today
    fake = FakeODS(rows, fail_at=fail_at, reject=reject)
    out = io.StringIO()
    with tempfile.TemporaryDirectory() as d:
        store = EventStore(os.path.join(d, "s.json"))
        with contextlib.redirect_stdout(out):
            kept = ODS.ingest_source(store, fake, source)
        # The store keeps a row's identity in its source refs, not at the top.
        ids = [ref.get("source_id") for r in store.records.values()
               for ref in r.get("sources") or []]
    return kept, ids, fake, out.getvalue()


def _windows(log):
    m = re.search(r"in (\d+) windows \((\d+) split\) with (\d+) requests", log)
    return tuple(int(x) for x in m.groups()) if m else None


# The civic sync step, measured from the civic job's CI logs (section 11).
# Wednesday full refreshes took 43.7 (2026-10-21), 22.6 (10-28) and 39.8 min
# (11-04); only-new days 4.4-11.8 (11-01..11-07). Upserts ran at 14.4-70.8 ms a
# row (70.8: 16,290 rows in 1,152.7 s on 11-04, with ReadTimeouts); preparing
# and comparing a row on a full refresh at 6.2-11.8 ms; an only-new day's
# state read and prepare at 7.4 ms an id (34,883 in 257 s, 11-06). The worst
# of each is taken.
SYNC_WEDNESDAY_MIN = 43.7
SYNC_ONLY_NEW_MIN = 11.8
UPSERT_MS = 70.8
PREPARE_MS = 11.8
STATE_READ_MS = 7.4
# What the plain read kept before the windows (the fewest, so the most new
# rows), and the most each credited source kept (the most rewritten rows).
PLAIN_KEPT = {"OpenAgenda France": 9982}
MEASURED_KEPT = {"OpenAgenda France": 9990,
                 "OpenAgenda (GB/IE/DE/NL/CA/BE/CH/ES/IT/SE)": 976}

_TOKEN = re.compile(r"\s*(matrix\.group|'[^']*'|\d+|==|!=|&&|\|\||\(|\))")


def _timeout_minutes(expr, group):
    """A step's timeout-minutes for one matrix group: a number, or a GitHub
    expression of `matrix.group`, quoted strings, numbers, == != && || and
    parentheses, whose && and || return operands as Python's and/or do.
    Anything else is None, never evaluated."""
    expr = expr.strip()
    if re.fullmatch(r"\d+", expr):
        return int(expr)
    m = re.fullmatch(r"\$\{\{(.*)\}\}", expr)
    if not m:
        return None
    body, pos, out = m.group(1).strip(), 0, []
    while pos < len(body):
        t = _TOKEN.match(body, pos)
        if not t:
            return None
        tok = t.group(1)
        out.append({"matrix.group": repr(group), "&&": " and ", "||": " or "}.get(tok, tok))
        pos = t.end()
    try:
        v = eval("".join(out), {"__builtins__": {}}, {})
    except Exception:
        return None
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def main():
    checks = []
    WIN = {"window": True}

    # ------------------------------------------------------ 1. under the cap
    #
    # NOTHING CHANGES BELOW THE CAP. The first page carries total_count, so
    # deciding costs no request; 3,000 rows ask the same 31 URLs with or
    # without `window` (30 full pages and the empty one that ends the loop),
    # and each is the URL the old loop built.
    rows = dataset([(0, 89, 3000)], extras=False)
    k0, ids0, f0, log0 = run(rows, src())
    k1, ids1, f1, log1 = run(rows, src(**WIN))
    old_urls = ["https://x.test/api/explore/v2.1/catalog/datasets/d/records?" +
                urllib.parse.urlencode({"where": ORIGINAL_WHERE, "limit": 100, "offset": o,
                                        "order_by": "firstdate_begin"})
                for o in range(0, 3001, 100)]
    checks.append((f0.urls == old_urls, "under the cap, no window: the old loop's 31 URLs, byte for byte"))
    checks.append((f1.urls == old_urls, "under the cap, window set: the same 31 URLs — the window never engages"))
    checks.append((k0 == k1 == 3000 and len(set(ids1)) == 3000, "...and both keep all 3,000"))
    checks.append(("windows" not in log1 and "NOT read" not in log1,
                   "...and the under-cap log says only what it said before"))
    rows = dataset([(0, 89, 9999)], extras=False)
    k, ids, f, log = run(rows, src(**WIN))
    checks.append((k == 9999 and all("date'" not in c["where"] for c in f.calls) and f.refused == 0,
                   "9,999 rows with a window: the plain read, all 9,999, no date window"))

    # ------------------------------------------------------- 2. over the cap
    #
    # THE CASE THE WINDOWS EXIST FOR: 25,000 rows front-loaded the way an
    # agenda is (most of them soon). Every row in the window is kept, none of
    # the 94 rows outside it, the portal never answers 400, and every request
    # still carries the original query — the windows only narrow it.
    rows = dataset([(0, 29, 15000), (30, 59, 6250), (60, 89, 3750)])
    want = expected(rows)
    k, ids, f, log = run(rows, src(**WIN))
    w = _windows(log)
    checks.append((len(want) == 25090, f"fixture: 25,000 + 90 midnight rows in the window ({len(want)})"))
    checks.append((set(ids) == want and k == len(want) == len(ids),
                   f"over the cap: every row in the window is read, once ({k} kept)"))
    checks.append((f.refused == 0 and all(c["offset"] + c["limit"] <= CAP for c in f.calls),
                   "...and no request passes offset + limit 10,000 (the fake answers 400 past it)"))
    checks.append((all(c["where"].startswith(ORIGINAL_WHERE) for c in f.calls),
                   "...and every window is the original query plus date bounds"))
    checks.append((w is not None and w[1] >= 2 and w[2] == len(f.calls) + 0,
                   f"...in {w and w[0]} windows, {w and w[1]} split, and the log counts its requests ({w and w[2]})"))
    checks.append((len(f.calls) <= 25090 // 100 + 3 * (w[0] if w else 0),
                   f"...for about one request a hundred rows plus a few a window ({len(f.calls)})"))
    checks.append(("the query holds 25090" in log,
                   "...and the log prints the query's total — the number nobody had measured"))

    # THE WINDOWS KEEP THE PAUSE. France asks up to five times as many pages
    # as the plain read did, so every windowed request after the first waits
    # PAUSE_S, as the plain loop's pages do (a review found deleting the
    # sleep passed every case above).
    slept, real_sleep, real_pause = [], ODS.time.sleep, ODS.PAUSE_S
    ODS.PAUSE_S, ODS.time.sleep = 0.25, slept.append
    try:
        k, ids, f, log = run(rows, src(**WIN))
    finally:
        ODS.time.sleep, ODS.PAUSE_S = real_sleep, real_pause
    checks.append((len(f.urls) > 100 and slept.count(0.25) >= len(f.urls) - 1,
                   f"every request after the first waits PAUSE_S ({slept.count(0.25)} waits, "
                   f"{len(f.urls)} requests)"))

    # ----------------------------------------- 3. a half still over the cap
    #
    # 16,000 rows in the first week: halving 90 days once leaves 16,000+ in
    # the first half, and again, and again. It must keep halving rather than
    # read the first 10,000 of a half and stop.
    rows = dataset([(0, 6, 16000), (7, 89, 6000)])
    want = expected(rows)
    k, ids, f, log = run(rows, src(**WIN))
    w = _windows(log)
    checks.append((set(ids) == want and f.refused == 0,
                   f"a half over the cap is split again: all {len(want)} read, no 400"))
    checks.append((w is not None and w[1] >= 4, f"...which took {w and w[1]} splits (>= 4)"))

    # ---------------------------------------------------- 4. one row, once
    #
    # One uid listed twice, on day 2 and day 70, lands in two windows; the 90
    # midnight rows sit exactly on window boundaries. The repeat is dropped by
    # the adapter's own id and counted, and no boundary row is read twice.
    rows = dataset([(0, 89, 21000)])
    twin = dict(rows[5]); twin["_dt"] = NOW + timedelta(days=70)
    twin["firstdate_begin"] = twin["_dt"].isoformat()
    rows.append(twin)
    k, ids, f, log = run(rows, src(**WIN))
    checks.append((ids.count(rows[5]["uid"]) == 1 and len(ids) == len(set(ids)) == k,
                   "a uid served by two windows is stored once"))
    checks.append(("1 repeated ids dropped" in log,
                   "...and counted as exactly one repeat: no midnight row was read by two windows"))

    # ------------------------------------------------------------ 5. bounds
    #
    # REQUESTS: 40 is 40, including the first page that measured the total.
    # Windows are read earliest first and rows in start order, so what a
    # bounded walk keeps is the EARLIEST rows — the far end is what it drops,
    # the same end the plain read always dropped.
    rows = dataset([(0, 29, 15000), (30, 89, 10000)])
    want = expected(rows)
    ordered = sorted((r for r in rows if r["uid"] in want), key=lambda r: (r["_dt"], r["uid"]))
    k, ids, f, log = run(rows, src(window={"max_requests": 40}))
    checks.append((len(f.calls) == 40, f"max_requests 40: exactly 40 requests ({len(f.calls)})"))
    checks.append(("stopped by the request bound (40)" in log, "...and the log names the bound"))
    checks.append((set(ids) == {r["uid"] for r in ordered[:len(ids)]} and len(ids) > 0,
                   f"...and the {len(ids)} kept are the earliest rows, not a scattering"))

    # WINDOWS: with room for 3, the root splits once and neither half may; the
    # 18,000-row first half is read to the cap and its loss printed, the
    # 7,000-row second half in full. Three windows visited, never more.
    rows = dataset([(0, 44, 18000), (45, 89, 7000)], extras=False)
    k, ids, f, log = run(rows, src(window={"max_windows": 3}))
    w = _windows(log)
    checks.append((w is not None and w[0] == 3 and w[1] == 1,
                   f"max_windows 3: three windows, one split ({w})"))
    checks.append((k == 17000 and "8000 rows in it are NOT read" in log and f.refused == 0,
                   f"...the over-cap half is read to the cap and its 8,000 named, the other half whole ({k})"))

    # ROWS: `max` is still the row cap, checked a page at a time as before.
    rows = dataset([(0, 89, 25000)], extras=False)
    k, ids, f, log = run(rows, src(window=True, max=12000))
    checks.append((12000 <= k < 12100 and "row cap (12000)" in log,
                   f"max 12,000: stops within a page of it and says so ({k})"))

    # ------------------------------------------------- 6. one day is the floor
    #
    # 12,500 rows on one day cannot be split by a date: it is read to the cap,
    # the 2,500 past it are printed as lost, and the other days are whole.
    rows = dataset([(3, 3, 12500), (10, 80, 500)], extras=False)
    k, ids, f, log = run(rows, src(**WIN))
    checks.append((k == 10500 and f.refused == 0, f"a 12,500-row day: 10,000 of it + the 500 elsewhere ({k})"))
    # The day is the first that holds a row, so its window may open at now():
    # "now..2026-11-12" holds day 3 alone, since nothing starts before it.
    checks.append((re.search(r"window (?:now|2026-11-11)\.\.2026-11-12 holds 12500, .*\(one day\); "
                             r"2500 rows in it are NOT read", log) is not None,
                   "...and the log names the day, its total and the 2,500 lost"))

    # ------------------------------------- 7. over the cap without a window
    #
    # The old read, unchanged — 100 requests, 10,000 rows — but it now SAYS
    # what it did not read, which for months it did not.
    rows = dataset([(0, 89, 25000)], extras=False)
    k, ids, f, log = run(rows, src())
    checks.append((k == 10000 and len(f.calls) == 100 and f.refused == 0
                   and all(c["where"] == ORIGINAL_WHERE for c in f.calls),
                   "no window: the old 100 requests, 10,000 rows, the original query"))
    checks.append(("the query holds 25000; 15000 rows past the 10000 read are NOT read" in log,
                   "...and the log now says 15,000 were not read"))
    k, ids, f, log = run(rows, src(max=6000))
    checks.append((k == 6000 and "19000 rows past the 6000 read are NOT read (max 6000," in log,
                   "...and when the source's own max stopped it first, the line names that cap"))

    # ------------------------------------------- 8. the machine's date is wrong
    #
    # Window bounds are dates computed HERE; the window's ends are now() THERE.
    # Every window keeps the original now() bounds, so a date two weeks early or
    # three weeks late costs splits and never a row: no past row, no row past
    # the horizon, none lost at a boundary.
    rows = dataset([(0, 29, 15000), (30, 89, 10000)])
    want = expected(rows)
    for today in ("20261025", "20261201"):
        k, ids, f, log = run(rows, src(**WIN), today=today)
        checks.append((set(ids) == want and len(ids) == k and f.refused == 0,
                       f"MAPSEE_TODAY={today} against a portal on 2026-11-08: exactly the window's {len(want)}"))

    # -------------------------------------------- 9. a refusal ends the walk
    #
    # A 429 on the fifth request is a refusal: nothing after it is asked, and
    # nothing is asked again.
    k, ids, f, log = run(rows, src(**WIN), fail_at=5)
    checks.append((len(f.calls) == 5 and "failure at offset" in log and "429" in log
                   and "the plain way" not in log,
                   f"a 429 ends the windowed walk on the spot, no fallback ({len(f.calls)} requests)"))

    # A 400 IS THE QUERY, NOT THE READER. The windowed `<date'...'` has never
    # been sent to the live portal. If it answers 400, the walk must not end
    # with less than the plain read kept: it falls back to the original query
    # from the page already in hand — 1 + 1 + 99 requests, the first 10,000.
    ordered = sorted((r for r in rows if r["uid"] in want), key=lambda r: (r["_dt"], r["uid"]))
    first10k = {r["uid"] for r in ordered[:CAP]}
    k, ids, f, log = run(rows, src(**WIN), reject=lambda w: "date'" in w)
    checks.append((k == CAP and set(ids) == first10k and len(f.calls) == 101
                   and "answered 400 to a windowed query" in log,
                   f"every date window refused with 400: the plain read's first 10,000 in {len(f.calls)} requests"))
    # ...and after a window that worked, the plain read tops up to the same
    # 10,000 without storing the earlier window's rows twice.
    k, ids, f, log = run(rows, src(**WIN), reject=lambda w: ">=date'" in w)
    checks.append((k == CAP and set(ids) == first10k and len(ids) == len(set(ids))
                   and "repeated ids dropped" in log,
                   f"a 400 after an earlier window: still the first 10,000, each once ({k})"))
    os.environ["MAPSEE_TODAY"] = TODAY

    # ------------------------------------------------- 10. the credit line
    #
    # Licence Ouverte asks for the licensor's name. The credit is the LAST
    # paragraph, which the sync's _cap_prose keeps when it trims long prose to
    # 800; a credit too long for that (> 200) is refused before any request.
    import mapsee_supabase_sync as SYNC
    credit = "Event data: OpenAgenda (openagenda.com), via the Opendatasoft public dataset evenements-publics-openagenda."
    s = src(attribution=credit)
    r = _row("a1", NOW + timedelta(days=1))
    r["longdescription_fr"] = ("Une phrase assez longue pour remplir la description. " * 40).strip()
    ev = ODS.to_event(r, s)
    capped = SYNC._cap_prose(ev.description)
    checks.append((ev.description.endswith("\n\n" + credit), "the credit is the description's last paragraph"))
    checks.append((len(capped) <= SYNC.DESCRIPTION_MAX and capped.endswith("\n\n" + credit),
                   f"...and survives the sync's cut of {len(ev.description)} characters to {len(capped)}"))
    r2 = dict(r); r2.pop("longdescription_fr")
    checks.append((ODS.to_event(r2, s).description == credit, "a row with no prose carries the credit alone"))
    checks.append((ODS.to_event(r, src()).description == r["longdescription_fr"],
                   "a source with no attribution: the description is the source's text, as before"))
    raised = False
    try:
        run(dataset([(0, 5, 10)], extras=False), src(attribution="x" * 201))
    except ValueError:
        raised = True
    checks.append((raised, "a 201-character credit is refused (the sync would cut it off)"))

    # ------------------------------------------------ 11. the real config
    #
    # The budget the windows were enabled on, re-checked against the file and
    # the workflow: at its request bound a windowed source adds at most
    # (max_requests - 1) pages at the 0.84 s a page measured on 2026-11-01..07,
    # on top of the 182 s the whole step took at most. That must stay under half
    # the step's own timeout-minutes.
    cfg = json.load(open("ods_sources.json", encoding="utf-8"))
    sources = cfg["sources"]
    windowed = [x for x in sources if x.get("window")]
    checks.append((bool(sources) and all(ODS._attribution(x) is None or len(ODS._attribution(x)) <= 200
                                         for x in sources), "ods_sources.json loads; every credit fits"))
    fr = next(x for x in sources if x["name"] == "OpenAgenda France")
    checks.append((ODS._window_limits(fr) == (32, 500) and fr["max"] > CAP and ODS._attribution(fr),
                   "OpenAgenda France is windowed (32 windows, 500 requests), its row cap is past 10,000, it is credited"))
    checks.append((all(int(x.get("max", 6000)) > CAP for x in windowed),
                   "every windowed source has a row cap past 10,000 (else the window is pointless)"))
    # The real France entry — its columns, its country filter, its window and
    # its credit — end to end through the fake portal. The fixture rows use
    # the mirror's column names, which is what makes this the real mapping.
    rows = dataset([(0, 29, 15000), (30, 89, 10000)])
    rows.append(_row("gb1", NOW + timedelta(days=2), country="GB"))
    os.environ["MAPSEE_TODAY"] = TODAY
    fake = FakeODS(rows)
    with tempfile.TemporaryDirectory() as d:
        store = EventStore(os.path.join(d, "s.json"))
        with contextlib.redirect_stdout(io.StringIO()):
            k = ODS.ingest_source(store, fake, fr)
        recs = list(store.records.values())
    got = {ref["source_id"] for r in recs for ref in r.get("sources") or []}
    checks.append((got == expected(rows) and fake.refused == 0,
                   f"the real France entry reads all {len(got)} French rows of an over-cap fixture, no GB row, no 400"))
    checks.append((all((r.get("description") or "").endswith("\n\n" + fr["attribution"]) for r in recs),
                   "...and every stored France row ends with its credit"))
    wf = open(os.path.join(".github", "workflows", "aggregate-events.yml"), encoding="utf-8").read()
    m = re.search(r"id: ingest_ods\n(?:.*\n){0,4}?\s+timeout-minutes: (\d+)", wf)
    step_s = int(m.group(1)) * 60 if m else 0
    worst = 182 + sum((ODS._window_limits(x)[1] - 1) * 0.84 for x in windowed)
    checks.append((m is not None and worst <= step_s / 2,
                   f"at its bounds the step is {worst / 60:.1f} min of its {step_s // 60}-minute timeout"))
    # THE SYNC STEP, WHICH IS WHERE THE ROWS COST TIME (a review, 2026-11-08).
    # Every row read here is written by the civic job's "Sync feeds to
    # Supabase (batch-geocoded)" step, and ITS timeout is the limit: cut off
    # there, the step fails, the job turns red and the rest of the day's
    # writes are lost. The worst day is the first windowed run landing ON a
    # Wednesday full refresh: each windowed source's new rows (its max, less
    # what the plain read already kept) prepared and inserted, and every row
    # of a credited source already stored rewritten for its new last
    # paragraph. Worst of each measurement, civic CI logs 2026-10-21..11-07:
    # a 43.7-min Wednesday, 70.8 ms an upserted row, 11.8 ms to prepare one.
    for expr, group, want in (
            ("${{ (matrix.group == 'rec' || matrix.group == 'civic') && 60 || 20 }}", "civic", 60),
            ("${{ (matrix.group == 'rec' || matrix.group == 'civic') && 60 || 20 }}", "ics", 20),
            ("${{ matrix.group == 'civic' && 90 || matrix.group == 'rec' && 60 || 20 }}", "civic", 90),
            ("${{ matrix.group == 'civic' && 90 || matrix.group == 'rec' && 60 || 20 }}", "rec", 60),
            ("45", "civic", 45),
            ("${{ __import__('os') }}", "civic", None)):
        checks.append((_timeout_minutes(expr, group) == want,
                       f"the timeout reader: {expr[:44]}... for {group} is {want}"))
    m = re.search(r"- name: Sync feeds to Supabase \(batch-geocoded\)\n(?:.*\n){0,4}?"
                  r"\s+timeout-minutes: (.+)\n", wf)
    sync_min = _timeout_minutes(m.group(1), "civic") if m else None
    checks.append((sync_min is not None,
                   f"the civic sync step's timeout-minutes is read from the workflow ({sync_min})"))
    new_rows = sum(int(x["max"]) - PLAIN_KEPT.get(x["name"], 0) for x in windowed)
    credited = sum(MEASURED_KEPT.get(x["name"], int(x.get("max", 6000)))
                   for x in sources if ODS._attribution(x))
    weekday = SYNC_ONLY_NEW_MIN + new_rows * (UPSERT_MS + STATE_READ_MS) / 60000
    after = SYNC_WEDNESDAY_MIN + credited * UPSERT_MS / 60000 + new_rows * PREPARE_MS / 60000
    on_wed = after + new_rows * UPSERT_MS / 60000
    fits = int(PLAIN_KEPT["OpenAgenda France"] + max(0.0, (sync_min or 0) - SYNC_WEDNESDAY_MIN
               - credited * UPSERT_MS / 60000) * 60000 / (UPSERT_MS + PREPARE_MS))
    checks.append((sync_min is not None and on_wed <= sync_min,
                   f"the civic sync step at its worst: a first windowed run on a weekday "
                   f"{weekday:.1f} min, the Wednesday after {after:.1f}, ON a Wednesday "
                   f"{on_wed:.1f}, of its {sync_min}-minute timeout ({new_rows:,} new rows, "
                   f"{credited:,} credited; France's max fits up to about {fits:,})"))
    checks.append(("MapseeAggregator/1.0" in ODS.UA and "+https://mapsee.me" in ODS.UA,
                   "the module's own User-Agent is unchanged"))

    failed = 0
    for ok, why in checks:
        failed += 0 if ok else 1
        print(f"{'ok  ' if ok else 'FAIL'}  {why}")
    print(f"\n{len(checks)} cases, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
