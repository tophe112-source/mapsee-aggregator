#!/usr/bin/env python3
"""The import-state cache may change what an --only-new sync READS, never what
it writes.

On 10-26 the only-new syncs a cache can serve read 461,619 ids (892.3 s) to keep
about 36,800 rows; the rest were stored rows with nothing to say. --state-cache
keeps the values a state read RETURNED and reads only what it must
(state_cache_must_read). Everything below is about the one bug class that does
real harm: a row the cache calls stored that is not, or a value in the file that
no read returned. A row skipped on a cache entry is dropped exactly as a stored,
unchanged row always was; a row that reaches upsert() was read this run (the
claimed guard), and a hidden or cancelled row is never written or lifted on a
cached value.

One printed line per case; exits non-zero on any failure. No request leaves this
process: PostgREST is a dictionary below.

    python test_sync_state_cache.py
"""
import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

os.environ.setdefault("MAPSEE_DB_WAIT_SECONDS", "0")

import yaml                                               # noqa: E402

import mapsee_supabase_sync as sync                       # noqa: E402

FAILS = []
ROOT = Path(__file__).resolve().parent


def check(label, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"  got {got!r}, want {want!r}"))
    if not ok:
        FAILS.append(label)


NOW = datetime.now(timezone.utc).replace(microsecond=0)
TODAY = NOW.date()
T = NOW + timedelta(days=4)
N = len(sync.STATE_FIELDS)


def stamp(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")


OLD = stamp(NOW - timedelta(hours=40))                    # liftable (UNCANCEL_AFTER is 30 h)
YOUNG = stamp(NOW - timedelta(hours=2))
TMP = tempfile.TemporaryDirectory()


def path(name):
    return os.path.join(TMP.name, name)


# --------------------------------------------------------------------------- #
class FakeDB:
    """PostgREST over a dict: the filters this pipeline sends, nothing more. It
    records every id a state read asked about and every value it answered."""

    def __init__(self, rows):
        self.rows = {k: dict(dict.fromkeys(("claimed_at",) + sync.STATE_FIELDS), **v) for k, v in rows.items()}
        self.asked, self.reads, self.patches = set(), [], []
        self.served = {}                                  # fp -> (STATE_FIELDS..., claimed) as answered
        self.headers = {}

    @staticmethod
    def _match(row, col, cond):
        v = row.get(col)
        if cond == "is.null":
            return v is None
        if cond == "not.is.null":
            return v is not None
        if cond.startswith("eq."):
            return str(v) == cond[3:]
        raise AssertionError(f"unexpected filter {col}={cond}")

    def _select(self, url):
        q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        ids = json.loads("[" + q.pop("external_id")[4:-1] + "]")
        q.pop("external_source")
        for k in ("select", "order", "limit", "offset"):
            q.pop(k, None)
        return ids, [i for i in sorted(ids) if i in self.rows
                     and all(self._match(self.rows[i], c, f) for c, f in q.items())]

    def get(self, url, headers=None, timeout=None):
        q = parse_qs(urlsplit(url).query)
        fields = q["select"][0].split(",")
        ids, hit = self._select(url)
        if fields[:2] == ["external_id", "claimed_at"]:   # fetch_import_state
            self.asked.update(ids)
            self.reads.append(sorted(ids))
        page = [{f: (i if f == "external_id" else self.rows[i].get(f)) for f in fields} for i in hit]
        if all(f in fields for f in sync.STATE_FIELDS):
            for row in page:
                self.served[row["external_id"]] = (tuple(row[f] for f in sync.STATE_FIELDS)
                                                   + (row["claimed_at"] is not None,))
        return type("R", (), {"status_code": 200, "json": lambda s: page,
                              "headers": {"Content-Range": f"0-{len(page) - 1}/{len(page)}" if page else "*/0"}})()

    def patch(self, url, headers=None, data=None, timeout=None):
        q = parse_qs(urlsplit(url).query)
        _, hit = self._select(url)
        body = json.loads(data)
        self.patches.append((sorted(hit), body))
        for i in hit:
            self.rows[i].update({k: v for k, v in body.items() if k != "external_id"})
        out = [{"external_id": i} for i in hit]
        return type("R", (), {"status_code": 200, "json": lambda s: out})()


def rec(fp, when=T, source="toronto-rec", **kw):
    return dict(dict(fingerprint=fp, name=f"Session {fp}", start_utc=stamp(when), latitude=43.7, longitude=-79.4,
                     coords_exact=True, sources=[{"source": source, "source_id": fp, "url": None}]), **kw)


WHOLE = {"toronto-rec": {"source": "toronto-rec", "id_prefix": "", "from": stamp(NOW - timedelta(hours=1)),
                         "to": stamp(NOW + timedelta(days=90)), "at": stamp(NOW)}}


def write_store(name, events, tombstones=(), complete=None):
    p = path(name)
    data = {"_meta": {}, "events": events}
    if tombstones:
        data["tombstones"] = list(tombstones)
    if complete:
        data["complete_reads"] = complete
    json.dump(data, open(p, "w", encoding="utf-8"))
    return p


def store_id(store, cache):
    return f"{os.path.basename(store)}|{os.path.basename(cache)}"


def entry(starts_at=None, ends_at=None, hidden_at=None, cancelled_at=None, claimed=False, days_ago=1):
    """A cache entry in STATE_FIELDS order, as a read of `days_ago` days back left it."""
    vals = dict(starts_at=starts_at, ends_at=ends_at, hidden_at=hidden_at, cancelled_at=cancelled_at)
    return [vals[f] for f in sync.STATE_FIELDS] + [claimed, (TODAY - timedelta(days=days_ago)).isoformat()]


def write_cache(cache, store, rows, version=None):
    json.dump({"version": version or sync.state_cache_version(), "store": store_id(store, cache),
               "saved": stamp(NOW), "rows": rows}, open(cache, "w", encoding="utf-8"))


def read_cache(cache):
    return json.load(open(cache, encoding="utf-8"))["rows"]


RUNS = []                                                 # (label, written, asked) of every main() run


def run_sync(label, store, db, flags, cache=None, upserted=None, terms=(), geocode=None, same=()):
    """(rows handed to upsert, the log, exit code). Every run is checked below
    for the claimed-guard invariant: nothing reaches upsert unread."""
    written = []

    def up(rows, url, key):
        written.extend(r["external_id"] for r in rows)
        if upserted:
            return upserted(rows)
        for r in rows:                                    # the write lands: the next read sees it
            db.rows.setdefault(r["external_id"], dict.fromkeys(("claimed_at",) + sync.STATE_FIELDS)).update(
                starts_at=r.get("starts_at"), ends_at=r.get("ends_at"))
        return len(rows), 0, 0
    argv = ["sync", "--store", store, "--skip-unchanged"] + flags + (["--state-cache", cache] if cache else [])
    code, out = None, io.StringIO()
    with patch.dict(os.environ, {"MAPSEE_HOST_PROFILE_ID": "host", "SUPABASE_URL": "https://db.example",
                                 "SUPABASE_SERVICE_ROLE_KEY": "unused-test-key"}), \
         patch("sys.argv", argv), patch("requests.Session", return_value=db), \
         patch.object(sync, "batch_geocode", side_effect=geocode or (lambda *a, **k: {})), \
         patch.object(sync, "_enrich_music_links"), \
         patch.object(sync, "load_blocklist", return_value=list(terms)), \
         patch.object(sync, "unchanged_ids", side_effect=lambda s, u, k, rows: {r["external_id"] for r in rows
                                                                                if r["external_id"] in same}), \
         patch.object(sync, "_patch_sleep"), \
         patch.object(sync, "upsert", side_effect=up), redirect_stdout(out):
        try:
            sync.main()
        except SystemExit as e:
            code = e.code
    RUNS.append((label, list(written), set(db.asked)))
    return written, out.getvalue(), code


def only_read_values(before, after, served):
    """Entries of `after` that are neither carried unchanged from `before` nor
    exactly what this run's reads answered (the bug class: a value never read)."""
    bad = {}
    for fp, e in after.items():
        if before.get(fp) == e:
            continue
        if fp in served and tuple(e[:N]) + (e[N],) == served[fp] and e[N + 1] == TODAY.isoformat():
            continue
        bad[fp] = e
    return bad


# --------------------------------------------------------------------------- #
print("A warm cache: exactly the must-read set is read, and what is written does not change")
STORE = write_store("warm.json", [
    rec("same"), rec("moved_time"), rec("new"), rec("detail", source_details={"free": True}),
    rec("back"), rec("young"), rec("stale"), rec("week"), rec("takedown"), rec("claimed_now"),
    rec("claimed_moved"), rec("ours_hid_again")], complete=WHOLE)
DB_ROWS = {"same": {"starts_at": stamp(T)}, "moved_time": {"starts_at": stamp(T - timedelta(hours=1))},
           "detail": {"starts_at": stamp(T)},
           "back": {"starts_at": stamp(T), "cancelled_at": OLD, "hidden_at": OLD},
           "young": {"starts_at": stamp(T), "cancelled_at": YOUNG, "hidden_at": YOUNG},
           "stale": {"starts_at": stamp(T)}, "week": {"starts_at": stamp(T)},
           "takedown": {"starts_at": stamp(T), "hidden_at": OLD},
           "claimed_now": {"starts_at": stamp(T), "claimed_at": "2026-10-01"},
           "claimed_moved": {"starts_at": stamp(T - timedelta(hours=1)), "claimed_at": "2026-10-01"},
           # ours, then taken down on top: hidden_at moved, so the stamps are no longer equal
           "ours_hid_again": {"starts_at": stamp(T), "cancelled_at": OLD, "hidden_at": YOUNG}}
WARM = {"same": entry(stamp(T)), "moved_time": entry(stamp(T - timedelta(hours=1))),
        "detail": entry(stamp(T)), "back": entry(stamp(T), cancelled_at=OLD, hidden_at=OLD),
        "young": entry(stamp(T), cancelled_at=YOUNG, hidden_at=YOUNG),
        "stale": entry(stamp(T), days_ago=9), "week": entry(stamp(T), days_ago=7),
        "takedown": entry(stamp(T), hidden_at=OLD), "claimed_now": entry(stamp(T)),
        "claimed_moved": entry(stamp(T - timedelta(hours=1))),
        "ours_hid_again": entry(stamp(T), cancelled_at=OLD, hidden_at=OLD)}
MUST = ["back", "claimed_moved", "detail", "moved_time", "new", "ours_hid_again", "stale", "young"]

with patch.object(sync, "state_cache_sampled", return_value=False):
    plain_db = FakeDB(DB_ROWS)
    plain, _, _ = run_sync("no cache", STORE, plain_db, ["--only-new"])
    cache = path("warm_cache.json")
    write_cache(cache, STORE, WARM)
    db = FakeDB(DB_ROWS)
    written, log, code = run_sync("warm", STORE, db, ["--only-new"], cache)
check("the state read asks for the must-read set only: absent, moved time, exact detail, our equal "
      "stamps (any age), expired, re-checked claimed/time, a take-down on top of ours",
      db.reads, [MUST])
check("  ...where a run without the cache read every fingerprint", plain_db.reads,
      [sorted(r["fingerprint"] for r in json.load(open(STORE))["events"])])
check("  ...and the cache writes exactly what the full read wrote", sorted(written), sorted(plain))
check("  ...which is the new row, the time change, the detail refresh and the relist",
      sorted(written), ["back", "detail", "moved_time", "new"])
check("  ...lifting the same row, with both exact stamps in the filter",
      (db.patches, db.rows["back"]["hidden_at"]),
      ([(["back"], {"cancelled_at": None, "hidden_at": None})], None))
check("a take-down (hidden_at alone) is never lifted or written, cached or read",
      (db.rows["takedown"]["hidden_at"], db.rows["ours_hid_again"]["hidden_at"],
       "takedown" in written or "ours_hid_again" in written), (OLD, YOUNG, False))
check("a row claimed since the cache read it is never written", ("claimed_now" in written,
                                                                   "claimed_moved" in written), (False, False))
check("the log counts the skip", "State cache: 4 of 12 stored row(s) skipped unread" in log, True)
saved = read_cache(cache)
check("the file keeps what was skipped, unchanged, and what was read and not written, freshly dated",
      {fp: e[N + 1] for fp, e in saved.items()},
      {"same": WARM["same"][N + 1], "week": WARM["week"][N + 1], "takedown": WARM["takedown"][N + 1],
       "claimed_now": WARM["claimed_now"][N + 1], "young": TODAY.isoformat(), "stale": TODAY.isoformat(),
       "claimed_moved": TODAY.isoformat(), "ours_hid_again": TODAY.isoformat()})
check("  ...holding the read's own values for a row it re-read (the take-down on top, the claim)",
      (saved["ours_hid_again"][:N], saved["claimed_moved"][N]),
      ([dict(starts_at=stamp(T), ends_at=None, hidden_at=YOUNG, cancelled_at=OLD)[f] for f in sync.STATE_FIELDS],
       True))
check("  ...and never a value no read returned", only_read_values(WARM, saved, db.served), {})
check("  ...nor anything written this run, whatever the read said", sorted(set(written) & set(saved)), [])

print()
print("Tomorrow, from that file: only what changed since is read")
db2 = FakeDB({k: v for k, v in db.rows.items()})
with patch.object(sync, "state_cache_sampled", return_value=False):
    written2, log2, _ = run_sync("warm, day 2", STORE, db2, ["--only-new"], cache)
check("the rows written yesterday, the exact-detail row, our young cancellation and the claimed row "
      "whose owner moved it (its time still differs from the source's, as every day today) are read "
      "again; the rest are not", db2.reads, [["back", "claimed_moved", "detail", "moved_time", "new", "young"]])
check("  ...and only the detail row is written (yesterday's writes now match their source)",
      sorted(written2), ["detail"])

print()
print("A row this run cancels leaves the file, so a relist is read and lifted")
cstore = write_store("cancel.json", [rec("x"), rec("y")], complete=WHOLE)
ccache = path("cancel_cache.json")
cdb = FakeDB({"x": {"starts_at": stamp(T)}, "y": {"starts_at": stamp(T)}})
run_sync("cancel day 0", cstore, cdb, ["--only-new"], ccache)
check("day 0: both stored rows are cached", sorted(read_cache(ccache)), ["x", "y"])
cstore = write_store("cancel.json", [rec("y")], tombstones=[{"fingerprint": "x", "source": "toronto-rec"}],
                     complete=WHOLE)
cdb.reads.clear()
with patch.object(sync, "state_cache_sampled", return_value=False):
    _, clog, _ = run_sync("cancel day 1", cstore, cdb, ["--only-new"], ccache)
check("  ...reading nothing: y is in the file, and x is not listed", cdb.reads, [])
check("day 1: the tombstone cancels x with two equal stamps",
      (cdb.rows["x"]["cancelled_at"] is not None, cdb.rows["x"]["cancelled_at"] == cdb.rows["x"]["hidden_at"]),
      (True, True))
check("  ...and x is out of the file, y carried", sorted(read_cache(ccache)), ["y"])
cdb.rows["x"].update(cancelled_at=OLD, hidden_at=OLD)    # a day and a half later
cstore = write_store("cancel.json", [rec("x"), rec("y")], complete=WHOLE)
cdb.reads.clear()
with patch.object(sync, "state_cache_sampled", return_value=False):
    cwritten, _, _ = run_sync("cancel day 2", cstore, cdb, ["--only-new"], ccache)
check("day 2: listed live by a complete read, x is READ (not taken from the file) and lifted",
      (cdb.reads, cdb.rows["x"]["cancelled_at"], cdb.rows["x"]["hidden_at"], "x" in cwritten),
      ([["x"]], None, None, True))

print()
print("A drifting sample means today's full read")
dstore = write_store("drift.json", [rec("a"), rec("b"), rec("c"), rec("d"), rec("e")])
drows = {k: {"starts_at": stamp(T)} for k in "abcde"}
dcache = path("drift_cache.json")
write_cache(dcache, dstore, {k: entry(stamp(T)) for k in "abcde"})
with patch.object(sync, "state_cache_sampled", side_effect=lambda fp, now: fp in ("a", "b", "c")):
    ddb = FakeDB(drows)
    _, dlog, _ = run_sync("sample agrees", dstore, ddb, ["--only-new"], dcache)
    check("a sample that agrees with the file: only the sample is read", ddb.reads, [["a", "b", "c"]])
    check("  ...and the log says 0 of 3 differ", "0 of 3 sampled rows differ from the cache, 0 of them" in dlog, True)
    write_cache(dcache, dstore, {k: entry(stamp(T)) for k in "abcde"})
    drows["b"] = {"starts_at": stamp(T), "claimed_at": "2026-10-20"}     # claimed since
    drows["e"] = {"starts_at": stamp(T - timedelta(hours=1))}            # moved by another store's sync
    ddb = FakeDB(drows)
    dwritten, dlog, _ = run_sync("sample drifts", dstore, ddb, ["--only-new"], dcache)
check("1 of 3 sampled rows differing (over 1%): the rest are read too, as a run without the file would",
      ddb.reads, [["a", "b", "c"], ["d", "e"]])
check("  ...with a warning", "::warning::State cache: 1 of 3 sampled rows differ" in dlog, True)
check("  ...so the row another sync moved is written again, as it is today", dwritten, ["e"])
check("  ...and the file is rebuilt from that read alone", (sorted(read_cache(dcache)), read_cache(dcache)["b"][N]),
      (["a", "b", "c", "d"], True))

print()
print("A file this sync cannot trust is today's full read, call for call")
ustore = write_store("untrusted.json", [rec("p"), rec("q"), rec("r")])
urows = {"p": {"starts_at": stamp(T)}, "q": {"starts_at": stamp(T - timedelta(hours=3))}}
base_db = FakeDB(urows)
base_written, _, _ = run_sync("untrusted baseline", ustore, base_db, ["--only-new"])
ucache = path("untrusted_cache.json")
good = {k: entry(stamp(T)) for k in "pqr"}
cases = {
    "missing": None,
    "corrupt": "{not json",
    "an older version": dict(version="0:starts_at"),
    "another store's": dict(store="feeds_events.json|sync_state_ics.json"),
    "a malformed entry": dict(rows={"p": ["2026-10-27T00:00:00+00:00", None]}),
}
for name, spoil in cases.items():
    if os.path.exists(ucache):
        os.remove(ucache)
    if isinstance(spoil, str):
        open(ucache, "w").write(spoil)
    elif isinstance(spoil, dict):
        body = {"version": sync.state_cache_version(), "store": store_id(ustore, ucache), "saved": stamp(NOW),
                "rows": good}
        body.update(spoil)
        json.dump(body, open(ucache, "w"))
    udb = FakeDB(urows)
    uwritten, ulog, _ = run_sync(f"untrusted {name}", ustore, udb, ["--only-new"], ucache)
    check(f"{name} file: the same reads and writes as a run without one",
          (udb.reads, sorted(uwritten)), (base_db.reads, sorted(base_written)))
    check(f"  ...and a good file is written from that read",
          sorted(sync.load_state_cache(ucache, store_id(ustore, ucache))[0] or {}), ["p"])
check("a version is the cache's own number and STATE_FIELDS, in order",
      sync.state_cache_version(), f"{sync.STATE_CACHE_VERSION}:" + ",".join(sync.STATE_FIELDS))
with patch.object(sync, "STATE_FIELDS", sync.STATE_FIELDS + ("price_text",)):
    refused, why = sync.load_state_cache(ucache, store_id(ustore, ucache))
check("a column added to the state read refuses yesterday's file, with no number to remember to bump",
      (refused, "this sync reads" in why), (None, True))

print()
print("Without --only-new the file is not read, and is rebuilt from the full read")
rstore = write_store("refresh.json", [rec("w1"), rec("w2"), rec("same1"), rec("same2"), rec("newr")])
rrows = {"w1": {"starts_at": stamp(T)}, "w2": {"starts_at": stamp(T)}, "same1": {"starts_at": stamp(T)},
         "same2": {"starts_at": stamp(T)}}
rbase = FakeDB(rrows)
run_sync("refresh baseline", rstore, rbase, [], same={"same1", "same2"})
rcache = path("refresh_cache.json")
write_cache(rcache, rstore, {k: entry(stamp(T)) for k in ("w1", "w2", "same1", "same2")}
            | {"ghost": entry(stamp(T))})
rdb = FakeDB(rrows)
rwritten, rlog, _ = run_sync("refresh", rstore, rdb, [], rcache, same={"same1", "same2"})
check("a refresh reads every id, exactly as without the file", rdb.reads, rbase.reads)
check("  ...writes what differs and what is new", sorted(rwritten), ["newr", "w1", "w2"])
check("  ...and keeps only what it read and did not write: nothing carried, not even a row outside its store",
      sorted(read_cache(rcache)), ["same1", "same2"])
check("  ...each from the read itself", only_read_values({}, read_cache(rcache), rdb.served), {})

print()
print("Nothing written, held, refused or moderated this run enters the file (a refresh, where it would)")
estore = write_store("evict.json", [rec("kept"), rec("refused"), rec("rude", name="Session rude words"),
                                    rec("census", coords_exact=False, address="1 Main St", city="Seattle",
                                        region="WA")])
erows = {k: {"starts_at": stamp(T)} for k in ("kept", "refused", "rude", "census")}


def held_census(_session, addrs, **_kw):
    got = sync.CensusAnswers()
    got.held = frozenset(addrs)
    return got


for outcome, result in (("rejected with a 400", lambda rows: (0, len(rows), 0)),
                        ("LOST to an unanswering database", lambda rows: (0, 0, len(rows)))):
    ecache = path(f"evict_{outcome[:4]}.json")
    edb = FakeDB(erows)
    ewritten, elog, ecode = run_sync(f"evict {outcome}", estore, edb, [], ecache, upserted=result,
                                     terms=["rude"], geocode=held_census, same={"kept"})
    got = read_cache(ecache)
    check(f"a row {outcome} is not kept; the identical row is",
          (ewritten, sorted(got)), (["refused"], ["kept"]))
    check("  ...nor the row moderation dropped, nor the stored row Census held back",
          ("rude" in got, "census" in got, "Census hold: 1 stored row(s) held back" in elog,
           "Moderation pre-filter: dropped 1" in elog), (False, False, True, True))
check("  ...and a LOST run still fails, after the file is written",
      "never reached Supabase" in str(ecode) and "State cache: 1 row(s) saved" in elog, True)

print()
print("A partial store (a feed group's checkpoint) carries the rest of the file forward")
pstore = write_store("partial.json", [rec("p1")])
pcache = path("partial_cache.json")
write_cache(pcache, pstore, {"p1": entry(stamp(T)), "later": entry(stamp(T), days_ago=2),
                             "gone": entry(stamp(T), days_ago=9)})
pdb = FakeDB({"p1": {"starts_at": stamp(T)}, "later": {"starts_at": stamp(T)}})
with patch.object(sync, "state_cache_sampled", return_value=False):
    run_sync("partial", pstore, pdb, ["--only-new"], pcache)
check("the final sync's rows survive the checkpoint, with their own read date; an expired one does not",
      {fp: e[N + 1] for fp, e in read_cache(pcache).items()},
      {"p1": (TODAY - timedelta(days=1)).isoformat(), "later": (TODAY - timedelta(days=2)).isoformat()})
check("  ...and the checkpoint read nothing", pdb.reads, [])

print()
print("--dry-run never touches the file")
dry = path("dry_cache.json")
_, _, _ = run_sync("dry missing", pstore, FakeDB({}), ["--only-new", "--dry-run"], dry)
check("a missing file is not created", os.path.exists(dry), False)
write_cache(dry, pstore, {"p1": entry(stamp(T))})
before = open(dry, "rb").read()
opened = []
real_open = open


def spy_open(file, *a, **kw):
    opened.append(str(file))
    return real_open(file, *a, **kw)


with patch("builtins.open", side_effect=spy_open):
    run_sync("dry existing", pstore, FakeDB({}), ["--only-new", "--dry-run"], dry)
check("an existing one is neither opened nor changed", (dry in opened, open(dry, "rb").read() == before),
      (False, True))

print()
print("The must-read rules, one at a time")
when = NOW
r0 = rec("z")
ok = entry(stamp(T))
check("an entry the TTL has not reached, with nothing to say: skipped", sync.state_cache_must_read(r0, ok, when), False)
check("no entry: read", sync.state_cache_must_read(r0, None, when), True)
check("read 9 days ago: read again (TTL)",
      sync.state_cache_must_read(r0, entry(stamp(T), days_ago=9), when), True)
check("re-keyed this run (moved) or held: read", (sync.state_cache_must_read(r0, ok, when, moved={"z"}),
                                                  sync.state_cache_must_read(r0, ok, when, held={"z"})), (True, True))
check("exact source_details: read", sync.state_cache_must_read(rec("z", source_details={"a": 1}), ok, when), True)
check("our two equal stamps, however young: read (it is liftable 30 h on)",
      sync.state_cache_must_read(r0, entry(stamp(T), cancelled_at=YOUNG, hidden_at=YOUNG), when), True)
check("  ...the same instant written two ways is still equal",
      sync.state_cache_must_read(r0, entry(stamp(T), cancelled_at=YOUNG,
                                           hidden_at=YOUNG.replace("+00:00", "Z")), when), True)
check("a source time that moved against the cached one: read",
      sync.state_cache_must_read(rec("z", when=T + timedelta(minutes=30)), ok, when), True)
check("a take-down (hidden_at alone): skipped, exactly as a stored row is today",
      sync.state_cache_must_read(r0, entry(stamp(T), hidden_at=OLD), when), False)
with patch.object(sync, "keep_reason", return_value=None):
    check("the explicit rules stand without the keep_reason guard: source_details, equal stamps, a moved "
          "time, the TTL, re-keyed and held",
          [sync.state_cache_must_read(rec("z", source_details={"a": 1}), ok, when),
           sync.state_cache_must_read(r0, entry(stamp(T), cancelled_at=OLD, hidden_at=OLD), when),
           sync.state_cache_must_read(rec("z", when=T + timedelta(minutes=30)), ok, when),
           sync.state_cache_must_read(r0, entry(stamp(T), days_ago=9), when),
           sync.state_cache_must_read(r0, ok, when, moved={"z"}), sync.state_cache_must_read(r0, ok, when, held={"z"}),
           sync.state_cache_must_read(r0, ok, when)], [True] * 6 + [False])
real_reason = sync.keep_reason
with patch.object(sync, "keep_reason", side_effect=lambda r, *a, **k: "rule" if r["name"] == "NEW RULE"
                  else real_reason(r, *a, **k)):
    check("a rule added to keep_reason later is honoured by the cache unprompted",
          (sync.state_cache_must_read(rec("z", name="NEW RULE"), ok, when),
           sync.state_cache_must_read(rec("z"), ok, when)), (True, False))
fps = [f"{i:040x}" for i in range(20000)]
day = datetime(2026, 10, 27, tzinfo=timezone.utc)
picked = [fp for fp in fps if sync.state_cache_sampled(fp, day)]
check("the sample is about 1% and deterministic", (150 <= len(picked) <= 250,
                                                   picked == [fp for fp in fps if sync.state_cache_sampled(fp, day)]),
      (True, True))
check("  ...and a different 1% the next day",
      len(set(picked) & {fp for fp in fps if sync.state_cache_sampled(fp, day + timedelta(days=1))}), 0)
check("next_state_cache adds only a read's values, drops what a read did not find, and evicts",
      sync.next_state_cache({"kept": ok, "gone": ok, "evicted": ok, "old": entry(stamp(T), days_ago=9)},
                            {"read": False, "evicted": False}, {"read": {"starts_at": "S", "ends_at": None,
                                                                         "hidden_at": None, "cancelled_at": None},
                                                                "evicted": {"starts_at": "E", "ends_at": None,
                                                                            "hidden_at": None, "cancelled_at": None}},
                            {"read", "gone", "evicted", "unread_new"}, {"evicted"}, NOW),
      {"kept": ok, "read": [dict(starts_at="S", ends_at=None, hidden_at=None, cancelled_at=None)[f]
                            for f in sync.STATE_FIELDS] + [False, TODAY.isoformat()]})

print()
print("THE INVARIANT, over every run above: nothing reached upsert() unread")
unread = {label: sorted(set(written) - asked) for label, written, asked in RUNS if set(written) - asked}
check(f"every row handed to upsert had its fingerprint in a state read of its own run ({len(RUNS)} runs)",
      unread, {})

print()
print("The workflow: a pair of its own per job, after the others, the flag after $ABSENT")
wf = yaml.safe_load((ROOT / ".github/workflows/aggregate-events.yml").read_text(encoding="utf-8"))
FILES = {"ticketmaster": {"mapsee_events.json": "sync_state_ticketmaster.json",
                          "global_events.json": "sync_state_global.json"},
         "feeds": {"feeds_events.json": "sync_state_${{ matrix.group }}.json"},
         "races": {"races_events.json": "sync_state_races.json"},
         "extra_sources": {"extra_events.json": "sync_state_extra.json",
                           "eventbrite_events.json": "sync_state_eventbrite.json"}}
for job, stores in FILES.items():
    steps = wf["jobs"][job]["steps"]
    caches = [(i, s) for i, s in enumerate(steps) if str(s.get("uses", "")).startswith("actions/cache")]
    mine = [(i, s) for i, s in caches if s["with"]["key"].startswith("sync-state-")]
    others = [i for i, s in caches if not s["with"]["key"].startswith("sync-state-")]
    kinds = [s["uses"] for _, s in mine]
    check(f"{job}: one restore and one save", kinds, ["actions/cache/restore@v4", "actions/cache/save@v4"])
    (ri, restore), (si, save) = mine
    prefix = f"sync-state-{'feeds-${{ matrix.group }}' if job == 'feeds' else job.split('_')[0]}-"
    check(f"  ...keyed {prefix}<run>-<attempt>, restoring from the prefix, saved always()",
          (restore["with"]["key"], save["with"]["key"], restore["with"]["restore-keys"].strip(), save.get("if")),
          (prefix + "${{ github.run_id }}-${{ github.run_attempt }}",) * 2 + (prefix, "always()"))
    check("  ...after every existing restore and save (their path lists, and versions, untouched)",
          (all(ri > i for i, s in caches if s["uses"] != "actions/cache/save@v4" and i in others),
           all(si > i for i in others)), (True, True))
    check("  ...and no existing pair names a state file (its version would change, restoring none of it)",
          [s["name"] for i, s in caches if i in others and "sync_state_" in str(s["with"].get("path", ""))], [])
    check("  ...one file per store, the same in both", (set(restore["with"]["path"].strip().splitlines()),
                                                         set(save["with"]["path"].strip().splitlines())),
          (set(stores.values()), set(stores.values())))
    calls = [ln.strip() for s in steps for ln in s.get("run", "").splitlines()
             if "mapsee_supabase_sync.py" in ln and "$ONLY_NEW" in ln]
    for store, file in stores.items():
        got = [c for c in calls if f"--store {store} " in c]
        check(f"  ...every --only-new sync of {store} passes --state-cache {file}, after $ONLY_NEW and $ABSENT",
              all(c.split("$ONLY_NEW", 1)[1].split("||")[0].strip().replace("$ABSENT", "").strip()
                  == f"--state-cache {file}" for c in got) and bool(got), True)
feeds = wf["jobs"]["feeds"]["steps"]
check("feeds: the checkpoint and the final sync share the group's file",
      [s["id"] for s in feeds if "--state-cache sync_state_${{ matrix.group }}.json" in s.get("run", "")],
      ["checkpoint", "sync"])
check("  ...the final sync keeps the string the absence contract pins",
      "--skip-unchanged $ONLY_NEW $ABSENT --state-cache" in next(s for s in feeds if s.get("id") == "sync")["run"],
      True)
text = (ROOT / ".github/workflows/aggregate-events.yml").read_text(encoding="utf-8")
flagged = [ln.strip() for ln in text.splitlines() if "--state-cache" in ln and not ln.strip().startswith("#")]
check("no sync without --only-new carries it (osm_market, facility_hours, recital), nor Meetup or OpenActive, "
      "whose every record has exact source_details",
      [ln for ln in flagged if "$ONLY_NEW" not in ln or any(s in ln for s in ("meetup", "openactive", "osm_market",
                                                                              "facility_hours", "recital"))], [])
for other in sorted(set(wf["jobs"]) - set(FILES)):
    check(f"{other}: no state cache", any("sync-state-" in str(s.get("with", {}).get("key", ""))
                                          for s in wf["jobs"][other].get("steps", [])), False)
others = [p for p in (ROOT / ".github/workflows").glob("*.yml") if p.name != "aggregate-events.yml"]
check("no other workflow passes it", [p.name for p in others if any(
    "--state-cache" in ln and not ln.strip().startswith("#") for ln in p.read_text(encoding="utf-8").splitlines())],
    [])

print()
print(f"{len(FAILS)} failed")
sys.exit(1 if FAILS else 0)
