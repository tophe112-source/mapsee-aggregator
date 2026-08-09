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
Per host, because IndexNow keys a submission to one `host` and every URL in the
batch must belong to it.

  mapsee.me — the flagship, and the ONLY door that submits events:
  * Event pages created since --since-hours, filtered by the SAME predicate the
    sitemap uses (public, not hidden, still upcoming). That predicate is the
    definition of "a page a crawler is allowed to see", and it is applied here
    in the query rather than trusted from anywhere else - submitting a private
    event's URL to a search engine would be a data leak, not a bug.
  * Its /c/ city, region and category pages.

  the six niche doors (bar.ventures, oneday.cafe, plansie.com, fleabop.com,
  wegosie.com, awaresie.com) - their /c/ landing pages only:
  * Each opens onto its own slice of the catalog, so bar.ventures/c/seattle and
    mapsee.me/c/seattle list different events. Every one is SELF-canonical and
    publishes its own sitemap, and each domain is separately verified in Bing
    and Search Console. There is no duplication to avoid here.
  * NOT their event pages. An event is one piece of content behind seven doors,
    and each door canonicals it to itself - announcing it seven times would put
    seven copies in the index and split its authority. Only mapsee.me lists
    events in a sitemap, and only mapsee.me pushes them.

The /c/ pages are not "new", but their content genuinely changes every run - the
listings turn over and the live count in each <title> moves with them - so they
are legitimately updated URLs, not spam.

The key file needs no per-door deployment: one Worker serves all seven hosts from
one assets binding, so /<key>.txt already resolves on every domain.

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

# The flagship door, and the only one that submits EVENTS.
#
# An event page is the same content behind whichever door you arrive through,
# and each door canonicals it to itself — so announcing one event under seven
# hostnames would put seven copies into the index and split its authority. Only
# mapsee.me lists events in a sitemap, and only mapsee.me pushes them here.
#
# The doors' LANDING pages are a different matter entirely; see lens_hosts().
HOST = "mapsee.me"
SITE = f"https://{HOST}"

# Where the roster of doors comes from.
#
# Not a hardcoded list. mapsee.me publishes every door and its domain at this
# endpoint (it is what the app itself themes from, and what the aggregator's
# catalog curator already reads its target categories from), so asking it means
# a door added later is picked up with no change here. That matters: awaresie.com
# was added as a seventh door and went unnoticed in several places that kept
# their own copy of the list.
LENS_API = f"{SITE}/api/lenses"

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


def lens_hosts(session: requests.Session):
    """Every OTHER front door's origin, from the live roster.

    WHY THESE ARE SUBMITTED TOO
    ---------------------------
    The six niche doors are not skins over one page. Each opens onto its own
    slice of the catalog - bar.ventures is party/music/food, fleabop.com is
    markets - so bar.ventures/c/seattle and mapsee.me/c/seattle list different
    events, and every door's /c/ page is SELF-canonical (verified live: each one
    declares itself). Each publishes its own 141-URL sitemap, and each domain is
    separately verified in Bing and Search Console.

    So the duplication argument never applied to these pages, only to their event
    pages - and this file's first version conflated the two and pushed nothing at
    all for six domains that had every right to it.
    """
    try:
        r = session.get(LENS_API, timeout=30)
        r.raise_for_status()
        roster = (r.json() or {}).get("lenses") or {}
    except (requests.RequestException, ValueError) as e:
        print(f"... could not read the lens roster ({e}) - submitting {HOST} only")
        return []
    sites = []
    for lens in roster.values():
        site = (lens or {}).get("site")
        # A door with no category filter shows what mapsee.me shows, and its
        # pages canonical back here; only the filtered ones are their own pages.
        if site and site.rstrip("/") != SITE and (lens.get("categories") or []):
            sites.append(site.rstrip("/"))
    return sorted(set(sites))


def landing_urls(site: str, session: requests.Session):
    """The evergreen /c/ city and region pages a given door publishes.

    Read from THAT HOST's own sitemap-pages.xml rather than built here. The
    sitemap is the site's published list - the Worker generates it from the same
    CITIES constant the /c/ route uses, expressly so the two "can never drift" -
    and each door announces only the pages it actually opens onto. Reading the
    published answer per host keeps the invariant that IndexNow announces a
    SUBSET of what the sitemap announces, with no second copy of any list here.

    A failure is not fatal. The events are the time-critical half; the landing
    pages are the same URLs tomorrow.
    """
    try:
        r = session.get(f"{site}/sitemap-pages.xml", timeout=30)
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"... could not read {site}/sitemap-pages.xml ({e}) - skipping it")
        return []
    import re
    # Only the /c/ pages. The homepage is in there too and is crawled constantly
    # on its own; submitting it daily would be noise.
    return re.findall(rf"<loc>({re.escape(site)}/c/[^<]+)</loc>", r.text)


# ---------------------------------------------------------------------------
# Submission
# ---------------------------------------------------------------------------

def submit(host: str, urls, session: requests.Session, dry_run: bool) -> bool:
    """POST one host's URL list in protocol-sized batches.

    One call per host: the protocol keys a submission to a single `host`, and
    every URL in urlList has to belong to it or the whole batch is refused
    with 422. The key file is the same for all of them - one Worker serves
    every door from one assets binding, so /<key>.txt already resolves on all
    seven hostnames (verified). keyLocation therefore points at THIS host's
    copy, which is what the protocol asks for.
    """
    ok = True
    for i in range(0, len(urls), MAX_URLS_PER_REQUEST):
        batch = urls[i:i + MAX_URLS_PER_REQUEST]
        payload = {
            "host": host,
            "key": INDEXNOW_KEY,
            "keyLocation": f"https://{host}/{INDEXNOW_KEY}.txt",
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
            continue

        try:
            code = (r.json() or {}).get("errorCode", "")
        except Exception:
            code = ""

        # NOT a failure, despite the 403.
        #
        # The first time a domain submits, Bing fetches the key file out of band
        # and checks it, and answers 403 SiteVerificationNotCompleted until that
        # finishes. It is asynchronous, it can take hours, and it resolves on its
        # own with no action from us.
        #
        # This is separated out because the daily job would otherwise fail every
        # morning until verification completed — and a scheduled job that cries
        # wolf for a week is a scheduled job nobody reads afterwards. That is the
        # exact failure this repo already has a health checker to prevent.
        #
        # Every OTHER 403 stays fatal. KeyNotFound or a key file that does not
        # match is a real misconfiguration, it will never self-resolve, and it
        # must be loud.
        if r.status_code == 403 and code == "SiteVerificationNotCompleted":
            print(f"  PENDING {len(batch)} URLs held: {host} is still being verified by the "
                  f"search engine (it fetches the key file on its own schedule).")
            print("          Nothing to fix. The next run submits them again.")
            continue

        # 403 (other) = key file missing or mismatched. 422 = the URLs do not
        # belong to the host. Both are configuration errors, not transient ones.
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
    ap.add_argument("--doors-off", action="store_true",
                    help="Submit mapsee.me only, skipping the six niche front doors.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print what would be submitted; send nothing.")
    args = ap.parse_args()

    base_url = os.environ.get("SUPABASE_URL") or DEFAULT_SUPABASE_URL
    anon_key = os.environ.get("SUPABASE_ANON_KEY") or DEFAULT_SUPABASE_ANON

    session = requests.Session()
    ok = True

    # ---- mapsee.me: the new events, plus its own landing pages ----
    try:
        event_ids = fetch_new_event_ids(base_url, anon_key, args.since_hours, session)
    except requests.RequestException as e:
        print(f"FAIL could not read new events from Supabase: {e}")
        sys.exit(1)

    urls = [f"{SITE}/e/{eid}" for eid in event_ids]
    print(f"{HOST}: {len(urls)} new indexable event page(s) in the last {args.since_hours}h")
    if not args.no_pages:
        pages = landing_urls(SITE, session)
        if pages:
            urls.extend(pages)
            print(f"{HOST}: {len(pages)} /c/ landing page(s) whose listings changed this run")

    if urls:
        print(f"submitting {len(urls)} URL(s) for {HOST}")
        ok &= submit(HOST, urls, session, args.dry_run)
    else:
        print(f"{HOST}: nothing to submit")

    # ---- the other doors: their landing pages only ----
    #
    # No events here. An event is one piece of content behind seven doors and
    # each door canonicals it to itself, so announcing it seven times would
    # split its authority - that is the duplication this deliberately avoids.
    # Their /c/ pages are the opposite case: different events, self-canonical,
    # separately verified domains, and until now getting no push at all.
    if not args.no_pages and not args.doors_off:
        for site in lens_hosts(session):
            host = site.split("://", 1)[-1]
            pages = landing_urls(site, session)
            if not pages:
                continue
            print(f"submitting {len(pages)} URL(s) for {host}")
            ok &= submit(host, pages, session, args.dry_run)

    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
