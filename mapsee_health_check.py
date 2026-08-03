#!/usr/bin/env python3
"""Ingest health check — turns a silently-dead source into a loud one.

WHY THIS EXISTS
---------------
Every ingest job in this repo is deliberately failure-tolerant: `set +e`, bare
`exit 0`, and `|| true` are sprinkled through aggregate-events.yml so that one
bad metro or one 403ing calendar cannot abort a sweep of forty others. That is
the right call — but it has a cost that bit us before: **the workflow reports
green whether or not it actually ingested anything.** If Ticketmaster rotates a
key, or a venue's ICS feed starts returning 403, the run succeeds, ingests zero
events from that source, and nothing anywhere raises a hand. With the heavy
sweep on Mon/Thu, the blind window is up to 3.5 days.

This script closes that gap. It asks the database what actually landed, compares
against a committed baseline of sources that are supposed to be alive, and exits
non-zero with a readable report when something has gone quiet. The workflow
turns that into a GitHub issue, which emails you.

It reads. It never writes event data.

WHAT IT CHECKS
--------------
  1. SILENT  — a baseline source whose newest event is older than its staleness
               budget. This is the big one: the feed still parses, the job still
               exits 0, but nothing new has arrived in days.
  2. MISSING — a baseline source that has no rows at all any more.
  3. DRAINED — a baseline source whose upcoming-event count has collapsed past
               `--drop-pct` versus the baseline. Catches a feed that went from
               2,000 events to 12 without going fully dark.
  4. LEDGER  — how many entries catalog_curate.py has marked dead, and whether
               that number is growing. Advisory only; does not fail the run.

New sources appearing are reported as NEW and never fail the check — growth is
not a problem.

STALENESS BUDGETS
-----------------
A flat threshold does not work here, because the sources do not run on the same
cadence: Ticketmaster and the feeds run daily, but SeatGeek/DICE/AXS/Meetup/
Eventbrite only run Mon & Thu. A 72h rule would page you every Sunday about
sources that are behaving perfectly. So the budget is per-source, defaulting to
DEFAULT_STALE_DAYS (8 — comfortably past a Mon/Thu gap plus a missed run), and
overridable per source in the baseline file.

USAGE
-----
    # one-time, after a known-good run — records what "healthy" looks like
    python mapsee_health_check.py --update-baseline

    # what CI runs; exits 1 if anything is SILENT / MISSING / DRAINED
    python mapsee_health_check.py

    # write a markdown report for the workflow to put in a GitHub issue
    python mapsee_health_check.py --markdown health_report.md

    # look, but never fail the build (useful while tuning)
    python mapsee_health_check.py --warn-only

Environment: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY (the anon key also works —
source_stats() is granted to anon — but CI already has the service key).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
BASELINE = os.path.join(HERE, "source_health_baseline.json")
LEDGER = os.path.join(HERE, "curation_ledger.json")

# Which config file holds which source type, and which key on an entry is the
# ledger's key for it. IMPORTED rather than re-declared: catalog_curate.py is
# what WRITES the ledger, so if the two disagreed about where a source's URL
# lives this check would quietly stop matching and report nothing wrong.
# ODS entries have no literal url — the records endpoint is derived — so its
# builder comes across too.
try:
    from catalog_curate import CONFIG as CURATE_CONFIG, _ods_url
except Exception:      # pragma: no cover - curator absent; degrade to ICS only
    CURATE_CONFIG = {"ics": ("ics_sources.json", "url")}

    def _ods_url(_e):
        return ""

# Days without a new event before a source is considered silent. 8 rather than
# 3: the heavy sweep is Mon & Thu, so a healthy SeatGeek can legitimately go
# ~4 days quiet, and one skipped run must not cry wolf.
DEFAULT_STALE_DAYS = 8

# Sources that are genuinely infrequent by nature get a longer rope. Anything
# not listed here uses DEFAULT_STALE_DAYS. Keys are matched case-insensitively
# against the source name.
STALE_OVERRIDES = {
    "nps": 21,           # national park programming is published in batches
    "recreation": 21,
    "parkrun": 14,
    "opendata": 14,      # civic portals refresh on their own slow schedule
    "ods": 14,
    "ckan": 14,
}

TIMEOUT = 30


# --- data ------------------------------------------------------------------

def fetch_source_stats(url: str, key: str) -> list[dict]:
    """Call the source_stats() RPC. Returns [] on a shape we don't recognise."""
    endpoint = url.rstrip("/") + "/rest/v1/rpc/source_stats"
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    r = requests.post(endpoint, headers=headers, json={}, timeout=TIMEOUT)
    r.raise_for_status()
    rows = r.json()
    if not isinstance(rows, list):
        print(f"!! source_stats() returned {type(rows).__name__}, expected a list",
              file=sys.stderr)
        return []
    return rows


def _parse_ts(v):
    """Postgres timestamptz -> aware datetime, or None. Tolerates 'Z' and +00."""
    if not v:
        return None
    s = str(v).strip().replace("Z", "+00:00")
    # '2026-08-02T06:17:00.123456+00' -> pad the offset so fromisoformat copes
    if len(s) >= 3 and s[-3] in "+-":
        s += ":00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def stale_budget(source: str) -> int:
    low = (source or "").lower()
    for frag, days in STALE_OVERRIDES.items():
        if frag in low:
            return days
    return DEFAULT_STALE_DAYS


def ledger_summary() -> tuple[int, int]:
    """(dead, total) from the curation ledger. (0, 0) if it isn't there."""
    if not os.path.exists(LEDGER):
        return 0, 0
    try:
        with open(LEDGER, encoding="utf-8") as fh:
            led = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return 0, 0
    if not isinstance(led, dict):
        return 0, 0
    dead = sum(1 for v in led.values()
               if isinstance(v, dict) and v.get("status") == "fail")
    return dead, len(led)


def _norm_url(u) -> str:
    """Ledger keys are bare host+path; config urls carry a scheme and sometimes
    a www. Normalise both to the same shape so they can be compared.

    NOT str.lstrip("www.") — that strips a CHARACTER SET, so any host beginning
    with w or . loses letters ("westseattle.org" -> "estseattle.org") and would
    silently never match its ledger row.
    """
    s = str(u or "").strip().lower()
    for scheme in ("https://", "http://"):
        if s.startswith(scheme):
            s = s[len(scheme):]
            break
    if s.startswith("www."):
        s = s[4:]
    return s


def _load_ledger() -> dict:
    try:
        with open(LEDGER, encoding="utf-8") as fh:
            led = json.load(fh)
        return led if isinstance(led, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def configured_dead() -> list[dict]:
    """Feeds that are WIRED IN and that the curator audit has marked dead.

    The bare ledger count conflates two completely different things. Most
    "fail" rows are candidates that were evaluated and rejected — that is the
    curation process succeeding, and reporting it as a number that only ever
    goes up trains you to ignore it. The rows that matter are the ones whose URL
    is still in a *_sources.json: those were working when they were added, they
    are being fetched on every run, and they are now returning nothing.

    Measured 2026-08-02: 131 ledger rows were 'fail' and 13 of them were still
    configured — including three metro library systems (Denver, Fairfax County,
    Carnegie Pittsburgh), which are among the best 'learning' and 'community'
    supply the catalog has, and which feed awaresie.com and plansie.com.
    """
    led = _load_ledger()
    if not led:
        return []
    wired = {}
    for kind, (fname, url_key) in CURATE_CONFIG.items():
        path = os.path.join(HERE, fname)
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                blob = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        # both shapes: a bare list, or {"sources": [...]} — same as _entries()
        entries = blob.get("sources", []) if isinstance(blob, dict) else blob
        if not isinstance(entries, list):
            continue
        for e in entries:
            if not isinstance(e, dict):
                continue
            u = _ods_url(e) if kind == "ods" else e.get(url_key)
            if u:
                wired[_norm_url(u)] = (fname, e)
    out = []
    for key, row in led.items():
        if not isinstance(row, dict) or row.get("status") != "fail":
            continue
        hit = wired.get(_norm_url(key))
        if not hit:
            continue                      # a rejected candidate, not a regression
        fname, entry = hit
        out.append({
            "url": _norm_url(key),
            "name": row.get("name") or entry.get("name") or key,
            "reason": row.get("reason") or "?",
            "category": entry.get("category") or "-",
            "config": fname,
        })
    return sorted(out, key=lambda d: (d["category"], d["url"]))


# --- checks ----------------------------------------------------------------

def evaluate(rows: list[dict], baseline: dict, drop_pct: float, now: datetime):
    """Compare live stats to the baseline. Returns (problems, notes, live)."""
    live = {r.get("source") or "?": r for r in rows}
    base_sources = baseline.get("sources", {})
    problems, notes = [], []

    for name, b in sorted(base_sources.items()):
        row = live.get(name)
        if row is None:
            problems.append({
                "kind": "MISSING", "source": name,
                "detail": f"no rows at all (baseline had {b.get('upcoming', 0):,} upcoming)",
            })
            continue

        budget = int(b.get("stale_days") or stale_budget(name))
        last = _parse_ts(row.get("last_added"))
        if last is None:
            problems.append({
                "kind": "SILENT", "source": name,
                "detail": "no last_added timestamp on any row",
            })
        else:
            age = (now - last).days
            if age > budget:
                problems.append({
                    "kind": "SILENT", "source": name,
                    "detail": f"newest event is {age}d old (budget {budget}d) - "
                              f"last added {last:%Y-%m-%d %H:%M} UTC",
                })

        base_up = int(b.get("upcoming") or 0)
        now_up = int(row.get("upcoming") or 0)
        if base_up >= 50 and now_up < base_up * (1 - drop_pct):
            pct = 100 * (1 - (now_up / base_up)) if base_up else 0
            problems.append({
                "kind": "DRAINED", "source": name,
                "detail": f"upcoming fell {pct:.0f}% - {base_up:,} -> {now_up:,}",
            })

    for name in sorted(set(live) - set(base_sources)):
        notes.append(f"NEW    {name}: {int(live[name].get('upcoming') or 0):,} upcoming "
                     f"(not in baseline - rerun with --update-baseline to adopt it)")

    dead, total = ledger_summary()
    if total:
        base_dead = int(baseline.get("ledger_dead") or 0)
        arrow = ""
        if dead > base_dead:
            arrow = f"  ^ up {dead - base_dead} since the baseline"
        notes.append(f"LEDGER {dead}/{total} audited feeds marked dead{arrow} "
                     f"(most are rejected candidates - see FEED_DOWN for the wired-in ones)")

    # A configured feed going dark is a real regression and gets a problem row,
    # but only ONCE: the ones already in the baseline stay in the notes, so the
    # daily issue reports today's news rather than re-litigating the backlog.
    cd = configured_dead()
    if cd:
        known = set(baseline.get("configured_dead") or [])
        fresh = [d for d in cd if d["url"] not in known]
        for d in fresh:
            problems.append({
                "kind": "FEED_DOWN", "source": d["name"],
                "detail": f"`{d['url']}` ({d['category']}, {d['config']}) - {d['reason']}",
            })
        by_cat: dict[str, int] = {}
        for d in cd:
            by_cat[d["category"]] = by_cat.get(d["category"], 0) + 1
        spread = ", ".join(f"{k} x{v}" for k, v in sorted(by_cat.items(), key=lambda kv: -kv[1]))
        notes.append(f"FEEDS  {len(cd)} configured feed(s) currently dead: {spread}")
        for d in cd:
            if d["url"] not in {f["url"] for f in fresh}:
                notes.append(f"       (known) [{d['category']}] {d['url']} - {d['reason'][:60]}")

    return problems, notes, live


def render(problems, notes, live, baseline) -> str:
    """Human-readable report. Also the body of the GitHub issue."""
    out = []
    total_up = sum(int(r.get("upcoming") or 0) for r in live.values())
    out.append(f"**{len(live)} sources reporting · {total_up:,} upcoming events**")
    out.append(f"Baseline taken {baseline.get('taken_at', 'never')}")
    out.append("")

    if problems:
        out.append(f"### {len(problems)} source(s) need attention")
        out.append("")
        out.append("| | Source | What happened |")
        out.append("|---|---|---|")
        for p in problems:
            out.append(f"| `{p['kind']}` | **{p['source']}** | {p['detail']} |")
        out.append("")
        out.append("`SILENT` — the job still exits 0, but nothing new is arriving. "
                   "Usually an expired key or a feed that changed shape.  ")
        out.append("`MISSING` — the source has no rows left at all.  ")
        out.append("`DRAINED` — still ingesting, but a fraction of what it used to.  ")
        out.append("`FEED_DOWN` — a feed still listed in a `*_sources.json` that the "
                   "curator audit now can't read. Fix the URL, or retire it from the "
                   "config so the run stops paying for it.")
    else:
        out.append("### All baseline sources are healthy ✅")

    if notes:
        out.append("")
        out.append("### Notes")
        out.append("```")
        out.extend(notes)
        out.append("```")

    out.append("")
    out.append("<details><summary>Full per-source table</summary>")
    out.append("")
    out.append("| Source | Upcoming | Total | Added 72h | Last added |")
    out.append("|---|---:|---:|---:|---|")
    for name, r in sorted(live.items(), key=lambda kv: -int(kv[1].get("upcoming") or 0)):
        last = _parse_ts(r.get("last_added"))
        when = f"{last:%Y-%m-%d %H:%M}" if last else "-"
        out.append(f"| {name} | {int(r.get('upcoming') or 0):,} "
                   f"| {int(r.get('total') or 0):,} "
                   f"| {int(r.get('added_72h') or 0):,} | {when} |")
    out.append("")
    out.append("</details>")
    return "\n".join(out)


# --- main ------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--update-baseline", action="store_true",
                    help="record the current state as healthy and exit 0")
    ap.add_argument("--markdown", metavar="PATH",
                    help="also write the report as markdown to PATH")
    ap.add_argument("--drop-pct", type=float, default=0.6,
                    help="fractional fall in upcoming events that counts as DRAINED "
                         "(default 0.6 = a 60%% collapse)")
    ap.add_argument("--warn-only", action="store_true",
                    help="always exit 0, even when problems are found")
    args = ap.parse_args(argv)

    url = os.environ.get("SUPABASE_URL")
    key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
           or os.environ.get("SUPABASE_ANON_KEY"))
    if not url or not key:
        # Consistent with every adapter in this repo: no credentials is a skip,
        # not a failure. Keeps the check harmless in forks and local checkouts.
        print("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY unset - skipping health check.")
        return 0

    try:
        rows = fetch_source_stats(url, key)
    except requests.RequestException as ex:
        # Reaching the database is itself the check. If this fails, say so
        # loudly - that is exactly the silent-failure case this script exists
        # to catch.
        print(f"!! could not read source_stats(): {ex}", file=sys.stderr)
        return 0 if args.warn_only else 1

    if not rows:
        print("!! source_stats() returned no rows at all - the pipeline has "
              "ingested nothing, or the RPC is missing.", file=sys.stderr)
        return 0 if args.warn_only else 1

    now = datetime.now(timezone.utc)

    if args.update_baseline:
        dead, _ = ledger_summary()
        snapshot = {
            "_comment": "Written by mapsee_health_check.py --update-baseline. "
                        "This is what a healthy pipeline looked like at the time "
                        "shown. Regenerate after deliberately adding or retiring "
                        "a source; do not hand-edit counts. `stale_days` may be "
                        "hand-tuned per source and is preserved on regeneration.",
            "taken_at": now.strftime("%Y-%m-%d %H:%M UTC"),
            "ledger_dead": dead,
            # Wired-in feeds known to be dead at baseline time. Anything that
            # goes dark AFTER this becomes a FEED_DOWN problem; these stay in
            # the notes so the backlog is visible without being re-reported.
            "configured_dead": [d["url"] for d in configured_dead()],
            "sources": {},
        }
        prev = {}
        if os.path.exists(BASELINE):
            try:
                with open(BASELINE, encoding="utf-8") as fh:
                    prev = (json.load(fh) or {}).get("sources", {})
            except (OSError, json.JSONDecodeError):
                prev = {}
        for r in rows:
            name = r.get("source") or "?"
            entry = {
                "upcoming": int(r.get("upcoming") or 0),
                "total": int(r.get("total") or 0),
                "stale_days": stale_budget(name),
            }
            # keep any hand-tuned budget from the previous baseline
            if isinstance(prev.get(name), dict) and prev[name].get("stale_days"):
                entry["stale_days"] = int(prev[name]["stale_days"])
            snapshot["sources"][name] = entry
        with open(BASELINE, "w", encoding="utf-8") as fh:
            json.dump(snapshot, fh, indent=2, sort_keys=True)
            fh.write("\n")
        print(f"baseline written: {len(snapshot['sources'])} sources, "
              f"{sum(s['upcoming'] for s in snapshot['sources'].values()):,} upcoming")
        print(f"  -> {BASELINE}")
        return 0

    if not os.path.exists(BASELINE):
        print("No baseline yet. Run:  python mapsee_health_check.py --update-baseline")
        print("(after a run you are confident was healthy)")
        return 0

    with open(BASELINE, encoding="utf-8") as fh:
        baseline = json.load(fh)

    problems, notes, live = evaluate(rows, baseline, args.drop_pct, now)
    report = render(problems, notes, live, baseline)
    print(report)

    if args.markdown:
        with open(args.markdown, "w", encoding="utf-8") as fh:
            fh.write(report + "\n")

    if problems and not args.warn_only:
        print(f"\n!! {len(problems)} source(s) need attention", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
