#!/usr/bin/env python3
"""Which rows a rewrite-every-run adapter may leave alone.

`--only-new` froze every row on the day it landed, which for the three
OpenStreetMap adapters is a permanent staleness bug: every column they write is
derived from an upstream people edit, so a playground that gained opening hours
stays furniture for ever. Dropping the flag fixed that and bought a second
problem — an UPDATE writing identical bytes still costs a dead tuple, a WAL
record and a relocated live row, and almost every row IS identical.

So the sync reads back first. The whole value of that is in WHICH DIRECTION IT
FAILS: skipping a row that really changed is invisible and permanent (it looks
exactly like --only-new, which is what we just removed), while writing a row
that did not change costs one wasted UPDATE, which is what the code did before
this existed. Every case below is about that asymmetry.

    python test_skip_unchanged.py
"""
import sys
import urllib.parse

import mapsee_supabase_sync as m

FAILS = 0


def check(label, cond, detail=""):
    global FAILS
    print(f"{'ok  ' if cond else 'FAIL'}  {label}" + ("" if cond else f"   {detail!r}"))
    if not cond:
        FAILS += 1


# A stub PostgREST that answers with whatever rows it was given, and records
# every request so the cases below can assert on what went over the wire.
class Stub:
    def __init__(self, stored, status=200, body=None):
        self.stored = {r["external_id"]: r for r in stored}
        self.status = status
        self.body = body
        self.calls = []

    def get(self, url, headers=None, timeout=None):
        self.calls.append(url)
        outer = self

        class R:
            status_code = outer.status

            @staticmethod
            def json():
                if outer.body is not None:
                    return outer.body
                q = urllib.parse.unquote(url.split("?", 1)[1])
                want = [x.strip('"') for x in
                        q.split("external_id=in.(", 1)[1].rsplit(")", 1)[0].split(",")]
                sel = q.split("select=", 1)[1].split("&", 1)[0].split(",")
                out = []
                for i in want:
                    row = outer.stored.get(i)
                    if row:
                        out.append({k: v for k, v in row.items() if k in sel})
                return out
        return R()


def row(**kw):
    base = dict(external_id="a" * 40, external_source="mapsee", title="Fountain",
                description="A fountain.", lat=47.6, lon=-122.3, pin_only=True,
                icon="\N{POTABLE WATER SYMBOL}", category="outdoors", categories=None,
                starts_at="2026-09-01T07:00:00+00:00", ends_at="2026-09-02T06:59:00+00:00",
                recurring_hours=None, is_private=False)
    base.update(kw)
    return base


# --- 1. identical is identical --------------------------------------------
{
    None
}
mine = row()
s = Stub([dict(mine)])
check("a row equal to what is stored is not rewritten",
      m.unchanged_ids(s, "https://x", "k", [mine]) == {mine["external_id"]}, )

# --- 2. every column is compared, because the loop is driven by the row ----
# A hand-kept list of "columns that matter" is the failure this repo has paid
# for twice (the 🔎 link past a passing check, the attribution line two repos
# spell differently). Change ANY column and the row must be written.
for col, val in [("title", "Fountain (relocated)"), ("description", "A fountain. Wheelchair accessible."),
                 ("lat", 47.61), ("lon", -122.31), ("pin_only", False),
                 ("icon", "\N{ARTIST PALETTE}"), ("category", "arts"),
                 ("categories", ["kids"]), ("recurring_hours", {"tz": "UTC", "days": {"0": ["09:00", "17:00"]}}),
                 ("is_private", True), ("starts_at", "2026-09-03T07:00:00+00:00")]:
    stored = row()
    changed = row(**{col: val})
    s = Stub([stored])
    check(f"a changed {col} is written",
          m.unchanged_ids(s, "https://x", "k", [changed]) == set(),
          (col, val))

# --- 3. THE FAIL-OPEN, which is the whole design --------------------------
# Every one of these must answer "write it". The alternative is an OSM edit
# that silently never lands, which is indistinguishable from the --only-new
# staleness this replaced.
mine = row()
check("a row that is not stored yet is written",
      m.unchanged_ids(Stub([]), "https://x", "k", [mine]) == set())
check("a refusal (400: a column the database has not got) writes everything",
      m.unchanged_ids(Stub([dict(mine)], status=400), "https://x", "k", [mine]) == set())
check("a 500 writes everything",
      m.unchanged_ids(Stub([dict(mine)], status=500), "https://x", "k", [mine]) == set())
check("a body that is not a list writes everything",
      m.unchanged_ids(Stub([dict(mine)], body={"code": "57014"}), "https://x", "k", [mine]) == set())


class Boom(Stub):
    def get(self, *a, **kw):
        raise OSError("connection reset")


check("a thrown request writes everything",
      m.unchanged_ids(Boom([dict(mine)]), "https://x", "k", [mine]) == set())
# A column WE write that the server did not return cannot be compared, so the
# row is changed. This is the shape a new column arrives in: to_row gains it,
# the select asks for it, and a server that predates it answers without it.
partial = {k: v for k, v in row().items() if k != "pin_only"}
check("a column the server omits is a changed row, not an equal one",
      m.unchanged_ids(Stub([partial]), "https://x", "k", [row()]) == set())

# --- 4. shapes the two sides spell differently -----------------------------
# Postgres answers timestamps with an offset, and floats at 15 significant
# digits (section 9). Treating those as differences would write every row every run and quietly
# undo the whole point; treating an UNPARSEABLE one as equal would be the
# dangerous direction, so it is not.
check("the same instant spelled Z and +00:00 is not a change",
      m.unchanged_ids(Stub([row(starts_at="2026-09-01T07:00:00+00:00")]), "https://x", "k",
                      [row(starts_at="2026-09-01T07:00:00Z")]) == {"a" * 40})
check("the same instant in another offset is not a change",
      m.unchanged_ids(Stub([row(starts_at="2026-09-01T07:00:00+00:00")]), "https://x", "k",
                      [row(starts_at="2026-09-01T09:00:00+02:00")]) == {"a" * 40})
check("47.6 and 47.60 are the same coordinate",
      m.unchanged_ids(Stub([row(lat=47.60)]), "https://x", "k", [row(lat=47.6)]) == {"a" * 40})
check("a NAIVE stamp is not an instant and is written rather than guessed",
      m.unchanged_ids(Stub([row(starts_at="2026-09-01T07:00:00+00:00")]), "https://x", "k",
                      [row(starts_at="2026-09-01T07:00:00")]) == set())
check("an unparseable stamp on OUR side is written, not treated as null",
      m.unchanged_ids(Stub([row(starts_at=None)]), "https://x", "k",
                      [row(starts_at="not a date")]) == set())
check("...and two unparseable stamps are still not equal",
      m.unchanged_ids(Stub([row(starts_at="also not a date")]), "https://x", "k",
                      [row(starts_at="not a date")]) == set())
check("both genuinely null IS equal (a date-less standing row)",
      m.unchanged_ids(Stub([row(starts_at=None, ends_at=None)]), "https://x", "k",
                      [row(starts_at=None, ends_at=None)]) == {"a" * 40})
# NULL and "" are different things in this schema (0108's CHECK rejects an empty
# array; a NULL description is what amenityHasContent reads). Conflating them
# would let a real clearing edit fail to land.
check("null and empty string are not the same value",
      m.unchanged_ids(Stub([row(street_address=None)]), "https://x", "k",
                      [row(street_address="")]) == set())

# --- 4b. THE ROLLED WINDOW, which would have made this a no-op --------------
# 0156's roll_recurring_windows rewrites starts_at/ends_at on any row with
# recurring_hours whose window has passed — hourly — so the STORED window on a
# standing row is the rolled one and to_row's is today's. Every OSM amenity,
# every imported shop and every collapsed OpenActive weekly series is a standing
# row, so comparing that column would have skipped nothing at all on precisely
# the population this exists to stop rewriting, while every case above still
# passed.
WEEKLY = {"tz": "Europe/London", "days": {"2": ["19:00", "20:00"]}}
check("a standing row whose window the roller moved is still unchanged",
      m.unchanged_ids(Stub([row(recurring_hours=WEEKLY, starts_at="2026-09-08T18:00:00+00:00",
                                ends_at="2026-09-08T19:00:00+00:00")]), "https://x", "k",
                      [row(recurring_hours=WEEKLY, starts_at="2026-09-01T18:00:00+00:00",
                           ends_at="2026-09-01T19:00:00+00:00")]) == {"a" * 40})
# ...and nothing is lost by looking away, because a change to the PATTERN is a
# change to recurring_hours, which is compared.
check("a changed weekly pattern is still written",
      m.unchanged_ids(Stub([row(recurring_hours=WEEKLY, starts_at="2026-09-08T18:00:00+00:00")]),
                      "https://x", "k",
                      [row(recurring_hours={"tz": "Europe/London", "days": {"3": ["19:00", "20:00"]}},
                           starts_at="2026-09-01T18:00:00+00:00")]) == set())
# A row CHANGING SHAPE must be written whichever way it goes, so the exemption
# needs a pattern on BOTH sides: a class that stopped recurring keeps a rolled
# window that nothing will ever move again.
check("a row that stops being standing is written",
      m.unchanged_ids(Stub([row(recurring_hours=WEEKLY, starts_at="2026-09-08T18:00:00+00:00")]),
                      "https://x", "k",
                      [row(recurring_hours=None, starts_at="2026-09-01T18:00:00+00:00")]) == set())
check("a row that becomes standing is written",
      m.unchanged_ids(Stub([row(recurring_hours=None, starts_at="2026-09-08T18:00:00+00:00")]),
                      "https://x", "k",
                      [row(recurring_hours=WEEKLY, starts_at="2026-09-01T18:00:00+00:00")]) == set())
# An ORDINARY occasion keeps its clock compared — a gig moved by an hour is the
# single most important edit this must not sleep through.
check("an occasion moved by an hour is written",
      m.unchanged_ids(Stub([row(starts_at="2026-09-01T19:00:00+00:00")]), "https://x", "k",
                      [row(starts_at="2026-09-01T20:00:00+00:00")]) == set())

# --- 4c. a column fed by the CLOCK is named, not obeyed ---------------------
# Adding `"last_seen_at": now()` to to_row is the obvious way to make "gone from
# the feed" detectable, and this repo has already written down that it wants it.
# It would also make every row differ on every run for ever, so the filter skips
# nothing and reports "all N rows differ" — which is exactly what a genuine
# refresh of a changed catalogue looks like.
import io, contextlib
stamped_mine = [row(external_id=f"{i:040d}", last_seen_at="2026-09-02T00:00:00+00:00")
                for i in range(60)]
stamped_theirs = [dict(r, last_seen_at="2026-09-01T00:00:00+00:00") for r in stamped_mine]
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    got = m.unchanged_ids(Stub(stamped_theirs), "https://x", "k", stamped_mine)
out = buf.getvalue()
check("a clock column still writes every row (behaviour unchanged)", got == set(), len(got))
check("...but it is NAMED rather than silently obeyed",
      "::warning::" in out and "last_seen_at" in out, out.strip()[:160])
# The warning must not fire on the ordinary case, or it is noise and gets muted.
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    m.unchanged_ids(Stub([dict(r) for r in stamped_mine]), "https://x", "k", stamped_mine)
check("...and stays quiet when rows genuinely match", "::warning::" not in buf.getvalue(),
      buf.getvalue().strip()[:160])

# --- 5. the sentinel cannot be equal to itself ------------------------------
# object() will not do here: the same instance compares equal to itself, which
# is the fail-open direction, so a single unparseable value on BOTH sides would
# read as "no change" — the one outcome this must never produce.
check("_Unknown is never equal to another _Unknown", m._Unknown() != m._Unknown())
check("...nor to itself", not (lambda u: u == u)(m._Unknown()))

# --- 6. many rows, and the URL that carries them ----------------------------
# Chunked by the LENGTH of the URL it builds rather than a row count, because a
# fixed 100 starts producing a 414 the day an adapter's fingerprint gets longer
# — and PostgREST answers that with an HTML error page, which json() then throws
# on, which fails open and writes everything for ever with nothing saying why.
many = [row(external_id=f"{i:040d}", title=f"Fountain {i}") for i in range(500)]
s = Stub([dict(r) for r in many[:250]])
got = m.unchanged_ids(s, "https://x", "k", many)
check("250 of 500 stored and identical are skipped", len(got) == 250, len(got))
check("...over more than one request", len(s.calls) > 1, len(s.calls))
check("...none of which builds an over-long URL",
      all(len(u) < 8000 for u in s.calls), max((len(u) for u in s.calls), default=0))

# --- 6b. the columns to blame are always named, not only past 99% ----------
# The 2026-09-09 refresh rewrote 76,946 of 221,000 compared rows and said
# nothing about why, because the only diagnostic fired at 99% of one column.
# The top three are printed on every read-back now; what is compared is not.
import contextlib
import io
four = [row(external_id=f"{i:040d}", title=f"F {i}", description="new", lat=1.0) for i in range(4)]
stored = [dict(r) for r in four]
for r in stored[:3]:
    r["description"] = "old"               # differs on 3 rows
stored[0]["title"] = stored[1]["title"] = "renamed"   # on 2
stored[0]["lat"] = 2.0                     # on 1; stored[3] is identical
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    got = m.unchanged_ids(Stub(stored), "https://x", "k", four)
check("the identical row is still the only one skipped", got == {four[3]["external_id"]}, got)
check("...and the three most-blamed columns are printed with counts, most first",
      "3 of 4 stored rows differ; most-blamed columns: description 3, title 2, lat 1"
      in buf.getvalue(), buf.getvalue())

# --- 7. Only a STORED row is read back; a new one is written unasked --------
# The import state already says which rows exist, read with the same filter and
# failing closed. Handing unchanged_ids the new rows too cost 258,728 needless
# id lookups over 187 calls in a week of logs (Ticketmaster 653 asked for 4
# stored), and a new row can only ever be written.
src = open("mapsee_supabase_sync.py", encoding="utf-8").read()
check("a batch of new rows reads nothing back",
      m.rows_to_compare([row(external_id="new")], {"existing": False}) == [])
check("an exact-detail refresh under only-new still compares its stored row",
      [r["external_id"] for r in m.rows_to_compare([row(external_id="existing")], {"existing": False})]
      == ["existing"])
check("...and only it, never the new rows beside it",
      [r["external_id"] for r in m.rows_to_compare(
          [row(external_id="new"), row(external_id="existing"), row(external_id="new2")],
          {"existing": False, "claimed-elsewhere": True})] == ["existing"])
check("no state read means nothing is compared", m.rows_to_compare([row()], None) == [])
# LAST, after the claimed-guard and the moderation pre-filter, so nothing is
# read back for a row that is about to be dropped anyway.
# Anchored on `= upsert(` rather than on the whole assignment: upstream has
# already widened that tuple once (n, skipped -> n, skipped, lost) and pinning
# the literal line failed a check whose actual property — the filter runs after
# the ones that DROP rows, and before the write — was never in danger.
check("...and runs after the filters that drop rows",
      src.index("Moderation pre-filter") < src.index("stored = rows_to_compare(rows, state)")
      < src.index("= upsert("))

# --- 7b. main() itself: unchanged_ids hears only stored ids; new rows are written
# Driven through main() rather than the helper alone, because the bug was in
# what main() handed over: every row that survived the filters.
import json
import os
import tempfile
from unittest.mock import patch

os.environ.setdefault("MAPSEE_DB_WAIT_SECONDS", "0")


class StateStore:
    """The import-state read: answers only the ids it holds, unclaimed."""

    def __init__(self, stored):
        self.stored, self.calls, self.headers = set(stored), [], {}

    def get(self, url, **kwargs):
        self.calls.append(url)
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        clause = q.get("external_id", [None])[0]
        ids = json.loads("[" + clause[4:-1] + "]") if clause else []
        page = [dict(external_id=e, claimed_at=None) for e in sorted(set(ids) & self.stored)]

        class R:
            status_code = 200
            headers = {"Content-Range": f"0-{len(page) - 1}/{len(page)}" if page else "*/0"}

            def json(self):
                return page
        return R()


_empty = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
_empty.write('{"events": []}')
_empty.close()


def run_main(flags, recs, stored, same=()):
    """(the id lists unchanged_ids was asked about, the ids handed to upsert)."""
    asked = []

    def spy(_session, _url, _key, rows):
        asked.append([r["external_id"] for r in rows])
        return {r["external_id"] for r in rows if r["external_id"] in same}

    def fake_build_rows(_path, _host, _geo=None, keep=None, **_kw):
        kept = keep(list(recs)) if keep else list(recs)
        return [row(external_id=r["fingerprint"], title=r["fingerprint"]) for r in kept]

    with patch.dict(os.environ, {"MAPSEE_HOST_PROFILE_ID": "host", "SUPABASE_URL": "https://db.example",
                                 "SUPABASE_SERVICE_ROLE_KEY": "unused-test-key"}), \
         patch("sys.argv", ["sync", "--store", _empty.name, "--skip-unchanged"] + flags), \
         patch("requests.Session", return_value=StateStore(stored)), \
         patch.object(m, "build_rows", side_effect=fake_build_rows), \
         patch.object(m, "load_blocklist", return_value=[]), \
         patch.object(m, "unchanged_ids", side_effect=spy), \
         patch.object(m, "upsert", return_value=(0, 0, 0)) as write, \
         contextlib.redirect_stdout(io.StringIO()):
        m.main()
    return asked, sorted(r["external_id"] for r in write.call_args.args[0])


def rec(fp, detail=False):
    r = {"fingerprint": fp, "name": fp, "start_utc": "2026-11-01T18:00:00Z"}
    if detail:
        r["source_details"] = {"availability": "sold out"}     # an exact-detail refresh
    return r


four = [rec("a"), rec("b"), rec("c"), rec("d")]
asked, written = run_main([], four, stored={"a", "b"}, same={"a"})
check("a refresh asks unchanged_ids about its stored rows only", asked == [["a", "b"]], asked)
check("...writes the stored row that differs and every new row, unasked",
      written == ["b", "c", "d"], written)
asked, written = run_main([], four, stored=set())
check("a refresh of nothing but new rows reads nothing back", asked == [], asked)
check("...and writes them all", written == ["a", "b", "c", "d"], written)
asked, written = run_main(["--only-new"], [rec("a", detail=True), rec("b"), rec("c"), rec("d")],
                          stored={"a", "b"})
check("an --only-new run asks about the stored row it kept, not the new ones beside it",
      asked == [["a"]], asked)
check("...and writes that row and every new row", written == ["a", "c", "d"], written)
check("unchanged_ids is never asked about an id the import state did not find",
      all(set(ids) <= {"a", "b"} for ids in asked), asked)
os.unlink(_empty.name)

# --- 8. EVERY sync invocation carries it, because one that does not is silent -
# A flag passed in thirteen places and forgotten in the fourteenth costs nothing
# visible: that job rewrites every row it touches, exactly as before, and the
# only evidence is a line in its log that nobody reads. Same shape as the
# parkrun config that was never committed while the job printed a friendly skip
# every night — so it is asserted rather than remembered.
import glob
missing = []
for wf in sorted(glob.glob(".github/workflows/*.yml")):
    for ln, line in enumerate(open(wf, encoding="utf-8"), 1):
        if "mapsee_supabase_sync.py" in line and "--store" in line and "--skip-unchanged" not in line:
            missing.append(f"{wf}:{ln}")
check("every sync invocation passes --skip-unchanged", not missing, missing)

# --- 9. FLOAT8 READS BACK AT 15 SIGNIFICANT DIGITS --------------------------
# PostgREST answers a double precision column as '%.15g', so a 17-digit source
# coordinate never equalled its stored self and was rewritten on every compare:
# facility hours blamed 'lat 21, lon 21' of 434 every day 10-20..10-26, and the
# 10-21 ics sync re-blamed 352 lon seconds after its checkpoint had written them.
# Meadowbrook Teen Life Center, as the store has it and as the read returns it.
MEAD = dict(lat=47.70713352786287, lon=-122.29359643875411)
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    got = m.unchanged_ids(Stub([row(lat=47.7071335278629, lon=-122.293596438754)]), "https://x", "k",
                          [row(**MEAD)])
check("a 17-digit coordinate against its 15-digit read-back is not a change", got == {"a" * 40}, got)
check("...and the rows that saved are counted, so OpenActive's share is measured, not inferred",
      "1 row(s) differed only past the 15th significant digit" in buf.getvalue(), buf.getvalue())
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    m.unchanged_ids(Stub([row(**MEAD)]), "https://x", "k", [row(**MEAD)])
check("...and nothing is counted when the floats are equal outright",
      "15th significant digit" not in buf.getvalue(), buf.getvalue())
# ROUNDING TOO COARSE IS THE DANGEROUS DIRECTION: round(v, 6) would pass every
# case above and swallow this one, a real 1e-7 degree (1 cm) move.
check("a 1e-7 degree move is still written",
      m.unchanged_ids(Stub([row(lat=47.6062095)]), "https://x", "k", [row(lat=47.6062096)]) == set())
# ...and one that does not straddle a half: 47.6062095 sits on round(v, 6)'s
# boundary, so that pair is caught by it by luck. These two round to one value.
check("...including one that round(v, 6) or '%.8g' would fold into its neighbour",
      m.unchanged_ids(Stub([row(lat=47.6062091)]), "https://x", "k", [row(lat=47.6062092)]) == set())
check("a stored int 47 and our 47.0 are the same coordinate",
      m.unchanged_ids(Stub([row(lat=47)]), "https://x", "k", [row(lat=47.0)]) == {"a" * 40})
check("an int is not cut to 15 digits (a future bigint column compares exactly)",
      m._norm_cmp("n", 2 ** 53) != m._norm_cmp("n", 2 ** 53 - 1))
check("NaN against NaN is written, never taken for equal",
      m.unchanged_ids(Stub([row(lat=float("nan"))]), "https://x", "k", [row(lat=float("nan"))]) == set())

# --- 10. A PIN THAT IS ALWAYS OPEN STORES ITS ZONE, NOT SEVEN DAYS ---------
# 330 B a row of heap (PG16, 100,000 rows) that nothing reads: 0239's roller
# skips pins and every other reader tests presence. Written compact from now on;
# the ~544k pins stored with the full week must NOT be rewritten just to compact
# them (each would be a non-HOT UPDATE), so the compare treats the two as equal —
# and only between two pins, so everything else still fails toward writing.
ALWAYS = {str(d): [["00:00", "23:59"]] for d in range(7)}
LA_FULL = {"tz": "America/Los_Angeles", "days": ALWAYS}
LA = {"tz": "America/Los_Angeles"}
OLD_WIN = dict(starts_at="2026-09-01T07:00:00+00:00", ends_at="2026-09-02T06:59:00+00:00")
NEW_WIN = dict(starts_at="2026-10-27T07:00:00+00:00", ends_at="2026-10-28T06:59:00+00:00")


def same_pair(stored, mine):
    return m.unchanged_ids(Stub([stored]), "https://x", "k", [mine]) == {"a" * 40}


check("a stored pin with the full week against our compact pin is not a change",
      same_pair(row(recurring_hours=LA_FULL), row(recurring_hours=LA)))
check("...nor the other way round",
      same_pair(row(recurring_hours=LA), row(recurring_hours=LA_FULL)))
check("compact against compact is not a change",
      same_pair(row(recurring_hours=LA), row(recurring_hours=LA)))
check("a pin whose zone changed is written",
      not same_pair(row(recurring_hours=LA_FULL), row(recurring_hours={"tz": "America/Denver"})))
check("a stored pin with any other days is written compact",
      not same_pair(row(recurring_hours={"tz": "America/Los_Angeles", "days": dict(ALWAYS, **{"6": [["00:00", "18:00"]]})}),
                    row(recurring_hours=LA)))
check("a stored full week carrying another key is not folded away",
      not same_pair(row(recurring_hours=dict(LA_FULL, note="x")), row(recurring_hours=LA)))
check("a stored compact pin that becomes a listing is written",
      not same_pair(row(recurring_hours=LA), row(pin_only=False, recurring_hours=LA_FULL)))
check("a stored full-week pin that becomes a listing is written",
      not same_pair(row(recurring_hours=LA_FULL), row(pin_only=False, recurring_hours=LA_FULL)))
check("a listing's full week against the same full week is not a change",
      same_pair(row(pin_only=False, recurring_hours=LA_FULL), row(pin_only=False, recurring_hours=LA_FULL)))
check("a LISTING stored without its days is written: the equivalence is for pins only",
      not same_pair(row(pin_only=False, recurring_hours=LA), row(pin_only=False, recurring_hours=LA_FULL)))
check("a pin the server answered without recurring_hours is written",
      m.unchanged_ids(Stub([{k: v for k, v in row(recurring_hours=LA_FULL).items() if k != "recurring_hours"}]),
                      "https://x", "k", [row(recurring_hours=LA)]) == set())
# THE VALUE MUST STAY TRUTHY: skip_cols looks away from the roller's window only
# when both sides carry recurring_hours, and pins are no longer rolled.
check("a compact pin with last month's window against today's is not a change",
      same_pair(row(recurring_hours=LA, **OLD_WIN), row(recurring_hours=LA, **NEW_WIN)))
check("...and a stored full-week pin with an old window against a compact one is not either",
      same_pair(row(recurring_hours=LA_FULL, **OLD_WIN), row(recurring_hours=LA, **NEW_WIN)))

# What to_row writes. Seattle, so the zone is America/Los_Angeles either way
# (timezonefinder or the US longitude fallback).
SEA = (47.61, -122.33)
pin = m._recurring_hours({"pin_only": True, "recurring_days": ALWAYS}, *SEA)
check("an always-open pin is written as its zone alone", pin == LA, pin)
check("...which is truthy, so skip_cols still looks away from its window",
      bool(pin) and m.skip_cols({"recurring_hours": pin}, {"recurring_hours": pin}) == m._TS_COLS)
listing = m._recurring_hours({"pin_only": False, "recurring_days": ALWAYS}, *SEA)
check("an always-open LISTING keeps its days (the roller reads them)", listing == LA_FULL, listing)
bounded = m._recurring_hours({"pin_only": True, "recurring_days": {"0": [["08:00", "20:00"]]}}, *SEA)
check("a pin with any other days keeps them", bounded.get("days") == {"0": [["08:00", "20:00"]]}, bounded)
check("only pin_only True compacts (a truthy 1 is not a pin's flag)",
      m._recurring_hours({"pin_only": 1, "recurring_days": ALWAYS}, *SEA) == LA_FULL)
check("tuples from an unserialised record are the same week",
      m._recurring_hours({"pin_only": True, "recurring_days": {d: [("00:00", "23:59")] for d in range(7)}},
                         *SEA) == LA)
check("a week with no days is still no pattern",
      m._recurring_hours({"pin_only": True, "recurring_days": {}}, *SEA) is None)

# --- 11. A LINE to_row NO LONGER WRITES IS NOT A REASON TO WRITE ------------
# OpenActive lost its 🔎 search and its weekly session count on 2026-10-27. ~78k
# rows carry the one and ~66k the other, all compared daily, so a stored copy
# that differs ONLY by them must not be rewritten for it. PAIRWISE, and toward
# writing everywhere else: each case below that must be written is one an
# unconditional strip or a looser opener match would have swallowed.
TICKETS = "Tickets / info: https://book.example.test/s/1"
SHOW = "\n\n\U0001F50E More on this show: https://www.google.com/search?q=Hall%20Yoga%20"
LISTEN = "\n\n\U0001F3B5 Listen — Spotify: https://open.spotify.com/search/x"
OPEN_NEW = "\U0001F501 Runs weekly — on 1 day a week.\n\n"
OPEN_2 = "\U0001F501 Runs weekly — 2 sessions scheduled over the next few weeks, on 1 day a week.\n\n"
OPEN_17 = "\U0001F501 Runs weekly — 17 sessions scheduled over the next few months, on 1 day a week.\n\n"
OFF = "⚠️ Not on Tue 3 Nov: cancelled by the publisher.\n\n"
WEEKLY = {"tz": "Europe/London", "days": {"1": [["19:00", "20:00"]]}}
SOON = dict(starts_at="2026-10-29T19:00:00+00:00", ends_at="2026-10-29T20:00:00+00:00")


def quiet_same(stored, mine):
    with contextlib.redirect_stdout(io.StringIO()):     # the count line is asserted once, below
        return same_pair(stored, mine)


def dated(d):
    return row(pin_only=False, description=d, **SOON)


def weekly(d, **kw):
    return row(pin_only=False, description=d, recurring_hours=WEEKLY, **dict(SOON, **kw))


body = "Yoga for all.\n\n" + TICKETS
check("a dated row stored with the 🔎 line, ours without: not a change",
      quiet_same(dated(body + SHOW + "2026-10-29"), dated(body)))
check("...nor with the line between two others (Tickets above, 🎵 Listen below)",
      quiet_same(dated(body + SHOW + "2026-10-29" + LISTEN), dated(body + LISTEN)))
check("the 🔎 line plus a prose edit is written",
      not quiet_same(dated(body + SHOW + "2026-10-29"), dated("Yoga for all, now indoors.\n\n" + TICKETS)))
check("a row that still writes the line (Ticketmaster) compares it exactly",
      not quiet_same(dated(body + SHOW + "2026-10-29"), dated(body + SHOW + "2026-10-30")))
check("...and prose of ours that happens to hold a 🔎 is never taken for the line",
      not quiet_same(dated("Look for the \U0001F50E sign.\n\n" + TICKETS + SHOW + "2026-10-29"),
                    dated("Look for the \U0001F50E sign.\n\n" + TICKETS)))
check("a standing row whose stored search is on our soonest date: not a change",
      quiet_same(weekly(OPEN_NEW + body + SHOW + "2026-10-29"), weekly(OPEN_NEW + body)))
check("a standing row whose search date has rolled is written (once, as it would be today)",
      not quiet_same(weekly(OPEN_NEW + body + SHOW + "2026-10-22"), weekly(OPEN_NEW + body)))
check("the old weekly opener with the same day count: not a change",
      quiet_same(weekly(OPEN_2 + body), weekly(OPEN_NEW + body)))
check("...its 'few months' spelling too",
      quiet_same(weekly(OPEN_17 + body), weekly(OPEN_NEW + body)))
check("...and behind the '⚠️ Not on' line, which stays first",
      quiet_same(weekly(OFF + OPEN_2 + body), weekly(OFF + OPEN_NEW + body)))
check("a different '⚠️ Not on' line is written",
      not quiet_same(weekly(OFF + OPEN_2 + body), weekly(OFF.replace("3 Nov", "10 Nov") + OPEN_NEW + body)))
check("the old opener with a different number of days is written",
      not quiet_same(weekly(OPEN_2.replace("on 1 day", "on 2 days") + body), weekly(OPEN_NEW + body)))
check("the old opener with any other remainder is written",
      not quiet_same(weekly(OPEN_2 + "Yoga for some.\n\n" + TICKETS), weekly(OPEN_NEW + body)))
check("an opener the old adapter could not have written (9 sessions, 'few weeks') is written",
      not quiet_same(weekly(OPEN_2.replace("2 sessions", "9 sessions") + body), weekly(OPEN_NEW + body)))
check("the transition row (old opener, search on our soonest date): not a change",
      quiet_same(weekly(OPEN_2 + body + SHOW + "2026-10-29"), weekly(OPEN_NEW + body)))
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    m.unchanged_ids(Stub([weekly(OPEN_2 + body + SHOW + "2026-10-29")]), "https://x", "k",
                    [weekly(OPEN_NEW + body)])
check("...and the log counts the rows a retired line alone made different",
      "1 row(s) differed only by a line to_row no longer writes" in buf.getvalue(), buf.getvalue())
check("a stored row answered without its description is written",
      m.unchanged_ids(Stub([{k: v for k, v in weekly(OPEN_2 + body).items() if k != "description"}]),
                      "https://x", "k", [weekly(OPEN_NEW + body)]) == set())
check("a stored NULL description against ours is written",
      not quiet_same(weekly(None), weekly(OPEN_NEW + body)))

from test_sync_lookup import run as run_lookup_checks
FAILS += run_lookup_checks()

print()
print(f"{FAILS} FAILED" if FAILS else "a rewrite writes what changed")
sys.exit(1 if FAILS else 0)
