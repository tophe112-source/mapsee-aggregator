#!/usr/bin/env python3
"""
mapsee_uncancel.py - put back imported rows the pipeline CANCELLED, for the day a
rule misfires.

mapsee_supabase_sync cancels an unclaimed imported row (cancelled_at AND
hidden_at, the pair ../mapsee's own cancel_event writes) when the store carries a
tombstone for it (a publisher's STATUS:CANCELLED, eventStatus, a "CANCELLED -"
title, Ticketmaster's status) or, under --retire-absent, when a community-centre
timetable read whole stops listing a session. The sync lifts its own cancellation
when the row is listed live again, but only a day later (UNCANCEL_AFTER), and
absence has a circuit breaker rather than a review. If a source glitch slips
under the breaker, or a rule turns out wrong, this puts the rows back NOW.

WHAT IT TOUCHES, and why that is safe: rows with external_source 'mapsee',
claimed_at null, and cancelled_at at or after --since. cancelled_at on an
unclaimed import is only ever written by this pipeline: mapsee_prune_cancelled,
the retire_* scripts, the sync's legacy re-key and an organizer take-down set
hidden_at ALONE. And the sync writes BOTH stamps with one value and never
re-stamps either, so a row whose hidden_at no longer equals its cancelled_at was
hidden again by someone else on top of our cancellation (a take-down, a prune):
it is listed, counted, and never lifted here. The exact stamps travel in the
PATCH filter with the ownership conditions, so a row claimed or hidden between
the read and the write is left alone by the database itself.

A ROW WHOSE SOURCE STILL SAYS CANCELLED COMES BACK OFF at the next sync, as it
should. Fix the rule first (or park the source), then run this.

Windowed by starts_at like mapsee_retire_online_events and mapsee_reclassify:
deep OFFSET paging on this table dies under the statement timeout.

Env:  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
Run:  python mapsee_uncancel.py --since 2026-10-06T06:00:00Z           # dry run, the default
      python mapsee_uncancel.py --since 2026-10-06T06:00:00Z --apply
"""
import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

PAGE = 500
PATCH_IDS = 100          # ids per PATCH - the filter travels in the URL
TIMEOUT_CODE = "57014"   # postgres: canceling statement due to statement timeout


def sb(path, method="GET", body=None, prefer=""):
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None)
    req.add_header("apikey", SERVICE_KEY)
    req.add_header("authorization", f"Bearer {SERVICE_KEY}")
    req.add_header("content-type", "application/json")
    if prefer:
        req.add_header("prefer", prefer)
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read().decode("utf-8", "replace")
        return json.loads(raw) if raw.strip() else None


def since_stamp(text):
    """--since as a UTC stamp. A bare date is that day's 00:00Z; a time with no
    offset is refused, because "since 06:00" in the wrong zone lifts too much."""
    s = str(text).strip()
    if len(s) == 10:
        s += "T00:00:00+00:00"
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if d.tzinfo is None:
        raise ValueError("--since needs a date, or a time with Z or an offset")
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _same_instant(a, b):
    """Both stamps present and the same instant (the sync writes them as one)."""
    if not a or not b:
        return False
    try:
        fa = datetime.fromisoformat(str(a).replace("Z", "+00:00"))
        fb = datetime.fromisoformat(str(b).replace("Z", "+00:00"))
    except ValueError:
        return False
    return fa == fb


def ours(since):
    """The filter every read AND every write carries."""
    q = urllib.parse.quote(since, safe="")
    return f"external_source=eq.mapsee&claimed_at=is.null&cancelled_at=gte.{q}"


def run(args, sb=sb, now=None, out=print):
    since = since_stamp(args.since)
    written, failed, rehidden = [0], [0], [0]

    def flush(rows):
        if not rows or not args.apply:
            return 0
        done = 0
        by_stamp = {}
        for r in rows:
            by_stamp.setdefault(str(r.get("cancelled_at")), []).append(str(r["id"]))
        for st, ids in sorted(by_stamp.items()):
            q_st = urllib.parse.quote(st, safe="")
            for i in range(0, len(ids), PATCH_IDS):
                chunk = ids[i:i + PATCH_IDS]
                try:
                    # return=representation: count the rows the database actually
                    # changed (one claimed or re-hidden in between is not one lifted).
                    got = sb(f"events?id=in.({','.join(chunk)})&{ours(since)}"
                             f"&cancelled_at=eq.{q_st}&hidden_at=eq.{q_st}&select=id", "PATCH",
                             {"cancelled_at": None, "hidden_at": None}, prefer="return=representation")
                    n = len(got) if isinstance(got, list) else len(chunk)
                    written[0] += n
                    done += n
                except Exception as e:                # noqa: BLE001
                    failed[0] += len(chunk)
                    out(f"    PATCH of {len(chunk)} failed: {type(e).__name__}")
        return done

    now = time.time() if now is None else now
    start, end, step = now - args.back * 86400, now + args.days * 86400, 86400 * 4
    t, pages, found, skipped = start, 0, 0, []
    examples = []
    while t < end and pages < args.max_pages:
        w_a = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
        w_b = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(min(t + step, end)))
        q = (f"events?select=id,title,starts_at,cancelled_at,hidden_at&{ours(since)}"
             f"&starts_at=gte.{w_a}&starts_at=lt.{w_b}&order=starts_at.asc,id.asc&limit={PAGE}")
        offset = 0
        while True:
            rows = None
            for attempt in range(3):
                try:
                    rows = sb(q + f"&offset={offset}") or []
                    break
                except Exception as e:                # noqa: BLE001
                    if TIMEOUT_CODE in str(e) or attempt == 2:
                        out(f"  window {w_a[:10]} offset {offset} failed ({type(e).__name__})")
                        skipped.append(w_a[:10])
                        break
                    time.sleep(1.5 * (attempt + 1))
            if not rows:
                break
            ids = [str(r["id"]) for r in rows]
            found += len(ids)
            # Ours and untouched: both stamps the same instant (see the header).
            mine = [r for r in rows if _same_instant(r.get("cancelled_at"), r.get("hidden_at"))]
            rehidden[0] += len(rows) - len(mine)
            for r in rows[:max(0, 30 - len(examples))]:
                examples.append((str(r.get("starts_at"))[:16], str(r.get("cancelled_at"))[:16],
                                 r.get("title") or "?"))
            moved = flush(mine)
            if len(rows) < PAGE:
                break
            # The filter names cancelled_at, so a row this page lifted has left
            # the result set: step past only what is still in it (the same
            # correction mapsee_retire_online_events makes, for the same reason).
            offset += PAGE - (moved if args.apply else 0)
        pages += 1
        t += step

    out(f"\n  {found} imported row(s) cancelled by the pipeline since {since}")
    if rehidden[0]:
        out(f"  {rehidden[0]} of them were hidden again since by someone else (a take-down, a "
            f"prune): left alone")
    if skipped:
        out(f"  INCOMPLETE: {len(skipped)} window(s) errored: {', '.join(skipped[:6])}")
    for when, at, title in examples:
        out(f"    {when}  (cancelled {at})  {title[:64]}")
    if found > len(examples):
        out(f"    ... and {found - len(examples)} more")
    if not args.apply:
        out("\n  DRY RUN - nothing written. Read the list above, then re-run with --apply.")
        return 1 if skipped else 0
    out(f"\n  un-cancelled {written[0]} row(s) (cancelled_at and hidden_at cleared)"
        + (f"; {failed[0]} FAILED to write" if failed[0] else ""))
    # Non-zero when the sweep was not whole or a write failed: an escape hatch
    # that reports success after lifting half is how a misfire stays half-fixed.
    return 1 if (skipped or failed[0]) else 0


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Lift the pipeline's own cancellations made since a time.")
    ap.add_argument("--since", required=True, help="ISO date, or time with Z/offset")
    ap.add_argument("--apply", action="store_true", help="write (default is a dry run)")
    ap.add_argument("--days", type=int, default=400, help="how far ahead to sweep")
    ap.add_argument("--back", type=int, default=1, help="days BEFORE now to include")
    ap.add_argument("--max-pages", type=int, default=400)
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if not SUPABASE_URL or not SERVICE_KEY:
        print("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set", file=sys.stderr)
        return 2
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
