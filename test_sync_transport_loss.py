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

    failed = 0
    for ok, why in checks:
        failed += 0 if ok else 1
        print(f"{'ok  ' if ok else 'FAIL'}  {why}")
    print(f"\n{len(checks)} cases, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
