#!/usr/bin/env python3
"""The database verdict, from the Worker's probe heartbeat. No network.

    python test_db_health.py
"""
import sys
from datetime import datetime, timedelta, timezone

import mapsee_db_health as h

FAILS = 0


def check(label, cond, detail=""):
    global FAILS
    print(f"{'ok  ' if cond else 'FAIL'}  {label}" + ("" if cond else f"   {detail!r}"))
    if not cond:
        FAILS += 1


NOW = datetime(2026, 10, 19, 16, 0, tzinfo=timezone.utc)


def probe(minutes_ago, **payload):
    base = {"ok": True, "ms": 310, "db_ms": 300, "slow": False, "status": 200, "skipped": False}
    base.update(payload)
    return {"kind": h.PROBE_KIND, "payload": base, "computed_at": (NOW - timedelta(minutes=minutes_ago)).isoformat()}


ROLLER = {"kind": h.ROLLER_KIND, "computed_at": (NOW - timedelta(minutes=3)).isoformat(),
          "payload": {"ok": True, "moved": 13404, "stopped": "budget", "lag_listed_min": 0, "error": None}}

v = h.verdict([probe(2), ROLLER], NOW)
check("a fresh, fast probe is ok, exit 0", v["state"] == "ok" and v["exit"] == 0, v)
check("...and the report carries the latency and the roller's state", "300 ms" in v["lines"][0] and "moved 13404" in v["lines"][1], v["lines"])

v = h.verdict([probe(3, ok=False, error="database probe: HTTP 503: {\"code\":\"PGRST002\"} after 59193 ms")], NOW)
check("a failed probe is down, exit 1, with the Worker's reason", v["state"] == "down" and v["exit"] == 1 and "PGRST002" in v["lines"][0], v)
check("...and points at the diagnosis file", any("DIAGNOSE_UNHEALTHY.sql" in line for line in v["lines"]), v["lines"])

v = h.verdict([probe(1, db_ms=4120, slow=True)], NOW)
check("a slow probe is slow, exit 1, with the latency", v["state"] == "slow" and v["exit"] == 1 and "4120 ms" in v["lines"][0], v)

v = h.verdict([probe(45)], NOW)
check("a probe 45 minutes old is stale, exit 2: the alarm is off, not the database down",
      v["state"] == "stale" and v["exit"] == 2 and "45 min ago" in v["lines"][0], v)
v = h.verdict([probe(45, ok=False, error="x")], NOW)
check("...even when its last word was a failure (an old failure is not a current one)", v["state"] == "stale", v["state"])
check("...and the age limit is the caller's", h.verdict([probe(45)], NOW, max_age_min=60)["state"] == "ok")
check("the default limit is four missed probes", h.MAX_AGE_MIN_DEFAULT == 20)

v = h.verdict([ROLLER], NOW)
check("no probe row at all is missing, exit 2, naming the Worker task", v["state"] == "missing" and v["exit"] == 2 and "probeDb" in v["lines"][0], v)

v = h.verdict("ReadTimeout", NOW)
check("an unreadable snapshot is down, exit 1 (three rows of a tiny table did not come back)",
      v["state"] == "down" and v["exit"] == 1 and "ReadTimeout" in v["lines"][0], v)

v = h.verdict([{"kind": h.PROBE_KIND, "payload": {"ok": True}, "computed_at": "not a time"}], NOW)
check("an unreadable computed_at is stale, not a crash", v["state"] == "stale", v)


# fetch_rows: retries 5xx and transport errors, settles on anything under 500
class R:
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, str(body)

    def json(self):
        if isinstance(self._body, str):
            raise ValueError("not json")
        return self._body


def scripted(script):
    calls = []

    def post(endpoint, headers=None, json=None, timeout=None):
        calls.append(endpoint)
        item = script.pop(0)
        if isinstance(item, type):
            raise item("boom")
        return item
    return post, calls


post, calls = scripted([R(503, "down"), ConnectionError, R(200, [{"kind": "x"}])])
slept = []
rows = h.fetch_rows("https://x.supabase.co/", "k", post=post, sleep=slept.append)
check("a 5xx and a transport error are retried with a widening gap, then the rows come back",
      rows == [{"kind": "x"}] and len(calls) == 3 and slept == [2, 4], (rows, len(calls), slept))
check("...against stats_snapshot_all", calls[0].endswith("/rest/v1/rpc/stats_snapshot_all"), calls[0])
post, calls = scripted([R(503, "down"), R(503, "down"), R(503, "still down")])
rows = h.fetch_rows("https://x.supabase.co", "k", post=post, sleep=lambda s: None)
check("three 5xx in a row come back as the reason, not as rows", isinstance(rows, str) and "503" in rows, rows)
post, calls = scripted([R(401, {"message": "bad key"})])
rows = h.fetch_rows("https://x.supabase.co", "k", post=post, sleep=lambda s: None)
check("a 4xx is settled at once: one call, the body in the reason", len(calls) == 1 and "bad key" in rows, (calls, rows))

md = h.render(h.verdict([probe(2)], NOW), NOW)
check("the markdown has a heading, bullets and the state line", md.startswith("### Database: answering") and "_State `ok`" in md, md)

print()
if FAILS:
    print(f"{FAILS} FAILED")
    sys.exit(1)
print("all db-health checks passed")
