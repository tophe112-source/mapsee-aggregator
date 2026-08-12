#!/usr/bin/env python3
"""
mapsee_prune_links.py — take back a promise the destination no longer keeps.

WHY THIS EXISTS. mapsee_ingest_osm_food checks every order and booking link
before writing it (destination_verdict). That protects listings written from now
on and does nothing at all for the ones already on the map, because an UPSERT can
add and update but cannot delete: when a place's link goes dead the ingest simply
SKIPS the place, no row is written, and the row written back when the link still
worked survives untouched — still rendering an "Order pickup" button, still
promising somebody hungry a destination that is not there.

That is the worst shape of bug this repo keeps producing: not an error anywhere,
just a confident sentence that stopped being true. Found 2026-08-12 by checking
production after a backfill "succeeded" — Vietlicious still carried
order.chownow.com/order/39625/locations/60228, which serves an empty React shell.

WHAT IT DOES. Reads mapsee-sourced events carrying a 🛒 Order or 🍽️ Reserve line,
checks each distinct URL ONCE, and where the destination is provably gone,
rewrites the description without that line — including the lead sentence, which
otherwise goes on saying "Order for pickup on their own site" after the order
link has been removed.

WHAT IT REFUSES TO DO.

  * It never drops a link on "unknown". destination_verdict separates a 404 from
    a 403, and it has to: the big ordering hosts block scrapers, so treating an
    unfetchable page as dead would strip most of the map's order links. That bug
    was live for one city earlier the same day. Only alive/dead/unknown="dead"
    counts, and the default everywhere is to keep.
  * It never touches a CLAIMED event. Once a venue owns its listing the text is
    theirs, exactly as in mapsee_reclassify.
  * It writes nothing without --apply.

ORPHANS. Stripping the last transactional link leaves a pin with nothing behind
it, which is precisely what the OSM adapter refuses to create ("no order link, no
import"). Those are counted and reported, and --hide-orphans will set hidden_at
on them — hidden, not deleted, because a link dying is often temporary and a
restaurant should not be erased over a weekend outage.

Env:  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
Run:  python mapsee_prune_links.py                      # dry run, the default
      python mapsee_prune_links.py --apply
      python mapsee_prune_links.py --apply --hide-orphans
"""
import argparse
import collections
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

from mapsee_menu_links import destination_verdict

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
PAGE = 500

# The two transactional lines this pipeline writes, and the "Tickets / info"
# line the sync derives from ticket_url — which for an OSM import is the SAME
# url, so leaving it behind would just move the dead link to a different button.
ORDER_LINE = re.compile(r"^\s*\U0001F6D2[^:]*:\s*(\S+)\s*$")
BOOK_LINE = re.compile(r"^\s*\U0001F37D️?[^:]*:\s*(\S+)\s*$")
TICKETS_LINE = re.compile(r"^\s*Tickets\s*/\s*info:\s*(\S+)\s*$", re.I)

# The generated lead. Rewritten rather than left, because "Order for pickup on
# their own site" is a claim, and it is false the moment the order line goes.
LEAD = re.compile(
    r"^(?P<head>.*?\.)\s*(?P<claim>Order for pickup or book a table|"
    r"Order for pickup|Book a table) on their own site;\s*"
    r"mapsee\.me is not taking the order\.", re.S)


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


def rewrite(desc, dead_urls):
    """Description without the dead lines, or None if nothing changed.

    Returns (new_desc, still_has_order, still_has_booking).
    """
    kept, dropped = [], 0
    has_order = has_book = False
    for block in desc.split("\n\n"):
        m_o, m_b, m_t = (ORDER_LINE.match(block), BOOK_LINE.match(block),
                         TICKETS_LINE.match(block))
        url = (m_o or m_b or m_t)
        if url and url.group(1) in dead_urls:
            dropped += 1
            continue
        if m_o:
            has_order = True
        if m_b:
            has_book = True
        kept.append(block)
    if not dropped:
        return None, has_order, has_book

    out = "\n\n".join(kept)
    # Fix the lead so it does not keep advertising what we just removed.
    m = LEAD.match(out)
    if m:
        if has_order and has_book:
            claim = "Order for pickup or book a table on their own site; mapsee.me is not taking the order."
        elif has_order:
            claim = "Order for pickup on their own site; mapsee.me is not taking the order."
        elif has_book:
            claim = "Book a table on their own site; mapsee.me is not taking the order."
        else:
            claim = ""      # nothing left to promise: say nothing
        head = m.group("head")
        out = (head + (" " + claim if claim else "")) + out[m.end():]
    return out, has_order, has_book


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write (default is a dry run)")
    ap.add_argument("--hide-orphans", action="store_true",
                    help="also hide rows left with no transactional link at all")
    ap.add_argument("--days", type=int, default=120, help="how far ahead to sweep")
    ap.add_argument("--back", type=int, default=2,
                    help="days BEFORE now to include, so in-progress events are swept")
    ap.add_argument("--max-pages", type=int, default=90)
    ap.add_argument("--max-checks", type=int, default=400,
                    help="distinct URLs to probe per run; we are a guest on these servers")
    args = ap.parse_args()

    if not SUPABASE_URL or not SERVICE_KEY:
        print("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set", file=sys.stderr)
        return 2

    now = time.time()
    start = now - args.back * 86400
    step = 86400 * 2
    t, pages = start, 0
    rows_by_url = collections.defaultdict(list)
    scanned = 0
    skipped_windows = []

    # Windowed like mapsee_reclassify, for the same reason: deep OFFSET paging on
    # this table dies under the statement timeout.
    while t < now + args.days * 86400 and pages < args.max_pages:
        w_a = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
        w_b = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                            time.gmtime(min(t + step, now + args.days * 86400)))
        q = ("events?select=id,title,description,claimed_by"
             "&external_source=eq.mapsee&is_private=eq.false&hidden_at=is.null"
             f"&starts_at=gte.{w_a}&starts_at=lt.{w_b}&order=starts_at.asc&limit={PAGE}")
        offset = 0
        while True:
            batch = None
            for attempt in range(3):
                try:
                    batch = sb(q + f"&offset={offset}") or []
                    break
                except Exception as e:
                    if attempt == 2:
                        print(f"  window {w_a[:10]} offset {offset} failed 3x ({e})",
                              file=sys.stderr)
                        skipped_windows.append(w_a[:10])
                    else:
                        time.sleep(1.5 * (attempt + 1))
            if not batch:
                break
            for row in batch:
                scanned += 1
                if row.get("claimed_by"):
                    continue                       # the venue owns this listing
                desc = row.get("description") or ""
                for block in desc.split("\n\n"):
                    m = ORDER_LINE.match(block) or BOOK_LINE.match(block)
                    if m:
                        rows_by_url[m.group(1)].append(row)
            if len(batch) < PAGE:
                break
            offset += PAGE
        pages += 1
        t += step

    print(f"\n  scanned {scanned} aggregator rows · "
          f"{len(rows_by_url)} distinct transactional URLs")
    if skipped_windows:
        print(f"  INCOMPLETE: {len(skipped_windows)} window(s) errored and were not examined: "
              f"{', '.join(skipped_windows[:6])}")

    # ONE probe per distinct URL. A venue has one row per open day, so the naive
    # loop would ask the same restaurant's server seven times to learn one fact.
    order = sorted(rows_by_url, key=lambda u: -len(rows_by_url[u]))
    if len(order) > args.max_checks:
        print(f"  CAPPED: probing the {args.max_checks} most-used of {len(order)} URLs; "
              f"the remaining {len(order) - args.max_checks} are NOT examined this run")
        order = order[:args.max_checks]

    verdicts = {}
    for i, url in enumerate(order, 1):
        verdicts[url] = destination_verdict(url)
        time.sleep(0.4)
        if i % 25 == 0:
            print(f"    probed {i}/{len(order)}", flush=True)

    tally = collections.Counter(verdicts.values())
    print(f"\n  verdicts: " + " · ".join(f"{k}={v}" for k, v in tally.most_common()))
    dead = {u for u, v in verdicts.items() if v == "dead"}
    if not dead:
        print("  nothing to prune.")
        return 0
    print(f"\n  DEAD destinations ({len(dead)}), most-used first:")
    for u in sorted(dead, key=lambda x: -len(rows_by_url[x]))[:20]:
        names = {r.get("title") or "?" for r in rows_by_url[u]}
        print(f"    {len(rows_by_url[u]):4} rows  {', '.join(sorted(names))[:38]:40} {u[:56]}")

    # Rebuild each affected row once, even if two of its lines are dead.
    affected = {}
    for u in dead:
        for row in rows_by_url[u]:
            affected[row["id"]] = row

    written = orphans = unchanged = 0
    orphan_ids = []
    for rid, row in affected.items():
        new, has_order, has_book = rewrite(row.get("description") or "", dead)
        if new is None:
            unchanged += 1
            continue
        if not (has_order or has_book):
            orphans += 1
            orphan_ids.append(rid)
        if args.apply:
            try:
                sb(f"events?id=eq.{rid}", "PATCH", {"description": new},
                   prefer="return=minimal")
                written += 1
            except Exception as e:
                print(f"    PATCH {rid} failed: {e}", file=sys.stderr)

    print(f"\n  rows affected {len(affected)} · rewritten {written}"
          + ("" if args.apply else "  (DRY RUN — nothing written)"))
    print(f"  left with NO transactional link (orphans): {orphans}")
    if orphans and not args.hide_orphans:
        print("  orphans are a pin with nothing behind it — the OSM adapter refuses to")
        print("  CREATE those. Re-run with --hide-orphans to hide them (reversible).")
    if orphans and args.hide_orphans and args.apply:
        hidden = 0
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        for rid in orphan_ids:
            try:
                sb(f"events?id=eq.{rid}", "PATCH", {"hidden_at": stamp},
                   prefer="return=minimal")
                hidden += 1
            except Exception as e:
                print(f"    hide {rid} failed: {e}", file=sys.stderr)
        print(f"  hidden {hidden} orphan rows (hidden_at set; not deleted)")
    if not args.apply:
        print("\n  Read the table above, then re-run with --apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
