#!/usr/bin/env python3
"""
mapsee_db_health.py — is the database answering, by the Worker's own probe.

Every five minutes the mapsee Worker (../mapsee src/index.js, probeDb) makes the
cheapest read the anon key can make and writes the result to the stats_snapshot
heartbeat table as kind 'cron:probeDb': ok, ms, db_ms, slow, status, and the
reason when it failed. This script reads that row through stats_snapshot_all()
and turns it into a verdict and a markdown report, the way mapsee_health_check
does for the sources, so db-health.yml can open, update and close an issue.

WHY HERE AND NOT IN THE WORKER. The Worker measures on time (a cron there fires
to the minute) but has no free channel to a person. This repository has one:
an issue, the channel source-health already uses. A scheduled run here can
start late (hours, on 2026-10-19), so the row's own clock is what is graded,
never this run's.

VERDICTS, exit codes:
  0  OK      the latest probe answered in time
  1  DOWN    the latest probe failed (ok=false), or the snapshot itself could
             not be read after retries - the database is not answering
  1  SLOW    the latest probe answered, over the Worker's threshold
  2  STALE   the latest probe is older than --max-age-min: the probe is not
             running (the Worker's cron, or its heartbeat write), so NOTHING
             here says the database is healthy - the alarm is off
  2  MISSING no cron:probeDb row at all: the probe has never run on this
             project (deploy the Worker that carries it)

    python mapsee_db_health.py --markdown db_health.md
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

PROBE_KIND = "cron:probeDb"
ROLLER_KIND = "cron:roll_recurring"
MAX_AGE_MIN_DEFAULT = 20          # four missed five-minute probes
ATTEMPTS = 3
TIMEOUT = 30
EXIT_OK, EXIT_UNHEALTHY, EXIT_CANNOT_RUN = 0, 1, 2


def _when(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def fetch_rows(url: str, key: str, post=None, sleep=time.sleep):
    """The snapshot rows, or a string saying why they could not be read."""
    import requests
    post = post or requests.post
    endpoint = url.rstrip("/") + "/rest/v1/rpc/stats_snapshot_all"
    headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    last = ""
    for attempt in range(ATTEMPTS):
        try:
            r = post(endpoint, headers=headers, json={}, timeout=TIMEOUT)
        except Exception as ex:                                  # noqa: BLE001 - transport, any kind
            last = f"{type(ex).__name__}"
        else:
            if r.status_code < 500:
                try:
                    rows = r.json()
                except ValueError:
                    return f"HTTP {r.status_code}: not JSON"
                if r.status_code != 200:
                    return f"HTTP {r.status_code}: {json.dumps(rows)[:200]}"
                return rows if isinstance(rows, list) else "not a list of rows"
            last = f"HTTP {r.status_code}: {(r.text or '')[:160]}"
        if attempt < ATTEMPTS - 1:
            sleep(2 ** (attempt + 1))
    return last or "no response"


def verdict(rows, now=None, max_age_min=MAX_AGE_MIN_DEFAULT) -> dict:
    """{"state", "exit", "title", "lines"} from the snapshot rows (or an error string)."""
    now = now or datetime.now(timezone.utc)
    if isinstance(rows, str):
        return {"state": "down", "exit": EXIT_UNHEALTHY,
                "title": "Database: the stats snapshot could not be read",
                "lines": [f"`stats_snapshot_all()` did not answer after {ATTEMPTS} tries: {rows}.",
                          "That read is three rows from a tiny table; when it fails, the database "
                          "(or PostgREST in front of it) is not answering."]}
    by_kind = {r.get("kind"): r for r in rows if isinstance(r, dict)}
    probe = by_kind.get(PROBE_KIND)
    if not probe:
        return {"state": "missing", "exit": EXIT_CANNOT_RUN,
                "title": "Database: the probe has never run",
                "lines": [f"No `{PROBE_KIND}` row in the snapshot. The Worker that carries the five-minute "
                          "probe (mapsee src/index.js, probeDb) is not deployed, or has not fired yet."]}
    p = probe.get("payload") or {}
    at = _when(probe.get("computed_at"))
    age_min = (now - at).total_seconds() / 60 if at else None
    lines = []
    roller = by_kind.get(ROLLER_KIND)
    if roller:
        rp = roller.get("payload") or {}
        r_at = _when(roller.get("computed_at"))
        r_age = f"{(now - r_at).total_seconds() / 60:.0f} min ago" if r_at else "unknown"
        lines.append(f"Roller: ok={rp.get('ok')}, moved {rp.get('moved')}, stopped on `{rp.get('stopped')}`, "
                     f"map lag {rp.get('lag_listed_min')} min, {r_age}"
                     + (f"; error: {rp.get('error')}" if rp.get("error") else "") + ".")
    if age_min is None or age_min > max_age_min:
        when = f"{age_min:.0f} min ago" if age_min is not None else "at an unreadable time"
        return {"state": "stale", "exit": EXIT_CANNOT_RUN,
                "title": "Database: the probe has stopped running",
                "lines": [f"The latest `{PROBE_KIND}` row is from {when}; the Worker writes one every five minutes. "
                          f"Nothing here says the database is healthy - the alarm is off. Check the Worker's cron "
                          f"(wrangler.jsonc triggers) and its heartbeat write (cron.status_write_failed in Workers Logs).",
                          f"Its last word was ok={p.get('ok')}, db_ms={p.get('db_ms')}, slow={p.get('slow')}"
                          + (f", error: {p.get('error')}" if p.get("error") else "") + "."] + lines}
    if p.get("ok") is False:
        return {"state": "down", "exit": EXIT_UNHEALTHY,
                "title": "Database: the probe is failing",
                "lines": [f"The latest probe ({age_min:.0f} min ago) failed: {p.get('error') or 'no reason recorded'}.",
                          "The read is one row of an empty table with the anon key; when it fails, every door's "
                          "database is not answering. Read backend/supabase/DIAGNOSE_UNHEALTHY.sql sections 5, 6, 8 and 9 "
                          "in ../mapsee, and the 2026-10-19 note in docs/agents/database-and-query-performance.md there."] + lines}
    if p.get("slow"):
        return {"state": "slow", "exit": EXIT_UNHEALTHY,
                "title": "Database: the probe is slow",
                "lines": [f"The latest probe ({age_min:.0f} min ago) answered in {p.get('db_ms')} ms, over the Worker's "
                          f"threshold. A read that touches no row waiting this long means the instance is starved "
                          f"(CPU, I/O budget, swap) or its connections are spent."] + lines}
    return {"state": "ok", "exit": EXIT_OK, "title": "Database: answering",
            "lines": [f"The latest probe ({age_min:.0f} min ago) answered in {p.get('db_ms')} ms (HTTP {p.get('status')})."]
            + lines}


def render(v: dict, checked_at=None) -> str:
    checked_at = checked_at or datetime.now(timezone.utc)
    out = [f"### {v['title']}", ""]
    out += [f"- {line}" for line in v["lines"]]
    out += ["", f"_State `{v['state']}`, checked {checked_at:%Y-%m-%d %H:%M UTC}._"]
    return "\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="Grade the database by the Worker's five-minute probe heartbeat.")
    ap.add_argument("--markdown", metavar="PATH", help="Write the report here (for an issue body).")
    ap.add_argument("--max-age-min", type=float, default=MAX_AGE_MIN_DEFAULT,
                    help=f"A probe older than this is 'stale' (default {MAX_AGE_MIN_DEFAULT}).")
    a = ap.parse_args()
    url, key = os.environ.get("SUPABASE_URL", "").strip(), os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not (url and key):
        print("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY.", file=sys.stderr)
        return EXIT_CANNOT_RUN
    v = verdict(fetch_rows(url, key), max_age_min=a.max_age_min)
    report = render(v)
    print(report)
    if a.markdown:
        with open(a.markdown, "w", encoding="utf-8") as fh:
            fh.write(report)
    return v["exit"]


if __name__ == "__main__":
    sys.exit(main())
