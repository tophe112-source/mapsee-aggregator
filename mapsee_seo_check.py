#!/usr/bin/env python3
"""SEO regression check — crawls what we publish and complains when it breaks.

WHY THIS EXISTS
---------------
The indexable surface across the estate is now large and almost entirely
GENERATED: 141 city and region pages, 800 category pages and 100 weekend pages
per door, seven doors, plus ~51,000 event pages. None of it is hand-written, so
none of it gets looked at, and every failure mode here is silent by nature:

  * a canonical that starts pointing at a page which refuses to be indexed
  * a route that begins 404ing after a regex change, while the sitemap keeps
    announcing it
  * a JSON-LD block that stops parsing because a title contained a quote
  * a title that grows past what a search result renders
  * a page that quietly loses its description

Every one of those keeps returning HTTP 200. Nothing in a deploy fails. The only
evidence is a slow decline in Search Console weeks later, which is exactly the
shape of problem this repo already built `source-health.yml` to prevent for the
ingest side. This is the same idea pointed at the published pages.

It only reads. It never writes anything anywhere.

WHAT IT CHECKS
--------------
Sampled from each host's own sitemap, so it can never drift from what is
actually announced:

  BROKEN      the sitemap lists a URL that does not return 200
  NO_CANONICAL / NO_TITLE / NO_DESCRIPTION   on an indexable page
  BAD_JSONLD  a structured-data block that does not parse
  DEAD_CANONICAL   canonical points at a URL that 404s
  NOINDEX_CANONICAL   canonical points at a page that is itself noindex, which
              tells a crawler to go index something that refuses to be indexed
  LONG_TITLE  over the length a search result renders (advisory, counted only)

A noindex page is not checked past that point: withholding a page is a decision,
not a fault, and the whole /c/ family does it deliberately for thin cities.

USAGE
-----
    python mapsee_seo_check.py                 # sample every door, exit 1 on problems
    python mapsee_seo_check.py --sample 40     # smaller/faster
    python mapsee_seo_check.py --host mapsee.me
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from concurrent.futures import ThreadPoolExecutor

import requests

LENS_API = "https://mapsee.me/api/lenses"
FALLBACK_HOSTS = ["mapsee.me"]

# Roughly where Google stops rendering a title. Advisory: a long title is a
# worse result, not a broken page, so it is counted and never fails the run.
TITLE_MAX = 70

UA = "MapseeSeoCheck/1.0 (+https://mapsee.me)"


def hosts_from_roster(session):
    """Every front door, from the roster mapsee.me publishes.

    Same source the IndexNow submitter uses, for the same reason: a door added
    later is picked up with no change here.
    """
    try:
        r = session.get(LENS_API, timeout=30)
        r.raise_for_status()
        roster = (r.json() or {}).get("lenses") or {}
    except (requests.RequestException, ValueError) as e:
        print(f"... could not read the lens roster ({e}) - checking mapsee.me only")
        return list(FALLBACK_HOSTS)
    out = []
    for lens in roster.values():
        site = (lens or {}).get("site") or ""
        if site.startswith("http"):
            out.append(site.split("://", 1)[1].rstrip("/"))
    return sorted(set(out)) or list(FALLBACK_HOSTS)


def sitemap_urls(session, host):
    try:
        r = session.get(f"https://{host}/sitemap-pages.xml", timeout=45)
        r.raise_for_status()
    except requests.RequestException as e:
        return None, str(e)
    return re.findall(r"<loc>([^<]+)</loc>", r.text), None


def get(session, url):
    return session.get(url, timeout=45, allow_redirects=False)


def check_one(session, host, url, problems, counts):
    try:
        r = get(session, url)
    except requests.RequestException as e:
        problems.append((host, "BROKEN", url, str(e)[:70]))
        return
    if r.status_code != 200:
        problems.append((host, "BROKEN", url, f"HTTP {r.status_code}"))
        return

    b = r.text
    counts["checked"] += 1
    robots = (re.search(r'name="robots" content="([^"]*)"', b) or [None, ""])[1]
    if "noindex" in robots:
        counts["noindex"] += 1
        return
    counts["indexable"] += 1

    canon = (re.search(r'rel="canonical" href="([^"]*)"', b) or [None, ""])[1]
    title = (re.search(r"<title>([^<]*)</title>", b) or [None, ""])[1]
    desc = (re.search(r'name="description" content="([^"]*)"', b) or [None, ""])[1]

    if not canon:
        problems.append((host, "NO_CANONICAL", url, ""))
    if not title:
        problems.append((host, "NO_TITLE", url, ""))
    elif len(title.replace("&amp;", "&")) > TITLE_MAX:
        counts["long_title"] += 1
    if not desc:
        problems.append((host, "NO_DESCRIPTION", url, ""))

    for m in re.finditer(r'application/ld\+json">(.*?)</script>', b, re.S):
        try:
            json.loads(m.group(1))
        except Exception:
            problems.append((host, "BAD_JSONLD", url, ""))
            break

    # A canonical is a redirect for crawlers. Pointing it at a 404, or at a page
    # that itself says noindex, is worse than having none: the first throws the
    # signal away, the second sends it somewhere that refuses to accept it.
    if canon and canon.split("?")[0] != url.split("?")[0]:
        counts["cross_canonical"] += 1
        try:
            cr = get(session, canon)
            if cr.status_code != 200:
                problems.append((host, "DEAD_CANONICAL", url, f"{canon} -> {cr.status_code}"))
            elif "noindex" in (re.search(r'name="robots" content="([^"]*)"', cr.text)
                               or [None, ""])[1]:
                problems.append((host, "NOINDEX_CANONICAL", url, canon))
        except requests.RequestException:
            problems.append((host, "DEAD_CANONICAL", url, canon))


def main() -> None:
    ap = argparse.ArgumentParser(description="Crawl the published landing pages and report regressions.")
    ap.add_argument("--sample", type=int, default=60,
                    help="Pages to check per host. The full set is ~1,000 per door and "
                         "the failures here are systematic rather than per-page, so a "
                         "sample finds them and a full crawl only costs time.")
    ap.add_argument("--host", action="append",
                    help="Check only this host (repeatable). Default: every door in the roster.")
    ap.add_argument("--seed", type=int, default=0,
                    help="Sampling seed. Fixed by default so a failure is reproducible; "
                         "vary it in CI runs to sweep more of the corpus over time.")
    args = ap.parse_args()

    session = requests.Session()
    session.headers["User-Agent"] = UA

    hosts = args.host or hosts_from_roster(session)
    rng = random.Random(args.seed)
    problems, all_counts = [], {}

    for host in hosts:
        urls, err = sitemap_urls(session, host)
        if urls is None:
            problems.append((host, "BROKEN", f"https://{host}/sitemap-pages.xml", err[:70]))
            continue
        if not urls:
            problems.append((host, "EMPTY_SITEMAP", f"https://{host}/sitemap-pages.xml", ""))
            continue
        pick = urls if len(urls) <= args.sample else rng.sample(urls, args.sample)
        counts = {"checked": 0, "indexable": 0, "noindex": 0, "long_title": 0, "cross_canonical": 0}
        with ThreadPoolExecutor(max_workers=8) as ex:
            list(ex.map(lambda u: check_one(session, host, u, problems, counts), pick))
        counts["announced"] = len(urls)
        all_counts[host] = counts
        print(f"{host:<16} announced={counts['announced']:<5} sampled={counts['checked']:<4} "
              f"indexable={counts['indexable']:<4} noindex={counts['noindex']:<4} "
              f"long-title={counts['long_title']:<3} cross-canonical={counts['cross_canonical']}")

    if not problems:
        print("\nNo problems found.")
        return

    print(f"\n{len(problems)} problem(s):")
    by_kind = {}
    for p in problems:
        by_kind.setdefault(p[1], []).append(p)
    for kind, rows in sorted(by_kind.items()):
        print(f"\n{kind}  ({len(rows)})")
        for host, _, url, extra in rows[:8]:
            print(f"   {host}  {url}  {extra}")
        if len(rows) > 8:
            print(f"   ... and {len(rows) - 8} more")
    sys.exit(1)


if __name__ == "__main__":
    main()
