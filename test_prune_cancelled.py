#!/usr/bin/env python3
"""
test_prune_cancelled.py — how mapsee_prune_cancelled READS the table and what a
listing found off is allowed to reach.

Prints one line per case and exits non-zero, like the other gate scripts. The
evidence rules (what counts as cancelled, what is a refusal) are pinned in
test_cancelled_events.py; this file pins the two things around them:

  THE COLLECT pages by keyset, never by OFFSET. Every run 2026-10-20..10-26,
  the 16:55 --days 2 ones included, lost its first two-day window to HTTP 500
  at OFFSET 37.5k-49.5k. A keyset cursor that splits one starts_at instant
  across a page boundary must lose and repeat nothing.

  THE URL INDEX. The Sunday sweep writes {listing: far row ids}; a near run
  that finds a listing off by the same evidence also hides those ids, read
  back first. A live listing, a refusal, a claimed or re-linked or already
  hidden row, and a missing, unreadable or stale file must all leave the run
  exactly as it was before the index existed.

All against a fake PostgREST (sb() patched): no network, no database.
"""
import argparse
import contextlib
import io
import json
import os
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from urllib.parse import parse_qsl

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mapsee_prune_cancelled as PRUNE

FAILS = []


def check(label, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {label}" + ("" if ok else f"   got {got!r}, want {want!r}"))
    if not ok:
        FAILS.append(label)


# --------------------------------------------------------------------------- #
# A fake PostgREST over a list of rows: eq / is / in / gt / gte / lt filters,
# and=(...) with a nested or(...), order, limit and (so the old code can be run
# against it) offset. Every request is kept, so a test can ask what was SENT.
# --------------------------------------------------------------------------- #
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


def _ts(v):
    return datetime.fromisoformat(str(v).replace("Z", "+00:00"))


def _val(col, raw):
    if col in ("starts_at", "hidden_at"):
        return _ts(raw)
    if col == "is_private":
        return raw == "true"
    return raw


def _cmp(row, col, op, raw):
    have = row.get(col)
    if op == "is":
        return have is None if raw == "null" else have is (raw == "true")
    if op == "in":
        return str(have) in raw.strip("()").split(",")
    if have is None:
        return False
    have = _val(col, have) if col in ("starts_at", "hidden_at") else have
    want = _val(col, raw)
    return {"eq": have == want, "gt": have > want, "gte": have >= want, "lt": have < want,
            "lte": have <= want}[op]


def _term(row, term):
    if term.startswith("or(") and term.endswith(")"):
        return any(_term(row, t) for t in _split(term[3:-1]))
    if term.startswith("and(") and term.endswith(")"):
        return all(_term(row, t) for t in _split(term[4:-1]))
    col, op, raw = term.split(".", 2)
    return _cmp(row, col, op, raw)


class FakeDB:
    def __init__(self, rows):
        self.rows = {r["id"]: dict(r) for r in rows}
        self.calls = []

    def __call__(self, path, method="GET", body=None, prefer=""):
        self.calls.append((method, path, body, prefer))
        table, _, qs = path.partition("?")
        assert table == "events", table
        filters, select, order, limit, offset = [], None, [], None, 0
        for k, v in parse_qsl(qs, keep_blank_values=True):
            if k == "select":
                select = v.split(",")
            elif k == "order":
                order = [o.split(".") for o in v.split(",")]
            elif k == "limit":
                limit = int(v)
            elif k == "offset":
                offset = int(v)
            elif k == "and":
                filters.append(f"and{v}")
            else:
                filters.append(f"{k}.{v}")
        hit = [r for r in self.rows.values() if all(_term(r, f) for f in filters)]
        for col, _dir in reversed(order):
            hit.sort(key=lambda r: _ts(r[col]) if col == "starts_at" else str(r[col]))
        hit = hit[offset:offset + limit] if limit else hit[offset:]
        if method == "PATCH":
            for r in hit:
                r.update(body)
            if "return=representation" not in prefer:
                return None
        return [{c: r.get(c) for c in select} if select else dict(r) for r in hit]


NOW = time.time()


def at(days, frac=0.0):
    """A starts_at the way PostgREST writes it, `days` from now (whole second)."""
    dt = datetime.fromtimestamp(int(NOW) + days * 86400, timezone.utc) + timedelta(seconds=frac)
    return dt.isoformat()


def uid(n):
    return str(uuid.UUID(int=n))


def row(n, days, url=None, frac=0.0, **kw):
    r = {"id": uid(n), "title": f"row {n}", "starts_at": at(days, frac), "external_source": "mapsee",
         "is_private": False, "hidden_at": None, "claimed_by": None, "claimed_at": None,
         "description": f"Blurb.\n\nTickets / info: {url}" if url else "Blurb."}
    r.update(kw)
    return r


def put(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def collected_ids(rows_by_url):
    return sorted(r["id"] for rows in rows_by_url.values() for r in rows)


# --------------------------------------------------------------------------- #
print("collect: a keyset cursor, never an OFFSET, and nothing lost at a page boundary")
# Seven rows at ONE instant, then two at the same second plus 0.25 s (PostgREST
# writes the fraction, and the cursor must keep it), on a page size of 3: two
# page boundaries fall inside the instant. Window 2 holds four more.
rows = ([row(n, 1, f"https://x.test/e/{n}") for n in range(10, 17)]
        + [row(n, 1, f"https://x.test/e/{n}", frac=0.25) for n in (20, 21)]
        + [row(30, 1.5, "https://x.test/e/30")]
        + [row(n, 2 + n % 10 / 5, f"https://x.test/e/{n}") for n in (40, 41, 42, 43)])
eligible = sorted(r["id"] for r in rows)
rows += [row(50, 5, "https://x.test/e/50"),                       # beyond --days 4
         row(51, -0.2, "https://x.test/e/51"),                    # already started
         row(52, 1, "https://x.test/e/52", hidden_at=at(-3)),     # hidden: never listed
         row(53, 1, "https://x.test/e/53", external_source=None), # a community event
         row(54, 1, "https://x.test/e/54", is_private=True),
         row(55, 1.2, "https://x.test/e/55", claimed_by=uid(9001)), # listed, then skipped
         row(56, 1.3)]                                              # listed, nothing to probe
db = FakeDB(rows)
with patch.object(PRUNE, "sb", db), patch.object(PRUNE, "PAGE", 3):
    scanned, by_url, skipped = PRUNE.collect(argparse.Namespace(days=4, back=0, max_pages=90))
got = collected_ids(by_url)
check("every eligible row is collected exactly once across two boundaries inside one instant",
      got, eligible)
check("...and nothing else (started, beyond the horizon, hidden, community, private, claimed)",
      sorted(set(got) - set(eligible)), [])
check("scanned counts the claimed row and the row with no link as read", scanned, len(eligible) + 2)
check("no window was skipped", skipped, [])
listing = [p for m, p, _b, _pr in db.calls if m == "GET"]
check("no request carries offset=", [p for p in listing if "offset=" in p], [])
check("no request carries count=", [p for p in listing if "count=" in p], [])
check("every request orders starts_at, id and carries the three filters",
      all("order=starts_at.asc,id.asc" in p and PRUNE.LISTED in p for p in listing), True)
check("a cursor page names the last row's instant with its fraction (.250000Z), '+' never bare",
      any("or(starts_at.gt." in p and "250000Z" in p for p in listing) and not any("+" in p for p in listing),
      True)


class _Stuck(FakeDB):
    """A server that ignores the cursor: the same full page every time (and,
    so a missing guard fails here instead of hanging the gate, an error from
    the 10th request on)."""
    def __call__(self, path, method="GET", body=None, prefer=""):
        self.calls.append((method, path, body, prefer))
        if len(self.calls) >= 10:
            raise RuntimeError("still asking")
        return [dict(r) for r in list(self.rows.values())[:3]]


stuck = _Stuck([row(n, 1, f"https://x.test/e/{n}") for n in range(60, 66)])
with patch.object(PRUNE, "sb", stuck), patch.object(PRUNE, "PAGE", 3), \
        contextlib.redirect_stderr(io.StringIO()):
    _, _, skipped = PRUNE.collect(argparse.Namespace(days=2, back=0, max_pages=90))
check("a page that does not move the cursor is a skipped window, not an endless loop",
      (len(stuck.calls) <= 3, len(skipped)), (True, 1))


# --------------------------------------------------------------------------- #
print()
print("the URL index: key, build, load")
check("scheme and host fold, the fragment goes, path case and query stay",
      PRUNE._url_key(" HTTPS://Leisure.TEST/Tenby-Centre?a=B#today "), "https://leisure.test/Tenby-Centre?a=B")
check("a path differing only in case is a different listing",
      PRUNE._url_key("https://leisure.test/tenby-centre") == PRUNE._url_key("https://leisure.test/Tenby-Centre"),
      False)

A = "https://leisure.test/Tenby-Centre"          # a timetable: one page, many sessions
A2 = "HTTPS://LEISURE.TEST/Tenby-Centre#today"   # the same request, spelled differently
F = "https://pool.test/timetable"                # live
H = "https://blocked.test/e/1"                   # answers 403
R = "https://refuses.test/e/1"                   # robots.txt says no
E = "https://later.test/e/1"
TIX = [f"https://tix.test/e/{i}" for i in range(5)]   # five dead at once: a refusing host

sweep_rows = [
    row(100, 0.5, A), row(101, 3, A), row(102, 30, A2), row(103, 100, A),
    row(104, 60, A), row(105, 45, A), row(106, 50, A), row(108, 70, A),
    row(107, 80, A, claimed_by=uid(9002)),       # claimed at sweep time: never indexed
    row(110, 3, "https://single.test/e/1"),        # one row: nothing a nearer row could bring in
    row(120, 15, "https://far.test/e/1"), row(121, 20, "https://far.test/e/1"),  # beyond reach
    row(130, 0.5, "https://near.test/e/1"), row(131, 1.5, "https://near.test/e/1"),  # all near
    row(140, 9, E), row(141, 40, E),
    row(150, 0.5, F), row(151, 20, F),
    row(160, 0.5, H), row(161, 20, H),
    row(170, 0.5, R), row(171, 20, R),
] + [row(180 + i, 0.5, u) for i, u in enumerate(TIX)] + [row(190 + i, 20, u) for i, u in enumerate(TIX)]

doc = PRUNE.build_url_index({u: [r for r in sweep_rows if r["description"].endswith(u) and not r["claimed_by"]]
                             for u in {r["description"].split(": ", 1)[1] for r in sweep_rows}}, NOW)
check("the near row is left out, the far rows of both spellings share one entry",
      doc["urls"].get(PRUNE._url_key(A)), sorted(uid(n) for n in (101, 102, 103, 104, 105, 106, 108)))
check("one row, every row beyond reach, or every row near: no entry",
      [k for k in doc["urls"] if any(h in k for h in ("single.test", "far.test", "near.test"))], [])
check("a listing whose soonest row is 9 days out is reachable and keeps both ids",
      doc["urls"].get(E), [uid(140), uid(141)])

CAL = "https://library.test/events"            # a calendar's own page, linked by every row it lists
cal = {CAL: [row(2000 + k, 0.5 + k * 0.3) for k in range(PRUNE.URL_INDEX_MAX_FAR + 8)]}
big = PRUNE.build_url_index({**cal, "https://one.test/e": [row(3000, 1), row(3001, 9)]}, NOW)
check(f"a listing with more than {PRUNE.URL_INDEX_MAX_FAR} far rows is a calendar's page: left out, and counted",
      (CAL in big["urls"], big["left_out"]["listings"], big["left_out"]["rows"] > PRUNE.URL_INDEX_MAX_FAR,
       "https://one.test/e" in big["urls"]), (False, 1, True, True))
over = {k: [uid(4000 + j) for j in range(PRUNE.URL_INDEX_MAX_FAR + 1)] for k in ("https://cal.test/x",)}

VERDICTS = {A: "gone", A2: "gone", F: "live", H: "unknown", R: "robots", "https://near.test/e/1": "live",
            **{u: "gone" for u in TIX}}


class _Robots:
    def __init__(self, *a, **k):
        pass

    def check(self, url):
        return {"allowed": True, "crawl_delay": None}


def run(db, *argv, verdicts=VERDICTS):
    """main() with the database, the probe and robots.txt faked -> (exit, stdout)."""
    out = io.StringIO()
    with patch.object(PRUNE, "sb", db), patch.object(PRUNE, "SUPABASE_URL", "https://db.test"), \
            patch.object(PRUNE, "SERVICE_KEY", "k"), patch.object(PRUNE.robots_txt, "Robots", _Robots), \
            patch.object(PRUNE, "cancellation_verdict", lambda u, robots=None, **k: verdicts.get(u, "live")), \
            patch.object(sys, "argv", ["mapsee_prune_cancelled.py", "--delay", "0", *argv]), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = PRUNE.main()
    return code, out.getvalue()


tmp = tempfile.mkdtemp()
path = os.path.join(tmp, "prune_url_index.json")
code, out = run(FakeDB(sweep_rows), "--write-url-index", path,
                verdicts={u: "live" for u in VERDICTS})
written = json.load(open(path, encoding="utf-8"))
check("the full sweep writes the file", (code, sorted(written["urls"]) == sorted(doc["urls"])), (0, True))
check("...and says what it weighs", "URL INDEX: wrote 17 far row(s) of 10 listing(s)" in out, True)
check("...stamped now, version 1, cut at 2 days",
      (written["version"], written["near_days"], abs(PRUNE._epoch(written["written_at"]) - NOW) < 120),
      (1, 2, True))

os.remove(path)
try:
    run(FakeDB(sweep_rows), "--write-url-index", path, verdicts=None)   # the probe phase crashes
except Exception:
    pass
check("it is written BEFORE the probing, so a run that dies probing still leaves it to save",
      os.path.exists(path), True)
put(path, json.dumps(written))


# --------------------------------------------------------------------------- #
print()
print("a near run: a listing found off also hides the far rows the index holds")


def week_later():
    """The table a near run finds days after the sweep: the far rows of A have
    moved on in every way a row can."""
    rows = [dict(r) for r in sweep_rows]
    by = {r["id"]: r for r in rows}
    by[uid(102)]["claimed_by"] = uid(9003)                                      # claimed since
    by[uid(103)]["description"] = "Blurb.\n\nTickets / info: https://leisure.test/new-page"  # re-linked
    by[uid(104)]["hidden_at"] = "2026-10-01T00:00:00+00:00"                     # an organizer's take-down
    by[uid(105)]["claimed_at"] = at(-1)                                         # claimer's profile deleted
    del by[uid(106)]                                                            # deleted
    by[uid(108)]["starts_at"] = at(-0.1)                                        # moved to a time now past
    return FakeDB(list(by.values()))


db = week_later()
code, out = run(db, "--days", "2", "--url-index", path, "--apply")
hidden = sorted(i for i, r in db.rows.items() if r["hidden_at"] and i != uid(104))
check("the dead listing's near row and its one still-qualifying far row are hidden, nothing else",
      hidden, [uid(100), uid(101)])
check("...with one stamp", len({db.rows[i]["hidden_at"] for i in hidden}), 1)
check("'far rows hidden from the URL index' is counted", "far rows hidden from the URL index: 1" in out, True)
check("a row claimed since the sweep is skipped", db.rows[uid(102)]["hidden_at"], None)
check("a row whose claimer deleted their profile (claimed_at only) is skipped", db.rows[uid(105)]["hidden_at"], None)
check("a row re-linked since the sweep is not condemned by its old link", db.rows[uid(103)]["hidden_at"], None)
check("a row that has started since (moved earlier) is left, like the collect leaves it",
      db.rows[uid(108)]["hidden_at"], None)
check("an organizer's take-down keeps its own stamp", db.rows[uid(104)]["hidden_at"], "2026-10-01T00:00:00+00:00")
check("a live listing hides nothing, near or far", (db.rows[uid(150)]["hidden_at"], db.rows[uid(151)]["hidden_at"]),
      (None, None))
check("a 403 hides nothing, near or far", (db.rows[uid(160)]["hidden_at"], db.rows[uid(161)]["hidden_at"]),
      (None, None))
check("robots.txt refusing hides nothing, near or far",
      (db.rows[uid(170)]["hidden_at"], db.rows[uid(171)]["hidden_at"]), (None, None))
check("a host answering dead wholesale hides nothing, near or far",
      [i for i in [*range(180, 185), *range(190, 195)] if db.rows[uid(i)]["hidden_at"]], [])
patches = [p for m, p, _b, _pr in db.calls if m == "PATCH"]
check("every hide carries the collect's filters and both claimed columns",
      bool(patches) and all(PRUNE.LISTED in p and "claimed_by=is.null" in p and "claimed_at=is.null" in p
                            for p in patches), True)
reads = [p for m, p, _b, _pr in db.calls if m == "GET" and "id=in." in p]
check("the far ids are read back first, under the same filters, not yet started",
      bool(reads) and all(PRUNE.LISTED in p and "starts_at=gte." in p for p in reads), True)
check("the near run leaves the file as it found it", json.load(open(path, encoding="utf-8")), written)

print()
print("...and without a usable index the run is the run it was before")
base = week_later()
run(base, "--days", "2", "--apply")
before = sorted(i for i, r in base.rows.items() if r["hidden_at"] and i != uid(104))
check("no --url-index: only the near row", before, [uid(100)])
stale = dict(written, written_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW - 9 * 86400)))
for k, (label, content, says) in enumerate((
        ("a missing file", None, "URL INDEX: none at"),
        ("a corrupt file", "{not json", "URL INDEX: unreadable"),
        ("a file nine days old", json.dumps(stale), "older than 8"),
        ("another version", json.dumps(dict(written, version=2)), "not version 1"))):
    p = os.path.join(tmp, f"case-{k}.json")
    if content is not None:
        put(p, content)
    db = week_later()
    code, out = run(db, "--days", "2", "--url-index", p, "--apply")
    check(f"{label}: the same hides as no index, said aloud",
          (code, sorted(i for i, r in db.rows.items() if r["hidden_at"] and i != uid(104)), says in out,
           "far rows hidden from the URL index: 0" in out), (0, before, True, True))

ids, note = PRUNE.load_url_index(path, NOW)
check("a fresh file loads every entry", sum(len(v) for v in ids.values()), written["rows"])
odd = dict(written, urls={PRUNE._url_key(A): [uid(101), "1),hidden_at.is.null,id.in.(x"]})
put(os.path.join(tmp, "odd.json"), json.dumps(odd))
check("an id that is not a uuid never reaches a filter",
      PRUNE.load_url_index(os.path.join(tmp, "odd.json"), NOW)[0], {PRUNE._url_key(A): [uid(101)]})
put(os.path.join(tmp, "over.json"), json.dumps(dict(written, urls={**written["urls"], **over})))
check("...and an entry over the cap is dropped on load, whoever wrote it",
      "https://cal.test/x" in PRUNE.load_url_index(os.path.join(tmp, "over.json"), NOW)[0], False)
future = dict(written, written_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW + 86400)))
put(os.path.join(tmp, "future.json"), json.dumps(future))
check("a file stamped tomorrow is not believed", PRUNE.load_url_index(os.path.join(tmp, "future.json"), NOW)[0], {})

try:
    with contextlib.redirect_stderr(io.StringIO()):
        run(FakeDB([]), "--url-index", path, "--write-url-index", path)
    both = "accepted"
except SystemExit as e:
    both = e.code
check("one run never both reads and writes the file", both, 2)

db = week_later()
code, out = run(db, "--days", "2", "--url-index", path)
check("a dry run writes nothing and says what the index would add",
      ([m for m, *_ in db.calls if m == "PATCH"], "far rows the URL index would hide: 1" in out), ([], True))

print()
if FAILS:
    print(f"{len(FAILS)} FAILED: " + "; ".join(FAILS))
    sys.exit(1)
print("all prune collect and URL index checks passed")
