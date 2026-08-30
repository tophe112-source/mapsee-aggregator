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
# Postgres answers timestamps with an offset and floats in its own shortest
# form. Treating those as differences would write every row every run and quietly
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

# --- 7. --only-new makes it a no-op, and main() must not pay for it ---------
src = open("mapsee_supabase_sync.py", encoding="utf-8").read()
check("the filter is skipped under --only-new (every row is new by construction)",
      "if a.skip_unchanged and not a.only_new" in src)
# LAST, after the claimed-guard and the moderation pre-filter, so nothing is
# read back for a row that is about to be dropped anyway.
# Anchored on `= upsert(` rather than on the whole assignment: upstream has
# already widened that tuple once (n, skipped -> n, skipped, lost) and pinning
# the literal line failed a check whose actual property — the filter runs after
# the ones that DROP rows, and before the write — was never in danger.
check("...and runs after the filters that drop rows",
      src.index("Moderation pre-filter") < src.index("a.skip_unchanged and not a.only_new")
      < src.index("= upsert("))

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

print()
print(f"{FAILS} FAILED" if FAILS else "a rewrite writes what changed")
sys.exit(1 if FAILS else 0)
