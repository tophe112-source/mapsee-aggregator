#!/usr/bin/env python3
"""
test_meetup_category_cache.py - a Meetup event keeps its first category guess.

Prints one line per case and exits non-zero, like the other gate scripts.

WHAT THIS IS ABOUT. A Meetup row's category is the keyword whose eventSearch
results held it first in a run, both Meetup stores are rebuilt every run, and
the first keyword moves from day to day: category was blamed on 4,146-4,818
UPDATEs per leg per day over 10-22..10-26, each carrying ends_at and the Listen
line with it. mapsee_meetup_category_cache holds the first guess per
fingerprint until the event passes.

WHAT MUST NEVER HAPPEN is the expensive half, and most of what is pinned here:
the pass rewrites ONLY events[*].category. It must never add or drop a record,
or touch tombstones, seen, complete_reads or _meta: a tombstone is what keeps a
cancelled or taken-down event from being imported again. It must never block a
sync either: an unreadable cache or store is a ::warning:: and exit 0, and a
write that dies halfway leaves the last good file, not half of one.
"""
import contextlib
import io
import json
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mapsee_meetup_category_cache as M
from mapsee_ingest_meetup import DEFAULT_KEYWORDS, parse_keywords

FAILS = []


def check(label, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {label}" + ("" if ok else f"   got {got!r}, want {want!r}"))
    if not ok:
        FAILS.append(label)


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


TODAY = date(2026, 10, 27)
# TODAY IS PINNED FOR THE WHOLE SCRIPT, NOT ONLY WHERE A CASE PASSES today=.
# Every case driven through Dir.main() reaches M.main(), which asks _today()
# and prunes an entry once its day is before today minus one. On the real
# clock the 2026-11-05 fixtures were pruned from 2026-11-07 UTC on, and ten
# checks went red on every push that touched a .py file.
os.environ["MAPSEE_TODAY"] = TODAY.strftime("%Y%m%d")
VERSION = M.keyword_version()


def ev(fp, cat, day="2026-11-05", **extra):
    rec = {"fingerprint": fp, "name": f"Event {fp}", "category": cat,
           "start_local": f"{day}T19:00:00-07:00", "latitude": 47.6, "longitude": -122.3,
           "sources": [{"source": "meetup", "source_id": fp}],
           "source_details": {"organizer": {"type": "Organization", "name": "Group"}}}
    rec.update(extra)
    return rec


def store_doc(events, **extra):
    doc = {"_meta": {"version": 1, "updated": "2026-10-27T06:20:00Z", "count": len(events)},
           "events": events}
    doc.update(extra)
    return doc


class Dir:
    """A temp dir holding a store and (optionally) a cache, written the way
    EventStore.save writes a store (indent=2, ensure_ascii=False)."""

    def __init__(self, store, cache=None, raw_cache=None, raw_store=None):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = os.path.join(self.tmp.name, "meetup_events.json")
        self.cache = os.path.join(self.tmp.name, "meetup_category_cache.json")
        if raw_store is not None:
            Path(self.store).write_text(raw_store, encoding="utf-8")
        elif store is not None:
            Path(self.store).write_text(json.dumps(store, indent=2, ensure_ascii=False), encoding="utf-8")
        if raw_cache is not None:
            Path(self.cache).write_text(raw_cache, encoding="utf-8")
        elif cache is not None:
            Path(self.cache).write_text(json.dumps(cache), encoding="utf-8")

    def run(self, apply=True, today=TODAY):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            counts = M.hold_first_categories(self.store, self.cache, today=today, apply=apply)
        return counts, out.getvalue()

    def main(self, *flags):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = M.main(["--store", self.store, "--cache", self.cache, *flags])
        return code, out.getvalue()

    def stored(self):
        return load(self.store)

    def entries(self):
        return load(self.cache)["entries"]

    def bytes(self, which="store"):
        return Path(self.store if which == "store" else self.cache).read_bytes()

    def leftovers(self):
        return sorted(n for n in os.listdir(self.tmp.name) if n.endswith(".tmp"))

    def close(self):
        self.tmp.cleanup()


def cats(doc):
    return {r["fingerprint"]: r["category"] for r in doc["events"]}


print("the keyword list is the version")
kw = parse_keywords(DEFAULT_KEYWORDS)
check("  parse_keywords reads DEFAULT_KEYWORDS into 47 (keyword, category) pairs", len(kw), 47)
check("  a bare keyword is filed under community", parse_keywords("social, dance:party"),
      [("social", "community"), ("dance", "party")])
check("  the version is stable for the same list", M.keyword_version(kw), VERSION)
remapped = [(k, "fitness" if k == "dance" else c) for k, c in kw]
check("  remapping one keyword (dance:party -> dance:fitness) changes it",
      M.keyword_version(remapped) != VERSION, True)
check("  reordering the list changes it too (the first keyword decides)",
      M.keyword_version(list(reversed(kw))) != VERSION, True)

print("an event already held keeps its held category")
d = Dir(store_doc([ev("a", "party"), ev("b", "music"), ev("c", "community")]),
        {"version": VERSION, "entries": {"a": ["community", "2026-11-05"], "b": ["music", "2026-11-05"]}})
counts, out = d.run(apply=True)
check("  today's 'party' guess goes back to the held 'community'", cats(d.stored())["a"], "community")
check("  an event whose guess did not move is left as it is", cats(d.stored())["b"], "music")
check("  counts: 1 of 3 changed, 2 already held, 1 new",
      (counts["changed"], counts["events"], counts["held"], counts["new"]), (1, 3, 2, 1))
check("  the held entry is not overwritten by today's guess", d.entries()["a"], ["community", "2026-11-05"])
check("  the pair is tallied today->held", dict(counts["pairs"]), {("party", "community"): 1})
check("  the net per door says community gains the row party loses",
      dict(counts["net"]), {"community": 1, "party": -1})
check("  a second run over the held store changes nothing",
      d.run(apply=True)[0]["changed"], 0)
d.close()
d = Dir(store_doc([ev("a", None)]), {"version": VERSION, "entries": {"a": ["music", "2026-11-05"]}})
counts, _ = d.run(apply=True)
check("  a record whose guess went missing gets its held one", (cats(d.stored())["a"], dict(counts["pairs"])),
      ("music", {("-", "music"): 1}))
d.close()

print("a first sighting is added with today's category and its own date")
d = Dir(store_doc([ev("new", "fitness", day="2026-12-01"), ev("nodate", "arts", start_local=None),
                   ev("baddate", "arts", start_local="soon")]), None)
counts, _ = d.run(apply=True)
e = d.entries()
check("  the event's own local date", e["new"], ["fitness", "2026-12-01"])
check("  no date: held for the sweep's 90-day window", e["nodate"], ["arts", "2027-01-25"])
check("  an unreadable date: the same", e["baddate"], ["arts", "2027-01-25"])
check("  three new, nothing changed, the store not rewritten",
      (counts["new"], counts["changed"], counts["store_written"]), (3, 0, False))
check("  the cache carries the keyword version", load(d.cache)["version"], VERSION)
d.close()

print("past entries are pruned")
d = Dir(store_doc([ev("past-in-store", "music", day="2026-10-20")]),
        {"version": VERSION, "entries": {"old": ["music", "2026-10-25"], "yesterday": ["music", "2026-10-26"],
                                         "today": ["party", "2026-10-27"],
                                         "past-in-store": ["arts", "2026-10-20"]}})
counts, _ = d.run(apply=True)
e = d.entries()
check("  before yesterday (UTC) is pruned; yesterday and today are kept",
      sorted(e), ["today", "yesterday"])
check("  2 pruned", counts["pruned"], 2)
check("  a stored event already past is neither held nor added", cats(d.stored())["past-in-store"], "music")
d.close()

print("a keyword list that changed starts the cache empty")
d = Dir(store_doc([ev("a", "party")]),
        {"version": "an-older-list", "entries": {"a": ["community", "2026-11-05"]}})
counts, out = d.run(apply=True)
check("  the old guess is not applied", cats(d.stored())["a"], "party")
check("  today's guess becomes the held one", d.entries()["a"], ["party", "2026-11-05"])
check("  and the note says why", "keyword list changed" in out, True)
d.close()

print("a held category the keyword list does not have is ignored")
d = Dir(store_doc([ev("a", "party"), ev("b", "party")]),
        {"version": VERSION, "entries": {"a": ["nightclub", "2026-11-05"], "b": [7, "2026-11-05"]}})
counts, _ = d.run(apply=True)
check("  the store keeps today's guess", cats(d.stored()), {"a": "party", "b": "party"})
check("  and it replaces the entry", d.entries(), {"a": ["party", "2026-11-05"], "b": ["party", "2026-11-05"]})
d.close()
d = Dir(store_doc([ev("a", "not-a-door")]), None)
d.run(apply=True)
check("  a stored guess outside the list is never cached", d.entries(), {})
d.close()

print("a corrupt cache costs a day of flips, never the store")
d = Dir(store_doc([ev("a", "party")]), raw_cache='{"version": "' + VERSION + '", "entries": {"a": ["comm')
before = d.bytes()
code, out = d.main()
check("  exit 0", code, 0)
check("  the store is byte-identical", d.bytes(), before)
check("  a ::warning:: says so", "::warning::" in out, True)
check("  the cache is rewritten whole, with today's guess", d.entries(), {"a": ["party", "2026-11-05"]})
d.close()

d = Dir(store_doc([ev("a", "party")]), raw_cache='["not", "a", "cache"]')
before = d.bytes()
code, out = d.main()
check("  a cache that is not a cache: exit 0, store untouched, warned, rewritten",
      (code, d.bytes() == before, "::warning::" in out, d.entries()), (0, True, True, {"a": ["party", "2026-11-05"]}))
d.close()

print("a store that cannot be read is left alone, and the sync still runs")
d = Dir(None, {"version": VERSION, "entries": {"a": ["community", "2026-11-05"]}}, raw_store='{"events": [')
before, cache_before = d.bytes(), d.bytes("cache")
code, out = d.main()
check("  exit 0", code, 0)
check("  the store is byte-identical", d.bytes(), before)
check("  the cache is byte-identical", d.bytes("cache"), cache_before)
check("  a ::warning:: says so", "::warning::" in out, True)
d.close()
d = Dir(None, {"version": VERSION, "entries": {}}, raw_store='["not", "a", "store"]')
before = d.bytes()
code, out = d.main()
check("  a store that is not a store: exit 0, untouched, warned",
      (code, d.bytes() == before, "::warning::" in out), (0, True, True))
d.close()
d = Dir(None, None)
code, out = d.main()
check("  no store at all (the leg never swept): exit 0, no cache written",
      (code, os.path.exists(d.cache)), (0, False))
d.close()

print("only events[*].category is ever written")
tombs = [{"fingerprint": "called-off", "source": "meetup", "reason": "status:CANCELLED",
          "at": "2026-10-26T06:30:00Z"}]
reads = {"meetup": {"at": "2026-10-27T06:00:00Z", "count": 3}}
original = store_doc([ev("a", "party"), ev("b", "music"), {"name": "no fingerprint", "category": "party"},
                      ev("called-off", "food")],
                     tombstones=tombs, complete_reads=reads, seen=["s1", "s2"])
d = Dir(original, {"version": VERSION, "entries": {
    "a": ["community", "2026-11-05"], "called-off": ["arts", "2026-11-05"],
    "s1": ["music", "2026-11-05"]}})
counts, _ = d.run(apply=True)
after = d.stored()
check("  the store was rewritten (two categories changed)", (counts["store_written"], counts["changed"]), (True, 2))
check("  the same events, in the same order", [r.get("fingerprint") for r in after["events"]],
      [r.get("fingerprint") for r in original["events"]])
check("  tombstones unchanged", after["tombstones"], tombs)
check("  seen unchanged", after["seen"], ["s1", "s2"])
check("  complete_reads unchanged", after["complete_reads"], reads)
check("  _meta unchanged", after["_meta"], original["_meta"])
check("  a record with no fingerprint is untouched", after["events"][2], original["events"][2])
strip = lambda doc: [{k: v for k, v in r.items() if k != "category"} for r in doc["events"]]
check("  every other field of every record is unchanged", strip(after), strip(original))
check("  only the category lines differ from the file as EventStore wrote it",
      [ln for ln in d.bytes().decode().splitlines() if '"category"' not in ln],
      [ln for ln in json.dumps(original, indent=2, ensure_ascii=False).splitlines() if '"category"' not in ln])
check("  no key was added to the document", sorted(after), sorted(original))
d.close()

print("report only: the store is never rewritten, the cache is, and the line says what holding would do")
d = Dir(store_doc([ev("a", "party"), ev("b", "party"), ev("c", "fitness"), ev("d", "community"),
                   ev("e", "food")]),
        {"version": VERSION, "entries": {"a": ["community", "2026-11-05"], "b": ["community", "2026-11-05"],
                                         "c": ["party", "2026-11-05"], "d": ["community", "2026-11-05"],
                                         "gone": ["music", "2026-10-01"]}})
before, inode = d.bytes(), os.stat(d.store).st_ino
code, out = d.main("--report-only")
check("  exit 0", code, 0)
check("  the store is byte-identical", d.bytes(), before)
check("  and not even rewritten (os.replace would give it a new inode)", os.stat(d.store).st_ino, inode)
check("  the cache keeps the first sightings and gains today's new one",
      d.entries(), {"a": ["community", "2026-11-05"], "b": ["community", "2026-11-05"],
                    "c": ["party", "2026-11-05"], "d": ["community", "2026-11-05"],
                    "e": ["food", "2026-11-05"]})
line = next((ln for ln in out.splitlines() if ln.startswith("Meetup category cache (report only):")), "")
check("  the tally line", line,
      "Meetup category cache (report only): 3 of 5 stored events would have changed category "
      "(4 already held; top today->held: party->community x2, fitness->party x1); 1 new; 1 pruned; 5 entries")
check("  the net line, for reading a drift onto party, fitness or food",
      next((ln for ln in out.splitlines() if ln.startswith("  net rows per door")), ""),
      "  net rows per door if held: community +2, fitness -1, party -1")
d.close()
d = Dir(store_doc([ev("a", "party")]), {"version": VERSION, "entries": {"a": ["community", "2026-11-05"]}})
counts, _ = d.run(apply=False)
check("  report mode counts the change and writes only the cache",
      (counts["changed"], counts["store_written"], counts["cache_written"], cats(d.stored())),
      (1, False, True, {"a": "party"}))
code, out = d.main()
check("  without --report-only the same line says the store changed",
      next((ln for ln in out.splitlines() if ln.startswith("Meetup category cache")), ""),
      "Meetup category cache: 1 of 1 stored events changed category (1 already held; "
      "top today->held: party->community x1); 0 new; 0 pruned; 1 entries")
check("  and it did", cats(d.stored()), {"a": "community"})
d.close()

print("a write that dies halfway leaves the last good file")
real_dump = json.dump


def dies_halfway(obj, fh, **kw):
    fh.write('{"half": ')
    raise OSError("disk full")


for which in ("store", "cache"):
    d = Dir(store_doc([ev("a", "party"), ev("n", "music")]),
            {"version": VERSION, "entries": {"a": ["community", "2026-11-05"]}})
    store_before, cache_before = d.bytes(), d.bytes("cache")
    calls = []

    def fake(obj, fh, **kw):
        calls.append(fh.name)
        if fh.name.endswith(("meetup_events.json.tmp" if which == "store" else "meetup_category_cache.json.tmp")):
            return dies_halfway(obj, fh, **kw)
        return real_dump(obj, fh, **kw)

    json.dump = fake
    try:
        code, out = d.main()
    finally:
        json.dump = real_dump
    check(f"  dying in the {which} write: exit 0 and a ::warning::", (code, "::warning::" in out), (0, True))
    check(f"  dying in the {which} write: no .tmp file left behind", d.leftovers(), [])
    check(f"  dying in the {which} write: the cache is its last good copy", d.bytes("cache"), cache_before)
    if which == "store":
        check("  dying in the store write: the store is its last good copy", d.bytes(), store_before)
    else:
        check("  dying in the cache write: the store holds its category (written first)",
              cats(d.stored()), {"a": "community", "n": "music"})
    d.close()
d = Dir(store_doc([ev("a", "party")]), {"version": VERSION, "entries": {"a": ["community", "2026-11-05"]}})
d.run(apply=True)
check("  a write that succeeds leaves no .tmp file either", d.leftovers(), [])
d.close()

print("two legs, one cache: a border event gets the US pass's entry")
with tempfile.TemporaryDirectory() as tmp:
    us, intl = os.path.join(tmp, "meetup_events.json"), os.path.join(tmp, "meetup_global_events.json")
    cache = os.path.join(tmp, "meetup_category_cache.json")
    for path, doc in ((us, store_doc([ev("border", "community"), ev("sea", "music")])),
                      (intl, store_doc([ev("border", "party"), ev("yvr", "arts")]))):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2)
    with contextlib.redirect_stdout(io.StringIO()):
        M.hold_first_categories(us, cache, today=TODAY, apply=True)
        counts = M.hold_first_categories(intl, cache, today=TODAY, apply=True)
    check("  the international copy takes the US guess", cats(load(intl))["border"], "community")
    check("  the international pass counts it as already held", (counts["held"], counts["new"]), (1, 1))
    check("  one entry per fingerprint across both legs", sorted(load(cache)["entries"]),
          ["border", "sea", "yvr"])

print("MAPSEE_TODAY fixes today")
os.environ["MAPSEE_TODAY"] = "20261101"
try:
    check("  read from the environment", M._today(), date(2026, 11, 1))
finally:
    os.environ["MAPSEE_TODAY"] = TODAY.strftime("%Y%m%d")   # back to the pin, never the real clock

print()
if FAILS:
    print(f"{len(FAILS)} FAILED: " + "; ".join(FAILS))
    sys.exit(1)
print("all meetup category cache checks passed")
