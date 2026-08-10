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

WHERE THE NUMBERS COME FROM
---------------------------
`stats_snapshot_all()` — the precomputed snapshot from ../mapsee migration 0112,
refreshed every 6h by the Worker cron (`refreshStats` in ../mapsee/src/index.js,
`"crons": ["23 */6 * * *"]`).

NOT `source_stats()`, which this script called for its first eight runs and which
failed all eight. That RPC aggregates `public.events` on read, and 0112 records
the measurement that killed it: 8,956 events upcoming in a single day, 88,614
added in a week, and `source_stats(p_days => 1)` timing out at 3.4s against the
API role's ~3s ceiling. Postgres raises 57014, PostgREST renders that as a bare
HTTP 500, and this script rendered THAT as "could not reach the database - usual
causes: a rotated key, the project paused, or a transient network failure." None
of which it was. The product had already moved to the snapshot; only the
aggregator was still calling the dead function.

Two lessons are wired in below. Read the RPC the product reads, so there is one
answer to "what landed" rather than two that can disagree. And carry the server's
error body VERBATIM into the report — `{"code":"57014"}` names the fault on day
one, where a status code alone bought four days of identical, unactionable
alerts.

WHAT A "SOURCE" IS HERE
-----------------------
`external_source`, which mapsee_supabase_sync.py sets to the literal 'mapsee' on
every row it writes; community-created events carry NULL and the snapshot
coalesces those to 'community'. So this check sees the aggregator as ONE bucket.
It answers "has the pipeline stopped delivering", not "has the Meetup adapter
gone quiet" — per-adapter provenance is not persisted anywhere in public.events
(external_id is a bare SHA-1 fingerprint with no source prefix). Catching a
single dead adapter needs a `source` column on the event row; until that exists,
`catalog_curate.py audit` + FEED_DOWN below is the per-feed signal.

WHAT IT CHECKS
--------------
  1. SILENT  — a baseline source whose newest event is older than its staleness
               budget. This is the big one: the feed still parses, the job still
               exits 0, but nothing new has arrived in days.
  2. MISSING — a baseline source that has no rows at all any more.
  3. DRAINED — a baseline source whose upcoming-event count has collapsed past
               `--drop-pct` versus the baseline. Catches a feed that went from
               2,000 events to 12 without going fully dark.
  4. FEED_DOWN — a feed still wired into a *_sources.json that the curator audit
               can no longer read. This is the per-feed granularity the
               per-source numbers cannot give.
  5. LEDGER  — how many entries catalog_curate.py has marked dead, and whether
               that number is growing. Advisory only; does not fail the run.

New sources appearing are reported as NEW and never fail the check — growth is
not a problem.

EXIT CODES
----------
  0  healthy (or nothing to check yet, or --warn-only)
  1  a source is unhealthy — SILENT / MISSING / DRAINED / FEED_DOWN
  2  the check could not run at all: the database was unreachable, the RPC is
     missing, or the snapshot is stale. NOTHING was evaluated, so this says
     nothing about the pipeline. It is a different problem with a different fix
     and source-health.yml files it as a different issue — reporting it as
     "one or more sources have gone quiet" is a false statement, and that is
     exactly what the thread on issue #3 became.

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

    # write a baseline ONLY if one does not exist yet (what CI runs, so the
    # check cannot sit at "no baseline, nothing evaluated" for ever)
    python mapsee_health_check.py --seed-baseline

Environment: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY (the anon key also works —
stats_snapshot_all() is granted to anon — but CI already has the service key).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
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

# The snapshot the product reads (../mapsee migration 0112). Cheap: three rows.
SNAPSHOT_RPC = "stats_snapshot_all"
# The pre-0112 live aggregate. Kept ONLY as a fallback for a deployment small
# enough that 0112 was never applied — against production it times out, which is
# the whole reason this script stopped calling it first.
LEGACY_RPC = "source_stats"

# The Worker recomputes the snapshot every 6h. Four consecutive misses means the
# cron is down, and every freshness number below is frozen at whatever it said
# then — SILENT would start firing about sources that are delivering fine. So a
# stale snapshot is "the check cannot run", not "a source is quiet".
SNAPSHOT_MAX_AGE_H = 24

# Transient 5xx and connection resets happen. Four days of the SAME 500 is not
# transient, and the report has to be able to tell the difference.
ATTEMPTS = 3

EXIT_OK = 0
EXIT_UNHEALTHY = 1
EXIT_CANNOT_RUN = 2


class CheckCannotRun(Exception):
    """Nothing could be evaluated. Carries a report-ready title and detail.

    Deliberately NOT the same outcome as "a source is unhealthy": the two want
    different fixes, different issue threads and different exit codes.
    """

    def __init__(self, title: str, detail: str, hint: str = ""):
        super().__init__(f"{title}: {detail}")
        self.title = title
        self.detail = detail
        self.hint = hint


# --- data ------------------------------------------------------------------

def _error_detail(r) -> str:
    """The server's own explanation, verbatim.

    PostgREST answers a failed RPC with {"code","message","details","hint"} and
    the code is the diagnosis: 57014 is a statement timeout, PGRST202 is a
    function that is not there, 42703 is a column the function's body references
    and the table no longer has. Discarding that body and reporting `500` is how
    a dead RPC read as "a rotated key, or maybe the network" for four days.
    """
    body = (r.text or "").strip()
    if not body:
        return "(empty response body)"
    try:
        parsed = json.loads(body)
    except ValueError:
        return body[:400]
    if isinstance(parsed, dict):
        bits = [str(parsed[k]) for k in ("code", "message", "details", "hint")
                if parsed.get(k)]
        if bits:
            return " · ".join(bits)[:400]
    return body[:400]


def _rpc(url: str, key: str, name: str, body: dict | None = None):
    """POST an RPC, retrying only what is worth retrying.

    5xx and connection failures get ATTEMPTS tries with a widening gap; a 4xx is
    a settled answer (missing function, bad key) and retrying it just delays the
    report. Raises CheckCannotRun when the request never completed at all.
    """
    endpoint = url.rstrip("/") + f"/rest/v1/rpc/{name}"
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    last_network = ""
    for attempt in range(ATTEMPTS):
        try:
            r = requests.post(endpoint, headers=headers, json=body or {},
                              timeout=TIMEOUT)
        except requests.RequestException as ex:
            last_network = f"{type(ex).__name__}: {ex}"
        else:
            if r.status_code < 500:
                return r                       # settled, success or not
            last_network = ""
            if attempt == ATTEMPTS - 1:
                return r                       # let the caller report the body
        if attempt < ATTEMPTS - 1:
            time.sleep(2 ** (attempt + 1))     # 2s, then 4s
    raise CheckCannotRun(
        f"Could not reach `{name}()`",
        last_network or "no response after retries",
        "The database was unreachable on every attempt. If the next scheduled "
        "run is green, it was transient.")


def _normalise(rows) -> list[dict]:
    """Keep only rows that name a source. Tolerates both RPC shapes.

    0057 returned (source, upcoming, total, last_added, added_72h); 0111 and the
    0112 snapshot return (source, upcoming, added_24h, added_7d, last_added).
    Nothing below requires a field that only one of them has.
    """
    return [r for r in rows if isinstance(r, dict) and r.get("source")]


# The Worker records each cron task's last run as a `cron:<task>` row in the
# same snapshot table (../mapsee `runCronTask`). This check is the only thing
# that reads them on a schedule: /stats can show them too, but it is
# STATS_TOKEN-gated and nobody was watching it — which is exactly how
# refresh_stats stayed dead for six days.
CRON_PREFIX = "cron:"


def cron_report(by_kind: dict) -> list[str]:
    """One line per cron task: what it did, when, and why if it failed.

    Returns [] when the Worker has recorded nothing — a deployment older than
    the bookkeeping, or a cron that has never fired. Silence here is genuinely
    ambiguous, so it is not reported as either good or bad news.
    """
    now = datetime.now(timezone.utc)
    out = []
    for kind, row in sorted(by_kind.items()):
        if not str(kind).startswith(CRON_PREFIX):
            continue
        task = str(kind)[len(CRON_PREFIX):]
        payload = row.get("payload")
        payload = payload if isinstance(payload, dict) else {}
        at = _parse_ts(row.get("computed_at"))
        when = f"{(now - at).total_seconds() / 3600:.0f}h ago" if at else "never"
        # HOW LONG it ran is the diagnosis, not decoration. A 57014 at ~3s means
        # the statement died on the API role's default ceiling; the same error at
        # ~240s would mean the raised timeout in ../mapsee 0112 took effect and
        # the query genuinely needs longer than four minutes. Those are different
        # bugs with different fixes, and runCronTask has always recorded the
        # number — this line just stopped throwing it away.
        ms = payload.get("ms")
        took = f", {int(ms) / 1000:.1f}s" if isinstance(ms, (int, float)) else ""
        if payload.get("ok") is False:
            state = f"FAILED - {payload.get('error') or 'no reason recorded'}"
        elif payload.get("skipped"):
            state = f"skipped - {payload['skipped']}"
        else:
            state = "ok"
        out.append(f"CRON   {task}: {state} ({when}{took})")
    return out


def fetch_stats(url: str, key: str) -> tuple[list[dict], datetime | None, str, list[str]]:
    """(rows, computed_at, which RPC answered, cron lines). Raises CheckCannotRun."""
    r = _rpc(url, key, SNAPSHOT_RPC)

    if r.status_code == 404:
        # 0112 not applied — a small deployment, or a fresh project. Fall back to
        # computing live, which is correct there and hopeless on production.
        return _fetch_legacy(url, key)

    if not r.ok:
        raise CheckCannotRun(
            f"`{SNAPSHOT_RPC}()` returned HTTP {r.status_code}",
            _error_detail(r),
            "401/403 is a rotated or revoked key. Anything else is a fault in "
            "the RPC itself — the code above names it.")

    try:
        payload = r.json()
    except ValueError:
        raise CheckCannotRun(f"`{SNAPSHOT_RPC}()` returned unparseable JSON",
                             (r.text or "")[:400]) from None

    by_kind = {row.get("kind"): row for row in (payload or [])
               if isinstance(row, dict)}
    snap = by_kind.get("sources")
    if not snap:
        raise CheckCannotRun(
            "The stats snapshot has never been computed",
            "`stats_snapshot` holds no `sources` row.",
            "Nothing has run `refresh_stats()`. The Worker cron in ../mapsee "
            "(`\"crons\": [\"23 */6 * * *\"]`) does it in production; by hand it "
            "is `select public.refresh_stats(30);` with the service role.")

    computed_at = _parse_ts(snap.get("computed_at"))
    rows = _normalise(snap.get("payload") or [])
    cron = cron_report(by_kind)
    if computed_at:
        age_h = (datetime.now(timezone.utc) - computed_at).total_seconds() / 3600
        if age_h > SNAPSHOT_MAX_AGE_H:
            # THE case this was written for. "The snapshot is 142h old" is a
            # symptom; the Worker knows the cause and records it beside the
            # snapshot, so quote it here rather than sending someone to
            # `wrangler tail` for something already in the database.
            why = ("\n".join(cron) if cron else
                   "The Worker has recorded no cron status at all — either it "
                   "predates the bookkeeping in ../mapsee `runCronTask`, or the "
                   "cron is not firing, which nothing else would show.")
            raise CheckCannotRun(
                f"The stats snapshot is {age_h:.0f}h old",
                f"Last computed {computed_at:%Y-%m-%d %H:%M} UTC; the budget is "
                f"{SNAPSHOT_MAX_AGE_H}h (the Worker recomputes every 6h).\n\n"
                f"{why}",
                "Every freshness number is frozen at that moment, so evaluating "
                "them would report sources as silent that are delivering "
                "normally. Fix the `refresh_stats` cron, not the sources.")
    return rows, computed_at, SNAPSHOT_RPC, cron


def _fetch_legacy(url: str, key: str) -> tuple[list[dict], datetime | None, str, list[str]]:
    """The pre-0112 path. Live aggregate; times out on any real dataset."""
    r = _rpc(url, key, LEGACY_RPC)
    if not r.ok:
        raise CheckCannotRun(
            f"`{LEGACY_RPC}()` returned HTTP {r.status_code}",
            _error_detail(r),
            f"`{SNAPSHOT_RPC}()` is absent too, so this project is missing "
            f"../mapsee migration 0112. Code 57014 means {LEGACY_RPC}() timed "
            f"out aggregating public.events — apply 0112 and schedule "
            f"`refresh_stats()`; it cannot be tuned into working.")
    try:
        # No cron rows on this path: they live in the snapshot table 0112 adds,
        # and reaching here means 0112 was never applied.
        return _normalise(r.json() or []), None, LEGACY_RPC, []
    except ValueError:
        raise CheckCannotRun(f"`{LEGACY_RPC}()` returned unparseable JSON",
                             (r.text or "")[:400]) from None


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
            # Not "the column is empty" — the snapshot computes last_added as
            # `max(created_at) filter (where created_at > now() - 30 days)`, so
            # NULL is a positive finding: nothing at all has arrived in a month.
            problems.append({
                "kind": "SILENT", "source": name,
                "detail": "nothing added in the last 30 days (the snapshot's window)",
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


def render(problems, notes, live, baseline, computed_at=None, via="") -> str:
    """Human-readable report. Also the body of the GitHub issue."""
    out = []
    total_up = sum(int(r.get("upcoming") or 0) for r in live.values())
    out.append(f"**{len(live)} sources reporting · {total_up:,} upcoming events**")
    out.append(f"Baseline taken {baseline.get('taken_at', 'never')}")
    if computed_at:
        age_h = (datetime.now(timezone.utc) - computed_at).total_seconds() / 3600
        out.append(f"Numbers from `{via}`, computed "
                   f"{computed_at:%Y-%m-%d %H:%M} UTC ({age_h:.0f}h ago)")
    elif via:
        out.append(f"Numbers from `{via}` (computed live)")
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
    # `total` and `added_72h` were 0057's columns and no longer exist: an
    # all-time count over public.events cannot be computed inside the statement
    # timeout, which is why 0112 stopped trying. 24h/7d are what the snapshot
    # carries, and they answer the same question better.
    out.append("| Source | Upcoming | Added 24h | Added 7d | Last added |")
    out.append("|---|---:|---:|---:|---|")
    for name, r in sorted(live.items(), key=lambda kv: -int(kv[1].get("upcoming") or 0)):
        last = _parse_ts(r.get("last_added"))
        when = f"{last:%Y-%m-%d %H:%M}" if last else "-"
        out.append(f"| {name} | {int(r.get('upcoming') or 0):,} "
                   f"| {int(r.get('added_24h') or 0):,} "
                   f"| {int(r.get('added_7d') or 0):,} | {when} |")
    out.append("")
    out.append("</details>")
    return "\n".join(out)


# --- main ------------------------------------------------------------------

def write_markdown(path, text):
    """Write the report, if one was asked for.

    THE FILE IS THE CONTRACT. source-health.yml treats any non-zero exit as
    "unhealthy" and then `cat`s this file into a GitHub issue under `set -e`.
    Two failure paths used to return 1 without writing it -- an unreachable
    database and an empty source_stats() -- so the notifier died on a missing
    file and the run failed with `cat: health_report.md: No such file`. The
    alarm broke in precisely the case it exists to raise. Every path that can
    exit non-zero writes something now.
    """
    if not path:
        return
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text.rstrip() + "\n")
    except OSError as ex:
        print(f"!! could not write {path}: {ex}", file=sys.stderr)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--update-baseline", action="store_true",
                    help="record the current state as healthy and exit 0")
    ap.add_argument("--seed-baseline", action="store_true",
                    help="write a baseline only if none exists yet, then exit 0 "
                         "(safe to leave on in CI: it never overwrites)")
    ap.add_argument("--markdown", metavar="PATH",
                    help="also write the report as markdown to PATH")
    ap.add_argument("--drop-pct", type=float, default=0.6,
                    help="fractional fall in upcoming events that counts as DRAINED "
                         "(default 0.6 = a 60%% collapse)")
    ap.add_argument("--warn-only", action="store_true",
                    help="always exit 0, even when problems are found")
    args = ap.parse_args(argv)
    try:
        return _run(args)
    except CheckCannotRun as ex:
        # NOT exit 1. Nothing was evaluated, so claiming a source went quiet
        # would be a fabrication - and for four days on issue #3 it was one.
        body = [f"### {ex.title}", "", "```", ex.detail, "```"]
        if ex.hint:
            body += ["", ex.hint]
        body += ["", "**Nothing was evaluated**, so this says nothing about "
                     "whether any source is healthy - only that the check "
                     "itself could not run."]
        print(f"!! {ex.title}: {ex.detail}", file=sys.stderr)
        write_markdown(args.markdown, "\n".join(body))
        return EXIT_OK if args.warn_only else EXIT_CANNOT_RUN
    except Exception:
        # Same contract as write_markdown's note. A traceback exits non-zero,
        # the workflow reads that as "unhealthy", and with no report the
        # notifier dies on a missing file - so a bug in here looked exactly
        # like a broken notifier rather than like a bug in here.
        tb = traceback.format_exc()
        print(tb, file=sys.stderr)
        write_markdown(args.markdown,
                       "### The health check crashed\n\n"
                       "It exited before evaluating any source, so this says "
                       "nothing about whether the pipeline is healthy - only "
                       "that the check itself is broken.\n\n"
                       f"```\n{tb.rstrip()}\n```")
        return EXIT_OK if args.warn_only else EXIT_CANNOT_RUN


def _run(args):
    url = os.environ.get("SUPABASE_URL")
    key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
           or os.environ.get("SUPABASE_ANON_KEY"))
    if not url or not key:
        # Consistent with every adapter in this repo: no credentials is a skip,
        # not a failure. Keeps the check harmless in forks and local checkouts.
        print("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY unset - skipping health check.")
        return EXIT_OK

    rows, computed_at, via, cron = fetch_stats(url, key)   # raises CheckCannotRun

    if not rows:
        raise CheckCannotRun(
            f"`{via}` reported no sources at all",
            "The snapshot computed successfully and contains zero rows.",
            "Either the pipeline has ingested nothing whatsoever, or every row "
            "in public.events is hidden. Both affect every source at once, so "
            "this is one problem rather than forty.")

    now = datetime.now(timezone.utc)

    if args.update_baseline or (args.seed_baseline and not os.path.exists(BASELINE)):
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
                "stale_days": stale_budget(name),
            }
            # keep any hand-tuned budget from the previous baseline
            if isinstance(prev.get(name), dict) and prev[name].get("stale_days"):
                entry["stale_days"] = int(prev[name]["stale_days"])
            snapshot["sources"][name] = entry
        with open(BASELINE, "w", encoding="utf-8") as fh:
            json.dump(snapshot, fh, indent=2, sort_keys=True)
            fh.write("\n")
        seeded = "seeded" if args.seed_baseline and not args.update_baseline else "written"
        print(f"baseline {seeded}: {len(snapshot['sources'])} sources, "
              f"{sum(s['upcoming'] for s in snapshot['sources'].values()):,} upcoming")
        print(f"  -> {BASELINE}")
        write_markdown(args.markdown,
                       f"### Baseline {seeded}\n\n"
                       f"`{len(snapshot['sources'])}` source(s), "
                       f"{sum(s['upcoming'] for s in snapshot['sources'].values()):,} "
                       f"upcoming events, from `{via}`.\n\n"
                       f"This run recorded what healthy looks like; it did not "
                       f"evaluate anything. The next run is the first real check.")
        return EXIT_OK

    if not os.path.exists(BASELINE):
        # Exit 0 so a fresh checkout is not a red tick, but say it in the REPORT
        # too. This branch is why source-health could be green and blind at the
        # same time: SILENT / MISSING / DRAINED all need the baseline, so with no
        # file the workflow passed every day having checked precisely nothing,
        # and nobody looked at stdout to find out. `--seed-baseline` in CI means
        # it now lasts one run instead of for ever.
        msg = ("### No baseline — nothing was checked\n\n"
               "`source_health_baseline.json` does not exist, so there is no "
               "record of what healthy looks like to compare against. This run "
               "passed without evaluating a single source.\n\n"
               "Fix: `python mapsee_health_check.py --update-baseline` after a "
               "run you trust, and commit the file.")
        print(msg)
        write_markdown(args.markdown, msg)
        return EXIT_OK

    with open(BASELINE, encoding="utf-8") as fh:
        baseline = json.load(fh)

    problems, notes, live = evaluate(rows, baseline, args.drop_pct, now)
    # Cron status rides along even on a healthy run. A task that failed once
    # while the snapshot is still inside its budget is not an outage and must
    # not fail the check — but it is the early warning for the one that is
    # coming, and it costs nothing to print since the rows are already in hand.
    notes.extend(cron)
    report = render(problems, notes, live, baseline, computed_at, via)
    print(report)
    write_markdown(args.markdown, report)

    if problems and not args.warn_only:
        print(f"\n!! {len(problems)} source(s) need attention", file=sys.stderr)
        return EXIT_UNHEALTHY
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
