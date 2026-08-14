#!/usr/bin/env python3
"""
mapsee_cleanup.py — delete aggregator-imported events that are in the past, to
keep the Mapsee events table lean (and the sync's only-new lookup fast).

SAFETY: this is scoped to `external_source = 'mapsee'` ONLY — the events imported
by this aggregator. User/community events (external_source IS NULL) are never
matched, so they can't be deleted. Every request also carries a `starts_at`
cutoff, so it can never become an unfiltered delete.

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
    # ORDER MATTERS for the query plan: starts_at stays the leading, indexed
    # predicate (migration 0113, events_aggregator_cleanup_idx) and the ends_at
    # test filters the far smaller set it returns. Making ends_at the leading
    # term would drop the index and bring back the statement timeouts this file
    # was rewritten to survive.
    flt = (f"external_source=eq.mapsee&starts_at=lt.{cutoff}"
           f"&or=(ends_at.is.null,ends_at.lt.{cutoff})")
    assert "external_source=eq.mapsee" in flt and "starts_at=lt." in flt, "refusing an unscoped delete"
    base = url.rstrip("/") + "/rest/v1/events?" + flt
    auth = {"apikey": key, "Authorization": f"Bearer {key}"}

    # How many match? ESTIMATED, from the planner. An exact count walks every
    # matching row and was itself timing out — and when it did, the old code read
    # the missing Content-Range as "?" and carried on as though it had an answer.
    # This number is only ever printed, so a planner estimate is the right price.
    cnt = _req("GET", base + "&select=id",
               headers=dict(auth, **{"Range-Unit": "items", "Range": "0-0",
                                     "Prefer": "count=estimated"}),
               timeout=60)
    if cnt.status_code >= 300:
        print(f"Couldn't count matching events [{cnt.status_code}]: {cnt.text[:200]}")
        total = "unknown"
    else:
        total = cnt.headers.get("Content-Range", "*/unknown").split("/")[-1]
    print(f"Aggregator events that started before {cutoff}: ~{total}")

    if a.dry_run:
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

        page = _req("GET", f"{base}&select=id&limit={batch}", headers=auth,
                    timeout=min(120, max(10, int(left))))
        # No `order`: sorting is work the database does not need to do here.
        # Paging is stable without it because every row this returns is deleted
        # before the next fetch, so the window always moves forward.
        if _timed_out(page):
            if batch <= BATCH_MIN:
                sys.exit(f"Even {batch} ids time out on fetch — apply migration 0113 "
                         f"(events_aggregator_cleanup_idx), which is what makes this "
                         f"filter index-backed.")
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

    print(f"Deleted {deleted} past aggregator events (est. {total} were eligible).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
