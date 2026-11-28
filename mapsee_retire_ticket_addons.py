#!/usr/bin/env python3
"""
mapsee_retire_ticket_addons.py — hide the Ticketmaster rows that are a product
sold beside a show, not the show.

WHY. "WAMU Theater Amplified Access: Steve Lacy (Not an Event Ticket)" is a
lounge upgrade; "Parking permit Muse" is a parking space. Ticketmaster lists
both as events at the show's venue and minute, so they were imported as events
and drew a second (and, when Ticketmaster re-keyed one, a third) row beside
the concert. mapsee_ingest refuses them at import since 2026-10-23
(_TM_NOT_A_TICKET_RX) — but AN UPSERT CANNOT DELETE, so every one written
before that stays until its date has passed. This is the other half, the same
shape as mapsee_retire_online_events.py.

Measured 2026-11-28 over the next 10 UTC days of live imported rows (189,403
listed, read-only): 39 rows on a Ticketmaster link whose title says so, 28 by
the import's rule as it stood and 11 by the two shapes added that day
("<show> | Parking ticket", "... Add-on"); 2 of them were Seattle's next 24
hours. The same titles on meetup.com (12: "[This is Not an event] Superclub
board game", "Tuesday Run starting in the parking lot") are real meet-ups, which
is why the rule reads a row only when its link is Ticketmaster's.

THE TEST IS THE IMPORT'S OWN PREDICATE over the stored title, plus the row's
own "Tickets / info:" link being on a Ticketmaster or Live Nation host: a row
this hides is exactly a row the import now refuses, and the two cannot drift.

hidden_at ONLY: never cancelled_at (the show was not cancelled, and the sync
may lift its own cancellations), never duplicate_of (it is not a copy of the
show; the collapser's --unhide and its kept-row passes must not see it). A
claimed row, a row somebody RSVPed to or chatted on, and a promoted row are
left alone, and the PATCH filter says so again at write time. Nothing is
deleted: mapsee_cleanup removes the row a week after it would have ended, and
a hidden row still counts as existing, so --only-new never brings it back.

REPORT ONLY unless --apply. --unhide --stamp S reverses exactly one run: rows
hidden at that stamp, with no duplicate_of and no cancelled_at, that still
match the predicate.

Env:  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY; MAPSEE_TODAY=YYYYMMDD fixes now.
Run:  python mapsee_retire_ticket_addons.py                 # report
      python mapsee_retire_ticket_addons.py --apply
      python mapsee_retire_ticket_addons.py --unhide --stamp 2026-11-29T07:10:00+00:00 --apply
"""
import argparse
import os
import sys
from datetime import timedelta
from typing import Any, Dict, List, Optional
from urllib.parse import quote

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

import mapsee_dedupe_events as D
from mapsee_ingest import _TM_NOT_A_TICKET_RX
from mapsee_supabase_sync import seller_key

SELLERS = {"ticketmaster", "livenation"}


def is_addon(title: Optional[str], link: Optional[str]) -> bool:
    """The stored row is a Ticketmaster product: the import's own title rule,
    on a row whose link is Ticketmaster's or Live Nation's."""
    return bool(_TM_NOT_A_TICKET_RX.search(title or "")) and seller_key(link) in SELLERS


def find(db: "D.Db", now, days: int, budget) -> Optional[List[Dict[str, Any]]]:
    """Live imported rows of the next `days` UTC days (and yesterday's still
    running) that are add-ons, each with its link. None when a read failed."""
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    hits: Dict[str, Dict[str, Any]] = {}
    for off in range(-1, days):
        lo = midnight + timedelta(days=off)
        rows, missed, _ = D.list_window(db, lo, lo + timedelta(days=1), now, budget)
        if missed:
            D._p(f"  {lo.date()}: {len(missed)} part(s) of the day could not be listed")
            return None
        for r in rows:
            if _TM_NOT_A_TICKET_RX.search(r.get("title") or ""):
                hits[r["id"]] = r
    links = D.read_links(db, hits) if hits else {}
    if links is None:
        return None
    out = []
    for i, r in sorted(hits.items()):
        if is_addon(r.get("title"), links.get(i)) and not r.get("claimed_at"):
            out.append(dict(r, link=links.get(i)))
    return out


def unhide(db: "D.Db", stamp: str, apply: bool) -> int:
    scope = (f"{db.base}&hidden_at=eq.{quote(stamp, safe='')}&cancelled_at=is.null&duplicate_of=is.null"
             f"&claimed_at=is.null")
    resp = db.get(f"{scope}&select=id,title,description&order=id.asc&limit={D.PAGE}")
    if not D._ok(resp):
        D._p(f"Couldn't list the rows hidden at {stamp}: {D._why(resp)}")
        return 1
    rows = [r for r in resp.json() or []
            if is_addon(r.get("title"), D.canonical_link(r.get("description")))]
    D._p(f"Add-on rows hidden at {stamp}: {len(rows)}")
    for r in rows[:50]:
        D._p(f"  {r['id']}  {r.get('title')!r}")
    if not apply:
        D._p("Report only: nothing un-hidden (add --apply).")
        return 0
    done = owed = 0
    for chunk in D._chunks(r["id"] for r in rows):
        got = db.patch(f"{scope}&select=id&id=in.({','.join(chunk)})", {"hidden_at": None})
        if D._ok(got):
            done += len(got.json() or [])
        else:
            owed += len(chunk)
            D._p(f"  un-hide failed for {len(chunk)} row(s): {D._why(got)}")
    D._p(f"Un-hid {done} row(s).")
    return 1 if owed else 0


def main(argv: Optional[List[str]] = None, http=None) -> int:
    ap = argparse.ArgumentParser(description="Hide Ticketmaster product rows (report unless --apply).")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--days", type=int, default=120, help="Forward horizon in UTC days (default 120).")
    ap.add_argument("--max-seconds", type=int, default=900)
    ap.add_argument("--unhide", action="store_true", help="Reverse one run; needs --stamp.")
    ap.add_argument("--stamp", default=None)
    a = ap.parse_args(argv)
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not (url and key):
        sys.exit("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY (server-side secrets).")
    db = D.Db(url, key, http if http is not None else requests.Session())
    if a.unhide:
        if not a.stamp:
            sys.exit("--unhide needs --stamp: it reverses one run, never everything.")
        return unhide(db, a.stamp, a.apply)
    now = D._now()
    started = D._clock()
    budget = lambda: D._clock() - started > a.max_seconds   # noqa: E731
    stamp = now.replace(microsecond=0).isoformat()
    D._p(f"Mode: {'APPLY (hidden_at=' + stamp + ')' if a.apply else 'REPORT ONLY'}; {a.days} days ahead")
    rows = find(db, now, a.days, budget)
    if rows is None:
        D._p("OWED: the listing was not read whole; nothing hidden. Run it again.")
        return 1
    D._p(f"Ticketmaster product rows still visible: {len(rows)}")
    for r in rows[:60]:
        D._p(f"  {r['id']}  {str(r.get('starts_at') or '')[:16]}  {r.get('title')!r}")
    if not a.apply or not rows:
        return 0
    rsvp = D.read_rsvp_ids(db, [r["id"] for r in rows])
    if rsvp is None:
        D._p("OWED: RSVPs could not be read; nothing hidden.")
        return 1
    hidden = owed = 0
    todo = [r["id"] for r in rows if not D.engaged(r, rsvp, now)]
    for chunk in D._chunks(todo):
        resp = db.patch(f"{db.base}&claimed_at=is.null&hidden_at=is.null&last_message_at=is.null"
                        f"&or=(promoted_until.is.null,promoted_until.lt.{D.Z(now)})&select=id"
                        f"&id=in.({','.join(chunk)})", {"hidden_at": stamp})
        if D._ok(resp):
            hidden += len(resp.json() or [])
        else:
            owed += len(chunk)
            D._p(f"  hide failed for {len(chunk)} row(s): {D._why(resp)}")
    D._p(f"Hidden {hidden} of {len(rows)} ({len(rows) - len(todo)} left: somebody is on them)."
         + (f" OWED {owed}." if owed else ""))
    return 1 if owed else 0


if __name__ == "__main__":
    raise SystemExit(main())
