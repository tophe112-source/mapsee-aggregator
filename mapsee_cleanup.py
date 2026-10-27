#!/usr/bin/env python3
"""
mapsee_cleanup.py — delete aggregator-imported events that are in the past, to
keep the Mapsee events table lean (and the sync's only-new lookup fast).

SAFETY: this is scoped to `external_source = 'mapsee'` ONLY — the events imported
by this aggregator. User/community events (external_source IS NULL) are never
matched, so they can't be deleted. Every request also carries a `starts_at`
cutoff, so it can never become an unfiltered delete. Nor is a STANDING row
(recurring_hours set: a centre, a pool, a pin) ever matched, hidden or not —
the comment on the filter in main() says why, with the 2026-10-26 numbers.

Default keeps events for a WEEK after they END, so attendees can keep chatting
about a show before it disappears.

It used to be a week after they START, on the stated grounds that "aggregator
events store ends_at = NULL". They do not: mapsee_supabase_sync.py sets ends_at
on every row (_compute_end — the source's real end, else start plus a
category-typical duration). A multi-day event therefore looked over on its second
day. The next ingest would re-add it — --only-new compares against what is in the
table, not a ledger — but events cascade to event_messages, event_rsvps, invites
and cohosts, so it came back stripped of its guest list while it was still on.

WHY THIS DELETES IN BATCHES. It used to issue one DELETE for the whole cutoff
window and eventually started failing with

    Delete failed [500]: {"code":"57014","message":"canceling statement due to
                          statement timeout"}

A dozen tables carry `on delete cascade` on events — event_messages, event_rsvps,
invites, cohosts, food_orders and friends — so removing a week of imported events
is really removing that many rows across all of them, in ONE statement, against a
hosted statement timeout. It cannot be waited out; it has to be cut up. So this
pages through ids and deletes a bounded slice at a time, halving the slice if the
database still says no, and leaving whatever it did not reach for the next
scheduled run. A janitor job that makes partial progress is working; one that
fails the build because it could not finish in a single statement is not.

Env:  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
Run:  python mapsee_cleanup.py [--older-than-days 7] [--dry-run]
"""
import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

# Ids go back to PostgREST inside `id=in.(...)`, i.e. in the URL, and a UUID plus
# its separator is ~37 bytes. 100 keeps the whole request line near 4KB, well
# under the 8KB that fronting proxies commonly cap it at. Raising this trades
# fewer round trips for a request line that can start getting rejected.
BATCH_DEFAULT = 100
BATCH_MIN = 10                 # below this, a timeout is not about batch size
TIMEOUT_CODE = "57014"         # postgres: canceling statement due to statement timeout


def _timed_out(resp) -> bool:
    """A statement timeout, which is a signal to take smaller bites."""
    return resp.status_code >= 500 and TIMEOUT_CODE in (resp.text or "")


ATTEMPTS = 3


def _req(method: str, url: str, **kw):
    """One PostgREST call, retrying only what is worth retrying.

    TWO DIFFERENT 5xx ARRIVE HERE AND THEY WANT OPPOSITE THINGS. A statement
    timeout (57014) means we asked for too much and must take a SMALLER bite —
    the batch halving below owns that, and retrying the same request unchanged
    would just time out again. An upstream 503 — "upstream connect error or
    disconnect/reset before headers", Envoy failing to reach Postgres — means we
    asked for nothing at all, and the identical request will work once the edge
    recovers. So this retries the second and hands the first straight back.

    Without it, a provider blip ended the run: 2026-08-14's outage produced
    "Couldn't count matching events [503]" followed by "Couldn't list events to
    delete [503]" and exit 1, on a job whose work is idempotent and would have
    succeeded seconds later. mapsee_health_check._rpc has retried 5xx all along;
    this is the same rule in the one script that still lacked it.
    """
    last = None
    for attempt in range(ATTEMPTS):
        try:
            r = requests.request(method, url, **kw)
        except requests.RequestException as ex:
            last = f"{type(ex).__name__}: {ex}"
        else:
            if r.status_code < 500 or _timed_out(r):
                return r                          # settled, or the caller's to shrink
            last = f"HTTP {r.status_code}: {(r.text or '')[:160]}"
            if attempt == ATTEMPTS - 1:
                return r                          # let the caller report the body
        if attempt < ATTEMPTS - 1:
            print(f"Upstream unavailable ({last}) — retrying")
            time.sleep(2 ** (attempt + 1))        # 2s, then 4s
    raise SystemExit(f"Supabase unreachable after {ATTEMPTS} attempts: {last}. "
                     f"Nothing was deleted. If the next scheduled run is green, "
                     f"it was a transient upstream outage.")


def main() -> int:
    ap = argparse.ArgumentParser(description="Delete past aggregator events from Supabase.")
    ap.add_argument("--older-than-days", type=int, default=7,
                    help="Delete aggregator events that started more than N days ago "
                         "(default 7 = a week of post-event chat).")
    ap.add_argument("--dry-run", action="store_true", help="Report the count; delete nothing.")
    ap.add_argument("--batch", type=int, default=BATCH_DEFAULT,
                    help=f"Events per delete statement (default {BATCH_DEFAULT}).")
    ap.add_argument("--max-seconds", type=int, default=600,
                    help="Stop cleanly after this long and leave the rest for the next run "
                         "(default 600).")
    a = ap.parse_args()

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not (url and key):
        sys.exit("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY (server-side secrets).")

    cutoff = (datetime.now(timezone.utc) - timedelta(days=max(0, a.older_than_days))
              ).strftime("%Y-%m-%dT%H:%M:%SZ")

    # SAFETY: always scoped to imported events AND a past cutoff — never unfiltered.
    # Carried on the id-batched deletes below too, belt and braces: the primary
    # keys alone would be enough to identify the rows, but then a bug in the id
    # fetch could delete something this job is not allowed to touch.
    #
    # The `or=` clause is what stops a STILL-RUNNING event being deleted. The
    # module docstring used to justify a start-time-only cutoff with "aggregator
    # events store ends_at = NULL" — that stopped being true when
    # mapsee_supabase_sync.py:640 started setting ends_at on every row
    # (_compute_end: the source's real end, else start + a category-typical
    # duration). A ten-day festival therefore has starts_at eight days ago and
    # ends_at in the future, and matched a filter that means "over".
    #
    # Deleting one is not merely cosmetic. The next ingest does re-add it
    # (--only-new compares against what is currently IN the table, not a ledger),
    # but events cascade: event_messages, event_rsvps, invites and cohosts go
    # with it. The event comes back empty, mid-run, with its guest list gone.
    #
    # The cleanup indexes are keyed by external_source and starts_at; ends_at
    # stays in the filter as the running-event safety check. SQL predicate text
    # order does not control index selection. Keep the full eligibility scope on
    # both candidate reads and deletes.
    #
    # NO STANDING ROW IS EVER DELETED BY AGE, HIDDEN OR NOT. A community centre,
    # a library, a pool or a furniture pin is one row with recurring_hours set,
    # whose window the database's hourly roller (0188; 0234 in ../mapsee) moves
    # to today.
    #
    # A LIVE ONE IS THE ROLLER'S TO BRING BACK. Measured 2026-10-12, the roller
    # did 5,000 rolls an hour against ~20,800 due, so its queue head sat four
    # days old (2026-10-08T21:59Z) and 65 of the 70 facility-hours centres were
    # stale. Seven days stale, this filter would have deleted them, and the
    # delete cascades to each centre's chat and RSVPs.
    #
    # A HIDDEN ONE IS A TOMBSTONE. The roller skips hidden rows, so a hidden
    # standing row's window freezes and it ages past the cutoff a week later.
    # fetch_import_state (mapsee_supabase_sync.py) keys on whether the row
    # EXISTS, so once it is deleted the next sync of a source that still lists
    # it re-inserts it VISIBLE. An organizer's take-down (hidden_at alone, as
    # AGENTS.md promises) comes back about eight days later, and
    # mapsee_retire_thin_artwork.py's `--unhide` (its rows are pins) has
    # nothing left to put back. From 2026-10-12 to 10-26 this filter carried
    # `and=(or(recurring_hours.is.null,hidden_at.not.is.null))`, which reached
    # exactly those rows (how many it deleted was not measured); the stall
    # below is what stopped it.
    #
    # THE SAME CLAUSE IS WHAT KEEPS THE FETCH OFF THE FROZEN PIN BAND. 0239
    # (../mapsee, applied 2026-10-19 18:30Z) stopped rolling pins and froze
    # 465,926 of them at local midnight of 10-19: cohorts at 10-18 22:00Z
    # (CEST), 23:00Z (BST) and 10-19 04:00-07:00Z (US). 0113's
    # events_aggregator_cleanup_idx holds every imported row, pins too, and the
    # old OR clause could only be checked on the heap, so every page stepped
    # over the band before it reached a deletable row (0235 measured ~0.23 ms
    # per stale row walked: the 8 s ceiling after ~35k). On 2026-10-26 (cutoff
    # 10-19T15:17Z, the first past the band; job 121659644598) the run deleted
    # 20,300 rows at 0.47-0.78 s per 100, took 7.5 s for the next 100, timed
    # out fetching 100, 50, 25 and 12, got 10 more (20,410) and ended "Even 10
    # ids time out on fetch" at 15:20:49, exit 1. The 11 runs before it deleted
    # a mean of 28,599 a day and ended "Nothing left to delete". The wall only
    # grows: every frozen pin is below every later cutoff, and a pin the sync
    # rewrites freezes at its own day.
    #
    # `&recurring_hours=is.null` is the literal that lets the planner prove
    # ../mapsee migration 0241's events_cleanup_dated_idx, (external_source,
    # starts_at) WHERE external_source IS NOT NULL AND recurring_hours IS NULL,
    # which holds no standing row at all. On a local model the candidate fetch
    # read 325 buffers through it against 82,433 through 0113, in custom and
    # forced-generic plans; with the old OR clause the planner kept 0113 even
    # with the new index present. Widen this clause again and the planner
    # silently goes back to walking the band. (The index says IS NOT NULL, not
    # = 'mapsee', because PostgREST sends eq values as $n parameters and a
    # generic plan can prove only that.) 0113 stays for the unscoped walks:
    # mapsee_spam_purge.py's has no recurring_hours filter.
    flt = (f"external_source=eq.mapsee&starts_at=lt.{cutoff}"
           f"&or=(ends_at.is.null,ends_at.lt.{cutoff})"
           f"&recurring_hours=is.null")
    assert "external_source=eq.mapsee" in flt and "starts_at=lt." in flt, "refusing an unscoped delete"
    assert "&recurring_hours=is.null" in flt, "refusing a delete that can reach a standing row"
    assert "hidden_at" not in flt, "refusing to delete a hidden standing row: it is a tombstone"
    base = url.rstrip("/") + "/rest/v1/events?" + flt
    auth = {"apikey": key, "Authorization": f"Bearer {key}"}

    # A count is useful for a dry run, but it adds a database request before
    # every mutation run. Keep that planner estimate out of the path that is
    # already bounded by page size and max-seconds. `planned` asks PostgREST for
    # a statistics-based estimate; an exact count can scan the whole match set.
    if a.dry_run:
        cnt = _req("GET", base + "&select=id",
                   headers=dict(auth, **{"Range-Unit": "items", "Range": "0-0",
                                         "Prefer": "count=planned"}),
                   timeout=60)
        if cnt.status_code >= 300:
            sys.exit(f"Couldn't count matching events [{cnt.status_code}]: {cnt.text[:200]}")
        total = cnt.headers.get("Content-Range", "*/unknown").split("/")[-1]
        print(f"Aggregator events that started before {cutoff}: ~{total}")
        print("Dry run — nothing deleted.")
        return 0

    batch = max(BATCH_MIN, a.batch)
    deleted = 0
    started = time.monotonic()

    while True:
        left = a.max_seconds - (time.monotonic() - started)
        if left <= 0:
            print(f"Reached the {a.max_seconds}s budget — the next scheduled run picks up "
                  f"where this left off.")
            break

        # No OFFSET: every successful delete removes its page from the candidate
        # set, so the bounded limit fetch advances without a count or cursor.
        #
        # ORDER BY starts_at, AND ONLY starts_at. Without an ORDER the planner is
        # free to answer a LIMIT with a sequential scan. The 2026-10-10 live
        # EXPLAIN did not (it used 0113), but a local model of the table with
        # nothing left to delete did, in every index configuration: ~193k
        # buffers, which on production is the whole 1.76 GB heap under an 8 s
        # ceiling, on exactly the run that should end "Nothing left to
        # delete". Ordered by the index's own second key, the fetch is an index
        # scan that stops at the limit. An id tiebreak would bring back the
        # Incremental Sort that 10-10 EXPLAIN measured (limit cost 1,436.65 to
        # 1,456.77). It sits BEFORE &limit= because test_cleanup.py reads the
        # batch size off the end of the URL.
        page = _req("GET", f"{base}&select=id&order=starts_at.asc&limit={batch}",
                    headers=auth, timeout=min(120, max(10, int(left))))
        if _timed_out(page):
            if batch <= BATCH_MIN:
                sys.exit(f"Even {batch} ids time out on fetch. Check whether "
                         f"events_cleanup_dated_idx (the mapsee repo's migration 0241) "
                         f"exists and is valid; without it this read falls back to "
                         f"0113's events_aggregator_cleanup_idx, which holds every "
                         f"frozen pin and steps over them on the heap. Then inspect "
                         f"the live query plan; the timeout may have another cause.")
            batch = max(BATCH_MIN, batch // 2)
            print(f"Fetch timed out — retrying with batches of {batch}.")
            continue
        if page.status_code >= 300:
            sys.exit(f"Couldn't list events to delete [{page.status_code}]: {page.text[:300]}")

        ids = [r["id"] for r in (page.json() or []) if r.get("id")]
        if not ids:
            print("Nothing left to delete.")
            break

        resp = _req("DELETE", f"{base}&id=in.({','.join(ids)})",
                    headers=dict(auth, Prefer="return=minimal,count=exact"),
                    timeout=min(300, max(30, int(left))))
        if _timed_out(resp):
            if batch <= BATCH_MIN:
                sys.exit(f"Even {batch} events at a time exceed the statement timeout. "
                         f"The cascade from events is wide (event_messages, rsvps, "
                         f"invites, orders…); this needs looking at server-side.")
            batch = max(BATCH_MIN, batch // 2)
            print(f"Delete timed out — retrying with batches of {batch}.")
            continue
        if resp.status_code >= 300:
            sys.exit(f"Delete failed [{resp.status_code}]: {resp.text[:300]}")

        # The exact number removed, from the delete itself. Not len(ids): the
        # scope filters ride along on the delete, so a row whose starts_at moved
        # between the fetch and now is correctly skipped, and counting the ids we
        # ASKED about would quietly overstate the work.
        try:
            got = int(resp.headers.get("Content-Range", "*/0").split("/")[-1])
        except ValueError:
            got = len(ids)
        if got == 0:
            sys.exit(f"Fetched {len(ids)} ids but deleted none — the filters disagree with "
                     f"themselves. Stopping rather than spinning on the same page.")
        deleted += got
        print(f"  deleted {deleted}…", flush=True)

    print(f"Deleted {deleted} past aggregator events.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
