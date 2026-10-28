#!/usr/bin/env python3
"""
test_standing_pass.py — every walker still reaches the standing rows once their
stored window stops moving (R1).

WHY. Until ../mapsee 0248 the roller rewrote every listed standing row's
starts_at/ends_at to its current window, about 125-150k events UPDATEs a day
and the database's largest writer (3.2 GB of WAL a day, measured 2026-10-21..
10-27). From 0248 it writes only events_map, and a standing row's stored window
is its ANCHOR, the last window the sync wrote: days or weeks in the past. Every
walker here that found rows by a starts_at window would silently stop seeing
every shop, centre and weekly class, and nothing would say so.

So each one also reads the standing rows by id, through
mapsee_standing.standing_pages: the literal PostgREST predicate of ../mapsee
0247's events_standing_walk_idx,

    recurring_hours=not.is.null&hidden_at=is.null&pin_only=is.false&order=id.asc

plus its own scope filters, continued by id=gt.<last id>, never OFFSET and
never starts_at. Pinned here, against a fake PostgREST that honours every
filter it is sent (eq, is, not.is, in, gt/gte/lt, ilike, cs, or/and, order,
limit, offset), in two worlds:

  STALE  the phase-2 world: every standing row's anchor is days in the past.
         Each walker must still reach the standing rows its rule is about.
  LIVE   the world before 0248: anchors current, so a shut standing row is in
         a window AND in the id walk. No URL probed twice, no row written
         twice: these changes must be harmless before 0248.

And in both: no pin, no private row (where the walker reads public rows only),
no hidden row and no other source's row is ever reached; a claimed row is read
and left alone. The seven walkers of the spec's assertion 1 (A1 menu_links, A2
prune_links, A3 reclassify, A4 retire_online_events, A6 prune_cancelled, A9
retire_openactive_slots, A14 retire_perday_osm) all fail it on the walkers
before this change (3c53953): none of them sends that request.

Prints one line per case, exits non-zero on a failure, like the other gates.
MAPSEE_TODAY is pinned (menu_links takes its week's slice from it).
"""
import argparse
import contextlib
import io
import json
import os
import re
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from urllib.parse import parse_qsl, urlencode
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["MAPSEE_TODAY"] = "20261028"

import mapsee_standing as STD  # noqa: E402

STD.RETRY_PAUSE = 0

FAILS = []
WALK = [("recurring_hours", "not.is.null"), ("hidden_at", "is.null"), ("pin_only", "is.false"),
        ("order", "id.asc")]


def check(label, got, want=True):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {label}" + ("" if ok else f"   got {got!r}, want {want!r}"))
    if not ok:
        FAILS.append(label)


def crashed(label, e):
    print(f"FAIL {label}   crashed: {type(e).__name__}: {str(e)[:160]}")
    FAILS.append(label)


# --------------------------------------------------------------------------- #
# A fake PostgREST over one `events` table. Every request is kept.
# --------------------------------------------------------------------------- #
TS = {"starts_at", "ends_at", "hidden_at", "cancelled_at", "created_at", "claimed_at"}
BOOL = {"is_private", "pin_only"}


def _ts(v):
    dt = datetime.fromisoformat(str(v).strip('"').replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _split(s):
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch == "," and depth == 0:
            out.append(cur)
            cur = ""
            continue
        depth += ch == "("
        depth -= ch == ")"
        cur += ch
    return out + [cur] if cur else out


def _match(row, col, expr):
    neg = expr.startswith("not.")
    if neg:
        expr = expr[4:]
    op, _, raw = expr.partition(".")
    have = row.get(col)
    if op == "is":
        res = (have is None) if raw == "null" else (have is (raw == "true"))
    elif op == "in":
        res = have is not None and str(have) in [x.strip('"') for x in raw.strip("()").split(",")]
    elif op == "ilike":
        res = have is not None and re.fullmatch(re.escape(raw).replace(r"\*", ".*"), str(have),
                                                 re.I | re.S) is not None
    elif op == "cs":
        res = isinstance(have, list) and all(w in have for w in raw.strip("{}").split(","))
    elif have is None:
        res = False
    else:
        hv = _ts(have) if col in TS else have
        wv = _ts(raw) if col in TS else ((raw == "true") if col in BOOL else raw.strip('"'))
        res = {"eq": hv == wv, "neq": hv != wv, "gt": hv > wv, "gte": hv >= wv,
               "lt": hv < wv, "lte": hv <= wv}[op]
    return not res if neg else res


def _term(row, t):
    if t.startswith("or(") and t.endswith(")"):
        return any(_term(row, x) for x in _split(t[3:-1]))
    if t.startswith("and(") and t.endswith(")"):
        return all(_term(row, x) for x in _split(t[4:-1]))
    col, _, expr = t.partition(".")
    return _match(row, col, expr)


def _project(row, select):
    if not select:
        return dict(row)
    out = {}
    for item in _split(select):
        alias, _, src = item.rpartition(":")
        if "->>" in src:
            col, key = src.split("->>")
            v = row.get(col)
            out[alias or key] = (v.get(key) if isinstance(v, dict) else None)
        else:
            out[alias or src] = row.get(src)
    return out


class FakeDB:
    def __init__(self, rows):
        self.rows = {r["id"]: dict(r) for r in rows}
        self.calls = []

    def query(self, method, qs, body=None, prefer=""):
        filters, select, order, limit, offset = [], None, [], None, 0
        for k, v in parse_qsl(qs, keep_blank_values=True):
            if k == "select":
                select = v
            elif k == "order":
                order = [o.split(".") for o in v.split(",")]
            elif k == "limit":
                limit = int(v)
            elif k == "offset":
                offset = int(v)
            elif k in ("or", "and"):
                filters.append(f"{k}{v}")
            else:
                filters.append(f"{k}.{v}")
        hit = [r for r in self.rows.values() if all(_term(r, f) for f in filters)]
        for col, *rest in reversed(order):
            desc = bool(rest) and rest[0] == "desc"
            hit.sort(key=lambda r: (_ts(r[col]) if col in TS else str(r[col])), reverse=desc)
        hit = hit[offset:offset + limit] if limit is not None else hit[offset:]
        if method == "PATCH":
            for r in hit:
                r.update(body)
            if "return=representation" not in (prefer or ""):
                return None
        return [_project(r, select) for r in hit]

    def __call__(self, path, method="GET", body=None, prefer=""):
        self.calls.append((method, path, body))
        table, _, qs = path.partition("?")
        assert table == "events", table
        return self.query(method, qs, body, prefer)

    # the views a test asks of what was sent
    def gets(self):
        return [p for m, p, _ in self.calls if m == "GET"]

    def standing(self):
        return [p for p in self.gets() if "recurring_hours=not.is.null" in p]

    def patches(self):
        return [(p, b) for m, p, b in self.calls if m == "PATCH"]


def pairs(path):
    return parse_qsl(path.partition("?")[2], keep_blank_values=True)


def walk_ok(fdb):
    """Spec assertion 1: a request carrying exactly the walk's four pairs."""
    return any(all(p in pairs(u) for p in WALK) for u in fdb.standing())


def no_window(fdb):
    """Spec assertion 2: no standing request carries offset= or starts_at=."""
    return [u for u in fdb.standing() if "offset=" in u or "starts_at=" in u
            or any(k in ("offset", "starts_at") for k, _ in pairs(u))]


def scoped(fdb, *want):
    """Spec assertion 3: every standing request carries the walker's scope."""
    return bool(fdb.standing()) and all(all(w in pairs(u) for w in want) for u in fdb.standing())


def patched_ids(fdb):
    out = []
    for p, _b in fdb.patches():
        q = dict(pairs(p))
        if "id" in q:
            v = q["id"]
            out += v[4:-1].split(",") if v.startswith("in.(") else [v[3:]]
    return out


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
NOW = time.time()
OSM = "Listing from OpenStreetMap contributors (ODbL). Is this your place?"
OA = "Session data published by Test Pool via OpenActive, licensed CC-BY 4.0."
WEEKLY = "\U0001F501 Runs weekly — 17 sessions scheduled over the next few months, on 1 day a week.\n\n" + OA


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


def uid(hexprefix, n=0):
    return f"{hexprefix}{n:0{8 - len(hexprefix)}x}-0000-4000-8000-000000000000"


def ev(i, *, standing=False, stale=True, title="row", desc="Blurb.", category="community",
       external_source="mapsee", is_private=False, pin_only=False, hidden_at=None, claimed_by=None,
       lat=47.61, lon=-122.33, starts=None, hours=None, now=None):
    """A row. A STANDING row's stored window is its anchor: 10 days back when
    `stale` (after ../mapsee 0248), else shut now and opening in 2 h (before
    it, the roller kept it there)."""
    now = NOW if now is None else now
    if starts is None:
        # A pin's window and a hidden row's are frozen in BOTH worlds: 0239
        # stopped rolling pins, and the roller never rolled a hidden row.
        frozen = stale or pin_only or hidden_at is not None
        starts = (now - 10 * 86400) if (standing and frozen) else (now + 2 * 3600)
    rh = None
    if standing or pin_only:
        rh = hours or ({"tz": "UTC"} if pin_only else
                       {"tz": "America/Los_Angeles", "days": {str(d): ["11:00", "22:00"] for d in range(7)}})
    return {"id": i, "title": title, "description": desc, "category": category, "categories": None,
            "external_source": external_source, "is_private": is_private, "pin_only": pin_only,
            "hidden_at": hidden_at, "cancelled_at": None, "claimed_by": claimed_by, "claimed_at": None,
            "recurring_hours": rh, "starts_at": iso(starts), "ends_at": iso(starts + 8 * 3600),
            "created_at": iso(now - 20 * 86400), "lat": lat, "lon": lon, "place_name": title}


def run_main(mod, argv, fdb, extra=()):
    """mod.main() with sys.argv, the database and the URL faked. -> (code, out)."""
    out = io.StringIO()
    with contextlib.ExitStack() as st:
        st.enter_context(patch.object(mod, "sb", fdb))
        st.enter_context(patch.object(mod, "SUPABASE_URL", "https://db.test"))
        st.enter_context(patch.object(mod, "SERVICE_KEY", "k"))
        st.enter_context(patch.object(sys, "argv", [mod.__name__ + ".py", *argv]))
        for e in extra:
            st.enter_context(e)
        st.enter_context(contextlib.redirect_stdout(out))
        st.enter_context(contextlib.redirect_stderr(out))
        try:
            code = mod.main()
        except SystemExit as e:
            code = e.code
    return code, out.getvalue()


# --------------------------------------------------------------------------- #
print("mapsee_standing: the walk, its bounds and its paging")
# --------------------------------------------------------------------------- #
check("STANDING is the walk index's predicate, literally",
      STD.STANDING, "recurring_hours=not.is.null&hidden_at=is.null&pin_only=is.false")
check("uuid_bound(0, 14) is the bottom of the id space", STD.uuid_bound(0, 14),
      "00000000-0000-0000-0000-000000000000")
bounds = [STD.slice_bounds(k, 14) for k in range(14)]
check("the 14 slices tile the id space: each hi is the next lo, no gap, no overlap",
      all(bounds[k][1] == bounds[k + 1][0] for k in range(13)) and bounds[13][1] is None, True)
check("...in increasing order, the last one open-ended (no id=lt.)",
      [b[0] for b in bounds] == sorted(b[0] for b in bounds) and len({b[0] for b in bounds}) == 14, True)
ints = [uuid.UUID(b[0]).int for b in bounds]
check("...and of equal width, to one unit", max(b - a for a, b in zip(ints, ints[1:] + [1 << 128]))
      - min(b - a for a, b in zip(ints, ints[1:] + [1 << 128])) <= 1, True)
for bad in ("14/14", "-1/14", "3", "a/b", "0/0"):
    try:
        STD.parse_slice(bad)
        check(f"parse_slice refuses {bad!r}", "accepted", "refused")
    except ValueError:
        check(f"parse_slice refuses {bad!r}", "refused", "refused")
check("parse_slice('13/14')", STD.parse_slice("13/14"), (13, 14))

ids = [str(uuid.UUID(int=(k * (1 << 128)) // 2500 + 7)) for k in range(2500)]
rows = [ev(i, standing=True) for i in ids]
rows += [ev(uid("ff", 1), pin_only=True), ev(uid("ff", 2), standing=True, hidden_at=iso(NOW)),
         ev(uid("ff", 3))]                                        # a pin, a hidden row, a dated row
fdb = FakeDB(rows)
got = [r["id"] for page in STD.standing_pages(fdb, "id,title") for r in page]
check("a full walk returns every listed standing row once, in id order, and nothing else",
      got, sorted(ids))
check("three pages: unbounded, then id=gt.<last id of the page before>",
      [dict(pairs(u)).get("id") for u in fdb.gets()], [None, f"gt.{sorted(ids)[999]}", f"gt.{sorted(ids)[1999]}"])
check("every page is the literal walk, ordered by id, limit 1000, never offset or starts_at",
      all(u.startswith("events?select=id,title&" + STD.STANDING + "&order=id.asc&limit=1000")
          for u in fdb.gets()) and not no_window(fdb), True)
lo, hi = STD.slice_bounds(5, 14)
fdb = FakeDB(rows)
got = [r["id"] for page in STD.standing_pages(fdb, "id", lo=lo, hi=hi, page=50) for r in page]
check("a slice reads exactly the ids in [lo, hi)", got, [i for i in sorted(ids) if lo <= i < hi])
check("...lo inclusive on the first page only, hi on every page",
      [("id", f"gte.{lo}") in pairs(u) for u in fdb.gets()][:2] == [True, False]
      and all(("id", f"lt.{hi}") in pairs(u) for u in fdb.gets()), True)
for bad_scope in ("&starts_at=gte.2026-01-01", "&offset=500", "&hidden_at=not.is.null", "&or=(starts_at.lt.x)",
                  "is_private=eq.false"):
    try:
        list(STD.standing_pages(FakeDB([]), "id", scope=bad_scope))
        check(f"a scope may not window, page or re-state the walk: {bad_scope}", "accepted", "refused")
    except ValueError:
        check(f"a scope may not window, page or re-state the walk: {bad_scope}", "refused", "refused")


class Flaky(FakeDB):
    def __init__(self, rows, fail_on):
        super().__init__(rows)
        self.fail_on, self.n = fail_on, 0

    def __call__(self, path, method="GET", body=None, prefer=""):
        self.n += 1
        if self.n in self.fail_on:
            self.calls.append((method, path, body))
            raise RuntimeError("HTTP 500")
        return super().__call__(path, method, body, prefer)


fl = Flaky(rows, {2})
got = [r["id"] for page in STD.standing_pages(fl, "id") for r in page]
check("a page that fails once is asked again, the same page, and the walk completes",
      (len(got), fl.gets()[1] == fl.gets()[2]), (2500, True))
fl = Flaky(rows, {2, 3, 4})
seen = []
try:
    for page in STD.standing_pages(fl, "id"):
        seen += page
    check("three failures of one page raise StandingWalkError", "returned", "raised")
except STD.StandingWalkError as e:
    check("three failures of one page raise StandingWalkError, saying how far it got",
          (e.rows, len(seen)), (1000, 1000))


class Stuck(FakeDB):
    def __call__(self, path, method="GET", body=None, prefer=""):
        self.calls.append((method, path, body))
        if len(self.calls) > 5:
            raise AssertionError("still asking")
        return [{"id": uid("10")}] * 3


try:
    list(STD.standing_pages(Stuck([]), "id", page=3))
    check("a server ignoring the cursor ends the walk with an error, not a loop", "returned", "raised")
except STD.StandingWalkError:
    check("a server ignoring the cursor ends the walk with an error, not a loop", "raised", "raised")


# --------------------------------------------------------------------------- #
print()
print("A14 mapsee_retire_perday_osm: the replacements are read by id")
# --------------------------------------------------------------------------- #
try:
    import mapsee_retire_perday_osm as RP
    A = uid("20")
    world = [ev(A, standing=True, title="Pho Corner", desc="Pho.\n\n" + OSM, category="food"),
             ev(uid("21"), standing=True, title="Pho Pin", desc=OSM, pin_only=True, lat=47.7),
             ev(uid("22"), standing=True, title="Hidden Pho", desc=OSM, hidden_at=iso(NOW - 86400), lat=47.8),
             ev(uid("30"), title="Pho Corner", desc="Pho.\n\n" + OSM, category="food", starts=NOW + 86400),
             ev(uid("31"), title="Pho Pin", desc=OSM, lat=47.7, starts=NOW + 86400),
             ev(uid("32"), title="Hidden Pho", desc=OSM, lat=47.8, starts=NOW + 86400)]
    fdb = FakeDB(world)
    code, out = run_main(RP, ["--days", "3"], fdb)
    check("A14: the walk request (assertion 1)", walk_ok(fdb))
    check("A14: no offset, no starts_at in it (assertion 2)", no_window(fdb), [])
    check("A14: its scope, external_source=eq.mapsee (assertion 3)", scoped(fdb, ("external_source", "eq.mapsee")))
    check("A14: a stale anchor still replaces its venue's per-day row; a pin or a hidden row never does",
          "old rows to hide                   : 1" in out, True)
except Exception as e:                                       # noqa: BLE001
    crashed("A14", e)


# --------------------------------------------------------------------------- #
print()
print("A1 mapsee_menu_links: half the budget to what is coming up, half to the places that stand")
# --------------------------------------------------------------------------- #
try:
    import mapsee_menu_links as ML
    PIN_NOW = datetime(2026, 10, 28, 12, tzinfo=timezone.utc).timestamp()
    WEEK_LO = STD.uuid_bound(datetime(2026, 10, 28).isocalendar()[1] % 53, 53)

    def menu_world(stale):
        site = "Tickets / info: https://{}.test/"
        return [
            ev(uid("e0"), standing=True, stale=stale, title="Pho Corner", category="food",
               desc="Pho.\n\n" + site.format("pho"), now=PIN_NOW, lat=47.61),
            ev(uid("e1"), standing=True, stale=stale, title="Pin Cafe", category="food", pin_only=True,
               desc=site.format("pin"), now=PIN_NOW, lat=47.62),
            ev(uid("e2"), standing=True, stale=stale, title="Private Cafe", category="food", is_private=True,
               desc=site.format("private"), now=PIN_NOW, lat=47.63),
            ev(uid("e3"), standing=True, stale=stale, title="Hidden Cafe", category="food",
               hidden_at=iso(PIN_NOW - 86400), desc=site.format("hidden"), now=PIN_NOW, lat=47.64),
            ev(uid("e4"), standing=True, stale=stale, title="Yoga", category="fitness",
               desc=site.format("yoga"), now=PIN_NOW, lat=47.65),
        ]

    def menu_run(world, argv=("--limit", "10")):
        fdb = FakeDB(world)
        fetched = []

        def fake_fetch(url, timeout=15):
            fetched.append(url)
            return f'<a href="https://order.toasttab.com/online/{url.split("//")[1].split(".")[0]}">Order</a>', url
        code, out = run_main(ML, list(argv), fdb, [patch.object(ML, "fetch", fake_fetch),
                                                   patch.object(ML.time, "sleep", lambda s: None)])
        return fdb, fetched, out

    fdb, fetched, out = menu_run(menu_world(True))
    check("A1: the walk request (assertion 1)", walk_ok(fdb))
    check("A1: no offset, no starts_at in it (assertion 2)", no_window(fdb), [])
    check("A1: its scope, category=eq.food and is_private=eq.false (assertion 3)",
          scoped(fdb, ("category", "eq.food"), ("is_private", "eq.false")))
    check("A1 STALE: the standing food venue is examined and gets its order line",
          (fetched, [i for i in patched_ids(fdb)]), (["https://pho.test/"], [uid("e0")]))
    check("A1: no pin, private, hidden or other-category row is fetched or written",
          not any(h in u for u in fetched for h in ("pin", "private", "hidden", "yoga")), True)
    check("A1: this ISO week's slice of the id space is where the walk starts, then it wraps",
          [dict(pairs(u)).get("id") for u in fdb.standing()], [f"gte.{WEEK_LO}", f"lt.{WEEK_LO}"])
    fdb, fetched, out = menu_run(menu_world(False))
    check("A1 LIVE: a shut venue the dated read also returns is examined once and written once",
          (fetched, patched_ids(fdb)), (["https://pho.test/"], [uid("e0")]))

    # Assertion 5: the budget. 30 dated and 30 standing food rows, --limit 10.
    many = ([ev(uid("a", n), title=f"Dated {n}", category="food", starts=PIN_NOW + 3600 * (n + 1),
                desc=f"Tickets / info: https://d{n}.test/", lat=40 + n / 100, now=PIN_NOW) for n in range(30)]
            + [ev(uid("f", n), standing=True, title=f"Stand {n}", category="food",
                  desc=f"Tickets / info: https://s{n}.test/", lat=50 + n / 100, now=PIN_NOW) for n in range(30)])
    fdb = FakeDB(many)
    with patch.object(ML, "sb", fdb):
        got = ML.read_candidates(10)
    served = sum(len(fdb.query("GET", u.partition("?")[2])) for u in fdb.gets())
    check("A1: the total row budget is unchanged: 10 rows read, 5 dated and 5 standing (assertion 5)",
          (len(got), served, sum(1 for r in got if r["id"].startswith("a")),
           [dict(pairs(u)).get("limit") for u in fdb.gets()]), (10, 10, 5, ["5", "5"]))
    fdb = FakeDB(many[:2] + many[30:])
    with patch.object(ML, "sb", fdb):
        got = ML.read_candidates(10)
    check("A1: a short dated half leaves its share to the standing half",
          (len(got), [dict(pairs(u)).get("limit") for u in fdb.gets()]), (10, ["5", "8"]))
except Exception as e:                                       # noqa: BLE001
    crashed("A1", e)


# --------------------------------------------------------------------------- #
# The shared world for A2, A3, A4 and A6.
# --------------------------------------------------------------------------- #
DEAD = "https://order.dead.test/pho"


def shared_world(stale):
    """S_A a standing restaurant whose order link has died; S_C a standing
    online-only class; S_D yoga filed under food; and the rows no walker may
    reach (a pin, a private row, a hidden row, another source's row) or may
    read but must leave (a claimed row), each carrying every bait at once."""
    bait = (f"Pho.\n\n\U0001F6D2 Order: {DEAD}\n\nTickets / info: {DEAD}\n\n"
            "This live online session is only available through Our Parks Plus. via OpenActive")
    return [
        ev(uid("40"), standing=True, stale=stale, title="Pho Corner", category="food",
           desc=f"Pho.\n\n\U0001F6D2 Order: {DEAD}\n\nTickets / info: {DEAD}\n\n{OSM}"),
        ev(uid("41"), standing=True, stale=stale, title="Our Parks Live Yoga", category="fitness",
           desc="This live online session is only available through Our Parks Plus.\n\n" + OA,
           lat=51.5, lon=-0.12),
        ev(uid("42"), standing=True, stale=stale, title="Gentle Morning Hatha Yoga", category="food",
           desc="Slow stretches to start the day."),
        ev(uid("50"), standing=True, stale=stale, title="Pin", category="food", desc=bait, pin_only=True),
        ev(uid("51"), standing=True, stale=stale, title="Private", category="food", desc=bait, is_private=True),
        ev(uid("52"), standing=True, stale=stale, title="Hidden", category="food", desc=bait,
           hidden_at=iso(NOW - 3 * 86400)),
        ev(uid("53"), standing=True, stale=stale, title="Community", category="food", desc=bait,
           external_source=None),
        ev(uid("54"), standing=True, stale=stale, title="Claimed", category="food", desc=bait,
           claimed_by=uid("99")),
        ev(uid("60"), title="Gig tonight", desc="Tickets / info: https://tix.test/e/1", starts=NOW + 3 * 3600),
    ]


NEVER = {uid("50"), uid("51"), uid("52"), uid("53"), uid("54")}


def touched_never(fdb):
    return sorted(set(patched_ids(fdb)) & NEVER)


# --------------------------------------------------------------------------- #
print()
print("A2 mapsee_prune_links: a dead order link on a standing row is still taken back")
# --------------------------------------------------------------------------- #
try:
    import mapsee_prune_links as PL

    def links_run(world, argv=("--apply",)):
        fdb = FakeDB(world)
        probed = []

        def verdict(u):
            probed.append(u)
            return "dead" if u == DEAD else "alive"
        code, out = run_main(PL, list(argv), fdb, [patch.object(PL, "destination_verdict", verdict),
                                                   patch.object(PL.time, "sleep", lambda s: None)])
        return fdb, probed, out

    fdb, probed, out = links_run(shared_world(True))
    check("A2: the walk request (assertion 1)", walk_ok(fdb))
    check("A2: no offset, no starts_at in it (assertion 2)", no_window(fdb), [])
    check("A2: its scope, external_source=eq.mapsee and is_private=eq.false (assertion 3)",
          scoped(fdb, ("external_source", "eq.mapsee"), ("is_private", "eq.false")))
    check("A2 STALE: the standing row's dead line is probed and rewritten",
          (probed, patched_ids(fdb)), ([DEAD], [uid("40")]))
    check("A2: no pin, private, hidden, other-source or claimed row is written", touched_never(fdb), [])
    fdb, probed, out = links_run(shared_world(False))
    check("A2 LIVE: in a window and in the walk, the URL is probed once and the row written once",
          (probed, patched_ids(fdb)), ([DEAD], [uid("40")]))
except Exception as e:                                       # noqa: BLE001
    crashed("A2", e)


# --------------------------------------------------------------------------- #
print()
print("A3 mapsee_reclassify: a classifier fix still reaches the standing rows")
# --------------------------------------------------------------------------- #
try:
    import mapsee_reclassify as RC
    yoga = shared_world(True)[2]
    p_, e_ = RC.recompute(yoga)
    move = RC.transition("food", [], p_, e_)

    def reclass_run(world, argv):
        fdb = FakeDB(world)
        code, out = run_main(RC, argv, fdb, [patch.object(RC, "read_hidden_copies", lambda *a, **k: {})])
        return fdb, code, out

    fdb, code, out = reclass_run(shared_world(True), ["--apply", "--allow", move.replace(" ", "")])
    check(f"A3: the fixture recomputes ({move!r})", bool(move) and p_ != "food", True)
    check("A3: the walk request (assertion 1)", walk_ok(fdb))
    check("A3: no offset, no starts_at in it (assertion 2)", no_window(fdb), [])
    check("A3: its scope, external_source=eq.mapsee and is_private=eq.false (assertion 3)",
          scoped(fdb, ("external_source", "eq.mapsee"), ("is_private", "eq.false")))
    check("A3 STALE: the standing yoga row is recomputed and written", patched_ids(fdb), [uid("42")])
    check("A3: no pin, private, hidden, other-source or claimed row is written", touched_never(fdb), [])
    fdb, code, out = reclass_run(shared_world(False), ["--apply", "--allow", move.replace(" ", "")])
    check("A3 LIVE: in a window and in the walk, it is written once", patched_ids(fdb), [uid("42")])
    fdb, code, out = reclass_run(shared_world(True), ["--only", "food"])
    check("A3: --only adds its category or= to the walk, beside the scope",
          scoped(fdb, ("external_source", "eq.mapsee"), ("or", "(category.eq.food,categories.cs.{food})")))
except Exception as e:                                       # noqa: BLE001
    crashed("A3", e)


# --------------------------------------------------------------------------- #
print()
print("A4 mapsee_retire_online_events: forward by id too; --unhide unchanged")
# --------------------------------------------------------------------------- #
try:
    import mapsee_retire_online_events as RO

    def online_run(world, argv):
        fdb = FakeDB(world)
        code, out = run_main(RO, argv, fdb)
        return fdb, code, out

    fdb, code, out = online_run(shared_world(True), ["--apply"])
    check("A4: the walk request in the forward pass (assertion 1)", walk_ok(fdb))
    check("A4: no offset, no starts_at in it (assertion 2)", no_window(fdb), [])
    check("A4: its scope, external_source=eq.mapsee and is_private=eq.false (assertion 3)",
          scoped(fdb, ("external_source", "eq.mapsee"), ("is_private", "eq.false")))
    check("A4 STALE: the standing online-only class is hidden", patched_ids(fdb), [uid("41")])
    check("A4: no pin, private, hidden, other-source or claimed row is written", touched_never(fdb), [])
    fdb, code, out = online_run(shared_world(False), ["--apply"])
    check("A4 LIVE --apply: hidden once (the window hid it; the walk's hidden_at=is.null leaves it out)",
          patched_ids(fdb), [uid("41")])
    fdb, code, out = online_run(shared_world(False), [])
    check("A4 LIVE dry run: counted once, in a window and in the walk", "1 to hide" in out, True)
    world = shared_world(True)
    for r in world:
        if r["id"] == uid("41"):
            r["hidden_at"] = iso(NOW - 3600)
    fdb, code, out = online_run(world, ["--unhide"])
    check("A4 --unhide sends no standing request (assertion 7)", fdb.standing(), [])
except Exception as e:                                       # noqa: BLE001
    crashed("A4", e)


# --------------------------------------------------------------------------- #
print()
print("A5 mapsee_uncancel: reads cancelled (hidden) rows by its own rule, no standing request")
# --------------------------------------------------------------------------- #
try:
    import mapsee_uncancel as UC
    fdb = FakeDB(shared_world(True))
    with contextlib.redirect_stdout(io.StringIO()):
        UC.run(UC.parse_args(["--since", "2026-10-01"]), sb=fdb, out=lambda *a: None)
    check("A5 sends no standing request (assertion 7)", (bool(fdb.gets()), fdb.standing()), (True, []))
except Exception as e:                                       # noqa: BLE001
    crashed("A5", e)


# --------------------------------------------------------------------------- #
print()
print("A6 mapsee_prune_cancelled: one id slice of the standing rows a scheduled run")
# --------------------------------------------------------------------------- #
try:
    import mapsee_prune_cancelled as PC

    class _Robots:
        def __init__(self, *a, **k):
            pass

        def check(self, url):
            return {"allowed": True, "crawl_delay": None}

    def prune_run(world, *argv):
        fdb = FakeDB(world)
        probed = []

        def verdict(u, robots=None, **k):
            probed.append(u)
            return "gone" if u in (DEAD, "https://tix.test/e/1") else "live"
        code, out = run_main(PC, ["--delay", "0", *argv], fdb,
                             [patch.object(PC.robots_txt, "Robots", _Robots),
                              patch.object(PC, "cancellation_verdict", verdict)])
        return fdb, probed, code, out

    fdb, probed, code, out = prune_run(shared_world(True), "--days", "2", "--standing-slice", "0/1", "--apply")
    check("A6: the walk request (assertion 1)", walk_ok(fdb))
    check("A6: no offset, no starts_at in it (assertion 2)", no_window(fdb), [])
    check("A6: its scope: external_source, is_private and both claimed columns (assertion 3)",
          scoped(fdb, ("external_source", "eq.mapsee"), ("is_private", "eq.false"),
                 ("claimed_by", "is.null"), ("claimed_at", "is.null")))
    check("A6 STALE: the standing row's listing is probed and the row hidden, beside tonight's gig",
          (sorted(probed), sorted(patched_ids(fdb))), (sorted([DEAD, "https://tix.test/e/1"]),
                                                       sorted([uid("40"), uid("60")])))
    check("A6: no pin, private, hidden, other-source or claimed row is probed or hidden",
          (touched_never(fdb), [u for u in probed if u not in (DEAD, "https://tix.test/e/1")]), ([], []))
    check("A6: every hide still carries LISTED and UNCLAIMED",
          all(PC.LISTED in p and PC.UNCLAIMED in p for p, _ in fdb.patches()), True)
    fdb, probed, code, out = prune_run(shared_world(False), "--days", "2", "--standing-slice", "0/1", "--apply")
    check("A6 LIVE: a shut standing row in its window and its slice is probed once and hidden once",
          (sorted(probed), sorted(patched_ids(fdb))), (sorted([DEAD, "https://tix.test/e/1"]),
                                                       sorted([uid("40"), uid("60")])))
    fdb, probed, code, out = prune_run(shared_world(True), "--days", "2", "--apply")
    check("A6: no --standing-slice (a dispatch run), no standing request", fdb.standing(), [])
    fdb, probed, code, out = prune_run(shared_world(True), "--days", "2", "--standing-slice", "13/14")
    last = fdb.standing()
    check("A6: slice 13/14 starts at its bound and has no id=lt. (assertion 4)",
          bool(last) and all(not any(k == "id" and v.startswith("lt.") for k, v in pairs(u)) for u in last)
          and ("id", f"gte.{STD.uuid_bound(13, 14)}") in pairs(last[0]), True)
    fdb, probed, code, out = prune_run(shared_world(True), "--days", "2", "--standing-slice", "0/14")
    check("A6: slice 0/14 is [0, 1/14): id=gte.00000000-... and id=lt.<the next bound>",
          [(k, v) for k, v in pairs(fdb.standing()[0]) if k == "id"],
          [("id", "gte.00000000-0000-0000-0000-000000000000"), ("id", f"lt.{STD.uuid_bound(1, 14)}")])
    check("A6: ...and the standing row at 40000000-... is in slice 3/14, not 0/14", DEAD in probed, False)
    tmp = tempfile.mkdtemp()
    idx = os.path.join(tmp, "prune_url_index.json")
    world = shared_world(True) + [ev(uid("41", 1), standing=True, title="Pho Corner 2",
                                     desc=f"Tickets / info: {DEAD}")]
    fdb, probed, code, out = prune_run(world, "--write-url-index", idx, "--standing-slice", "0/1")
    written = json.load(open(idx, encoding="utf-8"))
    check("A6: standing rows never enter the far-row URL index",
          [i for v in written["urls"].values() for i in v if i in (uid("40"), uid("41", 1))], [])
    class _NoWalk(FakeDB):
        def __call__(self, path, method="GET", body=None, prefer=""):
            if "recurring_hours=not.is.null" in path:
                self.calls.append((method, path, body))
                raise RuntimeError("HTTP 500")
            return super().__call__(path, method, body, prefer)

    nowalk = _NoWalk(shared_world(True))
    probed = []
    code, out = run_main(PC, ["--delay", "0", "--days", "2", "--standing-slice", "0/1", "--apply"], nowalk,
                         [patch.object(PC.robots_txt, "Robots", _Robots),
                          patch.object(PC, "cancellation_verdict",
                                       lambda u, robots=None, **k: probed.append(u) or "gone")])
    check("A6: a slice that cannot be read is said aloud, and the dated run goes on as before",
          (code, "INCOMPLETE: the standing slice 0/1" in out, probed, patched_ids(nowalk)),
          (0, True, ["https://tix.test/e/1"], [uid("60")]))
    # The probe queue: the slice's own URLs take every other place from the front.
    r_d = [{"id": f"d{n}", "starts_at": iso(NOW + 3600 * (n + 1))} for n in range(4)]
    r_s = [{"id": f"s{n}", "starts_at": iso(NOW - 86400 * (10 - n))} for n in range(4)]
    by_url = {f"https://d.test/{n}": [r_d[n]] for n in range(4)}
    by_url.update({f"https://s.test/{n}": [r_s[n]] for n in range(4)})
    order, _ = PC.probe_order(by_url, {f"https://s.test/{n}" for n in range(4)})
    check("A6: probe_order alternates slice URLs and the soonest dated URLs, from the front",
          [u.split("//")[1][0] for u in order], list("sdsdsdsd"))
    order, _ = PC.probe_order(by_url, set())
    check("A6: without a slice the queue is the soonest-first queue it always was",
          order, sorted(by_url, key=lambda u: (min(r["starts_at"] for r in by_url[u]), u)))
except Exception as e:                                       # noqa: BLE001
    crashed("A6", e)


# --------------------------------------------------------------------------- #
print()
print("A9 mapsee_retire_openactive_slots: the weekly keepers are read by id")
# --------------------------------------------------------------------------- #
try:
    import mapsee_retire_openactive_slots as RS
    LON = ZoneInfo("Europe/London")

    def next_mondays(n):
        d = datetime.now(LON).date() + timedelta(days=1)
        out = []
        while len(out) < n:
            if d.weekday() == 0:
                out.append(datetime(d.year, d.month, d.day, 19, 40, tzinfo=LON))
            d += timedelta(days=1)
        return out

    def oa_world(stale):
        keeper = ev(uid("70"), standing=True, stale=stale, title="Aqua Aerobics", desc=WEEKLY,
                    lat=51.5, lon=-0.12, hours={"tz": "Europe/London", "days": {"0": [["19:40", "20:40"]]}})
        dated = [ev(uid("71", n), title="Aqua Aerobics", desc=OA, lat=51.5, lon=-0.12,
                    starts=m.timestamp()) for n, m in enumerate(next_mondays(2))]
        pin = ev(uid("72"), standing=True, stale=stale, title="Aqua Aerobics", desc=WEEKLY, pin_only=True,
                 lat=51.6, lon=-0.12, hours={"tz": "Europe/London", "days": {"0": [["19:40", "20:40"]]}})
        orphan = [ev(uid("73", n), title="Aqua Aerobics", desc=OA, lat=51.6, lon=-0.12,
                     starts=m.timestamp()) for n, m in enumerate(next_mondays(2))]
        return [keeper, pin] + dated + orphan

    def oa_run(world, argv):
        fdb = FakeDB(world)
        out = io.StringIO()
        with patch.object(RS, "sb", fdb), patch.object(RS, "SUPABASE_URL", "https://db.test"), \
                patch.object(RS, "SERVICE_KEY", "k"), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(out):
            code = RS.main(argv)
        return fdb, code, out.getvalue()

    fdb, code, out = oa_run(oa_world(True), ["--apply"])
    check("A9: the walk request (assertion 1)", walk_ok(fdb))
    check("A9: no offset, no starts_at in it (assertion 2)", no_window(fdb), [])
    check("A9: its scope, external_source=eq.mapsee and claimed_by=is.null (assertion 3)",
          scoped(fdb, ("external_source", "eq.mapsee"), ("claimed_by", "is.null")))
    check("A9 STALE: a keeper whose anchor is days old still retires the dated rows it speaks for",
          sorted(patched_ids(fdb)), sorted([uid("71", 0), uid("71", 1)]))
    check("A9: the keeper itself is never hidden, and a pin is never a keeper",
          [i for i in patched_ids(fdb) if i in (uid("70"), uid("72"), uid("73", 0), uid("73", 1))], [])
    after = list(fdb.rows.values())
    fdb2 = FakeDB(after)
    out = io.StringIO()
    with patch.object(RS, "sb", fdb2), patch.object(RS, "SUPABASE_URL", "https://db.test"), \
            patch.object(RS, "SERVICE_KEY", "k"), contextlib.redirect_stdout(out):
        RS.main(["--apply", "--unhide"])
    check("A9 STALE --unhide: the keeper is found by id, so the reversal reaches the same rows",
          (sorted(patched_ids(fdb2)), [b for _p, b in fdb2.patches()][:1]),
          (sorted([uid("71", 0), uid("71", 1)]), [{"hidden_at": None}]))
    fdb, code, out = oa_run(oa_world(False), ["--apply"])
    check("A9 LIVE: in a window and in the walk, the same rows, each hidden once",
          sorted(patched_ids(fdb)), sorted([uid("71", 0), uid("71", 1)]))
except Exception as e:                                       # noqa: BLE001
    crashed("A9", e)


# --------------------------------------------------------------------------- #
print()
print("A10 mapsee_indexnow: a standing row is announced whatever its stored start")
# --------------------------------------------------------------------------- #
try:
    import mapsee_indexnow as IX

    class _Resp:
        def __init__(self, rows):
            self.status_code, self._rows, self.text = 200, rows, ""

        def json(self):
            return self._rows

    class _Session:
        def __init__(self, fdb):
            self.fdb, self.params = fdb, []

        def get(self, url, params=None, headers=None, timeout=None):
            self.params.append(dict(params))
            qs = urlencode(params)
            return _Resp(self.fdb.query("GET", qs))

    fresh = NOW - 3600
    world = [dict(ev(uid("80"), standing=True, title="New shop"), created_at=iso(fresh)),
             dict(ev(uid("81"), title="Over already", starts=NOW - 7200), created_at=iso(fresh)),
             dict(ev(uid("82"), title="Next week", starts=NOW + 7 * 86400), created_at=iso(fresh)),
             dict(ev(uid("83"), standing=True, title="New pin", pin_only=True), created_at=iso(fresh)),
             dict(ev(uid("84"), standing=True, title="New private", is_private=True), created_at=iso(fresh))]
    sess = _Session(FakeDB(world))
    got, complete = IX.fetch_new_event_ids("https://db.test", "k", 26, sess)
    check("A10: the stale standing row and the upcoming dated one; not the past one, a pin or a private row"
          " (assertion 6)", sorted(got), sorted([uid("80"), uid("82")]))
    check("A10: the request keeps its created_at range and carries no starts_at and no or=",
          all("created_at" in p and "starts_at" not in p and "or" not in p for p in sess.params[:1]), True)
except Exception as e:                                       # noqa: BLE001
    crashed("A10", e)


print()
if FAILS:
    print(f"{len(FAILS)} FAILED: " + "; ".join(FAILS))
    sys.exit(1)
print("all standing-pass checks passed")
