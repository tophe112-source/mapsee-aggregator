#!/usr/bin/env python3
"""
test_sync_transport_loss.py — a read timeout is a hole, not the end of the run.

THE FAILURE THIS PREVENTS. _post_retry inspected resp.status_code and nothing
else, so it retried the batch when Supabase SAID "503" and let the batch where
Supabase said NOTHING raise straight out of upsert() and kill the process. Those
are the same transient condition — Postgres busy — separated only by whether the
answer arrived inside the 30s read timeout.

On 2026-08-29 that one gap failed two workflows from the same line:

  * "Aggregate public events" — the Meetup sweep spent 4h50m collecting the
    international metros and threw all of them away syncing, having already
    banked the US leg because aggregate-events.yml knew "a later timeout must
    not discard it" at the JOB boundary and not inside the sync.
  * "OSM second-hand shops" — 34m, same ReadTimeout, same line.

Both stacks ended:
    upsert -> _post_retry -> _post -> requests.exceptions.ReadTimeout

Run: python test_sync_transport_loss.py
"""
import json
import sys
import types

import requests as _real_requests            # for its REAL exception classes
import mapsee_supabase_sync as S


class Resp:
    def __init__(self, code, body=None):
        self.status_code = code
        self._body = body or {}
        self.text = json.dumps(self._body)

    def json(self):
        return self._body


def timing_out(fail_batches, exc=None):
    """A PostgREST that times out on the batches whose 0-based index is listed.

    Keyed off the ROWS in the batch, not a call counter, so a batch that is
    unreachable stays unreachable across all three of _post_retry's attempts —
    a counter would silently "recover" on retry and test nothing.

    Raises the REAL requests exception, because that is what production raises
    and what the fix must therefore catch.
    """
    exc = exc or _real_requests.exceptions.ReadTimeout("Read timed out. (read timeout=30)")
    calls = {"posts": 0, "rows_seen": 0}

    def post(url, headers=None, data=None, timeout=None):
        calls["posts"] += 1
        batch = json.loads(data)
        # "row 173" -> 173 -> batch 3. Stable under retry and row-by-row.
        first = int(batch[0]["title"].split()[1])
        if first // 50 in fail_batches:
            raise exc
        calls["rows_seen"] += len(batch)
        return Resp(201)

    mod = types.ModuleType("requests")
    mod.post = post
    return mod, calls


def always_timing_out():
    calls = {"posts": 0}

    def post(url, headers=None, data=None, timeout=None):
        calls["posts"] += 1
        raise _real_requests.exceptions.ReadTimeout("Read timed out. (read timeout=30)")

    mod = types.ModuleType("requests")
    mod.post = post
    return mod, calls


def recovering(fail_first_n):
    """Times out `fail_first_n` times, then answers. Proves the RETRY works."""
    calls = {"posts": 0}

    def post(url, headers=None, data=None, timeout=None):
        calls["posts"] += 1
        if calls["posts"] <= fail_first_n:
            raise _real_requests.exceptions.ConnectTimeout("connect timed out")
        return Resp(201)

    mod = types.ModuleType("requests")
    mod.post = post
    return mod, calls


def main():
    checks = []
    # The backoff is 2/4/8s: recorded, not waited, or case 3 alone takes a minute.
    import time
    slept = []
    time.sleep = lambda s: slept.append(s)
    rows = [{"title": f"row {i}", "lat": 1.0, "lon": 2.0} for i in range(500)]   # 10 batches

    # ---- 1. THE REGRESSION. One unreachable batch must not kill the other nine.
    fake, calls = timing_out({3})
    sys.modules["requests"] = fake
    try:
        sent, skipped, lost = S.upsert([dict(r) for r in rows], "https://x.test", "k")
        crashed = False
    except _real_requests.exceptions.RequestException:
        crashed, sent, skipped, lost = True, 0, 0, 0
    checks.append((not crashed,
                   "a read timeout no longer escapes upsert() and kills the run"))
    checks.append((sent == 450 and lost == 50,
                   f"...the nine reachable batches still land, the one hole is counted "
                   f"(sent={sent}, lost={lost})"))
    checks.append((skipped == 0,
                   f"...and a LOST row is not reported as a SKIPPED one (skipped={skipped})"))

    # ---- 2. THE RETRY ITSELF. A blip that clears on attempt 2 costs nothing.
    fake, calls = recovering(1)
    sys.modules["requests"] = fake
    sent, skipped, lost = S.upsert([{"title": "one"}], "https://x.test", "k")
    checks.append((sent == 1 and lost == 0 and calls["posts"] == 2,
                   f"a transport blip is RETRIED, not counted as a loss "
                   f"(sent={sent}, lost={lost}, posts={calls['posts']})"))

    # ---- 3. THE CIRCUIT BREAKER. A real outage must not burn the job's clock.
    # 10 batches x 3 attempts = 30 posts if it never gives up. It must stop at
    # GIVE_UP_AFTER=5 consecutive misses, i.e. 15.
    fake, calls = always_timing_out()
    sys.modules["requests"] = fake
    sent, skipped, lost = S.upsert([dict(r) for r in rows], "https://x.test", "k")
    checks.append((sent == 0 and lost == 500,
                   f"a total outage loses every row and says so (sent={sent}, lost={lost})"))
    checks.append((calls["posts"] == 15,
                   f"...and stops asking after 5 consecutive misses rather than "
                   f"retrying all 10 batches ({calls['posts']} posts, not 30)"))

    # ---- 4. THE HAPPY PATH is untouched: no retries, no losses.
    fake, calls = timing_out(set())
    sys.modules["requests"] = fake
    sent, skipped, lost = S.upsert([dict(r) for r in rows], "https://x.test", "k")
    checks.append((sent == 500 and skipped == 0 and lost == 0 and calls["posts"] == 10,
                   f"a healthy database pays nothing for any of this "
                   f"(sent={sent}, posts={calls['posts']})"))

    # ---- 5. THE ACCOUNTING HOLDS when a batch dies HALFWAY through row-by-row.
    # A whole-batch 400 sends the chunk row-by-row; if the transport then dies on
    # row 20 of 50, the 20 already counted must not be counted a second time as
    # lost. sent + skipped + lost must always equal the rows handed in.
    def half_dead(url, headers=None, data=None, timeout=None):
        batch = json.loads(data)
        if len(batch) > 1:
            return Resp(400, {"code": "P0001", "message": "content_blocked"})  # force row-by-row
        n = int(batch[0]["title"].split()[1])
        if n >= 20:
            raise _real_requests.exceptions.ReadTimeout("Read timed out. (read timeout=30)")
        return Resp(201)
    sys.modules["requests"] = types.SimpleNamespace(post=half_dead)
    fifty = [{"title": f"row {i}"} for i in range(50)]
    sent, skipped, lost = S.upsert(fifty, "https://x.test", "k")
    checks.append((sent + skipped + lost == 50,
                   f"a batch that dies halfway still accounts for exactly 50 rows "
                   f"(sent={sent} + skipped={skipped} + lost={lost} = {sent + skipped + lost})"))
    checks.append((sent == 20 and lost == 30,
                   f"...the 20 that landed are not also reported lost "
                   f"(sent={sent}, lost={lost})"))

    # ---- 6. A ROW-LEVEL rejection is still a rejection, not a loss.
    def post_blocked(url, headers=None, data=None, timeout=None):
        batch = json.loads(data)
        if any("BLOCKED" in (r.get("title") or "") for r in batch):
            return Resp(400, {"code": "P0001", "message": "content_blocked"})
        return Resp(201)
    sys.modules["requests"] = types.SimpleNamespace(post=post_blocked)
    sent, skipped, lost = S.upsert([{"title": "ok"}, {"title": "BLOCKED"}, {"title": "fine"}],
                                   "https://x.test", "k")
    checks.append((sent == 2 and skipped == 1 and lost == 0,
                   f"a rejected row is skipped, never lost (sent={sent}, "
                   f"skipped={skipped}, lost={lost})"))

    # ---- 7. TWO 5xx WANT OPPOSITE THINGS, AND A 4xx IS THE ONLY ANSWER THAT
    # BLAMES THE ROWS. A final 503 used to fall through to row-by-row isolation:
    # 3 + 50x3 = 153 POSTs per batch against a database that was already failing
    # (2026-08-29 13:36:49, OpenActive, 503 PGRST002).
    import contextlib
    import io
    PGRST002 = {"code": "PGRST002",
                "message": "Could not query the database for the schema cache. Retrying."}
    STMT_TIMEOUT = {"code": "57014", "message": "canceling statement due to statement timeout"}
    sizes = []

    def answering(fn):
        def post(url, headers=None, data=None, timeout=None):
            batch = json.loads(data)
            sizes.append(len(batch))
            return fn(batch)
        return types.SimpleNamespace(post=post)

    def run(rows_in):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            out = S.upsert(rows_in, "https://x.test", "k")
        return out, buf.getvalue()

    fifty = lambda: [{"title": f"row {i}"} for i in range(50)]

    sizes.clear(); slept.clear()
    sys.modules["requests"] = answering(lambda b: Resp(503, PGRST002))
    (sent, skipped, lost), log = run(fifty())
    checks.append((sent == 0 and skipped == 0 and lost == 50,
                   f"a batch answered 503 PGRST002 every time is LOST, not skipped "
                   f"(sent={sent}, skipped={skipped}, lost={lost})"))
    checks.append((sizes == [50, 50, 50],
                   f"...in 3 POSTs of the whole batch, never row by row (was 153): {sizes}"))
    checks.append((len(slept) == 2 and 2 <= slept[0] <= 2.5 and 4 <= slept[1] <= 5,
                   f"...retrying after ~2s then ~4s, jittered, and not waiting after the "
                   f"last batch ({[round(s, 2) for s in slept]})"))
    checks.append(("PGRST002" in log and "UPDATE waits for the next refresh day" in log,
                   "...and the warning quotes the server and says a lost UPDATE is not "
                   "re-sent by the --only-new runs"))

    sizes.clear(); slept.clear()
    (sent, skipped, lost), _log = run([dict(r) for r in rows])
    checks.append((lost == 500 and len(sizes) == 15,
                   f"...a sustained 503 is a miss toward GIVE_UP_AFTER, like a timeout "
                   f"(posts={len(sizes)}, lost={lost})"))
    checks.append((sum(1 for s in slept if 8 <= s <= 10) == 4,
                   f"...and each lost batch waits ~8s before the next one asks, until it "
                   f"gives up ({[round(s, 1) for s in slept]})"))

    sizes.clear()
    sys.modules["requests"] = answering(lambda b: Resp(429, {"message": "rate limited"}))
    (sent, skipped, lost), _log = run(fifty())
    checks.append((lost == 50 and sizes == [50, 50, 50],
                   f"a 429 is not the rows' fault either: lost, not isolated ({sizes})"))

    # A statement timeout wants a SMALLER bite: 50 -> 25 -> 12/13, which lands.
    sizes.clear()
    sys.modules["requests"] = answering(
        lambda b: Resp(500, STMT_TIMEOUT) if len(b) > 13 else Resp(201))
    (sent, skipped, lost), _log = run(fifty())
    checks.append((sent == 50 and skipped == 0 and lost == 0,
                   f"a 500 57014 batch lands by halving (sent={sent}, lost={lost})"))
    checks.append((sizes == [50] * 3 + [25] * 3 + [12, 13] + [25] * 3 + [12, 13],
                   f"...50 -> 25 -> 12, and never row by row: {sizes}"))

    # ...and only what still times out at 12 is isolated.
    sizes.clear()
    sys.modules["requests"] = answering(
        lambda b: Resp(500, STMT_TIMEOUT) if len(b) > 1 else Resp(201))
    (sent, skipped, lost), _log = run(fifty())
    checks.append((sent == 50 and sizes.count(1) == 50
                   and min(s for s in sizes if s > 1) == 12,
                   f"a 500 that persists at 12 rows is isolated row by row (sent={sent}, "
                   f"single-row posts={sizes.count(1)})"))

    # A 503 mid-isolation stops the isolation instead of asking 45 more times.
    sizes.clear()

    def blocked_then_down(b):
        if len(b) > 1:
            return Resp(400, {"code": "P0001", "message": "content_blocked"})
        return Resp(201) if int(b[0]["title"].split()[1]) < 5 else Resp(503, PGRST002)
    sys.modules["requests"] = answering(blocked_then_down)
    (sent, skipped, lost), _log = run(fifty())
    checks.append((sent == 5 and skipped == 0 and lost == 45 and len(sizes) == 1 + 5 + 3,
                   f"a 503 during row-by-row isolation stops it: the rest are lost, not "
                   f"asked for (sent={sent}, lost={lost}, posts={len(sizes)})"))

    failed = 0
    for ok, why in checks:
        failed += 0 if ok else 1
        print(f"{'ok  ' if ok else 'FAIL'}  {why}")
    print(f"\n{len(checks)} cases, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
