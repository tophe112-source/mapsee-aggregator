#!/usr/bin/env python3
"""
mapsee_spam_purge.py — remove the advertisements that got in before the gate did.

    python mapsee_spam_purge.py                     # report only, the default
    python mapsee_spam_purge.py --apply

mapsee_spam.py stops new ones at ingest. It reaches nothing already stored, for
the reason CLAUDE.md gives about classifier fixes: `--only-new` is the default in
CI, so a scheduled run can only ADD, and Wednesday's full run re-reads the
SOURCES rather than re-applying our own rules to rows we already hold. A row that
arrived in August is in the table until something goes and gets it.

So this is the backfill half, and it is the same shape as mapsee_reclassify.py:
one predicate, imported from the same module the ingest uses, applied to rows
that predate it. Not a second copy of the rules — a second copy would drift, and
the drift would be invisible because both would look right in isolation.

TWO ACTIONS, NOT ONE, and they follow the split in mapsee_spam.py exactly:

  * A row spam_reason() refuses is DELETED. It is an advertisement.
  * A row whose only problem is an end date years out has that END CLEARED, and
    keeps its place. That is not a verdict about the row (see the 487-day
    Extinction Rebellion listing in the module header), and clearing the end is
    what lets mapsee_cleanup.py reach it at all — its filter is `starts_at <
    cutoff AND (ends_at IS NULL OR ends_at < cutoff)`, so an end in 2036 is a pin
    nothing in this pipeline can ever remove. Most of what this touches then
    disappears on the next cleanup run without this script deleting anything.

WHY IT PAGES BY starts_at AND FILTERS IN PYTHON. The obvious implementation is a
`description=ilike.*…*` filter and letting Postgres do the work. Measured: that
is a sequential scan over the whole events table and it does not finish inside
the statement timeout — every attempt comes back 57014. The spam predicate is
also not expressible as a `like` anyway (it is three regexes and a date
subtraction). So the walk is over the INDEX migration 0113 already provides
(external_source, starts_at), a window at a time, and the judging happens here.

SAFETY, in three layers, because this deletes on a CONTENT judgement.

  * Scoped to `external_source = 'mapsee'` on every request, like
    mapsee_cleanup.py. Community events (external_source IS NULL) cannot be
    matched and therefore cannot be deleted.
  * `--apply` is required to write anything. Without it this reports and exits.
  * `--max-share` refuses to delete when the matched fraction is implausibly
    high. This is the one that makes it safe to SCHEDULE: the failure to fear is
    not a spam wave, it is a widened regex, and a widened regex looks exactly
    like a very successful run.

Env:  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

from mapsee_spam import implausible_end, spam_reason

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

# Same reasoning as mapsee_cleanup.BATCH_DEFAULT: ids ride back inside
# `id=in.(…)` in the URL, and a UUID plus its separator is ~37 bytes.
BATCH = 100
PAGE = 500                    # rows read per window step
TIMEOUT_CODE = "57014"


def _timed_out(resp) -> bool:
    return resp.status_code >= 500 and TIMEOUT_CODE in (resp.text or "")


def too_many(matched: int, seen: int, max_share: float, min_sample: int) -> bool:
    """Is this run deleting an implausible share of what it read?

    A free function so it can be graded without a database — the tripwire is the
    part that has to work on the day something else has already gone wrong, and
    logic reachable only through a live PostgREST call is logic nobody tests.
    """
    if seen < min_sample:
        return False           # a share off a handful of rows is noise
    return (matched / seen) > max_share if seen else False


def main() -> int:
    ap = argparse.ArgumentParser(description="Remove advertisements already in the events table.")
    ap.add_argument("--apply", action="store_true",
                    help="Actually delete and clear. Without it, nothing is written.")
    # FIVE YEARS BACK, NOT THIRTY DAYS, and the reported row is why. "Shatru
    # Nashak…" STARTED on 2026-04-11 — five months before anyone noticed it —
    # and was still on the map because its end was 2036 and mapsee_cleanup can
    # only delete a row whose end has also passed. The rows this script most
    # needs to reach are, by construction, the ones with a past start that
    # cleanup could not touch, so a window that begins near today misses exactly
    # the population it was written for. It is cheap: cleanup runs weekly, so
    # almost nothing else is left back there to walk past.
    ap.add_argument("--from-days-ago", type=int, default=1825,
                    help="Start the walk this far back (default 1825 = 5 years). "
                         "The forever-pins have a PAST start; that is why they are "
                         "still here.")
    ap.add_argument("--max-seconds", type=int, default=900)
    ap.add_argument("--show", type=int, default=40, help="Titles to print (default 40).")
    # THE GUARD THAT MAKES THIS SAFE TO SCHEDULE, and it is a SHARE rather than a
    # count on purpose. An absolute cap has to be re-tuned every time the
    # catalogue grows and is wrong in both directions while you are guessing at
    # it; a share is self-calibrating and expresses the actual invariant, which
    # is that spam is a MINORITY of what this pipeline imports.
    #
    # The failure it exists for is not "spam spiked". It is "somebody widened a
    # regex and it now matches ordinary titles" — the exact mistake made while
    # writing mapsee_spam.py, where rejecting on span alone turned a real 487-day
    # Extinction Rebellion listing into an advertisement. That was caught by
    # reading an audit by hand. Scheduled and unattended, it would have deleted
    # real events every night and the only symptom would have been the map
    # getting quieter.
    #
    # Measured 2026-09-03: 11.8% of the MOBILIZON supply, which is a small
    # fraction of a catalogue also carrying Ticketmaster, OSM, Eventbrite and
    # forty other adapters. 5% of everything read is comfortably above the real
    # number and far below "the predicate has stopped meaning what it meant".
    ap.add_argument("--max-share", type=float, default=0.05,
                    help="Refuse to write if more than this share of rows read are "
                         "matched (default 0.05). A high share means the predicate "
                         "changed meaning, not that spam did.")
    ap.add_argument("--min-sample", type=int, default=200,
                    help="Below this many rows read, --max-share is not evaluated: a "
                         "share off a handful of rows is noise (default 200).")
    a = ap.parse_args()

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not (url and key):
        sys.exit("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY (server-side secrets).")
    auth = {"apikey": key, "Authorization": f"Bearer {key}"}
    root = url.rstrip("/") + "/rest/v1/events"

    # SAFETY: on every request, without exception. Community rows carry
    # external_source IS NULL and can never match this.
    SCOPE = "external_source=eq.mapsee"
    assert "external_source=eq.mapsee" == SCOPE, "refusing an unscoped walk"

    cursor = (datetime.now(timezone.utc) - timedelta(days=max(0, a.from_days_ago))
              ).strftime("%Y-%m-%dT%H:%M:%SZ")
    started = time.monotonic()
    seen = 0
    seen_ids: set[str] = set()
    doomed: list[tuple[str, str, str]] = []       # (id, reason, title)
    unbounded: list[tuple[str, int, str]] = []    # (id, span, title)

    print(f"Walking aggregator events from {cursor} forward…")
    while time.monotonic() - started < a.max_seconds:
        q = (f"{root}?{SCOPE}&starts_at=gte.{cursor}"
             f"&select=id,title,description,starts_at,ends_at"
             f"&order=starts_at.asc&limit={PAGE}")
        r = requests.get(q, headers=auth, timeout=120)
        if _timed_out(r):
            sys.exit("The windowed read timed out. Check migration 0113 "
                     "(events_aggregator_cleanup_idx) is applied — it is what makes "
                     "this walk index-backed.")
        if r.status_code >= 300:
            sys.exit(f"Read failed [{r.status_code}]: {r.text[:300]}")
        rows = r.json() or []
        if not rows:
            break
        for row in rows:
            # gte, NOT gt, so a run of events sharing one exact starts_at cannot
            # straddle the window boundary and lose its tail — which means the
            # row the cursor sits on is re-read on the next step, hence the set.
            # Judging it twice would be harmless; COUNTING it twice would quietly
            # make the report wrong, and the report is the thing a person reads
            # before allowing a content-based delete.
            if row["id"] in seen_ids:
                continue
            seen_ids.add(row["id"])
            seen += 1
            why = spam_reason(row.get("title"), row.get("description"),
                              row.get("starts_at"), row.get("ends_at"))
            if why:
                doomed.append((row["id"], why, (row.get("title") or "")[:70]))
                continue
            span = implausible_end(row.get("starts_at"), row.get("ends_at"))
            if span is not None:
                unbounded.append((row["id"], span, (row.get("title") or "")[:70]))
        # A WINDOW, NOT AN OFFSET. offset= re-walks everything before it on each
        # step, which is what turns a long table into a timeout halfway through.
        last = rows[-1]["starts_at"]
        if len(rows) < PAGE or last == cursor:
            break                     # short page, or a whole page at one instant
        cursor = last

    print(f"read {seen} row(s)")
    print(f"  {len(doomed)} advertisement(s) to delete")
    print(f"  {len(unbounded)} row(s) whose end date is not a fact")
    for _id, why, title in doomed[:a.show]:
        print(f"    DELETE [{why}] {title}")
    if len(doomed) > a.show:
        print(f"    … and {len(doomed) - a.show} more")
    for _id, span, title in unbounded[:a.show]:
        print(f"    CLEAR END [{span} days] {title}")
    if len(unbounded) > a.show:
        print(f"    … and {len(unbounded) - a.show} more")

    if not a.apply:
        print()
        print("Report only. Re-run with --apply to delete and clear.")
        return 0

    # Checked on DELETIONS only. Clearing an end date is reversible in the sense
    # that matters — the row stays, the sync refills it from the source on the
    # next full refresh — so it does not deserve a tripwire that stops the run.
    share = (len(doomed) / seen) if seen else 0.0
    if too_many(len(doomed), seen, a.max_share, a.min_sample):
        print()
        print(f"REFUSING TO WRITE: {len(doomed)} of {seen} rows ({share:.1%}) matched, "
              f"over the {a.max_share:.0%} ceiling.")
        print("That is not what a spam rate looks like. Read the titles above before "
              "anything is deleted — the likely cause is a rule in mapsee_spam.py that "
              "now matches ordinary listings, not a change at the sources.")
        print("If the titles really are all advertisements, re-run with a higher "
              "--max-share and say so in the run's notes.")
        return 1

    deleted = 0
    for i in range(0, len(doomed), BATCH):
        ids = [d[0] for d in doomed[i:i + BATCH]]
        resp = requests.delete(f"{root}?{SCOPE}&id=in.({','.join(ids)})",
                               headers=dict(auth, Prefer="return=minimal,count=exact"),
                               timeout=300)
        if resp.status_code >= 300:
            sys.exit(f"Delete failed [{resp.status_code}]: {resp.text[:300]}")
        try:
            deleted += int(resp.headers.get("Content-Range", "*/0").split("/")[-1])
        except ValueError:
            deleted += len(ids)
        print(f"  deleted {deleted}…", flush=True)

    cleared = 0
    for i in range(0, len(unbounded), BATCH):
        ids = [u[0] for u in unbounded[i:i + BATCH]]
        resp = requests.patch(f"{root}?{SCOPE}&id=in.({','.join(ids)})",
                              headers=dict(auth, **{"Content-Type": "application/json",
                                                    "Prefer": "return=minimal,count=exact"}),
                              json={"ends_at": None}, timeout=300)
        if resp.status_code >= 300:
            sys.exit(f"Clearing ends_at failed [{resp.status_code}]: {resp.text[:300]}")
        try:
            cleared += int(resp.headers.get("Content-Range", "*/0").split("/")[-1])
        except ValueError:
            cleared += len(ids)
        print(f"  cleared {cleared}…", flush=True)

    print(f"Deleted {deleted} advertisement(s); cleared the end date on {cleared} row(s). "
          f"The cleared ones leave on mapsee_cleanup.py's next run once their start is past.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
