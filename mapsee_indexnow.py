#!/usr/bin/env python3
"""IndexNow submitter — tells search engines about new events the day they land.

WHY THIS EXISTS
---------------
mapsee.me publishes its catalog through a sitemap index: 50 files x 1,000 URLs,
so up to 50,000 event pages, plus 141 evergreen /c/ city and region pages.
That is a large corpus for a young domain, and crawl budget is finite — a
crawler works down the sitemap at whatever rate it thinks the site deserves.

The problem is that **event pages are perishable in a way ordinary pages are
not.** sitemapEvents() (mapsee/src/index.js) only lists events whose start time
is still in the future, so an event page's entire useful life runs from the
moment it is ingested to the moment it starts. A concert added on Monday for a
show on Friday has a four-day window. If the crawler reaches it on day nine, the
URL was never worth publishing: the page it finally fetches is for something
that already happened, and the visitor it might have sent has nowhere to go.

Sitemaps are a *pull*: here is everything, come and get it whenever. IndexNow is
a *push*: these specific URLs are new, right now. That is exactly the shape of
this problem, and it is the one signal the site was not sending.

WHAT IT SUBMITS TO
------------------
One POST to api.indexnow.org is shared with every participating engine, so this
covers Bing, Yandex, Seznam and Naver from a single call. Google does not
participate — Google is still served by the sitemaps, which are unchanged and
keep doing their job. The reason to care about Bing specifically is no longer
really Bing's own search share: Bing's index is what backs Copilot and
ChatGPT's web search, so an event that lands there is an event an assistant can
answer with while it is still in the future.

WHAT IT SUBMITS
---------------
  * Event pages created since --since-hours, filtered by the SAME predicate the
    sitemap uses (public, not hidden, still upcoming). That predicate is the
    definition of "a page a crawler is allowed to see", and it is applied here
    in the query rather than trusted from anywhere else — submitting a private
    event's URL to a search engine would be a data leak, not a bug.
  * The /c/ city and region pages, once per run. These are not "new", but their
    content genuinely changes every run — the listings turn over and the live
    count in each <title> moves with them — so they are legitimately updated
    URLs, not spam. 141 of them per day is a rounding error against the events.

CREDENTIALS
-----------
This job needs no secret. It reads with the **anon** key — the same public key
every visitor's browser already uses, and the same one the Worker builds its
sitemaps with. That is deliberate: it means this script can only ever see what
an anonymous crawler could see, so it is structurally incapable of submitting a
private URL, independent of whether the query predicate is right.

USAGE
-----
    python mapsee_indexnow.py                 # last 26h of new events + /c/ pages
    python mapsee_indexnow.py --dry-run       # print what would be sent
    python mapsee_indexnow.py --since-hours 72 --no-pages

Exit codes: 0 on success or nothing-to-do, 1 on a submission failure.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import List

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# The public anon key. Hardcoded rather than required from env because it is
# already published in mapsee/site/js/config.js and shipped to every browser —
# demanding it as a secret would imply a confidentiality it does not have, and
# would mean this job fails closed on a machine that simply hasn't set it.
DEFAULT_SUPABASE_URL = "https://sjdcamppswwhwecheran.supabase.co"
DEFAULT_SUPABASE_ANON = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InNqZGNhbXBwc3d3aHdlY2hlcmFuIiwicm9sZSI6ImFub24i"
    "LCJpYXQiOjE3ODI2NzkxOTAsImV4cCI6MjA5ODI1NTE5MH0."
    "691iTuB-Egmzj2ZtXCaW5P6Zwc9A0VIRJVnm2HTIanA"
)

# Only mapsee.me is submitted. The other lens doors (bar.ventures, oneday.cafe,
# plansie.com, fleabop.com, wegosie.com, awaresie.com) serve a *filtered* view of
# the same events and their /c/ pages canonicalize back here, so pushing the same
# event under six hostnames would be asking six engines to index six near-copies
# of one page — the exact duplication the canonical tags exist to prevent.
HOST = "mapsee.me"
SITE = f"https://{HOST}"

# Must match the filename of the key file deployed at the site root, and that
# file's contents. See mapsee/site/afea88b0d7114a5188694ff0f3580849.txt — it is a
# static asset (build.mjs mirrors site/ into dist/), so no Worker route is
# involved and there is nothing to keep in sync but this constant.
#
# The key is NOT a secret. It is published at a public URL by design: it exists
# so an engine can prove whoever submitted the URLs controls the domain, which
# only works if the engine can fetch it.
INDEXNOW_KEY = "afea88b0d7114a5188694ff0f3580849"
KEY_LOCATION = f"{SITE}/{INDEXNOW_KEY}.txt"

ENDPOINT = "https://api.indexnow.org/indexnow"

# The protocol's per-request ceiling. Batching is not optional above this.
MAX_URLS_PER_REQUEST = 10_000

# PostgREST caps a page; loop until the source runs dry rather than assuming one
# page holds a whole day of ingest. A heavy Mon/Thu sweep lands tens of thousands
# of events, so a single 1,000-row read would silently submit the first 1,000.
PAGE = 1000


# ---------------------------------------------------------------------------
# Reading what is newly crawlable
# ---------------------------------------------------------------------------

def fetch_new_event_ids(base_url: str, anon_key: str, since_hours: int,
                        session: requests.Session) -> List[str]:
    """Event ids created in the window that a crawler is allowed to index.

    The three predicates after created_at are lifted verbatim from
    sitemapEvents() in mapsee/src/index.js. If that ever changes, this must
    change with it — the invariant is "IndexNow announces a subset of what the
    sitemap announces", never a superset.
    """
    since = (datetime.now(timezone.utc) - timedelta(hours=since_hours)).isoformat()
    now = datetime.now(timezone.utc).isoformat()

    ids: List[str] = []
    offset = 0
    while True:
        params = {
            "select": "id",
            "created_at": f"gte.{since}",
            "is_private": "eq.false",     # private events must never be announced
            "hidden_at": "is.null",       # moderated-away events must never be announced
            "starts_at": f"gte.{now}",    # a past event's page is not worth a crawl
            "order": "created_at.asc",
            "limit": str(PAGE),
            "offset": str(offset),
        }
        r = session.get(
            f"{base_url.rstrip('/')}/rest/v1/events",
            params=params,
            headers={"apikey": anon_key, "authorization": f"Bearer {anon_key}"},
            timeout=30,
        )
        r.raise_for_status()
        rows = r.json() or []
        ids.extend(row["id"] for row in rows if row.get("id"))
        if len(rows) < PAGE:
            return ids
        offset += PAGE


def landing_urls(session: requests.Session) -> List[str]:
    """The evergreen /c/ city and region pages, read from the live sitemap.

    Fetched from mapsee.me/sitemap-pages.xml rather than parsed out of the
    sibling repo's src/cities.js, for two reasons. It needs no checkout of the
    other repo, so this job stays a one-repo job with no cross-repo token. And
    the sitemap IS the site's own published list of these pages — the Worker
    generates it from the same CITIES constant the /c/ route uses, expressly so
    the two "can never drift". Reading the published answer cannot drift either;
    a third copy of the slug list in this file could.

    A failure here is not fatal. The events are the time-critical half; the
    landing pages are the same 140 URLs tomorrow.
    """
    try:
        r = session.get(f"{SITE}/sitemap-pages.xml", timeout=30)
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"... could not read sitemap-pages.xml ({e}) — skipping landing pages")
        return []
    import re
    # Only the /c/ pages. The homepage is in there too and is crawled constantly
    # on its own; submitting it daily would be noise.
    return re.findall(rf"<loc>({re.escape(SITE)}/c/[^<]+)</loc>", r.text)


# ---------------------------------------------------------------------------
# Submission
# ---------------------------------------------------------------------------

def submit(urls: List[str], session: requests.Session, dry_run: bool) -> bool:
    """POST the URL list in protocol-sized batches. True if everything landed."""
    ok = True
    for i in range(0, len(urls), MAX_URLS_PER_REQUEST):
        batch = urls[i:i + MAX_URLS_PER_REQUEST]
        payload = {
            "host": HOST,
            "key": INDEXNOW_KEY,
            "keyLocation": KEY_LOCATION,
            "urlList": batch,
        }
        if dry_run:
            print(f"  [dry-run] would POST {len(batch)} URLs to {ENDPOINT}")
            continue
        try:
            r = session.post(
                ENDPOINT,
                data=json.dumps(payload),
                headers={"Content-Type": "application/json; charset=utf-8"},
                timeout=60,
            )
        except requests.RequestException as e:
            print(f"  FAIL batch {i // MAX_URLS_PER_REQUEST + 1}: {e}")
            ok = False
            continue
        # 200 accepted, 202 accepted-pending-key-validation. Both are successes;
        # 202 just means the engine has not fetched the key file yet, which is
        # the normal response for the first submission from a new key.
        if r.status_code in (200, 202):
            print(f"  OK {len(batch)} URLs accepted ({r.status_code})")
        else:
            # 403 = key file not found or not matching. 422 = URLs don't belong
            # to the host. Both are configuration errors worth failing on, not
            # transient ones worth retrying.
            print(f"  FAIL {len(batch)} URLs rejected: HTTP {r.status_code} {r.text[:200]}")
            ok = False
    return ok


def main() -> None:
    ap = argparse.ArgumentParser(description="Submit newly-ingested Mapsee event URLs to IndexNow.")
    ap.add_argument("--since-hours", type=int, default=26,
                    help="How far back to look for new events. Default 26 — a "
                         "deliberate overlap with the 24h ingest cron so a run "
                         "that starts a little late cannot leave a gap. "
                         "Re-submitting a URL is harmless; missing one is not.")
    ap.add_argument("--no-pages", action="store_true",
                    help="Skip the /c/ city and region landing pages.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print what would be submitted; send nothing.")
    args = ap.parse_args()

    base_url = os.environ.get("SUPABASE_URL") or DEFAULT_SUPABASE_URL
    anon_key = os.environ.get("SUPABASE_ANON_KEY") or DEFAULT_SUPABASE_ANON

    session = requests.Session()

    try:
        event_ids = fetch_new_event_ids(base_url, anon_key, args.since_hours, session)
    except requests.RequestException as e:
        print(f"FAIL could not read new events from Supabase: {e}")
        sys.exit(1)

    urls = [f"{SITE}/e/{eid}" for eid in event_ids]
    print(f"{len(urls)} new indexable event page(s) in the last {args.since_hours}h")

    if not args.no_pages:
        pages = landing_urls(session)
        if pages:
            urls.extend(pages)
            print(f"{len(pages)} /c/ landing page(s) whose listings changed this run")

    if not urls:
        print("nothing to submit")
        return

    print(f"submitting {len(urls)} URL(s) -> {ENDPOINT}")
    if not submit(urls, session, args.dry_run):
        sys.exit(1)


if __name__ == "__main__":
    main()
