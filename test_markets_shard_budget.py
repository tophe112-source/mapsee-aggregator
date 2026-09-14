"""Does the OSM market sweep fit its job, and does a sweep that runs out of time
still save what it swept?

WHY THIS EXISTS. markets_osm swept all 259 bboxes in one run and was cancelled
at its 330-minute job cap on 8 of the 9 sweep days from 2026-08-21 to
2026-09-11; the one finish (2026-08-28) took 314 minutes, ~73s a bbox with 47
retries. mapsee_ingest_markets.main() saves the store once, at the end, so each
cancelled run synced ZERO rows and the layer's 42-day horizon ran down.

Two fixes, both pinned here:
  * shard_by_weekday — the bboxes are split across the run weekdays by index,
    so Monday sweeps the even ones and Friday the odd ones (~157 min each), and
    together the two days still cover every bbox exactly once.
  * --max-minutes — checked before each bbox and each second-pass retry; when it
    runs out the sweep RETURNS, so the store is saved and the sync has a file.

No network: Overpass is a stub, and the clock is a fake that each POST advances.

Run: python test_markets_shard_budget.py
"""
import hashlib
import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mapsee_ingest_markets as M

M.OVERPASS_BACKOFF_S = 0

fails = []


def check(label, cond, detail=""):
    print(f"{'ok  ' if cond else 'FAIL'} {label}{'' if cond else '   ' + str(detail)}")
    if not cond:
        fails.append(label)


def bbox(i):
    return {"name": f"B{i}", "city": f"City{i}", "s": i, "w": i, "n": i + 0.5, "e": i + 0.5}


BBOXES = [bbox(i) for i in range(10)]


class _Resp:
    def __init__(self, status, els=None):
        self.status_code, self._els, self.headers = status, els or [], {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return {"elements": self._els}


class _Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def time(self):
        return self.now


class _Session:
    """Each POST costs `cost` fake seconds; bboxes named in `fail` answer 504."""

    def __init__(self, clock, cost=73.0, fail=()):
        self.clock, self.cost, self.fail = clock, cost, set(fail)
        self.calls = []
        self.headers = {}

    def post(self, endpoint, data=None, timeout=None):
        body = data.decode() if isinstance(data, bytes) else str(data)
        i = next(j for j, b in enumerate(BBOXES) if "{s},{w},{n},{e}".format(**b) in body)
        self.calls.append(f"B{i}")
        self.clock.now += self.cost
        if f"B{i}" in self.fail:
            return _Resp(504)
        return _Resp(200, [{"lat": i + 0.25, "lon": i + 0.25,
                            "tags": {"name": f"Market {i}", "opening_hours": "Sa 09:00-14:00"}}])


def run_with_clock(fn):
    """Swap the module's clock and sleep for fakes, and always put them back."""
    clock = _Clock()
    real_time, real_sleep = M.time.time, M.time.sleep
    M.time.time, M.time.sleep = clock.time, (lambda *_a, **_k: None)
    try:
        return fn(clock)
    finally:
        M.time.time, M.time.sleep = real_time, real_sleep


# --------------------------------------------------------------------------- #
# shard selection
# --------------------------------------------------------------------------- #
names = lambda bs: [b["name"] for b in bs]
mon = M.shard_bboxes(BBOXES, [0, 4], 0)
fri = M.shard_bboxes(BBOXES, [0, 4], 4)
check("Monday (weekday 0) sweeps the even-index bboxes",
      names(mon) == ["B0", "B2", "B4", "B6", "B8"], names(mon))
check("Friday (weekday 4) sweeps the odd-index bboxes",
      names(fri) == ["B1", "B3", "B5", "B7", "B9"], names(fri))
check("the two sweep days cover every bbox exactly once",
      sorted(names(mon) + names(fri), key=lambda n: int(n[1:])) == names(BBOXES))
check("a day that is not a run day (a --force backfill) takes every bbox",
      names(M.shard_bboxes(BBOXES, [0, 4], 2)) == names(BBOXES))
check("a single run weekday is not sharded — it would leave half the bboxes unswept forever",
      names(M.shard_bboxes(BBOXES, [0], 0)) == names(BBOXES))
check("an odd bbox count still covers everything across the two days",
      sorted(names(M.shard_bboxes(BBOXES[:7], [0, 4], 0) + M.shard_bboxes(BBOXES[:7], [0, 4], 4)))
      == sorted(names(BBOXES[:7])))

# load_overpass honours the flag, and only the flag.
def sweep_calls(source, weekday):
    def go(clock):
        s = _Session(clock)
        with redirect_stdout(io.StringIO()):
            M.load_overpass(s, source, weekday=weekday)
        return s.calls
    return run_with_clock(go)


src = {"bboxes": BBOXES, "pause_s": 0, "run_weekdays": [0, 4], "shard_by_weekday": True}
calls = sweep_calls(src, 4)
check("load_overpass with shard_by_weekday asks Overpass only for today's shard",
      calls == ["B1", "B3", "B5", "B7", "B9"], calls)
calls = sweep_calls(dict(src, shard_by_weekday=False), 4)
check("...and without the flag a run weekday still sweeps every bbox",
      len(calls) == 10, calls)

# The real config: the flag is on, and each day's share is about half of 259.
OSM = next((s for s in json.load(open("market_sources.json", encoding="utf-8"))
            if s.get("type") == "overpass"), None)
check("the OSM marketplaces source is configured", OSM is not None)
if OSM:
    days = OSM.get("run_weekdays") or []
    check("it shards by weekday", OSM.get("shard_by_weekday") is True)
    check("over exactly two run weekdays", len(days) == 2, days)
    shares = [len(M.shard_bboxes(OSM["bboxes"], days, d)) for d in days]
    check("each day sweeps at most half the bboxes (+1), not the whole 314-minute sweep",
          all(n <= len(OSM["bboxes"]) // 2 + 1 for n in shares) and sum(shares) == len(OSM["bboxes"]),
          f"{shares} of {len(OSM['bboxes'])}")

# --------------------------------------------------------------------------- #
# the budget
# --------------------------------------------------------------------------- #
# Each bbox costs 73 fake seconds; a 150-second budget admits the bboxes that
# START before it runs out (t=0, 73, 146) and none after.
src = {"bboxes": BBOXES, "pause_s": 0}
buf = io.StringIO()


def budgeted(clock):
    s = _Session(clock)
    with redirect_stdout(buf):
        out = M.load_overpass(s, src, deadline=clock.now + 150)
    return out, s


out, sess = run_with_clock(budgeted)
log = buf.getvalue()
check("the budget is checked before each bbox, so the sweep stops between bboxes",
      sess.calls == ["B0", "B1", "B2"], sess.calls)
check("...and RETURNS what it swept rather than raising or discarding it",
      [m["name"] for m in out] == ["Market 0", "Market 1", "Market 2"], [m["name"] for m in out])
check("...and says how many bboxes it did not reach, by count and by name",
      "7 of 10 bbox(es) not swept" in log and "B3" in log, log.strip()[-200:])
# GitHub only annotates a workflow command that STARTS the line; mid-line it is
# plain text nobody sees in the run summary.
check("...as a GitHub annotation: the line opens with ::warning::",
      any(l.startswith("::warning::") and "not swept" in l for l in log.splitlines()),
      log.strip()[-200:])

# No budget = the old behaviour, every bbox.
buf = io.StringIO()


def unbudgeted(clock):
    s = _Session(clock)
    with redirect_stdout(buf):
        out = M.load_overpass(s, src)
    return out, s


out, sess = run_with_clock(unbudgeted)
check("no deadline sweeps every bbox and warns about nothing",
      len(sess.calls) == 10 and "not swept" not in buf.getvalue(), sess.calls)

# The second pass checks the budget too: a bbox that failed the main pass is not
# retried once time is up, and is still counted as missing.
buf = io.StringIO()


def second_pass(clock):
    s = _Session(clock, cost=10.0, fail={"B1"})
    with redirect_stdout(buf):
        # 3 bboxes: B0 (t=0), B1 x4 attempts (t=10..40), B2 (t=50) -> t=60.
        # The deadline at t=55 lets the main pass finish and stops the retry.
        out = M.load_overpass(s, {"bboxes": BBOXES[:3], "pause_s": 0}, deadline=clock.now + 55)
    return out, s


out, sess = run_with_clock(second_pass)
log = buf.getvalue()
check("a failed bbox is not retried in the second pass once the budget is spent",
      sess.calls.count("B1") == 4, sess.calls)
check("...and is reported as still missing, not recovered",
      "recovered 0, still missing 1" in log and "B1" in log, log.strip()[-200:])

# --------------------------------------------------------------------------- #
# end to end: main() saves the store even when the budget ends the sweep
# --------------------------------------------------------------------------- #
# main() also rewrites geocode_cache.json (a committed repo file) on the way
# out, so that write is stubbed AND the file is hashed either side: a test that
# drives real machinery has to intercept every write it does.
cache_path = M.GEO_CACHE_PATH


def digest():
    try:
        return hashlib.sha256(open(cache_path, "rb").read()).hexdigest()
    except OSError:
        return None


before = digest()
real_save_cache, real_session = M._save_cache, M.requests.Session
tmp = tempfile.mkdtemp()
cfg_path, store_path = os.path.join(tmp, "cfg.json"), os.path.join(tmp, "store.json")
json.dump([{"name": "OSM test", "type": "overpass", "horizon_days": 14, "pause_s": 0,
            "bboxes": BBOXES}], open(cfg_path, "w", encoding="utf-8"))


def end_to_end(clock):
    s = _Session(clock, cost=30.0)
    M.requests.Session = lambda: s
    M._save_cache = lambda _c: None
    # --max-minutes 1: bboxes start at t=0 and t=30, the one at t=60 does not.
    with redirect_stdout(io.StringIO()):
        rc = M.main(["--config", cfg_path, "--store", store_path, "--type", "overpass",
                     "--force", "--max-minutes", "1"])
    return rc, s


try:
    rc, sess = run_with_clock(end_to_end)
finally:
    M._save_cache, M.requests.Session = real_save_cache, real_session
saved = json.load(open(store_path, encoding="utf-8")) if os.path.exists(store_path) else {}
cities = sorted({e.get("city") for e in saved.get("events", [])})
check("a sweep ended by --max-minutes still exits 0", rc == 0, rc)
check("...stopped at the budget (2 of 10 bboxes asked)", sess.calls == ["B0", "B1"], sess.calls)
check("...and SAVED the store with the markets it swept, so the sync has a file",
      cities == ["City0", "City1"], cities)
check("geocode_cache.json is untouched by this test", digest() == before)

print()
print(f"{'FAILURES: ' + ', '.join(fails) if fails else 'all checks passed'}")
sys.exit(1 if fails else 0)
