#!/usr/bin/env python3
"""
test_curate_reapply.py — putting a finished curation run back on a moved main.

curate-catalog.yml holds its checkout for 30-90 minutes and then pushes four
files, so anything that lands on main in that window makes the push a
non-fast-forward. Three runs died that way, at their LAST step, with every
verified source thrown away: 2026-08-20, 2026-08-27 (six commits landed inside
its hour) and 2026-09-02 (the Wednesday gap sweep, queued behind the daily one,
checked out the SHA the daily one had just superseded).

The commit step now does what osm-food.yml does — reset to the fresh tip and
RE-APPLY, five times if it has to — and that only works because every part of
what a run produces is re-appliable. This grades the parts, because each way it
can go wrong is silent:

  * a ledger merge that overwrites rather than unions throws away the weekly
    audit's rows, or this run's 300 probes, and the file still parses;
  * a cursor merged one level too high drops every OTHER backend's position, so
    the next run re-walks ground it covered a month ago and reports nothing;
  * an append that is not idempotent puts one run in the trend twice, and the
    trend is the only thing anybody reads that file for;
  * a `merge` that is not idempotent duplicates a source on every retry.

The last one is the reason the loop can re-read at all, so it is pinned here
even though cmd_merge is older than any of this.

Runs the REAL cmd_reapply against a temp tree — no network, no database. Both
production failures in this repo's OSM adapters were in a `main()` that no test
ever called; a merge rule graded only through its helpers is the same gap.

Run: python test_curate_reapply.py
"""
import json
import os
import shutil
import sys
import tempfile

import catalog_curate as cc

fails = []


def check(label, cond, detail=""):
    """`cond` may be a callable, and the reason is the regression sweep itself.

    Half of what is asserted here indexes into a structure the code under test
    produced, so the FIRST regression tried — a cursor merged one level too high
    — turned `m["socrata"]["markets"]` into a KeyError and ended the run in a
    traceback with every later rule ungraded. The mutation was caught, and the
    output could not say which rule caught it. Pass a lambda for anything that
    dereferences a result and a throw is reported as the failed assertion it is.
    """
    if callable(cond):
        try:
            cond = cond()
        except Exception as exc:  # noqa: BLE001
            cond, detail = False, f"raised {type(exc).__name__}: {exc}"
    print(f"{'ok  ' if cond else 'FAIL'}  {label}{'' if cond else '   ' + str(detail)}")
    if not cond:
        fails.append(label)


def run(fn, *a):
    """Call into the code under test; a throw becomes a FAILURE, not a traceback.

    Same reason as the callable form of `check` above — the regression sweep
    plants faults that raise, and a run that dies on the first one grades none of
    the rules after it.
    """
    try:
        return fn(*a)
    except Exception as exc:  # noqa: BLE001
        check(f"{getattr(fn, '__name__', fn)}{a!r} did not raise", False,
              f"raised {type(exc).__name__}: {exc}")
        return None


def rec(day, status="ok", name="x"):
    return {"type": "ics", "name": name, "status": status, "reason": "", "checked": day}


# --- the ledger union ------------------------------------------------------
# The audit (source-health.yml) writes curation_ledger.json too, so "main has
# rows this run has never seen" is the ordinary case rather than a corner.
base = {"a.example": rec(20260901), "b.example": rec(20260830)}
ours = {"b.example": rec(20260902, "fail"), "c.example": rec(20260902)}
m = cc._merge_ledgers(base, ours)
check("a row only main has survives the merge", m.get("a.example") == base["a.example"], m)
check("a row only this run has is added", m.get("c.example") == ours["c.example"], m)
check("the more recent probe wins a shared URL",
      lambda: m["b.example"]["checked"] == 20260902 and m["b.example"]["status"] == "fail", m)
check("nothing is lost either way", len(m) == 3, sorted(m))

# The other direction: main's row is NEWER, so it stays. A run that has been
# sitting in the concurrency queue is not automatically the better source.
m = cc._merge_ledgers({"b.example": rec(20260902)}, {"b.example": rec(20260801, "fail")})
check("an older probe does not overwrite a newer one",
      lambda: m["b.example"]["checked"] == 20260902, m)

# Same day is this run finishing after whatever landed while it ran.
m = cc._merge_ledgers({"b.example": rec(20260902, "ok")}, {"b.example": rec(20260902, "fail")})
check("on the same day this run's answer is the later observation",
      lambda: m["b.example"]["status"] == "fail", m)

# A row with no date must not outrank a dated one — the same default
# _dead_recently already uses, so a malformed row costs a repeat, not a record.
m = cc._merge_ledgers({"b.example": rec(20260902)}, {"b.example": {"status": "fail"}})
check("an undated row does not beat a dated one",
      lambda: m["b.example"].get("checked") == 20260902, m)

# --- the cursor merge ------------------------------------------------------
# ONE LEVEL DOWN. A top-level dict.update() replaces a whole backend, so a run
# that swept socrata alone would silently reset ckan and osm to where they were
# whenever main last wrote them — correct-looking file, months of walking gone.
base = {"socrata": {"art events": 100, "markets": 200},
        "ckan": {"data.gov.ie|events": 540},
        "osm": {"metro": 8, "metro_key": "AE:Abu Dhabi"}}
ours = {"socrata": {"art events": 200}}
m = cc._merge_cursors(base, ours)
check("a backend this run never touched keeps its positions",
      lambda: m["ckan"] == {"data.gov.ie|events": 540} and m["osm"]["metro"] == 8, m)
check("a query this run did not read keeps its offset",
      lambda: m["socrata"]["markets"] == 200, m)
check("a query this run advanced takes this run's offset",
      lambda: m["socrata"]["art events"] == 200, m)
check("the merge does not mutate the base it was handed",
      lambda: base["socrata"]["art events"] == 100, base)
check("a backend only this run has is added",
      lambda: cc._merge_cursors({}, {"osm": {"metro": 3}})["osm"] == {"metro": 3})
# osm's cursor carries a STRING beside its int, and a non-dict backend value is
# not something to merge into.
m = cc._merge_cursors({"osm": {"metro_key": "AE:Abu Dhabi"}}, {"osm": {"metro_key": "GB:Leeds"}})
check("a string position merges like any other",
      lambda: m["osm"]["metro_key"] == "GB:Leeds", m)

# --- cmd_reapply, against a real tree --------------------------------------
tmp = tempfile.mkdtemp(prefix="curate-reapply-")
snap = os.path.join(tmp, ".curate-snapshot")
os.makedirs(snap)
keep = (cc.HERE, cc.LEDGER_FILE, cc.CURSOR_FILE, cc.COVERAGE_HISTORY_FILE)
cc.HERE = tmp
cc.LEDGER_FILE = os.path.join(tmp, "curation_ledger.json")
cc.CURSOR_FILE = os.path.join(tmp, "curation_cursor.json")
cc.COVERAGE_HISTORY_FILE = os.path.join(tmp, "coverage_history.jsonl")


def write(path, obj):
    json.dump(obj, open(path, "w", encoding="utf-8"), indent=1, sort_keys=True)


def read(path):
    return json.load(open(path, encoding="utf-8"))


def plant_main():
    """The tree as `git reset --hard origin/main` leaves it."""
    write(cc.LEDGER_FILE, {"a.example": rec(20260901),
                           "audit.example": rec(20260902, "fail")})
    write(cc.CURSOR_FILE, {"socrata": {"art events": 100}, "ckan": {"q": 540}})
    open(cc.COVERAGE_HISTORY_FILE, "w", encoding="utf-8").write(
        json.dumps({"total_sources": 1, "day": 1}, sort_keys=True) + "\n")
    write(os.path.join(tmp, "ics_sources.json"),
          [{"name": "Already there", "url": "https://already.example/f.ics"}])


# What the run computed, snapshotted before the reset.
write(os.path.join(snap, "curation_ledger.json"),
      {"a.example": rec(20260901), "new.example": rec(20260902)})
write(os.path.join(snap, "curation_cursor.json"),
      {"socrata": {"art events": 200}})
write(os.path.join(snap, "coverage-after.json"), {"total_sources": 2, "day": 2})
verified = os.path.join(tmp, "cand-socrata.verified.json")
write(verified, [{"type": "ics", "name": "Fresh", "url": "https://fresh.example/f.ics"}])

plant_main()
check("cmd_reapply reports success", lambda: run(cc.cmd_reapply, snap) == 0)
led = read(cc.LEDGER_FILE)
check("the audit's row that only main had is still there", "audit.example" in led, sorted(led))
check("this run's new probe landed", "new.example" in led, sorted(led))
check("the cursor advanced without dropping the other backend",
      read(cc.CURSOR_FILE) == {"socrata": {"art events": 200}, "ckan": {"q": 540}},
      read(cc.CURSOR_FILE))
hist = open(cc.COVERAGE_HISTORY_FILE, encoding="utf-8").read().splitlines()
check("one coverage line was appended", len(hist) == 2, hist)
check("...and it is this run's snapshot",
      lambda: json.loads(hist[-1])["total_sources"] == 2, hist)

# A SECOND CALL WITHOUT A RESET. The loop resets first, so this is belt to that
# brace — but a half-succeeded push is the one case where it is not, and one run
# counted twice is invisible in a trend nobody can recompute.
run(cc.cmd_reapply, snap)
hist = open(cc.COVERAGE_HISTORY_FILE, encoding="utf-8").read().splitlines()
check("re-running without a reset does not append the line twice", len(hist) == 2, hist)

# THE RETRY, AS THE WORKFLOW ACTUALLY RUNS IT: main moves, we reset onto it, and
# re-apply. The second attempt must land exactly what the first would have.
plant_main()                                   # the reset
run(cc.cmd_reapply, snap)
run(cc.cmd_merge, verified)
first = (read(cc.LEDGER_FILE), read(cc.CURSOR_FILE), read(os.path.join(tmp, "ics_sources.json")),
         open(cc.COVERAGE_HISTORY_FILE, encoding="utf-8").read())
plant_main()                                   # the next attempt's reset
run(cc.cmd_reapply, snap)
run(cc.cmd_merge, verified)
second = (read(cc.LEDGER_FILE), read(cc.CURSOR_FILE), read(os.path.join(tmp, "ics_sources.json")),
          open(cc.COVERAGE_HISTORY_FILE, encoding="utf-8").read())
check("attempt 2 produces exactly what attempt 1 did", first == second)
check("the verified source reached the config",
      lambda: any(e["url"] == "https://fresh.example/f.ics" for e in second[2]), second[2])
check("...exactly once",
      lambda: sum(1 for e in second[2]
                  if e["url"] == "https://fresh.example/f.ics") == 1, second[2])
check("...beside the one that was already there", len(second[2]) == 2, second[2])

# MERGE ON TOP OF ITSELF, no reset. This is what makes re-reading safe rather
# than a second copy of the catalog, and it is the oldest code in the loop.
run(cc.cmd_merge, verified)
check("merging the same verified file twice adds nothing",
      len(read(os.path.join(tmp, "ics_sources.json"))) == 2,
      read(os.path.join(tmp, "ics_sources.json")))

# A MISSING SNAPSHOT IS LOUD AND NOT FATAL. The step runs under `bash -e`, so a
# non-zero return here would abandon the verified sources as well — which is the
# exact failure the retry loop exists to prevent.
plant_main()
rc = run(cc.cmd_reapply, os.path.join(tmp, "nope"))
check("a missing snapshot directory does not fail the commit step", rc == 0, rc)
check("...and leaves main's ledger alone",
      lambda: read(cc.LEDGER_FILE) == {"a.example": rec(20260901),
                                       "audit.example": rec(20260902, "fail")})

# A SNAPSHOT HALF-WRITTEN BY A CANCELLED SWEEP. _save_ledger truncates before it
# dumps, so a cancel mid-write leaves exactly this.
open(os.path.join(snap, "curation_ledger.json"), "w", encoding="utf-8").write('{"a.exa')
plant_main()
rc = run(cc.cmd_reapply, snap)
check("an unreadable ledger snapshot costs the ledger, not the run", rc == 0, rc)
check("...and the cursor and the coverage line still land",
      lambda: read(cc.CURSOR_FILE)["socrata"]["art events"] == 200
      and len(open(cc.COVERAGE_HISTORY_FILE, encoding="utf-8").read().splitlines()) == 2)

cc.HERE, cc.LEDGER_FILE, cc.CURSOR_FILE, cc.COVERAGE_HISTORY_FILE = keep
shutil.rmtree(tmp, ignore_errors=True)

# --- the workflow's half of the contract -----------------------------------
# The snapshot is passed between two steps by PATH, and a typo in either one is
# a silent no-op for ever — this repo has already paid for that shape twice (a
# parkrun config nobody committed under a friendly `if [ -f ... ]` skip, and an
# Overpass cache key nobody bumped). Cheap to assert, so assert it.
wf = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       ".github", "workflows", "curate-catalog.yml"),
          encoding="utf-8").read()
check("the commit step re-applies before it commits", "catalog_curate.py reapply" in wf)
check("...onto a tree reset to the branch's current head",
      'git reset --hard "origin/$BRANCH"' in wf, )
check("...and retries rather than dying on a rejected push",
      "push rejected" in wf and "for attempt in" in wf)
check("checkout names the branch, so a queued run is not stale by construction",
      "ref: ${{ github.ref_name }}" in wf)
check("the coverage line is no longer appended outside the retry loop",
      "coverage-after.json >> coverage_history.jsonl" not in wf
      and "cat coverage-after.json >>" not in wf)
check("the scratch rm cannot run before the merge that needs the verified files",
      lambda: "rm -f cand-" not in wf
      or wf.index("catalog_curate.py merge") < wf.index("rm -f cand-"))

print()
print(f"{'FAILURES: ' + ', '.join(fails) if fails else 'all checks passed'}")
sys.exit(1 if fails else 0)
