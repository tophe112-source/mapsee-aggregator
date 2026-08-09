#!/usr/bin/env python3
"""Tests for mapsee_health_check.py — the alarm, not the thing it watches.

WHY THIS EXISTS
---------------
source-health.yml ran eight times and failed eight times. Not because a source
was down: because the check called `source_stats()`, an RPC ../mapsee migration
0112 had already retired for timing out, and PostgREST renders a statement
timeout as a bare HTTP 500. The check turned that 500 into "could not reach the
database — usual causes: a rotated key, the project paused, a transient network
failure" and filed it under an issue titled "one or more sources have gone
quiet". Every word of that was wrong, and it repeated daily for four days.

Two failures, and both are testable without a database:

  1. IT CALLED THE WRONG RPC. Nothing asserted which endpoint it hit, so
     swapping it is a silent change either way.
  2. IT THREW AWAY THE ANSWER. The 500 body said `{"code":"57014", ...}` — the
     diagnosis, in the response, discarded before anyone saw it.

So these tests fake the HTTP layer and assert on the contract that matters: the
right endpoint, the server's own words in the report, and an exit code that
distinguishes "a source is quiet" from "the check could not run". No network, no
secrets, no database — same gate as the other three test scripts.

    python test_health_check.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

import mapsee_health_check as hc

FAILURES: list[str] = []
CALLS: list[dict] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


class FakeResponse:
    def __init__(self, status: int, payload=None, text: str = ""):
        self.status_code = status
        self._payload = payload
        self.text = text if text else (json.dumps(payload) if payload is not None else "")

    @property
    def ok(self):
        return self.status_code < 400

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def fake_post(routes):
    """Route by RPC name. `routes[name]` is a FakeResponse or a list of them
    (consumed one per attempt, so retry behaviour is observable)."""
    def _post(endpoint, headers=None, json=None, timeout=None):   # noqa: A002
        name = endpoint.rsplit("/", 1)[-1]
        CALLS.append({"rpc": name, "body": json})
        r = routes.get(name)
        if r is None:
            return FakeResponse(404, {"code": "PGRST202",
                                      "message": f"Could not find the function public.{name}"})
        if isinstance(r, list):
            return r.pop(0) if len(r) > 1 else r[0]
        return r
    return _post


def snapshot_rows(computed_at, sources, cron=None):
    rows = [{"kind": "sources", "payload": sources,
             "computed_at": computed_at.strftime("%Y-%m-%dT%H:%M:%S+00:00")}]
    for task, (payload, at) in (cron or {}).items():
        rows.append({"kind": f"cron:{task}", "payload": payload,
                     "computed_at": at.strftime("%Y-%m-%dT%H:%M:%S+00:00")})
    return rows


def run(routes, argv, baseline=None, env=True, dead_feeds=()):
    """Run main() with a faked transport and an isolated baseline path.

    configured_dead() and the ledger are stubbed by default. They read the REAL
    curation_ledger.json and *_sources.json, so without this every case here
    inherits whatever the catalog happens to be carrying — the first draft had
    four tests failing on fifteen genuinely-dead production feeds, which is a
    true finding about the catalog and nothing at all about the code under test.
    Sleep is stubbed too, so the retry paths cost no wall-clock.
    """
    CALLS.clear()
    real = (hc.requests.post, hc.BASELINE, hc.configured_dead,
            hc.ledger_summary, hc.time.sleep)
    real_env = {k: os.environ.get(k) for k in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")}
    tmp = tempfile.mkdtemp()
    md = os.path.join(tmp, "report.md")
    hc.BASELINE = os.path.join(tmp, "baseline.json")
    if baseline is not None:
        with open(hc.BASELINE, "w", encoding="utf-8") as fh:
            json.dump(baseline, fh)
    hc.requests.post = fake_post(routes)
    hc.configured_dead = lambda: list(dead_feeds)
    hc.ledger_summary = lambda: (0, 0)
    hc.time.sleep = lambda _s: None
    if env:
        os.environ["SUPABASE_URL"] = "https://example.supabase.co"
        os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "test-key"
    else:
        os.environ.pop("SUPABASE_URL", None)
        os.environ.pop("SUPABASE_SERVICE_ROLE_KEY", None)
    try:
        code = hc.main(argv + ["--markdown", md])
        report = open(md, encoding="utf-8").read() if os.path.exists(md) else ""
        return code, report
    finally:
        (hc.requests.post, hc.BASELINE, hc.configured_dead,
         hc.ledger_summary, hc.time.sleep) = real
        for k, v in real_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def rpc_sequence():
    """The distinct RPCs tried, in order — retries of the same one collapsed."""
    seq = []
    for c in CALLS:
        if not seq or seq[-1] != c["rpc"]:
            seq.append(c["rpc"])
    return seq


NOW = datetime.now(timezone.utc)
HEALTHY = snapshot_rows(NOW - timedelta(hours=2), [
    {"source": "mapsee", "upcoming": 9000, "added_24h": 400,
     "added_7d": 2000, "last_added": (NOW - timedelta(hours=3)).isoformat()},
    {"source": "community", "upcoming": 40, "added_24h": 1,
     "added_7d": 6, "last_added": (NOW - timedelta(hours=20)).isoformat()},
])
BASE = {
    "taken_at": "2026-08-01 00:00 UTC",
    "sources": {"mapsee": {"upcoming": 9000, "stale_days": 8},
                "community": {"upcoming": 40, "stale_days": 8}},
}


def t_reads_the_snapshot_not_the_dead_rpc():
    """THE regression. source_stats() cannot answer on a real dataset."""
    code, report = run({hc.SNAPSHOT_RPC: FakeResponse(200, HEALTHY)}, [], baseline=BASE)
    rpcs = [c["rpc"] for c in CALLS]
    check("reads stats_snapshot_all()", rpcs and rpcs[0] == hc.SNAPSHOT_RPC, f"called {rpcs}")
    check("does not call source_stats() when the snapshot answers",
          hc.LEGACY_RPC not in rpcs, f"called {rpcs}")
    check("a healthy pipeline exits 0", code == hc.EXIT_OK, f"exit {code}")
    check("healthy report says so", "healthy" in report.lower(), report[:120])


def t_server_error_body_reaches_the_report():
    """The 57014 case, exactly as production returned it for four days."""
    body = {"code": "57014", "message": "canceling statement due to statement timeout"}
    code, report = run({hc.SNAPSHOT_RPC: FakeResponse(500, body)}, [], baseline=BASE)
    check("a 500 exits 2, not 1", code == hc.EXIT_CANNOT_RUN, f"exit {code}")
    check("the postgres error CODE is in the report", "57014" in report, report[:200])
    check("the postgres MESSAGE is in the report",
          "statement timeout" in report, report[:200])
    check("the report does not claim a source went quiet",
          "gone quiet" not in report.lower() and "Nothing was evaluated" in report,
          report[:200])


def t_transient_5xx_is_retried():
    routes = {hc.SNAPSHOT_RPC: [FakeResponse(502, {"message": "bad gateway"}),
                                FakeResponse(200, HEALTHY)]}
    code, _ = run(routes, [], baseline=BASE)
    check("a 502 then a 200 is healthy", code == hc.EXIT_OK, f"exit {code}")
    check("the retry actually happened", len(CALLS) == 2, f"{len(CALLS)} call(s)")


def t_4xx_is_not_retried():
    """A revoked key is a settled answer; retrying it only delays the report."""
    code, report = run({hc.SNAPSHOT_RPC: FakeResponse(401, {"message": "Invalid API key"})},
                       [], baseline=BASE)
    check("a 401 exits 2", code == hc.EXIT_CANNOT_RUN, f"exit {code}")
    check("a 401 is attempted once", len(CALLS) == 1, f"{len(CALLS)} call(s)")
    check("the key error is quoted", "Invalid API key" in report, report[:200])


def t_missing_snapshot_function_falls_back():
    """0112 not applied: try the old RPC and name the migration if it fails."""
    code, report = run({hc.LEGACY_RPC: FakeResponse(500, {"code": "57014",
                                                          "message": "timeout"})},
                       [], baseline=BASE)
    check("falls back to source_stats() on a 404",
          rpc_sequence() == [hc.SNAPSHOT_RPC, hc.LEGACY_RPC], f"called {rpc_sequence()}")
    check("names migration 0112 as the fix", "0112" in report, report[:300])
    check("the fallback failing exits 2", code == hc.EXIT_CANNOT_RUN, f"exit {code}")


def t_never_computed_snapshot():
    empty = [{"kind": "overview", "payload": {}, "computed_at": NOW.isoformat()}]
    code, report = run({hc.SNAPSHOT_RPC: FakeResponse(200, empty)}, [], baseline=BASE)
    check("a snapshot with no sources row exits 2", code == hc.EXIT_CANNOT_RUN, f"exit {code}")
    check("it points at refresh_stats()", "refresh_stats" in report, report[:300])


def t_stale_snapshot_is_not_a_quiet_source():
    """Frozen numbers would report healthy sources as SILENT as days pass."""
    stale = snapshot_rows(NOW - timedelta(hours=hc.SNAPSHOT_MAX_AGE_H + 5),
                          HEALTHY[0]["payload"])
    code, report = run({hc.SNAPSHOT_RPC: FakeResponse(200, stale)}, [], baseline=BASE)
    check("a stale snapshot exits 2, not 1", code == hc.EXIT_CANNOT_RUN, f"exit {code}")
    check("it blames the cron, not the sources",
          "refresh_stats" in report or "cron" in report.lower(), report[:300])
    check("with no cron rows it says the Worker recorded nothing",
          "recorded no cron status" in report, report[:500])


def t_stale_snapshot_quotes_the_cron_reason():
    """The point of the whole exercise: say WHY, not just that it is stale.

    ../mapsee's runCronTask writes the failure beside the snapshot it failed to
    write. Without this the report says '142h old' and sends someone to
    `wrangler tail` for something already sitting in the database.
    """
    stale_at = NOW - timedelta(hours=hc.SNAPSHOT_MAX_AGE_H + 100)
    rows = snapshot_rows(stale_at, HEALTHY[0]["payload"], cron={
        "refreshStats": ({"ok": False, "error": "refresh_stats 504: canceling "
                                                "statement due to statement timeout"},
                         NOW - timedelta(hours=2)),
        "purgeStaleLive": ({"ok": True, "skipped": False}, NOW - timedelta(hours=2)),
    })
    code, report = run({hc.SNAPSHOT_RPC: FakeResponse(200, rows)}, [], baseline=BASE)
    check("still exits 2", code == hc.EXIT_CANNOT_RUN, f"exit {code}")
    check("the failing task is named", "refreshStats" in report, report[:600])
    check("the recorded reason is quoted", "504" in report and "statement timeout" in report,
          report[:600])
    check("the healthy task is shown as ok", "purgeStaleLive: ok" in report, report[:600])


def t_cron_status_rides_along_on_a_healthy_run():
    """A cron that failed once inside the snapshot budget is not an outage —
    it is the early warning for the one that is coming."""
    rows = snapshot_rows(NOW - timedelta(hours=2), HEALTHY[0]["payload"], cron={
        "refreshStats": ({"ok": False, "error": "refresh_stats 504: timeout"},
                         NOW - timedelta(hours=2)),
    })
    code, report = run({hc.SNAPSHOT_RPC: FakeResponse(200, rows)}, [], baseline=BASE)
    check("a failing cron alone does NOT fail the check", code == hc.EXIT_OK, f"exit {code}")
    check("but it is reported", "CRON" in report and "refreshStats" in report, report[:600])
    check("with its reason", "504" in report, report[:600])


def t_skipped_cron_reads_as_skipped():
    rows = snapshot_rows(NOW - timedelta(hours=2), HEALTHY[0]["payload"], cron={
        "syncMenus": ({"ok": True, "skipped": "not bound to the Worker: ANTHROPIC_API_KEY"},
                      NOW - timedelta(hours=1)),
    })
    code, report = run({hc.SNAPSHOT_RPC: FakeResponse(200, rows)}, [], baseline=BASE)
    check("a skipped cron does not fail the check", code == hc.EXIT_OK, f"exit {code}")
    check("the unbound secret is named", "ANTHROPIC_API_KEY" in report, report[:600])
    check("and it reads as skipped, not failed",
          "skipped" in report and "FAILED" not in report, report[:600])


def t_silent_source_is_unhealthy():
    old = snapshot_rows(NOW - timedelta(hours=1), [
        {"source": "mapsee", "upcoming": 9000, "added_24h": 0, "added_7d": 0,
         "last_added": (NOW - timedelta(days=20)).isoformat()},
    ])
    code, report = run({hc.SNAPSHOT_RPC: FakeResponse(200, old)}, [], baseline=BASE)
    check("a stale source exits 1", code == hc.EXIT_UNHEALTHY, f"exit {code}")
    check("it is reported as SILENT", "SILENT" in report, report[:300])


def t_drained_source_is_unhealthy():
    drained = snapshot_rows(NOW - timedelta(hours=1), [
        {"source": "mapsee", "upcoming": 100, "added_24h": 5, "added_7d": 30,
         "last_added": (NOW - timedelta(hours=2)).isoformat()},
    ])
    code, report = run({hc.SNAPSHOT_RPC: FakeResponse(200, drained)}, [], baseline=BASE)
    check("a collapsed source exits 1", code == hc.EXIT_UNHEALTHY, f"exit {code}")
    check("it is reported as DRAINED", "DRAINED" in report, report[:300])


def t_null_last_added_reads_as_a_month_of_silence():
    """The snapshot's max(created_at) is filtered to 30 days, so NULL is a
    finding — not a missing column."""
    nothing = snapshot_rows(NOW - timedelta(hours=1), [
        {"source": "mapsee", "upcoming": 9000, "added_24h": 0, "added_7d": 0,
         "last_added": None},
    ])
    code, report = run({hc.SNAPSHOT_RPC: FakeResponse(200, nothing)}, [], baseline=BASE)
    check("a null last_added exits 1", code == hc.EXIT_UNHEALTHY, f"exit {code}")
    check("it is explained as 30 days of nothing", "30 days" in report, report[:400])


def t_missing_baseline_says_it_checked_nothing():
    """The silent-green branch: passing because there is nothing to compare to."""
    code, report = run({hc.SNAPSHOT_RPC: FakeResponse(200, HEALTHY)}, [])
    check("no baseline still exits 0", code == hc.EXIT_OK, f"exit {code}")
    check("but the report admits nothing was checked",
          "nothing was checked" in report.lower(), report[:200])


def t_seed_baseline_writes_once():
    routes = {hc.SNAPSHOT_RPC: FakeResponse(200, HEALTHY)}
    code, report = run(routes, ["--seed-baseline"])
    check("--seed-baseline exits 0", code == hc.EXIT_OK, f"exit {code}")
    check("--seed-baseline reports a seed", "Baseline seeded" in report, report[:200])
    # and with a baseline present it must NOT overwrite: it evaluates instead
    code, report = run(routes, ["--seed-baseline"], baseline=BASE)
    check("--seed-baseline with a baseline evaluates instead of reseeding",
          code == hc.EXIT_OK and "Baseline seeded" not in report, report[:200])


def t_warn_only_never_fails():
    body = {"code": "57014", "message": "canceling statement due to statement timeout"}
    code, _ = run({hc.SNAPSHOT_RPC: FakeResponse(500, body)}, ["--warn-only"], baseline=BASE)
    check("--warn-only exits 0 on an unreachable RPC", code == hc.EXIT_OK, f"exit {code}")


def t_no_credentials_is_a_skip():
    code, _ = run({}, [], baseline=BASE, env=False)
    check("no credentials exits 0", code == hc.EXIT_OK, f"exit {code}")
    check("no credentials makes no request", not CALLS, f"{len(CALLS)} call(s)")


def main():
    for fn in (t_reads_the_snapshot_not_the_dead_rpc,
               t_server_error_body_reaches_the_report,
               t_transient_5xx_is_retried,
               t_4xx_is_not_retried,
               t_missing_snapshot_function_falls_back,
               t_never_computed_snapshot,
               t_stale_snapshot_is_not_a_quiet_source,
               t_stale_snapshot_quotes_the_cron_reason,
               t_cron_status_rides_along_on_a_healthy_run,
               t_skipped_cron_reads_as_skipped,
               t_silent_source_is_unhealthy,
               t_drained_source_is_unhealthy,
               t_null_last_added_reads_as_a_month_of_silence,
               t_missing_baseline_says_it_checked_nothing,
               t_seed_baseline_writes_once,
               t_warn_only_never_fails,
               t_no_credentials_is_a_skip):
        print(f"\n--- {fn.__name__} ---")
        fn()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: " + ", ".join(FAILURES))
        return 1
    print("all health-check tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
