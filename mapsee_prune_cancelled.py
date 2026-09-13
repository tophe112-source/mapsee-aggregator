#!/usr/bin/env python3
"""
mapsee_prune_cancelled.py — take an event off the map after the source calls it off.

WHY THIS EXISTS. Every cancellation check in this repo runs at INGEST: Dice and
Venuepilot read a status field, Eventbrite reads is_cancelled, OpenActive honours
a `state: deleted` tombstone, the festival and JSON-LD adapters read schema.org's
eventStatus, and mapsee_ingest_meetup now reads Meetup's. All of them protect the
events we have not written yet, and NONE of them can do anything about a row
already in the table — an upsert cannot delete, and `--only-new` (the CI default)
means a scheduled run can only ever ADD. An event that was real when we took it
and is called off a week later stays on the map, with its pin, its RSVP button
and its chat, until mapsee_cleanup reaps it a week after it was supposed to end.

That is the same shape of bug as mapsee_prune_links: not an error anywhere, just
a confident sentence that stopped being true.

HOW BIG IT IS. Measured 2026-09-13 by sampling 2,000 live event pages from
mapsee.me's own sitemaps, taking the 502 whose "Tickets / info" link is a Meetup
event, and re-probing every one at its own source URL:

    scheduled   437
    CANCELLED    47      the source page's own markup says EventCancelled
    HTTP 404     18      the listing has been deleted outright
                ---
                 65      12.9% of the Meetup events on the map are not happening

Meetup is about a quarter of the map's linked rows (502 of the 1,969 sampled rows
carrying a Tickets / info link), so this is not a corner. And the ingest-side
check cannot reach them: the same day, a live eventSearch sweep of Seattle
returned 30 events and ALL 30 were ACTIVE. These rows were live when we took
them. They were cancelled afterwards, which is the only way most events are
cancelled, and this is the only thing that can notice.

WHAT COUNTS AS EVIDENCE. Two things, both unambiguous:

  * schema.org eventStatus of EventCancelled or EventPostponed, in JSON-LD or
    microdata. Postponed counts because the date WE hold is the one that is not
    happening; a new date is a new event and the adapters will import it.
  * HTTP 404 or 410 on the listing. Gone is gone.

WHAT IS NOT EVIDENCE, and the list is the point:

  * A 403, a 429, a 5xx, a timeout, a non-HTML body. The big ticketing hosts
    block scrapers, and treating a refusal as a cancellation would empty the map
    of exactly the sources that guard themselves best. Same rule, same reason, as
    destination_verdict in mapsee_menu_links.
  * PROSE. "This event has been cancelled" is a sentence, and so is "cancellation
    policy", "free cancellation up to 24 hours", "cancelled orders are refunded"
    — which appear on a large share of legitimate ticket pages. There is no text
    rule here and there should not be one; the markup already says it properly.
  * A cancellation marker on a page that is describing SOMEBODY ELSE'S event. A
    "Tickets / info" link sometimes lands on a venue's whole calendar, where one
    cancelled show among twenty would otherwise condemn our row. So a page is
    only read as cancelled when EVERY eventStatus on it says so; a mixed page is
    unknown, and unknown keeps the row.
  * A whole HOST answering dead at once. Restaurants close independently and so
    do gigs; twenty listings on one domain dying on the same afternoon is a
    statement about us, not about them. Lifted wholesale from mapsee_prune_links,
    where it was learned by nearly deleting every Uber Eats link on the map.

HIDE, NOT DELETE. hidden_at comes off the map immediately, is one PATCH to undo,
and does not cascade through event_messages, event_rsvps, invites and cohosts —
which matters most for exactly these rows, because somebody may have RSVP'd to
the thing that got cancelled. mapsee_cleanup deletes it a week after it would
have ended, as it does every other imported row, so nothing accumulates. And
hiding is durable against re-import: fetch_import_state does not filter on
hidden_at, so a hidden row still counts as existing and `--only-new` will not
put it back.

HOW BIG THE SWEEP ACTUALLY IS, measured on the first live run 2026-09-13:
**88,128 rows and 25,311 distinct source URLs inside a three-day window.** At
roughly one probe a second that is seven hours of work, so every run is capped
and every run leaves most of the map unexamined. Which part it examines is
therefore the whole design, and it is decided by two things: `--days`/`--back`
choose the slice, and the soonest-first ordering decides what inside that slice
gets the budget. `--back` defaults to 0 for exactly this reason — see its note.

Env:  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
Run:  python mapsee_prune_cancelled.py                    # dry run, the default
      python mapsee_prune_cancelled.py --apply
      python mapsee_prune_cancelled.py --apply --max-seconds 900
"""
import argparse
import collections
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
PAGE = 500

UA = ("MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me) "
      "cancellation-check")

# The line the sync writes from ticket_url — the source's own page for this
# event, and the only pointer a stored row keeps back to where it came from.
TICKETS_LINE = re.compile(r"^\s*Tickets\s*/\s*info:\s*(\S+)\s*$", re.I | re.M)

# schema.org eventStatus, in the two ways a page can state it. JSON-LD is by far
# the commoner; the microdata form is matched in both attribute orders because
# half the CMSs that emit it put content= first.
_LD_STATUS = re.compile(r'"eventStatus"\s*:\s*"([^"]*)"', re.I)
_MICRO_STATUS = re.compile(
    r'<meta[^>]*\bitemprop\s*=\s*["\']eventStatus["\'][^>]*\bcontent\s*=\s*["\']([^"\']*)["\']'
    r'|<meta[^>]*\bcontent\s*=\s*["\']([^"\']*)["\'][^>]*\bitemprop\s*=\s*["\']eventStatus["\']',
    re.I)

_OFF = ("eventcancelled", "eventpostponed")

# Hosts proven to refuse a plain HTTP client, so their pages are never evidence.
# Kept deliberately short: a host belongs here only once a run has shown it
# answers everything the same way. The per-run host guard below catches the rest.
UNVERIFIABLE_HOSTS = re.compile(r"(^|\.)(ticketmaster\.[a-z.]+|livenation\.[a-z.]+)$", re.I)


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


def page_verdict(html: str) -> str:
    """"cancelled" | "live" | "unknown", from a page we actually fetched.

    Split out from the fetch so the decision is testable without a network, and
    so there is exactly one place that decides what markup means.

    A page with no eventStatus at all is "live", not "unknown": most event pages
    do not carry the property, and calling all of them unknown would be the same
    as not running. Absence of a cancellation is the normal state of the world;
    only a POSITIVE marker acts, and that is what keeps this fail-open.
    """
    found = [m for m in _LD_STATUS.findall(html or "")]
    found += [(a or b) for a, b in _MICRO_STATUS.findall(html or "")]
    found = [f.strip().rsplit("/", 1)[-1].lower() for f in found if f and f.strip()]
    if not found:
        return "live"
    off = [f for f in found if f in _OFF]
    if not off:
        return "live"
    if len(off) == len(found):
        return "cancelled"
    # A calendar page listing many events, one of them cancelled. We cannot tell
    # which one is ours, so we say so.
    return "unknown"


def cancellation_verdict(url: str, timeout: int = 20) -> str:
    """"cancelled" | "gone" | "live" | "unknown". Only the first two act."""
    try:
        host = (urllib.parse.urlparse(url).hostname or "").lower()
    except Exception:
        return "unknown"
    if host and UNVERIFIABLE_HOSTS.search(host):
        return "unknown"
    try:
        req = urllib.request.Request(url, headers={"user-agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ct = (r.headers.get("content-type") or "").lower()
            if "html" not in ct and "json" not in ct and "text" not in ct:
                return "unknown"
            return page_verdict(r.read(600_000).decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        # The listing is gone. Everything else — 403 bot-block, 429, 5xx — is us
        # being refused, which is not the same as the event being off.
        return "gone" if e.code in (404, 410) else "unknown"
    except Exception:
        return "unknown"


def _host(url: str) -> str:
    try:
        return (urllib.parse.urlparse(url).hostname or "").lower().lstrip("www.")
    except Exception:
        return ""


def collect(args):
    """Upcoming aggregator rows, grouped by the source URL that would prove them off."""
    now = time.time()
    start = now - args.back * 86400
    step = 86400 * 2
    t, pages = start, 0
    rows_by_url = collections.defaultdict(list)
    scanned = 0
    skipped_windows = []

    # Windowed like mapsee_reclassify and mapsee_prune_links, for the same
    # reason: deep OFFSET paging on this table dies under the statement timeout.
    while t < now + args.days * 86400 and pages < args.max_pages:
        w_a = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
        w_b = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                            time.gmtime(min(t + step, now + args.days * 86400)))
        q = ("events?select=id,title,description,claimed_by,starts_at"
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
                m = TICKETS_LINE.search(row.get("description") or "")
                if m:
                    rows_by_url[m.group(1)].append(row)
            if len(batch) < PAGE:
                break
            offset += PAGE
        pages += 1
        t += step
    return scanned, rows_by_url, skipped_windows


# A majority, not unanimity — mapsee_prune_links learned that demanding 100%
# disarms the guard on the very host it was written for, because one live
# listing among fifteen dead ones is enough to break a unanimity test.
REFUSAL_SHARE = 0.6
REFUSAL_MIN = 5           # higher than prune_links' 3: a venue really can cancel
                          # three nights in a row, and this one HIDES rows.


def refusing_hosts(verdicts):
    """Hosts whose links are dead at a rate that says more about us than them."""
    by_host = collections.defaultdict(list)
    for u, v in verdicts.items():
        by_host[_host(u)].append(v)
    out = set()
    for host, vs in by_host.items():
        n_off = sum(1 for v in vs if v in ("cancelled", "gone"))
        if len(vs) >= REFUSAL_MIN and n_off >= max(REFUSAL_MIN, int(len(vs) * REFUSAL_SHARE)):
            out.add(host)
            print(f"  HOST REFUSING: {host} — {n_off} of {len(vs)} listings read as off. "
                  f"Treating as unknown, not hiding them.")
    return out


def main():
    ap = argparse.ArgumentParser(
        description="Hide aggregator events the source has since cancelled or deleted.")
    ap.add_argument("--apply", action="store_true", help="write (default is a dry run)")
    ap.add_argument("--days", type=int, default=120, help="how far ahead to sweep")
    # ZERO, AND THE FIRST LIVE RUN IS WHY. It was 1, on the reasoning that a
    # multi-day event cancelled mid-run is worth catching. True, and irrelevant
    # next to what it costs: the queue is sorted soonest-first, so a day of
    # ALREADY-STARTED events sits at the front of it, and a capped run spends
    # its entire budget there. Measured 2026-09-13 on the first production dry
    # run — 88,128 rows and 25,311 distinct URLs inside a three-day window, of
    # which the run reached 894, and every single one of the 47 rows it found
    # had started YESTERDAY. It never got as far as today. An event that has
    # already begun is the least useful thing this can hide; cleanup deletes it
    # a week later regardless. Pass --back 1 deliberately when that is the job.
    ap.add_argument("--back", type=int, default=0,
                    help="days BEFORE now to include (default 0: only what has not started)")
    ap.add_argument("--max-pages", type=int, default=90)
    # THE BUDGET IS THE REAL LIMITER, and this is only politeness. It was 1200,
    # chosen against an estimate of "a few hundred URLs in a two-day window"
    # that turned out to be off by a factor of twenty — so the cap bit long
    # before --max-seconds did and the run stopped early for no good reason.
    # Sized now so that --max-seconds is what ends a sweep.
    ap.add_argument("--max-checks", type=int, default=4000,
                    help="distinct source URLs to probe per run; we are a guest on these servers")
    ap.add_argument("--max-seconds", type=int, default=0,
                    help="stop probing after this long and act on what we have (0 = no budget)")
    ap.add_argument("--delay", type=float, default=0.35, help="seconds between probes")
    args = ap.parse_args()

    if not SUPABASE_URL or not SERVICE_KEY:
        print("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set", file=sys.stderr)
        return 2

    began = time.time()
    scanned, rows_by_url, skipped_windows = collect(args)
    print(f"\n  scanned {scanned} aggregator rows · "
          f"{len(rows_by_url)} distinct source URLs")
    if skipped_windows:
        print(f"  INCOMPLETE: {len(skipped_windows)} window(s) errored and were not examined: "
              f"{', '.join(skipped_windows[:6])}")
    if not rows_by_url:
        print("  nothing to check.")
        return 0

    # ONE probe per distinct URL. A weekly series shares one listing, so the
    # naive loop would ask the same server the same question every week.
    #
    # SOONEST FIRST, and that ordering is the whole budget strategy. There are
    # far more upcoming rows than one run can politely probe, so something has to
    # be left out — and the run must not leave out the SAME things every day.
    # mapsee_prune_links sorts by how many rows share a URL, which is right for
    # it (a restaurant's link is one fact about seven rows) and wrong here: it
    # would re-probe the same popular listings daily and never reach the tail.
    # By start date the cap truncates the FAR end, which is both the least
    # harmful place for a stale row and the part that will be probed anyway as it
    # comes closer. A cancelled gig tomorrow is a wasted evening; a cancelled gig
    # in November is a row that has four months of runs to be caught in.
    first_start = {u: min(str(r.get("starts_at") or "9") for r in rows)
                   for u, rows in rows_by_url.items()}
    order = sorted(rows_by_url, key=lambda u: (first_start[u], u))
    if len(order) > args.max_checks:
        print(f"  CAPPED: probing the {args.max_checks} soonest of {len(order)} URLs "
              f"(through {first_start[order[args.max_checks - 1]][:10]}); the remaining "
              f"{len(order) - args.max_checks} are further out and wait for a later run")
        order = order[:args.max_checks]

    verdicts, budget_hit = {}, 0
    for i, url in enumerate(order, 1):
        if args.max_seconds and time.time() - began > args.max_seconds:
            budget_hit = len(order) - i + 1
            break
        verdicts[url] = cancellation_verdict(url)
        time.sleep(args.delay)
        if i % 50 == 0:
            print(f"    probed {i}/{len(order)}", flush=True)
    if budget_hit:
        print(f"  BUDGET: stopped after {args.max_seconds}s with {budget_hit} URL(s) "
              f"unprobed; they are left for the next run")

    tally = collections.Counter(verdicts.values())
    print("\n  verdicts: " + " · ".join(f"{k}={v}" for k, v in tally.most_common()))

    refusing = refusing_hosts(verdicts)
    off = {u: v for u, v in verdicts.items()
           if v in ("cancelled", "gone") and _host(u) not in refusing}
    if not off:
        print("  nothing to hide.")
        return 0

    affected = {}
    for u in off:
        for row in rows_by_url[u]:
            affected[row["id"]] = (row, off[u], u)

    print(f"\n  CALLED OFF at the source ({len(off)} listings, {len(affected)} rows):")
    for rid, (row, verdict, u) in list(affected.items())[:30]:
        print(f"    {verdict:<10} {str(row.get('starts_at'))[:10]}  "
              f"{(row.get('title') or '?')[:46]:<48} {u[:58]}")
    if len(affected) > 30:
        print(f"    … and {len(affected) - 30} more")

    if not args.apply:
        print("\n  DRY RUN — nothing written. Read the table above, then re-run with --apply.")
        return 0

    hidden, failed = 0, 0
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for rid in affected:
        try:
            sb(f"events?id=eq.{rid}", "PATCH", {"hidden_at": stamp}, prefer="return=minimal")
            hidden += 1
        except Exception as e:
            failed += 1
            print(f"    hide {rid} failed: {e}", file=sys.stderr)
    print(f"\n  hidden {hidden} row(s) (hidden_at set; not deleted, and reversible)"
          + (f" · {failed} failed" if failed else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
